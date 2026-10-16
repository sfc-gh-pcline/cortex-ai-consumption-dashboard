"""Entry point for the Cortex AI Consumption Dashboard.

Owns grouped sidebar navigation, contextual date/filter controls, and modal
dependency loading. Individual services live in app_pages/.
"""

import datetime as dt
import os
import sys
import time
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import streamlit as st

# Hosted containers can boot from an old file snapshot and receive the committed
# files afterwards. This script is re-read on every run, but imported modules are
# not, so stale utils modules are dropped here before the imports below re-read them.
# utils.runtime is checked inline because it holds the refresh logic itself.
_runtime_module = sys.modules.get("utils.runtime")
if _runtime_module is not None:
    try:
        _runtime_mtime = Path(str(_runtime_module.__file__)).stat().st_mtime
    except (OSError, TypeError):
        _runtime_mtime = None
    _runtime_stamp = getattr(_runtime_module, "__source_mtime__", None)
    _process_started = float(os.environ.get("DASHBOARD_PROCESS_STARTED_AT", "0"))
    if (_runtime_stamp is not None and _runtime_stamp != _runtime_mtime) or (
        _runtime_stamp is None and _runtime_mtime is not None and _runtime_mtime > _process_started
    ):
        del sys.modules["utils.runtime"]
from utils import runtime  # noqa: E402

_refreshed_modules = runtime.refresh_stale_modules()

from utils import agg, ui  # noqa: E402
from utils import queries as q  # noqa: E402
from utils.config import (  # noqa: E402
    _invalidate_health_cache,
    aggregate_snapshot_identity_for,
    load_settings,
)
from utils.date_ranges import (  # noqa: E402
    CUSTOM,
    DATE_PRESETS,
    DEFAULT_PRESET,
    parse_legacy_dates,
    resolve_custom_input,
    resolve_periods,
)
from utils.preflight import run_preflight  # noqa: E402
from utils.view_state import FILTER_CAPABILITIES, PAGES, CoverageState  # noqa: E402

runtime.stamp_loaded_modules()

APP_TITLE = "Cortex AI Consumption Dashboard"

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=":material/monitoring:",
    layout="wide",
)
st.html(ui.selected_control_style())

FILTER_KEYS = ("users", "models", "tags")


class WarmState(TypedDict):
    """Dependency generations attached to one loaded comparison window."""

    window: tuple[dt.date, dt.date] | None
    deps: dict[str, str]


# "Today" comes from Snowflake's UTC clock, not the app container's: the two are
# not guaranteed to agree (a -0700 session reports Aug 12 while UTC is Aug 13),
# and the fact queries bucket rows on UTC days. See utils.queries.utc_today.
today = q.utc_today()
st.session_state.utc_today = today

# ---------------------------------------------------------------------------
# First-run preflight
#
# Runs once per session (gated by preflight_done in session_state) to check
# connection health, active warehouse, and ACCOUNT_USAGE read access.  A fast
# path: if the session already passed, the overhead is a single dict lookup.
#
# Errors block the app (they would cause every subsequent query to fail or
# produce a misleading empty-data state).  Warnings are shown inline but do
# not prevent the loading modal from opening, since the warehouse may still
# work via a role or user default that CURRENT_WAREHOUSE() does not reflect.
# ---------------------------------------------------------------------------

if not st.session_state.get("preflight_done"):
    _pf = run_preflight()
    st.session_state["preflight_result"] = _pf
    st.session_state["preflight_done"] = True
else:
    _pf = st.session_state.get("preflight_result")

if _pf is not None:
    for _chk in _pf.errors():
        st.error(
            f"**Setup required — {_chk.name}:** {_chk.detail}  \n"
            f":material/build: {_chk.remediation}",
            icon=":material/error:",
        )
    for _chk in _pf.warnings():
        st.warning(
            f"**{_chk.name}:** {_chk.detail}  \n:material/info: {_chk.remediation}",
            icon=":material/warning:",
        )
    if not _pf.ok:
        st.stop()


def _request_refresh() -> None:
    """Invalidate the active dependency keys and request a reload."""
    _invalidate_health_cache()
    page_id = st.session_state.active_page_id
    dependency_key = next(item.dependency_key for item in PAGES if item.page_id == page_id)
    q.reload_dependencies(q.warm_dependencies_for(dependency_key))
    st.session_state.refreshing = True
    st.session_state.refresh_page_id = page_id


