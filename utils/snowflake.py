"""Shared Snowflake primitives for the Cortex AI Consumption Dashboard.

This module is the leaf of the import graph: ``utils/config.py`` and
``utils/queries.py`` both import from here but never from each other.
It owns the Streamlit connection, identifier validation, raw (non-cached)
execution helpers, and the ``DtBodySpec`` dataclass used by the acceleration
layer.
"""

import datetime
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

import pandas as pd
import streamlit as st

from utils.admin import require_admin


@st.cache_resource(show_spinner=False)
def connection_lock():
    """Serialize access to the owner's shared stateful Snowflake connection."""
    return RLock()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def _connection():
    """Return the shared Streamlit Snowflake connection."""
    from streamlit.connections import SnowflakeConnection

    configured_ttl = os.getenv("SNOWFLAKE_CONNECTION_TTL")
    ttl = None
    if configured_ttl:
        try:
            ttl = float(configured_ttl)
        except ValueError:
            duration = pd.Timedelta(configured_ttl)
            if pd.isna(duration):
                raise ValueError("SNOWFLAKE_CONNECTION_TTL must be a valid duration.") from None
            ttl = float(duration.total_seconds())
        if not math.isfinite(ttl) or ttl < 0:
            raise ValueError("SNOWFLAKE_CONNECTION_TTL must be finite and non-negative.")
    return st.connection("snowflake", type=SnowflakeConnection, ttl=ttl)


# ---------------------------------------------------------------------------
# Identifier safety
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MAX_IDENT_LEN = 255


def quote_identifier(name: str) -> str:
    """Validate and double-quote a Snowflake identifier in uppercase form.

    Accepts single-part names only (no dots). Rejects SQL injection vectors
    (semicolons, comment tokens, whitespace). Canonicalizes to uppercase so
    all created objects are stored and compared in a consistent form.

    Returns:
        A double-quoted uppercase identifier, e.g. ``'"MY_WAREHOUSE"'``.

    Raises:
        ValueError: If *name* is invalid.
    """
    if not name or len(name) > _MAX_IDENT_LEN:
        raise ValueError(
            f"Identifier must be 1-{_MAX_IDENT_LEN} characters, got {len(name) if name else 0}."
        )
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Identifier {name!r} contains invalid characters. "
            "Only letters, digits, underscores, and dollar signs are allowed "
            "(must start with a letter or underscore)."
        )
    for bad in (";", "--", "/*", "*/"):
        if bad in name:
            raise ValueError(f"Identifier {name!r} contains forbidden token {bad!r}.")
    if "." in name:
        raise ValueError(f"Identifier {name!r} contains a dot — use single-part names only.")
    if any(c.isspace() for c in name):
        raise ValueError(f"Identifier {name!r} contains whitespace.")
    return f'"{name.upper()}"'


def surface_to_suffix(surface_key: str) -> str:
    """Convert a surface key to a clean table/task name suffix.

    Handles em-dash separators (e.g. "CoWork — Service Mix" -> "COWORK_SERVICE_MIX")
    without leaving double underscores.
    """
    return surface_key.upper().replace(" — ", "_").replace("—", "_").replace(" ", "_")


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


class OwnershipConflictError(Exception):
    """Raised when a DDL target exists but is not in the app's owned_objects."""

    def __init__(self, fqn: str):  # noqa: D107
        self.fqn = fqn
        super().__init__(
            f"Object {fqn} already exists and was not created by this app. "
            "Rename it or choose a different prefix."
        )


# ---------------------------------------------------------------------------
# Raw (non-cached) execution
# ---------------------------------------------------------------------------


