"""Query Acceleration SQL: how the aggregate layer is built, read, and operated.

Educational reference. Every statement is generated from the same builders the
Acceleration Settings page and sql/config.sql use, so nothing here can drift from
what the app provisions. This page issues no data queries of its own.
"""

import streamlit as st

from utils import queries as q
from utils import ui
from utils.config import load_settings, source_for

state = st.session_state

ui.page_header(
    q.PAGE_QUERY_ACCELERATION_SQL,
    "The SQL that creates, refreshes, reads, and operates the aggregate tables "
    "behind query acceleration. Learn the pattern, then adapt it.",
    eyebrow="Documentation",
)

st.info(
    "Acceleration pre-materializes the `ACCOUNT_USAGE` views the dashboard reads into scheduled "
    "aggregate tables, so pages avoid cold-start scans. There are three source classes:\n\n"
    "- **Windowed aggregate** tables are served through the **prior UTC day** (today's mutable "
    "rows stay on the live source).\n"
    "- The **global** Model Rates aggregate is computed over a fixed 90-day window, not the "
    "dashboard date range.\n"
    "- **Cortex Inference API** is never materialized. It always reads live `ACCOUNT_USAGE` "
    "because its per-model rate literals require live data.\n\n"
    "Provision all of this from SQL with `sql/config.sql`, or from **Acceleration Settings**.",
    icon=":material/bolt:",
)

settings = load_settings()

_LABELS = {entry.surface_key: entry for entry in q.ACCELERATION_SQL_REFERENCE}
surface_key = st.segmented_control(
    "Surface",
    list(_LABELS.keys()),
    default=next(iter(_LABELS)),
    key="accel_surface",
    label_visibility="collapsed",
)

if not surface_key:
    st.stop()

entry = _LABELS[surface_key]
object_label = (
    "Global aggregate" if entry.object_type == "GLOBAL_AGGREGATE" else "Windowed aggregate"
)

st.markdown(f"**{surface_key}** · :blue-badge[{object_label}]")
st.markdown(f":gray[{entry.explanation}]")
st.markdown("**Source views**  \n" + "  \n".join(f"`{source}`" for source in entry.source_views))

# What the dashboard reads now, resolved through the live source router.
_reference = next((e for e in q.SQL_REFERENCE if e.runtime_key == surface_key), None)
if _reference is not None:
    statement = q.reference_statement_for(
        _reference, state.start_date, state.end_date, settings, today=q.utc_today()
    )
    is_dt = source_for(surface_key, settings) == "dt"
    with ui.section("What the dashboard reads now"):
        if is_dt and statement.served_end_date is not None:
            st.caption(
                f":material/bolt: Reading the scheduled table through "
                f":blue[{statement.served_end_date:%b %d, %Y}] (the prior UTC day)."
            )
        elif is_dt:
            st.caption(":material/bolt: Reading the global aggregate table.")
        else:
            st.caption(
                ":material/database: Acceleration is not active for this surface yet, so the "
                "dashboard reads live `ACCOUNT_USAGE`. Provision and enable it to serve from "
                "the table below."
            )
        st.code(q.inline_params(statement.sql, statement.params), language="sql", line_numbers=True)

lifecycle = q.acceleration_lifecycle_sql(surface_key, settings)
materialization = q.acceleration_materialization_for(surface_key)

with ui.section("How the aggregate is built"):
    st.caption(
        "The scheduled task runs this body and writes the result into the aggregate table. It is "
        "the windowless form of the surface's fact query: the same SELECT without the date "
        "predicate, so the table holds all retained history and the app filters it per window."
    )
    if materialization is not None:
        st.code(materialization.body_sql, language="sql", line_numbers=True)

with st.expander("Create the aggregate table"):
    st.caption(
        "Created empty with the exact column contract via `CREATE TABLE AS SELECT ... WHERE "
        "1 = 0`, so the schema is fixed regardless of later body changes."
    )
    st.code(lifecycle["Create aggregate table"], language="sql", line_numbers=True)

with st.expander("Create the nightly refresh task"):
    st.caption(
        "A Snowflake Task runs a daily `INSERT OVERWRITE` at 01:00 UTC over the retained window, "
        "so each refresh fully replaces the table with current history."
    )
    st.code(lifecycle["Create refresh task"], language="sql", line_numbers=True)
    st.caption("Task warehouse DDL:")
    st.code(lifecycle["Create task warehouse"], language="sql", line_numbers=True)

with st.expander("Operate the task"):
    st.markdown("**Enable the schedule** (tasks are created suspended):")
    st.code(lifecycle["Enable schedule"], language="sql")
    st.markdown("**Run once now** (populate immediately instead of waiting for 01:00 UTC):")
    st.code(lifecycle["Run now"], language="sql")
    st.markdown("**Pause the schedule** (stop refreshing without dropping data):")
    st.code(lifecycle["Pause schedule"], language="sql")
    st.warning(
        "Destructive, for reference only. Dropping the task stops refreshes; dropping the "
        "table discards the materialized data and the dashboard reverts to live "
        "`ACCOUNT_USAGE`. Prefer teardown in Acceleration Settings, which drops only "
        "app-owned objects.",
        icon=":material/delete_forever:",
    )
    st.code(lifecycle["Drop resource"], language="sql")

with st.expander("Validate the snapshot"):
    st.caption(
        "After a refresh, confirm the table has rows and how current it is, and inspect recent "
        "task runs. These are read-only checks."
    )
    prefix = settings.object_prefix
    db = settings.database
    schema = settings.schema
    from utils.snowflake import surface_to_suffix

    suffix = surface_to_suffix(surface_key)
    table_fqn = f'"{db}"."{schema}"."{prefix}_{suffix}"'
    task_name = f"{prefix}_TASK_{suffix}"
    st.code(f"SELECT COUNT(*) AS rows FROM {table_fqn};", language="sql")
    st.code(f"SELECT MAX(USAGE_DATE) AS latest_day FROM {table_fqn};", language="sql")
    st.code(
        "SELECT NAME, STATE, SCHEDULED_TIME, COMPLETED_TIME\n"
        f"FROM TABLE({db}.INFORMATION_SCHEMA.TASK_HISTORY(\n"
        "  SCHEDULED_TIME_RANGE_START => DATEADD('day', -2, CURRENT_TIMESTAMP()),\n"
        "  RESULT_LIMIT => 100))\n"
        f"WHERE NAME = '{task_name}'\n"
        "ORDER BY SCHEDULED_TIME DESC;",
        language="sql",
    )

st.caption(
    "Every statement above is generated from the same builders Acceleration Settings and "
    "`sql/config.sql` use, so it matches what the app provisions."
)
