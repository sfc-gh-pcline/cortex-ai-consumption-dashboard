"""Formatting and layout helpers shared by every dashboard page.

Covers number formatting, the KPI cards, section cards, insight banners and the
chart and table wrappers, so pages stay declarative.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from enum import Enum
from typing import Any, NamedTuple, Required, TypedDict, overload
from urllib.parse import urlencode

import pandas as pd
import streamlit as st

from utils import agg
from utils import queries as q
from utils.view_state import ComparisonState, CoverageState, FilterSelection, SourceCoverage

PAGE_FILTER_KEYS = {
    "coco": ("coco_subs", "coco_roles"),
    "cowork": ("cowork_instances", "cowork_agents"),
    "cortex_agents": ("agent_fqns",),
    "cortex_inference_api": ("rest_regions", "rest_tags"),
    "cortex_ai_functions": ("aifn_functions", "aifn_tags"),
    "cortex_search": ("search_services", "search_types"),
    "warehouse_attribution": ("warehouse_surfaces", "warehouse_names", "warehouse_tiers"),
}
PAGE_BREAKDOWN_DEFAULTS = {
    "coco": ("coco_breakdown", "By user"),
    "cowork": ("cowork_breakdown", "By agent"),
    "cortex_agents": ("agent_breakdown", "By agent"),
    "cortex_inference_api": ("rest_breakdown", "By model"),
    "cortex_ai_functions": ("aifn_breakdown", "By function"),
}


def stop_page() -> None:
    """Close completed loading before an early page exit requests a script stop."""
    if st.session_state.pop("close_loading_dialog", False):
        st.rerun()
    st.stop()


def driver_url(route: str, dimension: str, value: str | None) -> str:
    """Build a copied native-widget URL without mutating registered query parameters."""
    parameters = {key: st.session_state.get(key, []) for key in ("users", "models", "tags")}
    parameters["range"] = st.session_state.get("applied_range", "7d")
    if parameters["range"] == "Custom" and (periods := st.session_state.get("periods")):
        parameters["start"] = periods.requested.start.isoformat()
        parameters["end"] = periods.requested.end.isoformat()
    if dimension in {"User", "Model"} and value is not None:
        parameters["users" if dimension == "User" else "models"] = [value]
    return f"/{route}?{urlencode(parameters, doseq=True)}"


def clear_view_filters(page_id: str, *, reset: bool = False) -> None:
    """Clear shared filters and this page only; optionally reset its display defaults."""
    for key in ("users", "models", "tags", *PAGE_FILTER_KEYS.get(page_id, ())):
        st.session_state[key] = []
    st.session_state.tag_users = None
    if reset:
        st.session_state["range"] = "7d"
        st.session_state["start"] = None
        st.session_state["end"] = None
        st.session_state.pop("periods", None)
        st.session_state.pop("legacy_dates_error", None)
        st.session_state[f"trend_{page_id}"] = "Total"
        st.session_state[f"compare_{page_id}"] = False
        if page_id == "cortex_search":
            st.session_state["detail_search"] = False
        if page_id == "cowork":
            st.session_state["detail_cowork_mix"] = False
        from utils.view_state import PAGES

        identity = next((page for page in PAGES if page.page_id == page_id), None)
        if identity is not None:
            st.session_state[f"detail_warehouse_{identity.dependency_key}"] = False
        if page_id == "total_consumption":
            st.session_state["overview_service"] = q.SURFACES[0]
            st.session_state["overview_dimension"] = "Service only"
        if page_id in PAGE_BREAKDOWN_DEFAULTS:
            key, value = PAGE_BREAKDOWN_DEFAULTS[page_id]
            st.session_state[key] = value
        for key in tuple(st.session_state):
            if key.startswith(f"chart_{page_id}_"):
                st.session_state[key] = "Chart"
            elif key.startswith(f"top_{page_id}_"):
                st.session_state[key] = 10


def scope_filter(label: str, options: Sequence[str], *, key: str, **kwargs) -> list[str]:
    """Retain unsupported URL selections rather than silently broadening the scope."""
    selected = st.session_state[key] if key in st.session_state else st.query_params.get_all(key)
    choices = sorted(set(options) | set(selected))
    invalid = set(selected) - set(options)
    with st.container():
        if invalid:
            st.warning(
                f"{label}: selected values are absent from this view: {', '.join(sorted(invalid))}."
            )
        return st.multiselect(label, choices, key=key, bind="query-params", **kwargs)


Number = float | int | None
AI_FUNCTIONS_WAREHOUSE_START = date(2026, 1, 5)
_logger = logging.getLogger(__name__)


def selected_control_style() -> str:
    """Keep native selection borders/fills while making selected labels readable."""
    return (
        '<style>[data-testid="stMain"] button[role="radio"][aria-checked="true"] '
        "{color: inherit;}</style>"
    )


def freshness_note(coverages: Sequence[SourceCoverage], end: date) -> str | None:
    """Name sources whose newest recorded usage day precedes the requested end.

    The dashboard reflects the ACCOUNT_USAGE views exactly, so when the newest row
    for a source is older than the window end, the empty trailing days are a
    property of the source view. Saying so keeps that gap from reading as a bug.
    """
    behind = [
        (coverage.dependency, coverage.latest_usage)
        for coverage in coverages
        if coverage.state != CoverageState.UNAVAILABLE
        and coverage.latest_usage is not None
        and coverage.latest_usage < end
    ]
    if not behind:
        return None
    listed = ", ".join(f"{dependency} {latest:%b %d}" for dependency, latest in behind)
    return (
        f"Latest recorded usage: {listed}; requested through {end:%b %d}. "
        "The source view has no later rows, so empty trailing days reflect the source, "
        "not this dashboard."
    )


def load_period_usage(dependency: str, start: date, end: date) -> pd.DataFrame:
    """Read the current/prior union once while retaining both periods for filters."""
    periods = st.session_state.get("periods")
    if periods is not None and periods.previous is not None:
        start = periods.previous.start
    if dependency == q.PAGE_TOTAL_CONSUMPTION:
        frame = q.load_unified_facts(start, end)
        coverages: Sequence[SourceCoverage] = frame.attrs.get("source_coverages", ())
    else:
        result = q.load_dependency_result(dependency, start, end)
        frame = result.frame
        coverages = (result.coverage,)
        if result.coverage.state == CoverageState.UNAVAILABLE:
            st.warning(f"{dependency} source unavailable. Use Reload results to retry.")
            with st.expander(f"{dependency} source diagnostic"):
                st.text(result.coverage.reason or "Source returned no result")
    if note := freshness_note(coverages, end):
        st.caption(note)
    if periods is not None:
        frame = frame.copy()
        frame.attrs["periods"] = periods
    return frame


def load_period_warehouse(start: date, end: date):
    """Retain both warehouse periods until the display component applies scope."""
    periods = st.session_state.get("periods")
    read_start = periods.previous.start if periods and periods.previous else start
    loaded = q.load_dependency_result(q.SURFACE_WAREHOUSE_ATTRIBUTION, read_start, end)
    result = q.WarehouseFactsResult(
        loaded.frame,
        loaded.coverage.state in {CoverageState.UNAVAILABLE, CoverageState.PARTIAL},
        loaded.coverage.reason,
    )
    if periods is not None:
        frame = result.frame.copy()
        frame.attrs["periods"] = periods
        return result._replace(frame=frame)
    return result


def optional_warehouse(source: str, start: date, end: date) -> None:
    """Load attributed compute only after an explicit component selection."""
    if st.toggle(
        "Attributed warehouse detail", key=f"detail_warehouse_{source}", bind="query-params"
    ):
        warehouse_component(load_period_warehouse(start, end), source)


@st.fragment
def today_section(page_key: str, today: date) -> None:
    """Render an explicitly requested direct partial-day view without a baseline."""
    st.header("Today so far")
    st.caption(
        "Partial UTC day. Source ingestion may lag; these values never enter completed totals or deltas."
    )
    if not st.button("Load today so far", key=f"today_{page_key}"):
        return
    dependency = f"{q.SURFACE_SEARCH} — Daily" if page_key == q.SURFACE_SEARCH else page_key
    try:
        if page_key == q.PAGE_TOTAL_CONSUMPTION:
            frame = q.load_unified_facts(today, today, direct_only=True)
        else:
            frame = q.load_direct_dependency(dependency, today, today)
    except Exception:
        _logger.exception("Today source unavailable")
        st.error("Today's source is unavailable. Completed-period results are unchanged.")
        return
    if unavailable := frame.attrs.get("unavailable_sources"):
        st.warning("Partial source coverage: " + ", ".join(unavailable))
        if len(unavailable) == len(q.SURFACES):
            st.metric("Observed credits today (partial)", "Unavailable")
            return
    metric = "WAREHOUSE_CREDITS" if page_key == q.SURFACE_WAREHOUSE_ATTRIBUTION else "CREDITS"
    st.caption(
        "Scope: dates and global user/model/tag selections only; page-specific selectors are not applied to this separate component."
    )
    scoped = scoped_usage(
        frame, None if page_key == q.PAGE_TOTAL_CONSUMPTION else page_key, metric=metric
    )
    st.metric("Observed credits today (partial)", fmt_credits(agg.sum_column(scoped, metric)))
    st.dataframe(scoped, hide_index=True)


def _current_scope(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Slice the displayed interval and evaluate its identically filtered baseline."""
    periods = frame.attrs.get("periods")
    if periods is None:
        return frame
    coverages = frame.attrs.get("source_coverages", ())
    if coverage := frame.attrs.get("coverage"):
        coverages = (coverage,)
    comparison = agg.compare_periods(frame, periods, coverages, metric)
    current = agg.period_slice(frame, periods.current)
    current.attrs.update(
        comparisons={metric: comparison},
        display_window=periods.current,
        source_coverages=coverages,
        series_values={
            column: tuple(frame[column].dropna().unique())
            for column in ("SURFACE", "SUB_SURFACE")
            if column in frame
        },
    )
    if periods.previous is not None:
        previous_daily = agg.calendar_daily(frame, periods.previous, coverages, metric)
        current.attrs["previous_daily"] = tuple(previous_daily.to_dict("records"))
    if comparison.state not in {ComparisonState.COMPARABLE, ComparisonState.BOTH_ZERO}:
        st.caption(comparison.reason or "Comparison unavailable.")
    else:
        st.caption(
            f"Compared with {periods.previous.start:%Y-%m-%d} to "
            f"{periods.previous.end:%Y-%m-%d} UTC using the same filters."
        )
    return current


