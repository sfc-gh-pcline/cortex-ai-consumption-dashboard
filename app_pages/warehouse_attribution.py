"""Cross-surface Snowflake warehouse compute cost attributed to Cortex usage.

Reads the shared `warehouse_credit_facts` (utils.queries.load_warehouse_credit_facts),
which already restricts to Cortex-attributable rows only (a QUERY_TAG match, or a
QUERY_ID present in a Cortex usage view) -- see the module docstring on
utils/queries.py for the full de-duplication and inclusion-predicate design. Every
consumer on this page reads the fact without an extra SURFACE exclusion filter: the
fact's scope is already Cortex-only, so the headline total is exactly
SUM(WAREHOUSE_CREDITS) over every fact row.
"""

import streamlit as st

from utils import agg, ui
from utils import queries as q

state = st.session_state

ui.page_header(
    q.SURFACE_WAREHOUSE_ATTRIBUTION,
    "Snowflake virtual-warehouse compute credits attributed to Cortex surfaces, "
    "from QUERY_ATTRIBUTION_HISTORY and QUERY_METERING_HISTORY.",
    eyebrow="Cross-surface",
)

if notice := ui.ai_functions_warehouse_coverage_notice(state.start_date, state.end_date):
    st.warning(notice, icon=":material/warning:")

with st.spinner("Loading warehouse attribution..."):
    wh_result = ui.load_period_warehouse(state.start_date, state.end_date)

with st.container(horizontal=True, vertical_alignment="bottom"):
    selected_surfaces = ui.scope_filter(
        "Service", agg.options(wh_result.frame, "SURFACE"), key="warehouse_surfaces"
    )
    selected_warehouses = ui.scope_filter(
        "Warehouse", agg.options(wh_result.frame, "WAREHOUSE_NAME"), key="warehouse_names"
    )
    selected_tiers = ui.scope_filter(
        "Attribution tier", agg.options(wh_result.frame, "TIER"), key="warehouse_tiers"
    )
facts = ui.scoped_usage(
    wh_result.frame,
    q.SURFACE_WAREHOUSE_ATTRIBUTION,
    unscoped_view=True,
    metric="WAREHOUSE_CREDITS",
    page_values={
        "SURFACE": selected_surfaces,
        "WAREHOUSE_NAME": selected_warehouses,
        "TIER": selected_tiers,
    },
)

# This fact is metered in warehouse credits, not the Cortex token credits the rest of
# the app calls CREDITS, so the breakdowns are built on their own metric columns and
# rendered as extra columns. Order matters: the first entry drives sort and share.
WAREHOUSE_METRICS = ["WAREHOUSE_CREDITS", "COMPUTE_CREDITS", "CLOUD_SERVICES_CREDITS"]
WAREHOUSE_COLUMNS = {
    "WAREHOUSE_CREDITS": st.column_config.NumberColumn("Total credits", format="%.6f"),
    "COMPUTE_CREDITS": st.column_config.NumberColumn("Compute", format="%.6f"),
    "CLOUD_SERVICES_CREDITS": st.column_config.NumberColumn("Cloud services", format="%.6f"),
}

if wh_result.degraded:
    st.warning(wh_result.message, icon=":material/warning:")

if facts is None or facts.empty:
    ui.empty_usage(
        facts,
        state=ui.infer_state(facts),
        label="Warehouse credits",
        metric="WAREHOUSE_CREDITS",
    )
    ui.stop_page()

window_days = agg.window_days(state.start_date, state.end_date)
total_credits = float(facts["WAREHOUSE_CREDITS"].sum())
compute_credits = float(facts["COMPUTE_CREDITS"].sum())
cloud_services_credits = float(facts["CLOUD_SERVICES_CREDITS"].sum())
per_request_credits = float(facts.loc[facts["TIER"] == "per_request", "WAREHOUSE_CREDITS"].sum())
per_request_share = per_request_credits / total_credits if total_credits else 0.0

by_surface = agg.by_dimension(facts, "SURFACE", label_col="SURFACE", metrics=WAREHOUSE_METRICS)
top = agg.leader(by_surface, total_credits, value_col="WAREHOUSE_CREDITS")

if top is not None:
    top_surface, share = top
    ui.insight_banner(
        f"{top_surface['SURFACE']} leads at {share:.0%} of credits · "
        f"{per_request_share:.0%} is per-request attributable.",
    )

