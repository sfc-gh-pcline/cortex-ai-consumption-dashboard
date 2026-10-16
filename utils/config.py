"""Configuration and acceleration layer for the Cortex AI Consumption Dashboard.

Owns the Settings model, persistence in CORTEX_DASHBOARD_CONFIG, DDL builders
for scheduled aggregate tables and tasks, status/health checks, and the
source-routing decision (``source_for``).

Import boundary: this module imports only from ``utils/snowflake.py`` (the shared
primitive leaf). It NEVER imports ``utils/queries.py``. DDL body SQL reaches this
module via ``DtBodySpec`` passed by the caller from ``DT_BODY_REGISTRY``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Literal

import pandas as pd
import streamlit as st

from utils.admin import require_admin
from utils.snowflake import (
    DtBodySpec,
    _execute_raw,
    _fetch_raw,
    quote_identifier,
    surface_to_suffix,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

CONFIG_TABLE_NAME = "CORTEX_DASHBOARD_CONFIG"

# Surface keys that have scheduled tables. Must match the keys in
# DT_BODY_REGISTRY (utils/queries.py) but defined here to avoid importing
# queries.py from config.py (which triggers circular/partial-import failures
# when the config page loads before the query layer is fully initialized).
_DT_SURFACE_KEYS = (
    "CoCo",
    "Cortex AI Functions",
    "Cortex Agents",
    "CoWork",
    "CoWork — Service Mix",
    "Cortex Search — Daily",
    "Cortex Search — Serving",
    "Cortex Search — Batch",
    "Warehouse Attribution",
    "Model Rates",
)

# How many UTC days of history the scheduled task retains.
TASK_RETENTION_DAYS = 365


def settled_utc_end_date(today: dt.date) -> dt.date:
    """Return the latest UTC date eligible for aggregate serving and parity.

    ACCOUNT_USAGE can backfill rows after a scheduled task has refreshed. The
    current UTC day therefore remains direct-source mutable data and is never
    served from, or compared against, a nightly aggregate snapshot.
    """
    return today - dt.timedelta(days=1)


def effective_served_window(
    start_date: dt.date,
    end_date: dt.date,
    *,
    accelerated: bool,
    today: dt.date,
) -> tuple[dt.date, dt.date]:
    """Return the date window that the selected source can safely serve.

    Direct reads preserve the requested window. Aggregate reads stop at the
    settled UTC day and collapse a future-only custom range to that day.
    """
    if not accelerated:
        return start_date, end_date

    settled_end_date = settled_utc_end_date(today)
    return min(start_date, settled_end_date), min(end_date, settled_end_date)


@dataclass
class OwnedObject:
    """A Snowflake object created and owned by this app's acceleration layer."""

    fqn: str
    object_type: Literal["TABLE", "TASK", "WAREHOUSE"]


def _infer_object_type(fqn: str) -> Literal["TABLE", "TASK", "WAREHOUSE"]:
    """Infer object type from FQN string for backward-compat migration only.

    Migration path: old ``owned_objects`` entries were plain FQN strings with
    no explicit type. New entries are ``OwnedObject`` records. This helper is
    called only when deserializing legacy string entries.
    """
    if "." not in fqn:
        return "WAREHOUSE"
    if "_TASK_" in fqn.upper():
        return "TASK"
    return "TABLE"


@dataclass
class Settings:
    """Persisted acceleration-layer configuration."""

    enabled: bool = False
    database: str = "COST_MGMT_DB"
    schema: str = "PUBLIC"
    warehouse_name: str = "COST_MGMT_WH"
    warehouse_size: str = "XSMALL"
    object_prefix: str = "COST_MGMT"
    owned_objects: list[OwnedObject] = field(default_factory=list)
    wh_degraded: bool = False


# ---------------------------------------------------------------------------
# Settings persistence (MERGE-based key-value store)
# ---------------------------------------------------------------------------


def _config_table_fqn(settings: Settings) -> str:
    db = quote_identifier(settings.database)
    schema = quote_identifier(settings.schema)
    return f"{db}.{schema}.{CONFIG_TABLE_NAME}"


def _ensure_config_table(settings: Settings) -> None:
    """Create the config table if it doesn't exist."""
    fqn = _config_table_fqn(settings)
    _execute_raw(
        f"CREATE TABLE IF NOT EXISTS {fqn} ("
        "KEY VARCHAR NOT NULL, "
        "VALUE VARCHAR, "
        "UPDATED_AT TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(), "
        "PRIMARY KEY (KEY))"
    )


def _owned_object_to_dict(o: OwnedObject | object) -> dict[str, str]:
    """Serialize an OwnedObject to a plain dict for JSON storage.

    Accepts legacy plain-string entries (backward compat) and coerces them.
    """
    if isinstance(o, OwnedObject):
        return {"fqn": o.fqn, "type": o.object_type}
    # Legacy: plain FQN string stored by an older version of the app.
    fqn = str(o)
    return {"fqn": fqn, "type": _infer_object_type(fqn)}