def _clear_filters() -> None:
    """Clear shared filters and the active page's local filters."""
    ui.clear_view_filters(st.session_state.active_page_id)


def _record_warmed(dependencies: tuple[str, ...], settings) -> None:
    """Mark dependencies warm at their current generation for this window.

    Args:
        dependencies: The dependency keys just warmed.
        settings: Current acceleration-layer settings, for the generation.
    """
    warm = st.session_state.setdefault("warm", {"window": None, "deps": {}})
    for dep in dependencies:
        warm["deps"][dep] = q.dependency_generation(dep, settings)


def _warm_caches(
    start_date: dt.date,
    end_date: dt.date,
    dependencies: tuple[str, ...],
    *,
    announce: bool,
    page_key: str,
) -> None:
    """Warm an explicit set of dependencies with step-by-step progress.

    Only the ``dependencies`` passed in are warmed -- the caller computes these
    as the subset of the active page's requirements that are not already warm
    for the current window and aggregate generation, so revisiting a warm tab
    warms nothing and unrelated surfaces stay cold.

    Records each attempted generation, then continues the same run so native
    bound controls are not removed by widget cleanup during a reload.

    Args:
        start_date: First UTC day to warm, inclusive.
        end_date: Last UTC day to warm, inclusive.
        dependencies: The dependency keys to warm, in load order.
        announce: Whether to queue the "Data refreshed" toast, which
            distinguishes an explicit refresh from a cold-window load.
        page_key: Title of the active navigation page; used only for the
            status caption copy, not for cache invalidation.
    """
    request_id = st.session_state.get("load_request_id")
    steps = [
        (
            dependency,
            lambda dependency=dependency: q.load_dependency_result(
                dependency, start_date, end_date
            ),
        )
        for dependency in dependencies
    ]

    # The caption frame stays muted so the two things worth reading pop out of it:
    # the source view as a code chip (an identifier, styled like every other view
    # name in the app) and the window in the accent colour (the value the user
    # picked). Distinguishing them by kind rather than by two hues keeps the line
    # legible and does not lean on colour alone to carry meaning.
    settings = load_settings()
    aggregate_deps = tuple(
        dep for dep in dependencies if q.DEPENDENCY_KINDS[dep] == "windowed_aggregate"
    )
    source_mode, snapshot_freshness = aggregate_snapshot_identity_for(aggregate_deps, settings)
    page_plan = q.PAGE_REFRESH_PLANS.get(page_key)
    page_is_direct_only = (
        page_plan is not None and not page_plan.windowed and bool(page_plan.direct_only)
    )
    served_start_date, served_end_date = q.effective_window(
        start_date,
        end_date,
        surface_key=q.SURFACE_COCO,
        today=today,
    )
    if page_is_direct_only:
        st.caption(
            f"Reading directly from `ACCOUNT_USAGE` for "
            f":blue[**{start_date:%b %d} — {end_date:%b %d, %Y}**] "
            f"(direct source — not aggregate-eligible)."
        )
    elif source_mode == "aggregate":
        st.caption(
            "Loading aggregate snapshot for "
            f":blue[**{served_start_date:%b %d} — {served_end_date:%b %d, %Y}**] "
            f"(latest successful snapshot: {snapshot_freshness[:19]})."
        )
    else:
        st.caption(
            f"Querying `ACCOUNT_USAGE` for :blue[**{start_date:%b %d} — {end_date:%b %d, %Y}**]"
        )
    progress = st.progress(0.0, text="Starting...")
    with st.container(height=120, border=False, key="loading_outcomes", autoscroll=True):
        done = st.empty()

    try:
        timings: list[tuple[str, float]] = []
        outcomes = []
        load_started = time.perf_counter()
        query_count_before = st.session_state.get("source_query_count", 0)
        for index, (label, loader) in enumerate(steps):
            progress.progress(index / len(steps), text=f"Loading {q.dependency_label(label)}...")
            started = time.perf_counter()
            loaded = loader()
            if st.session_state.get("load_request_id") != request_id:
                st.stop()
            timings.append((label, time.perf_counter() - started))
            outcomes.append(
                {
                    "dependency": label,
                    "state": loaded.coverage.state.value,
                    "source": loaded.coverage.actual_source,
                    "reason": loaded.coverage.reason,
                }
            )
            done.markdown(
                "  \n".join(
                    f"{q.dependency_label(str(outcome['dependency']))}: "
                    + (
                        "unavailable; see source diagnostics"
                        if outcome["state"] == CoverageState.UNAVAILABLE.value
                        else "loaded"
                    )
                    for outcome in outcomes
                )
            )

        progress.progress(1.0, text="Done")
        st.session_state.last_warm_diagnostics = {
            "page": page_key,
            "dependencies": dependencies,
            "source": source_mode,
            "snapshot": snapshot_freshness,
            "timings": timings,
            "elapsed_seconds": time.perf_counter() - load_started,
            "outcomes": outcomes,
            "source_query_count": st.session_state.get("source_query_count", 0)
            - query_count_before,
        }
    except Exception as exc:  # noqa: BLE001 - surface the failure, don't loop on the dialog
        if st.session_state.get("load_request_id") != request_id:
            st.stop()
        st.session_state.refreshing = False
        # Record the attempt so the gate does not reopen this dialog on every
        # rerun; the page below will surface the underlying error itself.
        _record_warmed(dependencies, settings)
        st.error(f"Refresh failed: {exc}", icon=":material/error:")
        if st.button("Close"):
            st.rerun()
        st.stop()

    _record_warmed(dependencies, settings)
    st.session_state.refreshing = False
    if announce:
        st.session_state.refresh_done = not any(
            outcome["state"] == CoverageState.UNAVAILABLE.value for outcome in outcomes
        )
        st.session_state.refresh_failed = not st.session_state.refresh_done


