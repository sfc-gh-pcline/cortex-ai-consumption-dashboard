"""Equal-period and calendar-gap tests with explicit source evidence."""

from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pandas as pd
import pytest

from utils import agg, ui
from utils import queries as q
from utils.date_ranges import resolve_periods
from utils.view_state import ComparisonState, CoverageState, DateWindow, SourceCoverage

PERIODS = resolve_periods(date(2026, 9, 20), "7d")
assert PERIODS.previous is not None and PERIODS.current is not None
WINDOW = DateWindow(PERIODS.previous.start, PERIODS.current.end)
COVERAGE = SourceCoverage(
    "CoCo",
    "direct",
    datetime(2026, 9, 20, 8, tzinfo=UTC),
    WINDOW,
    WINDOW,
    retention_start=date(2025, 9, 21),
    state=CoverageState.AVAILABLE,
    complete_through=date(2026, 9, 19),
)


def facts(previous=2.0, current=2.0):
    return pd.DataFrame(
        {
            "USAGE_DATE": pd.date_range(WINDOW.start, WINDOW.end),
            "CREDITS": [previous] * 7 + [current] * 7,
            "USER_NAME": ["Alice"] * 14,
        }
    )


def test_constant_spend_odd_length_period_has_zero_change():
    result = agg.compare_periods(facts(), PERIODS, [COVERAGE])
    assert result.state == ComparisonState.COMPARABLE
    assert result.current == result.previous == 14.0
    assert result.delta_percent == 0.0


@pytest.mark.parametrize(
    "previous,current,state,delta",
    [
        (0, 0, ComparisonState.BOTH_ZERO, 0.0),
        (0, 1, ComparisonState.ZERO_BASELINE, None),
        (1, 0, ComparisonState.COMPARABLE, -100.0),
    ],
)
def test_zero_baseline_outcomes(previous, current, state, delta):
    result = agg.compare_periods(facts(previous, current), PERIODS, [COVERAGE])
    assert result.state == state
    assert result.delta_percent == delta


@pytest.mark.parametrize(
    "coverage",
    [
        replace(COVERAGE, state=CoverageState.UNKNOWN),
        replace(COVERAGE, complete_through=date(2026, 9, 18)),
        replace(COVERAGE, retention_start=date(2026, 9, 10)),
        replace(COVERAGE, state=CoverageState.UNAVAILABLE),
    ],
)
def test_any_incomplete_component_suppresses_combined_delta(coverage):
    result = agg.compare_periods(facts(), PERIODS, [COVERAGE, coverage])
    assert result.delta_percent is None


def test_missing_rate_suppresses_credit_delta():
    frame = facts()
    frame["IS_UNRATED"] = 0
    frame.loc[0, "IS_UNRATED"] = 1
    result = agg.compare_periods(frame, PERIODS, [COVERAGE])
    assert result.state == ComparisonState.INCOMPLETE
    assert result.delta_percent is None


def test_today_never_enters_completed_totals():
    frame = pd.concat(
        [facts(), pd.DataFrame({"USAGE_DATE": [date(2026, 9, 20)], "CREDITS": [999.0]})]
    )
    result = agg.compare_periods(frame, PERIODS, [COVERAGE])
    assert result.current == result.previous == 14.0


def test_calendar_zero_filling_requires_day_coverage():
    assert PERIODS.current is not None
    sparse = facts().iloc[[7, 9]]
    partial = replace(COVERAGE, state=CoverageState.PARTIAL, complete_through=date(2026, 9, 18))
    result = agg.calendar_daily(sparse, PERIODS.current, [partial])
    assert len(result) == 7
    assert result.CREDITS.iloc[1] == 0.0
    assert pd.isna(result.CREDITS.iloc[-1])
    unknown = agg.calendar_daily(sparse, PERIODS.current, [])
    assert pd.isna(unknown.CREDITS.iloc[1])
    assert unknown.CREDITS.iloc[0] == 2.0


def test_scope_uses_one_union_read_and_identical_filter_for_both_periods(monkeypatch):
    assert PERIODS.current is not None
    frame = facts(1, 2)
    frame.loc[0, "USER_NAME"] = "Bob"
    frame.attrs["coverage"] = COVERAGE
    loader = Mock(return_value=frame)
    monkeypatch.setattr(q, "load_coco_facts", loader)
    monkeypatch.setattr(
        ui.st,
        "session_state",
        {
            "periods": PERIODS,
            "users": ["Alice"],
            "models": [],
            "tags": [],
            "tag_users": None,
        },
    )
    monkeypatch.setattr(ui.st, "caption", lambda *_args: None)
    union = ui.load_period_usage("CoCo", PERIODS.current.start, PERIODS.current.end)
    loader.assert_called_once_with(WINDOW.start, WINDOW.end)
    assert agg.options(union, "USER_NAME") == ["Alice", "Bob"]
    selected = ui.scoped_usage(union, "CoCo")
    assert selected is not None
    assert len(selected) == 7
    comparison = selected.attrs["comparisons"]["CREDITS"]
    assert comparison.current == 14.0
    assert comparison.previous == 6.0
    delta = agg.trend_delta(selected)
    assert delta is not None and delta.startswith("+133.3%")


def test_no_metadata_never_falls_back_to_midpoint_delta():
    assert agg.trend_delta(facts(1, 2)) is None
    assert agg.daily_trend(facts(1, 2)) is None