def save_settings(settings: Settings) -> None:
    """Persist all settings with one set-based MERGE."""
    require_admin()
    _ensure_config_table(settings)
    fqn = _config_table_fqn(settings)
    pairs = {
        "enabled": str(settings.enabled).lower(),
        "database": settings.database,
        "schema": settings.schema,
        "warehouse_name": settings.warehouse_name,
        "warehouse_size": settings.warehouse_size,
        "object_prefix": settings.object_prefix,
        "owned_objects": json.dumps([_owned_object_to_dict(o) for o in settings.owned_objects]),
        "wh_degraded": str(settings.wh_degraded).lower(),
    }
    values_sql = ", ".join("(?, ?)" for _ in pairs)
    params = tuple(item for pair in pairs.items() for item in pair)
    _execute_raw(
        f"MERGE INTO {fqn} t "
        f"USING (SELECT COLUMN1 AS KEY, COLUMN2 AS VALUE FROM VALUES {values_sql}) s "
        "ON t.KEY = s.KEY "
        "WHEN MATCHED THEN UPDATE SET VALUE = s.VALUE, UPDATED_AT = CURRENT_TIMESTAMP() "
        "WHEN NOT MATCHED THEN INSERT (KEY, VALUE) VALUES (s.KEY, s.VALUE)",
        params,
    )


def _parse_owned_objects(raw: list) -> list[OwnedObject]:
    """Deserialize the owned_objects list, migrating legacy plain-string entries."""
    result: list[OwnedObject] = []
    for item in raw:
        if isinstance(item, str):
            # Legacy format: bare FQN string. Infer type for migration.
            result.append(OwnedObject(fqn=item, object_type=_infer_object_type(item)))
        elif isinstance(item, dict):
            fqn = item.get("fqn", "")
            otype = item.get("type") or _infer_object_type(fqn)
            result.append(OwnedObject(fqn=fqn, object_type=otype))  # type: ignore[arg-type]
    return result


@st.cache_data(ttl="30s", show_spinner=False)
def _load_settings_cached(db: str, schema: str) -> dict[str, str]:
    """Load all key-value pairs from the config table (cached 30s)."""
    if not db or not schema:
        return {}
    fqn = f"{quote_identifier(db)}.{quote_identifier(schema)}.{CONFIG_TABLE_NAME}"
    try:
        df = _fetch_raw(f"SELECT KEY, VALUE FROM {fqn}")
        return dict(zip(df["KEY"].tolist(), df["VALUE"].tolist(), strict=False))
    except Exception:  # noqa: BLE001
        return {}


def load_settings(db: str = "", schema: str = "") -> Settings:
    """Load settings from the config table, or return defaults."""
    if not db or not schema:
        try:
            from utils.snowflake import _connection

            connection = _connection().raw_connection
            db = db or connection.database or ""
            schema = schema or connection.schema or ""
        except Exception:  # noqa: BLE001
            return Settings()

    kv = _load_settings_cached(db, schema)
    if not kv:
        return Settings(database=db, schema=schema)

    import contextlib

    owned: list[OwnedObject] = []
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        raw = json.loads(kv.get("owned_objects", "[]"))
        if isinstance(raw, list):
            owned = _parse_owned_objects(raw)

    return Settings(
        enabled=kv.get("enabled", "false") == "true",
        database=kv.get("database", db),
        schema=kv.get("schema", schema),
        warehouse_name=kv.get("warehouse_name", ""),
        warehouse_size=kv.get("warehouse_size", "XSMALL"),
        object_prefix=kv.get("object_prefix", "CORTEX_DASH"),
        owned_objects=owned,
        wh_degraded=kv.get("wh_degraded", "false") == "true",
    )


def _invalidate_settings_cache() -> None:
    """Clear the settings cache so next read reflects mutations."""
    _load_settings_cached.clear()


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------


@dataclass
class SurfaceStatus:
    """Status of one surface's aggregate table and refresh task."""

    surface_key: str
    table_exists: bool = False
    scheduling_state: str = ""  # "ACTIVE" | "SUSPENDED" | ""
    row_count: int = 0
    data_timestamp: str = ""  # COMPLETED_TIME of last successful task run
    task_exists: bool | None = None
    table_probe_ok: bool = True
    latest_state: str = ""
    latest_attempt: str = ""
    latest_error: str = ""
    probe_errors: tuple[str, ...] = ()


