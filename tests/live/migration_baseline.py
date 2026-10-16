"""Live unified-versus-legacy CoCo reconciliation for pinned migration windows."""

from pathlib import Path

import pandas as pd
import pytest

from utils.queries import run_query

BASELINE_SQL = Path(__file__).with_suffix(".sql")
KEY_COLUMNS = ("WINDOW_LABEL", "INTERFACE")
MEASURE_COLUMNS = (
    "REQUEST_COUNT",
    "TOTAL_TOKENS",
    "TOTAL_CREDITS",
    "DISTINCT_USERS",
    "DISTINCT_MODELS",
    "DISTINCT_ROLES",
    "DISTINCT_REGIONS",
)


@pytest.mark.snowflake
def test_unified_coco_matches_legacy_baselines():
    """Require exact cross-source parity outside the one-hour latency window."""
    frame = run_query(BASELINE_SQL.read_text(encoding="utf-8"))
    assert set(frame["SOURCE"]) == {"unified", "legacy"}

    unified = frame.loc[frame["SOURCE"] == "unified"].set_index(list(KEY_COLUMNS)).sort_index()
    legacy = frame.loc[frame["SOURCE"] == "legacy"].set_index(list(KEY_COLUMNS)).sort_index()

    pd.testing.assert_frame_equal(
        unified.loc[:, MEASURE_COLUMNS],
        legacy.loc[:, MEASURE_COLUMNS],
        check_dtype=False,
        check_exact=True,
    )


@pytest.mark.snowflake
def test_unified_coco_has_no_unmatched_granular_token_keys():
    """Make malformed token/credit model pairs operator-visible in validation."""
    mismatch_sql = """
WITH windows AS (
    SELECT column1::STRING AS window_label, column2::TIMESTAMP_TZ AS start_time,
           column3::TIMESTAMP_TZ AS end_time
    FROM VALUES
        ('7day', '2026-08-10 00:00:00 +0000', '2026-08-17 00:00:00 +0000'),
        ('30day', '2026-07-18 00:00:00 +0000', '2026-08-17 00:00:00 +0000'),
        ('boundary', '2026-07-31 00:00:00 +0000', '2026-08-07 00:00:00 +0000')
),
token_keys AS (
    SELECT w.window_label, h.REQUEST_ID, token_model.key::STRING AS model_name
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY h
    JOIN windows w ON h.USAGE_TIME >= w.start_time AND h.USAGE_TIME < w.end_time,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model
),
credit_keys AS (
    SELECT w.window_label, h.REQUEST_ID, credit_model.key::STRING AS model_name
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY h
    JOIN windows w ON h.USAGE_TIME >= w.start_time AND h.USAGE_TIME < w.end_time,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
)
SELECT t.window_label, t.model_name, COUNT(*) AS unmatched_token_keys
FROM token_keys t
LEFT JOIN credit_keys c
  ON c.window_label = t.window_label AND c.request_id = t.request_id AND c.model_name = t.model_name
WHERE c.model_name IS NULL
GROUP BY t.window_label, t.model_name
ORDER BY t.window_label, t.model_name
"""
    mismatches = run_query(mismatch_sql)
    assert mismatches.empty, mismatches.to_dict(orient="records")
