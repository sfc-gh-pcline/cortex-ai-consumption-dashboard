"""Source evidence stays tied to acquisition, including cache hits and fallbacks."""

import datetime as dt
from unittest.mock import Mock

import pandas as pd
import pytest

from utils import config
from utils import queries as q
from utils.view_state import CoverageState, DateWindow

START = dt.date(2026, 9, 13)
END = dt.date(2026, 9, 19)


def acquired_frame(hour=8):
    frame = pd.DataFrame({"CREDITS": [1.0]})
    frame.attrs.update(
        read_started_at=dt.datetime(2026, 9, 20, hour, tzinfo=dt.UTC),
        fetched_at=dt.datetime(2026, 9, 20, hour, 5, tzinfo=dt.UTC),
    )
    return frame


def test_query_cache_keeps_acquisition_time_and_separates_generations(monkeypatch):
    conn = Mock()
    conn.query.side_effect = lambda *_args, **_kwargs: pd.DataFrame({"credits": [1.0]})
    monkeypatch.setattr(q, "_connection", lambda: conn)
    q.run_query.clear()
    try:
        first = q.run_query("synthetic-query", snapshot_timestamp="generation-one")
        repeated = q.run_query("synthetic-query", snapshot_timestamp="generation-one")
        assert conn.query.call_count == 1
        assert first.attrs == repeated.attrs
        assert first.attrs["read_started_at"] <= first.attrs["fetched_at"]
        assert first.attrs["fetched_at"].tzinfo == dt.UTC
        q.run_query("synthetic-query", snapshot_timestamp="generation-two")
        assert conn.query.call_count == 2
    finally:
        q.run_query.clear()


def test_direct_coverage_uses_read_start_not_query_completion():
    frame = acquired_frame(0)
    frame.attrs["fetched_at"] = dt.datetime(2026, 9, 20, 4, tzinfo=dt.UTC)
    result = q._with_coverage(frame, "CoCo", START, END, "direct")
    evidence = result.attrs["coverage"]
    assert evidence.state == CoverageState.PARTIAL
    assert evidence.complete_through == dt.date(2026, 9, 18)
    assert evidence.served == DateWindow(START, END)


def test_fresh_direct_empty_is_available_not_unavailable():
    frame = acquired_frame().iloc[:0]
    result = q._with_coverage(frame, "CoCo", START, END, "direct")
    assert result.empty
    assert result.attrs["coverage"].state == CoverageState.AVAILABLE


@pytest.mark.parametrize(
    "dependency", ["Cortex AI Functions", "Cortex Inference API", "Warehouse Attribution"]
)
def test_unknown_source_bound_never_invents_completeness(dependency):
    result = q._with_coverage(acquired_frame(), dependency, START, END, "direct")
    assert result.attrs["coverage"].state == CoverageState.UNKNOWN
    assert result.attrs["coverage"].complete_through is None


def test_missing_read_timestamp_is_not_replaced_with_now():
    result = q._with_coverage(pd.DataFrame(), "CoCo", START, END, "direct")
    assert result.attrs["coverage"].fetched_at is None
    assert result.attrs["coverage"].state == CoverageState.UNKNOWN


def test_aggregate_snapshot_does_not_imply_complete_ingestion():
    frame = acquired_frame()
    frame.attrs["snapshot_timestamp"] = "2026-09-20T01:03:00+00:00"
    result = q._with_coverage(frame, "Cortex Search — Daily", START, END, "aggregate")
    evidence = result.attrs["coverage"]
    assert evidence.snapshot_at == dt.datetime(2026, 9, 20, 1, 3, tzinfo=dt.UTC)
    assert evidence.state == CoverageState.UNKNOWN
    assert evidence.complete_through is None
    assert evidence.retention_start is None


