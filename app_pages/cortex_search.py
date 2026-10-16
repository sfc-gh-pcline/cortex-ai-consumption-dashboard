"""Cortex Search usage with explicit unsupported attribution scope."""

from datetime import timedelta

import pandas as pd
import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_SEARCH,
    "Cortex Search usage: serving, embedding and batch consumption from "
    "CORTEX_SEARCH_*_USAGE_HISTORY.",
    eyebrow="Cortex surface",
)

with st.spinner(f"Loading {q.SURFACE_SEARCH} usage..."):
    daily = ui.load_period_usage(f"{q.SURFACE_SEARCH} — Daily", state.start_date, state.end_date)

if _msg := state.get("_degraded", {}).get(q.SURFACE_SEARCH):
    st.caption(f":material/warning: {_msg}")

with st.container(horizontal=True, vertical_alignment="bottom"):
    selected_services = ui.scope_filter(
        "Service", agg.options(daily, "SERVICE_NAME"), key="search_services"
    )
    selected_types = ui.scope_filter(
        "Consumption type", agg.options(daily, "CONSUMPTION_TYPE"), key="search_types"
    )
search_filters = {"SERVICE_NAME": selected_services, "CONSUMPTION_TYPE": selected_types}
daily = ui.scoped_usage(daily, q.SURFACE_SEARCH, page_values=search_filters, unscoped_view=True)

if daily is None or daily.empty:
    ui.empty_usage(
        daily,
        state=ui.infer_state(daily),
    )
    ui.stop_page()

totals = agg.totals(daily)
window_days = agg.window_days(state.start_date, state.end_date)
credits = totals["CREDITS"]

by_consumption = agg.by_dimension(daily, "CONSUMPTION_TYPE", label_col="CONSUMPTION_TYPE")
top = agg.leader(by_consumption, credits)

if top is not None:
    top_type, share = top
    ui.insight_banner(
        f"{top_type['CONSUMPTION_TYPE']} leads at {share:.0%} of credits · "
        f"{daily['SERVICE_NAME'].nunique()} distinct services.",
    )

cards = ui.standard_cards(
    totals,
    window_days,
    credits_help="SERVING + EMBED_TEXT_TOKENS + BATCH credits, from CORTEX_SEARCH_DAILY_USAGE_HISTORY. "
    f"{ui.HELP_DELTA}",
    trend=agg.daily_trend(daily),
    delta=agg.trend_delta(daily),
)
cards.tokens["caption"] = "Embedding tokens only"
# No Users card: Cortex Search usage has no USER_NAME dimension at all.
ui.kpi_cards(
    [
        cards.credits,
        cards.tokens,
        {"label": "Services", "value": ui.fmt_int(daily["SERVICE_NAME"].nunique())},
    ]
)
with ui.section("Credits Over Time"):
    ui.trend_chart(agg.daily(daily), y_label="Credits")

with ui.section("By Consumption Type"):
    ui.breakdown_table(by_consumption, "Consumption type", label_col="CONSUMPTION_TYPE")
    st.caption(
        "SERVING is query-time search credits; EMBED_TEXT_TOKENS is embedding-model "
        "credits for indexing and re-indexing text; BATCH covers batch search "
        "batch queries against a search service, broken out below."
    )

ui.optional_warehouse(q.SURFACE_SEARCH, state.start_date, state.end_date)

if not st.toggle("Serving and batch detail", key="detail_search", bind="query-params"):
    periods = state.get("periods")
    today = state.get("utc_today")
    if (
        periods is not None
        and today is not None
        and (
            periods.partial_today is not None
            or (
                state.get("applied_range") != "Custom"
                and state.end_date == today - timedelta(days=1)
            )
        )
    ):
        with st.expander("Today so far (partial)"):
            ui.today_section(q.SURFACE_SEARCH, today)
    ui.stop_page()
serving = ui.load_period_usage(f"{q.SURFACE_SEARCH} — Serving", state.start_date, state.end_date)
batch = ui.load_period_usage(f"{q.SURFACE_SEARCH} — Batch", state.start_date, state.end_date)
serving = ui.scoped_usage(serving, q.SURFACE_SEARCH, page_values=search_filters, unscoped_view=True)
batch = ui.scoped_usage(batch, q.SURFACE_SEARCH, page_values=search_filters, unscoped_view=True)
wh_result = ui.load_period_warehouse(state.start_date, state.end_date)

