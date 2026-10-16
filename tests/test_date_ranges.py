"""Offline tests for shared date-range filter behavior."""

from __future__ import annotations

import datetime as dt

import pytest

from utils.date_ranges import (
    CUSTOM,
    DATE_PRESETS,
    preset_window,
    resolve_custom_input,
    resolve_periods,
)
from utils.view_state import DateWindow


def test_date_preset_labels_match_sidebar_contract():
    assert tuple(DATE_PRESETS) == ("7d", "30d", "60d", "90d")
    assert CUSTOM == "Custom"


@pytest.mark.parametrize(
    ("preset", "expected_start"),
    [
        ("7d", dt.date(2026, 8, 12)),
        ("30d", dt.date(2026, 7, 20)),
        ("60d", dt.date(2026, 6, 20)),
        ("90d", dt.date(2026, 5, 21)),
    ],
)
def test_preset_window_excludes_today_and_keeps_requested_day_count(preset, expected_start):
    today = dt.date(2026, 8, 19)

    start_date, end_date = preset_window(today, preset)

    assert (start_date, end_date) == (expected_start, today - dt.timedelta(days=1))
    assert (end_date - start_date).days + 1 == DATE_PRESETS[preset]


def test_preset_window_rejects_unsupported_preset():
    with pytest.raises(ValueError, match="Unsupported date preset"):
        preset_window(dt.date(2026, 8, 19), "12m")


def test_seven_completed_days_and_previous_period():
    periods = resolve_periods(dt.date(2026, 9, 17), "7d")
    assert periods.current == DateWindow(dt.date(2026, 9, 10), dt.date(2026, 9, 16))
    assert periods.previous == DateWindow(dt.date(2026, 9, 3), dt.date(2026, 9, 9))
    assert periods.partial_today is None


@pytest.mark.parametrize("today", [dt.date(2024, 3, 1), dt.date(2026, 1, 1), dt.date(2026, 5, 1)])
def test_calendar_boundaries_have_adjacent_equal_periods(today):
    periods = resolve_periods(today, "30d")
    assert periods.current is not None
    assert periods.previous is not None
    assert periods.current.days == periods.previous.days == 30
    assert periods.previous.end + dt.timedelta(days=1) == periods.current.start
    assert periods.current.end + dt.timedelta(days=1) == today


def test_custom_today_is_separate():
    today = dt.date(2026, 9, 20)
    periods = resolve_periods(today, CUSTOM, DateWindow(today - dt.timedelta(days=3), today))
    assert periods.current is not None
    assert periods.previous is not None
    assert periods.current.days == periods.previous.days == 3
    assert periods.partial_today == DateWindow(today, today)
    today_only = resolve_periods(today, CUSTOM, DateWindow(today, today))
    assert today_only.current is today_only.previous is None
    assert today_only.partial_today == DateWindow(today, today)


def test_custom_range_requires_endpoints_and_rejects_future():
    today = dt.date(2026, 9, 20)
    with pytest.raises(ValueError, match="both custom endpoints"):
        resolve_periods(today, CUSTOM)
    with pytest.raises(ValueError, match="beyond today"):
        resolve_periods(today, CUSTOM, DateWindow(today, today + dt.timedelta(days=1)))


@pytest.mark.parametrize(
    "chosen",
    [
        None,
        (),
        (dt.date(2026, 9, 18),),
        ("2026-09-18", "2026-09-19"),
        (dt.date(2026, 9, 19), dt.date(2026, 9, 18)),
        (dt.date(2026, 9, 20), dt.date(2026, 9, 21)),
        (dt.date(2020, 1, 1), dt.date(2026, 9, 19)),
    ],
)
def test_custom_widget_input_is_validated_before_application(chosen):
    with pytest.raises((TypeError, ValueError)):
        resolve_custom_input(dt.date(2026, 9, 20), chosen)


def test_complete_custom_widget_input_keeps_requested_days():
    chosen = (dt.date(2026, 8, 4), dt.date(2026, 8, 10))
    result = resolve_custom_input(dt.date(2026, 9, 20), chosen)
    assert result.requested == DateWindow(*chosen)
    assert result.previous == DateWindow(dt.date(2026, 7, 28), dt.date(2026, 8, 3))
