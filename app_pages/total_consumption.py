"""Six-source overview with separate estimated and non-attributable credits."""

import streamlit as st

from utils import agg, ui
from utils import queries as q
from utils.view_state import FILTER_CAPABILITIES, PAGES

state = st.session_state

ui.page_header(
    "AI Consumption Overview",
    "Reported AI credits and Inference API estimates for six supported services.",
    pills=q.SURFACES,
    eyebrow="Account overview",
)

with st.expander("Coverage and methodology"):
    st.markdown(
        "Selected ACCOUNT_USAGE sources, not an invoice-wide total. "
        "Source latency varies; completed calendar days may still be incomplete.\n\n"
        f"**Included services:** "
        f"{q.SURFACE_COCO} · {q.SURFACE_COWORK} · {q.SURFACE_AGENTS} · "
        f"{q.SURFACE_SEARCH} · {q.SURFACE_REST_API} · {q.SURFACE_AI_FUNCTIONS}\n\n"
        f"**Two things to know before reading numbers:**  \n"
        f"- **{q.SURFACE_REST_API} credits are estimated.** That view reports tokens only, "
        f"so credits are derived from per-model token rates.  \n"
        f"- **{q.SURFACE_SEARCH} whole-service credits lack user/model attribution.** "
        "Non-attributable records are separate when filters are active.\n\n"
        "Excludes warehouse compute, storage, data transfer and AI billing categories not "
        "represented by these six sources, including standalone Analyst, fine-tuning and "
        "provisioned throughput. Embedded tool costs may already be included in agent credits. "
        "Reference SQL shows baseline queries before in-memory filters."
    )

with st.spinner("Loading usage across all surfaces..."):
    facts = ui.load_period_usage(q.PAGE_TOTAL_CONSUMPTION, state.start_date, state.end_date)

_raw = facts
unavailable = facts.attrs.get("unavailable_sources", ())
if unavailable:
    st.warning(
        "Partial source coverage: unavailable "
        + ", ".join(unavailable)
        + ". Totals exclude these sources."
    )
else:
    st.caption(
        "Six sources read. Ingestion completeness is not certified. Warehouse compute is excluded."
    )
facts = ui.scoped_usage(facts)

if facts is None or facts.empty:
    _active = bool(state.get("users") or state.get("models") or state.get("tag_users"))
    ui.empty_usage(
        facts,
        label="AI usage credits",
        state=ui.LoadState.SOURCE_UNAVAILABLE
        if unavailable
        else ui.infer_state(_raw, active_filters=_active),
    )
    ui.stop_page()

totals = agg.totals(facts)

credits = totals["CREDITS"]
users = totals["USERS"]

by_surface = agg.by_dimension(facts, "SURFACE", label_col="SURFACE")
estimated = float(facts.loc[facts["CREDITS_ESTIMATED"], "CREDITS"].sum())
reported = credits - estimated
incomplete_rates = "RATE_INCOMPLETE" in facts and facts["RATE_INCOMPLETE"].any()
if incomplete_rates:
    inference = facts.loc[facts["CREDITS_ESTIMATED"]]
    rate_known = "UNRATED_TOKENS" in inference and inference["UNRATED_TOKENS"].notna().all()
    unrated = agg.sum_column(inference, "UNRATED_TOKENS") if rate_known else None
    st.warning(ui.inference_pricing_warning(unrated, overview=True))

ui.kpi_cards(
    [
        {
            "label": "AI usage credits" + (" (partial)" if unavailable or incomplete_rates else ""),
            "value": ui.fmt_credits(credits),
            "delta": agg.trend_delta(facts),
        },
        {"label": "Reported credits", "value": ui.fmt_credits(reported)},
        {
            "label": "Estimated credits" + (" (partial)" if incomplete_rates else ""),
            "value": "Unavailable"
            if incomplete_rates and estimated == 0
            else ui.fmt_credits(estimated),
        },
        {
            "label": "Attributed users",
            "value": ui.fmt_int(users),
            "help": "Identified users in matching facts; missing user attribution is excluded.",
        },
    ]
)
st.caption(
    f"{ui.fmt_tokens(totals['TOKENS'])} reported tokens; token coverage differs by service. "
    "Search serving and non-token-metered functions do not report comparable token work."
)

with ui.section("Credits Over Time By Surface"):
    ui.trend_chart(agg.daily(facts, "SURFACE"), color_col="SURFACE", y_label="Credits")

with ui.section("Surface Comparison"):
    table = by_surface.drop(columns="CALLS")
    ui.breakdown_table(
        table,
        "Surface",
        label_col="SURFACE",
        chart_key="total_consumption_services",
    )
    st.caption(
        f"{q.SURFACE_REST_API} credits are estimated from observed per-model token rates. "
        "Every other surface reports credits directly from Snowflake."
    )
    selected_service = st.selectbox(
        "Investigate service", q.SURFACES, key="overview_service", bind="query-params"
    )
    dimensions = ["Service only"]
    capabilities = FILTER_CAPABILITIES.get(selected_service, ())
    dimensions += [
        label
        for label, column in (("User", "USER_NAME"), ("Model", "MODEL_NAME"))
        if column in capabilities
    ]
    dimension = st.selectbox(
        "Investigate dimension", dimensions, key="overview_dimension", bind="query-params"
    )
    driver = None
    if dimension != "Service only":
        column = "USER_NAME" if dimension == "User" else "MODEL_NAME"
        choices = agg.options(facts.loc[facts["SURFACE"].eq(selected_service)], column)
        driver = st.selectbox(
            "Driver value",
            choices,
            index=None,
            key=f"overview_driver_{column}",
            bind="query-params",
        )
    target = next(item for item in PAGES if item.dependency_key == selected_service)
    st.link_button(
        "Open service",
        ui.driver_url(target.url_path, dimension, driver),
        icon=":material/arrow_forward:",
        disabled=dimension != "Service only" and driver is None,
    )

with ui.section("CoCo Surface Split"):
    coco = facts[facts["SURFACE"] == q.SURFACE_COCO]
    if coco.empty:
        ui.empty_state("No CoCo usage in this window.", state=ui.LoadState.NO_USAGE)
    else:
        by_sub = agg.by_dimension(coco, "SUB_SURFACE", label_col="SUB_SURFACE")
        ui.breakdown_table(by_sub, "Surface", label_col="SUB_SURFACE")

with ui.section("Top Users Across All Surfaces"):
    per_user = agg.by_dimension(facts, "USER_NAME", label_col="USER_NAME")
    pivot = (
        facts.pivot_table(index="USER_NAME", columns="SURFACE", values="CREDITS", aggfunc="sum")
        .fillna(0.0)
        .reset_index()
    )
    # USERS is a distinct-user count, so it is always 1 on a per-user grain.
    merged = per_user.drop(columns=["USERS", "CALLS"]).merge(pivot, on="USER_NAME", how="left")

    ui.breakdown_table(
        merged,
        "User",
        label_col="USER_NAME",
        limit=25,
        denominator=credits,
        extra_columns={
            surface: st.column_config.NumberColumn(surface, format="%.2f") for surface in q.SURFACES
        },
    )

with ui.section("Top Models Across All Surfaces"):
    models = agg.by_dimension(
        facts[facts["MODEL_NAME"].notna()], "MODEL_NAME", label_col="MODEL_NAME"
    )
    ui.breakdown_table(
        models.drop(columns="CALLS"), "Model", label_col="MODEL_NAME", limit=20, denominator=credits
    )
    st.caption("Models cover attributable records only; shares use all matching AI usage credits.")