def _retained_scope(periods) -> tuple | None:
    context = st.session_state.get("preflight_result")
    account = getattr(context, "account", "")
    role = getattr(context, "role", "")
    if not account or not role:
        return None
    return (
        account,
        role,
        st.session_state.get("active_page_id"),
        periods,
        tuple(st.session_state.get("users", ())),
        tuple(st.session_state.get("models", ())),
        tuple(st.session_state.get("tags", ())),
        None if st.session_state.get("tag_users") is None else tuple(st.session_state["tag_users"]),
        tuple(
            (name, tuple(st.session_state.get(name, ())))
            for name in PAGE_FILTER_KEYS.get(st.session_state.get("active_page_id"), ())
            if st.session_state.get(name)
        ),
    )


def render_retained_reload() -> None:
    """Render exact-scope last-successful facts before starting any reload work."""
    scope = _retained_scope(st.session_state.get("periods"))
    if scope is None:
        return
    for key, frame in st.session_state.get("last_successful_views", {}).items():
        saved_scope, source, metric, page_values = key
        if saved_scope != scope:
            continue
        st.warning(
            "Stale retained results while reloading the identical account, role, period and filters. Comparisons are suppressed."
        )
        partitions = agg.partition_scope(
            frame,
            FilterSelection(
                users=scope[4], models=scope[5], tags=scope[6], page_values=dict(page_values)
            ),
            tag_users=scope[7],
            source=source,
        )
        periods = frame.attrs.get("periods")
        for label, partition in (
            ("Matching retained credits (stale)", partitions.matched),
            ("Not attributable retained credits (stale)", partitions.unscoped),
        ):
            if partition.empty and partition is partitions.unscoped:
                continue
            current = agg.period_slice(partition, periods.current) if periods else partition
            value = fmt_credits(agg.sum_column(current, metric))
            if "UNRATED_TOKENS" in current and agg.sum_column(current, "UNRATED_TOKENS") > 0:
                st.warning("Retained estimate has missing rates and may understate credits.")
                if agg.sum_column(current, "UNRATED_TOKENS") >= agg.sum_column(current, "TOKENS"):
                    value = "Unavailable"
            st.metric(label, value)
            st.caption(
                "Attributed warehouse compute; separate from AI usage credits."
                if metric == "WAREHOUSE_CREDITS"
                else "Observed AI usage credits; Inference API credits are estimates."
            )
            detail = current.copy()
            detail.attrs = {}
            st.dataframe(detail, hide_index=True)


