"""Reference SQL for every AI surface, rendered from the builders the app runs."""

import streamlit as st

from utils import queries as q
from utils import ui

state = st.session_state

ui.page_header(
    "Reference SQL",
    "The baseline ACCOUNT_USAGE queries behind every page, for the selected window.",
    eyebrow="Documentation",
)

st.info(
    "These are the queries the dashboard runs when query acceleration is **not** enabled, "
    "read directly from `SNOWFLAKE.ACCOUNT_USAGE`. They are generated from the same builders "
    "the dashboard executes, so they cannot drift from what produced the numbers. Bound date "
    "parameters are substituted inline, so each one can be pasted into a worksheet and run "
    "as-is. All timestamps are bucketed on **UTC** calendar days via `CONVERT_TIMEZONE`, "
    "because these views mix `TIMESTAMP_TZ` and `TIMESTAMP_LTZ` columns. When acceleration is "
    "on, see the **Query Acceleration SQL** tab for the scheduled-table reads that replace "
    "these.",
    icon=":material/code:",
)

st.caption(
    f":material/calendar_month: Window: {state.start_date:%b %d, %Y} to "
    f"{state.end_date:%b %d, %Y}  ·  change it in the sidebar to regenerate these."
)


def render(entry: q.SqlReference) -> None:
    """Render one baseline statement with its note and source views.

    Args:
        entry: The reference entry to show.
    """
    st.markdown(f":gray[{entry.note}]")
    st.markdown("**Source views**  \n" + "  \n".join(f"`{source}`" for source in entry.sources))
    sql, params = entry.builder(state.start_date, state.end_date)
    st.code(q.inline_params(sql, params), language="sql", line_numbers=True)


st.markdown("**Core surface queries**")
groups = q.sql_reference_groups()
group = st.segmented_control(
    "Surface",
    groups,
    default=groups[0],
    key="sql_group",
    label_visibility="collapsed",
)

if group:
    entries = q.sql_reference_for(group)
    entry = entries[0]

    # Only offer the second level where there is a choice to make, so single
    # statement groups stay one click deep.
    if len(entries) > 1:
        variants = [e.variant for e in entries]
        variant = st.segmented_control(
            f"{group} statement",
            variants,
            default=variants[0],
            key=f"sql_variant_{group}",
            label_visibility="collapsed",
        )
        entry = next((e for e in entries if e.variant == variant), entries[0])

    heading = entry.label if entry.kind else f"{group} · {entry.variant}"
    with ui.section(heading):
        if entry.kind:
            st.caption(f":material/label: {entry.kind}")
        render(entry)

shared = q.sql_reference_for(q.SHARED)
with st.expander(f"Shared building blocks ({len(shared)})"):
    st.caption(
        "Statements that back more than one page. They are run once and their "
        "results reused, rather than repeated inside each surface's query."
    )
    for entry in shared:
        with ui.section(entry.label):
            st.caption(f":material/label: {entry.kind}")
            render(entry)

with ui.section("How Credits Are Derived"):
    st.markdown(
        """
| Surface | Credit source | Exact or estimated |
| --- | --- | --- |
| CoCo | `TOKEN_CREDITS` | Exact, reported by Snowflake |
| Cortex AI Functions | `CREDITS` | Exact, reported by Snowflake |
| Cortex Inference API | derived from tokens × observed model rates | **Estimated** |
| Cortex Agents | `TOKEN_CREDITS` | Exact, reported by Snowflake |
| CoWork | `TOKEN_CREDITS` plus `METADATA:sql_query_credits` and `METADATA:ai_functions_credits` | Exact, reported by Snowflake |
"""
    )
    st.caption(
        "Cortex Agents and CoWork do not overlap: per Snowflake's docs, "
        "requests originating from Cortex Agents are excluded from "
        "SNOWFLAKE_COWORK_USAGE_HISTORY and recorded in CORTEX_AGENT_USAGE_HISTORY, "
        "so the two can be summed without double counting."
    )
