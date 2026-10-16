"""Offline behavior tests for the materialized Model Rates source."""

import datetime as dt
import unittest.mock

import pandas as pd

from utils.config import Settings, SurfaceStatus
from utils.queries import (
    SURFACE_MODEL_RATES,
    _load_model_rates_cached,
    _model_rates_dt_sql,
    load_model_rates,
    window_statements,
)


def test_model_rates_dt_sql_uses_explicit_columns():
    sql, params = _model_rates_dt_sql("DB", "SCHEMA", "PREFIX")

    assert sql == (
        "SELECT MODEL_NAME, R_INPUT, R_OUTPUT, R_CACHE_READ, R_CACHE_WRITE "
        'FROM "DB"."SCHEMA"."PREFIX_MODEL_RATES"'
    )
    assert params == ()


def test_model_rates_cache_key_changes_with_aggregate_generation():
    _load_model_rates_cached.clear()
    settings = Settings(enabled=True, database="DB", schema="SCHEMA", object_prefix="PREFIX")
    rates = pd.DataFrame({"MODEL_NAME": ["model"], "R_INPUT": [1.0]})

    with unittest.mock.patch("utils.queries.run_query", return_value=rates) as run_query:
        _load_model_rates_cached("aggregate", "2026-08-20 01:00:00", "DB", "SCHEMA", "PREFIX")
        _load_model_rates_cached("aggregate", "2026-08-21 01:00:00", "DB", "SCHEMA", "PREFIX")

    assert run_query.call_count == 2
    assert settings.enabled


def test_load_model_rates_falls_back_when_aggregate_is_empty():
    settings = Settings(enabled=True, database="DB", schema="SCHEMA", object_prefix="PREFIX")
    status = SurfaceStatus(
        surface_key=SURFACE_MODEL_RATES,
        table_exists=True,
        scheduling_state="ACTIVE",
        data_timestamp="2026-08-20 01:00:00",
    )
    live = pd.DataFrame({"MODEL_NAME": ["live"], "R_INPUT": [1.0]})

    with (
        unittest.mock.patch("utils.config.load_settings", return_value=settings),
        unittest.mock.patch("utils.config.source_for", return_value="dt"),
        unittest.mock.patch(
            "utils.config._health_cache", return_value={SURFACE_MODEL_RATES: status}
        ),
        unittest.mock.patch(
            "utils.queries._load_model_rates_cached", side_effect=[pd.DataFrame(), live]
        ),
        unittest.mock.patch("streamlit.session_state", {}),
    ):
        result = load_model_rates()

    assert result.equals(live)


def test_load_model_rates_uses_live_source_when_acceleration_is_disabled():
    settings = Settings(enabled=False, database="DB", schema="SCHEMA", object_prefix="PREFIX")
    live = pd.DataFrame({"MODEL_NAME": ["live"], "R_INPUT": [1.0]})

    with (
        unittest.mock.patch("utils.config.load_settings", return_value=settings),
        unittest.mock.patch("utils.config.source_for", return_value="account_usage"),
        unittest.mock.patch("utils.queries._load_model_rates_cached", return_value=live) as cached,
    ):
        result = load_model_rates()

    assert result.equals(live)
    assert cached.call_args.args[0] == "direct"


def test_load_model_rates_falls_back_when_aggregate_read_raises():
    settings = Settings(enabled=True, database="DB", schema="SCHEMA", object_prefix="PREFIX")
    status = SurfaceStatus(
        surface_key=SURFACE_MODEL_RATES,
        table_exists=True,
        scheduling_state="ACTIVE",
        data_timestamp="2026-08-20 01:00:00",
    )
    live = pd.DataFrame({"MODEL_NAME": ["live"], "R_INPUT": [1.0]})

    with (
        unittest.mock.patch("utils.config.load_settings", return_value=settings),
        unittest.mock.patch("utils.config.source_for", return_value="dt"),
        unittest.mock.patch(
            "utils.config._health_cache", return_value={SURFACE_MODEL_RATES: status}
        ),
        unittest.mock.patch(
            "utils.queries._load_model_rates_cached",
            side_effect=[RuntimeError("read failed"), live],
        ),
        unittest.mock.patch("streamlit.session_state", {}),
    ):
        result = load_model_rates()

    assert result.equals(live)


def test_model_rates_body_uses_relative_utc_cutoff():
    from utils.queries import model_rates_body_sql

    sql = model_rates_body_sql()
    assert "DATEADD(" in sql
    assert "'day', -90, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())" in sql
    assert "DATE_TRUNC" in sql


def test_window_statements_excludes_live_model_rates_sql():
    settings = Settings(enabled=False, database="DB", schema="SCHEMA", object_prefix="PREFIX")
    with (
        unittest.mock.patch("utils.config.load_settings", return_value=settings),
        unittest.mock.patch("utils.config.source_for", return_value="account_usage"),
        unittest.mock.patch("utils.queries.utc_today", return_value=dt.date(2026, 8, 20)),
    ):
        statements = window_statements(dt.date(2026, 8, 1), dt.date(2026, 8, 7))

    assert all("R_CACHE_WRITE" not in sql for sql, _ in statements)
