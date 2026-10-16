"""Offline page smoke checks with synthetic populated and unsupported-scope facts."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from utils import agg, config, preflight, ui
from utils import queries as q
from utils.date_ranges import CUSTOM, resolve_periods
from utils.snowflake import surface_to_suffix
from utils.view_state import CoverageState, DateWindow, FilterSelection, SourceCoverage

pytestmark = pytest.mark.usefixtures("authorized_admin")


def dashboard_facts():
    """Supply all existing page dimensions without copying account data."""
    return pd.DataFrame(
        {
            "USAGE_DATE": [date(2026, 9, 18), date(2026, 9, 19)],
            "USAGE_HOUR": pd.to_datetime(["2026-09-18", "2026-09-19"]),
            "SURFACE": ["CoCo", "CoCo"],
            "USER_NAME": ["Alice", "Bob"],
            "MODEL_NAME": ["model-a", "model-b"],
            "SUB_SURFACE": ["cli", "desktop"],
            "ROLE_NAME": ["ANALYST", "ADMIN"],
            "INFERENCE_REGION": ["region-a", "region-b"],
            "QUERY_TAG": ["tag-a", "tag-b"],
            "AGENT_FQN": ["DB.SCHEMA.AGENT_A", "DB.SCHEMA.AGENT_B"],
            "COWORK_NAME": ["Demo A", "Demo B"],
            "AGENT_TAG_LABELS": ["team = a", "team = b"],
            "FUNCTION_NAME": ["AI_COMPLETE", "AI_CLASSIFY"],
            "SERVICE_NAME": ["SEARCH_A", "SEARCH_B"],
            "SERVICE_TYPE": ["cortex_agents", "cortex_analyst"],
            "CONSUMPTION_TYPE": ["SERVING", "EMBED_TEXT_TOKENS"],
            "QUERY_ID": ["query-a", "query-b"],
            "REQUEST_ID": ["request-a", None],
            "SOURCE_VIEW": ["ATTRIBUTION", "ATTRIBUTION"],
            "WAREHOUSE_NAME": ["DEMO_WH", "DEMO_WH"],
            "TIER": ["per_request", "surface"],
            "CREDITS": [1.0, 0.001],
            "TOKENS": [100, 20],
            "CALLS": [2, 1],
            "ROOT_REQUESTS": [1, 1],
            "CHILD_REQUESTS": [1, 0],
            "SQL_QUERY_CREDITS": [0.2, 0.0],
            "AI_FUNCTIONS_CREDITS": [0.1, 0.0],
            "INCOMPLETE_CALLS": [0, 1],
            "IS_UNRATED": [0, 1],
            "CREDITS_ESTIMATED": [False, True],
            "COMPUTE_CREDITS": [0.2, 0.1],
            "CLOUD_SERVICES_CREDITS": [0.0, 0.0],
            "WAREHOUSE_CREDITS": [0.2, 0.1],
            "TOK_INPUT": [80, 10],
            "TOK_OUTPUT": [20, 10],
        }
    )


@pytest.mark.parametrize(
    "page",
    [
        "total_consumption",
        "coco",
        "cowork",
        "cortex_agents",
        "cortex_ai_functions",
        "cortex_inference_api",
        "cortex_search",
        "warehouse_attribution",
    ],
)
@pytest.mark.parametrize("models", [[], ["model-a"]])
@pytest.mark.parametrize("comparison", [False, True])
def test_populated_pages_render_without_network(monkeypatch, page, models, comparison):
    for name in (
        "load_unified_facts",
        "load_coco_facts",
        "load_cowork_facts",
        "load_cowork_service_mix",
        "load_agent_facts",
        "load_ai_fn_facts",
        "load_rest_facts",
        "load_search_daily_facts",
        "load_search_serving_facts",
        "load_search_batch_facts",
    ):
        monkeypatch.setattr(q, name, lambda *_args: dashboard_facts())
    monkeypatch.setattr(
        q,
        "load_warehouse_credit_facts",
        lambda *_args: q.WarehouseFactsResult(dashboard_facts(), False, None),
    )
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "app_pages" / f"{page}.py"))
    if comparison:
        app.session_state.periods = resolve_periods(
            date(2026, 9, 20), CUSTOM, DateWindow(date(2026, 9, 18), date(2026, 9, 19))
        )
    for key, value in {
        "start_date": date(2026, 9, 18),
        "end_date": date(2026, 9, 19),
        "users": [],
        "models": models,
        "tags": [],
        "tag_users": None,
    }.items():
        app.session_state[key] = value
    app.run()
    assert not app.exception
    if models and page in {"cowork", "cortex_agents", "cortex_search", "warehouse_attribution"}:
        assert any("Not attributable" in warning.value for warning in app.warning)
    if page == "total_consumption":
        assert {metric.label for metric in app.metric} == {
            "AI usage credits",
            "Reported credits",
            "Estimated credits",
            "Attributed users",
        }
        assert len(app.metric) == 4
    if page in {"coco", "cortex_ai_functions"}:
        assert "Usage records" in {metric.label for metric in app.metric}
    if page == "cortex_search":
        assert "Services" in {metric.label for metric in app.metric}
        assert "Calls" not in {metric.label for metric in app.metric}


@pytest.fixture
def entrypoint(monkeypatch):
    """Run the real entrypoint with source I/O replaced and prewarming isolated."""
    today = date(2026, 9, 20)
    loads = []

    def load(start, end):
        loads.append((start, end))
        frame = dashboard_facts()
        frame["IS_UNRATED"] = 0
        window = DateWindow(start, end)
        frame.attrs["coverage"] = SourceCoverage(
            "synthetic",
            "direct",
            datetime(2026, 9, 20, 8, tzinfo=UTC),
            window,
            window,
            retention_start=date(2025, 9, 21),
            state=CoverageState.AVAILABLE,
            complete_through=date(2026, 9, 19),
        )
        return frame

    monkeypatch.setattr(q, "utc_today", lambda: today)
    monkeypatch.setattr(preflight, "run_preflight", lambda: None)
    monkeypatch.setattr(config, "load_settings", config.Settings)
    monkeypatch.setattr(config, "aggregate_snapshot_identity_for", lambda *_args: ("direct", ""))
    monkeypatch.setattr(q, "warm_dependencies_for", lambda _page: ())
    monkeypatch.setattr(q, "load_user_tags", lambda: pd.DataFrame())
    for name in (
        "load_unified_facts",
        "load_coco_facts",
        "load_cowork_facts",
        "load_cowork_service_mix",
        "load_agent_facts",
        "load_ai_fn_facts",
        "load_rest_facts",
        "load_search_daily_facts",
        "load_search_serving_facts",
        "load_search_batch_facts",
    ):
        monkeypatch.setattr(q, name, load)
    monkeypatch.setattr(
        q,
        "load_warehouse_credit_facts",
        lambda start, end: q.WarehouseFactsResult(load(start, end), False, None),
    )
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"))
    return app, loads


def test_driver_drilldown_url_preserves_date_and_applies_user(entrypoint):
    app, _loads = entrypoint
    app.query_params["range"] = "Custom"
    app.query_params["start"] = "2026-09-18"
    app.query_params["end"] = "2026-09-19"
    app.run()
    app.selectbox(key="overview_service").set_value("CoCo").run()
    app.selectbox(key="overview_dimension").set_value("User").run()
    app.selectbox(key="overview_driver_USER_NAME").set_value("Alice").run()
    assert not app.exception
    link = next(item for item in app.get("link_button") if item.proto.label == "Open service")
    assert link.proto.url == "/coco?users=Alice&range=Custom&start=2026-09-18&end=2026-09-19"


@pytest.mark.parametrize(
    ("page", "local_layout"),
    [
        ("total_consumption", None),
        ("coco", "columns"),
        ("cowork", "columns"),
        ("cortex_agents", None),
        ("cortex_search", "container"),
        ("cortex_inference_api", "columns"),
        ("cortex_ai_functions", "columns"),
        ("warehouse_attribution", "container"),
    ],
)
def test_filter_rows_use_native_bottom_alignment(entrypoint, monkeypatch, page, local_layout):
    import streamlit as st

    containers = Mock(wraps=st.container)
    columns = Mock(wraps=st.columns)
    monkeypatch.setattr(st, "container", containers)
    monkeypatch.setattr(st, "columns", columns)
    app, _loads = entrypoint
    app.switch_page(f"app_pages/{page}.py").run()
    assert not app.exception
    aligned_rows = [
        call
        for call in containers.call_args_list
        if call.kwargs.get("horizontal") and call.kwargs.get("vertical_alignment") == "bottom"
    ]
    assert len(aligned_rows) >= (2 if local_layout == "container" else 1)
    if local_layout == "columns":
        assert any(
            call.args == (2,) and call.kwargs.get("vertical_alignment") == "bottom"
            for call in columns.call_args_list
        )


def test_scope_filter_keeps_warning_and_widget_in_one_container(monkeypatch):
    from contextlib import contextmanager

    import streamlit as st

    events = []

    @contextmanager
    def container():
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(st, "session_state", {"roles": ["ABSENT_ROLE"]})
    monkeypatch.setattr(st, "container", container)
    monkeypatch.setattr(st, "warning", lambda _message: events.append("warning"))
    widget = Mock(side_effect=lambda *_args, **_kwargs: events.append("widget") or ["ABSENT_ROLE"])
    monkeypatch.setattr(st, "multiselect", widget)
    assert ui.scope_filter("Role", ["ADMIN"], key="roles") == ["ABSENT_ROLE"]
    assert events == ["enter", "warning", "widget", "exit"]
    widget.assert_called_once_with(
        "Role", ["ABSENT_ROLE", "ADMIN"], key="roles", bind="query-params"
    )


def test_previous_period_table_retains_real_dates(entrypoint):
    app, _loads = entrypoint
    app.run()
    app.switch_page("app_pages/coco.py").run()
    app.toggle(key="compare_coco").set_value(True).run()
    app.segmented_control(key="chart_coco_CREDITS").set_value("Table").run()
    assert not app.exception
    tables = [table.value for table in app.dataframe if "Period" in table.value]
    assert len(tables) == 1
    assert len(tables[0]) == 14
    assert set(tables[0]["Period"]) == {"Current", "Previous"}
    assert tables[0]["Day"].tolist() == list(range(1, 8)) * 2
    next(button for button in app.button if button.label == "Reset view").click().run()
    assert not app.exception
    assert app.toggle(key="compare_coco").value is False
    assert app.segmented_control(key="chart_coco_CREDITS").value == "Chart"


def test_search_auxiliary_reads_require_explicit_detail_selection(entrypoint, monkeypatch):
    app, _loads = entrypoint
    serving = Mock(wraps=q.load_search_serving_facts)
    batch = Mock(wraps=q.load_search_batch_facts)
    warehouse = Mock(wraps=q.load_warehouse_credit_facts)
    monkeypatch.setattr(q, "load_search_serving_facts", serving)
    monkeypatch.setattr(q, "load_search_batch_facts", batch)
    monkeypatch.setattr(q, "load_warehouse_credit_facts", warehouse)
    app.switch_page("app_pages/cortex_search.py").run()
    assert not app.exception
    serving.assert_not_called()
    batch.assert_not_called()
    warehouse.assert_not_called()
    app.toggle(key="detail_search").set_value(True).run()
    assert not app.exception
    assert serving.call_count == batch.call_count == warehouse.call_count == 1
    assert any(header.value == "Hourly Serving Trend" for header in app.header)


def test_superseded_load_does_not_commit_warm_state_or_diagnostics(entrypoint, monkeypatch):
    import streamlit as st

    app, _loads = entrypoint
    original = q.load_dependency_result
    monkeypatch.setattr(q, "initial_dependencies_for", lambda _page: (q.SURFACE_COCO,))
    monkeypatch.setattr(q, "dependency_generation", lambda *_args: "")

    def superseded(dependency, start, end):
        result = original(dependency, start, end)
        st.session_state.load_request_id = "replacement-navigation"
        return result

    monkeypatch.setattr(q, "load_dependency_result", superseded)
    app.run()
    assert not app.exception
    assert app.session_state.warm["deps"] == {}
    assert "last_warm_diagnostics" not in app.session_state
    assert not app.metric


def test_loading_uses_modal_without_raw_diagnostics(entrypoint, monkeypatch):
    import streamlit as st

    app, _loads = entrypoint
    original = q.load_dependency_result
    calls = []
    monkeypatch.setattr(
        q, "initial_dependencies_for", lambda _page: (q.SURFACE_COCO, q.SURFACE_AGENTS)
    )
    monkeypatch.setattr(q, "dependency_generation", lambda *_args: "")

    def interrupted(dependency, start, end):
        calls.append(dependency)
        if len(calls) == 2:
            st.stop()
        return original(dependency, start, end)

    monkeypatch.setattr(q, "load_dependency_result", interrupted)
    app.run()
    assert not app.exception
    assert not app.get("json")
    assert app.get("dialog")
    assert any("CoCo" in item.value for item in app.markdown)
    outcome_box = next(
        block for block in app.get("flex_container") if block.proto.id.endswith("loading_outcomes")
    )
    assert outcome_box.proto.height_config.pixel_height == 120


@pytest.mark.parametrize("page", ["cortex_search", "coco", "total_consumption"])
@pytest.mark.parametrize("empty", [False, True])
def test_finished_modal_closes_before_page_early_exit(entrypoint, monkeypatch, page, empty):
    app, _loads = entrypoint
    if empty:
        monkeypatch.setattr(q, "load_dependency", lambda *_args: dashboard_facts().iloc[:0])
        monkeypatch.setattr(q, "load_unified_facts", lambda *_args: dashboard_facts().iloc[:0])
    app.switch_page(f"app_pages/{page}.py").run()
    assert not app.exception
    assert not any(element.proto.dialog.is_open for element in app.get("dialog"))


def test_failure_is_not_retried_by_filters_page_or_rerun(entrypoint, monkeypatch):
    app, _loads = entrypoint
    monkeypatch.setattr(q, "warm_dependencies_for", lambda _page: (q.SURFACE_COCO,))
    loader = Mock(side_effect=RuntimeError("synthetic source unavailable"))
    monkeypatch.setattr(q, "load_coco_facts", loader)
    app.switch_page("app_pages/coco.py").run()
    assert not app.exception
    loader.assert_called_once()
    app.run()
    loader.assert_called_once()
    next(button for button in app.button if button.label == "Reload results").click().run()
    assert loader.call_count == 2


def test_reload_preserves_local_controls_and_native_url(entrypoint):
    app, _loads = entrypoint
    app.query_params["users"] = "Alice"
    app.query_params["coco_roles"] = "ANALYST"
    app.switch_page("app_pages/coco.py").run()
    assert app.multiselect(key="coco_roles").value == ["ANALYST"]
    next(button for button in app.button if button.label == "Reload results").click().run()
    assert not app.exception
    assert app.multiselect(key="users").value == ["Alice"]
    assert app.multiselect(key="coco_roles").value == ["ANALYST"]
    assert app.query_params["coco_roles"] == ["ANALYST"]


def test_reload_renders_retained_results_before_source_work(entrypoint, monkeypatch):
    import streamlit as st

    app, _loads = entrypoint
    app.session_state.preflight_done = True
    app.session_state.preflight_result = preflight.PreflightResult(account="A", role="R")
    app.switch_page("app_pages/coco.py").run()
    assert not app.exception
    events = []
    original_render = ui.render_retained_reload
    original_load = q.load_dependency_result

    def render():
        original_render()
        events.append("rendered")

    def load(*args):
        events.append("read")
        st.session_state.load_request_id = "superseded"
        return original_load(*args)

    monkeypatch.setattr(ui, "render_retained_reload", render)
    monkeypatch.setattr(q, "initial_dependencies_for", lambda _page: (q.SURFACE_COCO,))
    monkeypatch.setattr(q, "load_dependency_result", load)
    next(button for button in app.button if button.label == "Reload results").click().run()
    assert not app.exception
    assert events == ["rendered", "read"]
    assert app.metric[0].label == "Matching retained credits (stale)"
    assert app.metric[0].value == "1.00"
    assert not app.metric[0].delta
    assert "last_warm_diagnostics" not in app.session_state


def test_navigation_cancels_pending_page_refresh_even_on_reference(entrypoint):
    app, _loads = entrypoint
    app.switch_page("app_pages/coco.py").run()
    request = app.session_state.load_request_id
    app.session_state.refreshing = True
    app.session_state.refresh_page_id = "coco"
    app.switch_page("app_pages/sql.py").run()
    assert not app.exception
    assert not app.session_state.refreshing
    assert app.session_state.load_request_id != request
    assert not app.metric


def test_successful_reload_preserves_bound_filters_without_cleanup_rerun(entrypoint, monkeypatch):
    app, _loads = entrypoint
    app.switch_page("app_pages/coco.py").run()
    app.multiselect(key="users").select("Alice").run()
    app.multiselect(key="coco_roles").select("ANALYST").run()
    monkeypatch.setattr(q, "initial_dependencies_for", lambda _page: (q.SURFACE_COCO,))
    monkeypatch.setattr(q, "dependency_generation", lambda *_args: "")
    next(button for button in app.button if button.label == "Reload results").click().run()
    assert not app.exception
    assert app.multiselect(key="users").value == ["Alice"]
    assert app.multiselect(key="coco_roles").value == ["ANALYST"]
    assert app.metric[0].value == "1.00"
    assert not any("Stale retained" in warning.value for warning in app.warning)


def test_entrypoint_reads_completed_union_without_loading_today(entrypoint, monkeypatch):
    app, loads = entrypoint
    today_loader = Mock()
    monkeypatch.setattr(q, "load_direct_dependency", today_loader)
    app.run()
    assert not app.exception
    assert set(loads) == {(date(2026, 9, 6), date(2026, 9, 19))}
    assert app.session_state.periods.current == DateWindow(date(2026, 9, 13), date(2026, 9, 19))
    today_loader.assert_not_called()


def test_entrypoint_draft_dates_preserve_last_applied_view(entrypoint):
    app, loads = entrypoint
    app.run()
    applied_periods = app.session_state.periods
    app.session_state["range"] = CUSTOM
    app.run()
    app.date_input(key="start").set_value(date(2026, 8, 4)).run()
    assert not app.exception
    assert app.session_state.periods == applied_periods
    app.button(key="FormSubmitter:custom_period-Apply dates").click().run()
    assert not app.exception
    assert app.session_state.periods == applied_periods
    assert any("both dates" in error.value for error in app.error)
    assert set(loads) == {(date(2026, 9, 6), date(2026, 9, 19))}
    app.date_input(key="end").set_value(date(2026, 8, 10)).run()
    assert app.session_state.periods == applied_periods
    app.button(key="FormSubmitter:custom_period-Apply dates").click().run()
    assert not app.exception
    assert app.session_state.periods.current == DateWindow(date(2026, 8, 4), date(2026, 8, 10))
    assert loads[-1] == (date(2026, 7, 28), date(2026, 8, 10))
    assert not any(button.label == "Load today so far" for button in app.button)


def test_entrypoint_today_only_is_opt_in_and_reference_remains_reachable(entrypoint, monkeypatch):
    app, loads = entrypoint
    app.session_state["range"] = CUSTOM
    app.session_state["start"] = date(2026, 9, 20)
    app.session_state["end"] = date(2026, 9, 20)
    app.run()
    assert not app.exception
    assert app.session_state.periods.current is None
    assert loads == []
    today_loader = Mock(return_value=dashboard_facts())
    monkeypatch.setattr(q, "load_direct_dependency", today_loader)
    app.switch_page("app_pages/coco.py").run()
    assert not app.exception
    assert loads == []
    app.button(key="today_CoCo").click().run()
    assert not app.exception
    today_loader.assert_called_once_with("CoCo", date(2026, 9, 20), date(2026, 9, 20))
    app.switch_page("app_pages/acceleration_sql.py").run()
    assert not app.exception
    assert loads == []


@pytest.mark.parametrize(
    "page",
    [
        "total_consumption",
        "coco",
        "cowork",
        "cortex_agents",
        "cortex_search",
        "cortex_ai_functions",
        "cortex_inference_api",
    ],
)
@pytest.mark.parametrize("covered", [False, True])
def test_entrypoint_prior_only_usage_remains_visible(entrypoint, monkeypatch, page, covered):
    app, _loads = entrypoint
    original = q.load_unified_facts

    def prior_only(start, end):
        frame = original(start, end)
        frame["USAGE_DATE"] = [date(2026, 9, 7), date(2026, 9, 8)]
        if not covered:
            frame.attrs = {}
        return frame

    for name in (
        "load_unified_facts",
        "load_coco_facts",
        "load_cowork_facts",
        "load_agent_facts",
        "load_search_daily_facts",
        "load_ai_fn_facts",
        "load_rest_facts",
    ):
        monkeypatch.setattr(q, name, prior_only)
    app.run()
    if page != "total_consumption":
        app.switch_page(f"app_pages/{page}.py").run()
    assert not app.exception
    assert len(app.metric) == 1
    assert app.metric[0].value == "0.00"
    if covered:
        assert app.metric[0].delta == "-100.0% (-1.0010)"
    else:
        assert not app.metric[0].delta
        assert any("does not establish zero" in info.value for info in app.info)
    assert any("Previous-period observed credits: 1.00" in caption.value for caption in app.caption)


def test_entrypoint_both_empty_covered_periods_show_no_change(entrypoint, monkeypatch):
    app, _loads = entrypoint
    original = q.load_unified_facts

    def empty(start, end):
        return original(start, end).iloc[:0].copy()

    monkeypatch.setattr(q, "load_unified_facts", empty)
    monkeypatch.setattr(q, "load_coco_facts", empty)
    app.run()
    assert not app.exception
    assert app.metric[0].delta == "+0.0% (+0.0000)"
    assert any("No change" in caption.value for caption in app.caption)
    app.switch_page("app_pages/coco.py").run()
    assert not app.exception
    assert app.metric[0].delta == "+0.0% (+0.0000)"


def test_entrypoint_invalid_first_dates_issue_no_usage_reads(entrypoint):
    app, loads = entrypoint
    app.session_state["range"] = CUSTOM
    app.session_state["start"] = date(2026, 9, 18)
    app.run()
    assert not app.exception
    assert loads == []
    assert any("both dates" in error.value for error in app.error)
    app.switch_page("app_pages/acceleration_sql.py").run()
    assert not app.exception
    assert loads == []


def test_entrypoint_invalid_submitted_dates_preserve_valid_view(entrypoint, monkeypatch):
    app, loads = entrypoint
    app.run()
    previous = app.session_state.periods
    app.session_state["range"] = CUSTOM
    monkeypatch.setattr(
        ui.st,
        "date_input",
        lambda label, **_kwargs: date(2026, 9, 19) if label == "Start date" else date(2026, 9, 21),
    )
    app.run()
    app.button(key="FormSubmitter:custom_period-Apply dates").click().run()
    assert not app.exception
    assert app.session_state.periods == previous
    assert any("beyond today" in error.value for error in app.error)
    assert set(loads) == {(date(2026, 9, 6), date(2026, 9, 19))}


@pytest.mark.parametrize(
    "values",
    [["2026-08-04", "2026-08-10"], ["2026-09-20", "2026-09-20"]],
)
def test_entrypoint_imports_complete_legacy_link(entrypoint, values):
    app, _loads = entrypoint
    app.session_state["range"] = CUSTOM
    app.query_params["dates"] = values
    app.run()
    assert not app.exception
    assert app.session_state.periods.requested == DateWindow(
        *(date.fromisoformat(v) for v in values)
    )
    assert "dates" not in app.query_params
    assert app.query_params["start"] == [values[0]]
    assert app.query_params["end"] == [values[1]]
    assert app.date_input(key="start").proto.query_param_key == "start"
    assert app.date_input(key="end").proto.query_param_key == "end"


@pytest.mark.parametrize("values", [["2026-09-20"], ["invalid", "2026-09-20"]])
def test_entrypoint_rejects_ambiguous_legacy_link(entrypoint, values):
    app, loads = entrypoint
    app.session_state["range"] = CUSTOM
    app.query_params["dates"] = values
    app.run()
    assert not app.exception
    assert loads == []
    assert any("Legacy dates link" in warning.value for warning in app.warning)


def test_new_endpoints_take_precedence_over_legacy_dates(entrypoint):
    app, loads = entrypoint
    app.session_state["range"] = CUSTOM
    app.session_state["start"] = date(2026, 9, 20)
    app.session_state["end"] = date(2026, 9, 20)
    app.query_params["dates"] = ["2026-08-04", "2026-08-10"]
    app.run()
    assert not app.exception
    assert app.session_state.periods.current is None
    assert loads == []


def test_reversed_endpoints_do_not_replace_applied_window(entrypoint):
    app, loads = entrypoint
    app.run()
    previous = app.session_state.periods
    app.session_state["range"] = CUSTOM
    app.run()
    app.date_input(key="start").set_value(date(2026, 8, 10))
    app.date_input(key="end").set_value(date(2026, 8, 4))
    app.button(key="FormSubmitter:custom_period-Apply dates").click().run()
    assert not app.exception
    assert app.session_state.periods == previous
    assert any("on or after" in error.value for error in app.error)
    assert set(loads) == {(date(2026, 9, 6), date(2026, 9, 19))}


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("invalid", "2026-09-19", "both dates"),
        ("2020-01-01", "2026-09-19", "both dates"),
        ("2026-09-18", "2026-09-21", "both dates"),
        ("2026-08-10", "2026-08-04", "on or after"),
    ],
)
def test_invalid_native_date_urls_do_not_load_usage(entrypoint, start, end, message):
    app, loads = entrypoint
    app.query_params.update(range=CUSTOM, start=start, end=end)
    app.run()
    assert not app.exception
    assert loads == []
    assert any(message in error.value for error in app.error)
    assert not app.metric


@pytest.mark.parametrize(
    ("page", "loader"),
    [
        ("coco", "load_coco_facts"),
        ("cowork", "load_cowork_facts"),
        ("cortex_agents", "load_agent_facts"),
        ("cortex_search", "load_search_daily_facts"),
        ("cortex_ai_functions", "load_ai_fn_facts"),
        ("cortex_inference_api", "load_rest_facts"),
        ("warehouse_attribution", "load_warehouse_credit_facts"),
    ],
)
def test_main_source_failure_renders_unavailable_not_zero(entrypoint, monkeypatch, page, loader):
    app, _loads = entrypoint
    monkeypatch.setattr(q, loader, Mock(side_effect=RuntimeError("synthetic denied")))
    app.run()
    app.switch_page(f"app_pages/{page}.py").run()
    assert not app.exception
    assert len(app.metric) == 1
    assert app.metric[0].value == "Unavailable"
    assert not app.metric[0].delta


@pytest.mark.parametrize("lookup", ["unavailable", "empty", "matching"])
def test_tag_lookup_preserves_unavailable_vs_successful_empty(entrypoint, monkeypatch, lookup):
    app, _loads = entrypoint
    if lookup == "unavailable":
        monkeypatch.setattr(
            q, "load_user_tags", Mock(side_effect=RuntimeError("synthetic unavailable"))
        )
    else:
        rows = {"USER_NAME": ["Alice"], "TAG_LABEL": ["team = selected"]}
        monkeypatch.setattr(
            q,
            "load_user_tags",
            lambda: pd.DataFrame(rows if lookup == "matching" else {key: [] for key in rows}),
        )
    app.run()
    app.session_state["tags"] = ["team = selected"]
    app.run()
    assert not app.exception
    expected = None if lookup == "unavailable" else ["Alice"] if lookup == "matching" else []
    assert app.session_state.tag_users == expected
    facts = dashboard_facts()
    facts.loc[len(facts)] = facts.iloc[0].copy()
    facts.loc[len(facts) - 1, "USER_NAME"] = None
    facts.loc[len(facts) - 1, "MODEL_NAME"] = "other"
    scope = agg.partition_scope(
        facts,
        selection=FilterSelection(users=("Alice",), models=("model-a",), tags=("team = selected",)),
        tag_users=expected,
    )
    assert scope.excluded["USER_NAME"].fillna("missing").tolist() == (
        ["Bob", "missing"] if lookup != "empty" else ["Alice", "Bob", "missing"]
    )


@pytest.mark.parametrize("loader", ["load_search_serving_facts", "load_search_batch_facts"])
def test_auxiliary_source_failure_keeps_search_headline(entrypoint, monkeypatch, loader):
    app, _loads = entrypoint
    monkeypatch.setattr(q, loader, Mock(side_effect=RuntimeError("synthetic denied")))
    app.run()
    app.switch_page("app_pages/cortex_search.py").run()
    app.toggle(key="detail_search").set_value(True).run()
    assert not app.exception
    assert app.metric[0].value == "1.00"
    assert any("source unavailable" in warning.value for warning in app.warning)


def test_entirely_unrated_inference_is_not_displayed_as_free(entrypoint, monkeypatch):
    app, _loads = entrypoint
    original = q.load_rest_facts

    def unrated(start, end):
        frame = original(start, end)
        frame["CREDITS"] = 0.0
        frame["UNRATED_TOKENS"] = frame["TOKENS"]
        frame["RATE_INCOMPLETE"] = True
        return frame

    monkeypatch.setattr(q, "load_rest_facts", unrated)
    app.run()
    app.switch_page("app_pages/cortex_inference_api.py").run()
    assert not app.exception
    assert app.metric[0].label == "Credits (partial estimate)"
    assert app.metric[0].value == "Unavailable"
    assert not app.metric[0].delta


def test_grouped_navigation_has_one_title_and_keeps_dependency_identity(entrypoint):
    app, loads = entrypoint
    app.run()
    assert [item.value for item in app.title] == ["Overview"]
    app.switch_page("app_pages/coco.py").run()
    assert not app.exception
    assert [item.value for item in app.title] == ["CoCo"]
    assert app.session_state.active_page_id == "coco"
    assert loads


def test_reset_clears_only_current_page_preferences(entrypoint):
    app, _loads = entrypoint
    app.run()
    app.switch_page("app_pages/coco.py").run()
    app.session_state["agent_fqns"] = ["retained-agent"]
    app.multiselect(key="coco_roles").select("ADMIN").run()
    app.multiselect(key="users").select("Bob").run()
    next(
        button for button in app.button if button.label == "Clear filters on this view"
    ).click().run()
    assert not app.exception
    assert app.session_state["users"] == []
    assert app.session_state["coco_roles"] == []
    assert app.session_state["agent_fqns"] == ["retained-agent"]


def test_invalid_native_filter_remains_explicit_not_all_users(entrypoint):
    app, _loads = entrypoint
    app.query_params["users"] = "not-a-user"
    app.run()
    assert not app.exception
    assert app.multiselect(key="users").value == ["not-a-user"]
    assert any("absent from this view" in warning.value for warning in app.warning)
    assert all(metric.value != "1.00" for metric in app.metric)


@pytest.mark.parametrize(
    ("page", "expected_calls"),
    [
        ("total_consumption", 8),
        ("coco", 2),
        ("cowork", 2),
        ("cortex_agents", 2),
        ("cortex_search", 1),
        ("cortex_ai_functions", 2),
        ("cortex_inference_api", 3),
        ("warehouse_attribution", 3),
        ("sql", 0),
        ("acceleration_sql", 0),
        ("config", 0),
    ],
)
@pytest.mark.parametrize("accelerated", [False, True])
def test_real_cold_path_uses_each_source_once(monkeypatch, page, expected_calls, accelerated):
    statements = []
    metadata_statements = []

    def metadata_query(sql, params=()):
        metadata_statements.append((sql, params))
        if "CURRENT_DATABASE()" in sql:
            return pd.DataFrame({"DB": ["FIXTURE_DB"], "S": ["PUBLIC"]})
        if "SELECT KEY, VALUE" in sql:
            return pd.DataFrame({"KEY": ["enabled"], "VALUE": ["true"]})
        if sql.startswith("SHOW TASKS"):
            return pd.DataFrame(
                {
                    "NAME": [
                        f"CORTEX_DASH_TASK_{surface_to_suffix(key)}"
                        for key in config._DT_SURFACE_KEYS
                    ],
                    "STATE": ["started"] * len(config._DT_SURFACE_KEYS),
                }
            )
        if "INFORMATION_SCHEMA.TABLES" in sql:
            return pd.DataFrame(
                {
                    "TABLE_NAME": [
                        f"CORTEX_DASH_{surface_to_suffix(key)}" for key in config._DT_SURFACE_KEYS
                    ],
                    "ROW_COUNT": [2] * len(config._DT_SURFACE_KEYS),
                }
            )
        if "AS LAST_RUN" in sql:
            return pd.DataFrame(
                {
                    "NAME": [
                        f"CORTEX_DASH_TASK_{surface_to_suffix(key)}"
                        for key in config._DT_SURFACE_KEYS
                    ],
                    "LAST_RUN": ["2026-09-20T01:00:00+00:00"] * len(config._DT_SURFACE_KEYS),
                }
            )
        return pd.DataFrame()

    def query(sql, *, params, ttl, show_spinner):
        assert ttl == 0
        assert show_spinner is False
        statements.append((sql, params))
        if (
            "AS r_input" in sql and "CORTEX_REST_API_USAGE_HISTORY" not in sql
        ) or "CORTEX_DASH_MODEL_RATES" in sql:
            return pd.DataFrame(
                {"MODEL_NAME": ["model-a"], **{column: [0.01] for column in q.RATE_COLUMNS}}
            )
        if "TAG_REFERENCES" in sql:
            return pd.DataFrame({"USER_NAME": [], "TAG_LABEL": []})
        if "OVERLAP" in sql.upper():
            return pd.DataFrame({"OVERLAP_COUNT": [0]})
        frame = dashboard_facts()
        frame["IS_UNRATED"] = 0
        return frame

    connection = Mock(query=query, raw_connection=Mock(database="FIXTURE_DB", schema="PUBLIC"))
    monkeypatch.setattr(q, "_connection", lambda: connection)
    monkeypatch.setattr(q, "utc_today", lambda: date(2026, 9, 20))
    monkeypatch.setattr(preflight, "run_preflight", lambda: None)
    monkeypatch.setattr(config, "_fetch_raw", metadata_query)
    if accelerated:
        from utils import snowflake

        monkeypatch.setattr(snowflake, "_connection", lambda: connection)
    else:
        monkeypatch.setattr(config, "load_settings", config.Settings)
        monkeypatch.setattr(config, "source_for", lambda *_args: "direct")
        monkeypatch.setattr(
            config, "aggregate_snapshot_identity_for", lambda *_args: ("direct", "")
        )
    monkeypatch.setattr(config, "_reconcile_owned_objects", lambda _settings: [])
    config._health_cache.clear()
    config._load_settings_cached.clear()
    q.run_query.clear()
    q.load_user_tags.clear()
    q._load_model_rates_cached.clear()
    try:
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"))
        if page != "total_consumption":
            app.switch_page(f"app_pages/{page}.py")
        app.run(timeout=15)
        assert not app.exception
        expected = 1 if accelerated and page == "warehouse_attribution" else expected_calls
        assert len(statements) == expected
        assert len({(sql, tuple(params)) for sql, params in statements}) == expected
        if accelerated:
            assert not any("CURRENT_DATABASE()" in sql for sql, _params in metadata_statements)
            assert len(metadata_statements) <= 5
            if page == "coco":
                assert any('"CORTEX_DASH_COCO"' in sql for sql, _params in statements)
        for _sql, params in statements:
            if params and params[-2:] == ["2026-09-06", "2026-09-20"]:
                continue
            assert not any(value in {"2026-09-13", "2026-09-19"} for value in params)
        if page == "cortex_search":
            assert not any("TAG_REFERENCES" in sql for sql, _params in statements)
            assert not any("CORTEX_REST_API_USAGE_HISTORY" in sql for sql, _params in statements)
        if expected_calls:
            metadata_count = len(metadata_statements)
            if page not in {"cortex_search", "warehouse_attribution"}:
                app.multiselect(key="users").select("Alice")
            app.run(timeout=15)
            assert not app.exception
            assert len(statements) == expected
            assert len(metadata_statements) == metadata_count
    finally:
        q.run_query.clear()
        q.load_user_tags.clear()
        q._load_model_rates_cached.clear()
        config._health_cache.clear()
        config._load_settings_cached.clear()
