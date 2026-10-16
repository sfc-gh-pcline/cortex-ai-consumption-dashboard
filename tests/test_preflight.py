"""Offline unit tests for :mod:`utils.preflight`.

All tests here run without a live Snowflake connection by exercising only the
pure helper functions (_check_warehouse, _check_account_usage) and the
PreflightResult aggregation logic.  run_preflight() itself is not tested here
because it requires a live session; it carries the ``snowflake`` marker.
"""

from __future__ import annotations

import pandas as pd
import pytest

from utils.preflight import (
    CheckLevel,
    CheckResult,
    PreflightResult,
    _check_account_usage,
    _check_warehouse,
)

# ---------------------------------------------------------------------------
# PreflightResult helpers
# ---------------------------------------------------------------------------


class TestPreflightResult:
    def test_ok_when_all_checks_are_ok(self):
        r = PreflightResult(
            checks=[
                CheckResult("A", CheckLevel.OK, "fine"),
                CheckResult("B", CheckLevel.OK, "fine"),
            ]
        )
        assert r.ok is True

    def test_ok_when_only_warnings(self):
        r = PreflightResult(checks=[CheckResult("A", CheckLevel.WARNING, "meh")])
        assert r.ok is True

    def test_not_ok_when_any_error(self):
        r = PreflightResult(
            checks=[
                CheckResult("A", CheckLevel.OK, "fine"),
                CheckResult("B", CheckLevel.ERROR, "broken"),
            ]
        )
        assert r.ok is False

    def test_has_warnings_false_when_clean(self):
        r = PreflightResult(checks=[CheckResult("A", CheckLevel.OK, "fine")])
        assert r.has_warnings is False

    def test_has_warnings_true(self):
        r = PreflightResult(checks=[CheckResult("A", CheckLevel.WARNING, "watch out")])
        assert r.has_warnings is True

    def test_errors_filters_correctly(self):
        r = PreflightResult(
            checks=[
                CheckResult("A", CheckLevel.OK, "fine"),
                CheckResult("B", CheckLevel.ERROR, "bad"),
                CheckResult("C", CheckLevel.WARNING, "meh"),
            ]
        )
        errs = r.errors()
        assert len(errs) == 1
        assert errs[0].name == "B"

    def test_warnings_filters_correctly(self):
        r = PreflightResult(
            checks=[
                CheckResult("A", CheckLevel.WARNING, "w1"),
                CheckResult("B", CheckLevel.ERROR, "e1"),
                CheckResult("C", CheckLevel.OK, "ok"),
            ]
        )
        warns = r.warnings()
        assert len(warns) == 1
        assert warns[0].name == "A"

    def test_empty_checks_is_ok(self):
        assert PreflightResult().ok is True


# ---------------------------------------------------------------------------
# _check_warehouse
# ---------------------------------------------------------------------------


class TestCheckWarehouse:
    def test_ok_when_warehouse_is_set(self):
        row = pd.Series({"WAREHOUSE": "MY_WH", "ACCOUNT": "acc123"})
        result = _check_warehouse(row)
        assert result.level == CheckLevel.OK
        assert "MY_WH" in result.detail

    def test_warning_when_warehouse_is_null(self):
        row = pd.Series({"WAREHOUSE": None})
        result = _check_warehouse(row)
        assert result.level == CheckLevel.WARNING
        assert result.remediation  # has guidance

    def test_warning_when_warehouse_is_empty_string(self):
        row = pd.Series({"WAREHOUSE": ""})
        result = _check_warehouse(row)
        assert result.level == CheckLevel.WARNING

    def test_warning_on_empty_series(self):
        result = _check_warehouse(pd.Series(dtype="object"))
        assert result.level == CheckLevel.WARNING

    @pytest.mark.parametrize("warehouse", ["COMPUTE_WH", "MY_WH_2", "WH$1"])
    def test_ok_for_various_warehouse_names(self, warehouse):
        row = pd.Series({"WAREHOUSE": warehouse})
        assert _check_warehouse(row).level == CheckLevel.OK


# ---------------------------------------------------------------------------
# _check_account_usage
# ---------------------------------------------------------------------------


class TestCheckAccountUsage:
    def test_ok_when_accessible(self):
        result = _check_account_usage(accessible=True)
        assert result.level == CheckLevel.OK

    def test_error_on_access_denied(self):
        result = _check_account_usage(accessible=False, error_text="Access denied.")
        assert result.level == CheckLevel.ERROR
        assert result.remediation  # has GRANT instruction
        assert "GRANT" in result.remediation

    def test_error_includes_message(self):
        result = _check_account_usage(
            accessible=False, error_text="Permission denied for role PUBLIC"
        )
        assert "Permission denied" in result.detail

    def test_warning_when_no_active_warehouse(self):
        """Failure due to missing warehouse must not show as an access-denied error."""
        result = _check_account_usage(
            accessible=False,
            error_text="No active warehouse selected in the current session.",
        )
        assert result.level == CheckLevel.WARNING
        assert "warehouse" in result.detail.lower()

    def test_warning_on_warehouse_session_error_variant(self):
        result = _check_account_usage(
            accessible=False,
            error_text="Warehouse: session-scoped warehouse is not available.",
        )
        assert result.level == CheckLevel.WARNING

    def test_error_when_no_error_text(self):
        result = _check_account_usage(accessible=False)
        assert result.level == CheckLevel.ERROR
        assert result.detail  # not empty
