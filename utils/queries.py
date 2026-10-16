"""Snowflake query layer for AI consumption across every Cortex surface.

Each surface has a ``*_sql`` builder returning a :data:`Statement` and a
``load_*`` wrapper that executes it through the cached :func:`run_query`.
Splitting the two lets the SQL page render exactly what the app runs.
"""

import datetime as dt
import decimal
import logging
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol, cast

import pandas as pd
import streamlit as st

from utils.snowflake import connection_lock
from utils.view_state import CoverageState, DateWindow, LoadedDependency, SourceCoverage

if TYPE_CHECKING:
    from utils.config import Settings

_logger = logging.getLogger(__name__)

AU = "SNOWFLAKE.ACCOUNT_USAGE"

# A SQL statement paired with the values to bind to its qmark placeholders, in
# order. Every builder in this module returns one.
Statement = tuple[str, tuple]

# Surface labels are the values written into the SURFACE column of the unified
# frame, so every pill, breakdown, chart legend and pivot column on Total
# Consumption inherits them. They are also the navigation tab titles, the page
# headers and the loading-spinner text: those all read these constants rather
# than repeating the strings, so a label can only be changed here. The order
# drives the header pills and the per-surface column order in the top-users
# table, so it mirrors the tab order too.
SURFACE_COCO = "CoCo"
SURFACE_COWORK = "CoWork"
SURFACE_AGENTS = "Cortex Agents"
SURFACE_REST_API = "Cortex Inference API"
SURFACE_AI_FUNCTIONS = "Cortex AI Functions"
SURFACE_MODEL_RATES = "Model Rates"

SURFACE_SEARCH = "Cortex Search"

SURFACES = (
    SURFACE_COCO,
    SURFACE_COWORK,
    SURFACE_AGENTS,
    SURFACE_SEARCH,
    SURFACE_REST_API,
    SURFACE_AI_FUNCTIONS,
)

# Not part of SURFACES: Warehouse Attribution is a cross-surface page, not a
# surface in its own right, so it never appears in a SURFACE column value.
SURFACE_WAREHOUSE_ATTRIBUTION = "Warehouse Attribution"

# Query tags carrying a Cortex Code app name, from utils.queries.coco_facts_sql.
_COCO_APP_NAMES = ("cortex_code_cli", "cortex_code_desktop", "cortex_code_sandbox")

TIER_PER_REQUEST = "per_request"
TIER_SURFACE = "surface"

COCO_USAGE_VIEW = "SNOWFLAKE_COCO_USAGE_HISTORY"
COCO_SUB_SURFACES = ("cli", "desktop", "snowsight")

TOKEN_TYPES = ("input", "output", "cache_read_input", "cache_write_input")

TOKEN_TYPE_LABELS = {
    "input": "Input",
    "output": "Output",
    "cache_read_input": "Cache read",
    "cache_write_input": "Cache write",
}

TOKEN_TYPE_COLUMNS = {t: f"TOK_{t.upper()}" for t in TOKEN_TYPES}

RATE_COLUMNS = ("R_INPUT", "R_OUTPUT", "R_CACHE_READ", "R_CACHE_WRITE")

UNKNOWN = "(unknown)"
NONE_LABEL = "(none)"

# How far back to sample observed model rates. See model_rates_sql for the
# accuracy/cost tradeoff; widen it if the Cortex Inference API page reports
# rated-token coverage below ~100%.
RATES_WINDOW_DAYS = 90


# ---------------------------------------------------------------------------
# Query execution
#
# Each page issues exactly one aggregate query, at a grain small enough
# (< ~2k rows for full account history) that every breakdown, KPI and
# page-level filter is then derived in pandas. Only the date range triggers
# a re-query, so user / model / tag / dimension filters respond instantly.
# ---------------------------------------------------------------------------


def _connection():
    """Return the shared Streamlit Snowflake connection.

    Returns:
        The ``SnowflakeConnection`` Streamlit caches for the app. Calling
        ``st.connection`` repeatedly is cheap: it hands back the same object.
    """
    from utils.snowflake import _connection as shared_connection

    return shared_connection()


_active_dependency = ContextVar("active_dashboard_dependency", default="")


def prepare_acquisition(settings: "Settings", start: dt.date, end: dt.date) -> None:
    """Attach a bounded session result store to the current source scope."""
    context = st.session_state.get("preflight_result")
    scope = (
        getattr(context, "account", ""),
        getattr(context, "role", ""),
        settings.database,
        settings.schema,
        settings.object_prefix,
        settings.enabled,
        start,
        end,
    )
    if st.session_state.get("acquisition_scope") != scope:
        st.session_state.acquisition_results = {}
        st.session_state.pop("acquisition_overview", None)
    st.session_state.acquisition_scope = scope
    generations = {
        dependency: dependency_generation(dependency, settings) for dependency in DEPENDENCY_KINDS
    }
    if generations != st.session_state.get("acquisition_generations"):
        st.session_state.pop("acquisition_overview", None)
    st.session_state.acquisition_generations = generations


def _acquisition_key(dependency: str, start: dt.date, end: dt.date) -> tuple | None:
    scope = st.session_state.get("acquisition_scope")
    if not scope or scope[-2:] != (start, end):
        return None
    return (*scope, dependency, st.session_state.acquisition_generations.get(dependency, ""))


def dependency_is_ready(dependency: str, start: dt.date, end: dt.date) -> bool:
    """Check the actual retained acquisition, not a historical warm marker."""
    key = _acquisition_key(dependency, start, end)
    entry = st.session_state.get("acquisition_results", {}).get(key)
    return entry is not None and entry[0] > time.monotonic()


def _remember_acquisition(key: tuple | None, loaded: LoadedDependency) -> LoadedDependency:
    if key is None:
        return loaded
    if key != _acquisition_key(str(key[-2]), key[-4], key[-3]):
        return loaded
    entries = st.session_state.setdefault("acquisition_results", {})
    expires_at = (
        time.monotonic() + 1800
        if key[-2] in {"user_tags", SURFACE_MODEL_RATES}
        else loaded.frame.attrs.get("cache_expires_at", time.monotonic() + 1800)
    )
    entries[key] = (expires_at, loaded)
    while len(entries) > 24:
        entries.pop(next(iter(entries)))
    st.session_state.pop("acquisition_overview", None)
    return LoadedDependency(loaded.frame.copy(deep=True), loaded.coverage)


def _run_query(sql: str, params: tuple = (), *, snapshot_timestamp: str = "") -> pd.DataFrame:
    """Record exact cache keys for the active dependency before using cached SQL."""
    if dependency := _active_dependency.get():
        keys = st.session_state.setdefault("dependency_query_keys", {}).setdefault(dependency, [])
        cache_key = (sql, tuple(params), snapshot_timestamp)
        if cache_key not in keys:
            keys.append(cache_key)
    return _cached_query(sql, tuple(params), snapshot_timestamp=snapshot_timestamp)


@st.cache_data(ttl="30m", max_entries=64, show_spinner=False)
def _cached_query(sql: str, params: tuple = (), *, snapshot_timestamp: str = "") -> pd.DataFrame:
    """Execute SQL with qmark-bound parameters.

    Args:
        sql: Statement text, using ``?`` placeholders.
        params: Values to bind, in placeholder order.
        snapshot_timestamp: Aggregate generation captured before this read, part of the cache key.

    Returns:
        The result set with upper-cased column names and Decimal columns cast
        to float. Cached for 30 minutes; call :func:`clear_cache` to reset.
    """
    conn = _connection()
    started_at = dt.datetime.now(dt.UTC)
    st.session_state.source_query_count = st.session_state.get("source_query_count", 0) + 1
    with connection_lock():
        df = conn.query(sql, params=list(params), ttl=0, show_spinner=False)
    df.columns = [c.upper() for c in df.columns]
    result = _coerce_decimals(df)
    result.attrs.update(
        cache_expires_at=time.monotonic() + 1800,
        read_started_at=started_at,
        fetched_at=dt.datetime.now(dt.UTC),
        snapshot_timestamp=snapshot_timestamp,
    )
    return result


class CachedQuery(Protocol):
    """Public query callable with Streamlit's cache-clear interface."""

    def __call__(
        self, sql: str, params: tuple = (), *, snapshot_timestamp: str = ""
    ) -> pd.DataFrame:
        """Execute or retrieve one query result."""
        ...

    clear: Callable[..., None]


run_query = cast(CachedQuery, _run_query)
run_query.clear = _cached_query.clear


def reload_dependencies(dependencies: Sequence[str]) -> None:
    """Evict recorded active-page query keys, leaving unrelated windows and rates intact."""
    records = st.session_state.get("dependency_query_keys", {})
    warm = st.session_state.get("warm", {}).get("deps", {})
    for dependency in dependencies:
        if dependency == SURFACE_MODEL_RATES:
            continue
        for sql, params, snapshot in records.get(dependency, ()):
            _cached_query.clear(sql, params, snapshot_timestamp=snapshot)
        if dependency == "user_tags":
            load_user_tags.clear()
            _cached_query.clear(user_tags_sql(), (), snapshot_timestamp="")
        warm.pop(dependency, None)
        entries = st.session_state.get("acquisition_results", {})
        for key in list(entries):
            if key[-2] == dependency:
                entries.pop(key)
    st.session_state.pop("acquisition_overview", None)


def _coerce_decimals(df: pd.DataFrame) -> pd.DataFrame:
    """Cast Decimal columns to float.

    Snowflake NUMBER arrives as ``decimal.Decimal``, which will not combine
    with floats in pandas arithmetic.

    Args:
        df: Frame straight from the connector. Modified in place.

    Returns:
        The same frame, with object columns holding Decimals converted.
    """
    for column in df.columns:
        if df[column].dtype != object:
            continue
        values = df[column].dropna()
        if not values.empty and isinstance(values.iloc[0], decimal.Decimal):
            df[column] = df[column].astype(float)
    return df


def clear_cache() -> None:
    """Drop cached usage data so the next load re-queries Snowflake."""
    st.session_state.pop("acquisition_results", None)
    st.session_state.pop("acquisition_overview", None)
    run_query.clear()
    load_user_tags.clear()
    utc_today.clear()
    # Any cleared cache invalidates every page's warm record, so the cache gate
    # re-warms lazily on next visit rather than reading a cold cache outside the
    # modal.
    st.session_state.pop("warm", None)
    # Model Rates is deliberately NOT cleared: it holds per-model price
    # constants, not usage, and re-running it is the single most expensive query
    # in the app. It expires on its own 24h TTL.


# ---------------------------------------------------------------------------
# Source-aware loading (acceleration layer)
#
# When the Config page enables scheduled tables, loaders route to the
# materialized table instead of querying ACCOUNT_USAGE. The decision is made
# per surface via config.source_for(). On any connector error from the DT
# path, the loader falls back to ACCOUNT_USAGE and sets the degradation flag.
# ---------------------------------------------------------------------------

# Explicit column contracts for every DT-eligible surface. These are the
# uppercase column names the scheduled tables expose, in SELECT order.  The
# list is the single source of truth shared by:
#   _dt_read_sql     (read path - no SELECT *)
#   DT_BODY_REGISTRY (DtBodySpec.columns - CREATE TABLE / INSERT OVERWRITE)
# REST is absent: it is never materialised (see phase-0-outcomes.md).
_DT_COLUMNS: dict[str, tuple[str, ...]] = {
    SURFACE_COCO: (
        "SUB_SURFACE",
        "USAGE_DATE",
        "USER_NAME",
        "MODEL_NAME",
        "ROLE_NAME",
        "INFERENCE_REGION",
        "CALLS",
        "TOKENS",
        "CREDITS",
        "TOK_INPUT",
        "TOK_OUTPUT",
        "TOK_CACHE_READ_INPUT",
        "TOK_CACHE_WRITE_INPUT",
    ),
    SURFACE_AI_FUNCTIONS: (
        "USAGE_DATE",
        "USER_NAME",
        "FUNCTION_NAME",
        "MODEL_NAME",
        "QUERY_TAG",
        "CALLS",
        "TOKENS",
        "CREDITS",
        "INCOMPLETE_CALLS",
    ),
    SURFACE_AGENTS: (
        "USAGE_DATE",
        "USER_NAME",
        "AGENT_NAME",
        "AGENT_FQN",
        "AGENT_TAG_LABELS",
        "CALLS",
        "TOKENS",
        "CREDITS",
        "ROOT_REQUESTS",
        "CHILD_REQUESTS",
    ),
    SURFACE_COWORK: (
        "USAGE_DATE",
        "USER_NAME",
        "COWORK_NAME",
        "AGENT_FQN",
        "AGENT_NAME",
        "ROLE_NAME",
        "AGENT_TAG_LABELS",
        "CALLS",
        "TOKENS",
        "CREDITS",
        "SQL_QUERY_CREDITS",
        "AI_FUNCTIONS_CREDITS",
        "ROOT_REQUESTS",
        "CHILD_REQUESTS",
    ),
    f"{SURFACE_COWORK} — Service Mix": (
        "USAGE_DATE",
        "USER_NAME",
        "SERVICE_TYPE",
        "MODEL_NAME",
        "CALLS",
        "TOKENS",
        "CREDITS",
        "TOK_INPUT",
        "TOK_OUTPUT",
        "TOK_CACHE_READ_INPUT",
        "TOK_CACHE_WRITE_INPUT",
    ),
    f"{SURFACE_SEARCH} — Daily": (
        "USAGE_DATE",
        "SERVICE_NAME",
        "CONSUMPTION_TYPE",
        "MODEL_NAME",
        "CALLS",
        "TOKENS",
        "CREDITS",
    ),
    f"{SURFACE_SEARCH} — Serving": (
        "USAGE_DATE",
        "USAGE_HOUR",
        "SERVICE_NAME",
        "CALLS",
        "CREDITS",
    ),
    f"{SURFACE_SEARCH} — Batch": (
        "QUERY_ID",
        "USAGE_DATE",
        "SERVICE_NAME",
        "CONSUMPTION_TYPE",
        "MODEL_NAME",
        "TOKENS",
        "CREDITS",
    ),
    SURFACE_WAREHOUSE_ATTRIBUTION: (
        "QUERY_ID",
        "QUERY_TAG",
        "WAREHOUSE_NAME",
        "SURFACE",
        "TIER",
        "REQUEST_ID",
        "SOURCE_VIEW",
        "COMPUTE_CREDITS",
        "CLOUD_SERVICES_CREDITS",
        "WAREHOUSE_CREDITS",
        "USAGE_DATE",
    ),
    SURFACE_MODEL_RATES: (
        "MODEL_NAME",
        "R_INPUT",
        "R_OUTPUT",
        "R_CACHE_READ",
        "R_CACHE_WRITE",
    ),
}


def _dt_read_sql(surface_key: str, start_date: dt.date, end_date: dt.date) -> Statement:
    """Build a SELECT against a scheduled table for the given window.

    Filters the materialized USAGE_DATE column with the same UTC-day bounds as
    the ACCOUNT_USAGE path (half-open [start, end+1)).
    Uses an explicit column list from :data:`_DT_COLUMNS` rather than
    ``SELECT *``, so schema drift in the table never silently adds or reorders
    columns on the loaders that consume this result.
    """
    from utils.config import load_settings
    from utils.snowflake import quote_identifier, surface_to_suffix

    settings = load_settings()
    prefix = settings.object_prefix
    table_name = f"{prefix}_{surface_to_suffix(surface_key)}"

    fqn = (
        f"{quote_identifier(settings.database)}"
        f".{quote_identifier(settings.schema)}"
        f".{quote_identifier(table_name)}"
    )
    cols = ", ".join(_DT_COLUMNS[surface_key])
    sql = f"SELECT {cols} FROM {fqn} WHERE USAGE_DATE >= ?::DATE AND USAGE_DATE < ?::DATE"
    params = (
        f"{start_date:%Y-%m-%d}",
        f"{end_date + dt.timedelta(days=1):%Y-%m-%d}",
    )
    return sql, params


def _model_rates_dt_sql(database: str, schema: str, prefix: str) -> Statement:
    """Build the explicit-column read against the global Model Rates table."""
    from utils.snowflake import quote_identifier

    table_name = f"{prefix}_{SURFACE_MODEL_RATES.replace(' ', '_')}"
    fqn = f"{quote_identifier(database)}.{quote_identifier(schema)}.{quote_identifier(table_name)}"
    columns = ", ".join(_DT_COLUMNS[SURFACE_MODEL_RATES])
    return f"SELECT {columns} FROM {fqn}", ()


def _load_from_dt(surface_key: str, start_date: dt.date, end_date: dt.date) -> pd.DataFrame | None:
    """Try loading from the scheduled table. Returns None on failure."""
    import snowflake.connector.errors

    from utils.config import _health_cache, load_settings

    try:
        settings = load_settings()
        health = _health_cache(settings.database, settings.schema, settings.object_prefix)
        status = health.get(surface_key)
        snapshot = status.data_timestamp if status else ""
        stmt = _dt_read_sql(surface_key, start_date, end_date)
        return run_query(*stmt, snapshot_timestamp=snapshot)
    except (
        snowflake.connector.errors.ProgrammingError,
        snowflake.connector.errors.DatabaseError,
    ) as exc:
        _logger.warning("DT read failed for %s: %s", surface_key, exc)
        return None


