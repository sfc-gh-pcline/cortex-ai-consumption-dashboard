"""Cortex Agents usage: credits, tokens and request volume by agent and user."""

import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_AGENTS,
    "Agent request usage from CORTEX_AGENT_USAGE_HISTORY.",
    eyebrow="Cortex surface",
)

with st.spinner(f"Loading {q.SURFACE_AGENTS} usage..."):
    facts = ui.load_period_usage(q.SURFACE_AGENTS, state.start_date, state.end_date)

if _msg := state.get("_degraded", {}).get(q.SURFACE_AGENTS):
    st.caption(f":material/warning: {_msg}")

_raw = facts

if facts is None or facts.empty:
    _active = bool(state.get("users") or state.get("tag_users"))
    ui.empty_usage(
        ui.scoped_usage(facts, q.SURFACE_AGENTS, unscoped_view=True),
        state=ui.infer_state(_raw, active_filters=_active),
    )
    ui.stop_page()

selected_agents = ui.scope_filter(
    "Agent",
    agg.options(facts, "AGENT_FQN"),
    key="agent_fqns",
    placeholder="All agents",
)
facts = ui.scoped_usage(
    facts,
    q.SURFACE_AGENTS,
    page_values={"AGENT_FQN": selected_agents},
    unscoped_view=True,
)

if facts.empty:
    ui.empty_usage(facts, state=ui.LoadState.FILTERED_EMPTY)
    ui.stop_page()

totals = agg.totals(facts)
root_requests = agg.sum_column(facts, "ROOT_REQUESTS")
child_requests = agg.sum_column(facts, "CHILD_REQUESTS")

window_days = agg.window_days(state.start_date, state.end_date)
credits = totals["CREDITS"]
agents = facts["AGENT_FQN"].nunique()

by_agent = agg.by_dimension(facts, "AGENT_FQN", label_col="AGENT_FQN")
top = agg.leader(by_agent, credits)

if top is not None:
    top_agent, share = top
    per_turn = (child_requests / root_requests) if root_requests else 0.0
    ui.insight_banner(
        f"{top_agent['AGENT_FQN']} leads at {share:.0%} of credits · "
        f"{ui.fmt_int(root_requests)} user turns · "
        f"{per_turn:.2f} child requests per turn.",
    )

cards = ui.standard_cards(
    totals,
    window_days,
    credits_help=ui.HELP_DELTA,
    calls_label="Requests",
    call_noun="request",
    calls_help="Total agent requests, including nested child requests spawned while "
    "servicing a turn. See the root and child breakdown below.",
    trend=agg.daily_trend(facts),
    delta=agg.trend_delta(facts),
)

ui.kpi_cards(
    [
        *cards,
        {
            "label": "Agents",
            "value": ui.fmt_int(agents),
            "help": "Distinct agents by fully-qualified name (database.schema.name).",
            "icon": "smart_toy",
            "color": "gray",
            "caption": f"{ui.fmt_credits(credits / agents)} credits each" if agents else None,
        },
    ]
)
with ui.section("Credits Over Time"):
    ui.trend_chart(agg.daily(facts), y_label="Credits")

with ui.container_row():
    st.metric(
        "Root requests",
        ui.fmt_int(root_requests),
        help="Requests with no parent, one per user turn.",
        border=True,
    )
    st.metric(
        "Child requests",
        ui.fmt_int(child_requests),
        help="Nested requests spawned by an agent while servicing a turn.",
        border=True,
    )
    st.metric(
        "Child per root",
        f"{(child_requests / root_requests):.2f}" if root_requests else "0.00",
        help="Higher ratios mean more internal tool and reasoning steps per user turn.",
        border=True,
    )

with ui.section("Breakdowns"):
    labels = {
        "By agent": "AGENT_FQN",
        "By user": "USER_NAME",
        "By agent tag": "__agent_tag__",
        "By user tag": "__user_tag__",
    }
    choice = st.segmented_control(
        "Group by",
        list(labels.keys()),
        default="By agent",
        key="agent_breakdown",
        bind="query-params",
        label_visibility="collapsed",
    )
    column = labels.get(choice or "By agent", "AGENT_FQN")

    if column == "__agent_tag__":
        ui.breakdown_table(
            agg.by_agent_tag(facts),
            "Agent tag",
            label_col="TAG_LABEL",
            denominator=credits,
            calls_label="Requests",
        )
        st.caption("Tag groups overlap; their shares are not additive.")
    elif column == "__user_tag__":
        ui.breakdown_table(
            agg.by_user_tag(facts, q.load_user_tags()),
            "User tag",
            label_col="TAG_LABEL",
            denominator=credits,
            calls_label="Requests",
        )
        st.caption("Tag groups overlap; their shares are not additive.")
    else:
        frame = agg.by_dimension(facts, column)
        ui.breakdown_table(
            frame, (choice or "By agent").replace("By ", "").capitalize(), calls_label="Requests"
        )

ui.optional_warehouse(q.SURFACE_AGENTS, state.start_date, state.end_date)

with ui.section("Agent Detail"):
    requests = facts.groupby("AGENT_FQN")[["ROOT_REQUESTS", "CHILD_REQUESTS"]].sum().reset_index()
    merged = by_agent.merge(requests, on="AGENT_FQN", how="left")
    ui.breakdown_table(
        merged,
        "Agent (database.schema.name)",
        label_col="AGENT_FQN",
        calls_label="Requests",
        extra_columns={
            "ROOT_REQUESTS": st.column_config.NumberColumn("Root", format="localized"),
            "CHILD_REQUESTS": st.column_config.NumberColumn("Child", format="localized"),
        },
    )
