"""Source-aware Reference SQL tests.

Verify that the SQL page renders the statement the dashboard actually runs:
the scheduled-table read when acceleration is active, the ACCOUNT_USAGE builder
otherwise, correct settled-window bounds, and a materialization bridge for
every aggregate-backed entry. Offline: no Snowflake connection needed.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from utils import queries as q
from utils.config import Settings

START = dt.date(2026, 8, 1)
END = dt.date(2026, 8, 7)
TODAY = dt.date(2026, 8, 8)

# Aggregate-eligible settings whose health cache reports the surface as healthy.
_ENABLED = Settings(enabled=True, database="DB", schema="S", object_prefix="PREFIX")
_DISABLED = Settings(enabled=False)


def _entry(label: str) -> q.SqlReference:
    return next(e for e in q.SQL_REFERENCE if e.label == label)


class TestRuntimeKeyCoverage:
    def test_every_entry_has_runtime_key_or_is_fallback(self):
        for entry in q.SQL_REFERENCE:
            if entry.kind == q.KIND_FALLBACK:
                assert entry.runtime_key is None
            else:
                assert entry.runtime_key is not None, entry.label

    def test_runtime_keys_are_registered(self):
        for entry in q.SQL_REFERENCE:
            if entry.runtime_key is not None:
                assert entry.runtime_key in q.DEPENDENCY_KINDS


class TestReferenceStatementDirect:
    def test_windowed_entry_uses_account_usage_when_disabled(self):
        stmt = q.reference_statement_for(_entry(q.SURFACE_COCO), START, END, _DISABLED, today=TODAY)
        assert stmt.source_mode == "direct"
        assert "SNOWFLAKE_COCO_USAGE_HISTORY" in stmt.sql

    def test_rest_is_always_direct(self, monkeypatch):
        monkeypatch.setattr("utils.config.source_for", lambda *_a, **_k: "account_usage")
        monkeypatch.setattr(
            q,
            "load_model_rates",
            lambda: pd.DataFrame(columns=pd.Index(["MODEL_NAME", *q.RATE_COLUMNS])),
        )
        stmt = q.reference_statement_for(
            _entry(q.SURFACE_REST_API), START, END, _ENABLED, today=TODAY
        )
        assert stmt.source_mode == "direct"

    def test_user_tags_is_global_cached(self):
        stmt = q.reference_statement_for(_entry("User Tags"), START, END, _DISABLED, today=TODAY)
        assert stmt.source_mode == "global_cached"
        assert "TAG_REFERENCES" in stmt.sql


class TestReferenceStatementAggregate:
    def test_windowed_entry_reads_scheduled_table_when_healthy(self, monkeypatch):
        monkeypatch.setattr("utils.config.source_for", lambda *_a, **_k: "dt")
        stmt = q.reference_statement_for(_entry(q.SURFACE_COCO), START, END, _ENABLED, today=TODAY)
        rendered = q.inline_params(stmt.sql, stmt.params)
        assert stmt.source_mode == "aggregate"
        assert "PREFIX_COCO" in rendered
        assert "SNOWFLAKE_COCO_USAGE_HISTORY" not in stmt.sql
        assert stmt.materialization_key == q.SURFACE_COCO

    def test_aggregate_window_capped_at_prior_utc_day(self, monkeypatch):
        monkeypatch.setattr("utils.config.source_for", lambda *_a, **_k: "dt")
        # Request through 'today'; the aggregate read must stop at today - 1.
        stmt = q.reference_statement_for(
            _entry(q.SURFACE_COCO), START, TODAY, _ENABLED, today=TODAY
        )
        assert stmt.served_end_date == TODAY - dt.timedelta(days=1)
        rendered = q.inline_params(stmt.sql, stmt.params)
        # Half-open upper bound is served_end + 1 == TODAY, never TODAY + 1.
        assert f"'{TODAY + dt.timedelta(days=1):%Y-%m-%d}'" not in rendered

    def test_model_rates_reads_global_table_when_healthy(self, monkeypatch):
        monkeypatch.setattr("utils.config.source_for", lambda *_a, **_k: "dt")
        stmt = q.reference_statement_for(
            _entry("Model Credit Rates"), START, END, _ENABLED, today=TODAY
        )
        assert stmt.source_mode == "aggregate"
        assert "PREFIX_MODEL_RATES" in stmt.sql
        assert stmt.materialization_key == q.SURFACE_MODEL_RATES

    def test_model_rates_falls_back_to_live_rate_query_when_direct(self, monkeypatch):
        monkeypatch.setattr(q, "utc_today", lambda: TODAY)
        stmt = q.reference_statement_for(
            _entry("Model Credit Rates"), START, END, _DISABLED, today=TODAY
        )
        assert stmt.source_mode == "global_cached"
        assert "SNOWFLAKE_COCO_USAGE_HISTORY" in stmt.sql


class TestReferenceStatementFallback:
    @pytest.mark.parametrize(
        "label",
        [
            f"{q.SURFACE_WAREHOUSE_ATTRIBUTION} — Probe",
            f"{q.SURFACE_WAREHOUSE_ATTRIBUTION} — Attribution-only fallback",
        ],
    )
    def test_probe_and_fallback_are_labelled_fallback(self, label):
        stmt = q.reference_statement_for(_entry(label), START, END, _DISABLED, today=TODAY)
        assert stmt.source_mode == "fallback"
        assert stmt.materialization_key is None


class TestAccelerationMaterializationBridge:
    def test_body_matches_registered_spec_for_every_dt_surface(self):
        for key, spec in q.DT_BODY_REGISTRY.items():
            materialization = q.acceleration_materialization_for(key)
            assert materialization is not None
            assert materialization.body_sql == spec.body_fn()
            assert materialization.columns == spec.columns

    def test_returns_none_for_non_materialized_source(self):
        assert q.acceleration_materialization_for(q.SURFACE_REST_API) is None
        assert q.acceleration_materialization_for("user_tags") is None


class TestInlineParamsHardening:
    def test_renders_missing_numeric_rate_as_sql_null(self):
        assert q.inline_params("SELECT ?::FLOAT, ?", (None, "O'Brien")) == (
            "SELECT NULL::FLOAT, 'O''Brien'"
        )

    def test_preserves_question_mark_inside_value(self):
        assert q.inline_params("SELECT ?, ?", ("why?", "ok")) == "SELECT 'why?', 'ok'"

    def test_escapes_embedded_single_quote(self):
        assert q.inline_params("WHERE name = ?", ("O'Brien",)) == "WHERE name = 'O''Brien'"

    def test_leaves_sql_without_placeholders_alone(self):
        assert q.inline_params("SELECT 1", ()) == "SELECT 1"

    def test_extra_placeholders_without_values_are_left(self):
        assert q.inline_params("SELECT ?, ?", ("a",)) == "SELECT 'a', ?"
