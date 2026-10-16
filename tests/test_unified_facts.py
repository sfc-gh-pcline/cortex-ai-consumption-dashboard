"""Regression tests for the Total Consumption unified fact assembly."""

import datetime as dt

import pandas as pd
import pytest

from utils import agg, queries
from utils.date_ranges import resolve_periods
from utils.view_state import ComparisonState, CoverageState


def test_total_consumption_surface_pills_include_search():
    assert queries.SURFACES == (
        queries.SURFACE_COCO,
        queries.SURFACE_COWORK,
        queries.SURFACE_AGENTS,
        queries.SURFACE_SEARCH,
        queries.SURFACE_REST_API,
        queries.SURFACE_AI_FUNCTIONS,
    )


def test_unified_facts_includes_search_without_user_identity(monkeypatch):
    """Search contributes credits even though its usage views omit USER_NAME."""
    search_facts = pd.DataFrame(
        {
            "USAGE_DATE": [dt.date(2026, 8, 1)],
            "SERVICE_NAME": ["SEARCH_SERVICE"],
            "CONSUMPTION_TYPE": ["SERVING"],
            "MODEL_NAME": ["(none)"],
            "CALLS": [4],
            "TOKENS": [0],
            "CREDITS": [1.5],
        }
    )
    empty_facts = pd.DataFrame()

    monkeypatch.setattr(
        queries,
        "_UNIFIED_SOURCES",
        tuple(
            source._replace(
                loader=lambda *_args, source=source: (
                    search_facts if source.surface == queries.SURFACE_SEARCH else empty_facts
                )
            )
            for source in queries._UNIFIED_SOURCES
        ),
    )

    frame = queries.load_unified_facts(dt.date(2026, 8, 1), dt.date(2026, 8, 2))

    assert frame["SURFACE"].tolist() == [queries.SURFACE_SEARCH]
    assert frame["USER_NAME"].isna().all()
    assert agg.totals(frame)["CREDITS"] == 1.5


def test_global_user_filter_separates_search_rows_without_user_identity():
    """Search remains visible separately, never in a filtered total."""
    frame = pd.DataFrame(
        {
            "SURFACE": [queries.SURFACE_COCO, queries.SURFACE_SEARCH],
            "USER_NAME": ["ALICE", None],
            "MODEL_NAME": ["model-a", "(none)"],
            "CREDITS": [1.0, 2.0],
            "TOKENS": [10, 0],
            "CALLS": [1, 2],
        }
    )

    filtered = agg.apply_filters(frame, users=["ALICE"], models=["model-a"])

    assert filtered.matched["SURFACE"].tolist() == [queries.SURFACE_COCO]
    assert filtered.matched["CREDITS"].sum() == 1.0
    assert filtered.unscoped["SURFACE"].tolist() == [queries.SURFACE_SEARCH]
    assert filtered.unscoped["CREDITS"].sum() == 2.0
    assert filtered.excluded.empty


