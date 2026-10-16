"""Lightweight first-run preflight checks for connection and data-source access.

Provides actionable diagnostics for the three failure modes that produce
misleading empty pages: broken connection, missing warehouse, and
insufficient ACCOUNT_USAGE privileges.  Each check is a pure helper that
takes its inputs as arguments so it can be tested offline without a live
Snowflake session.

Typical call site (``streamlit_app.py``)::

    from utils.preflight import run_preflight

    result = run_preflight()
    if not result.ok:
        for err in result.errors():
            st.error(...)
        st.stop()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CheckLevel(StrEnum):
    """Severity of a single preflight check outcome."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class CheckResult:
    """Outcome of one preflight check.

    Attributes:
        name: Short label for the check, e.g. ``"Query warehouse"``.
        level: Severity: :attr:`CheckLevel.OK`, :attr:`CheckLevel.WARNING`,
            or :attr:`CheckLevel.ERROR`.
        detail: One-sentence description of the finding.
        remediation: Actionable fix instructions; empty string when level is OK.
    """

    name: str
    level: CheckLevel
    detail: str
    remediation: str = ""


@dataclass
class PreflightResult:
    """Aggregated result of all preflight checks.

    Attributes:
        checks: All checks in run order.
    """

    checks: list[CheckResult] = field(default_factory=list)
    account: str = ""
    role: str = ""

    @property
    def ok(self) -> bool:
        """True when no check reached ERROR level."""
        return not any(c.level == CheckLevel.ERROR for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        """True when at least one check reached WARNING level."""
        return any(c.level == CheckLevel.WARNING for c in self.checks)

    def errors(self) -> list[CheckResult]:
        """Return only ERROR-level checks."""
        return [c for c in self.checks if c.level == CheckLevel.ERROR]

    def warnings(self) -> list[CheckResult]:
        """Return only WARNING-level checks."""
        return [c for c in self.checks if c.level == CheckLevel.WARNING]


# ---------------------------------------------------------------------------
# Testable check helpers
# ---------------------------------------------------------------------------


def _check_warehouse(row: pd.Series) -> CheckResult:
    """Evaluate whether the session has a current warehouse.

    Args:
        row: A :class:`~pandas.Series` containing at least a ``WAREHOUSE``
            key, as returned by ``SELECT CURRENT_WAREHOUSE() AS WAREHOUSE``.
            Pass an empty Series to model a failed query.

    Returns:
        A :data:`CheckLevel.WARNING` result when the warehouse is NULL or
        empty, :data:`CheckLevel.OK` otherwise.
    """
    warehouse = row.get("WAREHOUSE") if hasattr(row, "get") else None
    if not warehouse:
        return CheckResult(
            name="Query warehouse",
            level=CheckLevel.WARNING,
            detail="No warehouse is active for this session.",
            remediation=(
                "Set a default warehouse on your role or user: "
                "ALTER USER <name> SET DEFAULT_WAREHOUSE = <warehouse>; "
                "or add `warehouse` to your connections.toml."
            ),
        )
    return CheckResult(
        name="Query warehouse",
        level=CheckLevel.OK,
        detail=f"Warehouse: {warehouse}.",
    )


def _check_account_usage(accessible: bool, error_text: str = "") -> CheckResult:
    """Evaluate read access to ``SNOWFLAKE.ACCOUNT_USAGE``.

    Args:
        accessible: ``True`` when the probe query succeeded without error.
        error_text: The exception message when *accessible* is ``False``.
            Used to distinguish a missing-warehouse failure (which is already
            reported separately) from a genuine privilege gap.

    Returns:
        :data:`CheckLevel.OK` when accessible.  :data:`CheckLevel.WARNING`
        when the error looks like a missing warehouse (can't verify without
        one).  :data:`CheckLevel.ERROR` for any other access denial.
    """
    if not accessible:
        if "no active warehouse" in error_text.lower() or (
            "warehouse" in error_text.lower() and "session" in error_text.lower()
        ):
            return CheckResult(
                name="ACCOUNT_USAGE access",
                level=CheckLevel.WARNING,
                detail="Cannot verify ACCOUNT_USAGE access — no active warehouse in this session.",
                remediation="Set a default warehouse, then reload to re-run the preflight.",
            )
        detail = (
            f"Cannot read SNOWFLAKE.ACCOUNT_USAGE: {error_text}"
            if error_text
            else "Cannot read SNOWFLAKE.ACCOUNT_USAGE."
        )
        return CheckResult(
            name="ACCOUNT_USAGE access",
            level=CheckLevel.ERROR,
            detail=detail,
            remediation=(
                "Grant imported privileges on the shared SNOWFLAKE database to your role: "
                "GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <your_role>;"
            ),
        )
    return CheckResult(
        name="ACCOUNT_USAGE access",
        level=CheckLevel.OK,
        detail="Can read SNOWFLAKE.ACCOUNT_USAGE views.",
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_preflight() -> PreflightResult:
    """Run lightweight connection and data-source checks.

    Runs two queries:

    1. ``SELECT CURRENT_WAREHOUSE()`` — metadata function, works without a
       virtual warehouse, used to detect a missing-warehouse configuration.
    2. ``SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY LIMIT 1``
       — verifies that the current role can read account usage views.

    A failed first query means the connection itself is broken; the second
    query is skipped in that case.

    Returns:
        A :class:`PreflightResult` whose :attr:`~PreflightResult.ok` property
        is ``False`` when any ERROR-level check is present.
    """
    from utils.snowflake import _fetch_raw

    result = PreflightResult()

    # --- 1. Session info (no virtual warehouse required) ---
    try:
        info = _fetch_raw(
            "SELECT CURRENT_ACCOUNT() AS ACCOUNT, "
            "CURRENT_USER() AS USER_NAME, "
            "CURRENT_ROLE() AS ROLE, "
            "CURRENT_WAREHOUSE() AS WAREHOUSE"
        )
        row: pd.Series = info.iloc[0] if not info.empty else pd.Series(dtype="object")
        result.account = str(row.get("ACCOUNT", "") or "")
        result.role = str(row.get("ROLE", "") or "")
        result.checks.append(_check_warehouse(row))
    except Exception as exc:  # noqa: BLE001
        result.checks.append(
            CheckResult(
                name="Connection",
                level=CheckLevel.ERROR,
                detail=f"Cannot connect to Snowflake: {exc}",
                remediation=(
                    "Verify the Snowflake connection credentials in connections.toml "
                    "or the Streamlit app's connection settings."
                ),
            )
        )
        # Cannot meaningfully check ACCOUNT_USAGE without a working connection.
        return result

    # --- 2. ACCOUNT_USAGE access (requires a virtual warehouse) ---
    try:
        _fetch_raw("SELECT 1 AS OK FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY LIMIT 1")
        result.checks.append(_check_account_usage(accessible=True))
    except Exception as exc:  # noqa: BLE001
        result.checks.append(_check_account_usage(accessible=False, error_text=str(exc)))

    return result
