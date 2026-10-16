"""Cortex AI Functions credits, tokens and hourly usage records."""

import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_AI_FUNCTIONS,
    "Cortex AI SQL function usage from CORTEX_AI_FUNCTIONS_USAGE_HISTORY.",
    eyebrow="Cortex surface",
)

if notice := ui.ai_functions_warehouse_coverage_notice(state.start_date, state.end_date):
    st.warning(notice, icon=":material/warning:")

with st.spinner(f"Loading {q.SURFACE_AI_FUNCTIONS} usage..."):
    facts = ui.load_period_usage(q.SURFACE_AI_FUNCTIONS, state.start_date, state.end_date)

if _msg := state.get("_degraded", {}).get(q.SURFACE_AI_FUNCTIONS):
    st.caption(f":material/warning: {_msg}")

_raw = facts

if facts is None or facts.empty:
    _active = bool(state.get("users") or state.get("models") or state.get("tag_users"))
    ui.empty_usage(
        ui.scoped_usage(facts, q.SURFACE_AI_FUNCTIONS),
        state=ui.infer_state(_raw, active_filters=_active),
    )
    ui.stop_page()

controls = st.columns(2, vertical_alignment="bottom")
with controls[0]:
    selected_functions = ui.scope_filter(
        "Function",
        agg.options(facts, "FUNCTION_NAME"),
        key="aifn_functions",
        placeholder="All functions",
    )
with controls[1]:
    selected_tags = ui.scope_filter(
        "Query tag",
        agg.options(facts, "QUERY_TAG"),
        key="aifn_tags",
        placeholder="All query tags",
    )

facts = ui.scoped_usage(
    facts,
    q.SURFACE_AI_FUNCTIONS,
    page_values={"FUNCTION_NAME": selected_functions, "QUERY_TAG": selected_tags},
)

if facts.empty:
    ui.empty_usage(facts, state=ui.LoadState.FILTERED_EMPTY)
    ui.stop_page()

totals = agg.totals(facts)

window_days = agg.window_days(state.start_date, state.end_date)
credits = totals["CREDITS"]
calls = totals["CALLS"]
functions = facts["FUNCTION_NAME"].nunique()

by_function = agg.by_dimension(facts, "FUNCTION_NAME", label_col="FUNCTION_NAME")
top = agg.leader(by_function, credits)

if top is not None:
    top_function, share = top
    ui.insight_banner(
        f"{top_function['FUNCTION_NAME']} leads at {share:.0%} of credits · "
        f"{ui.fmt_int(functions)} distinct functions · "
        f"{ui.fmt_int(calls / window_days)} observed usage records / day ({window_days} days).",
    )

cards = ui.standard_cards(
    totals,
    window_days,
    credits_help=ui.HELP_DELTA,
    tokens_help="Summed from the METRICS array, covering token-metered functions only. "
    "Functions billed per page or per row (for example AI_PARSE_DOCUMENT) "
    "can report credits without tokens. Token totals exclude non-token work; "
    "their reported credits are still included.",
    calls_label="Usage records",
    call_noun="usage record",
    calls_help="Hourly function/model usage records. Long-running invocations can span "
    "multiple records; this is not an invocation count.",
    trend=agg.daily_trend(facts),
    delta=agg.trend_delta(facts),
)

ui.kpi_cards(
    [
        *cards,
        {
            "label": "Functions",
            "value": ui.fmt_int(functions),
            "help": "Distinct Cortex AI functions invoked in the selected window, after filters.",
            "icon": "function",
            "color": "gray",
            "caption": f"{ui.fmt_credits(credits / functions)} credits each" if functions else None,
        },
    ]
)
incomplete = agg.sum_column(facts, "INCOMPLETE_CALLS")
if incomplete:
    st.warning(
        f"{ui.fmt_int(incomplete)} usage records were not marked complete in their aggregation "
        "window. This is not a failed-call count. Their reported credits are included above.",
        icon=":material/warning:",
    )

with ui.section("Credits Over Time"):
    ui.trend_chart(agg.daily(facts), y_label="Credits")

with ui.section("Breakdowns"):
    labels = {
        "By function": "FUNCTION_NAME",
        "By model": "MODEL_NAME",
        "By user": "USER_NAME",
        "By query tag": "QUERY_TAG",
        "By user tag": "__tag__",
    }
    choice = st.segmented_control(
        "Group by",
        list(labels.keys()),
        default="By function",
        key="aifn_breakdown",
        bind="query-params",
        label_visibility="collapsed",
    )
    column = labels.get(choice or "By function", "FUNCTION_NAME")

    if column == "__tag__":
        frame = agg.by_user_tag(facts, q.load_user_tags())
        ui.breakdown_table(frame, "User tag", label_col="TAG_LABEL", denominator=credits)
        st.caption("Tag groups overlap; their shares are not additive.")
    else:
        frame = agg.by_dimension(facts, column)
        ui.breakdown_table(frame, (choice or "By function").replace("By ", "").capitalize())

    if column == "FUNCTION_NAME":
        st.caption(
            "Token counts come from the METRICS array and cover token-metered functions. "
            "Functions billed per page or per row (for example AI_PARSE_DOCUMENT) report "
            "credits with zero tokens."
        )

ui.optional_warehouse(q.SURFACE_AI_FUNCTIONS, state.start_date, state.end_date)