def test_daily_chart_preserves_calendar_gaps_and_sparkline_is_omitted():
    frame = facts().iloc[[7, 9]].copy()
    frame["TOKENS"] = 10
    frame["CALLS"] = 1
    frame.attrs.update(display_window=PERIODS.current, source_coverages=())
    result = agg.daily(frame)
    assert len(result) == 7
    assert pd.isna(result.CREDITS.iloc[1])
    assert agg.daily_trend(frame) is None
    frame.attrs["source_coverages"] = (COVERAGE,)
    assert agg.daily_trend(frame) == [2.0, 0.0, 2.0, 0.0, 0.0, 0.0, 0.0]


def test_today_does_not_load_until_requested(monkeypatch):
    loader = Mock()
    monkeypatch.setattr(q, "load_direct_dependency", loader)
    monkeypatch.setattr(ui.st, "button", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(ui.st, "caption", lambda *_args: None)
    monkeypatch.setattr(ui.st, "subheader", lambda *_args: None)
    import inspect

    inspect.unwrap(ui.today_section)("CoCo", date(2026, 9, 20))
    loader.assert_not_called()


def test_requested_today_is_direct_and_separate(monkeypatch):
    frame = facts().iloc[:1].copy()
    loader = Mock(return_value=frame)
    monkeypatch.setattr(q, "load_direct_dependency", loader)
    monkeypatch.setattr(ui.st, "button", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ui.st, "caption", lambda *_args: None)
    monkeypatch.setattr(ui.st, "subheader", lambda *_args: None)
    monkeypatch.setattr(ui.st, "dataframe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui.st, "metric", lambda *_args: None)
    monkeypatch.setattr(ui.st, "session_state", {"periods": PERIODS})
    import inspect

    inspect.unwrap(ui.today_section)("CoCo", date(2026, 9, 20))
    loader.assert_called_once_with("CoCo", date(2026, 9, 20), date(2026, 9, 20))
    assert ui.st.session_state["periods"] == PERIODS


def test_warehouse_union_survives_until_comparison(monkeypatch):
    assert PERIODS.current is not None
    frame = facts(1, 2).rename(columns={"CREDITS": "WAREHOUSE_CREDITS"})
    frame.attrs["coverage"] = replace(COVERAGE, dependency=q.SURFACE_WAREHOUSE_ATTRIBUTION)
    loader = Mock(return_value=q.WarehouseFactsResult(frame, False, None))
    monkeypatch.setattr(q, "load_warehouse_credit_facts", loader)
    monkeypatch.setattr(ui.st, "session_state", {"periods": PERIODS})
    monkeypatch.setattr(ui.st, "caption", lambda *_args: None)
    loaded = ui.load_period_warehouse(PERIODS.current.start, PERIODS.current.end)
    assert len(loaded.frame) == 14
    current = ui.scoped_usage(
        loaded.frame, q.SURFACE_WAREHOUSE_ATTRIBUTION, metric="WAREHOUSE_CREDITS"
    )
    assert current is not None
    comparison = current.attrs["comparisons"]["WAREHOUSE_CREDITS"]
    assert comparison.previous == 7
    assert comparison.current == 14
    assert comparison.delta_percent == 100
    loader.assert_called_once_with(WINDOW.start, WINDOW.end)


def test_unavailable_combined_source_retains_observed_amounts():
    result = agg.compare_periods(
        facts(1, 2), PERIODS, [COVERAGE, replace(COVERAGE, state=CoverageState.UNAVAILABLE)]
    )
    assert result.current == 14
    assert result.previous == 7
    assert result.delta_percent is None
    assert result.state == ComparisonState.UNAVAILABLE


def test_partial_token_type_rate_suppresses_delta_even_when_model_exists():
    frame = facts(1, 2)
    frame["IS_UNRATED"] = 0
    frame["RATE_INCOMPLETE"] = False
    frame.loc[0, "RATE_INCOMPLETE"] = True
    result = agg.compare_periods(frame, PERIODS, [COVERAGE])
    assert result.delta_percent is None
    assert result.reason is not None and "token types" in result.reason


def test_prior_only_group_keeps_current_calendar_and_source_specific_gaps(monkeypatch):
    frame = facts()
    frame["SURFACE"] = ["CoCo"] * 7 + ["Cortex Search"] * 7
    frame["TOKENS"] = 10
    frame["CALLS"] = 1
    frame.attrs.update(
        periods=PERIODS,
        source_coverages=(
            COVERAGE,
            replace(COVERAGE, dependency="Cortex Search — Daily", state=CoverageState.UNKNOWN),
        ),
    )
    monkeypatch.setattr(ui.st, "session_state", {})
    monkeypatch.setattr(ui.st, "caption", lambda *_args: None)
    current = ui.scoped_usage(frame)
    daily = agg.daily(current, "SURFACE")
    coco = daily[daily.SURFACE.eq("CoCo")]
    assert len(coco) == 7
    assert coco.CREDITS.eq(0).all()
    assert len(daily) == 14
    assert daily[daily.SURFACE.eq("Cortex Search")].CREDITS.eq(2).all()


def test_empty_grouped_calendar_retains_every_unknown_day():
    frame = facts().iloc[:0].copy()
    frame["SUB_SURFACE"] = pd.Series(dtype=str)
    frame.attrs.update(display_window=PERIODS.current, source_coverages=())
    result = agg.daily(frame, "SUB_SURFACE")
    assert len(result) == 7
    assert result.CREDITS.isna().all()