with ui.section("Hourly Serving Trend"):
    if serving is None or serving.empty:
        ui.empty_state("No serving usage in this window.", state=ui.infer_state(serving))
    else:
        hourly = (
            serving.groupby("USAGE_HOUR")[["CREDITS", "CALLS"]]
            .sum()
            .reset_index()
            .sort_values("USAGE_HOUR")
        )
        hourly = hourly.set_index("USAGE_HOUR")["CREDITS"]
        hourly.index = hourly.index.tz_localize(None) if hourly.index.tz else hourly.index
        hours = pd.date_range(
            state.start_date, pd.Timestamp(state.end_date) + pd.Timedelta(hours=23), freq="h"
        )
        hourly_data = hourly.reindex(hours).rename_axis("USAGE_HOUR").reset_index()
        hourly_data.attrs = {}
        mode = st.segmented_control(
            "Display",
            ["Chart", "Table"],
            default="Chart",
            key="chart_cortex_search_hourly",
            bind="query-params",
        )
        if mode == "Table":
            st.dataframe(
                hourly_data,
                hide_index=True,
                column_config={"CREDITS": st.column_config.NumberColumn("Credits", format="%.8f")},
            )
        else:
            spec = ui.trend_spec("CREDITS", None, "Credits")
            spec["encoding"]["x"] = {
                "field": "USAGE_HOUR",
                "type": "temporal",
                "axis": {"format": "%b %d %H:%M", "tickCount": 8},
            }
            spec["encoding"]["tooltip"][0] = {
                "field": "USAGE_HOUR",
                "type": "temporal",
                "format": "%Y-%m-%d %H:%M UTC",
            }
            st.vega_lite_chart(hourly_data, spec, width="stretch")
        by_service = agg.by_dimension(serving, "SERVICE_NAME", label_col="SERVICE_NAME")
        ui.breakdown_table(by_service, "Service", label_col="SERVICE_NAME")

with ui.section("Batch Detail"):
    if batch is None or batch.empty:
        ui.empty_state(
            "No batch query usage in this window. Batch covers batch search "
            "queries against a search service, tracked by QUERY_ID.",
            state=ui.infer_state(batch),
            icon=":material/inbox:",
        )
    else:
        # Aggregate to one row per QUERY_ID before joining warehouse credits, so a
        # query with several CONSUMPTION_TYPE/MODEL_NAME detail rows gets its
        # warehouse-credit allocation exactly once, not once per detail row
        # (batch grain invariant, §5.3/§7/AC7).
        per_query = (
            batch.groupby("QUERY_ID")
            .agg(
                USAGE_DATE=("USAGE_DATE", "min"),
                SERVICE_NAME=("SERVICE_NAME", "first"),
                CREDITS=("CREDITS", "sum"),
                TOKENS=("TOKENS", "sum"),
            )
            .reset_index()
        )
        wh_frame = wh_result.frame
        if state.get("periods") is not None:
            wh_frame = agg.period_slice(wh_frame, state.periods.current)
        if wh_frame is not None and not wh_frame.empty:
            wh_by_query = (
                wh_frame[wh_frame["SURFACE"] == q.SURFACE_SEARCH]
                .groupby("QUERY_ID")["WAREHOUSE_CREDITS"]
                .sum()
                .reset_index()
            )
            per_query = per_query.merge(wh_by_query, on="QUERY_ID", how="left")
        if "WAREHOUSE_CREDITS" not in per_query.columns:
            per_query["WAREHOUSE_CREDITS"] = float("nan")

        st.dataframe(
            per_query.sort_values("USAGE_DATE", ascending=False),
            column_config={
                "QUERY_ID": st.column_config.TextColumn("Query ID"),
                "USAGE_DATE": st.column_config.DateColumn("Date"),
                "SERVICE_NAME": st.column_config.TextColumn("Service"),
                "CREDITS": st.column_config.NumberColumn("Batch credits", format="%.6f"),
                "TOKENS": st.column_config.NumberColumn("Tokens", format="compact"),
                "WAREHOUSE_CREDITS": st.column_config.NumberColumn(
                    "Warehouse credits", format="%.6f"
                ),
            },
            hide_index=True,
            width="stretch",
        )
        if wh_frame is None or wh_frame.empty:
            st.caption(
                "Missing warehouse attribution is blank, not zero. See the Warehouse "
                "Attribution page for account-wide degradation status."
            )

with ui.section("Index-Refresh Warehouse Cost"):
    st.info(
        "Index-refresh warehouse cost is not attributed by this dashboard. "
        "The implemented join covers batch-query IDs, not index-refresh warehouse work.",
        icon=":material/info:",
    )