def readiness(status: SurfaceStatus | None, now: dt.datetime | None = None) -> str:
    """Describe operations independently of source routing; 26h is a UI warning threshold."""
    if status is None or status.probe_errors or not status.table_probe_ok:
        return "Unknown"
    if not status.table_exists:
        return "Not created"
    if status.task_exists is False:
        return "Task missing"
    if status.latest_state in {"FAILED", "CANCELLED"}:
        return "Refresh failed"
    if status.scheduling_state == "SUSPENDED":
        return "Suspended"
    if not status.data_timestamp:
        return "Awaiting first refresh"
    try:
        timestamp = dt.datetime.fromisoformat(status.data_timestamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return "Unknown"
        age = (now or dt.datetime.now(dt.UTC)) - timestamp
    except ValueError:
        return "Unknown"
    if age > dt.timedelta(hours=26):
        return "Stale snapshot"
    if status.scheduling_state != "ACTIVE":
        return "Unknown"
    return "Ready"


def configured_objects(settings: Settings) -> dict[str, OwnedObject]:
    """Enumerate exact configured targets without inferring ownership from a prefix."""
    namespace = f"{quote_identifier(settings.database)}.{quote_identifier(settings.schema)}"
    objects = [OwnedObject(quote_identifier(settings.warehouse_name), "WAREHOUSE")]
    for surface in _DT_SURFACE_KEYS:
        suffix = surface_to_suffix(surface)
        objects.extend(
            [
                OwnedObject(
                    f"{namespace}.{quote_identifier(f'{settings.object_prefix}_{suffix}')}", "TABLE"
                ),
                OwnedObject(
                    f"{namespace}.{quote_identifier(f'{settings.object_prefix}_TASK_{suffix}')}",
                    "TASK",
                ),
            ]
        )
    return {obj.fqn: obj for obj in objects}


def validate_provision_targets(settings: Settings) -> None:
    """Reject existing unregistered objects and unknown metadata before registration."""
    expected = configured_objects(settings)
    owned = {obj.fqn for obj in settings.owned_objects if isinstance(obj, OwnedObject)}
    namespace = f"{quote_identifier(settings.database)}.{quote_identifier(settings.schema)}"
    for object_type, statement in (
        ("WAREHOUSE", "SHOW WAREHOUSES"),
        ("TABLE", f"SHOW TABLES IN SCHEMA {namespace}"),
        ("TASK", f"SHOW TASKS IN SCHEMA {namespace}"),
    ):
        rows = _fetch_raw(statement)
        if rows is None:
            raise ValueError(f"Unknown {object_type} inventory; provisioning blocked.")
        if rows.empty:
            continue
        names = rows.get("name", rows.get("NAME"))
        if names is None:
            raise ValueError(f"Invalid {object_type} inventory; provisioning blocked.")
        for name in names:
            fqn = quote_identifier(str(name))
            if object_type != "WAREHOUSE":
                fqn = f"{namespace}.{fqn}"
            if fqn in expected and fqn not in owned:
                raise ValueError(f"Existing target is not registered to this app: {fqn}")


class ActionPersistenceError(RuntimeError):
    """Carry completed resource outcomes when the subsequent settings write fails."""

    def __init__(self, results: dict[str, str], cause: Exception):
        """Preserve per-object results independently of configuration persistence."""
        self.results = results
        super().__init__(
            f"Resource outcomes below are final, but configuration persistence failed: {cause}. "
            "The saved ownership registry may still include dropped objects; reload and reconcile before further actions."
        )


def run_owned_action(action: str, targets: list[str], settings: Settings) -> dict[str, str]:
    """Revalidate every action target before executing; retain individual failures."""
    require_admin()
    builders = {"suspend": suspend_task_sql, "resume": resume_task_sql, "refresh": execute_task_sql}
    if action not in {*builders, "drop"}:
        raise ValueError("Unsupported lifecycle action.")
    expected = configured_objects(settings)
    owned = {obj.fqn: obj for obj in settings.owned_objects if isinstance(obj, OwnedObject)}
    selected = []
    for fqn in dict.fromkeys(targets):
        if fqn not in expected or fqn not in owned or owned[fqn] != expected[fqn]:
            raise ValueError(f"Not an exact registered target: {fqn}")
        obj = owned[fqn]
        if action != "drop" and obj.object_type != "TASK":
            raise ValueError("Task lifecycle actions require registered TASK targets.")
        selected.append(obj)
    if not selected:
        return {}
    if action != "drop":
        _invalidate_health_cache()
        health = _health_cache(settings.database, settings.schema, settings.object_prefix)
        expected_state = "SUSPENDED" if action == "resume" else "ACTIVE"
        for obj in selected:
            surface = next(
                key
                for key in _DT_SURFACE_KEYS
                if obj.fqn.endswith(
                    "."
                    + quote_identifier(f"{settings.object_prefix}_TASK_{surface_to_suffix(key)}")
                )
            )
            status = health.get(surface)
            if (
                status is None
                or status.probe_errors
                or not status.task_exists
                or status.scheduling_state != expected_state
            ):
                raise ValueError(f"Task state is not verified for {action}: {obj.fqn}")
    result = {}
    for obj in sorted(
        selected, key=lambda item: {"TASK": 0, "TABLE": 1, "WAREHOUSE": 2}[item.object_type]
    ):
        failed_task = any(
            value.startswith("Failed:") and owned[target].object_type == "TASK"
            for target, value in result.items()
        )
        if action == "drop" and obj.object_type != "TASK" and failed_task:
            result[obj.fqn] = "Failed: blocked by task drop failure; no mutation attempted"
            continue
        statement = (
            drop_object_sql(obj.fqn, obj.object_type)
            if action == "drop"
            else builders[action](obj.fqn)
        )
        try:
            _execute_raw(statement)
        except Exception as exc:  # noqa: BLE001 - retain per-object failure for explicit retry
            result[obj.fqn] = f"Failed: {exc}"
        else:
            result[obj.fqn] = "Queued" if action == "refresh" else "Succeeded"
    if action == "drop":
        candidate = replace(
            settings,
            enabled=False,
            owned_objects=[
                obj for obj in settings.owned_objects if result.get(obj.fqn) != "Succeeded"
            ],
        )
        try:
            save_settings(candidate)
        except Exception as exc:
            raise ActionPersistenceError(result, exc) from exc
        else:
            settings.enabled = candidate.enabled
            settings.owned_objects = candidate.owned_objects
        finally:
            _invalidate_settings_cache()
            _invalidate_health_cache()
    _invalidate_health_cache()
    return result


def latest_snapshot_freshness(statuses: dict[str, SurfaceStatus]) -> str:
    """Return the latest healthy aggregate task completion timestamp, if any."""
    successful_timestamps = [
        status.data_timestamp
        for status in statuses.values()
        if status.table_exists and status.scheduling_state == "ACTIVE" and status.data_timestamp
    ]
    return max(successful_timestamps, default="")


def aggregate_snapshot_identity(settings: Settings) -> tuple[str, str]:
    """Return the mode and freshness used to distinguish warmed cache generations."""
    if not settings.enabled:
        return "direct", ""
    freshness = latest_snapshot_freshness(
        _health_cache(settings.database, settings.schema, settings.object_prefix)
    )
    return ("aggregate", freshness) if freshness else ("direct", "")


def aggregate_snapshot_identity_for(
    surface_keys: tuple[str, ...], settings: Settings
) -> tuple[str, str]:
    """Return the aggregate generation relevant to a set of dependencies.

    A task refresh for an unrelated surface must not invalidate the warmed
    cache for the page the user is viewing. Callers pass only aggregate-eligible
    dependency keys; direct and global dependencies do not contribute a task
    generation.
    """
    if not settings.enabled:
        return "direct", ""

    statuses = _health_cache(settings.database, settings.schema, settings.object_prefix)
    timestamps = [
        status.data_timestamp
        for surface_key in surface_keys
        if (status := statuses.get(surface_key))
        and status.table_exists
        and status.scheduling_state == "ACTIVE"
        and status.data_timestamp
    ]
    return ("aggregate", max(timestamps)) if timestamps else ("direct", "")


def dep_source_mode(dep_key: str, settings: Settings) -> tuple[str, str]:
    """Return the active source mode and a user-facing label for a dependency.

    Args:
        dep_key: A key from :data:`utils.queries.DEPENDENCY_KINDS`.
        settings: Current acceleration-layer settings.

    Returns:
        A ``(mode, reason)`` pair where ``mode`` is one of ``"aggregate"``,
        ``"direct_only"``, or ``"global_cached"``, and ``reason`` is a short
        explanation suitable for display in the UI.
    """
    from utils.queries import DEPENDENCY_KINDS

    kind = DEPENDENCY_KINDS.get(dep_key)
    if kind == "direct_only":
        return "direct_only", "Always reads ACCOUNT_USAGE (not aggregate-eligible)"
    if kind == "global_cached":
        return "global_cached", "Global metadata; cached independently of the date window"
    if kind == "windowed_aggregate":
        if source_for(dep_key, settings) == "dt":
            return "aggregate", "Reading from aggregate snapshot through the prior UTC day"
        return "direct_only", "Aggregate table unavailable — reading ACCOUNT_USAGE"
    return "direct_only", "Unregistered dependency — reading ACCOUNT_USAGE"


@st.cache_data(ttl="5m", show_spinner=False)
def _health_cache(db: str, schema: str, prefix: str) -> dict[str, SurfaceStatus]:
    """Check task/table status for five minutes; explicit reload invalidates it.

    Uses SHOW TASKS for scheduling state, INFORMATION_SCHEMA.TASK_HISTORY for
    actual last-successful-run freshness, and INFORMATION_SCHEMA.TABLES for
    table existence and row counts.
    """
    result: dict[str, SurfaceStatus] = {}
    if not db or not schema or not prefix:
        return result
    qdb = quote_identifier(db)
    qschema = quote_identifier(schema)
    quote_identifier(prefix)
    errors = []

    # 1. Scheduling state via SHOW TASKS (does NOT require USE SCHEMA)
    try:
        tasks_df = _fetch_raw(
            f"SHOW TASKS LIKE '{prefix.upper()}_TASK_%' IN SCHEMA {qdb}.{qschema}"
        )
    except Exception as exc:  # noqa: BLE001
        tasks_df = None
        errors.append(f"Task probe: {exc}")

    # 2. Table existence and row counts
    try:
        tables_df = _fetch_raw(
            f"SELECT TABLE_NAME, ROW_COUNT "
            f"FROM {qdb}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = ? AND TABLE_NAME LIKE ?",
            (schema.upper(), f"{prefix.upper()}_%"),
        )
    except Exception as exc:  # noqa: BLE001
        tables_df = None
        errors.append(f"Table probe: {exc}")

    task_last_run: dict[str, str] = {}
    attempts = {}
    try:
        hist_df = _fetch_raw(
            f"SELECT NAME, STATE, SCHEDULED_TIME, ERROR_MESSAGE, "
            f"MAX(IFF(STATE = 'SUCCEEDED', COMPLETED_TIME, NULL)) "
            f"OVER (PARTITION BY NAME) AS LAST_RUN "
            f"FROM TABLE({qdb}.INFORMATION_SCHEMA.TASK_HISTORY("
            f"  SCHEDULED_TIME_RANGE_START => DATEADD('day', -7, CURRENT_TIMESTAMP()), "
            f"  SCHEDULED_TIME_RANGE_END => CURRENT_TIMESTAMP(), "
            f"  DATABASE_NAME => ?, SCHEMA_NAME => ?, "
            f"  RESULT_LIMIT => 10000"
            f")) "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY NAME ORDER BY SCHEDULED_TIME DESC) = 1",
            (db.upper(), schema.upper()),
        )
        if not hist_df.empty:
            for _, row in hist_df.iterrows():
                if pd.notna(row["LAST_RUN"]):
                    task_last_run[str(row["NAME"])] = str(row["LAST_RUN"])
                attempts[str(row["NAME"])] = row
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Task history probe: {exc}")

    for surface_key in _DT_SURFACE_KEYS:
        status = SurfaceStatus(surface_key=surface_key)
        status.table_probe_ok = tables_df is not None
        status.task_exists = False if tasks_df is not None else None
        status.probe_errors = tuple(errors)
        table_suffix = surface_to_suffix(surface_key)
        table_name = f"{prefix.upper()}_{table_suffix}"
        task_name = f"{prefix.upper()}_TASK_{table_suffix}"

        if tasks_df is not None and not tasks_df.empty:
            name_col = "name" if "name" in tasks_df.columns else "NAME"
            state_col = "state" if "state" in tasks_df.columns else "STATE"
            match = tasks_df[tasks_df[name_col] == task_name]
            if not match.empty:
                raw_state = str(match.iloc[0].get(state_col, "")).upper()
                status.task_exists = True
                # Normalize STARTED → ACTIVE for source_for compatibility
                status.scheduling_state = "ACTIVE" if raw_state == "STARTED" else raw_state

        # Freshness: populated only when the task has successfully completed
        status.data_timestamp = task_last_run.get(task_name, "")
        if task_name in attempts:
            attempt = attempts[task_name]
            status.latest_state = str(attempt.get("STATE", ""))
            status.latest_attempt = str(attempt.get("SCHEDULED_TIME", ""))
            status.latest_error = str(attempt.get("ERROR_MESSAGE", "") or "")

        if tables_df is not None and not tables_df.empty:
            match = tables_df[tables_df["TABLE_NAME"] == table_name]
            if not match.empty:
                status.table_exists = True
                status.row_count = int(match.iloc[0].get("ROW_COUNT", 0) or 0)

        result[surface_key] = status

    return result