def retain_same_scope(
    frame: pd.DataFrame,
    source: str | None,
    metric: str,
    page_values: Mapping[str, Sequence[str]] | None,
) -> pd.DataFrame:
    """Retain a bounded last-successful view only with verified account and identical scope."""
    scope = _retained_scope(frame.attrs.get("periods"))
    if scope is None:
        return frame
    key = (
        scope,
        source,
        metric,
        tuple(
            sorted((name, tuple(values)) for name, values in (page_values or {}).items() if values)
        ),
    )
    evidence = frame.attrs.get("source_coverages", ())
    if coverage := frame.attrs.get("coverage"):
        evidence = (coverage,)
    unavailable = bool(evidence) and all(
        item.state == CoverageState.UNAVAILABLE for item in evidence
    )
    snapshots = st.session_state.setdefault("last_successful_views", {})
    context_key = scope[:2]
    if st.session_state.get("retained_view_context") != context_key:
        snapshots.clear()
        st.session_state.retained_view_context = context_key
    if unavailable and key in snapshots:
        previous = snapshots[key].copy(deep=True)
        previous.attrs["stale"] = True
        previous.attrs.pop("coverage", None)
        original_evidence = snapshots[key].attrs.get("source_coverages", ())
        if previous_coverage := snapshots[key].attrs.get("coverage"):
            original_evidence = (previous_coverage,)
        previous.attrs["source_coverages"] = tuple(
            replace(
                item,
                state=CoverageState.UNKNOWN,
                reason="Retained after failed reload; not a fresh read.",
            )
            for item in original_evidence
        )
        previous.attrs["comparisons"] = {}
        st.warning(
            "Stale retained results for the identical account, period and filters. Reload failed; comparisons are suppressed."
        )
        return previous
    if (
        evidence
        and not frame.attrs.get("unavailable_sources")
        and all(item.state != CoverageState.UNAVAILABLE for item in evidence)
    ):
        snapshots[key] = frame.copy(deep=True)
        while len(snapshots) > 8:
            snapshots.pop(next(iter(snapshots)))
    return frame


@overload
def scoped_usage(
    frame: pd.DataFrame,
    source: str | None = None,
    *,
    page_values: Mapping[str, Sequence[str]] | None = None,
    unscoped_view: bool = False,
    metric: str = "CREDITS",
) -> pd.DataFrame: ...


@overload
def scoped_usage(
    frame: None,
    source: str | None = None,
    *,
    page_values: Mapping[str, Sequence[str]] | None = None,
    unscoped_view: bool = False,
    metric: str = "CREDITS",
) -> None: ...


def scoped_usage(
    frame: pd.DataFrame | None,
    source: str | None = None,
    *,
    page_values: Mapping[str, Sequence[str]] | None = None,
    unscoped_view: bool = False,
    metric: str = "CREDITS",
) -> pd.DataFrame | None:
    """Display non-attributable usage separately and return only matching facts.

    Args:
        frame: Successfully loaded facts, or None for an unavailable source.
        source: Capability identity, omitted for unified facts.
        page_values: Active page filters keyed by fact column.
        unscoped_view: Dedicated unsupported page may display its labeled unscoped data.
        metric: Credit measure for this component.

    Returns:
        Matching facts, or the explicitly labeled unscoped dedicated view.
    """
    if frame is None:
        return None
    state = st.session_state
    if page_values:
        selected_labels = [
            f"{column.replace('_', ' ').title()}: {', '.join(values)}"
            for column, values in page_values.items()
            if values
        ]
        if selected_labels:
            st.caption("Selected: " + "; ".join(selected_labels))
    frame = retain_same_scope(frame, source, metric, page_values)
    scoped = agg.partition_scope(
        frame,
        FilterSelection(
            users=tuple(state.get("users", ())),
            models=tuple(state.get("models", ())),
            tags=tuple(state.get("tags", ())),
            page_values={column: tuple(values) for column, values in (page_values or {}).items()},
        ),
        tag_users=state.get("tag_users"),
        source=source,
    )
    matched = scoped.matched
    if not scoped.unscoped.empty:
        periods = frame.attrs.get("periods")
        unscoped_current = (
            agg.period_slice(scoped.unscoped, periods.current) if periods else scoped.unscoped
        )
        amount = agg.sum_column(unscoped_current, metric)
        st.warning(
            f"Not attributable to selected filters: {fmt_credits(amount)} credits. "
            "These records lack at least one selected attribution dimension and are not "
            "included in the matching total. Known mismatches are excluded."
        )
        if unscoped_view and scoped.matched.empty:
            st.subheader("Not attributable to selected filters")
            st.caption("All metrics and detail below describe unscoped usage, not filter matches.")
            return _current_scope(scoped.unscoped, metric)
        with st.expander("Not attributable: usage detail"):
            detail = unscoped_current.copy()
            detail.attrs = {}
            st.dataframe(detail, hide_index=True)
    return _current_scope(matched, metric)


def ai_functions_warehouse_coverage_notice(start_date: date, end_date: date) -> str | None:
    """Describe unsupported historical AI Functions warehouse attribution.

    Args:
        start_date: First requested UTC day, inclusive.
        end_date: Last requested UTC day, inclusive.

    Returns:
        A historical coverage notice, or None when no historical warning applies.
    """
    if start_date >= AI_FUNCTIONS_WAREHOUSE_START:
        return None
    if end_date < AI_FUNCTIONS_WAREHOUSE_START:
        return (
            "AI Functions warehouse attribution is incomplete for this date range. "
            "The supported usage source contains data only from January 5, 2026. "
            "Missing earlier attribution does not mean zero warehouse usage."
        )
    return (
        "This date range includes days before January 5, 2026. AI Functions warehouse "
        "attribution is incomplete for those earlier days because the supported usage "
        "source has no earlier history."
    )