def _source_aware_load(
    surface_key: str,
    start_date: dt.date,
    end_date: dt.date,
    fallback_fn: Callable[[], pd.DataFrame],
) -> pd.DataFrame:
    """Load from DT if enabled+healthy, else fall back to ACCOUNT_USAGE.

    Sets st.session_state["_degraded"][surface_key] to None on DT success,
    to a reason string on fallback, or leaves it unset if DTs are disabled.
    """
    from utils.config import load_settings, source_for

    settings = load_settings()
    if source_for(surface_key, settings) == "dt":
        aggregate_start_date, aggregate_end_date = effective_window(
            start_date,
            end_date,
            surface_key=surface_key,
            today=utc_today(),
        )
        result = _load_from_dt(surface_key, aggregate_start_date, aggregate_end_date)
        if result is not None:
            st.session_state.setdefault("_degraded", {})[surface_key] = None
            return _with_coverage(
                result,
                surface_key,
                start_date,
                end_date,
                "aggregate",
                served_end=aggregate_end_date,
            )
        st.session_state.setdefault("_degraded", {})[surface_key] = (
            "Scheduled table unavailable — reading ACCOUNT_USAGE"
        )
    return _with_coverage(fallback_fn(), surface_key, start_date, end_date, "direct")


_SOURCE_LATENCY_HOURS = {
    SURFACE_COCO: 1.0,
    SURFACE_AGENTS: 1.0,
    SURFACE_COWORK: 1.0,
    f"{SURFACE_COWORK} — Service Mix": 1.0,
    f"{SURFACE_SEARCH} — Daily": 3.0,
    f"{SURFACE_SEARCH} — Serving": 1.0,
    f"{SURFACE_SEARCH} — Batch": 1.0,
}


def _with_coverage(
    frame: pd.DataFrame,
    dependency: str,
    start: dt.date,
    end: dt.date,
    actual_source: str,
    *,
    served_end: dt.date | None = None,
    partial_reason: str | None = None,
) -> pd.DataFrame:
    """Attach evidence from the cached read, never from the latest settings or rows.

    Args:
        frame: Query result with acquisition metadata.
        dependency: Stable dependency identity.
        start: Requested UTC start.
        end: Requested UTC end.
        actual_source: Path that returned this frame, not the preferred route.
        served_end: Actual aggregate query boundary, if capped.
        partial_reason: Known source omission such as unavailable adaptive metering.

    Returns:
        A copy carrying immutable source evidence. Aggregate completeness stays
        unknown until its source-read time can be verified independently of completion.
    """
    result = frame.copy()
    fetched = frame.attrs.get("fetched_at")
    started = frame.attrs.get("read_started_at")
    snapshot_value = frame.attrs.get("snapshot_timestamp")
    parsed_snapshot = pd.to_datetime(str(snapshot_value or ""), utc=True, errors="coerce")
    snapshot = parsed_snapshot.to_pydatetime() if pd.notna(parsed_snapshot) else None
    requested = DateWindow(start, end)
    actual_end = min(end, served_end) if served_end is not None else end
    served = DateWindow(start, actual_end) if start <= actual_end else None
    latency = _SOURCE_LATENCY_HOURS.get(dependency)
    retention = None
    complete_through = None
    status = CoverageState.UNKNOWN
    reason = "Source latency or acquisition time is unknown; comparison unavailable."
    if fetched is not None:
        retention = fetched.astimezone(dt.UTC).date() - dt.timedelta(days=364)
    if dependency == SURFACE_AI_FUNCTIONS:
        retention = max(retention or dt.date(2026, 1, 5), dt.date(2026, 1, 5))
    if actual_source == "aggregate":
        reason = (
            "Task completion is known only when recorded; its source-read watermark is unverified."
        )
        retention = None
    elif (
        actual_source == "direct"
        and started is not None
        and latency is not None
        and retention is not None
    ):
        cutoff = started.astimezone(dt.UTC) - dt.timedelta(hours=latency)
        complete_through = cutoff.date() - dt.timedelta(days=1)
        status = (
            CoverageState.AVAILABLE
            if served == requested and start >= retention and end <= complete_through
            else CoverageState.PARTIAL
        )
        reason = (
            None
            if status == CoverageState.AVAILABLE
            else "Selected dates exceed supported retention or source completeness."
        )
    if partial_reason:
        status = CoverageState.PARTIAL
        reason = partial_reason
    latest_usage = None
    if "USAGE_DATE" in frame.columns and not frame.empty:
        newest = pd.to_datetime(frame["USAGE_DATE"], errors="coerce").max()
        latest_usage = newest.date() if pd.notna(newest) else None
    result.attrs["coverage"] = SourceCoverage(
        dependency=dependency,
        actual_source=actual_source,
        fetched_at=fetched,
        requested=requested,
        served=served,
        retention_start=retention,
        latency_hours=latency,
        snapshot_at=snapshot,
        state=status,
        reason=reason,
        read_started_at=started,
        complete_through=complete_through,
        latest_usage=latest_usage,
    )
    return result


def effective_window(
    start_date: dt.date, end_date: dt.date, *, surface_key: str, today: dt.date
) -> tuple[dt.date, dt.date]:
    """Resolve the date range that the selected source safely serves.

    Aggregate-backed surfaces stop on the settled UTC day; direct and global
    sources retain the user's requested window.
    """
    from utils.config import effective_served_window, load_settings, source_for

    settings = load_settings()
    return effective_served_window(
        start_date,
        end_date,
        accelerated=source_for(surface_key, settings) == "dt",
        today=today,
    )


def window_statements(
    start_date: dt.date, end_date: dt.date, dependencies: tuple[str, ...] | None = None
) -> list[Statement]:
    """Build every statement a cold start needs, bar the Cortex Inference API one.

    That one is excluded because it binds the model rates as literals, so it
    cannot be built until :func:`load_model_rates` has returned. The caller
    (``_warm_caches`` in ``streamlit_app.py``) issues it in a second pass.

    When the acceleration toggle is on, surfaces routed to scheduled tables warm
    via ``_dt_read_sql`` instead of the ACCOUNT_USAGE builder.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.
        dependencies: Dependency keys to build statements for. None builds the
            full cold-start set. When given, only matching statements are
            returned, plus the metering probe when Warehouse Attribution is
            requested.

    Returns:
        Statements safe to run concurrently, in no particular order.
    """
    from utils.config import load_settings, source_for

    settings = load_settings()

    def _stmt(surface_key: str, au_builder: Callable[..., Statement]) -> Statement:
        if source_for(surface_key, settings) == "dt":
            aggregate_start_date, aggregate_end_date = effective_window(
                start_date,
                end_date,
                surface_key=surface_key,
                today=utc_today(),
            )
            return _dt_read_sql(surface_key, aggregate_start_date, aggregate_end_date)
        return au_builder(start_date, end_date)

    statements: dict[str, Statement] = {
        SURFACE_COCO: _stmt(SURFACE_COCO, coco_facts_sql),
        SURFACE_COWORK: _stmt(SURFACE_COWORK, cowork_facts_sql),
        SURFACE_AGENTS: _stmt(SURFACE_AGENTS, agent_facts_sql),
        SURFACE_AI_FUNCTIONS: _stmt(SURFACE_AI_FUNCTIONS, ai_fn_facts_sql),
        f"{SURFACE_COWORK} — Service Mix": _stmt(
            f"{SURFACE_COWORK} — Service Mix", cowork_service_mix_sql
        ),
        f"{SURFACE_SEARCH} — Daily": _stmt(f"{SURFACE_SEARCH} — Daily", search_daily_facts_sql),
        f"{SURFACE_SEARCH} — Serving": _stmt(
            f"{SURFACE_SEARCH} — Serving", search_serving_facts_sql
        ),
        f"{SURFACE_SEARCH} — Batch": _stmt(f"{SURFACE_SEARCH} — Batch", search_batch_facts_sql),
        SURFACE_WAREHOUSE_ATTRIBUTION: _stmt(
            SURFACE_WAREHOUSE_ATTRIBUTION, warehouse_credit_facts_sql
        ),
        "metering_probe": metering_probe_sql(),
        "user_tags": (user_tags_sql(), ()),
    }
    if dependencies is None:
        return list(statements.values())

    selected = [statements[key] for key in dependencies if key in statements]
    if SURFACE_WAREHOUSE_ATTRIBUTION in dependencies:
        selected.append(statements["metering_probe"])
    return selected


# ---------------------------------------------------------------------------
# Day boundaries
#
# ACCOUNT_USAGE mixes timestamp types: the CoCo and REST API views use
# TIMESTAMP_TZ, which casts to DATE using the offset stored on each row (UTC in
# practice), while CORTEX_AI_FUNCTIONS_USAGE_HISTORY uses TIMESTAMP_LTZ, which
# casts in the *session* timezone. A bare ::DATE therefore means different things
# per view, and shifts per viewer: on a -0700 session, 523 of 2,926 CoCo
# rows and 7 of 98 AI Function rows land on different days depending on which
# rule applies.
#
# Every surface is pinned to UTC calendar days instead, and the window bounds
# come from Snowflake's own UTC clock rather than the app container's, so the
# same window returns the same rows for every user.
# ---------------------------------------------------------------------------


def utc_day(column: str) -> str:
    """Build the SQL expression for the UTC calendar day of a timestamp column.

    Args:
        column: Column reference, optionally table-qualified.

    Returns:
        A SQL expression yielding a DATE.
    """
    return f"CONVERT_TIMEZONE('UTC', {column})::DATE"


def utc_window(column: str) -> str:
    """Build the half-open UTC window predicate for a timestamp column.

    Equivalent to ``CONVERT_TIMEZONE('UTC', col)::DATE BETWEEN start AND end``,
    but comparing the raw column against bound TIMESTAMP_TZ literals instead of
    wrapping it in functions, which keeps the filter sargable. Verified to
    return identical rows to the wrapped form (1,478 rows / 77,651,062 tokens on
    a 7-day CoCo window). Measured gain is modest -- about 10% on these views,
    which report no BYTES_SCANNED -- but it costs nothing.

    Args:
        column: Column reference, optionally table-qualified.

    Returns:
        A predicate with two ``?`` placeholders, whose values come from
        :func:`_window_params`.
    """
    return f"{column} >= ?::TIMESTAMP_TZ AND {column} < ?::TIMESTAMP_TZ"


def _window_params(start_date: dt.date, end_date: dt.date) -> list[str]:
    """Build the bounds to bind to a :func:`utc_window` predicate.

    The single place window bounds are produced, so builders never assemble
    them by hand.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        Midnight UTC on ``start_date``, then midnight UTC on the day after
        ``end_date``, as TIMESTAMP_TZ literals.
    """
    return [
        f"{start_date:%Y-%m-%d} 00:00:00 +0000",
        f"{end_date + dt.timedelta(days=1):%Y-%m-%d} 00:00:00 +0000",
    ]


@st.cache_data(ttl="5m", show_spinner=False)
def utc_today() -> dt.date:
    """Return today in UTC, as Snowflake sees it.

    Deliberately not ``dt.date.today()``: the app container's clock and timezone
    are not guaranteed to match the data. ``SYSDATE()`` is UTC by definition, so
    this agrees with :func:`utc_day`. It is a metadata-only query, which is why
    it is allowed to run before the cache-warming modal opens.

    Returns:
        The current UTC date.
    """
    with connection_lock():
        frame = _connection().query("SELECT SYSDATE()::DATE AS UTC_TODAY", ttl=0)
    return pd.to_datetime(frame["UTC_TODAY"].iloc[0]).date()


# ---------------------------------------------------------------------------
# Model credit rates
#
# CORTEX_REST_API_USAGE_HISTORY reports tokens but no credit column, so REST
# credits are estimated by applying per-model, per-token-type rates observed in
# CoCo history, which reports both TOKENS_GRANULAR and CREDITS_GRANULAR.
# Observed values land exactly on the published per-1M-token rates, so the
# estimate is precise for any model with a known rate.
#
# Every CoCo row carries exactly one model in TOKENS_GRANULAR, so the
# model and per-token-type counts are read as scalars via OBJECT_KEYS(...)[0]
# rather than LATERAL FLATTEN. That keeps model a plain filterable column and
# avoids row multiplication everywhere.
# ---------------------------------------------------------------------------


@st.cache_data(ttl="24h", show_spinner=False)
def _load_model_rates_cached(
    mode: str,
    snapshot_timestamp: str,
    database: str,
    schema: str,
    prefix: str,
) -> pd.DataFrame:
    """Load Model Rates from its selected source, keyed by aggregate generation."""
    if mode == "aggregate":
        result = run_query(
            *_model_rates_dt_sql(database, schema, prefix),
            snapshot_timestamp=snapshot_timestamp,
        )
    else:
        result = run_query(*model_rates_sql())
    result.attrs["actual_source"] = mode
    return result


def load_model_rates() -> pd.DataFrame:
    """Load observed credits per token, by model and token type.

    Scanned once and reused as a bound literal so the scan stays out of every
    page query.

    Cached for 24h and deliberately NOT cleared by :func:`clear_cache`: these
    are per-model price constants, not usage, so the Refresh button has no
    reason to re-run the most expensive query in the app.

    Returns:
        One row per model, with a MODEL_NAME column and the four
        :data:`RATE_COLUMNS`. A rate is NULL where that token type was never
        observed for the model.
    """
    from utils.config import _health_cache, load_settings, source_for

    settings = load_settings()
    if source_for(SURFACE_MODEL_RATES, settings) == "dt":
        status = _health_cache(settings.database, settings.schema, settings.object_prefix).get(
            SURFACE_MODEL_RATES
        )
        snapshot_timestamp = status.data_timestamp if status else ""
        try:
            rates = _load_model_rates_cached(
                "aggregate",
                snapshot_timestamp,
                settings.database,
                settings.schema,
                settings.object_prefix,
            )
        except Exception as exc:  # noqa: BLE001 - preserve exact live fallback on read failure
            _logger.warning("Model Rates aggregate read failed: %s", exc)
        else:
            if not rates.empty:
                st.session_state.setdefault("_degraded", {})[SURFACE_MODEL_RATES] = None
                return rates
            _logger.warning("Model Rates aggregate read returned no rows")
        st.session_state.setdefault("_degraded", {})[SURFACE_MODEL_RATES] = (
            "Scheduled table unavailable — reading ACCOUNT_USAGE"
        )

    return _load_model_rates_cached(
        "direct", "", settings.database, settings.schema, settings.object_prefix
    )


def model_rates_sql() -> Statement:
    """Build the observed credits-per-token query, over a bounded recent window.

    Bounding matters: unbounded, this scans all history in the unified CoCo view and
    was measured at 15-33s, the slowest query in the app by a wide margin. Rates
    are per-model constants that land exactly on the published per-1M-token
    rates, so a recent sample is as accurate as full history for any model in
    current use.

    The cost of bounding is coverage: a model that appears in Cortex Inference
    API usage but has not been seen in CoCo within the window has no rate, so
    its tokens fall into the unrated bucket and contribute nothing to the
    estimate. That is visible -- the Cortex Inference API page reports
    rated-token coverage as a percentage -- so widen :data:`RATES_WINDOW_DAYS`
    if it drops below ~100%.

    The bound is a date literal from :func:`utc_today` rather than ``SYSDATE()``
    so the SQL text is stable for the whole UTC day, which keeps it eligible for
    Snowflake's result cache.

    Returns:
        The statement, with one lower bound for the unified CoCo view.
    """
    since = utc_today() - dt.timedelta(days=RATES_WINDOW_DAYS)
    since_param = f"{since:%Y-%m-%d} 00:00:00 +0000"
    sql = f"""
WITH coco_granular AS (
    SELECT
        token_model.value AS tg,
        credit_model.value AS cg,
        token_model.key::STRING AS model_name
    FROM {AU}.{COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE h.USAGE_TIME >= ?::TIMESTAMP_TZ
      AND token_model.key = credit_model.key
)
SELECT
    model_name,
    SUM(cg:input::FLOAT)             / NULLIF(SUM(tg:input::NUMBER), 0)             AS r_input,
    SUM(cg:output::FLOAT)            / NULLIF(SUM(tg:output::NUMBER), 0)            AS r_output,
    SUM(cg:cache_read_input::FLOAT)  / NULLIF(SUM(tg:cache_read_input::NUMBER), 0)  AS r_cache_read,
    SUM(cg:cache_write_input::FLOAT) / NULLIF(SUM(tg:cache_write_input::NUMBER), 0) AS r_cache_write
FROM coco_granular
WHERE model_name IS NOT NULL
GROUP BY model_name
"""
    return sql, (since_param,)