def _invalidate_health_cache() -> None:
    """Clear the health cache so next check reflects mutations."""
    _health_cache.clear()
    _reconcile_tables.clear()


# ---------------------------------------------------------------------------
# Source routing
# ---------------------------------------------------------------------------


def source_for(surface_key: str, settings: Settings) -> Literal["dt", "account_usage"]:
    """Decide whether a surface should read from its aggregate table or ACCOUNT_USAGE.

    Returns "dt" only when the toggle is enabled, the surface has a table,
    its scheduled task is ACTIVE, and the task has at least one successful run
    (``data_timestamp`` is non-empty). Returns "account_usage" otherwise.
    """
    if not settings.enabled:
        return "account_usage"
    if surface_key == "Cortex Inference API":
        return "account_usage"  # REST always reads AU

    health = _health_cache(settings.database, settings.schema, settings.object_prefix)
    status = health.get(surface_key)
    if not status or not status.table_exists:
        return "account_usage"
    if status.scheduling_state != "ACTIVE":
        return "account_usage"
    if not status.data_timestamp:  # Task has never successfully run
        return "account_usage"
    return "dt"


def has_healthy_surfaces(settings: Settings) -> bool:
    """Return True if at least one surface is ready to serve from its aggregate table.

    A surface is healthy when its table exists, the task is ACTIVE, and the
    task has at least one recorded successful completion.
    """
    health = _health_cache(settings.database, settings.schema, settings.object_prefix)
    return any(
        s.table_exists and s.scheduling_state == "ACTIVE" and bool(s.data_timestamp)
        for s in health.values()
    )