@st.dialog("Loading data", width="medium")
def _loading_dialog(
    start_date: dt.date,
    end_date: dt.date,
    dependencies: tuple[str, ...],
    *,
    announce: bool,
    page_key: str,
) -> None:
    """Show retained results and progress while loading the active view."""
    ui.render_retained_reload()
    _warm_caches(start_date, end_date, dependencies, announce=announce, page_key=page_key)


# ---------------------------------------------------------------------------
# Navigation
#
# Grouped sidebar navigation built from utils.view_state.PAGES: Overview
# ungrouped, then AI Services, Infrastructure, Reference and Administration.
# Each page keeps its stable page_id, URL path and dependency_key separately
# from its displayed title, so relabeling never changes routes or refresh
# plans. st.navigation runs before the cache gate so the active page's
# dependency key is known when deciding what to load.
# ---------------------------------------------------------------------------

navigation = {}
icons = (
    "donut_large",
    "terminal",
    "groups",
    "smart_toy",
    "search",
    "api",
    "function",
    "dns",
    "code",
    "speed",
    "settings",
)
for identity, icon in zip(PAGES, icons, strict=True):
    navigation.setdefault(identity.group, []).append(
        st.Page(
            identity.path,
            title=identity.title,
            icon=f":material/{icon}:",
            url_path=identity.url_path,
            default=identity.page_id == "total_consumption",
        )
    )
st.sidebar.caption(APP_TITLE)
page = st.navigation(navigation, position="sidebar")
identity = next(item for item in PAGES if item.title == page.title)
page_key = identity.dependency_key
st.session_state.active_page_id = identity.page_id
st.session_state.load_request_id = str(uuid4())
refreshing = st.session_state.get("refreshing", False)
if refreshing and st.session_state.get("refresh_page_id") != identity.page_id:
    refreshing = False
    st.session_state.refreshing = False
st.session_state.entrypoint_title = True
st.title(identity.title)
# Build identity is rendered here, before any data gate, so it is visible even when a
# later step fails; utils.runtime is imported at process start to pin its import time.
runtime.render_sidebar_build(runtime.fingerprint())
if _refreshed_modules:
    st.sidebar.caption(f"Re-imported {len(_refreshed_modules)} changed modules this run.")

# ---------------------------------------------------------------------------
# Filter sidebar, part 1: brand and date range
#
# The date window decides which queries run, so it is rendered before the cache
# gate. Everything that reads a loader lives in part 2, after the gate.
# ---------------------------------------------------------------------------