def model_rates_body_sql() -> str:
    """Build the parameterless Model Rates aggregate body with a 90-day UTC cutoff."""
    return f"""
WITH coco_granular AS (
    SELECT
        token_model.value AS tg,
        credit_model.value AS cg,
        token_model.key::STRING AS model_name
    FROM {AU}.{COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE h.USAGE_TIME >= DATE_TRUNC(
        'day', DATEADD('day', -{RATES_WINDOW_DAYS}, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
    )
      AND token_model.key = credit_model.key
)
SELECT
    model_name,
    SUM(cg:input::FLOAT)             / NULLIF(SUM(tg:input::NUMBER), 0)             AS r_input,
    SUM(cg:output::FLOAT)            / NULLIF(SUM(tg:output::NUMBER), 0)            AS r_output,
    SUM(cg:cache_read_input::FLOAT)  / NULLIF(SUM(tg:cache_read_input::NUMBER), 0)  AS r_cache_read,
    SUM(cg:cache_write_input::FLOAT) / NULLIF(SUM(tg:cache_write_input::NUMBER), 0) AS r_cache_write
FROM coco_granular
WHERE model_name IS NOT NULL
GROUP BY model_name
"""


def _rates_cte(rates: pd.DataFrame | None = None) -> tuple[str, list]:
    """Build the model_rates CTE from the cached rates, with every value bound.

    Returns:
        The CTE text and the values to bind for it, in placeholder order.
        Models whose every rate is NULL are dropped; if that leaves nothing, the
        CTE is a typed empty result so the join downstream still compiles.
    """
    if rates is None:
        rates = load_model_rates()
    params: list = []
    rows = []
    for _, row in rates.iterrows():
        values = [row["MODEL_NAME"]] + [
            None if pd.isna(row[c]) else float(row[c]) for c in RATE_COLUMNS
        ]
        if all(v is None for v in values[1:]):
            continue
        params.extend(values)
        rows.append("(?, ?, ?, ?, ?)")

    if not rows:
        return (
            """
model_rates AS (
    SELECT NULL::STRING AS model_name, NULL::FLOAT AS r_input, NULL::FLOAT AS r_output,
           NULL::FLOAT AS r_cache_read, NULL::FLOAT AS r_cache_write
    WHERE 1 = 0
)""",
            params,
        )

    return (
        f"""
model_rates AS (
    SELECT
        column1::STRING AS model_name,
        column2::FLOAT  AS r_input,
        column3::FLOAT  AS r_output,
        column4::FLOAT  AS r_cache_read,
        column5::FLOAT  AS r_cache_write
    FROM VALUES
        {", ".join(rows)}
)""",
        params,
    )


_REST_EST_CREDITS = """
      NVL(r.TOKENS_GRANULAR:input::NUMBER, 0)             * NVL(w.r_input, 0)
    + NVL(r.TOKENS_GRANULAR:output::NUMBER, 0)            * NVL(w.r_output, 0)
    + NVL(r.TOKENS_GRANULAR:cache_read_input::NUMBER, 0)  * NVL(w.r_cache_read, 0)
    + NVL(r.TOKENS_GRANULAR:cache_write_input::NUMBER, 0) * NVL(w.r_cache_write, 0)"""

_AI_FN_TOKENS = """
    NVL(REDUCE(
        FILTER(a.METRICS, x -> x:key:unit::STRING = 'tokens'),
        0,
        (acc, x) -> acc + x:value::NUMBER
    ), 0)"""


# ---------------------------------------------------------------------------
# Fact loaders -- one query per page, filtered only by date
# ---------------------------------------------------------------------------


def coco_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the CoCo fact query.

    The unified CoCo view is scanned once. Granular token and credit objects are
    paired by model key, so each emitted row has one model's measures. Calls
    remain model-attributed, while token and credit totals remain additive.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per delivery surface, day, user, model, role and
        inference region, with per-token-type totals.
    """
    token_sums = ",\n".join(
        f"    SUM(NVL(tg:{t}::NUMBER, 0)) AS {TOKEN_TYPE_COLUMNS[t].lower()}" for t in TOKEN_TYPES
    )
    sql = f"""
