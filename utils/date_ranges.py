"""Date-range presets and inclusive UTC calendar-window helpers."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from types import MappingProxyType

from utils.view_state import ComparisonPeriods, DateWindow

DATE_PRESETS = MappingProxyType(
    {
        "7d": 7,
        "30d": 30,
        "60d": 60,
        "90d": 90,
    }
)
CUSTOM = "Custom"
DEFAULT_PRESET = "7d"


def preset_window(today: dt.date, preset: str) -> tuple[dt.date, dt.date]:
    """Return the inclusive UTC calendar window for a supported preset.

    Args:
        today: Current UTC day, excluded from completed presets.
        preset: Preset label from :data:`DATE_PRESETS`.

    Returns:
        First and last calendar days in the requested inclusive window.

    Raises:
        ValueError: If ``preset`` is not a supported preset label.
    """
    try:
        days = DATE_PRESETS[preset]
    except KeyError as exc:
        raise ValueError(f"Unsupported date preset: {preset}") from exc
    return today - dt.timedelta(days=days), today - dt.timedelta(days=1)


def resolve_periods(
    today: dt.date, preset: str, custom: DateWindow | None = None
) -> ComparisonPeriods:
    """Resolve adjacent equal completed periods without mixing in today.

    Args:
        today: Current UTC date.
        preset: Supported preset or Custom.
        custom: Validated requested custom range, required for Custom.

    Returns:
        Completed current/prior windows and a separate optional partial day.

    Raises:
        ValueError: If a custom range is absent or extends into the future.
    """
    if preset == CUSTOM:
        if custom is None:
            raise ValueError("Select both custom endpoints before applying.")
        requested = custom
    else:
        requested = DateWindow(*preset_window(today, preset))
    if requested.end > today:
        raise ValueError("The selected range cannot extend beyond today UTC.")
    partial = DateWindow(today, today) if requested.end == today else None
    if requested.start == today:
        return ComparisonPeriods(requested, None, None, partial)
    current = DateWindow(requested.start, min(requested.end, today - dt.timedelta(days=1)))
    previous = DateWindow(
        current.start - dt.timedelta(days=current.days), current.start - dt.timedelta(days=1)
    )
    return ComparisonPeriods(requested, current, previous, partial)


def resolve_custom_input(today: dt.date, chosen: object) -> ComparisonPeriods:
    """Validate both widget endpoints before committing a custom selection.

    Args:
        today: Current UTC date and latest allowed endpoint.
        chosen: Native date-range widget value, possibly incomplete.

    Returns:
        Validated completed periods and optional partial today.

    Raises:
        ValueError: If endpoints are absent, reversed, or outside the control bounds.
        TypeError: If either endpoint is not a calendar date.
    """
    if (
        not isinstance(chosen, (tuple, list))
        or len(chosen) != 2
        or any(endpoint is None for endpoint in chosen)
    ):
        raise ValueError("Select both dates before applying.")
    start, end = chosen
    if (
        not isinstance(start, dt.date)
        or not isinstance(end, dt.date)
        or isinstance(start, dt.datetime)
        or isinstance(end, dt.datetime)
    ):
        raise TypeError("Both endpoints must be calendar dates.")
    requested = DateWindow(start, end)
    if requested.start < today - dt.timedelta(days=730):
        raise ValueError("The selected range starts before the supported date control boundary.")
    return resolve_periods(today, CUSTOM, requested)


def parse_legacy_dates(today: dt.date, values: Sequence[str]) -> tuple[dt.date, dt.date]:
    """Import a complete legacy dates link without guessing a missing endpoint.

    Args:
        today: Current UTC day used to validate the control bounds.
        values: Repeated dates query parameters from an older dashboard link.

    Returns:
        Validated start and end dates, including identical same-day endpoints.

    Raises:
        ValueError: If the link is incomplete, malformed, or outside allowed bounds.
    """
    if len(values) != 2:
        raise ValueError("Legacy dates link needs two endpoints. Select Start date and End date.")
    try:
        chosen = tuple(dt.date.fromisoformat(value) for value in values)
    except ValueError as exc:
        raise ValueError("Legacy dates link contains an invalid date.") from exc
    periods = resolve_custom_input(today, chosen)
    return periods.requested.start, periods.requested.end
