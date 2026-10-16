"""Relative-period charts retain actual dates and unknown observations."""

import pandas as pd
import pytest

from utils import agg


def test_relative_periods_preserve_dates_and_null_components():
    current = pd.DataFrame(
        {"USAGE_DATE": ["2026-09-19", "2026-09-18", "2026-09-18"], "CREDITS": [2.0, 1.0, None]}
    )
    previous = pd.DataFrame({"USAGE_DATE": ["2026-09-16", "2026-09-17"], "CREDITS": [3.0, 4.0]})
    result = agg.relative_period_daily(current, previous)
    assert result["Day"].tolist() == [1, 2, 1, 2]
    assert result["Period"].tolist() == ["Current", "Current", "Previous", "Previous"]
    assert pd.isna(result.iloc[0]["CREDITS"])
    assert result["USAGE_DATE"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-09-18",
        "2026-09-19",
        "2026-09-16",
        "2026-09-17",
    ]


def test_relative_periods_reject_unequal_calendars():
    frame = pd.DataFrame({"USAGE_DATE": ["2026-09-18", "2026-09-19"], "CREDITS": [1.0, 2.0]})
    with pytest.raises(ValueError, match="equal numbers"):
        agg.relative_period_daily(frame, frame.iloc[:1])