RESOURCE_STATUS_COLORS = {
    "Active": "#00843D",
    "Configured": "#00843D",
    "Created": "#00843D",
    "Suspended": "#B54708",
    "Not created": "#B42318",
}


def resource_status_color(status: str, *, dark: bool = False) -> str:
    """Return the semantic text color for a resource status value.

    Args:
        status: Resource lifecycle state shown in the Config table.
        dark: Use contrast-safe colors for the dark background.

    Returns:
        Hex color for the displayed status.
    """
    if dark:
        if status in {"Ready", "Active", "Configured", "Created"}:
            return "#4ade80"
        if status in {"Suspended", "Stale snapshot", "Awaiting first refresh"}:
            return "#fbbf24"
        if status in {"Refresh failed", "Not created", "Task missing"}:
            return "#f87171"
        return "#cbd5e1"
    extra = {
        "Ready": "#166534",
        "Stale snapshot": "#92400e",
        "Awaiting first refresh": "#92400e",
        "Refresh failed": "#b91c1c",
        "Task missing": "#b91c1c",
    }
    return extra.get(status, RESOURCE_STATUS_COLORS.get(status, "#667085"))


def loading_query_summary(parallel_count: int, dependent_count: int = 1) -> str:
    """Describe the cold-load query sequence shown in the loading dialog.

    Args:
        parallel_count: Number of independent statements in the first wave.
        dependent_count: Number of statements that require the first-wave results.

    Returns:
        Concise loading status text with the total query count.
    """
    total_count = parallel_count + dependent_count
    return (
        f"Running {parallel_count} independent queries in parallel, then "
        f"{dependent_count} dependent query ({total_count} total)..."
    )


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------


def fmt_int(value: Number) -> str:
    """Format a count with thousands separators.

    Args:
        value: Count to format. Truncated to an integer.

    Returns:
        The formatted count, or ``"Unavailable"`` for None or NaN.
    """
    if value is None or pd.isna(value):
        return "Unavailable"
    return f"{int(value):,}"


def fmt_credits(value: Number, decimals: int = 2) -> str:
    """Format a credit amount to a fixed number of decimals.

    Args:
        value: Credit amount to format.
        decimals: Decimal places to keep.

    Returns:
        The amount, a positive below-precision indicator, or ``"Unavailable"``.
    """
    if value is None or pd.isna(value):
        return "Unavailable"
    if 0 < value < 10**-decimals:
        return f"<{10**-decimals:.{decimals}f}"
    return f"{float(value):,.{decimals}f}"


