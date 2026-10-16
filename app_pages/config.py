"""Acceleration Settings page: standard warehouse plus scheduled aggregate tables.

Create/manage aggregate tables (one per surface) populated by scheduled
INSERT OVERWRITE tasks, using a standard warehouse for task execution.
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from utils import queries as q
from utils import ui
from utils.admin import is_admin, viewer_name
from utils.config import (
    ActionPersistenceError,
    OwnedObject,
    ProvisionReport,
    _health_cache,
    _invalidate_health_cache,
    _invalidate_settings_cache,
    _reconcile_owned_objects,
    configured_objects,
    dep_source_mode,
    has_healthy_surfaces,
    latest_snapshot_freshness,
    load_settings,
    provision_targets,
    readiness,
    register_owned_objects,
    run_owned_action,
    save_settings,
)
from utils.runtime import fingerprint, render_fingerprint
from utils.snowflake import (
    quote_identifier,
    surface_to_suffix,
)

ui.page_header(
    "Acceleration Settings",
    "Speed up this dashboard by materializing usage data into aggregate tables.",
    eyebrow="Performance",
)

# Read-only build identity, shown before the admin gate so any viewer can confirm
# which code the container is running and whether its imports have gone stale.
render_fingerprint(fingerprint())

if not is_admin():
    viewer = viewer_name()
    st.info(
        "Acceleration settings require an authorized dashboard administrator. "
        + (f"You are signed in as {viewer}. " if viewer else "Your username could not be read. ")
        + "The app owner grants access by listing usernames in admin_access.toml; "
        "the Snowflake role does not apply because the app runs with the owner's rights."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Load current settings
# ---------------------------------------------------------------------------

settings = load_settings()
context = st.session_state.get("preflight_result")
account = context.account if context is not None else "Unverified"
role = context.role if context is not None else "Unverified"
st.caption(f"Account: {account}; role: {role}; target: {settings.database}.{settings.schema}")


def reset_setup_draft():
    saved = load_settings()
    st.session_state.setup_generation = st.session_state.get("setup_generation", 0) + 1
    for field in ("database", "schema", "warehouse_name", "warehouse_size", "object_prefix"):
        st.session_state[f"setup_{field}_{st.session_state.setup_generation}"] = getattr(
            saved, field
        )


editing_setup = st.toggle(
    "Edit setup",
    key="editing_setup",
    on_change=reset_setup_draft,
    help="Lifecycle actions are disabled while editing. Leave edit mode to discard unapplied form changes.",
)

# Startup reconciliation: detect orphaned objects
untracked = _reconcile_owned_objects(settings)
if untracked:
    st.warning(
        f"Found {len(untracked)} object(s) matching this app's naming convention "
        "that aren't tracked for teardown.",
        icon=":material/warning:",
    )
    with st.expander("Show untracked objects"):
        for fqn in untracked:
            st.code(fqn)
    if st.button("Adopt existing objects", disabled=editing_setup):
        planned = configured_objects(settings)
        register_owned_objects(
            [planned[fqn] for fqn in untracked if fqn in planned],
            settings,
        )
        _invalidate_settings_cache()
        _invalidate_health_cache()
        st.rerun()

# ---------------------------------------------------------------------------
# Operations summary
# ---------------------------------------------------------------------------

st.button("Refresh status", on_click=_invalidate_health_cache, icon=":material/refresh:")
st.caption("Operational status is cached for up to five minutes.")

health = (
    _health_cache(settings.database, settings.schema, settings.object_prefix)
    if settings.database and settings.schema
    else {}
)
healthy_surfaces = sum(readiness(status) == "Ready" for status in health.values())
total_surfaces = len(q.DT_BODY_REGISTRY)
surfaces_healthy = has_healthy_surfaces(settings)
snapshot_freshness = latest_snapshot_freshness(health)
toggle_disabled = editing_setup or (not surfaces_healthy and not settings.enabled)

summary_columns = st.columns(3)
summary_columns[0].metric(
    "Acceleration",
    "Active" if settings.enabled else "Direct source",
    delta="Aggregate tables" if settings.enabled else "ACCOUNT_USAGE",
    delta_color="normal",
)
summary_columns[1].metric("Ready surfaces", f"{healthy_surfaces} / {total_surfaces}")
summary_columns[2].metric("Refresh schedule", "01:00 UTC", delta="Nightly")

toggle_col, info_col = st.columns([1, 3])
with toggle_col:
    enabled = st.toggle(
        "Enable acceleration",
        value=settings.enabled,
        disabled=toggle_disabled,
        help=(
            "When enabled, queries read from aggregate tables instead of ACCOUNT_USAGE. "
            "Requires at least one aggregate table with a completed task run."
        ),
    )
    if enabled != settings.enabled:
        try:
            save_settings(replace(settings, enabled=enabled))
        except Exception as exc:  # noqa: BLE001 - preserve the saved setting after rejected persistence
            st.error(f"Acceleration setting was not saved: {exc}")
        else:
            _invalidate_settings_cache()
            _invalidate_health_cache()
            q.clear_cache()
            st.rerun()

with info_col:
    if settings.enabled:
        st.success(
            "Acceleration is enabled. Each source uses an eligible aggregate or direct fallback."
            + (
                f" Latest successful snapshot: {snapshot_freshness[:19]}."
                if snapshot_freshness
                else ""
            ),
            icon=":material/bolt:",
        )
    elif toggle_disabled:
        st.info(
            "Provision the acceleration layer and wait for one successful task run to enable it.",
            icon=":material/info:",
        )
    else:
        st.info("Reading from ACCOUNT_USAGE directly.", icon=":material/database:")

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

st.divider()

with ui.section("Resource status"):
    if not settings.database or not settings.schema:
        st.caption("Configure a database and schema in Setup to see resource status.")
    else:
        rows = []
        # Warehouse row
        wh_label = settings.warehouse_name or "Not configured"
        rows.append(
            {
                "Surface": f"Task WH ({wh_label})",
                "Object Type": "WAREHOUSE",
                "Status": "Configured" if settings.warehouse_name else "Not created",
                "Row Count": "",
                "Latest successful snapshot": "",
            }
        )

        # Per-surface aggregate tables
        for surface_key in q.DT_BODY_REGISTRY:
            status = health.get(surface_key)
            if status and status.table_exists:
                state_label = readiness(status)
                rows.append(
                    {
                        "Surface": surface_key,
                        "Object Type": (
                            "GLOBAL AGGREGATE"
                            if surface_key == q.SURFACE_MODEL_RATES
                            else "AGGREGATE TABLE"
                        ),
                        "Status": state_label,
                        "Latest attempt": status.latest_state,
                        "Attempt time": status.latest_attempt,
                        "Diagnostic": "; ".join((*status.probe_errors, status.latest_error)).strip(
                            "; "
                        ),
                        "Row Count": f"{status.row_count:,}" if status.row_count else "0",
                        "Latest successful snapshot": status.data_timestamp[:19]
                        if status.data_timestamp
                        else "",
                    }
                )
            else:
                rows.append(
                    {
                        "Surface": surface_key,
                        "Object Type": (
                            "GLOBAL AGGREGATE"
                            if surface_key == q.SURFACE_MODEL_RATES
                            else "AGGREGATE TABLE"
                        ),
                        "Status": readiness(status),
                        "Diagnostic": "; ".join(status.probe_errors)
                        if status
                        else "Health probe unavailable",
                        "Row Count": "",
                        "Latest successful snapshot": "",
                    }
                )

        st.caption(
            "Stale snapshot means more than 26 hours since success: a dashboard warning threshold, not a Snowflake SLA. Suspended tables can exist while routing uses direct reads."
        )

        # REST (always AU-only)
        rows.append(
            {
                "Surface": "Cortex Inference API",
                "Object Type": "N/A",
                "Status": "ACCOUNT_USAGE only",
                "Row Count": "",
                "Latest successful snapshot": "",
            }
        )

        st.dataframe(
            pd.DataFrame(rows).style.map(
                lambda status: (
                    f"color: {ui.resource_status_color(status, dark=st.context.theme.type == 'dark')}; font-weight: 600"
                ),
                subset=["Status"],
            ),
            hide_index=True,
            width="stretch",
        )

with ui.section("Source routing"):
    routing_rows = []
    for dependency in q.DEPENDENCY_KINDS:
        mode, reason = dep_source_mode(dependency, settings)
        routing_rows.append(
            {
                "Dependency": dependency,
                "Kind": q.DEPENDENCY_KINDS[dependency],
                "Mode": mode,
                "Reason": reason,
            }
        )
    st.dataframe(pd.DataFrame(routing_rows), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Load diagnostics
#
# The last cache-warm records which page was warmed, its resolved source mode,
# the aggregate snapshot it read, and per-loader wall-clock timings. This makes
# a "slow tab" report measurable instead of inferred: open the tab, then read
# the exact loaders and durations here.
# ---------------------------------------------------------------------------

_diag = st.session_state.get("last_warm_diagnostics")
if _diag:
    with st.expander("Load diagnostics"):
        source_label = (
            f"aggregate snapshot ({_diag['snapshot'][:19]})"
            if _diag["source"] == "aggregate" and _diag["snapshot"]
            else "direct ACCOUNT_USAGE"
        )
        st.caption(
            f"Last warm: **{_diag['page']}** · source: {source_label} · "
            f"{len(_diag['timings'])} loader(s)."
        )
        timing_rows = [
            {"Loader": label, "Seconds": round(seconds, 3)}
            for label, seconds in sorted(_diag["timings"], key=lambda item: item[1], reverse=True)
        ]
        if timing_rows:
            st.dataframe(
                pd.DataFrame(timing_rows),
                hide_index=True,
                width="stretch",
            )
        if _diag.get("outcomes"):
            st.dataframe(pd.DataFrame(_diag["outcomes"]), hide_index=True)
        if "elapsed_seconds" in _diag:
            st.caption(
                f"Whole dependency load: {_diag['elapsed_seconds']:.3f} seconds. Loader count is not SQL query count."
            )
        if "source_query_count" in _diag:
            st.caption(
                f"Source query attempts: {_diag['source_query_count']}. Excludes UTC clock, preflight, configuration and health probes; cache hits do not count."
            )
        st.caption(
            "Timings are Streamlit cache-populate durations for the warmed page. "
            "A near-zero value means the result was already cached."
        )

# ---------------------------------------------------------------------------
# Provisioning actions
# ---------------------------------------------------------------------------

operations_box = ui.section("Operations")
operations_box.caption(
    "Create the acceleration infrastructure: a task warehouse, "
    "aggregate tables, and scheduled tasks for each surface."
)

# Persist last provision report across reruns so users see prior results.
_REPORT_KEY = "provision_report"
prior_report: ProvisionReport | None = st.session_state.get(_REPORT_KEY)
if prior_report is not None and isinstance(prior_report, ProvisionReport):
    if prior_report.error:
        operations_box.error(prior_report.error)
    if prior_report.failures:
        operations_box.warning(
            f"Last provision run: {len(prior_report.failures)} surface(s) failed. "
            f"Failed: {', '.join(prior_report.failures)}",
            icon=":material/warning:",
        )
    elif prior_report.all_ok:
        operations_box.success(
            f"Last provision run: {len(prior_report.results)} surface(s) provisioned; initial refreshes queued, awaiting completion.",
            icon=":material/check_circle:",
        )
    for surface_key, result in prior_report.results.items():
        operations_box.checkbox(
            surface_key if result == "ok" else f"{surface_key}: {result}",
            value=result == "ok",
            disabled=True,
            key=f"provision_result_{surface_key}",
        )
    if prior_report.steps:
        operations_box.dataframe(
            pd.DataFrame(
                [{"Step": step, "Result": result} for step, result in prior_report.steps.items()]
            ),
            hide_index=True,
        )

operation_columns = operations_box.columns([1.25, 1, 1, 1])
with operation_columns[0]:
    provision_requested = st.button(
        "Provision aggregate tables",
        width="stretch",
        disabled=editing_setup
        or not (settings.database and settings.schema and settings.warehouse_name),
        type="primary",
        help="Creates task WH, aggregate tables, and scheduled INSERT OVERWRITE tasks.",
    )

    retry_provision = bool(
        prior_report and (prior_report.failures or prior_report.error)
    ) and st.button("Retry failed provisioning", disabled=editing_setup)
    if provision_requested or retry_provision:
        if editing_setup:
            st.error("Finish or discard setup changes before provisioning.")
            st.stop()
        _invalidate_settings_cache()
        settings = load_settings()
        report = prior_report if retry_provision else ProvisionReport()
        assert report is not None
        st.session_state[_REPORT_KEY] = report
        with st.status("Provisioning infrastructure...", expanded=True):
            provision_targets(settings, q.DT_BODY_REGISTRY, report)
        q.clear_cache()
        st.rerun()

operations_box.caption(
    "Aggregate tables refresh nightly at 01:00 UTC, retain 365 days of history, and serve through "
    "the prior UTC day. The task warehouse runs only during refreshes."
)


def perform_action(action, targets, *, retry=False):
    """Persist the outcome before rerunning, including validation failures."""
    if st.session_state.get("editing_setup"):
        st.error("Finish or discard setup changes before lifecycle actions.")
        return
    _invalidate_settings_cache()
    previous = st.session_state.get("lifecycle_report", {}) if retry else {}
    results = dict(previous.get("results", {}))
    error = ""
    persistence_failed = False
    try:
        results.update(run_owned_action(action, targets, load_settings()))
    except ActionPersistenceError as exc:
        results.update(exc.results)
        error = str(exc)
        persistence_failed = True
    except Exception as exc:  # noqa: BLE001 - keep prior outcomes after validation/probe failure
        error = str(exc)
    st.session_state.lifecycle_report = {
        "action": action,
        "results": results,
        "error": error,
        "persistence_failed": persistence_failed,
    }
    st.session_state.pop("teardown_pending", None)
    q.clear_cache()
    st.rerun()


try:
    allowed = configured_objects(settings)
except ValueError:
    allowed = {}
owned_tasks = [
    obj.fqn
    for obj in settings.owned_objects
    if isinstance(obj, OwnedObject) and obj.object_type == "TASK" and allowed.get(obj.fqn) == obj
]
operations_box.caption(
    f"Target: {settings.database}.{settings.schema}; only exact registered objects are eligible."
)
for column, action, label, schedule in (
    (operation_columns[1], "suspend", "Suspend refreshes", "ACTIVE"),
    (operation_columns[2], "resume", "Resume refreshes", "SUSPENDED"),
    (operation_columns[3], "refresh", "Refresh aggregate tables", "ACTIVE"),
):
    targets = [
        fqn
        for fqn in owned_tasks
        if any(
            status.task_exists
            and not status.probe_errors
            and status.scheduling_state == schedule
            and fqn
            == f"{quote_identifier(settings.database)}.{quote_identifier(settings.schema)}.{quote_identifier(f'{settings.object_prefix}_TASK_{surface_to_suffix(surface)}')}"
            for surface, status in health.items()
        )
    ]
    with column:
        if st.button(
            label,
            disabled=editing_setup or not targets,
            help="Applies only to verified registered tasks; refresh queues work and does not certify ingestion freshness.",
        ):
            perform_action(action, targets)

if prior_action := st.session_state.get("lifecycle_report"):
    if prior_action["error"]:
        st.error(prior_action["error"])
    results = prior_action["results"]
    if results:
        st.dataframe(
            pd.DataFrame(
                [{"Target": target, "Result": result} for target, result in results.items()]
            ),
            hide_index=True,
        )
        failures = [target for target, result in results.items() if result.startswith("Failed:")]
        if failures:
            st.warning(f"{len(failures)} target(s) failed. Successful targets will not be retried.")
            if st.button(
                "Retry failed targets",
                disabled=editing_setup or prior_action.get("persistence_failed", False),
                help="Resolve configuration persistence and reconcile ownership before retrying mutations.",
            ):
                perform_action(prior_action["action"], failures, retry=True)
        elif prior_action["action"] == "refresh":
            st.info(
                "Refreshes queued, not completed. Resource status shows the latest observed attempt."
            )

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

if "setup_generation" not in st.session_state:
    reset_setup_draft()

setup_generation = st.session_state.setup_generation
with st.expander("Setup"), st.form(f"acceleration_setup_{setup_generation}"):
    st.info(
        "This page creates Snowflake objects (warehouse, aggregate tables, tasks). "
        "Your active role must have **CREATE TABLE**, **CREATE TASK**, and "
        "**CREATE WAREHOUSE** privileges on the target schema. "
        "If provisioning fails, ask your Snowflake admin to grant these privileges.",
        icon=":material/admin_panel_settings:",
    )
    st.caption("Where acceleration objects (warehouse, aggregate tables) will be created.")

    setup_col1, setup_col2 = st.columns(2)
    with setup_col1:
        db_input = st.text_input(
            "Database",
            disabled=not editing_setup,
            key=f"setup_database_{setup_generation}",
            help="Target database for acceleration objects.",
        )
    with setup_col2:
        schema_input = st.text_input(
            "Schema",
            disabled=not editing_setup,
            key=f"setup_schema_{setup_generation}",
            help="Target schema for acceleration objects.",
        )

    setup_col3, setup_col4 = st.columns(2)
    with setup_col3:
        wh_name_input = st.text_input(
            "Task warehouse",
            disabled=not editing_setup,
            key=f"setup_warehouse_name_{setup_generation}",
            help="Standard warehouse used to execute the scheduled refresh tasks.",
        )
    with setup_col4:
        wh_size = st.selectbox(
            "Warehouse size",
            ["XSMALL", "SMALL", "MEDIUM", "LARGE", "XLARGE"],
            disabled=not editing_setup,
            key=f"setup_warehouse_size_{setup_generation}",
            help="Size for the task warehouse.",
        )

    prefix_input = st.text_input(
        "Object name prefix",
        key=f"setup_object_prefix_{setup_generation}",
        disabled=not editing_setup,
        help="All created objects use this prefix (e.g. COST_MGMT_COCO).",
    )

    submitted = st.form_submit_button(
        "Save configuration", type="primary", disabled=not editing_setup
    )
    errors = {}
    if submitted and not editing_setup:
        errors["Setup"] = "Enter edit mode before saving configuration."
    if submitted:
        for label, value in (
            ("Database", db_input),
            ("Schema", schema_input),
            ("Task warehouse", wh_name_input),
            ("Object name prefix", prefix_input),
        ):
            try:
                quote_identifier(value)
            except ValueError as exc:
                errors[label] = str(exc)
        for label, error in errors.items():
            st.error(f"{label}: {error}")
    if submitted and not errors:
        with st.spinner("Saving configuration..."):
            candidate = replace(
                settings,
                database=db_input,
                schema=schema_input,
                warehouse_name=wh_name_input,
                warehouse_size=wh_size,
                object_prefix=prefix_input,
            )
            if settings.owned_objects and configured_objects(candidate) != configured_objects(
                settings
            ):
                st.error(
                    "Registered objects still use the saved target. Tear them down successfully before changing database, schema, warehouse name or prefix."
                )
            else:
                try:
                    save_settings(candidate)
                except Exception as exc:  # noqa: BLE001 - preserve saved configuration after a failed write
                    st.error(f"Configuration was not saved: {exc}")
                else:
                    _invalidate_settings_cache()
                    _invalidate_health_cache()
                    q.clear_cache()
                    st.session_state.configuration_saved = True
                    st.rerun()

if st.session_state.pop("configuration_saved", False):
    st.success("Configuration saved.")

# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

with ui.section("Danger zone"):
    if not settings.owned_objects:
        st.caption("No acceleration objects to tear down.")
    else:
        st.warning(
            f"**{len(settings.owned_objects)} object(s)** will be permanently dropped. "
            "Dashboard queries will revert to reading ACCOUNT_USAGE directly.",
            icon=":material/delete_forever:",
        )

        @st.dialog("Confirm Teardown")
        def teardown_dialog():
            st.write("The following objects will be **permanently dropped**:")
            for obj in settings.owned_objects:
                fqn = obj.fqn if isinstance(obj, OwnedObject) else obj
                otype = obj.object_type if isinstance(obj, OwnedObject) else "TABLE"
                st.code(f"{fqn}  [{otype}]", language=None)
            st.divider()
            if st.button("Cancel"):
                st.session_state.pop("teardown_pending", None)
                st.rerun()
            if st.button(
                "Drop all objects", type="primary", width="stretch", disabled=editing_setup
            ):
                perform_action("drop", [obj.fqn for obj in settings.owned_objects])

        if st.button(
            "Tear down all acceleration objects", type="secondary", disabled=editing_setup
        ):
            st.session_state.teardown_pending = True
        if st.session_state.get("teardown_pending"):
            teardown_dialog()