def test_unavailable_source_does_not_become_zero_usage(monkeypatch):
    def unavailable(*_args):
        raise RuntimeError("synthetic source failure")

    monkeypatch.setattr(
        queries,
        "_UNIFIED_SOURCES",
        (queries._UNIFIED_SOURCES[0]._replace(loader=unavailable),),
    )
    result = queries.load_unified_facts(dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    assert result.empty
    assert result.attrs["unavailable_sources"] == (queries.SURFACE_COCO,)


@pytest.mark.parametrize("outcome", ["empty", "missing_metadata", "none", "exception"])
def test_every_unified_source_contributes_coverage(monkeypatch, outcome):
    periods = resolve_periods(dt.date(2026, 9, 20), "7d")
    assert periods.previous is not None and periods.current is not None
    start, end = periods.previous.start, periods.current.end

    def load(source, *_args):
        if source.surface == queries.SURFACE_SEARCH:
            if outcome == "none":
                return None
            if outcome == "exception":
                raise RuntimeError("synthetic source denied")
        frame = pd.DataFrame(
            {column: [] for column in ("USAGE_DATE", "CREDITS", "TOKENS", "CALLS")}
        )
        if source.surface == queries.SURFACE_SEARCH and outcome == "missing_metadata":
            return frame
        frame.attrs.update(
            fetched_at=dt.datetime(2026, 9, 20, 8, tzinfo=dt.UTC),
            read_started_at=dt.datetime(2026, 9, 20, 8, tzinfo=dt.UTC),
        )
        dependency = (
            f"{queries.SURFACE_SEARCH} — Daily"
            if source.surface == queries.SURFACE_SEARCH
            else source.surface
        )
        return queries._with_coverage(frame, dependency, start, end, "direct")

    monkeypatch.setattr(
        queries,
        "_UNIFIED_SOURCES",
        tuple(
            source._replace(loader=lambda *args, source=source: load(source, *args))
            for source in queries._UNIFIED_SOURCES
        ),
    )
    result = queries.load_unified_facts(start, end)
    coverage = result.attrs["source_coverages"]
    assert len(coverage) == len({item.dependency for item in coverage}) == 6
    search = next(item for item in coverage if item.dependency == "Cortex Search — Daily")
    expected = {
        "empty": CoverageState.AVAILABLE,
        "missing_metadata": CoverageState.UNKNOWN,
        "none": CoverageState.UNAVAILABLE,
        "exception": CoverageState.UNAVAILABLE,
    }
    assert search.state == expected[outcome]
    comparison = agg.compare_periods(result, periods, coverage)
    assert comparison.delta_percent is None
    if outcome in {"none", "exception"}:
        assert comparison.state == ComparisonState.UNAVAILABLE
        assert search.fetched_at is search.served is None


def test_unified_estimates_preserve_missing_token_type_evidence(monkeypatch):
    rest = next(source for source in queries._UNIFIED_SOURCES if source.credits_estimated)
    frame = pd.DataFrame(
        {
            "USAGE_DATE": [dt.date(2026, 9, 19)],
            "MODEL_NAME": ["partially-rated"],
            "CALLS": [1],
            "TOKENS": [15],
            "CREDITS": [0.1],
            "IS_UNRATED": [0],
            "RATE_INCOMPLETE": [True],
            "UNRATED_TOKENS": [5.0],
        }
    )
    monkeypatch.setattr(queries, "_UNIFIED_SOURCES", (rest._replace(loader=lambda *_args: frame),))
    result = queries.load_unified_facts(dt.date(2026, 9, 6), dt.date(2026, 9, 19))
    assert result.RATE_INCOMPLETE.tolist() == [True]
    assert result.UNRATED_TOKENS.tolist() == [5.0]
    assert result.CREDITS.tolist() == [0.1]


def test_one_populated_source_cannot_override_missing_source_coverage(monkeypatch):
    frame = pd.DataFrame(
        {
            "USAGE_DATE": [dt.date(2026, 9, 19)],
            "MODEL_NAME": ["model"],
            "SUB_SURFACE": ["cli"],
            "CALLS": [1],
            "TOKENS": [10],
            "CREDITS": [1.0],
        }
    )
    frame.attrs.update(
        fetched_at=dt.datetime(2026, 9, 20, 8, tzinfo=dt.UTC),
        read_started_at=dt.datetime(2026, 9, 20, 8, tzinfo=dt.UTC),
    )
    start, end = dt.date(2026, 9, 6), dt.date(2026, 9, 19)
    frame = queries._with_coverage(frame, queries.SURFACE_COCO, start, end, "direct")
    monkeypatch.setattr(
        queries,
        "_UNIFIED_SOURCES",
        tuple(
            source._replace(
                loader=lambda *_args, source=source: (
                    frame if source.surface == queries.SURFACE_COCO else pd.DataFrame()
                )
            )
            for source in queries._UNIFIED_SOURCES
        ),
    )
    result = queries.load_unified_facts(start, end)
    assert "coverage" not in result.attrs
    assert len(result.attrs["source_coverages"]) == 6
    comparison = agg.compare_periods(
        result, resolve_periods(dt.date(2026, 9, 20), "7d"), result.attrs["source_coverages"]
    )
    assert comparison.state == ComparisonState.INCOMPLETE
    assert comparison.delta_percent is None