def _render_sql_literal(val: object) -> str:
    r"""Render a Python value as a safe Snowflake SQL literal string.

    Supported types and their SQL representations:

    * ``None``               → ``NULL``
    * ``bool``               → ``TRUE`` / ``FALSE``
    * ``int``                → bare integer, e.g. ``42``
    * ``float``              → bare decimal/scientific; raises for non-finite
    * ``str``                → single-quoted, ``'`` doubled, ``\\`` doubled
    * ``datetime.datetime``  → ``TIMESTAMP 'YYYY-MM-DD HH:MM:SS'``
    * ``datetime.date``      → ``DATE 'YYYY-MM-DD'``

    Raises:
        ValueError: For non-finite float values.
        TypeError:  For any unsupported type.
    """
    if val is None:
        return "NULL"
    # bool must be checked before int because bool is a subclass of int.
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ValueError(f"Cannot render non-finite float as SQL literal: {val!r}")
        return repr(val)
    if isinstance(val, str):
        return "'" + val.replace("\\", "\\\\").replace("'", "''") + "'"
    # datetime.datetime is a subclass of datetime.date — check it first.
    if isinstance(val, datetime.datetime):
        return f"TIMESTAMP '{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, datetime.date):
        return f"DATE '{val.strftime('%Y-%m-%d')}'"
    raise TypeError(
        f"Unsupported type for SQL literal: {type(val).__name__!r}. "
        "Accepted types are None, bool, int, float, str, datetime.date, datetime.datetime."
    )


def _bind_params(sql: str, params: tuple) -> str:
    """Replace ``?`` placeholders in *sql* with rendered SQL literals from *params*.

    Substitution is performed left-to-right via :func:`re.sub`, which does
    **not** re-scan the replacement string — so a ``?`` inside a rendered
    string literal cannot become a new placeholder.

    Raises:
        ValueError: If the number of ``?`` placeholders in *sql* does not
            match ``len(params)``.
    """
    placeholder_count = sql.count("?")
    if placeholder_count != len(params):
        raise ValueError(
            f"SQL has {placeholder_count} placeholder(s) but {len(params)} param(s) were given."
        )
    if not params:
        return sql
    rendered = iter(_render_sql_literal(v) for v in params)
    return re.sub(r"\?", lambda _: next(rendered), sql)


def _execute_raw(sql: str, params: tuple = ()) -> None:
    """Execute a DDL/DML statement without caching.

    Used by the config layer for CREATE/ALTER/DROP/MERGE operations that must
    not go through Streamlit's ``@st.cache_data``. Uses the Snowpark session
    which handles DDL without requiring a fetchable result set.
    """
    require_admin()
    with connection_lock():
        conn = _connection()
        session = conn.session()
        session.sql(_bind_params(sql, params)).collect()


def _fetch_raw(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Execute a query without caching and return the result as a DataFrame.

    Used for status checks (SHOW TASKS, INFORMATION_SCHEMA queries) that must
    reflect the current state, not a cached snapshot.
    """
    with connection_lock():
        conn = _connection()
        session = conn.session()
        rows = session.sql(_bind_params(sql, params)).collect()
    if not rows:
        return pd.DataFrame()
    # Convert Snowpark Rows to DataFrame
    df = pd.DataFrame([row.as_dict() for row in rows])
    df.columns = [c.upper() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# DtBodySpec — registry value for the acceleration layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DtBodySpec:
    """Specification for one scheduled aggregate table in the acceleration layer.

    Attributes:
        surface_key: The SURFACE_* constant this spec belongs to.
        body_fn: Callable returning the windowless SELECT SQL string for the
            aggregate table body. Must not open a Snowflake connection at
            import time.
        create_probe: Optional callable that runs a Snowflake probe at
            create-time to decide which body variant to use (e.g. warehouse
            attribution full vs attribution-only). Returns a variant key.
        degraded_flag_key: Optional settings key that records whether the
            surface is running in a degraded/partial mode.
        columns: Explicit ordered column contract for the scheduled table.
            When non-empty, CREATE TABLE and INSERT OVERWRITE statements use
            this list instead of SELECT *, guaranteeing schema stability.
        cluster_column: Optional column used in the table's CLUSTER BY clause.
            Set to None for global tables without a date dimension.
        apply_outer_retention: Whether the task builder applies the standard
            USAGE_DATE retention predicate around the body SQL.
    """

    surface_key: str
    body_fn: Callable[[], str]
    create_probe: Callable[[], str] | None = None
    degraded_flag_key: str | None = None
    columns: tuple[str, ...] = ()
    cluster_column: str | None = "USAGE_DATE"
    apply_outer_retention: bool = True