# ---------------------------------------------------------------------------
# DDL builders
# ---------------------------------------------------------------------------


def create_refresh_warehouse_sql(name: str, size: str) -> str:
    """Build DDL to create a standard warehouse for scheduled task execution."""
    qname = quote_identifier(name)
    return (
        f"CREATE WAREHOUSE IF NOT EXISTS {qname} "
        f"WAREHOUSE_SIZE = '{size.upper()}' "
        "AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE"
    )


def resize_refresh_warehouse_sql(name: str, size: str) -> str:
    """Build DDL to align an existing owned warehouse with saved settings."""
    return f"ALTER WAREHOUSE {quote_identifier(name)} SET WAREHOUSE_SIZE = '{size.upper()}'"


def create_aggregate_table_sql(
    spec: DtBodySpec,
    db: str,
    schema: str,
    prefix: str,
) -> str:
    """Build DDL to create a standard aggregate table (initially empty, correct schema).

    The table is populated by the companion scheduled task created via
    :func:`create_refresh_task_sql`. Using IF NOT EXISTS avoids overwriting
    existing data when re-provisioning.

    When ``spec.columns`` is non-empty the CREATE AS SELECT enumerates those
    columns explicitly instead of using ``SELECT *``, guaranteeing that the
    table schema matches the declared contract regardless of body-SQL changes.
    """
    table_name = f"{prefix}_{surface_to_suffix(spec.surface_key)}"
    fqn = f"{quote_identifier(db)}.{quote_identifier(schema)}.{quote_identifier(table_name)}"
    body = spec.body_fn()
    col_list = ", ".join(spec.columns) if spec.columns else "*"
    cluster_by = f"\n  CLUSTER BY ({spec.cluster_column})" if spec.cluster_column else ""
    return (
        f"CREATE TABLE IF NOT EXISTS {fqn}\n"
        f"{cluster_by}\n"
        f"  AS SELECT {col_list} FROM ({body}) _init\n"
        f"  WHERE 1 = 0"
    )