with st.container(gap="small"):
    active_filters = sum(len(st.session_state.get(key, [])) for key in FILTER_KEYS)

    heading = st.container(horizontal=True)
    if active_filters:
        heading.markdown(f":blue-badge[{active_filters} active]")

    # bind="query-params" (Streamlit 1.60+) syncs each filter to the URL using its
    # key as the parameter name, so the whole view -- window and filters -- is
    # shareable by copying the address bar. Keys are therefore chosen to read well
    # as query parameters (?range=30d&users=...). A value equal to the default is
    # dropped from the URL, so the clean state stays a clean link.
    preset = (
        st.pills(
            "Date range",
            [*DATE_PRESETS, CUSTOM],
            default=DEFAULT_PRESET,
            key="range",
            bind="query-params",
        )
        or DEFAULT_PRESET
    )

    if preset == CUSTOM:
        if not st.session_state.get("legacy_dates_checked"):
            st.session_state.legacy_dates_checked = True
            legacy_dates = st.query_params.get_all("dates")
            if legacy_dates:
                if not any(
                    key in st.query_params or key in st.session_state for key in ("start", "end")
                ):
                    try:
                        legacy_start, legacy_end = parse_legacy_dates(today, legacy_dates)
                    except ValueError as exc:
                        st.session_state.legacy_dates_error = str(exc)
                    else:
                        st.query_params["start"] = legacy_start.isoformat()
                        st.query_params["end"] = legacy_end.isoformat()
                        del st.query_params["dates"]
                        st.rerun()
                del st.query_params["dates"]
        if legacy_error := st.session_state.get("legacy_dates_error"):
            st.warning(legacy_error)
        with st.form("custom_period"):
            chosen_start = st.date_input(
                "Start date",
                value=None,
                min_value=today - dt.timedelta(days=730),
                max_value=today,
                key="start",
                bind="query-params",
            )
            chosen_end = st.date_input(
                "End date",
                value=None,
                min_value=today - dt.timedelta(days=730),
                max_value=today,
                key="end",
                bind="query-params",
            )
            applied = st.form_submit_button("Apply dates")
        chosen = (chosen_start, chosen_end)
        if applied or "periods" not in st.session_state:
            try:
                candidate = resolve_custom_input(today, chosen)
            except (TypeError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.session_state.periods = candidate
                st.session_state.applied_range = CUSTOM
                st.session_state.pop("legacy_dates_error", None)
        periods = st.session_state.get("periods")
        draft = tuple(chosen) if isinstance(chosen, (tuple, list)) else ()
        if periods is not None and draft != (
            periods.requested.start,
            periods.requested.end,
        ):
            st.caption("Dates are not applied. Results retain the last valid applied range.")
    else:
        periods = resolve_periods(today, preset)
        st.session_state.applied_range = preset
        st.session_state.periods = periods

    if periods is None:
        st.info("Select and apply a complete date range before loading usage.")
    elif periods.current is None:
        st.info("Today is partial and has no completed-period comparison.")
    reference_periods = periods or resolve_periods(today, DEFAULT_PRESET)
    displayed = reference_periods.current or reference_periods.requested
    start_date, end_date = displayed.start, displayed.end

    st.session_state.start_date = start_date
    st.session_state.end_date = end_date
    st.caption(
        f"{start_date:%b %d, %Y} to {end_date:%b %d, %Y} UTC | "
        + (
            "Reference dates only. No usage range has been applied."
            if periods is None
            else f"{(end_date - start_date).days + 1} completed days"
        ),
    )

# ---------------------------------------------------------------------------
# Cache gate
#
# Loading runs inside a modal. Register native bound controls before closing
# through a guaranteed rerun, including when a page stops at an empty state.
# Filter changes (user, model, tag) are pure pandas and never reach this gate.
#
# Documentation and settings pages (Reference SQL, Query Acceleration SQL,
# Config) declare no data dependencies: they read the date range from part 1
# above but never the surface facts or the user/model/tag filters, so they skip
# the warm gate and the filter sidebar entirely and never trigger the dialog.
#
# Data pages warm cumulatively by dependency: only the dependencies a page needs
# that are not already warm for the current window and aggregate generation are
# loaded. Revisiting a warm tab warms nothing, and because each dependency
# carries its own generation, a nightly task completing for one surface
# re-warms only the pages that read it.
# ---------------------------------------------------------------------------

plan = q.PAGE_REFRESH_PLANS.get(page_key)
is_no_data = plan is not None and not (plan.windowed or plan.global_cached or plan.direct_only)

if is_no_data:
    page.run()
    st.stop()

if periods is None:
    st.stop()

if periods.current is None:
    ui.today_section(page_key, today)
    st.stop()

settings = load_settings()
needed = q.initial_dependencies_for(page_key)
generations = {dep: q.dependency_generation(dep, settings) for dep in needed}
load_start_date = periods.previous.start if periods.previous else start_date
q.prepare_acquisition(settings, load_start_date, end_date)
warm: WarmState | None = st.session_state.get("warm")
if not warm or warm.get("window") != (load_start_date, end_date):
    warm = {"window": (load_start_date, end_date), "deps": {}}
    st.session_state.warm = warm
    st.session_state.dependency_query_keys = {}

missing = [
    dependency
    for dependency in needed
    if not q.dependency_is_ready(dependency, load_start_date, end_date)
]
loaded_this_run = bool(refreshing or missing)
if loaded_this_run:
    _loading_dialog(
        load_start_date,
        end_date,
        needed if refreshing else tuple(missing),
        announce=refreshing,
        page_key=page_key,
    )

snapshot_identity = aggregate_snapshot_identity_for(
    q.aggregate_dependencies_for(page_key), settings
)

# ---------------------------------------------------------------------------
# Filter sidebar, part 2: filters read from the now-warm caches
# ---------------------------------------------------------------------------

with st.container(horizontal=True, vertical_alignment="bottom"):
    # Option lists come from the already-loaded facts, so no extra query is needed.
    if page_key == q.PAGE_TOTAL_CONSUMPTION:
        facts = q.load_unified_facts(load_start_date, end_date)
        capabilities = {"USER_NAME", "MODEL_NAME"}
    else:
        facts = q.load_dependency_result(
            q.main_dependency_for(page_key), load_start_date, end_date
        ).frame
        capabilities = FILTER_CAPABILITIES.get(page_key, frozenset())
    tag_result = (
        q.load_dependency_result("user_tags", load_start_date, end_date)
        if "USER_NAME" in capabilities
        else None
    )
    tags = (
        tag_result.frame
        if tag_result is not None and tag_result.coverage.state != CoverageState.UNAVAILABLE
        else None
    )
    st.session_state.opt_users = agg.options(facts, "USER_NAME")
    st.session_state.opt_models = agg.options(facts, "MODEL_NAME")
    st.session_state.opt_tags = agg.tag_label_options(tags)

    user_options = st.session_state.opt_users
    model_options = st.session_state.opt_models
    tag_options = st.session_state.opt_tags

    ui.scope_filter(
        "User",
        user_options,
        key="users",
        disabled="USER_NAME" not in capabilities,
        placeholder=f"All users ({len(user_options)})",
    )
    ui.scope_filter(
        "Model",
        model_options,
        key="models",
        disabled="MODEL_NAME" not in capabilities,
        placeholder=f"All models ({len(model_options)})",
    )
    selected_tags = ui.scope_filter(
        "User tag",
        tag_options,
        key="tags",
        disabled="USER_NAME" not in capabilities,
        placeholder=f"All tags ({len(tag_options)})",
        help="Resolved from current USER-domain tag references.",
    )

    if selected_tags:
        st.session_state.tag_users = (
            agg.users_for_tags(tags, selected_tags) if tags is not None else None
        )
    else:
        st.session_state.tag_users = None

    st.button(
        "Clear filters on this view", on_click=_clear_filters, icon=":material/filter_alt_off:"
    )
    st.button(
        "Reset view",
        on_click=ui.clear_view_filters,
        args=(identity.page_id,),
        kwargs={"reset": True},
        icon=":material/restart_alt:",
    )

    st.button(
        "Reload results",
        on_click=_request_refresh,
        width="stretch",
        icon=":material/refresh:",
        help="Reload this view's source results. Does not refresh aggregate tables or source ingestion.",
        type="primary",
    )

if st.session_state.pop("refresh_done", False):
    st.toast("Data refreshed", icon=":material/check_circle:")
if st.session_state.pop("refresh_failed", False):
    st.warning(
        "Reload completed with unavailable sources. See source diagnostics; unavailable values are not zero."
    )

ui.filter_summary(
    st.session_state.get("users", []),
    st.session_state.get("models", []),
    selected_tags,
)

st.session_state.close_loading_dialog = loaded_this_run
page.run()
if st.session_state.pop("close_loading_dialog", False):
    st.rerun()

if periods.partial_today is not None or (
    st.session_state.applied_range != CUSTOM and end_date == today - dt.timedelta(days=1)
):
    with st.expander("Today so far (partial)"):
        ui.today_section(page_key, today)