ui.kpi_cards(
    [
        {
            "label": "Warehouse credits",
            "value": ui.fmt_credits(total_credits),
            "delta": agg.trend_delta(facts, "WAREHOUSE_CREDITS"),
            "help": "COMPUTE_CREDITS + CLOUD_SERVICES_CREDITS, summed over every "
            "Cortex-attributable row. This is the headline total; see the caveat below "
            "on why it isn't a uniform metric across surfaces.",
            "icon": "toll",
            "color": "blue",
            "caption": f"{ui.fmt_credits(total_credits / window_days)} observed / day ({window_days} days)",
        },
        {
            "label": "Compute credits",
            "value": ui.fmt_credits(compute_credits),
            "help": "Warehouse compute portion only, from both source views.",
            "icon": "memory",
            "color": "violet",
        },
        {
            "label": "Cloud services credits",
            "value": ui.fmt_credits(cloud_services_credits),
            "help": "Always 0 for QUERY_ATTRIBUTION_HISTORY rows (that view excludes cloud "
            "services); the true value for QUERY_METERING_HISTORY (Adaptive warehouse) rows.",
            "icon": "cloud",
            "color": "gray",
        },
        {
            "label": "Per-request attributable",
            "value": f"{per_request_share:.0%}",
            "help": "Share of warehouse credits carrying a REQUEST_ID, so they attribute to a "
            "single user turn rather than a bare surface total.",
            "icon": "percent",
            "color": "green",
        },
    ]
)

st.caption(
    "**Headline metric caveat:** WAREHOUSE_CREDITS = COMPUTE_CREDITS + "
    "CLOUD_SERVICES_CREDITS, and CLOUD_SERVICES_CREDITS is 0 by construction for "
    "standard-warehouse (QUERY_ATTRIBUTION_HISTORY) rows. Surfaces running entirely on "
    "standard warehouses therefore show compute-only credits, while surfaces on "
    "Adaptive warehouses show compute plus cloud-services, so the two are not a uniform "
    "like-for-like comparison. See the compute/cloud-services split above."
)

with ui.section("Attributed Credits Over Time"):
    trend = (
        agg.calendar_daily(
            facts,
            facts.attrs["display_window"],
            facts.attrs.get("source_coverages", ()),
            "WAREHOUSE_CREDITS",
        )
        if facts.attrs.get("display_window")
        else facts.groupby("USAGE_DATE", as_index=False)["WAREHOUSE_CREDITS"].sum(min_count=1)
    )
    if previous := facts.attrs.get("previous_daily"):
        trend.attrs["previous_daily"] = previous
    ui.trend_chart(trend, value_col="WAREHOUSE_CREDITS", y_label="Attributed warehouse credits")

with ui.section("By Surface"):
    ui.breakdown_table(
        by_surface,
        "Surface",
        label_col="SURFACE",
        share_of="WAREHOUSE_CREDITS",
        extra_columns=WAREHOUSE_COLUMNS,
    )

with ui.section("By Warehouse"):
    by_warehouse = agg.by_dimension(
        facts.rename(columns={"WAREHOUSE_NAME": "__wh__"}),
        "__wh__",
        label_col="WAREHOUSE_NAME",
        metrics=WAREHOUSE_METRICS,
    )
    ui.breakdown_table(
        by_warehouse,
        "Warehouse",
        label_col="WAREHOUSE_NAME",
        share_of="WAREHOUSE_CREDITS",
        extra_columns=WAREHOUSE_COLUMNS,
    )

with ui.section("Tier Split"):
    by_tier = agg.by_dimension(facts, "TIER", label_col="TIER", metrics=WAREHOUSE_METRICS)
    ui.breakdown_table(
        by_tier,
        "Tier",
        label_col="TIER",
        share_of="WAREHOUSE_CREDITS",
        extra_columns=WAREHOUSE_COLUMNS,
    )
    st.caption(
        "**per_request** rows carry a request/query identifier; **surface** rows have "
        "service-level tags only. Neither tier is narrowed by user, model, user-tag or "
        "agent selections. Request identity alone does not establish that attribution."
    )

with ui.section("Detail"):
    detail_columns = [
        "USAGE_DATE",
        "SURFACE",
        "TIER",
        "WAREHOUSE_NAME",
        "SOURCE_VIEW",
        "QUERY_ID",
        "REQUEST_ID",
        "COMPUTE_CREDITS",
        "CLOUD_SERVICES_CREDITS",
        "WAREHOUSE_CREDITS",
    ]
    detail = facts[detail_columns].sort_values("WAREHOUSE_CREDITS", ascending=False)
    detail.attrs = {}
    st.dataframe(
        detail,
        column_config={
            "USAGE_DATE": st.column_config.DateColumn("Date"),
            "COMPUTE_CREDITS": st.column_config.NumberColumn("Compute", format="%.6f"),
            "CLOUD_SERVICES_CREDITS": st.column_config.NumberColumn(
                "Cloud services", format="%.6f"
            ),
            "WAREHOUSE_CREDITS": st.column_config.NumberColumn("Total", format="%.6f"),
        },
        hide_index=True,
        width="stretch",
    )