def create_refresh_task_sql(
    spec: DtBodySpec,
    db: str,
    schema: str,
    prefix: str,
    task_warehouse: str,
    lag_days: int = TASK_RETENTION_DAYS,
) -> str:
    """Build DDL to create (or replace) the scheduled INSERT OVERWRITE task.

    The task runs daily at 01:00 UTC, overwrites the aggregate table with the
    most recent ``lag_days`` UTC days of history, and starts SUSPENDED —
    call ``ALTER TASK ... RESUME`` after creation.

    When ``spec.columns`` is non-empty the INSERT SELECT enumerates those
    columns explicitly instead of using ``SELECT *``, ensuring the column
    order and set match the declared contract even if the body SQL evolves.
    """
    table_name = f"{prefix}_{surface_to_suffix(spec.surface_key)}"
    task_name = f"{prefix}_TASK_{surface_to_suffix(spec.surface_key)}"
    table_fqn = f"{quote_identifier(db)}.{quote_identifier(schema)}.{quote_identifier(table_name)}"
    task_fqn = f"{quote_identifier(db)}.{quote_identifier(schema)}.{quote_identifier(task_name)}"
    wh = quote_identifier(task_warehouse)
    body = spec.body_fn()
    col_list = ", ".join(spec.columns) if spec.columns else "*"
    retention_predicate = (
        f"\nWHERE USAGE_DATE >= DATEADD('day', -{lag_days}, "
        f"CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE)"
        if spec.apply_outer_retention
        else ""
    )
    return (
        f"CREATE OR REPLACE TASK {task_fqn}\n"
        f"  WAREHOUSE = {wh}\n"
        f"  SCHEDULE = 'USING CRON 0 1 * * * UTC'\n"
        f"  AS\n"
        f"INSERT OVERWRITE INTO {table_fqn}\n"
        f"SELECT {col_list} FROM ({body}) _body\n"
        f"{retention_predicate}"
    )


