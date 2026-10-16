"""Retained results never cross account, role, period, or filter scopes."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from utils import ui
from utils.preflight import PreflightResult
from utils.view_state import CoverageState, DateWindow, SourceCoverage


def test_failed_reload_displays_retained_value_without_delta(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from tests.test_app_workflows import dashboard_facts
    from utils import preflight
    from utils import queries as q

    monkeypatch.setattr(preflight, "run_preflight", lambda: PreflightResult(account="A", role="R"))
    frame = dashboard_facts()
    window = DateWindow(date(2026, 9, 6), date(2026, 9, 19))
    frame.attrs["coverage"] = SourceCoverage(
        "CoCo", "direct", datetime.now(UTC), window, window, state=CoverageState.AVAILABLE
    )
    monkeypatch.setattr(q, "load_coco_facts", lambda *_args: frame.copy())
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_pages/coco.py"))
    app.session_state.start_date = date(2026, 9, 13)
    app.session_state.end_date = date(2026, 9, 19)
    app.session_state.preflight_result = PreflightResult(account="A", role="R")
    app.session_state.active_page_id = "coco"
    app.run()
    assert not app.exception

    def unavailable(*_args):
        raise RuntimeError("offline unavailable")

    monkeypatch.setattr(q, "load_coco_facts", unavailable)
    app.run()
    assert not app.exception
    assert any("Stale retained" in warning.value for warning in app.warning)
    assert app.metric[0].label == "Retained credits (stale)"
    assert app.metric[0].value == "1.00"
    assert not app.metric[0].delta


@pytest.mark.parametrize(
    "changed", ["none", "account", "role", "users", "period", "page", "local", "tag_users"]
)
def test_retained_view_scope_is_exact(monkeypatch, changed):
    class State(dict):
        def __setattr__(self, key, value):
            self[key] = value

    state = State(
        {
            "preflight_result": PreflightResult(account="ACCOUNT_A", role="ROLE_A"),
            "active_page_id": "coco",
            "users": ["Alice"],
        }
    )
    monkeypatch.setattr(ui.st, "session_state", state)
    warnings = []
    monkeypatch.setattr(ui.st, "warning", warnings.append)
    window = DateWindow(date(2026, 9, 1), date(2026, 9, 7))
    coverage = SourceCoverage(
        "CoCo", "direct", datetime.now(UTC), window, window, state=CoverageState.AVAILABLE
    )
    frame = pd.DataFrame({"CREDITS": [3.0]})
    frame.attrs.update(source_coverages=(coverage,), periods=window)
    ui.retain_same_scope(frame, "CoCo", "CREDITS", {})
    unavailable = pd.DataFrame()
    unavailable.attrs.update(
        source_coverages=(replace(coverage, state=CoverageState.UNAVAILABLE),), periods=window
    )
    if changed == "account":
        state["preflight_result"].account = "ACCOUNT_B"
    elif changed == "role":
        state["preflight_result"].role = "ROLE_B"
    elif changed == "users":
        state["users"] = ["Bob"]
    elif changed == "period":
        unavailable.attrs["periods"] = DateWindow(date(2026, 8, 1), date(2026, 8, 7))
    elif changed == "page":
        state["active_page_id"] = "cowork"
    elif changed == "local":
        state["coco_roles"] = ["ADMIN"]
    elif changed == "tag_users":
        state["tag_users"] = []
    result = ui.retain_same_scope(unavailable, "CoCo", "CREDITS", {})
    if changed == "none":
        assert result["CREDITS"].sum() == 3.0
        assert result.attrs["stale"]
        assert result.attrs["comparisons"] == {}
        assert all(item.state == CoverageState.UNKNOWN for item in result.attrs["source_coverages"])
        assert warnings
    else:
        assert result.empty and not warnings


@pytest.mark.parametrize("changed", [False, True])
def test_inflight_preview_is_scoped_and_separates_unattributable(monkeypatch, changed):
    from utils.date_ranges import resolve_periods

    class State(dict):
        def __setattr__(self, key, value):
            self[key] = value

    periods = resolve_periods(date(2026, 9, 20), "7d")
    state = State(
        preflight_result=PreflightResult(account="A", role="R"),
        active_page_id="total_consumption",
        users=["Alice"],
        periods=periods,
    )
    monkeypatch.setattr(ui.st, "session_state", state)
    frame = pd.DataFrame(
        {
            "USAGE_DATE": [date(2026, 9, 19)] * 3,
            "SURFACE": ["CoCo", "CoCo", "Cortex Search"],
            "USER_NAME": ["Alice", "Bob", None],
            "CREDITS": [3.0, 100.0, 5.0],
        }
    )
    window = periods.requested
    frame.attrs.update(
        periods=periods,
        coverage=SourceCoverage(
            "synthetic",
            "direct",
            datetime.now(UTC),
            window,
            window,
            state=CoverageState.AVAILABLE,
        ),
    )
    ui.retain_same_scope(frame, None, "CREDITS", {})
    if changed:
        state["users"] = ["Bob"]
    metrics, warnings, tables = [], [], []
    monkeypatch.setattr(ui.st, "metric", lambda *args: metrics.append(args))
    monkeypatch.setattr(ui.st, "warning", warnings.append)
    monkeypatch.setattr(ui.st, "caption", lambda *_args: None)
    monkeypatch.setattr(ui.st, "dataframe", lambda frame, **_kwargs: tables.append(frame))
    ui.render_retained_reload()
    if changed:
        assert not metrics and not warnings and not tables
    else:
        assert metrics == [
            ("Matching retained credits (stale)", "3.00"),
            ("Not attributable retained credits (stale)", "5.00"),
        ]
        assert "while reloading" in warnings[0]
        assert [table.CREDITS.sum() for table in tables] == [3.0, 5.0]
        assert all(not table.attrs for table in tables)
