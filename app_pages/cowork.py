"""Snowflake CoWork usage by agent, service type, instance and user."""

import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_COWORK,
    "CoWork message requests from SNOWFLAKE_COWORK_USAGE_HISTORY.",
    eyebrow="Cortex surface",
)

with st.spinner(f"Loading {q.SURFACE_COWORK} usage..."):
    facts = ui.load_period_usage(q.SURFACE_COWORK, state.start_date, state.end_date)

if _msg := state.get("_degraded", {}).get(q.SURFACE_COWORK):
    st.caption(f":material/warning: {_msg}")

_raw = facts

if facts is None or facts.empty:
    _active = bool(state.get("users") or state.get("tag_users"))
    ui.empty_usage(
        ui.scoped_usage(facts, q.SURFACE_COWORK, unscoped_view=True),
        state=ui.infer_state(_raw, active_filters=_active),
        label="Token credits",
    )
    ui.stop_page()

controls = st.columns(2, vertical_alignment="bottom")
with controls[0]:
    selected_cowork = ui.scope_filter(
        "CoWork instance",
        agg.options(facts, "COWORK_NAME"),
        key="cowork_instances",
        placeholder="All instances",
    )
with controls[1]:
    selected_agents = ui.scope_filter(
        "Agent",
        agg.options(facts, "AGENT_FQN"),
        key="cowork_agents",
        placeholder="All agents",
    )

page_values = {"COWORK_NAME": selected_cowork, "AGENT_FQN": selected_agents}
facts = ui.scoped_usage(facts, q.SURFACE_COWORK, page_values=page_values, unscoped_view=True)

if facts.empty:
    ui.empty_usage(facts, state=ui.LoadState.FILTERED_EMPTY, label="Token credits")
    ui.stop_page()

totals = agg.totals(facts)
window_days = agg.window_days(state.start_date, state.end_date)
credits = totals["CREDITS"]
calls = totals["CALLS"]
users = totals["USERS"]
agents = facts["AGENT_FQN"].nunique()

sql_credits = agg.sum_column(facts, "SQL_QUERY_CREDITS")
ai_fn_credits = agg.sum_column(facts, "AI_FUNCTIONS_CREDITS")

by_agent = agg.by_dimension(facts, "AGENT_FQN", label_col="AGENT_FQN")
top = agg.leader(by_agent, credits)

if top is not None:
    top_agent, share = top
    ui.insight_banner(
        f"{top_agent['AGENT_FQN']} leads at {share:.0%} of token credits · "
        f"{ui.fmt_credits(sql_credits)} credits of warehouse SQL · "
        f"{ui.fmt_int(calls)} requests from {ui.fmt_int(users)} users.",
    )

cards = ui.standard_cards(
    totals,
    window_days,
    credits_label="Token credits",
    credits_help=f"TOKEN_CREDITS, the model inference portion of the CoWork bill. {ui.HELP_DELTA}",
    calls_label="Requests",
    calls_help="One row per CoWork message request. A single request fans out to "
    "one or more agent calls plus any tools it invokes, such as Cortex Analyst "
    "or Cortex Search. See the service split below.",
    call_noun="request",
    trend=agg.daily_trend(facts),
    delta=agg.trend_delta(facts),
)

cards.calls["icon"] = "forum"

ui.kpi_cards(
    [
        cards.credits,
        cards.tokens,
        cards.calls,
        {
            "label": "Agents",
            "value": ui.fmt_int(agents),
            "help": "Distinct agents invoked by CoWork, by fully-qualified name.",
            "icon": "smart_toy",
            "color": "gray",
            "caption": f"{ui.fmt_credits(credits / agents)} credits each" if agents else None,
        },
    ]
)
with ui.section("Credits Over Time"):
    ui.trend_chart(agg.daily(facts), y_label="Token credits")