def suspend_task_sql(task_fqn: str) -> str:
    """Build DDL to suspend a scheduled task."""
    return f"ALTER TASK {task_fqn} SUSPEND"


def resume_task_sql(task_fqn: str) -> str:
    """Build DDL to resume a scheduled task."""
    return f"ALTER TASK {task_fqn} RESUME"


def execute_task_sql(task_fqn: str) -> str:
    """Build SQL to queue an immediate task run."""
    return f"EXECUTE TASK {task_fqn}"


def drop_object_sql(fqn: str, object_type: str = "TABLE") -> str:
    """Build DDL to drop an owned object."""
    return f"DROP {object_type} IF EXISTS {fqn}"


def suspend_warehouse_sql(wh_fqn: str) -> str:
    """Build DDL to suspend a warehouse."""
    return f"ALTER WAREHOUSE {wh_fqn} SUSPEND"


def resume_warehouse_sql(wh_fqn: str) -> str:
    """Build DDL to resume a warehouse."""
    return f"ALTER WAREHOUSE {wh_fqn} RESUME"


# ---------------------------------------------------------------------------
# Warehouse-attribution probe
# ---------------------------------------------------------------------------


def metering_probe() -> str:
    """Probe whether QUERY_METERING_HISTORY is accessible.

    Returns "full" if the metering view can be read, "attribution_only" otherwise.
    This is the create_probe for the warehouse-attribution DtBodySpec.
    """
    try:
        _fetch_raw("SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_METERING_HISTORY LIMIT 1")
    except Exception:  # noqa: BLE001
        return "attribution_only"
    else:
        return "full"


def warehouse_body_for_variant(variant: str) -> str:
    """Return the warehouse-attribution body SQL for the given variant."""
    from utils.queries import _warehouse_body_sql_attribution_only, _warehouse_body_sql_full

    if variant == "full":
        return _warehouse_body_sql_full()
    return _warehouse_body_sql_attribution_only()


# ---------------------------------------------------------------------------
# Provision report
# ---------------------------------------------------------------------------


@dataclass
class ProvisionReport:
    """Result of a multi-surface provisioning action."""

    results: dict[str, str] = field(default_factory=dict)  # surface -> "ok" | error message
    steps: dict[str, str] = field(default_factory=dict)
    target: tuple = ()
    error: str = ""

    @property
    def all_ok(self) -> bool:  # noqa: D102
        return not self.error and all(v == "ok" for v in self.results.values())

    @property
    def failures(self) -> dict[str, str]:  # noqa: D102
        return {k: v for k, v in self.results.items() if v != "ok"}


def provision_targets(
    settings: Settings, specs: dict[str, DtBodySpec], report: ProvisionReport
) -> None:
    """Provision or retry failed steps without replaying successful object mutations."""
    require_admin()
    target = (
        settings.database,
        settings.schema,
        settings.warehouse_name,
        settings.warehouse_size,
        settings.object_prefix,
    )
    if report.target and report.target != target:
        report.error = "Saved provisioning target changed; previous steps cannot be retried."
        return
    report.target = target
    report.error = ""
    try:
        validate_provision_targets(settings)
        planned = configured_objects(settings)
        if report.steps and any(obj not in settings.owned_objects for obj in planned.values()):
            report.error = "Provisioning ownership changed; retry refused."
            return
        register_owned_objects(list(planned.values()), settings)
    except Exception as exc:  # noqa: BLE001 - registration must succeed before resource DDL
        report.error = f"Provisioning preflight/registration refused: {exc}"
        return

    def execute_step(key: str, statement: str) -> None:
        if report.steps.get(key) == "ok":
            return
        try:
            _execute_raw(statement)
        except Exception as exc:
            report.steps[key] = str(exc)
            raise
        report.steps[key] = "ok"

    try:
        execute_step(
            "Task warehouse",
            create_refresh_warehouse_sql(settings.warehouse_name, settings.warehouse_size),
        )
        if any(
            obj.object_type == "WAREHOUSE" and obj.fqn == quote_identifier(settings.warehouse_name)
            for obj in settings.owned_objects
        ):
            execute_step(
                "Task warehouse size",
                resize_refresh_warehouse_sql(settings.warehouse_name, settings.warehouse_size),
            )
    except Exception as exc:  # noqa: BLE001 - no surface DDL after warehouse/context failure
        report.error = f"Task warehouse/context failed: {exc}"
        return

    namespace = f"{quote_identifier(settings.database)}.{quote_identifier(settings.schema)}"
    try:
        for surface, spec in specs.items():
            if report.results.get(surface) == "ok":
                continue
            suffix = surface_to_suffix(surface)
            table = f"{namespace}.{quote_identifier(f'{settings.object_prefix}_{suffix}')}"
            task = f"{namespace}.{quote_identifier(f'{settings.object_prefix}_TASK_{suffix}')}"
            try:
                for key, statement in (
                    (
                        f"Create {table}",
                        create_aggregate_table_sql(
                            spec, settings.database, settings.schema, settings.object_prefix
                        ),
                    ),
                    (
                        f"Create {task}",
                        create_refresh_task_sql(
                            spec,
                            settings.database,
                            settings.schema,
                            settings.object_prefix,
                            settings.warehouse_name,
                        ),
                    ),
                    (f"Resume {task}", resume_task_sql(task)),
                    (f"Queue {task}", execute_task_sql(task)),
                ):
                    execute_step(key, statement)
            except Exception as exc:  # noqa: BLE001 - keep other surface outcomes and failed-step identity
                report.results[surface] = str(exc)
            else:
                report.results[surface] = "ok"
    finally:
        _invalidate_settings_cache()
        _invalidate_health_cache()