def fmt_tokens(value: Number) -> str:
    """Format a token count compactly, as 1.2B / 340.5M / 12.3K.

    Args:
        value: Token count to format.

    Returns:
        The count scaled to the largest fitting unit, thousands-separated in
        full below 1,000, or ``"Unavailable"`` for None or NaN.
    """
    if value is None or pd.isna(value):
        return "Unavailable"
    n = float(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= threshold:
            return f"{n / threshold:,.1f}{suffix}"
    return f"{int(n):,}"


def inference_pricing_warning(unrated: Number, *, overview: bool = False) -> str:
    """Explain an unpriced count (None when unknown) and its effect on visible credits."""
    if unrated is None or pd.isna(unrated):
        summary = (
            "Cortex Inference API pricing coverage is unknown: the unpriced token count is "
            "unavailable because pricing details are missing. "
        )
        impact = (
            "Estimated credits and AI usage credits may exclude unpriced usage. "
            if overview
            else "Credits (partial estimate) may exclude unpriced usage. "
        )
    else:
        noun = "token" if unrated == 1 else "tokens"
        summary = (
            f"{fmt_int(unrated)} Cortex Inference API {noun} could not be priced in this view "
            "because a model/token-type rate or token-type breakdown is missing. "
        )
        impact = (
            "Their credits are excluded from Estimated credits and AI usage credits. "
            if overview
            else "Their credits are excluded from Credits (partial estimate). "
        )
    unaffected = "Reported credits are unaffected. " if overview else ""
    return (
        summary
        + impact
        + unaffected
        + "Tokens and requests are still counted; unpriced does not mean free."
    )


def inference_pricing_label(unrated: Number) -> str:
    """Label pricing completeness without rounding a small unpriced count to 100%."""
    if unrated is None or pd.isna(unrated):
        return "Token pricing coverage unknown"
    if unrated > 0:
        noun = "token" if unrated == 1 else "tokens"
        return f"{fmt_int(unrated)} {noun} not priced"
    return "All reported tokens have observed rates"


# ---------------------------------------------------------------------------
# Shared KPI tooltips
#
# Every KPI carries a help tooltip. These cover the metrics whose definition is
# identical across surfaces; pages pass tailored text where the nuance differs.
# ---------------------------------------------------------------------------

HELP_TOKENS = (
    "All token types summed: input, output, cache-read and cache-write. "
    "Cache-read usually dominates and bills at a fraction of the input-token rate, "
    "which is why credits grow far more slowly than raw token counts."
)
HELP_CALLS = (
    "One row per billable request in ACCOUNT_USAGE. Not the same as user prompts: "
    "a single interaction can span several requests."
)
HELP_USERS = "Distinct users with at least one billable request in the selected window."
HELP_ACTIVE_DAYS = (
    "Distinct calendar days with recorded usage. Compare with the window length to "
    "see whether consumption is steady or bursty."
)
# Appended to the credits tooltip on every page, because the credits card is the
# only one carrying a delta.
HELP_DELTA = "Change against the previous equal-length completed UTC period; hidden unless coverage is comparable."


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------


def page_header(
    title: str,
    subtitle: str,
    pills: Sequence[str] | None = None,
    eyebrow: str | None = None,
) -> None:
    """Render the report-style page header.

    Args:
        title: Page title.
        subtitle: Muted one-line description under the title.
        pills: Labels to render as blue badges beneath the subtitle, usually the
            surfaces or sub-surfaces the page covers.
        eyebrow: Small blue kicker above the title, upper-cased on render.
    """
    if not st.session_state.get("entrypoint_title"):
        st.title(title)
        if eyebrow:
            st.caption(eyebrow)
    st.caption(subtitle)
    if pills and not st.session_state.get("entrypoint_title"):
        st.caption(", ".join(pills))


def _chip(label: str, values: Sequence | None, limit: int = 2) -> str | None:
    """Summarise one active filter for the filter strip.

    Args:
        label: Filter name, such as ``Users``.
        values: Selected values.
        limit: How many values to name before collapsing to a count.

    Returns:
        ``"Label: a, b"``, or ``"Label: 5 selected"`` past the limit, or None if
        the filter is inactive and should not appear at all.
    """
    if not values:
        return None
    if len(values) <= limit:
        return f"{label}: {', '.join(str(v) for v in values)}"
    return f"{label}: {len(values)} selected"


def filter_summary(
    users: Sequence[str] | None = None,
    models: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
) -> None:
    """Echo the active sidebar filters above the page.

    Ensures the view is never silently filtered when the sidebar is collapsed.
    Renders nothing when no filter is active.

    Args:
        users: Selected usernames.
        models: Selected model names.
        tags: Selected user tag labels.
    """
    chips = [
        c
        for c in (
            _chip("Users", users),
            _chip("Models", models),
            _chip("Tags", tags),
        )
        if c
    ]
    if not chips:
        return
    st.caption(":material/filter_alt: Filtered: " + "  ·  ".join(chips))


def section(title: str):
    """Open a native unbordered section with an H2 heading.

    Args:
        title: Heading text. Slugified into the container key, so it must be
            unique within a page; it is stable across reruns.

    Returns:
        The container, for use as a context manager.
    """
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
    box = st.container(key=f"section-{slug}")
    box.header(title, anchor=False)
    return box


def insight_banner(detail: str) -> None:
    """Render a concise driver observation without duplicating the headline metric."""
    with st.expander("Leading driver"):
        st.caption(detail)


class KpiCard(TypedDict, total=False):
    """One KPI card.

    Attributes:
        label: Card label. Required.
        value: Pre-formatted value string. Required.
        help: Tooltip text, rendered as a question-mark icon on the label.
            Required in practice: every metric in this app explains itself.
        icon: Material Symbols name, shown in a tinted badge.
        color: Badge color -- blue, violet, green, orange or gray.
        caption: Short derived stat under the value, such as a per-day rate.
            None hides it, which lets callers write a conditional inline.
        trend: Series for the sparkline.
        delta: Delta string. Also what colors the sparkline.
    """

    label: Required[str]
    value: Required[str]
    help: str
    icon: str
    color: str
    caption: str | None
    trend: Sequence[float] | None
    delta: str | None


def kpi_cards(cards: Sequence[KpiCard]) -> None:
    """Render equal-width KPI cards across the full page width.

    Args:
        cards: Cards to render, left to right.
    """
    with st.container(horizontal=True):
        for card in cards[:4]:
            _kpi_card(card)
    if len(cards) > 4:
        with st.expander("Additional metrics"), st.container(horizontal=True):
            for card in cards[4:]:
                _kpi_card(card)


def _kpi_card(card: KpiCard) -> None:
    """Render one KPI card: icon badge, label with tooltip, value, sub-caption.

    Args:
        card: The card to render.
    """
    with st.container(width=220):
        st.metric(
            card["label"],
            card["value"],
            help=card.get("help"),
            delta=card.get("delta"),
            delta_color="off",
        )
        if card.get("caption"):
            st.caption(f":gray[{card['caption']}]")


class StandardCards(NamedTuple):
    """The four KPI cards every surface page shows.

    Returned as named fields rather than a list because CoWork interleaves a
    bespoke card between credits and tokens, so pages need to reorder them.

    Attributes:
        credits: Credits card, carrying the sparkline and delta.
        tokens: Token total card.
        calls: Request count card.
        users: Distinct user card.
    """

    credits: KpiCard
    tokens: KpiCard
    calls: KpiCard
    users: KpiCard


def standard_cards(
    totals: dict[str, float],
    window_days: int,
    *,
    credits_help: str,
    credits_label: str = "Credits",
    tokens_label: str = "Tokens",
    tokens_help: str = HELP_TOKENS,
    calls_label: str = "Calls",
    calls_help: str = HELP_CALLS,
    users_label: str = "Users",
    call_noun: str = "call",
    trend: Sequence[float] | None = None,
    delta: str | None = None,
) -> StandardCards:
    """Build the credits, tokens, calls and users cards shared by every page.

    Captions are derived here so the per-day and per-call rates are phrased
    identically everywhere, and are omitted rather than shown as a division by
    zero when the window has no calls or users.

    Args:
        totals: Output of :func:`utils.agg.totals`.
        window_days: Output of :func:`utils.agg.window_days`, used for the
            per-day captions.
        credits_help: Tooltip for the credits card. Required because what
            credits mean differs per surface -- exact on most, estimated on the
            Cortex Inference API, token-only on CoWork. End it with
            :data:`HELP_DELTA`, since this is the card carrying the delta.
        credits_label: Label for the credits card, for surfaces that qualify it.
        tokens_label: Label for the tokens card.
        tokens_help: Tooltip for the tokens card.
        calls_label: Label for the calls card, for surfaces that count
            requests rather than calls.
        calls_help: Tooltip for the calls card.
        users_label: Label for the users card.
        call_noun: Noun for the tokens-per-call caption, matching
            ``calls_label``.
        trend: Sparkline series, from :func:`utils.agg.daily_trend`.
        delta: Delta string, from :func:`utils.agg.trend_delta`.

    Returns:
        The four cards, ready to pass to :func:`kpi_cards` in any order.
    """
    credits = totals["CREDITS"]
    tokens = totals["TOKENS"]
    calls = totals["CALLS"]
    users = totals["USERS"]

    return StandardCards(
        credits={
            "label": credits_label,
            "value": fmt_credits(credits),
            "help": credits_help,
            "icon": "toll",
            "color": "blue",
            "caption": f"{fmt_credits(credits / window_days)} observed / day ({window_days} days)",
            "trend": trend,
            "delta": delta,
        },
        tokens={
            "label": tokens_label,
            "value": fmt_tokens(tokens),
            "help": tokens_help,
            "icon": "generating_tokens",
            "color": "violet",
            "caption": f"{fmt_tokens(tokens / calls)} per {call_noun}" if calls else None,
        },
        calls={
            "label": calls_label,
            "value": fmt_int(calls),
            "help": calls_help,
            "icon": "bolt",
            "color": "green",
            "caption": f"{fmt_int(calls / window_days)} observed / day ({window_days} days)",
        },
        users={
            "label": users_label,
            "value": fmt_int(users),
            "help": HELP_USERS,
            "icon": "group",
            "color": "orange",
            "caption": f"{fmt_credits(credits / users)} credits each" if users else None,
        },
    )


def container_row():
    """Open a horizontal container that wraps on narrow screens.

    Returns:
        The container, for use as a context manager.
    """
    return st.container(horizontal=True)


def warehouse_component(result, surface: str) -> None:
    """Render auxiliary warehouse costs without implying user/model attribution.

    Args:
        result: WarehouseFactsResult from the shared loader.
        surface: AI service whose warehouse component to display.
    """
    st.header("Attributed warehouse compute")
    st.caption(
        "Scope: service and selected dates only. User, model, tag, agent and other "
        "page filters do not narrow this component. Separate from AI credits; do not "
        "add it to CoWork SQL metadata credits, which can overlap."
    )
    if result.degraded:
        st.warning(result.message or "Warehouse source coverage is incomplete.")
    if result.degraded and result.frame.empty:
        st.metric("Warehouse credits", "Unavailable")
        return
    frame = result.frame
    if periods := frame.attrs.get("periods"):
        frame = agg.period_slice(frame, periods.current)
    values = agg.warehouse_credits_for_surface(frame, surface)
    st.metric("Warehouse credits", fmt_credits(values["TOTAL"]))


class LoadState(Enum):
    """Reason a page has no data to display.

    Values are used by :func:`infer_state` and :func:`empty_state` to select
    the right message and icon without duplicating the text on every page.

    Attributes:
        OK: Data is present; no empty state needed.
        NO_USAGE: The loader returned no rows for the selected window,
            independent of any active filters.
        FILTERED_EMPTY: The loader returned rows but the active filters
            reduced the result to zero.
        SOURCE_UNAVAILABLE: The loader returned ``None``, signalling that the
            underlying data source (e.g. an ACCOUNT_USAGE view) could not be
            reached.
    """

    OK = "ok"
    NO_USAGE = "no_usage"
    FILTERED_EMPTY = "filtered_empty"
    SOURCE_UNAVAILABLE = "source_unavailable"


# Default (message, icon) per state.  FILTERED_EMPTY is intentionally the
# same as the legacy default so existing callers are unaffected.
_STATE_DEFAULTS: dict[LoadState, tuple[str, str]] = {
    LoadState.FILTERED_EMPTY: (
        "No usage matches the current filters.",
        ":material/filter_alt_off:",
    ),
    LoadState.NO_USAGE: (
        "No usage recorded in this date window.",
        ":material/inbox:",
    ),
    LoadState.SOURCE_UNAVAILABLE: (
        "This data source is not accessible. "
        "Check **Acceleration Settings** and the connection preflight for diagnostics.",
        ":material/error:",
    ),
}


def infer_state(
    raw_df: pd.DataFrame | None,
    *,
    active_filters: bool = False,
) -> LoadState:
    """Infer why a page has nothing to display.

    Call this with the DataFrame returned by the loader *before* any filter is
    applied, then pass the result to :func:`empty_state` so every page shows a
    consistent, accurate explanation.

    Args:
        raw_df: The DataFrame returned by the loader before any
            ``agg.apply_filters`` call.  ``None`` signals that the loader
            explicitly returned no result (source unavailable).
        active_filters: ``True`` when at least one sidebar filter (user, model,
            or tag) is active and was applied to *raw_df*.  Used to
            distinguish a window with no usage from a window that has usage
            hidden by filters.

    Returns:
        :attr:`LoadState.SOURCE_UNAVAILABLE` when *raw_df* is ``None``,
        :attr:`LoadState.FILTERED_EMPTY` when *raw_df* had rows but
        *active_filters* is ``True`` (implying filters removed them all), or
        :attr:`LoadState.NO_USAGE` when the source had no rows regardless of
        filters.
    """
    if raw_df is None:
        return LoadState.SOURCE_UNAVAILABLE
    coverage = raw_df.attrs.get("coverage")
    if coverage is not None and coverage.state == CoverageState.UNAVAILABLE:
        return LoadState.SOURCE_UNAVAILABLE
    if not raw_df.empty and active_filters:
        return LoadState.FILTERED_EMPTY
    return LoadState.NO_USAGE


def empty_state(
    message: str | None = None,
    icon: str | None = None,
    *,
    state: LoadState | None = None,
) -> None:
    """Show a neutral placeholder in place of an empty chart or table.

    When *state* is given, the default message and icon for that state are
    used as fallbacks.  The caller may override either value explicitly — with
    one exception: when *state* is :attr:`LoadState.SOURCE_UNAVAILABLE` the
    diagnostic message always wins over a caller-supplied "no data" string,
    because the root cause is access, not absence of rows.

    When neither *state* nor explicit values are provided, the function
    falls back to the :attr:`LoadState.FILTERED_EMPTY` defaults — the same
    behaviour as before *state* was introduced.

    Args:
        message: Override text.  ``None`` uses the state (or fallback) default.
            Ignored when *state* is :attr:`LoadState.SOURCE_UNAVAILABLE`.
        icon: Override Material icon string.  ``None`` uses the state default.
        state: The :class:`LoadState` that explains why the page is empty.
            Drives the default message and icon.
    """
    effective_state = state if state is not None else LoadState.FILTERED_EMPTY
    default_msg, default_icon = _STATE_DEFAULTS[effective_state]
    # For SOURCE_UNAVAILABLE, always show the diagnostic message so a page's
    # "no data" copy never masks an access problem the user needs to act on.
    use_default_msg = effective_state == LoadState.SOURCE_UNAVAILABLE
    st.info(
        default_msg if use_default_msg or message is None else message,
        icon=icon if icon is not None else default_icon,
    )


def empty_usage(
    frame: pd.DataFrame | None,
    *,
    state: LoadState = LoadState.NO_USAGE,
    label: str = "Credits",
    metric: str = "CREDITS",
) -> None:
    """Retain covered zero-period comparisons without inventing usage records.

    Args:
        frame: Empty current facts carrying the evaluated comparison, or unavailable.
        state: Fallback explanation when no comparison metadata exists.
        label: Credit metric label, including estimate or warehouse qualification.
        metric: Evaluated additive measure.
    """
    comparison = None if frame is None else frame.attrs.get("comparisons", {}).get(metric)
    if frame is not None and frame.attrs.get("stale"):
        st.metric(f"Retained {label.lower()} (stale)", fmt_credits(agg.sum_column(frame, metric)))
        return
    if (
        state == LoadState.SOURCE_UNAVAILABLE
        or comparison is None
        or comparison.state == ComparisonState.UNAVAILABLE
    ):
        unavailable = state == LoadState.SOURCE_UNAVAILABLE or (
            comparison is not None and comparison.state == ComparisonState.UNAVAILABLE
        )
        if unavailable:
            st.metric(label, "Unavailable")
        empty_state(state=LoadState.SOURCE_UNAVAILABLE if unavailable else state)
        return
    comparable = comparison.delta_percent is not None
    st.metric(
        label if comparable else f"Observed {label.lower()}",
        fmt_credits(comparison.current),
        delta=agg.trend_delta(frame, metric),
        delta_color="off",
    )
    if comparable:
        st.info("No usage matches this scope in the completed current period.")
        if comparison.state == ComparisonState.BOTH_ZERO:
            st.caption("No change: both covered periods have zero usage.")
    else:
        st.info("No current-period records were returned. This does not establish zero usage.")
    if comparison.previous is not None:
        st.caption(f"Previous-period observed credits: {fmt_credits(comparison.previous)}.")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def trend_chart(
    df: pd.DataFrame | None,
    value_col: str = "CREDITS",
    color_col: str | None = None,
    y_label: str = "Credits",
) -> None:
    """Draw a daily trend area chart, optionally split into one series per category.

    Renders :func:`empty_state` instead of an empty axis when there is no data.

    Args:
        df: Daily frame from :func:`utils.agg.daily`, carrying USAGE_DATE.
        value_col: Metric to plot.
        color_col: Column to split into one series per value. Ignored if absent
            from the frame.
        y_label: Y-axis label.
    """
    if df is None or df.empty:
        empty_state()
        return

    data = df.copy()
    data.attrs = {}
    data["USAGE_DATE"] = pd.to_datetime(data["USAGE_DATE"])
    page_id = st.session_state.get("active_page_id", "detail")
    key = f"chart_{page_id}_{value_col}"
    mode = st.segmented_control(
        "Display", ["Chart", "Table"], default="Chart", key=key, bind="query-params"
    )
    previous_daily = df.attrs.get("previous_daily")
    compare = bool(previous_daily) and st.toggle(
        "Previous period", key=f"compare_{page_id}", bind="query-params"
    )
    if compare:
        data = agg.relative_period_daily(data, pd.DataFrame(previous_daily), value_col)
        if mode == "Table":
            st.dataframe(data, hide_index=True)
        else:
            spec = trend_spec(value_col, "Period", y_label, dark=st.context.theme.type == "dark")
            spec["encoding"]["x"] = {
                "field": "Day",
                "type": "quantitative",
                "axis": {"tickMinStep": 1},
            }
            st.vega_lite_chart(data, spec, width="stretch")
        return
    if color_col and color_col in data:
        presentation = st.selectbox(
            "Trend",
            ["Total", "Compare services", "Composition"],
            key=f"trend_{page_id}",
            bind="query-params",
        )
    else:
        presentation = "Total"
    if presentation == "Total":
        data = data.groupby("USAGE_DATE", as_index=False)[value_col].agg(
            lambda values: values.sum(min_count=len(values))
        )
        color_col = None
    if mode == "Table":
        st.dataframe(data, hide_index=True)
        return
    spec = trend_spec(
        value_col,
        color_col,
        y_label,
        composition=presentation == "Composition",
        dark=st.context.theme.type == "dark",
    )
    st.vega_lite_chart(data, spec, width="stretch")


def trend_spec(
    metric: str, series: str | None, label: str, *, composition: bool = False, dark: bool = False
) -> dict:
    """Build a null-breaking trend with stable service colors and dash encodings."""
    names = [*q.SURFACES, *q.COCO_SUB_SURFACES, "Current", "Previous"]
    colors = (
        [
            "#29B5E8",
            "#a78bfa",
            "#4ade80",
            "#fbbf24",
            "#f472b6",
            "#60a5fa",
            "#2dd4bf",
            "#fb923c",
            "#c4b5fd",
        ]
        if dark
        else [
            "#0369a1",
            "#6d28d9",
            "#166534",
            "#92400e",
            "#be185d",
            "#1d4ed8",
            "#0f766e",
            "#9a3412",
            "#7e22ce",
        ]
    )
    colors += [colors[0], colors[1]]
    encoding = {
        "x": {
            "field": "USAGE_DATE",
            "type": "temporal",
            "title": None,
            "axis": {"format": "%b %d", "tickMinStep": 86400000},
        },
        "y": {
            "field": metric,
            "type": "quantitative",
            "title": label,
            "stack": "zero" if composition else None,
        },
        "tooltip": [
            {"field": "USAGE_DATE", "type": "temporal", "format": "%Y-%m-%d"},
            {"field": metric, "type": "quantitative", "format": ".8f"},
        ],
    }
    if series:
        encoding["color"] = {
            "field": series,
            "type": "nominal",
            "scale": {"domain": names, "range": colors},
            "legend": {"title": None},
        }
        encoding["tooltip"].append({"field": series, "type": "nominal"})
        if not composition:
            encoding["strokeDash"] = {
                "field": series,
                "type": "nominal",
                "legend": None,
                "scale": {
                    "domain": names,
                    "range": [
                        [1, 0],
                        [6, 3],
                        [2, 2],
                        [8, 2, 2, 2],
                        [10, 4],
                        [4, 2],
                        [1, 3],
                        [8, 4, 2, 4],
                        [12, 2],
                        [1, 0],
                        [6, 3],
                    ],
                },
            }
    return {
        "mark": {
            "type": "area" if composition else "line",
            "invalid": "break-paths-show-domains",
            "point": not composition,
        },
        "encoding": encoding,
        "height": 280,
    }


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

_NUMERIC_CONFIG = {
    "CALLS": st.column_config.NumberColumn("Usage records", format="localized"),
    "TOKENS": st.column_config.NumberColumn("Tokens", format="compact"),
    "CREDITS": st.column_config.NumberColumn("Credits", format="%.8f"),
    "USERS": st.column_config.NumberColumn("Users", format="localized"),
}


def _share_label(column: str, extra_columns: Mapping[str, object]) -> str:
    """Return the display label for ``column``, for use in the share-bar header.

    Reads the label off the column's own Streamlit config so the share bar and the
    column it describes cannot disagree. Column configs are dicts at runtime, but
    an unexpected shape falls back to the prettified column name rather than
    raising, since a header is not worth a broken page.
    """
    config = {**_NUMERIC_CONFIG, **extra_columns}.get(column)
    if isinstance(config, Mapping):
        configured = config.get("label")
        if isinstance(configured, str) and configured:
            return configured.lower()
    return column.replace("_", " ").lower()


def breakdown_table(
    df: pd.DataFrame | None,
    label: str,
    label_col: str | None = None,
    share_of: str = "CREDITS",
    share_label: str | None = None,
    extra_columns: Mapping[str, Any] | None = None,
    *,
    denominator: float | None = None,
    limit: int | None = None,
    calls_label: str = "Usage records",
    chart_key: str | None = None,
) -> None:
    """Render a sortable breakdown table with a share-of-total progress bar.

    Renders :func:`empty_state` instead of an empty table when there is no data.

    Args:
        df: Breakdown frame from :func:`utils.agg.by_dimension`. Only the
            metric columns present are shown, so one helper serves every page.
        label: Header for the dimension column.
        label_col: Column holding the dimension values. Defaults to the frame's
            first column, which is the label column :func:`utils.agg.by_dimension`
            produces, so the name does not have to be repeated at the call site.
        share_of: Metric the fixed 0-100% share bar represents.
        share_label: Wording for the share column header, which reads
            ``Share of <share_label>``. Defaults to the label configured for the
            ``share_of`` column, so the header matches that column's own header.
        extra_columns: Page-specific columns to show between the standard metrics
            and the share bar, mapping column name to a Streamlit column config.
            Columns absent from the frame are skipped, so a caller can offer a
            superset. Drop a standard metric by dropping it from ``df`` first.
        denominator: Full metric total, required for pre-truncated or overlapping groups.
        limit: Display top rows only after computing shares from the full frame.
        calls_label: Explicit physical-count interpretation for this component.
        chart_key: Stable page-specific identity enabling ranked Chart/Table and top-N controls.
    """
    if df is None or df.empty:
        empty_state()
        return

    data = df.copy()
    data.attrs = {}
    label_col = label_col or str(data.columns[0])
    total = data[share_of].sum() if denominator is None else denominator
    chart_mode = "Table"
    if chart_key:
        with st.container(horizontal=True):
            chart_mode = st.segmented_control(
                "Display",
                ["Chart", "Table"],
                default="Chart",
                key=f"chart_{chart_key}",
                bind="query-params",
            )
            top_n = st.selectbox(
                "Show", [10, 25, 50, "All"], key=f"top_{chart_key}", bind="query-params"
            )
        data = data.sort_values(share_of, ascending=False, kind="stable")
        limit = None if top_n == "All" else int(top_n)
        if limit is None:
            st.caption(f"All {len(data)} groups; full denominator {total:,.8f}.")
    data["SHARE"] = data[share_of] / total if total else 0.0
    if limit is not None:
        st.caption(f"Top {min(limit, len(data))} of {len(data)}; full denominator {total:,.8f}.")
        if limit < 1:
            raise ValueError("Top-N limit must be positive.")
        remainder = data.iloc[limit:]
        data = data.head(limit).copy()
        if not remainder.empty and share_of in {"CREDITS", "WAREHOUSE_CREDITS", "COMPUTE_CREDITS"}:
            other = {label_col: "Other", share_of: remainder[share_of].sum()}
            other["SHARE"] = other[share_of] / total if total else 0.0
            data = pd.concat([data, pd.DataFrame([other])], ignore_index=True)

    extra_columns = extra_columns or {}
    if chart_mode == "Chart":
        st.vega_lite_chart(
            data[[label_col, share_of, "SHARE"]],
            {
                "layer": [
                    {"mark": "bar"},
                    {
                        "mark": {"type": "text", "align": "left", "dx": 4},
                        "encoding": {
                            "text": {"field": share_of, "type": "quantitative", "format": ".4f"}
                        },
                    },
                ],
                "height": min(700, max(160, len(data) * 28)),
                "encoding": {
                    "y": {
                        "field": label_col,
                        "type": "nominal",
                        "sort": "-x",
                        "title": label,
                        "axis": {"labelLimit": 240},
                    },
                    "x": {
                        "field": share_of,
                        "type": "quantitative",
                        "title": _share_label(share_of, extra_columns),
                    },
                    "tooltip": [
                        {"field": label_col},
                        {"field": share_of, "format": ".8f"},
                        {"field": "SHARE", "format": ".2%"},
                    ],
                },
            },
            width="stretch",
        )
        return
    extras = [c for c in extra_columns if c in data.columns]

    config = {label_col: st.column_config.TextColumn(label)}
    config.update({k: v for k, v in _NUMERIC_CONFIG.items() if k in data.columns})
    if "CALLS" in data.columns:
        config["CALLS"] = st.column_config.NumberColumn(calls_label, format="localized")
    config.update({c: extra_columns[c] for c in extras})
    config["SHARE"] = st.column_config.ProgressColumn(
        f"Share of {share_label or _share_label(share_of, extra_columns)}",
        format="percent",
        min_value=0.0,
        max_value=1.0,
    )

    ordered = (
        [label_col]
        + [c for c in ["CREDITS", "TOKENS", "CALLS", "USERS"] if c in data.columns]
        + [
            column
            for column in extras
            if column not in {label_col, "CREDITS", "TOKENS", "CALLS", "USERS"}
        ]
        + ["SHARE"]
    )

    st.dataframe(
        data[ordered],
        column_config=config,
        hide_index=True,
        width="stretch",
    )


def token_mix_table(frame: pd.DataFrame | None) -> None:
    """Render token-type totals as a ranked table with a share-of-tokens bar.

    Args:
        frame: Long-form frame from :func:`utils.agg.token_mix`. A frame that
            sums to zero is treated as no data, since a surface with no token
            detail reports zeros rather than no rows.
    """
    if frame is None or frame.empty or frame["TOKENS"].sum() == 0:
        empty_state("No token detail available for the current filters.")
        return

    breakdown_table(frame, "Token type", share_of="TOKENS")