st.header("CoWork metadata costs")
st.caption(
    "Scope: the request records shown above. Observed SQL metadata may lag by eight hours "
    "and excludes adaptive warehouses. Null metadata does not prove zero usage. These costs "
    "can overlap warehouse attribution and AI Functions totals; do not add them together."
)
with ui.container_row():
    st.metric(
        "Observed SQL metadata credits",
        ui.fmt_credits(sql_credits) if sql_credits else "Unknown / none reported",
    )
    st.metric(
        "Observed AI Functions metadata credits",
        ui.fmt_credits(ai_fn_credits) if ai_fn_credits else "Unknown / none reported",
    )
ui.optional_warehouse(q.SURFACE_COWORK, state.start_date, state.end_date)

service_mix = None
if st.toggle("Service mix detail", key="detail_cowork_mix", bind="query-params"):
    service_mix = ui.load_period_usage(
        f"{q.SURFACE_COWORK} — Service Mix", state.start_date, state.end_date
    )
    service_mix = ui.scoped_usage(service_mix, "CoWork service mix", page_values=page_values)

# The service mix comes from the granular arrays, so it is filtered by date and
# user but not by the agent/instance pickers above, which act on the fact grain.
if service_mix is not None and not service_mix.empty:
    with ui.section("Where CoWork Credits Go"):
        by_service = agg.by_dimension(service_mix, "SERVICE_TYPE", label_col="SERVICE_TYPE")
        ui.breakdown_table(by_service, "Service", label_col="SERVICE_TYPE")
        st.caption(
            "A CoWork turn calls the agent and then whatever tools it needs, so "
            "cortex_agents and cortex_analyst are billed within the same request. "
            "This split reads TOKENS_GRANULAR / CREDITS_GRANULAR, so the agent and "
            "instance pickers above do not narrow it."
        )

    with ui.section("Model Split"):
        by_model = agg.by_dimension(service_mix, "MODEL_NAME", label_col="MODEL_NAME")
        ui.breakdown_table(by_model, "Model", label_col="MODEL_NAME")

    with ui.section("Token Type Mix"):
        ui.token_mix_table(agg.token_mix(service_mix))

with ui.section("Breakdowns"):
    labels = {
        "By agent": "AGENT_FQN",
        "By CoWork instance": "COWORK_NAME",
        "By user": "USER_NAME",
        "By role": "ROLE_NAME",
        "By user tag": "__tag__",
    }
    choice = st.segmented_control(
        "Group by",
        list(labels.keys()),
        default="By agent",
        key="cowork_breakdown",
        bind="query-params",
        label_visibility="collapsed",
    )
    column = labels.get(choice or "By agent", "AGENT_FQN")

    if column == "__tag__":
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
        if column == "USER_NAME" and users == 1:
            st.caption(
                "All CoWork usage in this window is attributed to a single user, so "
                "this breakdown shows one row. Attribution is present, there is just "
                "one user. CoWork requests made through Cortex Agents are attributed on "
                "the Cortex Agents page instead."
            )

with ui.section("Agent Detail"):
    extra = facts.groupby("AGENT_FQN")[["SQL_QUERY_CREDITS", "ROOT_REQUESTS"]].sum().reset_index()
    merged = by_agent.merge(extra, on="AGENT_FQN", how="left")
    st.dataframe(
        merged[["AGENT_FQN", "CREDITS", "SQL_QUERY_CREDITS", "TOKENS", "CALLS", "USERS"]],
        column_config={
            "AGENT_FQN": st.column_config.TextColumn("Agent (database.schema.name)"),
            "CREDITS": st.column_config.NumberColumn("Token credits", format="%.4f"),
            "SQL_QUERY_CREDITS": st.column_config.NumberColumn("SQL credits", format="%.4f"),
            "TOKENS": st.column_config.NumberColumn("Tokens", format="compact"),
            "CALLS": st.column_config.NumberColumn("Requests", format="localized"),
            "USERS": st.column_config.NumberColumn("Users", format="localized"),
        },
        hide_index=True,
        width="stretch",
    )
