"""The coverage line names sources whose newest recorded usage predates the window end."""

import datetime as dt

import pandas as pd

from utils import queries as q
from utils import ui
from utils.view_state import CoverageState, DateWindow, SourceCoverage

START = dt.date(2026, 9, 16)
END = dt.date(2026, 9, 22)


def _frame(dates):
    frame = pd.DataFrame({"USAGE_DATE": pd.to_datetime(dates), "CREDITS": [1.0] * len(dates)})
    frame.attrs.update(
        read_started_at=dt.datetime(2026, 9, 23, 8, tzinfo=dt.UTC),
        fetched_at=dt.datetime(2026, 9, 23, 8, 5, tzinfo=dt.UTC),
    )
    return frame


def _coverage(dependency, latest, state=CoverageState.UNKNOWN):
    return SourceCoverage(
        dependency=dependency,
        actual_source="aggregate",
        fetched_at=None,
        requested=DateWindow(START, END),
        served=DateWindow(START, END),
        state=state,
        latest_usage=latest,
    )


def test_coverage_records_newest_usage_day():
    result = q._with_coverage(
        _frame(["2026-09-16", "2026-09-21", "2026-09-18"]), "CoCo", START, END, "aggregate"
    )
    assert result.attrs["coverage"].latest_usage == dt.date(2026, 9, 21)


def test_coverage_without_rows_or_date_column_has_no_latest_usage():
    empty = q._with_coverage(_frame([]), "CoCo", START, END, "aggregate")
    assert empty.attrs["coverage"].latest_usage is None
    rates = pd.DataFrame({"MODEL_NAME": ["m"], "R_INPUT": [1.0]})
    assert (
        q._with_coverage(rates, q.SURFACE_MODEL_RATES, START, END, "direct")
        .attrs["coverage"]
        .latest_usage
        is None
    )


def test_note_names_only_sources_behind_the_window_end():
    note = ui.freshness_note(
        [
            _coverage("CoCo", dt.date(2026, 9, 21)),
            _coverage("CoWork", dt.date(2026, 9, 22)),
            _coverage("Cortex Search — Daily", dt.date(2026, 9, 18)),
            _coverage("Model Rates", None),
            _coverage("Cortex Inference API", dt.date(2026, 9, 10), CoverageState.UNAVAILABLE),
        ],
        END,
    )
    assert note is not None
    assert note.startswith("Latest recorded usage: CoCo Sep 21, Cortex Search — Daily Sep 18;")
    assert "requested through Sep 22" in note
    assert "CoWork" not in note
    assert "Inference" not in note


def test_no_note_when_every_source_reaches_the_window_end():
    assert ui.freshness_note([_coverage("CoCo", END), _coverage("Model Rates", None)], END) is None
