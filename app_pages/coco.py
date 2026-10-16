"""CoCo usage from the unified account-level usage view."""

import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_COCO,
    "Token, usage-record and credit consumption across cli, desktop, and snowsight.",
    pills=q.COCO_SUB_SURFACES,
    eyebrow="Cortex surface",
)

with st.spinner(f"Loading {q.SURFACE_COCO} usage..."):
    facts = ui.load_period_usage(q.SURFACE_COCO, state.start_date, state.end_date)

if _msg := state.get("_degraded", {}).get(q.SURFACE_COCO):
    st.caption(f":material/warning: {_msg}")

_raw = facts

if facts is None or facts.empty:
    _active = bool(state.get("users") or state.get("models") or state.get("tag_users"))
    ui.empty_usage(
        ui.scoped_usage(facts, q.SURFACE_COCO),
        state=ui.infer_state(_raw, active_filters=_active),
    )
    ui.stop_page()

controls = st.columns(2, vertical_alignment="bottom")
with controls[0]:
    selected_subs = ui.scope_filter(
        "Surface",
        q.COCO_SUB_SURFACES,
        key="coco_subs",
        placeholder="All surfaces",
    )
with controls[1]:
    selected_roles = ui.scope_filter(
        "Role",
        agg.options(facts, "ROLE_NAME"),
        key="coco_roles",
        placeholder="All roles",
    )

facts = ui.scoped_usage(
    facts,
    q.SURFACE_COCO,
    page_values={"SUB_SURFACE": selected_subs, "ROLE_NAME": selected_roles},
)

if facts.empty:
    ui.empty_usage(facts, state=ui.LoadState.FILTERED_EMPTY)
    ui.stop_page()

totals = agg.totals(facts)

window_days = agg.window_days(state.start_date, state.end_date)
credits = totals["CREDITS"]
users = totals["USERS"]

by_sub = agg.by_dimension(facts, "SUB_SURFACE", label_col="SUB_SURFACE")
top = agg.leader(by_sub, credits)

cards = ui.standard_cards(
    totals,
    window_days,
    credits_help=ui.HELP_DELTA,
    calls_label="Usage records",
    call_noun="usage record",
    calls_help="Model-expanded usage records. A request using multiple models contributes "
    "multiple records; this is not a distinct request or user-prompt count.",
    trend=agg.daily_trend(facts),
    delta=agg.trend_delta(facts),
)

ui.kpi_cards(
    [
        *cards,
        {
            "label": "Active days",
            "value": ui.fmt_int(totals["ACTIVE_DAYS"]),
            "help": ui.HELP_ACTIVE_DAYS,
            "icon": "calendar_month",
            "color": "gray",
            "caption": f"of {window_days} in window",
        },
    ]
)
with ui.section("Credits Over Time By Surface"):
    ui.trend_chart(agg.daily(facts, "SUB_SURFACE"), color_col="SUB_SURFACE", y_label="Credits")

with ui.section("Surface Split"):
    if top is not None:
        top_sub, share = top
        st.caption(f"{top_sub['SUB_SURFACE']} leads at {share:.0%} of observed credits.")
    ui.breakdown_table(by_sub, "Surface", label_col="SUB_SURFACE")

with ui.section("Token Type Mix"):
    ui.token_mix_table(agg.token_mix(facts))
    st.caption(
        "Cache-read tokens dominate agentic coding workloads and bill at a fraction of "
        "input-token rates, which is why credits scale far more slowly than raw tokens."
    )

with ui.section("Breakdowns"):
    labels = {
        "By user": "USER_NAME",
        "By model": "MODEL_NAME",
        "By role": "ROLE_NAME",
        "By inference region": "INFERENCE_REGION",
        "By user tag": "__tag__",
    }
    choice = st.segmented_control(
        "Group by",
        list(labels.keys()),
        default="By user",
        key="coco_breakdown",
        bind="query-params",
        label_visibility="collapsed",
    )
    column = labels.get(choice or "By user", "USER_NAME")

    if column == "__tag__":
        frame = agg.by_user_tag(facts, q.load_user_tags())
        ui.breakdown_table(frame, "User tag", label_col="TAG_LABEL", denominator=credits)
        st.caption(
            "Tags come from current USER-domain tag references, so they reflect each user's "
            "tags today rather than at request time. Users with several tags appear under each."
        )
    else:
        frame = agg.by_dimension(facts, column)
        ui.breakdown_table(frame, (choice or "By user").replace("By ", "").capitalize())

ui.optional_warehouse(q.SURFACE_COCO, state.start_date, state.end_date)

with st.expander("Methodology"):
    st.caption(
        "Unified account-level usage across cli, desktop, and snowsight. Data can arrive up to one hour late and is retained for up to 365 days."
    )
