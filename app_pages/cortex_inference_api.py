"""Cortex Inference API usage, with credits estimated from observed model rates."""

import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_REST_API,
    "Direct inference calls from CORTEX_REST_API_USAGE_HISTORY.",
    eyebrow="Cortex surface",
)

st.info(
    "Credits are estimated from observed per-model rates and cannot be pre-aggregated. "
    "This page always reads live data from ACCOUNT_USAGE, not from an aggregate snapshot.",
    icon=":material/info:",
)

with st.spinner(f"Loading {q.SURFACE_REST_API} usage..."):
    facts = ui.load_period_usage(q.SURFACE_REST_API, state.start_date, state.end_date)

_raw = facts

if facts is None or facts.empty:
    _active = bool(state.get("users") or state.get("models") or state.get("tag_users"))
    ui.empty_usage(
        ui.scoped_usage(facts, q.SURFACE_REST_API),
        state=ui.infer_state(_raw, active_filters=_active),
        label="Credits (est.)",
    )
    ui.stop_page()

controls = st.columns(2, vertical_alignment="bottom")
with controls[0]:
    selected_regions = ui.scope_filter(
        "Inference region",
        agg.options(facts, "INFERENCE_REGION"),
        key="rest_regions",
        placeholder="All regions",
    )
with controls[1]:
    selected_tags = ui.scope_filter(
        "Query tag",
        agg.options(facts, "QUERY_TAG"),
        key="rest_tags",
        placeholder="All query tags",
    )

facts = ui.scoped_usage(
    facts,
    q.SURFACE_REST_API,
    page_values={"INFERENCE_REGION": selected_regions, "QUERY_TAG": selected_tags},
)

if facts.empty:
    ui.empty_usage(facts, state=ui.LoadState.FILTERED_EMPTY, label="Credits (est.)")
    ui.stop_page()

totals = agg.totals(facts)
unrated = agg.sum_column(facts, "UNRATED_TOKENS")
rate_known = "UNRATED_TOKENS" in facts and facts["UNRATED_TOKENS"].notna().all()
tokens = totals["TOKENS"]

window_days = agg.window_days(state.start_date, state.end_date)
credits = totals["CREDITS"]
models = facts["MODEL_NAME"].nunique()
coverage = (1 - (unrated / tokens)) if tokens and rate_known else None
coverage_label = ui.inference_pricing_label(unrated if rate_known else None)
estimate_incomplete = not rate_known or unrated > 0
if estimate_incomplete:
    st.warning(ui.inference_pricing_warning(unrated if rate_known else None))

by_model = agg.by_dimension(facts, "MODEL_NAME", label_col="MODEL_NAME")
top = agg.leader(by_model, credits)

if top is not None:
    top_model, share = top
    ui.insight_banner(
        f"{top_model['MODEL_NAME']} leads at {share:.0%} of estimated credits · "
        f"{ui.fmt_tokens(tokens)} tokens · "
        f"{coverage_label}.",
    )

cards = ui.standard_cards(
    totals,
    window_days,
    credits_label="Credits (est.)",
    calls_label="Requests",
    call_noun="request",
    credits_help="Estimated: this view reports tokens but no credits. See the note below. "
    f"{ui.HELP_DELTA}",
    calls_help="One row per REST inference request to the Cortex Inference API.",
    trend=agg.daily_trend(facts),
    delta=agg.trend_delta(facts),
)

if estimate_incomplete:
    cards.credits["label"] = "Credits (partial estimate)"
    if coverage == 0:
        cards.credits["value"] = "Unavailable"

ui.kpi_cards(
    [
        *cards,
        {
            "label": "Models",
            "value": ui.fmt_int(models),
            "help": "Distinct models invoked. Tokens without a matching observed rate still "
            "count toward usage, but their credits are excluded from the estimate, not free.",
            "icon": "network_node",
            "color": "gray",
            "caption": coverage_label,
        },
    ]
)

st.info(
    "**Credits on this page are estimated.** `CORTEX_REST_API_USAGE_HISTORY` reports token "
    "counts but no credit column, so credits are derived by applying per-model, per-token-type "
    "rates observed in CoCo history, where both tokens and credits are reported. Those "
    "observed rates are an estimate basis, not a billing guarantee. "
    f"**{coverage_label}.** Coverage checks each reported token type against the same "
    "rate snapshot used for both periods. A model/token-type rate may be absent from the "
    "observed CoCo history, or the usage record may lack a token-type breakdown. "
    "The dashboard cannot price those tokens or determine their missing credit amount.",
    icon=":material/functions:",
)

if snapshot := facts.attrs.get("rate_snapshot_at"):
    st.caption(f"Observed rate snapshot fetched at {snapshot.isoformat()}; shared by both periods.")

with ui.section("Estimated Credits Over Time"):
    ui.trend_chart(agg.daily(facts), y_label="Credits (est.)")

with ui.section("Token Type Mix"):
    ui.token_mix_table(agg.token_mix(facts))

with ui.section("Breakdowns"):
    labels = {
        "By model": "MODEL_NAME",
        "By user": "USER_NAME",
        "By region": "INFERENCE_REGION",
        "By query tag": "QUERY_TAG",
        "By user tag": "__tag__",
    }
    choice = st.segmented_control(
        "Group by",
        list(labels.keys()),
        default="By model",
        key="rest_breakdown",
        bind="query-params",
        label_visibility="collapsed",
    )
    column = labels.get(choice or "By model", "MODEL_NAME")

    if column == "__tag__":
        frame = agg.by_user_tag(facts, q.load_user_tags())
        ui.breakdown_table(
            frame, "User tag", label_col="TAG_LABEL", denominator=credits, calls_label="Requests"
        )
        st.caption("Tag groups overlap; their shares are not additive.")
    else:
        frame = agg.by_dimension(facts, column)
        ui.breakdown_table(
            frame, (choice or "By model").replace("By ", "").capitalize(), calls_label="Requests"
        )
