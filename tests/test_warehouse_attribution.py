"""Warehouse membership, routing, error, and historical coverage contracts."""

import datetime as dt
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tests.fixtures.warehouse_attribution import fixture_sql
from utils import config, ui
from utils import queries as q

START = dt.date(2026, 9, 16)
END = START
CURRENT = "SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY"
LEGACY = "CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY"


@pytest.mark.parametrize("page", ["cortex_ai_functions", "warehouse_attribution"])
@pytest.mark.parametrize("historical", [True, False])
def test_page_coverage_notice_survives_empty_state(monkeypatch, page, historical):
    monkeypatch.setattr(q, "load_ai_fn_facts", lambda *_: pd.DataFrame())
    monkeypatch.setattr(
        q,
        "load_warehouse_credit_facts",
        lambda *_: q.WarehouseFactsResult(q._empty_warehouse_facts(), False, None),
    )
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_pages" / f"{page}.py"))
    app.session_state.start_date = dt.date(2026, 1, 4 if historical else 5)
    app.session_state.end_date = dt.date(2026, 1, 5)
    app.session_state.users = []
    app.session_state.models = []
    app.session_state.tag_users = []
    app.run()
    assert not app.exception
    coverage = [warning for warning in app.warning if "January 5, 2026" in warning.value]
    assert len(coverage) == int(historical)


@pytest.mark.parametrize("include_metering", [True, False])
def test_fixture_substitution_fails_closed(include_metering):
    sql = q._warehouse_credit_facts_sql(START, END, include_metering=include_metering)
    candidate = q.inline_params(*sql).replace(LEGACY, "CORTEX_AI_FUNCTIONS_USAGE_HISTORY")
    rendered = fixture_sql(candidate, include_metering=include_metering)
    assert q.AU not in rendered
    assert "MISSING_COUNT" in rendered
    assert "DUPLICATE_COUNT" in rendered
    with pytest.raises(ValueError, match="Unexpected source count"):
        fixture_sql(
            candidate.replace(CURRENT, f"{q.AU}.{LEGACY}"), include_metering=include_metering
        )
    with pytest.raises(ValueError, match="Unexpected source count"):
        fixture_sql(
            candidate + f" UNION ALL SELECT QUERY_ID FROM {CURRENT}",
            include_metering=include_metering,
        )


@pytest.mark.parametrize(
    "builder",
    [
        lambda: q.warehouse_credit_facts_sql(START, END)[0],
        lambda: q.warehouse_credit_facts_attribution_only_sql(START, END)[0],
        q._warehouse_body_sql_full,
        q._warehouse_body_sql_attribution_only,
    ],
)
def test_current_membership_has_query_grain_without_window_or_completion_filter(builder):
    sql = builder()
    block = sql.split("aif_ids AS (", 1)[1].split(")", 1)[0]
    assert f"SELECT DISTINCT QUERY_ID FROM {CURRENT}" in block
    assert LEGACY not in sql
    assert "START_TIME" not in block
    assert "IS_COMPLETED" not in block


@pytest.mark.parametrize("metering_available", [True, False])
def test_source_failure_returns_explicit_unavailable_result(monkeypatch, metering_available):
    monkeypatch.setattr(config, "load_settings", lambda: config.Settings(enabled=False))
    query = Mock(
        side_effect=[
            pd.DataFrame() if metering_available else RuntimeError("metering"),
            RuntimeError("AI membership source unavailable"),
        ]
    )
    monkeypatch.setattr(q, "run_query", query)
    result = q.load_warehouse_credit_facts(START, END)
    assert result.degraded
    assert result.message is not None
    assert "Warehouse attribution unavailable" in result.message
    assert "AI membership source unavailable" in result.message
    assert result.frame.empty
    assert tuple(result.frame.columns) == q._DT_COLUMNS[q.SURFACE_WAREHOUSE_ATTRIBUTION]
    assert query.call_count == 2


@pytest.mark.parametrize("mode", ["full", "fallback", "aggregate"])
def test_loader_preserves_successful_empty_and_selected_route(monkeypatch, mode):
    monkeypatch.setattr(config, "load_settings", lambda: config.Settings(enabled=False))
    monkeypatch.setattr(
        config, "source_for", lambda *_: "dt" if mode == "aggregate" else "account_usage"
    )
    monkeypatch.setattr(q.st, "session_state", {})
    frame = q._empty_warehouse_facts()
    monkeypatch.setattr(q, "_load_from_dt", Mock(return_value=frame))
    monkeypatch.setattr(q, "_warn_if_query_id_overlap", Mock())
    query = Mock(
        side_effect=[RuntimeError("metering") if mode == "fallback" else pd.DataFrame(), frame]
    )
    monkeypatch.setattr(q, "run_query", query)
    result = q.load_warehouse_credit_facts(START, END)
    assert result.frame.empty
    assert result.degraded == (mode == "fallback")
    assert query.call_count == (0 if mode == "aggregate" else 2)
    if mode != "aggregate":
        sql = query.call_args_list[-1].args[0]
        assert CURRENT in sql
        assert ("QUERY_METERING_HISTORY" in sql) == (mode == "full")


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 4), "does not mean zero"),
        (dt.date(2026, 1, 4), dt.date(2026, 1, 5), "includes days before"),
        (dt.date(2026, 1, 5), dt.date(2026, 1, 5), None),
        (dt.date(2026, 1, 6), dt.date(2026, 2, 1), None),
    ],
)
def test_historical_coverage_boundary(start, end, expected):
    notice = ui.ai_functions_warehouse_coverage_notice(start, end)
    if expected is None:
        assert notice is None
    else:
        assert notice is not None
        assert expected in notice
        assert "January 5, 2026" in notice


@pytest.mark.parametrize("variant", ["Full", "Attribution-only"])
def test_reference_includes_membership_dependencies(variant):
    entry = next(
        entry
        for entry in q.sql_reference_for(q.SURFACE_WAREHOUSE_ATTRIBUTION)
        if entry.variant == variant
    )
    assert CURRENT in entry.sources
    assert f"{q.AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY" in entry.sources
    assert (f"{q.AU}.QUERY_METERING_HISTORY" in entry.sources) == (variant == "Full")