# ---------------------------------------------------------------------------
# Ownership lifecycle (pre-registration + reconciliation)
# ---------------------------------------------------------------------------


def _register_owned_object(
    fqn: str,
    object_type: Literal["TABLE", "TASK", "WAREHOUSE"],
    settings: Settings,
) -> None:
    """Pre-register an object in owned_objects with its type BEFORE executing CREATE DDL.

    This ensures that if CREATE succeeds but a later save_settings fails, the
    object is still tracked for teardown.
    """
    existing = {o.fqn if isinstance(o, OwnedObject) else o for o in settings.owned_objects}
    if fqn not in existing:
        settings.owned_objects.append(OwnedObject(fqn=fqn, object_type=object_type))
        save_settings(settings)


def register_owned_objects(objects: list[OwnedObject], settings: Settings) -> None:
    """Pre-register new objects in one settings write before provisioning DDL."""
    existing = {o.fqn if isinstance(o, OwnedObject) else o for o in settings.owned_objects}
    new_objects = [obj for obj in objects if obj.fqn not in existing]
    if not new_objects:
        return
    candidate = replace(settings, owned_objects=[*settings.owned_objects, *new_objects])
    save_settings(candidate)
    settings.owned_objects = candidate.owned_objects


def _unregister_owned_object(fqn: str, settings: Settings) -> None:
    """Remove an object from owned_objects after a successful DROP."""
    settings.owned_objects = [
        o for o in settings.owned_objects if (o.fqn if isinstance(o, OwnedObject) else o) != fqn
    ]
    save_settings(settings)


def check_ownership_conflict(fqn: str, settings: Settings) -> None:
    """Raise OwnershipConflictError if the object exists but isn't in owned_objects.

    Call before CREATE to prevent accidentally overwriting pre-existing objects.
    """
    from utils.snowflake import OwnershipConflictError

    owned_fqns = {o.fqn if isinstance(o, OwnedObject) else o for o in settings.owned_objects}
    if fqn in owned_fqns:
        return  # We own it, safe to recreate
    try:
        _fetch_raw(f"SELECT 1 FROM {fqn} LIMIT 0")
    except OwnershipConflictError:
        raise
    except Exception:  # noqa: BLE001
        return  # Object doesn't exist — safe to create
    raise OwnershipConflictError(fqn)


@st.cache_data(ttl="5m", max_entries=32, show_spinner=False)
def _reconcile_tables(database: str, schema: str, prefix: str):
    """Cache the non-mutating namespace inventory separately from ownership."""
    return _fetch_raw(
        f"SELECT TABLE_NAME FROM {quote_identifier(database)}.INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_SCHEMA = ? AND TABLE_NAME LIKE ?",
        (schema.upper(), f"{prefix.upper()}_%"),
    )


def _reconcile_owned_objects(settings: Settings) -> list[str]:
    """Find objects matching our naming convention that aren't in owned_objects.

    Called at startup to detect orphaned objects from a previously failed
    registration. Returns a list of untracked FQNs found.
    """
    if not settings.database or not settings.schema:
        return []
    prefix = settings.object_prefix.upper()
    try:
        tables = _reconcile_tables(settings.database, settings.schema, prefix)
    except Exception:  # noqa: BLE001
        return []

    owned_fqns = {o.fqn if isinstance(o, OwnedObject) else o for o in settings.owned_objects}
    untracked: list[str] = []
    if not tables.empty:
        for name in tables["TABLE_NAME"].tolist():
            fqn = (
                f"{quote_identifier(settings.database)}"
                f".{quote_identifier(settings.schema)}"
                f".{quote_identifier(name)}"
            )
            if fqn not in owned_fqns:
                untracked.append(fqn)
    return untracked