def test_aggregate_fallback_records_actual_direct_route(monkeypatch):
    monkeypatch.setattr(q, "utc_today", lambda: dt.date(2026, 9, 20))
    monkeypatch.setattr(config, "load_settings", lambda: config.Settings(enabled=False))
    monkeypatch.setattr(config, "source_for", lambda *_args: "dt")
    monkeypatch.setattr(q, "effective_window", lambda start, end, **_kwargs: (start, end))
    monkeypatch.setattr(q, "_load_from_dt", lambda *_args: None)
    monkeypatch.setattr(q.st, "session_state", {})
    result = q._source_aware_load("CoCo", START, END, acquired_frame)
    assert result.attrs["coverage"].actual_source == "direct"
    assert result.attrs["coverage"].state == CoverageState.AVAILABLE
    assert "unavailable" in q.st.session_state["_degraded"]["CoCo"]


def test_retention_and_source_introduction_are_not_zero_coverage():
    result = q._with_coverage(acquired_frame(), "CoCo", dt.date(2025, 1, 1), END, "direct")
    assert result.attrs["coverage"].state == CoverageState.PARTIAL
    functions = q._with_coverage(acquired_frame(), "Cortex AI Functions", START, END, "direct")
    assert functions.attrs["coverage"].retention_start == dt.date(2026, 1, 5)


def test_dependency_result_preserves_error_without_fabricated_fetch(monkeypatch):
    monkeypatch.setattr(q, "load_dependency", Mock(side_effect=RuntimeError("synthetic denied")))
    result = q.load_dependency_result("CoCo", START, END)
    assert result.frame.empty
    assert result.coverage.state == CoverageState.UNAVAILABLE
    assert result.coverage.fetched_at is None
    assert result.coverage.served is None
    assert result.coverage.reason == "synthetic denied"


def test_dependency_result_wraps_successful_read_evidence(monkeypatch):
    frame = q._with_coverage(acquired_frame(), "CoCo", START, END, "direct")
    monkeypatch.setattr(q, "load_dependency", Mock(return_value=frame))
    result = q.load_dependency_result("CoCo", START, END)
    assert result.coverage is frame.attrs["coverage"]
    pd.testing.assert_frame_equal(result.frame, frame)


def test_invalid_snapshot_remains_unknown():
    frame = acquired_frame()
    frame.attrs["snapshot_timestamp"] = "invalid timestamp"
    result = q._with_coverage(frame, "CoCo", START, END, "aggregate")
    assert result.attrs["coverage"].snapshot_at is None
    assert result.attrs["coverage"].state == CoverageState.UNKNOWN


def test_token_type_rate_coverage_detects_partial_model_and_unknown_tokens():
    rates = pd.DataFrame(
        {
            "MODEL_NAME": ["partial", "free"],
            "R_INPUT": [0.01, 0.0],
            "R_OUTPUT": [None, 0.0],
            "R_CACHE_READ": [None, 0.0],
            "R_CACHE_WRITE": [None, 0.0],
        }
    )
    frame = pd.DataFrame(
        {
            "MODEL_NAME": ["partial", "free", "absent", "partial"],
            "TOK_INPUT": [10, 10, 10, 10],
            "TOK_OUTPUT": [5, 5, 5, 0],
            "TOKENS": [15, 15, 15, 12],
        }
    )
    result = q._with_rate_coverage(frame, rates)
    assert result.UNRATED_TOKENS.tolist() == [5, 0, 15, 2]
    assert result.RATE_INCOMPLETE.tolist() == [True, False, True, True]
    assert "UNRATED_TOKENS" not in frame


def test_rest_uses_one_identical_rate_snapshot_for_query_and_evidence(monkeypatch):
    rates = pd.DataFrame({"MODEL_NAME": ["model"], **{key: [0.01] for key in q.RATE_COLUMNS}})
    frame = pd.DataFrame({"MODEL_NAME": ["model"], "TOKENS": [10], "TOK_INPUT": [10]})
    rate_loader = Mock(return_value=rates)
    builder = Mock(return_value=("synthetic-rest", ()))
    monkeypatch.setattr(q, "load_model_rates", rate_loader)
    monkeypatch.setattr(q, "rest_facts_sql", builder)
    monkeypatch.setattr(q, "run_query", Mock(return_value=frame))
    result = q.load_rest_facts(START, END)
    rate_loader.assert_called_once_with()
    builder.assert_called_once_with(START, END, rates=rates)
    assert result.UNRATED_TOKENS.tolist() == [0]