WITH coco_granular AS (
    SELECT
        LOWER(h.INTERFACE) AS sub_surface,
        {utc_day("h.USAGE_TIME")} AS usage_date,
        NVL(h.USER_NAME, '{UNKNOWN}') AS user_name,
        NVL(h.METADATA:role_name::STRING, '{UNKNOWN}') AS role_name,
        NVL(h.METADATA:inference_region::STRING, '{UNKNOWN}') AS inference_region,
        token_model.key::STRING AS model_name,
        token_model.value AS tg,
        credit_model.value AS cg
    FROM {AU}.{COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE {utc_window("h.USAGE_TIME")}
      AND token_model.key = credit_model.key
)
SELECT
    sub_surface,
    usage_date,
    user_name,
    model_name,
    role_name,
    inference_region,
    COUNT(*)     AS calls,
    SUM(NVL(tg:input::NUMBER, 0) + NVL(tg:output::NUMBER, 0) +
        NVL(tg:cache_read_input::NUMBER, 0) + NVL(tg:cache_write_input::NUMBER, 0)) AS tokens,
    SUM(NVL(cg:input::FLOAT, 0) + NVL(cg:output::FLOAT, 0) +
        NVL(cg:cache_read_input::FLOAT, 0) + NVL(cg:cache_write_input::FLOAT, 0)) AS credits,
{token_sums}
FROM coco_granular
GROUP BY ALL
"""
    return sql, tuple(_window_params(start_date, end_date))


def load_coco_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load CoCo usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`coco_facts_sql`.
    """
    return _source_aware_load(
        SURFACE_COCO,
        start_date,
        end_date,
        lambda: run_query(*coco_facts_sql(start_date, end_date)),
    )


def ai_fn_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the Cortex AI Functions fact query.

    Credits come straight from the view. Tokens are summed out of the METRICS
    array, filtered to entries whose unit is ``tokens``, so functions billed per
    page or per row report credits with zero tokens.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per day, user, function, model and query tag.
    """
    params = _window_params(start_date, end_date)
    sql = f"""
SELECT
    {utc_day("a.START_TIME")}          AS usage_date,
    NVL(u.NAME, '{UNKNOWN}')                     AS user_name,
    NVL(a.FUNCTION_NAME, '{UNKNOWN}')            AS function_name,
    NVL(a.MODEL_NAME, '{UNKNOWN}')               AS model_name,
    NVL(NULLIF(a.QUERY_TAG, ''), '{NONE_LABEL}') AS query_tag,
    COUNT(*)                                    AS calls,
    SUM({_AI_FN_TOKENS.strip()}) AS tokens,
    SUM(NVL(a.CREDITS, 0))                      AS credits,
    SUM(CASE WHEN a.IS_COMPLETED = FALSE THEN 1 ELSE 0 END) AS incomplete_calls
FROM {AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY a
LEFT JOIN {AU}.USERS u ON u.USER_ID = a.USER_ID
WHERE {utc_window("a.START_TIME")}
GROUP BY ALL
"""
    return sql, tuple(params)


def load_ai_fn_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load Cortex AI Functions usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`ai_fn_facts_sql`.
    """
    return _source_aware_load(
        SURFACE_AI_FUNCTIONS,
        start_date,
        end_date,
        lambda: run_query(*ai_fn_facts_sql(start_date, end_date)),
    )


def rest_facts_sql(
    start_date: dt.date, end_date: dt.date, *, rates: pd.DataFrame | None = None
) -> Statement:
    """Build the Cortex Inference API fact query, with credits estimated.

    This view reports tokens but no credits, so credits are estimated by
    applying per-model, per-token-type rates in the model_rates CTE. Those rates
    are observed from CoCo history and bound as literals, which is why the
    statement can only be built after :func:`load_model_rates` has returned.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.
        rates: Captured rate snapshot shared with coverage checks, or load it on demand.

    Returns:
        The statement. One row per day, user, model, inference region and query
        tag, carrying an IS_UNRATED flag for rows whose model had no rate.
    """
    rates_sql, params = _rates_cte(rates)
    params.extend(_window_params(start_date, end_date))
    sql = f"""WITH {rates_sql}
SELECT
    {utc_day("r.START_TIME")}                AS usage_date,
    NVL(u.NAME, '{UNKNOWN}')                           AS user_name,
    NVL(r.MODEL_NAME, '{UNKNOWN}')                     AS model_name,
    NVL(NULLIF(r.INFERENCE_REGION, ''), '{UNKNOWN}')   AS inference_region,
    NVL(NULLIF(r.QUERY_TAG, ''), '{NONE_LABEL}')       AS query_tag,
    COUNT(*)                                          AS calls,
    SUM(NVL(r.TOKENS, 0))                             AS tokens,
    SUM({_REST_EST_CREDITS.strip()}) AS credits,
    SUM(NVL(r.TOKENS_GRANULAR:input::NUMBER, 0))             AS tok_input,
    SUM(NVL(r.TOKENS_GRANULAR:output::NUMBER, 0))            AS tok_output,
    SUM(NVL(r.TOKENS_GRANULAR:cache_read_input::NUMBER, 0))  AS tok_cache_read_input,
    SUM(NVL(r.TOKENS_GRANULAR:cache_write_input::NUMBER, 0)) AS tok_cache_write_input,
    MAX(CASE WHEN w.model_name IS NULL THEN 1 ELSE 0 END)    AS is_unrated
FROM {AU}.CORTEX_REST_API_USAGE_HISTORY r
LEFT JOIN {AU}.USERS u ON u.USER_ID = r.USER_ID
LEFT JOIN model_rates w ON w.model_name = r.MODEL_NAME
WHERE {utc_window("r.START_TIME")}
GROUP BY ALL
"""
    return sql, tuple(params)


def load_rest_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load Cortex Inference API usage at fact grain, with estimated credits.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`rest_facts_sql`.
    """
    rates = load_model_rates()
    return _with_coverage(
        _with_rate_coverage(run_query(*rest_facts_sql(start_date, end_date, rates=rates)), rates),
        SURFACE_REST_API,
        start_date,
        end_date,
        "direct",
    )


def _with_rate_coverage(frame: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    """Count unrated token types against the exact snapshot used by the query."""
    result = frame.copy()
    rate_index = rates.set_index("MODEL_NAME")
    unrated = pd.Series(0.0, index=result.index)
    accounted = pd.Series(0.0, index=result.index)
    for token_column, rate_column in zip(TOKEN_TYPE_COLUMNS.values(), RATE_COLUMNS, strict=True):
        if token_column not in result:
            continue
        tokens = result[token_column].fillna(0)
        known = result["MODEL_NAME"].map(rate_index[rate_column]).notna()
        unrated += tokens.where(~known, 0)
        accounted += tokens
    unrated += (result["TOKENS"].fillna(0) - accounted).clip(lower=0)
    result["UNRATED_TOKENS"] = unrated
    result["RATE_INCOMPLETE"] = unrated.gt(0)
    result.attrs["rate_snapshot_at"] = rates.attrs.get("fetched_at")
    return result


def agent_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the Cortex Agents fact query, splitting root from child requests.

    ``PARENT_REQUEST_ID`` separates root requests (one per user turn) from the
    child requests the agent spawns while servicing that turn.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per day, user and agent, with agent tags
        pipe-joined into a single column for :func:`utils.agg.by_agent_tag`.
    """
    params = _window_params(start_date, end_date)
    sql = f"""
SELECT
    {utc_day("START_TIME")}            AS usage_date,
    NVL(USER_NAME, '{UNKNOWN}')                  AS user_name,
    NVL(AGENT_NAME, '{UNKNOWN}')                 AS agent_name,
    NVL(AGENT_DATABASE_NAME, '{UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '{UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '{UNKNOWN}')                 AS agent_fqn,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                      AS agent_tag_labels,
    COUNT(*)                                    AS calls,
    SUM(NVL(TOKENS, 0))                         AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                  AS credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM {AU}.CORTEX_AGENT_USAGE_HISTORY
WHERE {utc_window("START_TIME")}
GROUP BY ALL
"""
    return sql, tuple(params)


def load_agent_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load Cortex Agents usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`agent_facts_sql`.
    """
    return _source_aware_load(
        SURFACE_AGENTS,
        start_date,
        end_date,
        lambda: run_query(*agent_facts_sql(start_date, end_date)),
    )


# ---------------------------------------------------------------------------
# Snowflake CoWork
#
# SNOWFLAKE_COWORK_USAGE_HISTORY records one row per CoWork message request. Per
# the docs, requests originating from Cortex Agents are recorded in
# CORTEX_AGENT_USAGE_HISTORY instead, so the two views do not overlap and can be
# summed without double counting.
#
# Two things make this view shaped differently from every other surface:
#
# 1. TOKENS_GRANULAR / CREDITS_GRANULAR are ARRAYs (not OBJECTs as on Cortex
#    Code), nested request_id -> service_type -> model -> token type. The service
#    type dimension is unique to CoWork: a single user turn fans out to
#    cortex_agents plus tool calls such as cortex_analyst, so the split is the
#    most informative breakdown on the page. It needs FLATTEN, which would
#    multiply rows, so it is loaded separately from the fact grain.
# 2. Token credits are not the whole bill. METADATA carries sql_query_credits
#    (warehouse compute for SQL the agent ran) and ai_functions_credits, which
#    are surfaced as their own columns rather than folded into CREDITS silently.
# ---------------------------------------------------------------------------


def cowork_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the CoWork fact query, keeping SQL and AI function credits separate.

    METADATA carries ``sql_query_credits`` and ``ai_functions_credits``, which
    are billed on top of TOKEN_CREDITS, so they are kept as their own columns
    rather than folded into credits.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per day, user, CoWork name, agent and role.
    """
    sql = f"""
SELECT
    {utc_day("START_TIME")}                     AS usage_date,
    NVL(USER_NAME, '{UNKNOWN}')                  AS user_name,
    NVL(SNOWFLAKE_COWORK_NAME, '{UNKNOWN}')      AS cowork_name,
    NVL(AGENT_DATABASE_NAME, '{UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '{UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '{UNKNOWN}')                 AS agent_fqn,
    NVL(AGENT_NAME, '{UNKNOWN}')                 AS agent_name,
    NVL(METADATA:role_name::STRING, '{UNKNOWN}') AS role_name,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                      AS agent_tag_labels,
    COUNT(*)                                    AS calls,
    SUM(NVL(TOKENS, 0))                         AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                  AS credits,
    SUM(NVL(METADATA:sql_query_credits::FLOAT, 0))    AS sql_query_credits,
    SUM(NVL(METADATA:ai_functions_credits::FLOAT, 0)) AS ai_functions_credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM {AU}.SNOWFLAKE_COWORK_USAGE_HISTORY
WHERE {utc_window("START_TIME")}
GROUP BY ALL
"""
    return sql, tuple(_window_params(start_date, end_date))


def load_cowork_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load CoWork usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`cowork_facts_sql`.
    """
    return _source_aware_load(
        SURFACE_COWORK,
        start_date,
        end_date,
        lambda: run_query(*cowork_facts_sql(start_date, end_date)),
    )


def cowork_service_mix_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the CoWork tokens and credits query, per service type and model.

    Four FLATTEN levels: array entry -> request_id -> service_type -> model. The
    ``start_time`` key sits alongside the service types at the third level and
    is filtered out. CREDITS_GRANULAR mirrors the same shape, so it is joined
    back on the same coordinates rather than flattened a second time
    independently.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. The window bounds are bound twice, once per CTE.
    """
    token_sums = ",\n".join(
        f"    SUM(NVL(m.value:{t}::NUMBER, 0)) AS {TOKEN_TYPE_COLUMNS[t].lower()}"
        for t in TOKEN_TYPES
    )
    credit_sums = " + ".join(f"NVL(cm.value:{t}::FLOAT, 0)" for t in TOKEN_TYPES)
    total_tokens = " + ".join(TOKEN_TYPE_COLUMNS[t].lower() for t in TOKEN_TYPES)
    outer_token_sums = ",\n".join(
        f"    SUM(t.{TOKEN_TYPE_COLUMNS[t].lower()}) AS {TOKEN_TYPE_COLUMNS[t].lower()}"
        for t in TOKEN_TYPES
    )
    sql = f"""
WITH tokens AS (
    SELECT
        {utc_day("h.START_TIME")} AS usage_date,
        NVL(h.USER_NAME, '{UNKNOWN}') AS user_name,
        rq.key   AS request_id,
        s.key    AS service_type,
        m.key    AS model_name,
{token_sums}
    FROM {AU}.SNOWFLAKE_COWORK_USAGE_HISTORY h,
         LATERAL FLATTEN(input => h.TOKENS_GRANULAR) e,
         LATERAL FLATTEN(input => e.value) rq,
         LATERAL FLATTEN(input => rq.value) s,
         LATERAL FLATTEN(input => s.value) m
    WHERE {utc_window("h.START_TIME")}
      AND s.key <> 'start_time'
    GROUP BY ALL
),
credits AS (
    SELECT
        rq.key AS request_id,
        s.key  AS service_type,
        cm.key AS model_name,
        SUM({credit_sums}) AS credits
    FROM {AU}.SNOWFLAKE_COWORK_USAGE_HISTORY h,
         LATERAL FLATTEN(input => h.CREDITS_GRANULAR) e,
         LATERAL FLATTEN(input => e.value) rq,
         LATERAL FLATTEN(input => rq.value) s,
         LATERAL FLATTEN(input => s.value) cm
    WHERE {utc_window("h.START_TIME")}
      AND s.key <> 'start_time'
    GROUP BY ALL
)
SELECT
    t.usage_date,
    t.user_name,
    t.service_type,
    t.model_name,
    COUNT(*)                     AS calls,
    SUM({total_tokens}) AS tokens,
    SUM(NVL(c.credits, 0))       AS credits,
{outer_token_sums}
FROM tokens t
LEFT JOIN credits c
       ON c.request_id = t.request_id
      AND c.service_type = t.service_type
      AND c.model_name = t.model_name
GROUP BY ALL
"""
    params = tuple(_window_params(start_date, end_date)) * 2
    return sql, params


def load_cowork_service_mix(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load CoWork tokens and credits split by service type and model.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`cowork_service_mix_sql`.
    """
    return _source_aware_load(
        f"{SURFACE_COWORK} — Service Mix",
        start_date,
        end_date,
        lambda: run_query(*cowork_service_mix_sql(start_date, end_date)),
    )


# ---------------------------------------------------------------------------
# Cortex Search
#
# Three separate loaders, not unioned: the three views have different grains
# and column names (CREDITS vs CREDITS_USED; TIMESTAMP_LTZ vs TIMESTAMP_TZ; no
# USER_NAME at all -- see the Search-in-unified note in app_pages/cortex_search.py).
# Search has no batch index-refresh attribution mechanism on any account
# checked (Phase 0 Task 0.1): no QUERY_TAG or WAREHOUSE_NAME resolves to a
# named Search service, so that cost is a documented gap, not a guessed number.
# ---------------------------------------------------------------------------


def search_daily_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the Cortex Search daily-usage fact query.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per day, service, consumption type and model.
        USAGE_DATE is TIMESTAMP_LTZ on this view (not DATE, unlike every other
        surface), so it is bucketed through :func:`utc_day` like the rest.
    """
    params = _window_params(start_date, end_date)
    sql = f"""
SELECT
    {utc_day("USAGE_DATE")}                       AS usage_date,
    NVL(SERVICE_NAME, '{UNKNOWN}')                AS service_name,
    NVL(CONSUMPTION_TYPE, '{UNKNOWN}')             AS consumption_type,
    NVL(MODEL_NAME, '{NONE_LABEL}')                AS model_name,
    COUNT(*)                                       AS calls,
    SUM(TRY_CAST(TOKENS AS NUMBER))                AS tokens,
    SUM(NVL(CREDITS, 0))                            AS credits
FROM {AU}.CORTEX_SEARCH_DAILY_USAGE_HISTORY
WHERE {utc_window("USAGE_DATE")}
GROUP BY ALL
"""
    return sql, tuple(params)


def load_search_daily_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load Cortex Search daily usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`search_daily_facts_sql`.
    """
    return _source_aware_load(
        f"{SURFACE_SEARCH} — Daily",
        start_date,
        end_date,
        lambda: run_query(*search_daily_facts_sql(start_date, end_date)),
    )


def search_serving_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the Cortex Search hourly-serving fact query.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per hour and service. START_TIME is
        TIMESTAMP_LTZ on this view; the hour bucket is computed from the UTC
        conversion, matching :func:`utc_day`'s convention.
    """
    params = _window_params(start_date, end_date)
    sql = f"""
SELECT
    {utc_day("START_TIME")}                                          AS usage_date,
    DATE_TRUNC('hour', CONVERT_TIMEZONE('UTC', START_TIME))          AS usage_hour,
    NVL(SERVICE_NAME, '{UNKNOWN}')                                    AS service_name,
    COUNT(*)                                                          AS calls,
    SUM(NVL(CREDITS, 0))                                              AS credits
FROM {AU}.CORTEX_SEARCH_SERVING_USAGE_HISTORY
WHERE {utc_window("START_TIME")}
GROUP BY ALL
"""
    return sql, tuple(params)


def load_search_serving_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load Cortex Search hourly serving usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`search_serving_facts_sql`.
    """
    return _source_aware_load(
        f"{SURFACE_SEARCH} — Serving",
        start_date,
        end_date,
        lambda: run_query(*search_serving_facts_sql(start_date, end_date)),
    )


def search_batch_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the Cortex Search batch-query fact query.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. One row per QUERY_ID, service, consumption type
        (BATCH_SERVING / BATCH_EMBED_TEXT_TOKENS) and model -- a single
        QUERY_ID can have more than one row here, so the batch warehouse-cost
        join (app_pages/cortex_search.py) aggregates to one row per QUERY_ID
        before joining warehouse_credit_facts, per the batch grain invariant
        (§5.3, §7, AC7). Credits are CREDITS_USED here, not CREDITS; START_TIME
        is TIMESTAMP_TZ here, not TIMESTAMP_LTZ like the other two views.
    """
    params = _window_params(start_date, end_date)
    sql = f"""
SELECT
    QUERY_ID,
    {utc_day("START_TIME")}                       AS usage_date,
    NVL(SERVICE_NAME, '{UNKNOWN}')                AS service_name,
    NVL(CONSUMPTION_TYPE, '{UNKNOWN}')             AS consumption_type,
    NVL(MODEL_NAME, '{NONE_LABEL}')                AS model_name,
    SUM(TRY_CAST(TOKENS AS NUMBER))                AS tokens,
    SUM(NVL(CREDITS_USED, 0))                      AS credits
FROM {AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
WHERE {utc_window("START_TIME")}
GROUP BY ALL
"""
    return sql, tuple(params)


def load_search_batch_facts(start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Load Cortex Search batch-query usage at fact grain.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The result of :func:`search_batch_facts_sql`.
    """
    return _source_aware_load(
        f"{SURFACE_SEARCH} — Batch",
        start_date,
        end_date,
        lambda: run_query(*search_batch_facts_sql(start_date, end_date)),
    )


@st.cache_data(ttl="1h", show_spinner=False)
def load_user_tags() -> pd.DataFrame:
    """Load current USER-domain tags.

    The per-row USER_TAGS column on the usage views is populated on a tiny
    fraction of rows, so TAG_REFERENCES is the only source that makes tag
    filtering work consistently across every surface.

    Returns:
        One row per tag reference, with TAG_NAME, TAG_VALUE, TAG_LABEL and
        USER_NAME columns.
    """
    return run_query(user_tags_sql())


def user_tags_sql() -> str:
    """Build the query for current USER-domain tag references.

    Returns:
        The SQL text. Unlike the fact builders this takes no parameters, so it
        is a bare string rather than a :data:`Statement`.
    """
    return f"""
SELECT
    TAG_NAME  AS tag_name,
    TAG_VALUE AS tag_value,
    TAG_NAME || ' = ' || TAG_VALUE AS tag_label,
    OBJECT_NAME AS user_name
FROM {AU}.TAG_REFERENCES
WHERE DOMAIN = 'USER'
  AND TAG_VALUE IS NOT NULL
ORDER BY tag_name, tag_value, user_name
"""


# ---------------------------------------------------------------------------
# Unified facts for the landing page
# ---------------------------------------------------------------------------

_METRIC_COLUMNS = ("CALLS", "TOKENS", "CREDITS")

# Column order of the frame load_unified_facts returns, populated or empty.
_UNIFIED_COLUMNS = (
    "USAGE_DATE",
    "USER_NAME",
    "MODEL_NAME",
    "SUB_SURFACE",
    "SURFACE",
    "CREDITS_ESTIMATED",
    "RATE_INCOMPLETE",
    "UNRATED_TOKENS",
    *_METRIC_COLUMNS,
)


class _UnifiedSource(NamedTuple):
    """One surface's contribution to the unified overview frame.

    Attributes:
        surface: Value written into the SURFACE column.
        loader: Fact loader for the surface.
        dimensions: Dimension columns the loader provides beyond USAGE_DATE and
            USER_NAME. Any unified dimension missing from this list is filled
            with None, so the frames stack.
        credits_estimated: Whether the surface's credits are derived rather
            than reported by Snowflake.
    """

    surface: str
    loader: Callable[[dt.date, dt.date], pd.DataFrame]
    dimensions: tuple[str, ...]
    credits_estimated: bool


# CoWork is additive, not a duplicate: the docs state that requests originating
# from Cortex Agents are recorded in CORTEX_AGENT_USAGE_HISTORY and excluded
# from that view. Only token credits roll up here so the surface comparison
# stays like-for-like; the CoWork page shows the SQL-warehouse credits that the
# view reports separately.
_UNIFIED_SOURCES = (
    _UnifiedSource(SURFACE_COCO, load_coco_facts, ("MODEL_NAME", "SUB_SURFACE"), False),
    _UnifiedSource(SURFACE_AI_FUNCTIONS, load_ai_fn_facts, ("MODEL_NAME",), False),
    _UnifiedSource(SURFACE_REST_API, load_rest_facts, ("MODEL_NAME",), True),
    _UnifiedSource(SURFACE_AGENTS, load_agent_facts, (), False),
    _UnifiedSource(SURFACE_COWORK, load_cowork_facts, (), False),
    _UnifiedSource(SURFACE_SEARCH, load_search_daily_facts, ("MODEL_NAME",), False),
)


def load_unified_facts(
    start_date: dt.date, end_date: dt.date, *, direct_only: bool = False
) -> pd.DataFrame:
    """Load every surface normalised onto one frame for the overview page.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.
        direct_only: Bypass aggregate routing for an explicitly requested partial-day view.

    Returns:
        The concatenation of every surface in :data:`_UNIFIED_SOURCES`, with
        :data:`_UNIFIED_COLUMNS` and one row per surface, day, user and model.
        Dimensions a surface does not report are None. Empty but correctly
        shaped when no surface has usage in the window.
    """
    use_acquisitions = not direct_only and _acquisition_key(SURFACE_COCO, start_date, end_date)
    if use_acquisitions:
        dependencies = tuple(
            f"{SURFACE_SEARCH} — Daily" if source.surface == SURFACE_SEARCH else source.surface
            for source in _UNIFIED_SOURCES
        )
        if (
            all(dependency_is_ready(dep, start_date, end_date) for dep in dependencies)
            and (cached := st.session_state.get("acquisition_overview"))
            and cached[0] == (start_date, end_date)
        ):
            return cached[1].copy(deep=True)
    frames = []
    unavailable = []
    coverages = []
    for source in _UNIFIED_SOURCES:
        dependency = (
            f"{SURFACE_SEARCH} — Daily" if source.surface == SURFACE_SEARCH else source.surface
        )
        failure = None
        try:
            facts = (
                load_direct_dependency(dependency, start_date, end_date)
                if direct_only
                else (
                    load_dependency_result(dependency, start_date, end_date).frame
                    if use_acquisitions
                    else source.loader(start_date, end_date)
                )
            )
        except Exception as exc:
            _logger.exception("Overview source unavailable: %s", source.surface)
            failure = str(exc)
            facts = None
        if facts is None:
            unavailable.append(source.surface)
            coverages.append(_unavailable_coverage(dependency, start_date, end_date, failure))
            continue
        if not isinstance(facts.attrs.get("coverage"), SourceCoverage):
            facts = _with_coverage(facts, dependency, start_date, end_date, "unknown")
        coverages.append(facts.attrs["coverage"])
        if facts.attrs["coverage"].state == CoverageState.UNAVAILABLE:
            unavailable.append(source.surface)
        if facts.empty:
            continue
        keep = ["USAGE_DATE", *source.dimensions, *_METRIC_COLUMNS]
        if "USER_NAME" in facts.columns:
            keep.insert(1, "USER_NAME")
        part = facts[keep].copy()
        if "USER_NAME" not in part.columns:
            part["USER_NAME"] = None
        for dimension in ("MODEL_NAME", "SUB_SURFACE"):
            if dimension not in source.dimensions:
                part[dimension] = None
        part["SURFACE"] = source.surface
        part["CREDITS_ESTIMATED"] = source.credits_estimated
        part["RATE_INCOMPLETE"] = (
            facts.get("RATE_INCOMPLETE", pd.Series(True, index=facts.index))
            if source.credits_estimated
            else False
        )
        part["UNRATED_TOKENS"] = (
            facts.get("UNRATED_TOKENS", pd.Series(float("nan"), index=facts.index))
            if source.credits_estimated
            else 0.0
        )
        frames.append(part[list(_UNIFIED_COLUMNS)])

    result = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=pd.Index(_UNIFIED_COLUMNS))
    )
    result.attrs = {}
    result.attrs["unavailable_sources"] = tuple(unavailable)
    result.attrs["source_coverages"] = tuple(coverages)
    if use_acquisitions:
        st.session_state.acquisition_overview = ((start_date, end_date), result.copy(deep=True))
    return result


# ---------------------------------------------------------------------------
# Cross-surface warehouse compute attribution
#
# QUERY_ATTRIBUTION_HISTORY (standard warehouses) and QUERY_METERING_HISTORY
# (Adaptive warehouses) are UNIONed into one de-duplicated, one-row-per-QUERY_ID
# fact, restricted to rows attributable to a Cortex surface: a QUERY_TAG match
# (cortex-agent[-id], snowflake-intelligence[-id], or a CoCo JSON tag), or a
# QUERY_ID present in a Cortex usage view (Search batch, AI Functions). Rows
# matching neither are excluded from the fact entirely -- there is no
# "(unattributed)" bucket here; unattributed spend is only ever surfaced in the
# separate reconciliation query on the SQL page, never in the user-facing fact.
#
# Both source CTEs are pre-aggregated to exactly one row per QUERY_ID (grouping
# by anything else would let a tag or warehouse that varies within a query
# produce more than one row per QUERY_ID -- the same fan-out this design exists
# to prevent), so the later UNION ALL cannot fan out either. The union prefers
# attribution over metering for any QUERY_ID present in both, via NOT EXISTS
# (NOT IN is NULL-unsafe: one NULL QUERY_ID in the subquery would silently
# discard the entire metering branch, which is the dominant spend source on
# accounts with Adaptive warehouses).
#
# On accounts with no Adaptive warehouses (verified 2026-08-14 on IUC17074),
# QUERY_METERING_HISTORY simply has zero rows -- the probe below still
# succeeds, so the loader takes the full (non-degraded) path and the metering
# CTE contributes nothing, rather than reporting a degraded state that would
# incorrectly imply a privilege or region problem.
# ---------------------------------------------------------------------------

WAREHOUSE_FACT_COLUMNS = (
    "QUERY_ID",
    "QUERY_TAG",
    "WAREHOUSE_NAME",
    "SURFACE",
    "TIER",
    "REQUEST_ID",
    "SOURCE_VIEW",
    "COMPUTE_CREDITS",
    "CLOUD_SERVICES_CREDITS",
    "WAREHOUSE_CREDITS",
    "USAGE_DATE",
)


class WarehouseFactsResult(NamedTuple):
    """Result of :func:`load_warehouse_credit_facts`.

    Attributes:
        frame: The fact, typed to :data:`WAREHOUSE_FACT_COLUMNS` even when
            empty. Always safe to index by any of those columns.
        degraded: True if QUERY_METERING_HISTORY was inaccessible (privilege or
            region error) and only attribution-sourced rows are present. False
            means the full two-path query ran, including on accounts where
            metering legitimately returns zero rows.
        message: Human-readable banner text for callers to show when
            ``degraded`` is True, else None.
    """

    frame: pd.DataFrame
    degraded: bool
    message: str | None


def _empty_warehouse_facts() -> pd.DataFrame:
    """Build a correctly typed, empty warehouse-facts frame.

    Returns:
        An empty frame with :data:`WAREHOUSE_FACT_COLUMNS`, so callers never
        need to special-case "no rows yet" versus "no rows ever".
    """
    return pd.DataFrame(columns=pd.Index(WAREHOUSE_FACT_COLUMNS))


def metering_probe_sql() -> Statement:
    """Build the lightweight availability check for QUERY_METERING_HISTORY.

    Run before the main query so a privilege or region error surfaces here,
    before the attribution side (which is always accessible) would otherwise
    mask it by returning rows on its own -- see :func:`load_warehouse_credit_facts`.

    Returns:
        The statement. No binds.
    """
    return f"SELECT 1 FROM {AU}.QUERY_METERING_HISTORY LIMIT 0", ()


def _metering_attribution_overlap_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the QUERY_ID intersection-count check between the two source views.

    The attribution-wins de-dup (Task 1.1) assumes the two source views'
    QUERY_ID sets are disjoint; that held on every account checked so far but
    is not a documented Snowflake guarantee (MRG-P1-5), so this runs on every
    full-path load and logs a warning if it is ever violated. Cheap: it scans
    only distinct QUERY_ID, not the full row set.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement, yielding a single OVERLAP_COUNT row.
    """
    params = _window_params(start_date, end_date) + _window_params(start_date, end_date)
    sql = f"""
SELECT COUNT(*) AS OVERLAP_COUNT
FROM (SELECT DISTINCT QUERY_ID FROM {AU}.QUERY_ATTRIBUTION_HISTORY WHERE {utc_window("START_TIME")}) a
JOIN (SELECT DISTINCT QUERY_ID FROM {AU}.QUERY_METERING_HISTORY WHERE {utc_window("QUERY_START_TIME")}) m
  ON a.QUERY_ID = m.QUERY_ID
"""
    return sql, tuple(params)


def _warn_if_query_id_overlap(start_date: dt.date, end_date: dt.date) -> None:
    """Log a warning if the two source views' QUERY_ID sets overlap (MRG-P1-5).

    Best effort: any failure here is swallowed, since this is an assertion on
    top of an already-successful load, not part of the load itself.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.
    """
    try:
        overlap = run_query(*_metering_attribution_overlap_sql(start_date, end_date))
        count = int(overlap["OVERLAP_COUNT"].iloc[0])
    except Exception:  # noqa: BLE001 - advisory log only, never fail the page
        return
    if count:
        _logger.warning(
            "warehouse_credit_facts: %d QUERY_ID(s) present in both "
            "QUERY_ATTRIBUTION_HISTORY and QUERY_METERING_HISTORY for %s..%s "
            "-- the attribution-wins de-dup assumption is violated.",
            count,
            start_date,
            end_date,
        )


def _warehouse_credit_facts_sql(
    start_date: dt.date, end_date: dt.date, *, include_metering: bool
) -> Statement:
    """Build the warehouse-credit fact query, with or without the metering side.

    Shared by :func:`warehouse_credit_facts_sql` (the full two-path statement)
    and :func:`warehouse_credit_facts_attribution_only_sql` (the fallback), so
    the classification logic -- the part that must never drift between the two
    paths -- is written exactly once.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.
        include_metering: Whether to union in the QUERY_METERING_HISTORY CTE.
            False builds the attribution-only fallback for when metering is
            inaccessible.

    Returns:
        The statement, restricted to Cortex-attributable rows only (§5.1):
        a tag match, or a QUERY_ID present in a Cortex usage view. Exactly one
        row per QUERY_ID.
    """
    coco_apps = ", ".join(f"'{app}'" for app in _COCO_APP_NAMES)
    is_coco_tag = f"TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ({coco_apps})"

    params = list(_window_params(start_date, end_date))

    attr_cte = f"""attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE({utc_day("START_TIME")}) AS USAGE_DATE
    FROM {AU}.QUERY_ATTRIBUTION_HISTORY
    WHERE {utc_window("START_TIME")}
    GROUP BY QUERY_ID
)"""
    ctes = [attr_cte]
    combined_source = "attr"

    if include_metering:
        params.extend(_window_params(start_date, end_date))
        ctes.append(
            f"""meter AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_USED_COMPUTE, 0))         AS COMPUTE_CREDITS,
        SUM(NVL(CREDITS_USED_CLOUD_SERVICES, 0))::FLOAT  AS CLOUD_SERVICES_CREDITS,
        'METERING'                AS SOURCE_VIEW,
        ANY_VALUE({utc_day("QUERY_START_TIME")}) AS USAGE_DATE
    FROM {AU}.QUERY_METERING_HISTORY
    WHERE {utc_window("QUERY_START_TIME")}
      AND WAREHOUSE_NAME IS NOT NULL
    GROUP BY QUERY_ID
)"""
        )
        ctes.append(
            """combined AS (
    SELECT * FROM attr
    UNION ALL
    SELECT m.* FROM meter m
    WHERE NOT EXISTS (SELECT 1 FROM attr a WHERE a.QUERY_ID = m.QUERY_ID)
)"""
        )
        combined_source = "combined"

    ctes.append(
        f"""search_ids AS (
    SELECT DISTINCT QUERY_ID FROM {AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
)"""
    )
    ctes.append(
        f"""aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM {AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
)"""
    )
    ctes.append(
        f"""classified AS (
    SELECT
        c.QUERY_ID,
        c.QUERY_TAG,
        c.WAREHOUSE_NAME,
        c.SOURCE_VIEW,
        c.COMPUTE_CREDITS,
        c.CLOUD_SERVICES_CREDITS,
        c.COMPUTE_CREDITS + c.CLOUD_SERVICES_CREDITS AS WAREHOUSE_CREDITS,
        c.USAGE_DATE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent%'             THEN '{SURFACE_AGENTS}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence%'   THEN '{SURFACE_COWORK}'
            WHEN {is_coco_tag}                                THEN '{SURFACE_COCO}'
            WHEN si.QUERY_ID IS NOT NULL                      THEN '{SURFACE_SEARCH}'
            WHEN af.QUERY_ID IS NOT NULL                      THEN '{SURFACE_AI_FUNCTIONS}'
        END AS SURFACE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'                       THEN '{TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'cortex-agent'                         THEN '{TIER_SURFACE}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%'             THEN '{TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence'               THEN '{TIER_SURFACE}'
            WHEN {is_coco_tag}
                 AND TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING IS NOT NULL THEN '{TIER_PER_REQUEST}'
            WHEN {is_coco_tag}                                           THEN '{TIER_SURFACE}'
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL      THEN '{TIER_PER_REQUEST}'
        END AS TIER,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'           THEN SUBSTR(c.QUERY_TAG, 14)
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%' THEN SUBSTR(c.QUERY_TAG, 24)
            WHEN {is_coco_tag}                                THEN TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL THEN c.QUERY_ID
        END AS REQUEST_ID
    FROM {combined_source} c
    LEFT JOIN search_ids si ON si.QUERY_ID = c.QUERY_ID
    LEFT JOIN aif_ids af ON af.QUERY_ID = c.QUERY_ID
)"""
    )

    sql = (
        "WITH "
        + ",\n".join(ctes)
        + """
SELECT
    QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW,
    COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE
FROM classified
WHERE SURFACE IS NOT NULL
"""
    )
    return sql, tuple(params)


def warehouse_credit_facts_sql(start_date: dt.date, end_date: dt.date) -> Statement:
    """Build the full (attribution UNION metering) warehouse-credit fact query.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. See :func:`_warehouse_credit_facts_sql`.
    """
    return _warehouse_credit_facts_sql(start_date, end_date, include_metering=True)


def warehouse_credit_facts_attribution_only_sql(
    start_date: dt.date, end_date: dt.date
) -> Statement:
    """Build the attribution-only fallback, for when metering is inaccessible.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        The statement. See :func:`_warehouse_credit_facts_sql`.
    """
    return _warehouse_credit_facts_sql(start_date, end_date, include_metering=False)


def load_warehouse_credit_facts(start_date: dt.date, end_date: dt.date) -> WarehouseFactsResult:
    """Load the cross-surface warehouse-credit fact, degrading gracefully.

    Two-path design (REC-MRG-3): probes QUERY_METERING_HISTORY first, since a
    combined statement referencing an inaccessible view fails before the
    always-accessible attribution side can return anything. On probe failure,
    falls back to an attribution-only statement rather than raising or
    returning an empty frame, so standard-warehouse spend never silently
    disappears because Adaptive-warehouse metering is unavailable.

    Args:
        start_date: First UTC day in the window, inclusive.
        end_date: Last UTC day in the window, inclusive.

    Returns:
        Never raises. ``degraded=True`` means the probe failed and only
        attribution-sourced rows are present; ``degraded=False`` covers both
        a full run and an account with no Adaptive warehouses (metering
        legitimately empty, not inaccessible).
    """
    # Source-aware: try DT path first if enabled
    from utils.config import load_settings, source_for

    settings = load_settings()
    if source_for(SURFACE_WAREHOUSE_ATTRIBUTION, settings) == "dt":
        result = _load_from_dt(SURFACE_WAREHOUSE_ATTRIBUTION, start_date, end_date)
        if result is not None:
            st.session_state.setdefault("_degraded", {})[SURFACE_WAREHOUSE_ATTRIBUTION] = None
            return WarehouseFactsResult(
                _with_coverage(
                    result,
                    SURFACE_WAREHOUSE_ATTRIBUTION,
                    start_date,
                    end_date,
                    "aggregate",
                    partial_reason="Aggregate excludes adaptive warehouse metering."
                    if settings.wh_degraded
                    else None,
                ),
                settings.wh_degraded,
                "Reading from scheduled table (attribution-only)" if settings.wh_degraded else None,
            )
        st.session_state.setdefault("_degraded", {})[SURFACE_WAREHOUSE_ATTRIBUTION] = (
            "Scheduled table unavailable — reading ACCOUNT_USAGE"
        )

    try:
        run_query(*metering_probe_sql())
    except Exception:  # noqa: BLE001 - probe failed, so degrade to the fallback path
        try:
            frame = run_query(*warehouse_credit_facts_attribution_only_sql(start_date, end_date))
        except Exception as exc:  # noqa: BLE001 - last resort: report, never raise
            return WarehouseFactsResult(
                _empty_warehouse_facts(),
                True,
                f"Warehouse attribution unavailable: {exc}",
            )
        return WarehouseFactsResult(
            _with_coverage(
                frame,
                SURFACE_WAREHOUSE_ATTRIBUTION,
                start_date,
                end_date,
                "direct",
                partial_reason="Adaptive warehouse metering is unavailable.",
            ),
            True,
            "Adaptive-warehouse (QUERY_METERING_HISTORY) credits are unavailable -- "
            "showing standard-warehouse (QUERY_ATTRIBUTION_HISTORY) credits only.",
        )

    try:
        frame = run_query(*warehouse_credit_facts_sql(start_date, end_date))
    except Exception as exc:  # noqa: BLE001 - explicit unavailable result, as in the fallback
        return WarehouseFactsResult(
            _empty_warehouse_facts(), True, f"Warehouse attribution unavailable: {exc}"
        )
    _warn_if_query_id_overlap(start_date, end_date)
    return WarehouseFactsResult(
        _with_coverage(frame, SURFACE_WAREHOUSE_ATTRIBUTION, start_date, end_date, "direct"),
        False,
        None,
    )


# ---------------------------------------------------------------------------
# SQL reference
#
# The SQL page renders these builders rather than a hand-maintained copy of the
# statements, so what is displayed is exactly what the dashboard executes. The
# window-independent queries are wrapped so every builder has the same call
# signature.
#
# Entries are grouped for display by ``group``, in top-nav order, so the page's
# first-level picker mirrors the sidebar one-for-one. Within a group the primary
# fact comes first and becomes the default selection; ``variant`` is the short
# label shown once a group is selected, so the group name is not repeated on
# every button. ``kind`` is empty for a group's primary fact and otherwise names
# what the statement is for, which the page shows as a caption.
# ---------------------------------------------------------------------------

# Grouping key for statements that back no single page, shown separately.
SHARED = "Shared"

# ``SqlReference.kind`` values. Empty means "this group's primary fact".
KIND_PRIMARY = ""
KIND_ADDITIONAL = "Additional fact"
KIND_SHARED_INPUT = "Shared input"
KIND_FALLBACK = "Fallback path"


class SqlReference(NamedTuple):
    """One entry on the reference SQL page.

    Attributes:
        label: Full heading for the entry, unique across the reference.
        sources: Fully qualified views the statement reads.
        note: Prose explaining what makes the statement non-obvious.
        builder: Builds the statement for a window. Window-independent
            builders accept and ignore the dates.
        group: Dashboard page the statement backs, or :data:`SHARED` for
            statements that feed more than one page. Drives the page's
            first-level picker.
        variant: Short label within the group, used for the second-level
            picker so the group name is not repeated on every button.
        kind: What the statement is for, shown as a caption. Empty for the
            group's primary fact; otherwise one of the ``KIND_*`` constants.
        runtime_key: The dependency key this statement's data is actually read
            through at runtime (a :data:`DEPENDENCY_KINDS` key), or None for
            conditional control-flow statements (the Warehouse Attribution
            probe and fallback) that are not a page's primary active source.
            Drives source-aware rendering on the SQL page.
    """

    label: str
    sources: tuple[str, ...]
    note: str
    builder: Callable[..., Statement]
    group: str
    variant: str
    kind: str = KIND_PRIMARY
    runtime_key: str | None = None


# ---------------------------------------------------------------------------
# Scheduled-table body functions (acceleration layer)
#
# Each function returns the SQL body for a scheduled table: the same SELECT
# as the windowed builder but WITHOUT the date-window predicate so it
# materializes all available history. The app's loaders then filter the
# materialized USAGE_DATE column for the selected window.
#
# REST is deliberately absent: rest_facts_sql binds model rates as literals at
# call time and is not materializable. See phase-0-outcomes.md.
# ---------------------------------------------------------------------------


def _coco_body_sql() -> str:
    """CoCo scheduled-table body (all history, no window filter)."""
    token_sums = ",\n".join(
        f"    SUM(NVL(tg:{t}::NUMBER, 0)) AS {TOKEN_TYPE_COLUMNS[t].lower()}" for t in TOKEN_TYPES
    )
    return f"""
WITH coco_granular AS (
    SELECT
        LOWER(h.INTERFACE) AS sub_surface,
        {utc_day("h.USAGE_TIME")} AS usage_date,
        NVL(h.USER_NAME, '{UNKNOWN}') AS user_name,
        NVL(h.METADATA:role_name::STRING, '{UNKNOWN}') AS role_name,
        NVL(h.METADATA:inference_region::STRING, '{UNKNOWN}') AS inference_region,
        token_model.key::STRING AS model_name,
        token_model.value AS tg,
        credit_model.value AS cg
    FROM {AU}.{COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE token_model.key = credit_model.key
)
SELECT
    sub_surface,
    usage_date,
    user_name,
    model_name,
    role_name,
    inference_region,
    COUNT(*)     AS calls,
    SUM(NVL(tg:input::NUMBER, 0) + NVL(tg:output::NUMBER, 0) +
        NVL(tg:cache_read_input::NUMBER, 0) + NVL(tg:cache_write_input::NUMBER, 0)) AS tokens,
    SUM(NVL(cg:input::FLOAT, 0) + NVL(cg:output::FLOAT, 0) +
        NVL(cg:cache_read_input::FLOAT, 0) + NVL(cg:cache_write_input::FLOAT, 0)) AS credits,
{token_sums}
FROM coco_granular
GROUP BY ALL
"""


def _ai_fn_body_sql() -> str:
    """AI Functions scheduled-table body."""
    return f"""
SELECT
    {utc_day("a.START_TIME")}                                     AS usage_date,
    NVL(u.NAME, '{UNKNOWN}')                                      AS user_name,
    NVL(a.FUNCTION_NAME, '{UNKNOWN}')                             AS function_name,
    NVL(a.MODEL_NAME, '{UNKNOWN}')                                AS model_name,
    NVL(NULLIF(a.QUERY_TAG, ''), '{NONE_LABEL}')                  AS query_tag,
    COUNT(*)                                                       AS calls,
    SUM({_AI_FN_TOKENS.strip()})                                   AS tokens,
    SUM(NVL(a.CREDITS, 0))                                        AS credits,
    SUM(CASE WHEN a.IS_COMPLETED = FALSE THEN 1 ELSE 0 END)       AS incomplete_calls
FROM {AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY a
LEFT JOIN {AU}.USERS u ON u.USER_ID = a.USER_ID
GROUP BY ALL
"""


def _agent_body_sql() -> str:
    """Cortex Agents scheduled-table body."""
    return f"""
SELECT
    {utc_day("START_TIME")}                            AS usage_date,
    NVL(USER_NAME, '{UNKNOWN}')                        AS user_name,
    NVL(AGENT_NAME, '{UNKNOWN}')                       AS agent_name,
    NVL(AGENT_DATABASE_NAME, '{UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '{UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '{UNKNOWN}')                       AS agent_fqn,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                            AS agent_tag_labels,
    COUNT(*)                                           AS calls,
    SUM(NVL(TOKENS, 0))                               AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                         AS credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM {AU}.CORTEX_AGENT_USAGE_HISTORY
GROUP BY ALL
"""


def _cowork_body_sql() -> str:
    """CoWork scheduled-table body."""
    return f"""
SELECT
    {utc_day("START_TIME")}                              AS usage_date,
    NVL(USER_NAME, '{UNKNOWN}')                          AS user_name,
    NVL(SNOWFLAKE_COWORK_NAME, '{UNKNOWN}')               AS cowork_name,
    NVL(AGENT_DATABASE_NAME, '{UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '{UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '{UNKNOWN}')                         AS agent_fqn,
    NVL(AGENT_NAME, '{NONE_LABEL}')                      AS agent_name,
    NVL(METADATA:role_name::STRING, '{UNKNOWN}')         AS role_name,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                              AS agent_tag_labels,
    COUNT(*)                                             AS calls,
    SUM(NVL(TOKENS, 0))                                 AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                           AS credits,
    SUM(NVL(METADATA:sql_query_credits::FLOAT, 0))       AS sql_query_credits,
    SUM(NVL(METADATA:ai_functions_credits::FLOAT, 0))    AS ai_functions_credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM {AU}.SNOWFLAKE_COWORK_USAGE_HISTORY
GROUP BY ALL
"""


def _cowork_service_mix_body_sql() -> str:
    """CoWork service-mix scheduled-table body (full history, same structure as windowed query)."""
    token_sums = ",\n".join(
        f"    SUM(NVL(m.value:{t}::NUMBER, 0)) AS {TOKEN_TYPE_COLUMNS[t].lower()}"
        for t in TOKEN_TYPES
    )
    credit_sums = " + ".join(f"NVL(cm.value:{t}::FLOAT, 0)" for t in TOKEN_TYPES)
    total_tokens = " + ".join(TOKEN_TYPE_COLUMNS[t].lower() for t in TOKEN_TYPES)
    outer_token_sums = ",\n".join(
        f"    SUM(t.{TOKEN_TYPE_COLUMNS[t].lower()}) AS {TOKEN_TYPE_COLUMNS[t].lower()}"
        for t in TOKEN_TYPES
    )
    return f"""
WITH tokens AS (
    SELECT
        {utc_day("h.START_TIME")} AS usage_date,
        NVL(h.USER_NAME, '{UNKNOWN}') AS user_name,
        rq.key   AS request_id,
        s.key    AS service_type,
        m.key    AS model_name,
{token_sums}
    FROM {AU}.SNOWFLAKE_COWORK_USAGE_HISTORY h,
         LATERAL FLATTEN(input => h.TOKENS_GRANULAR) e,
         LATERAL FLATTEN(input => e.value) rq,
         LATERAL FLATTEN(input => rq.value) s,
         LATERAL FLATTEN(input => s.value) m
    WHERE s.key <> 'start_time'
    GROUP BY ALL
),
credits AS (
    SELECT
        rq.key AS request_id,
        s.key  AS service_type,
        cm.key AS model_name,
        SUM({credit_sums}) AS credits
    FROM {AU}.SNOWFLAKE_COWORK_USAGE_HISTORY h,
         LATERAL FLATTEN(input => h.CREDITS_GRANULAR) e,
         LATERAL FLATTEN(input => e.value) rq,
         LATERAL FLATTEN(input => rq.value) s,
         LATERAL FLATTEN(input => s.value) cm
    WHERE s.key <> 'start_time'
    GROUP BY ALL
)
SELECT
    t.usage_date,
    t.user_name,
    t.service_type,
    t.model_name,
    COUNT(*)                     AS calls,
    SUM({total_tokens}) AS tokens,
    SUM(NVL(c.credits, 0))       AS credits,
{outer_token_sums}
FROM tokens t
LEFT JOIN credits c
       ON c.request_id = t.request_id
      AND c.service_type = t.service_type
      AND c.model_name = t.model_name
GROUP BY ALL
"""


def _search_daily_body_sql() -> str:
    """Cortex Search daily scheduled-table body."""
    return f"""
SELECT
    {utc_day("USAGE_DATE")}                              AS usage_date,
    NVL(SERVICE_NAME, '{UNKNOWN}')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '{UNKNOWN}')                   AS consumption_type,
    NVL(MODEL_NAME, '{NONE_LABEL}')                      AS model_name,
    COUNT(*)                                             AS calls,
    SUM(TRY_CAST(TOKENS AS NUMBER))                      AS tokens,
    SUM(NVL(CREDITS, 0))                                 AS credits
FROM {AU}.CORTEX_SEARCH_DAILY_USAGE_HISTORY
GROUP BY ALL
"""


def _search_serving_body_sql() -> str:
    """Cortex Search serving scheduled-table body."""
    return f"""
SELECT
    {utc_day("START_TIME")}                                          AS usage_date,
    DATE_TRUNC('hour', CONVERT_TIMEZONE('UTC', START_TIME))          AS usage_hour,
    NVL(SERVICE_NAME, '{UNKNOWN}')                                    AS service_name,
    COUNT(*)                                                          AS calls,
    SUM(NVL(CREDITS, 0))                                              AS credits
FROM {AU}.CORTEX_SEARCH_SERVING_USAGE_HISTORY
GROUP BY ALL
"""


def _search_batch_body_sql() -> str:
    """Cortex Search batch scheduled-table body."""
    return f"""
SELECT
    QUERY_ID,
    {utc_day("START_TIME")}                              AS usage_date,
    NVL(SERVICE_NAME, '{UNKNOWN}')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '{UNKNOWN}')                   AS consumption_type,
    NVL(MODEL_NAME, '{NONE_LABEL}')                      AS model_name,
    SUM(TRY_CAST(TOKENS AS NUMBER))                     AS tokens,
    SUM(NVL(CREDITS_USED, 0))                           AS credits
FROM {AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
GROUP BY ALL
"""


def _warehouse_body_sql_full() -> str:
    """Warehouse attribution scheduled-table body (full: attribution + metering).

    Replicates the classification logic from _warehouse_credit_facts_sql so the
    DT table contains the same columns: QUERY_ID, QUERY_TAG, WAREHOUSE_NAME,
    SURFACE, TIER, REQUEST_ID, SOURCE_VIEW, COMPUTE_CREDITS,
    CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE.

    Search membership is bounded to 365 days. AI membership uses available
    QUERY_ID history without restricting hourly usage windows or completion,
    which could otherwise omit long-running queries at date boundaries.
    """
    coco_apps = ", ".join(f"'{app}'" for app in _COCO_APP_NAMES)
    is_coco_tag = f"TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ({coco_apps})"

    return f"""
WITH attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE({utc_day("START_TIME")}) AS USAGE_DATE
    FROM {AU}.QUERY_ATTRIBUTION_HISTORY
    GROUP BY QUERY_ID
),
meter AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_USED_COMPUTE, 0))         AS COMPUTE_CREDITS,
        SUM(NVL(CREDITS_USED_CLOUD_SERVICES, 0))::FLOAT  AS CLOUD_SERVICES_CREDITS,
        'METERING'                AS SOURCE_VIEW,
        ANY_VALUE({utc_day("QUERY_START_TIME")}) AS USAGE_DATE
    FROM {AU}.QUERY_METERING_HISTORY
    WHERE WAREHOUSE_NAME IS NOT NULL
    GROUP BY QUERY_ID
),
combined AS (
    SELECT * FROM attr
    UNION ALL
    SELECT m.* FROM meter m
    WHERE NOT EXISTS (SELECT 1 FROM attr a WHERE a.QUERY_ID = m.QUERY_ID)
),
search_ids AS (
    SELECT DISTINCT QUERY_ID FROM {AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
    WHERE START_TIME >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
),
aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM {AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
),
classified AS (
    SELECT
        c.QUERY_ID,
        c.QUERY_TAG,
        c.WAREHOUSE_NAME,
        c.SOURCE_VIEW,
        c.COMPUTE_CREDITS,
        c.CLOUD_SERVICES_CREDITS,
        c.COMPUTE_CREDITS + c.CLOUD_SERVICES_CREDITS AS WAREHOUSE_CREDITS,
        c.USAGE_DATE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent%'             THEN '{SURFACE_AGENTS}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence%'   THEN '{SURFACE_COWORK}'
            WHEN {is_coco_tag}                                THEN '{SURFACE_COCO}'
            WHEN si.QUERY_ID IS NOT NULL                      THEN '{SURFACE_SEARCH}'
            WHEN af.QUERY_ID IS NOT NULL                      THEN '{SURFACE_AI_FUNCTIONS}'
        END AS SURFACE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'                       THEN '{TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'cortex-agent'                         THEN '{TIER_SURFACE}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%'             THEN '{TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence'               THEN '{TIER_SURFACE}'
            WHEN {is_coco_tag}
                 AND TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING IS NOT NULL THEN '{TIER_PER_REQUEST}'
            WHEN {is_coco_tag}                                           THEN '{TIER_SURFACE}'
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL      THEN '{TIER_PER_REQUEST}'
        END AS TIER,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'           THEN SUBSTR(c.QUERY_TAG, 14)
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%' THEN SUBSTR(c.QUERY_TAG, 24)
            WHEN {is_coco_tag}                                THEN TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL THEN c.QUERY_ID
        END AS REQUEST_ID
    FROM combined c
    LEFT JOIN search_ids si ON si.QUERY_ID = c.QUERY_ID
    LEFT JOIN aif_ids af ON af.QUERY_ID = c.QUERY_ID
)
SELECT
    QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW,
    COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE
FROM classified
WHERE SURFACE IS NOT NULL
"""


def _warehouse_body_sql_attribution_only() -> str:
    """Warehouse attribution scheduled-table body (attribution only, no metering).

    Same classification as full but without the metering CTE.

    Search membership is bounded to 365 days. AI membership uses available
    QUERY_ID history without restricting hourly usage windows or completion,
    which could otherwise omit long-running queries at date boundaries.
    """
    coco_apps = ", ".join(f"'{app}'" for app in _COCO_APP_NAMES)
    is_coco_tag = f"TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ({coco_apps})"

    return f"""
WITH attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE({utc_day("START_TIME")}) AS USAGE_DATE
    FROM {AU}.QUERY_ATTRIBUTION_HISTORY
    GROUP BY QUERY_ID
),
search_ids AS (
    SELECT DISTINCT QUERY_ID FROM {AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
    WHERE START_TIME >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
),
aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM {AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
),
classified AS (
    SELECT
        c.QUERY_ID,
        c.QUERY_TAG,
        c.WAREHOUSE_NAME,
        c.SOURCE_VIEW,
        c.COMPUTE_CREDITS,
        c.CLOUD_SERVICES_CREDITS,
        c.COMPUTE_CREDITS + c.CLOUD_SERVICES_CREDITS AS WAREHOUSE_CREDITS,
        c.USAGE_DATE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent%'             THEN '{SURFACE_AGENTS}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence%'   THEN '{SURFACE_COWORK}'
            WHEN {is_coco_tag}                                THEN '{SURFACE_COCO}'
            WHEN si.QUERY_ID IS NOT NULL                      THEN '{SURFACE_SEARCH}'
            WHEN af.QUERY_ID IS NOT NULL                      THEN '{SURFACE_AI_FUNCTIONS}'
        END AS SURFACE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'                       THEN '{TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'cortex-agent'                         THEN '{TIER_SURFACE}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%'             THEN '{TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence'               THEN '{TIER_SURFACE}'
            WHEN {is_coco_tag}
                 AND TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING IS NOT NULL THEN '{TIER_PER_REQUEST}'
            WHEN {is_coco_tag}                                           THEN '{TIER_SURFACE}'
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL      THEN '{TIER_PER_REQUEST}'
        END AS TIER,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'           THEN SUBSTR(c.QUERY_TAG, 14)
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%' THEN SUBSTR(c.QUERY_TAG, 24)
            WHEN {is_coco_tag}                                THEN TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL THEN c.QUERY_ID
        END AS REQUEST_ID
    FROM attr c
    LEFT JOIN search_ids si ON si.QUERY_ID = c.QUERY_ID
    LEFT JOIN aif_ids af ON af.QUERY_ID = c.QUERY_ID
)
SELECT
    QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW,
    COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE
FROM classified
WHERE SURFACE IS NOT NULL
"""


# The registry of DT-eligible surfaces. REST is deliberately absent.
# Values are DtBodySpec instances; the body_fn is called at scheduled-table
# creation time to get the windowless SQL.
from utils.snowflake import DtBodySpec  # noqa: E402

DT_BODY_REGISTRY: dict[str, DtBodySpec] = {
    SURFACE_COCO: DtBodySpec(
        surface_key=SURFACE_COCO,
        body_fn=_coco_body_sql,
        columns=_DT_COLUMNS[SURFACE_COCO],
    ),
    SURFACE_AI_FUNCTIONS: DtBodySpec(
        surface_key=SURFACE_AI_FUNCTIONS,
        body_fn=_ai_fn_body_sql,
        columns=_DT_COLUMNS[SURFACE_AI_FUNCTIONS],
    ),
    SURFACE_AGENTS: DtBodySpec(
        surface_key=SURFACE_AGENTS,
        body_fn=_agent_body_sql,
        columns=_DT_COLUMNS[SURFACE_AGENTS],
    ),
    SURFACE_COWORK: DtBodySpec(
        surface_key=SURFACE_COWORK,
        body_fn=_cowork_body_sql,
        columns=_DT_COLUMNS[SURFACE_COWORK],
    ),
    f"{SURFACE_COWORK} — Service Mix": DtBodySpec(
        surface_key=f"{SURFACE_COWORK} — Service Mix",
        body_fn=_cowork_service_mix_body_sql,
        columns=_DT_COLUMNS[f"{SURFACE_COWORK} — Service Mix"],
    ),
    f"{SURFACE_SEARCH} — Daily": DtBodySpec(
        surface_key=f"{SURFACE_SEARCH} — Daily",
        body_fn=_search_daily_body_sql,
        columns=_DT_COLUMNS[f"{SURFACE_SEARCH} — Daily"],
    ),
    f"{SURFACE_SEARCH} — Serving": DtBodySpec(
        surface_key=f"{SURFACE_SEARCH} — Serving",
        body_fn=_search_serving_body_sql,
        columns=_DT_COLUMNS[f"{SURFACE_SEARCH} — Serving"],
    ),
    f"{SURFACE_SEARCH} — Batch": DtBodySpec(
        surface_key=f"{SURFACE_SEARCH} — Batch",
        body_fn=_search_batch_body_sql,
        columns=_DT_COLUMNS[f"{SURFACE_SEARCH} — Batch"],
    ),
    SURFACE_WAREHOUSE_ATTRIBUTION: DtBodySpec(
        surface_key=SURFACE_WAREHOUSE_ATTRIBUTION,
        body_fn=_warehouse_body_sql_full,
        create_probe=None,  # Populated by config.py at create-time
        degraded_flag_key="wh_degraded",
        columns=_DT_COLUMNS[SURFACE_WAREHOUSE_ATTRIBUTION],
        apply_outer_retention=False,
    ),
    SURFACE_MODEL_RATES: DtBodySpec(
        surface_key=SURFACE_MODEL_RATES,
        body_fn=model_rates_body_sql,
        columns=_DT_COLUMNS[SURFACE_MODEL_RATES],
        cluster_column=None,
        apply_outer_retention=False,
    ),
}


SQL_REFERENCE = (
    SqlReference(
        SURFACE_COCO,
        (f"{AU}.{COCO_USAGE_VIEW}",),
        "One aggregate row per delivery surface, day, user, model, role and "
        "region. The unified view is flattened by model and token/credit model "
        "keys are paired before aggregation.",
        coco_facts_sql,
        group=SURFACE_COCO,
        variant="Fact grain",
        runtime_key=SURFACE_COCO,
    ),
    SqlReference(
        SURFACE_COWORK,
        (f"{AU}.SNOWFLAKE_COWORK_USAGE_HISTORY",),
        "Fact grain for CoWork. METADATA carries sql_query_credits and "
        "ai_functions_credits, which are billed on top of TOKEN_CREDITS, so they "
        "are kept as their own columns rather than folded into credits.",
        cowork_facts_sql,
        group=SURFACE_COWORK,
        variant="Fact grain",
        runtime_key=SURFACE_COWORK,
    ),
    SqlReference(
        f"{SURFACE_COWORK} — Service Mix",
        (f"{AU}.SNOWFLAKE_COWORK_USAGE_HISTORY",),
        "CoWork-only breakdown. TOKENS_GRANULAR and CREDITS_GRANULAR are ARRAYs "
        "nested request_id -> service_type -> model, so four FLATTEN levels are "
        "needed to split one turn across cortex_agents and its tool calls.",
        cowork_service_mix_sql,
        group=SURFACE_COWORK,
        variant="Service mix",
        kind=KIND_ADDITIONAL,
        runtime_key=f"{SURFACE_COWORK} — Service Mix",
    ),
    SqlReference(
        SURFACE_AGENTS,
        (f"{AU}.CORTEX_AGENT_USAGE_HISTORY",),
        "One row per agent request. PARENT_REQUEST_ID separates root requests "
        "(one per user turn) from child requests the agent spawns while "
        "servicing that turn.",
        agent_facts_sql,
        group=SURFACE_AGENTS,
        variant="Fact grain",
        runtime_key=SURFACE_AGENTS,
    ),
    SqlReference(
        SURFACE_SEARCH,
        (f"{AU}.CORTEX_SEARCH_DAILY_USAGE_HISTORY",),
        "Daily usage by CONSUMPTION_TYPE, SERVICE_NAME and MODEL_NAME. USAGE_DATE is "
        "TIMESTAMP_LTZ here (not DATE, unlike most other surfaces), so it is bucketed "
        "through the same UTC-day helper as everything else.",
        search_daily_facts_sql,
        group=SURFACE_SEARCH,
        variant="Daily",
        runtime_key=f"{SURFACE_SEARCH} — Daily",
    ),
    SqlReference(
        f"{SURFACE_SEARCH} — Serving",
        (f"{AU}.CORTEX_SEARCH_SERVING_USAGE_HISTORY",),
        "Hourly query-time serving credits by SERVICE_NAME. No CONSUMPTION_TYPE or "
        "TOKENS column on this view -- serving is credits-only.",
        search_serving_facts_sql,
        group=SURFACE_SEARCH,
        variant="Serving",
        kind=KIND_ADDITIONAL,
        runtime_key=f"{SURFACE_SEARCH} — Serving",
    ),
    SqlReference(
        f"{SURFACE_SEARCH} — Batch",
        (f"{AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY",),
        "Batch (AI_COMPLETE-style) query credits by QUERY_ID. Uses CREDITS_USED, not "
        "CREDITS like the other two Search views, and START_TIME here is "
        "TIMESTAMP_TZ, not TIMESTAMP_LTZ. Joins to the Warehouse Attribution fact by "
        "QUERY_ID on the Cortex Search page for per-query warehouse cost.",
        search_batch_facts_sql,
        group=SURFACE_SEARCH,
        variant="Batch",
        kind=KIND_ADDITIONAL,
        runtime_key=f"{SURFACE_SEARCH} — Batch",
    ),
    SqlReference(
        SURFACE_REST_API,
        (f"{AU}.CORTEX_REST_API_USAGE_HISTORY", f"{AU}.USERS"),
        "This view reports tokens but no credits, so credits are ESTIMATED by "
        "applying per-model, per-token-type rates in the model_rates CTE. Those "
        "rates are observed from CoCo history and bound as literals.",
        rest_facts_sql,
        group=SURFACE_REST_API,
        variant="Fact grain",
        runtime_key=SURFACE_REST_API,
    ),
    SqlReference(
        SURFACE_AI_FUNCTIONS,
        (f"{AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY", f"{AU}.USERS"),
        "Credits come straight from the view. Tokens are summed out of the "
        "METRICS array, filtered to entries whose unit is 'tokens', so functions "
        "billed per page or per row report credits with zero tokens.",
        ai_fn_facts_sql,
        group=SURFACE_AI_FUNCTIONS,
        variant="Fact grain",
        runtime_key=SURFACE_AI_FUNCTIONS,
    ),
    SqlReference(
        f"{SURFACE_WAREHOUSE_ATTRIBUTION} — Full",
        (
            f"{AU}.QUERY_ATTRIBUTION_HISTORY",
            f"{AU}.QUERY_METERING_HISTORY",
            f"{AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY",
            f"{AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY",
        ),
        "Two-path loader step 2 of 3: the full statement, run when the probe succeeds. "
        "Pre-aggregates each source view to one row per QUERY_ID before an "
        "attribution-wins UNION ALL, then restricts to Cortex-attributable rows only "
        "(a QUERY_TAG match or a QUERY_ID present in a Cortex usage view).",
        warehouse_credit_facts_sql,
        group=SURFACE_WAREHOUSE_ATTRIBUTION,
        variant="Full",
        runtime_key=SURFACE_WAREHOUSE_ATTRIBUTION,
    ),
    SqlReference(
        f"{SURFACE_WAREHOUSE_ATTRIBUTION} — Probe",
        (f"{AU}.QUERY_METERING_HISTORY",),
        "Two-path loader step 1 of 3: a zero-row availability probe run before the "
        "main statement, so a privilege or region error on QUERY_METERING_HISTORY "
        "surfaces here rather than silently masking valid attribution rows.",
        lambda *_: metering_probe_sql(),
        group=SURFACE_WAREHOUSE_ATTRIBUTION,
        variant="Probe",
        kind=KIND_FALLBACK,
    ),
    SqlReference(
        f"{SURFACE_WAREHOUSE_ATTRIBUTION} — Attribution-only fallback",
        (
            f"{AU}.QUERY_ATTRIBUTION_HISTORY",
            f"{AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY",
            f"{AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY",
        ),
        "Two-path loader step 3 of 3: run when the probe fails (privilege or region "
        "error), so standard-warehouse spend is never dropped just because "
        "Adaptive-warehouse metering is unavailable.",
        warehouse_credit_facts_attribution_only_sql,
        group=SURFACE_WAREHOUSE_ATTRIBUTION,
        variant="Attribution-only",
        kind=KIND_FALLBACK,
    ),
    SqlReference(
        "Model Credit Rates",
        (f"{AU}.{COCO_USAGE_VIEW}",),
        "Scanned once over a bounded 90-day window and reused as bound literals "
        "inside the REST API query, so rate collection does not repeat per page.",
        lambda *_: model_rates_sql(),
        group=SHARED,
        variant="Model credit rates",
        kind=KIND_SHARED_INPUT,
        runtime_key=SURFACE_MODEL_RATES,
    ),
    SqlReference(
        "User Tags",
        (f"{AU}.TAG_REFERENCES",),
        "Current USER-domain tag references. The per-row USER_TAGS column on the "
        "usage views is populated on too few rows to filter on reliably.",
        lambda *_: (user_tags_sql(), ()),
        group=SHARED,
        variant="User tags",
        kind=KIND_SHARED_INPUT,
        runtime_key="user_tags",
    ),
)


def sql_reference_groups() -> tuple[str, ...]:
    """List the page-level groups in :data:`SQL_REFERENCE`, in nav order.

    Returns:
        Each distinct ``group`` except :data:`SHARED`, first-seen order
        preserved so the SQL page's picker matches the top nav.
    """
    seen = [e.group for e in SQL_REFERENCE if e.group != SHARED]
    return tuple(dict.fromkeys(seen))


def sql_reference_for(group: str) -> tuple[SqlReference, ...]:
    """Select the statements belonging to one group.

    Args:
        group: A value from :func:`sql_reference_groups`, or :data:`SHARED`.

    Returns:
        The group's entries in declaration order, primary fact first.
    """
    return tuple(e for e in SQL_REFERENCE if e.group == group)


def inline_params(sql: str, params: Sequence) -> str:
    """Substitute bound qmark parameters into SQL for display.

    Lets the SQL page show a statement that can be pasted into a worksheet and
    run as-is. Display only for application-generated statements: it single
    quotes each value (doubling embedded single quotes) and only substitutes
    real ``?`` placeholder positions, leaving any ``?`` inside a parameter
    value intact.

    Args:
        sql: Statement text containing ``?`` placeholders.
        params: Values to inline, in placeholder order.

    Returns:
        The statement with each placeholder replaced by a quoted literal.
    """
    values = iter(params)
    out: list[str] = []
    for char in sql:
        if char == "?":
            try:
                value = next(values)
            except StopIteration:
                out.append(char)
                continue
            if value is None:
                out.append("NULL")
                continue
            escaped = str(value).replace("'", "''")
            out.append(f"'{escaped}'")
        else:
            out.append(char)
    return "".join(out).strip()


class ReferenceStatement(NamedTuple):
    """The read statement the SQL page shows for one reference entry.

    Attributes:
        sql: Statement text, with ``?`` placeholders for any bound params.
        params: Values bound to the placeholders, in order.
        source_mode: The active read path: ``aggregate`` (scheduled table),
            ``direct`` (live ACCOUNT_USAGE), ``global_cached`` (long-TTL
            metadata), or ``fallback`` (a conditional probe/fallback path).
        source_label: User-facing description of that path.
        served_start_date: First day the shown statement reads, or None when
            the statement is window-independent.
        served_end_date: Last day the shown statement reads, or None.
        materialization_key: Registry key whose scheduled body produced the
            aggregate rows, when the shown statement reads an aggregate table;
            None otherwise. Bridges the read to its build SQL.
    """

    sql: str
    params: tuple
    source_mode: str
    source_label: str
    served_start_date: dt.date | None
    served_end_date: dt.date | None
    materialization_key: str | None


class AccelerationMaterialization(NamedTuple):
    """The scheduled body that produces one aggregate table's rows."""

    surface_key: str
    body_sql: str
    columns: tuple[str, ...]
    cluster_column: str | None
    applies_outer_retention: bool


def acceleration_materialization_for(surface_key: str) -> AccelerationMaterialization | None:
    """Return the scheduled body definition for a materialized surface.

    Args:
        surface_key: A :data:`DT_BODY_REGISTRY` key.

    Returns:
        The materialization definition, or None when the surface is not
        materialized (for example Cortex Inference API or User Tags).
    """
    spec = DT_BODY_REGISTRY.get(surface_key)
    if spec is None:
        return None
    return AccelerationMaterialization(
        surface_key=surface_key,
        body_sql=spec.body_fn(),
        columns=spec.columns,
        cluster_column=spec.cluster_column,
        applies_outer_retention=spec.apply_outer_retention,
    )


def reference_statement_for(
    entry: SqlReference,
    start_date: dt.date,
    end_date: dt.date,
    settings: "Settings",
    *,
    today: dt.date,
) -> ReferenceStatement:
    """Resolve the statement the dashboard actually runs for a reference entry.

    Read-only: builds statement text using the same source routing the loaders
    use, so the SQL page shows the aggregate-table read when acceleration is
    active and the ACCOUNT_USAGE builder otherwise. Never executes a loader.

    Args:
        entry: The reference entry to resolve.
        start_date: First requested UTC day, inclusive.
        end_date: Last requested UTC day, inclusive.
        settings: Current acceleration-layer settings.
        today: Snowflake's UTC "today", for the settled-window cap.

    Returns:
        The resolved :class:`ReferenceStatement`.
    """
    from utils.config import effective_served_window, source_for
    from utils.snowflake import quote_identifier, surface_to_suffix

    key = entry.runtime_key

    # Conditional control-flow statements (Warehouse Attribution probe and
    # attribution-only fallback) document a path, not a primary active source.
    if key is None:
        sql, params = entry.builder(start_date, end_date)
        return ReferenceStatement(
            sql,
            tuple(params),
            "fallback",
            "Conditional fallback or availability probe",
            None,
            None,
            None,
        )

    # Materialized surfaces read the scheduled table when the source router
    # selects the aggregate path.
    if key in DT_BODY_REGISTRY and source_for(key, settings) == "dt":
        if key == SURFACE_MODEL_RATES:
            sql, params = _model_rates_dt_sql(
                settings.database, settings.schema, settings.object_prefix
            )
            return ReferenceStatement(
                sql, tuple(params), "aggregate", "Global aggregate table", None, None, key
            )
        served_start, served_end = effective_served_window(
            start_date, end_date, accelerated=True, today=today
        )
        table_name = f"{settings.object_prefix}_{surface_to_suffix(key)}"
        fqn = (
            f"{quote_identifier(settings.database)}"
            f".{quote_identifier(settings.schema)}"
            f".{quote_identifier(table_name)}"
        )
        # Format the read multi-line so the code block wraps in the UI instead
        # of scrolling horizontally: one column per line, and each clause on
        # its own line.
        cols = ",\n    ".join(_DT_COLUMNS[key])
        sql = (
            f"SELECT\n    {cols}\n"
            f"FROM {fqn}\n"
            "WHERE USAGE_DATE >= ?::DATE\n"
            "  AND USAGE_DATE < ?::DATE"
        )
        params = (
            f"{served_start:%Y-%m-%d}",
            f"{served_end + dt.timedelta(days=1):%Y-%m-%d}",
        )
        return ReferenceStatement(
            sql,
            params,
            "aggregate",
            "Scheduled aggregate table",
            served_start,
            served_end,
            key,
        )

    # Global metadata: date-independent, cached with a long TTL.
    if DEPENDENCY_KINDS.get(key) == "global_cached":
        sql, params = entry.builder(start_date, end_date)
        label = (
            "Global cached model-rate table"
            if key == SURFACE_MODEL_RATES
            else "Current USER-domain metadata"
        )
        return ReferenceStatement(sql, tuple(params), "global_cached", label, None, None, None)

    # Direct: live ACCOUNT_USAGE for the requested window.
    sql, params = entry.builder(start_date, end_date)
    return ReferenceStatement(
        sql, tuple(params), "direct", "Live SNOWFLAKE.ACCOUNT_USAGE", start_date, end_date, None
    )


# ---------------------------------------------------------------------------
# Query acceleration educational catalogue
#
# Teaches how the aggregate layer is built and read. Every entry maps to a
# DT_BODY_REGISTRY surface; the SQL shown is generated from the same builders
# Acceleration Settings and sql/config.sql use, so the page can never drift from what
# the app provisions.
# ---------------------------------------------------------------------------

_RUNTIME_KEY_TO_REFERENCE: dict[str, SqlReference] = {
    entry.runtime_key: entry for entry in SQL_REFERENCE if entry.runtime_key is not None
}


class AccelerationSqlReference(NamedTuple):
    """One surface's entry in the query-acceleration educational catalogue.

    Attributes:
        surface_key: A :data:`DT_BODY_REGISTRY` key.
        object_type: ``WINDOWED_AGGREGATE`` for date-windowed tables served
            through the prior UTC day, or ``GLOBAL_AGGREGATE`` for the global
            Model Rates table.
        source_views: The ACCOUNT_USAGE views the scheduled body reads.
        explanation: Why this aggregate exists and how the dashboard uses it.
    """

    surface_key: str
    object_type: Literal["WINDOWED_AGGREGATE", "GLOBAL_AGGREGATE"]
    source_views: tuple[str, ...]
    explanation: str


_ACCELERATION_EXPLANATIONS: dict[str, str] = {
    SURFACE_COCO: (
        "Flattens the unified CoCo usage view by token/credit model and aggregates to one row "
        "per surface, day, user, model, role and region so the CoCo page reads a small table "
        "instead of re-flattening history on every load."
    ),
    SURFACE_AI_FUNCTIONS: (
        "Pre-sums the METRICS token array and credits per function, model and query tag so the "
        "AI Functions page avoids the per-row REDUCE over ACCOUNT_USAGE."
    ),
    SURFACE_AGENTS: (
        "Rolls Cortex Agents requests up per agent and user, splitting root from child requests "
        "via PARENT_REQUEST_ID."
    ),
    SURFACE_COWORK: (
        "Aggregates CoWork per instance, agent and user, keeping the separately-billed "
        "sql_query_credits and ai_functions_credits as their own columns."
    ),
    f"{SURFACE_COWORK} — Service Mix": (
        "Splits each CoWork turn across service types and models by flattening TOKENS_GRANULAR "
        "and CREDITS_GRANULAR four levels deep, then joining tokens to credits per request."
    ),
    f"{SURFACE_SEARCH} — Daily": (
        "Daily Cortex Search usage by service, consumption type and model — the primary Search "
        "fact and the one the Total Consumption overview reads."
    ),
    f"{SURFACE_SEARCH} — Serving": (
        "Hourly query-time serving credits per service, for the Search page's serving trend."
    ),
    f"{SURFACE_SEARCH} — Batch": (
        "Batch (AI_COMPLETE-style) query credits per QUERY_ID, joined to warehouse attribution "
        "on the Search page for per-query compute cost."
    ),
    SURFACE_WAREHOUSE_ATTRIBUTION: (
        "Unifies QUERY_ATTRIBUTION_HISTORY and QUERY_METERING_HISTORY into one row per QUERY_ID, "
        "classified to the Cortex surface that incurred it, for cross-surface warehouse cost."
    ),
    SURFACE_MODEL_RATES: (
        "A global per-model, per-token-type rate table computed over a fixed 90-day CoCo window. "
        "The Cortex Inference API page multiplies these rates by REST token counts to estimate "
        "credits. Not date-windowed, so it is refreshed and read as a whole."
    ),
}


def _acceleration_source_views(surface_key: str) -> tuple[str, ...]:
    entry = _RUNTIME_KEY_TO_REFERENCE.get(surface_key)
    if entry is not None:
        return entry.sources
    return (f"{AU}.{COCO_USAGE_VIEW}",)


ACCELERATION_SQL_REFERENCE: tuple[AccelerationSqlReference, ...] = tuple(
    AccelerationSqlReference(
        surface_key=key,
        object_type="GLOBAL_AGGREGATE" if key == SURFACE_MODEL_RATES else "WINDOWED_AGGREGATE",
        source_views=_acceleration_source_views(key),
        explanation=_ACCELERATION_EXPLANATIONS.get(key, ""),
    )
    for key in DT_BODY_REGISTRY
)


def acceleration_table_sql(surface_key: str, settings: "Settings") -> str:
    """Return the CREATE TABLE DDL the app uses to provision one aggregate table."""
    from utils.config import create_aggregate_table_sql

    return create_aggregate_table_sql(
        DT_BODY_REGISTRY[surface_key], settings.database, settings.schema, settings.object_prefix
    )


def acceleration_task_sql(surface_key: str, settings: "Settings") -> str:
    """Return the CREATE TASK DDL the app uses to refresh one aggregate table."""
    from utils.config import create_refresh_task_sql

    return create_refresh_task_sql(
        DT_BODY_REGISTRY[surface_key],
        settings.database,
        settings.schema,
        settings.object_prefix,
        settings.warehouse_name or f"{settings.object_prefix}_WH",
    )


def acceleration_lifecycle_sql(surface_key: str, settings: "Settings") -> dict[str, str]:
    """Return labelled lifecycle statements for one surface's task and table.

    The ``Drop resource`` entry is destructive and is for documentation only;
    the acceleration page never offers it as a runnable action.
    """
    from utils.config import (
        create_refresh_warehouse_sql,
        drop_object_sql,
        execute_task_sql,
        resume_task_sql,
        suspend_task_sql,
    )
    from utils.snowflake import quote_identifier, surface_to_suffix

    suffix = surface_to_suffix(surface_key)
    prefix = settings.object_prefix
    wh = settings.warehouse_name or f"{prefix}_WH"
    task_fqn = (
        f"{quote_identifier(settings.database)}"
        f".{quote_identifier(settings.schema)}"
        f".{quote_identifier(f'{prefix}_TASK_{suffix}')}"
    )
    return {
        "Create task warehouse": create_refresh_warehouse_sql(wh, settings.warehouse_size),
        "Create aggregate table": acceleration_table_sql(surface_key, settings),
        "Create refresh task": acceleration_task_sql(surface_key, settings),
        "Enable schedule": resume_task_sql(task_fqn),
        "Run now": execute_task_sql(task_fqn),
        "Pause schedule": suspend_task_sql(task_fqn),
        "Drop resource": drop_object_sql(task_fqn, "TASK"),
    }


# ---------------------------------------------------------------------------
# Page dependency classification
#
# Every navigable page declares its data dependencies here. Dependencies are
# classified as:
#   windowed_aggregate  — backed by a scheduled aggregate table in DT_BODY_REGISTRY;
#                         served through the settled UTC day when acceleration is on
#   global_cached       — date-independent metadata (model rates, user tags);
#                         always read from ACCOUNT_USAGE but cached with a long TTL
#   direct_only         — must always read ACCOUNT_USAGE; not aggregate-eligible
#
# This is the single source of truth for source-mode messaging, cache-key
# identity, active-page warming scope, and parity assertions.
# ---------------------------------------------------------------------------

# Navigation page-key constants for non-surface pages.
PAGE_TOTAL_CONSUMPTION = "Total Consumption"
PAGE_SQL = "Reference SQL"
PAGE_QUERY_ACCELERATION_SQL = "Query Acceleration SQL"
PAGE_CONFIG = "Config"

DepKind = Literal["windowed_aggregate", "global_cached", "direct_only"]

# Display names for dependency keys that are not already human-readable surface
# titles. Used by the loading dialog; diagnostics keep the raw key.
DEPENDENCY_LABELS = {"user_tags": "User tags"}


def dependency_label(dependency: str) -> str:
    """Return the user-facing name of a dependency key."""
    return DEPENDENCY_LABELS.get(dependency, dependency)


# Every recognized dependency key mapped to its classification. Tests reject any
# dep key referenced in PAGE_REFRESH_PLANS that is absent from this dict.
DEPENDENCY_KINDS: dict[str, DepKind] = {
    SURFACE_COCO: "windowed_aggregate",
    SURFACE_AI_FUNCTIONS: "windowed_aggregate",
    SURFACE_AGENTS: "windowed_aggregate",
    SURFACE_COWORK: "windowed_aggregate",
    f"{SURFACE_COWORK} — Service Mix": "windowed_aggregate",
    f"{SURFACE_SEARCH} — Daily": "windowed_aggregate",
    f"{SURFACE_SEARCH} — Serving": "windowed_aggregate",
    f"{SURFACE_SEARCH} — Batch": "windowed_aggregate",
    SURFACE_WAREHOUSE_ATTRIBUTION: "windowed_aggregate",
    SURFACE_MODEL_RATES: "global_cached",
    "user_tags": "global_cached",
    SURFACE_REST_API: "direct_only",
}


class RefreshPlan(NamedTuple):
    """Declared data dependencies for one navigable page.

    Attributes:
        page_key: Navigation page title, matching :data:`PAGE_REFRESH_PLANS` key.
        windowed: Dep keys backed by scheduled aggregate tables.
        global_cached: Dep keys for date-independent cached metadata.
        direct_only: Dep keys that always read ACCOUNT_USAGE directly.
        uses_settled_window: True when the page's served range caps at the
            settled UTC day while acceleration is active.
    """

    page_key: str
    windowed: tuple[str, ...]
    global_cached: tuple[str, ...]
    direct_only: tuple[str, ...]
    uses_settled_window: bool


# Maps every navigable page to exactly one declared RefreshPlan. Pages with no
# data dependencies (Reference SQL, Config) have empty dep tuples.
PAGE_REFRESH_PLANS: dict[str, RefreshPlan] = {
    PAGE_TOTAL_CONSUMPTION: RefreshPlan(
        page_key=PAGE_TOTAL_CONSUMPTION,
        windowed=(
            SURFACE_COCO,
            SURFACE_COWORK,
            SURFACE_AGENTS,
            SURFACE_AI_FUNCTIONS,
            f"{SURFACE_SEARCH} — Daily",
        ),
        global_cached=(SURFACE_MODEL_RATES, "user_tags"),
        direct_only=(SURFACE_REST_API,),
        uses_settled_window=True,
    ),
    SURFACE_COCO: RefreshPlan(
        page_key=SURFACE_COCO,
        windowed=(SURFACE_COCO, SURFACE_WAREHOUSE_ATTRIBUTION),
        global_cached=("user_tags",),
        direct_only=(),
        uses_settled_window=True,
    ),
    SURFACE_COWORK: RefreshPlan(
        page_key=SURFACE_COWORK,
        windowed=(SURFACE_COWORK, f"{SURFACE_COWORK} — Service Mix", SURFACE_WAREHOUSE_ATTRIBUTION),
        global_cached=("user_tags",),
        direct_only=(),
        uses_settled_window=True,
    ),
    SURFACE_AGENTS: RefreshPlan(
        page_key=SURFACE_AGENTS,
        windowed=(SURFACE_AGENTS, SURFACE_WAREHOUSE_ATTRIBUTION),
        global_cached=("user_tags",),
        direct_only=(),
        uses_settled_window=True,
    ),
    SURFACE_SEARCH: RefreshPlan(
        page_key=SURFACE_SEARCH,
        windowed=(
            f"{SURFACE_SEARCH} — Daily",
            f"{SURFACE_SEARCH} — Serving",
            f"{SURFACE_SEARCH} — Batch",
            SURFACE_WAREHOUSE_ATTRIBUTION,
        ),
        global_cached=(),
        direct_only=(),
        uses_settled_window=True,
    ),
    SURFACE_REST_API: RefreshPlan(
        page_key=SURFACE_REST_API,
        windowed=(),
        global_cached=(SURFACE_MODEL_RATES, "user_tags"),
        direct_only=(SURFACE_REST_API,),
        uses_settled_window=False,
    ),
    SURFACE_AI_FUNCTIONS: RefreshPlan(
        page_key=SURFACE_AI_FUNCTIONS,
        windowed=(SURFACE_AI_FUNCTIONS, SURFACE_WAREHOUSE_ATTRIBUTION),
        global_cached=("user_tags",),
        direct_only=(),
        uses_settled_window=True,
    ),
    SURFACE_WAREHOUSE_ATTRIBUTION: RefreshPlan(
        page_key=SURFACE_WAREHOUSE_ATTRIBUTION,
        windowed=(SURFACE_WAREHOUSE_ATTRIBUTION,),
        global_cached=(),
        direct_only=(),
        uses_settled_window=True,
    ),
    PAGE_SQL: RefreshPlan(
        page_key=PAGE_SQL,
        windowed=(),
        global_cached=(),
        direct_only=(),
        uses_settled_window=False,
    ),
    PAGE_QUERY_ACCELERATION_SQL: RefreshPlan(
        page_key=PAGE_QUERY_ACCELERATION_SQL,
        windowed=(),
        global_cached=(),
        direct_only=(),
        uses_settled_window=False,
    ),
    PAGE_CONFIG: RefreshPlan(
        page_key=PAGE_CONFIG,
        windowed=(),
        global_cached=(),
        direct_only=(),
        uses_settled_window=False,
    ),
}


def warm_dependencies_for(page_key: str) -> tuple[str, ...]:
    """Return only declared page dependencies, with rate inputs before inference."""
    page_plan = PAGE_REFRESH_PLANS.get(page_key, RefreshPlan(page_key, (), (), (), False))
    dependencies = (
        *page_plan.windowed,
        *page_plan.global_cached,
        *page_plan.direct_only,
    )
    return tuple(dict.fromkeys(dependencies))


def initial_dependencies_for(page_key: str) -> tuple[str, ...]:
    """Warm primary facts and filter metadata; detail controls own auxiliary reads."""
    optional = {
        f"{SURFACE_COWORK} — Service Mix",
        f"{SURFACE_SEARCH} — Serving",
        f"{SURFACE_SEARCH} — Batch",
    }
    if page_key != SURFACE_WAREHOUSE_ATTRIBUTION:
        optional.add(SURFACE_WAREHOUSE_ATTRIBUTION)
    return tuple(
        dependency for dependency in warm_dependencies_for(page_key) if dependency not in optional
    )


def main_dependency_for(page_key: str) -> str:
    """Map the overview and Search page identities to their main facts."""
    return f"{SURFACE_SEARCH} — Daily" if page_key == SURFACE_SEARCH else page_key


def aggregate_dependencies_for(page_key: str) -> tuple[str, ...]:
    """Return aggregate dependencies that define one page's cache generation."""
    return tuple(
        key
        for key in warm_dependencies_for(page_key)
        if DEPENDENCY_KINDS[key] == "windowed_aggregate"
    )


def dependency_generation(dep: str, settings: "Settings") -> str:
    """Return the aggregate generation a dependency is currently served at.

    Windowed-aggregate dependencies carry the scheduled table's snapshot
    timestamp, so a nightly refresh advances the value and invalidates only
    the pages that read that dependency. Global-cached and direct-only
    dependencies are window-stable and return an empty string.

    Args:
        dep: A :data:`DEPENDENCY_KINDS` key.
        settings: Current acceleration-layer settings.

    Returns:
        The snapshot timestamp for a healthy windowed aggregate, else "".
    """
    if DEPENDENCY_KINDS.get(dep) != "windowed_aggregate":
        return ""
    from utils.config import aggregate_snapshot_identity_for

    return aggregate_snapshot_identity_for((dep,), settings)[1]


def dependencies_to_warm(
    needed: tuple[str, ...],
    warmed: dict[str, str],
    generations: dict[str, str],
) -> list[str]:
    """Return the subset of needed dependencies that are not already warm.

    A dependency is warm when it was warmed for the current window at the same
    generation it now resolves to. Anything missing, or warmed at a stale
    generation, must be re-warmed.

    Args:
        needed: Dependencies the active page requires.
        warmed: Map of already-warmed dependency to the generation it was
            warmed at, for the current window.
        generations: Map of dependency to its current generation.

    Returns:
        Dependencies to warm, preserving the order in ``needed``.
    """
    return [dep for dep in needed if warmed.get(dep) != generations.get(dep, "")]


def load_dependency(dependency: str, start_date: dt.date, end_date: dt.date) -> object:
    """Load one declared dependency through its production loader."""
    loaders: dict[str, Callable[[], object]] = {
        SURFACE_COCO: lambda: load_coco_facts(start_date, end_date),
        SURFACE_COWORK: lambda: load_cowork_facts(start_date, end_date),
        SURFACE_AGENTS: lambda: load_agent_facts(start_date, end_date),
        SURFACE_AI_FUNCTIONS: lambda: load_ai_fn_facts(start_date, end_date),
        f"{SURFACE_COWORK} — Service Mix": lambda: load_cowork_service_mix(start_date, end_date),
        f"{SURFACE_SEARCH} — Daily": lambda: load_search_daily_facts(start_date, end_date),
        f"{SURFACE_SEARCH} — Serving": lambda: load_search_serving_facts(start_date, end_date),
        f"{SURFACE_SEARCH} — Batch": lambda: load_search_batch_facts(start_date, end_date),
        SURFACE_WAREHOUSE_ATTRIBUTION: lambda: load_warehouse_credit_facts(start_date, end_date),
        SURFACE_MODEL_RATES: load_model_rates,
        "user_tags": load_user_tags,
        SURFACE_REST_API: lambda: load_rest_facts(start_date, end_date),
    }
    return loaders[dependency]()


def load_dependency_result(
    dependency: str, start_date: dt.date, end_date: dt.date
) -> LoadedDependency:
    """Load a dependency with explicit unavailable and coverage outcomes.

    Args:
        dependency: Registry key identifying the requested source.
        start_date: First requested UTC day.
        end_date: Last requested UTC day.

    Returns:
        Facts and evidence from the same read. Failed reads carry no fetch time
        or served range and never masquerade as successful empty usage.
    """
    if dependency not in DEPENDENCY_KINDS:
        raise ValueError(f"Unknown dependency: {dependency}")
    key = _acquisition_key(dependency, start_date, end_date)
    if dependency_is_ready(dependency, start_date, end_date):
        loaded = st.session_state.acquisition_results[key][1]
        return LoadedDependency(loaded.frame.copy(deep=True), loaded.coverage)
    reason = None
    request_id = st.session_state.get("load_request_id")
    token = _active_dependency.set(dependency)
    try:
        loaded = load_dependency(dependency, start_date, end_date)
    except Exception as exc:
        _logger.exception("Dependency unavailable: %s", dependency)
        reason = str(exc)
        loaded = None
    finally:
        _active_dependency.reset(token)
    if request_id != st.session_state.get("load_request_id"):
        key = None
    if loaded is not None:
        frame = loaded.frame if isinstance(loaded, WarehouseFactsResult) else loaded
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"No data frame returned by {dependency}")
        coverage = frame.attrs.get("coverage")
        if (
            isinstance(loaded, WarehouseFactsResult)
            and loaded.degraded
            and frame.empty
            and coverage is None
        ):
            reason = loaded.message or "Warehouse attribution unavailable"
        elif not isinstance(coverage, SourceCoverage):
            frame = _with_coverage(
                frame,
                dependency,
                start_date,
                end_date,
                frame.attrs.get("actual_source", "unknown"),
            )
            coverage = frame.attrs["coverage"]
        if reason is None and isinstance(coverage, SourceCoverage):
            return _remember_acquisition(key, LoadedDependency(frame.copy(deep=True), coverage))
    coverage = _unavailable_coverage(dependency, start_date, end_date, reason)
    frame = pd.DataFrame()
    frame.attrs["coverage"] = coverage
    return _remember_acquisition(key, LoadedDependency(frame, coverage))


def _unavailable_coverage(
    dependency: str, start: dt.date, end: dt.date, reason: str | None
) -> SourceCoverage:
    """Record a failed read without fabricating acquisition or served dates."""
    return SourceCoverage(
        dependency=dependency,
        actual_source="unavailable",
        fetched_at=None,
        requested=DateWindow(start, end),
        served=None,
        state=CoverageState.UNAVAILABLE,
        reason=reason or "Source returned no result",
    )


def load_direct_dependency(dependency: str, start_date: dt.date, end_date: dt.date) -> pd.DataFrame:
    """Read an optional partial-day source without changing acceleration settings.

    Args:
        dependency: Supported usage dependency, not global metadata.
        start_date: First UTC day to request.
        end_date: Last UTC day to request.

    Returns:
        Direct facts with source evidence, cached separately from aggregate SQL.
    """
    if dependency == SURFACE_REST_API:
        return load_rest_facts(start_date, end_date)
    builders = {
        SURFACE_COCO: coco_facts_sql,
        SURFACE_COWORK: cowork_facts_sql,
        SURFACE_AGENTS: agent_facts_sql,
        SURFACE_AI_FUNCTIONS: ai_fn_facts_sql,
        SURFACE_REST_API: rest_facts_sql,
        f"{SURFACE_SEARCH} — Daily": search_daily_facts_sql,
        f"{SURFACE_SEARCH} — Serving": search_serving_facts_sql,
        f"{SURFACE_SEARCH} — Batch": search_batch_facts_sql,
        f"{SURFACE_COWORK} — Service Mix": cowork_service_mix_sql,
        SURFACE_WAREHOUSE_ATTRIBUTION: warehouse_credit_facts_sql,
    }
    if dependency not in builders:
        raise ValueError(f"No direct usage builder for {dependency}")
    return _with_coverage(
        run_query(*builders[dependency](start_date, end_date)),
        dependency,
        start_date,
        end_date,
        "direct",
    )
