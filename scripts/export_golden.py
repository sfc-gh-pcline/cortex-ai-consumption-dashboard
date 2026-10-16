"""Export golden SQL and registry metadata from the Python builders.

The App Runtime port in ``web/`` must emit byte-identical statements to the
builders in ``utils/queries.py`` and ``utils/config.py``. This script captures
those statements for a fixed window into ``tests/fixtures/golden_sql.json``;
``web/tests/sql-parity.test.ts`` compares the TypeScript builders against it.

Run from the repository root after any builder change::

    uv run python scripts/export_golden.py

Read-only: it opens no Snowflake connection. Settings are supplied explicitly so
``_dt_read_sql`` never reaches for the app connection.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import config  # noqa: E402
from utils import queries as q  # noqa: E402
from utils.snowflake import quote_identifier, surface_to_suffix  # noqa: E402

OUTPUT = ROOT / "tests" / "fixtures" / "golden_sql.json"
START = dt.date(2026, 8, 1)
END = dt.date(2026, 8, 14)
TODAY = dt.date(2026, 9, 23)
SETTINGS = config.Settings(
    enabled=True,
    database="COST_MGMT_DB",
    schema="PUBLIC",
    warehouse_name="COST_MGMT_WH",
    warehouse_size="XSMALL",
    object_prefix="COST_MGMT",
)
# Two rated models, one partially rated, one with no rates at all (dropped by the CTE).
RATES = pd.DataFrame(
    [
        ("claude-3-5-sonnet", 3e-06, 1.5e-05, 3e-07, 3.75e-06),
        ("llama3.1-8b", 1.9e-07, 1.9e-07, None, None),
        ("unrated-model", None, None, None, None),
    ],
    columns=["MODEL_NAME", *q.RATE_COLUMNS],
)


def statement(stmt: tuple) -> dict:
    """Serialize a ``Statement`` tuple as a JSON object."""
    sql, params = stmt
    return {"sql": sql, "params": list(params)}


def source_commit() -> str:
    """Return the HEAD commit the fixture was generated from, or ``unknown``."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def export() -> dict:
    """Build the fixture document from every builder the web app must match."""
    # _dt_read_sql reads settings through the app connection and model_rates_sql
    # asks Snowflake for today's UTC date; pin both so the export stays offline.
    config.load_settings = lambda *_args, **_kwargs: SETTINGS  # type: ignore[assignment]
    q.utc_today = lambda: TODAY  # type: ignore[assignment]

    windowed = {
        "coco": q.coco_facts_sql(START, END),
        "ai_fn": q.ai_fn_facts_sql(START, END),
        "agent": q.agent_facts_sql(START, END),
        "cowork": q.cowork_facts_sql(START, END),
        "cowork_service_mix": q.cowork_service_mix_sql(START, END),
        "search_daily": q.search_daily_facts_sql(START, END),
        "search_serving": q.search_serving_facts_sql(START, END),
        "search_batch": q.search_batch_facts_sql(START, END),
        "warehouse_full": q.warehouse_credit_facts_sql(START, END),
        "warehouse_attribution_only": q.warehouse_credit_facts_attribution_only_sql(START, END),
        "metering_overlap": q._metering_attribution_overlap_sql(START, END),
        "rest_with_rates": q.rest_facts_sql(START, END, rates=RATES),
        "rest_no_rates": q.rest_facts_sql(START, END, rates=RATES.iloc[0:0]),
    }
    dt_keys = [key for key in q.DT_BODY_REGISTRY if key != q.SURFACE_MODEL_RATES]
    bodies = {key: spec.body_fn() for key, spec in q.DT_BODY_REGISTRY.items()}
    bodies[f"{q.SURFACE_WAREHOUSE_ATTRIBUTION} — attribution_only"] = (
        config.warehouse_body_for_variant("attribution_only")
    )
    quote_cases = {}
    for name in [
        "cost_mgmt_db",
        "PUBLIC",
        "_x$1",
        "",
        "a.b",
        "a b",
        "a;b",
        "a--b",
        "1abc",
        "x" * 256,
    ]:
        try:
            quote_cases[name] = {"ok": quote_identifier(name)}
        except ValueError as exc:
            quote_cases[name] = {"error": str(exc)}

    return {
        "source_commit": source_commit(),
        "window": {"start": START.isoformat(), "end": END.isoformat(), "today": TODAY.isoformat()},
        "settings": {
            "database": SETTINGS.database,
            "schema": SETTINGS.schema,
            "warehouse_name": SETTINGS.warehouse_name,
            "warehouse_size": SETTINGS.warehouse_size,
            "object_prefix": SETTINGS.object_prefix,
        },
        "rates": [
            {
                "MODEL_NAME": row["MODEL_NAME"],
                **{c: (None if pd.isna(row[c]) else float(row[c])) for c in q.RATE_COLUMNS},
            }
            for _, row in RATES.iterrows()
        ],
        "windowed": {name: statement(stmt) for name, stmt in windowed.items()},
        "inlined": {name: q.inline_params(*stmt) for name, stmt in windowed.items()},
        "static": {
            "model_rates": statement(q.model_rates_sql()),
            "model_rates_body": q.model_rates_body_sql(),
            "user_tags": q.user_tags_sql(),
            "metering_probe": statement(q.metering_probe_sql()),
        },
        "dt_reads": {key: statement(q._dt_read_sql(key, START, END)) for key in dt_keys},
        "model_rates_dt": statement(
            q._model_rates_dt_sql(SETTINGS.database, SETTINGS.schema, SETTINGS.object_prefix)
        ),
        "dt_columns": {key: list(cols) for key, cols in q._DT_COLUMNS.items()},
        "bodies": bodies,
        "ddl": {
            key: {
                "table": q.acceleration_table_sql(key, SETTINGS),
                "task": q.acceleration_task_sql(key, SETTINGS),
                "lifecycle": q.acceleration_lifecycle_sql(key, SETTINGS),
            }
            for key in q.DT_BODY_REGISTRY
        },
        "warehouse_ddl": config.create_refresh_warehouse_sql("cost_mgmt_wh", "xsmall"),
        "reference": [
            {
                "label": e.label,
                "sources": list(e.sources),
                "note": e.note,
                "group": e.group,
                "variant": e.variant,
                "kind": e.kind,
                "runtime_key": e.runtime_key,
                "statement": statement(e.builder(START, END))
                if e.runtime_key != q.SURFACE_REST_API
                else None,
            }
            for e in q.SQL_REFERENCE
        ],
        "reference_groups": list(q.sql_reference_groups()),
        "acceleration_reference": [
            {
                "surface_key": e.surface_key,
                "object_type": e.object_type,
                "source_views": list(e.source_views),
                "explanation": e.explanation,
            }
            for e in q.ACCELERATION_SQL_REFERENCE
        ],
        "identifiers": {
            "quote": quote_cases,
            "suffix": {
                key: surface_to_suffix(key) for key in [*q.DT_BODY_REGISTRY, "CoWork — Service Mix"]
            },
        },
    }


def main() -> int:
    """Write the fixture and report its path."""
    OUTPUT.write_text(json.dumps(export(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
