"""Tests for :mod:`utils.config`.

Offline tests: no Snowflake connection needed, safe for CI.
"""

import datetime as dt
import json
import unittest.mock
from typing import cast

import pandas as pd
import pytest

import utils.config as config
from utils.config import (
    ProvisionReport,
    Settings,
    create_aggregate_table_sql,
    create_refresh_task_sql,
    create_refresh_warehouse_sql,
    drop_object_sql,
    effective_served_window,
    execute_task_sql,
    latest_snapshot_freshness,
    resume_task_sql,
    settled_utc_end_date,
    source_for,
    suspend_task_sql,
)
from utils.queries import _dt_read_sql
from utils.snowflake import DtBodySpec

pytestmark = pytest.mark.usefixtures("authorized_admin")


class TestSettings:
    """Verify Settings serialization and round-trip."""

    def test_defaults(self):
        s = Settings()
        assert s.enabled is False
        assert s.owned_objects == []

    def test_owned_objects_json_roundtrip(self):
        s = Settings(
            owned_objects=cast(
                list[config.OwnedObject], ['"DB"."SCHEMA"."OBJ1"', '"DB"."SCHEMA"."OBJ2"']
            )
        )
        serialized = json.dumps(s.owned_objects)
        restored = json.loads(serialized)
        assert restored == s.owned_objects

    def test_wh_degraded_default(self):
        s = Settings()
        assert s.wh_degraded is False

    def test_save_settings_merges_all_values_in_one_statement(self):
        settings = Settings(database="DB", schema="S")
        with (
            unittest.mock.patch("utils.config._ensure_config_table"),
            unittest.mock.patch("utils.config._execute_raw") as execute,
        ):
            config.save_settings(settings)

        sql, params = execute.call_args.args
        assert sql.count("(?, ?)") == 8
        assert len(params) == 16
        assert "FROM VALUES" in sql

    def test_register_owned_objects_persists_new_entries_once(self):
        settings = Settings()
        objects = [
            config.OwnedObject(fqn='"DB"."S"."ONE"', object_type="TABLE"),
            config.OwnedObject(fqn='"DB"."S"."TWO"', object_type="TASK"),
        ]
        with unittest.mock.patch("utils.config.save_settings") as save:
            config.register_owned_objects(objects, settings)

        assert settings.owned_objects == objects
        save.assert_called_once_with(settings)


class TestDDLBuilders:
    """Verify DDL output is bind-free, window-free, and properly quoted."""

    def test_create_refresh_warehouse_sql(self):
        sql = create_refresh_warehouse_sql("my_wh", "MEDIUM")
        assert "IF NOT EXISTS" in sql
        assert '"MY_WH"' in sql
        assert "MEDIUM" in sql
        assert "AUTO_SUSPEND = 60" in sql
        assert "AUTO_RESUME = TRUE" in sql
        assert "?" not in sql  # bind-free

    def test_create_aggregate_table_sql(self):
        spec = DtBodySpec(surface_key="CoCo", body_fn=lambda: "SELECT 1 AS USAGE_DATE")
        sql = create_aggregate_table_sql(spec, "mydb", "myschema", "CORTEX_DASH")
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert '"MYDB"' in sql
        assert '"MYSCHEMA"' in sql
        assert '"CORTEX_DASH_COCO"' in sql
        assert "CLUSTER BY (USAGE_DATE)" in sql
        assert "WHERE 1 = 0" in sql
        assert "?" not in sql  # bind-free

    def test_create_aggregate_table_sql_skips_cluster_when_spec_disables_it(self):
        spec = DtBodySpec(
            surface_key="Model Rates",
            body_fn=lambda: "SELECT 'model' AS MODEL_NAME",
            cluster_column=None,
        )
        sql = create_aggregate_table_sql(spec, "mydb", "myschema", "CORTEX_DASH")
        assert "CLUSTER BY" not in sql

    def test_create_refresh_task_sql(self):
        spec = DtBodySpec(surface_key="CoCo", body_fn=lambda: "SELECT 1 AS USAGE_DATE")
        sql = create_refresh_task_sql(spec, "mydb", "myschema", "CORTEX_DASH", "MY_WH")
        assert "CREATE OR REPLACE TASK" in sql
        assert '"MYDB"' in sql
        assert '"MYSCHEMA"' in sql
        assert '"CORTEX_DASH_TASK_COCO"' in sql
        assert 'WAREHOUSE = "MY_WH"' in sql
        assert "USING CRON 0 1 * * * UTC" in sql
        assert "INSERT OVERWRITE INTO" in sql
        assert "DATEADD" in sql
        assert "365" in sql
        assert "?" not in sql  # bind-free

    def test_create_refresh_task_sql_custom_lag_days(self):
        spec = DtBodySpec(surface_key="CoCo", body_fn=lambda: "SELECT 1 AS USAGE_DATE")
        sql = create_refresh_task_sql(spec, "mydb", "myschema", "CORTEX_DASH", "MY_WH", lag_days=90)
        assert "90" in sql
        assert "365" not in sql

    def test_warehouse_attribution_task_uses_its_body_retention_bound(self):
        spec = DtBodySpec(
            surface_key="Warehouse Attribution",
            body_fn=lambda: "SELECT 1 AS USAGE_DATE",
            apply_outer_retention=False,
        )
        sql = create_refresh_task_sql(spec, "mydb", "myschema", "CORTEX_DASH", "MY_WH")
        assert "WHERE USAGE_DATE" not in sql

    def test_suspend_resume_task(self):
        fqn = '"DB"."S"."T"'
        assert suspend_task_sql(fqn) == 'ALTER TASK "DB"."S"."T" SUSPEND'
        assert resume_task_sql(fqn) == 'ALTER TASK "DB"."S"."T" RESUME'

    def test_execute_task_sql(self):
        fqn = '"DB"."S"."T"'
        assert execute_task_sql(fqn) == 'EXECUTE TASK "DB"."S"."T"'

    def test_drop_object_sql(self):
        sql = drop_object_sql('"DB"."SCHEMA"."OBJ"', "TABLE")
        assert sql == 'DROP TABLE IF EXISTS "DB"."SCHEMA"."OBJ"'

    def test_drop_object_sql_task(self):
        sql = drop_object_sql('"DB"."SCHEMA"."TASK_OBJ"', "TASK")
        assert sql == 'DROP TASK IF EXISTS "DB"."SCHEMA"."TASK_OBJ"'


class TestSourceFor:
    """Verify routing logic without a connection."""

    def test_disabled_returns_account_usage(self):
        s = Settings(enabled=False)
        assert source_for("CoCo", s) == "account_usage"

    def test_rest_always_account_usage(self):
        s = Settings(enabled=True, database="DB", schema="S", object_prefix="X")
        assert source_for("Cortex Inference API", s) == "account_usage"

    def test_enabled_but_empty_health_returns_account_usage(self, monkeypatch):
        # _health_cache returns {} when no objects exist
        monkeypatch.setattr("utils.config._health_cache", lambda *_args: {})
        s = Settings(enabled=True, database="DB", schema="S", object_prefix="X")
        assert source_for("CoCo", s) == "account_usage"


class TestSettledWindow:
    """Verify aggregate serving never includes the mutable UTC day."""

    def test_settled_utc_end_date_handles_calendar_boundaries(self):
        assert settled_utc_end_date(dt.date(2026, 3, 1)) == dt.date(2026, 2, 28)
        assert settled_utc_end_date(dt.date(2028, 3, 1)) == dt.date(2028, 2, 29)
        assert settled_utc_end_date(dt.date(2027, 1, 1)) == dt.date(2026, 12, 31)

    def test_direct_window_is_unchanged(self):
        requested = dt.date(2026, 8, 13), dt.date(2026, 8, 19)
        assert (
            effective_served_window(*requested, accelerated=False, today=dt.date(2026, 8, 19))
            == requested
        )

    def test_aggregate_window_caps_end_at_settled_date(self):
        assert effective_served_window(
            dt.date(2026, 8, 13),
            dt.date(2026, 8, 19),
            accelerated=True,
            today=dt.date(2026, 8, 19),
        ) == (dt.date(2026, 8, 13), dt.date(2026, 8, 18))

    def test_aggregate_window_clamps_future_only_custom_range(self):
        assert effective_served_window(
            dt.date(2026, 8, 19),
            dt.date(2026, 8, 19),
            accelerated=True,
            today=dt.date(2026, 8, 19),
        ) == (dt.date(2026, 8, 18), dt.date(2026, 8, 18))


class TestSnapshotFreshness:
    """Verify snapshot freshness only considers aggregate-servable sources."""

    def test_returns_latest_healthy_completion(self):
        statuses = {
            "CoCo": config.SurfaceStatus(
                "CoCo",
                table_exists=True,
                scheduling_state="ACTIVE",
                data_timestamp="2026-08-19 01:01:00",
            ),
            "Search": config.SurfaceStatus(
                "Search",
                table_exists=True,
                scheduling_state="ACTIVE",
                data_timestamp="2026-08-19 01:04:00",
            ),
        }
        assert latest_snapshot_freshness(statuses) == "2026-08-19 01:04:00"

    def test_ignores_unhealthy_sources(self):
        statuses = {
            "CoCo": config.SurfaceStatus(
                "CoCo",
                table_exists=True,
                scheduling_state="SUSPENDED",
                data_timestamp="2026-08-19 01:04:00",
            )
        }
        assert latest_snapshot_freshness(statuses) == ""


class TestHealthCache:
    """Verify the task-history query uses Snowflake's NAME column."""

    def test_task_history_query_groups_by_name(self):
        with unittest.mock.patch("utils.config._fetch_raw", return_value=pd.DataFrame()) as fetch:
            config._health_cache.clear()
            config._health_cache("DB", "S", "PREFIX")

        task_history_sql = next(
            call.args[0] for call in fetch.call_args_list if "TASK_HISTORY" in call.args[0]
        )
        assert "MAX(IFF(STATE = 'SUCCEEDED', COMPLETED_TIME, NULL))" in task_history_sql
        assert "DATABASE_NAME => ?, SCHEMA_NAME => ?" in task_history_sql
        assert "PARTITION BY NAME" in task_history_sql
        assert sum("TASK_HISTORY" in call.args[0] for call in fetch.call_args_list) == 1
        assert "TASK_NAME" not in task_history_sql


def test_health_cache_survives_page_updates_but_explicit_refresh_invalidates(monkeypatch):
    from streamlit.runtime.caching.storage import in_memory_cache_storage_wrapper

    clock = [0.0]
    monkeypatch.setattr(
        in_memory_cache_storage_wrapper.cache_utils, "TTLCACHE_TIMER", lambda: clock[0]
    )
    fetch = unittest.mock.Mock(return_value=pd.DataFrame())
    monkeypatch.setattr(config, "_fetch_raw", fetch)
    config.st.cache_data.clear()
    try:
        config._health_cache("TTL_FIXTURE", "PUBLIC", "TEST")
        calls = fetch.call_count
        clock[0] = 31.0
        config._health_cache("TTL_FIXTURE", "PUBLIC", "TEST")
        assert fetch.call_count == calls
        config._invalidate_health_cache()
        config._health_cache("TTL_FIXTURE", "PUBLIC", "TEST")
        assert fetch.call_count == 2 * calls
        clock[0] = 332.0
        config._health_cache("TTL_FIXTURE", "PUBLIC", "TEST")
        assert fetch.call_count == 3 * calls
    finally:
        config.st.cache_data.clear()


def test_settings_use_current_connection_context_without_sql(monkeypatch):
    from utils import snowflake

    raw = unittest.mock.Mock(database="FIRST_DB", schema="PUBLIC")
    monkeypatch.setattr(snowflake, "_connection", lambda: unittest.mock.Mock(raw_connection=raw))
    read = unittest.mock.Mock(return_value={})
    monkeypatch.setattr(config, "_load_settings_cached", read)
    assert config.load_settings().database == "FIRST_DB"
    raw.database = "SECOND_DB"
    raw.schema = "OTHER"
    assert config.load_settings().database == "SECOND_DB"
    assert config.load_settings().schema == "OTHER"
    config.load_settings("EXPLICIT_DB", "EXPLICIT_SCHEMA")
    assert read.call_args.args == ("EXPLICIT_DB", "EXPLICIT_SCHEMA")


class TestProvisionReport:
    """Verify ProvisionReport aggregation."""

    def test_all_ok(self):
        r = ProvisionReport(results={"CoCo": "ok", "Agents": "ok"})
        assert r.all_ok is True
        assert r.failures == {}

    def test_with_failures(self):
        r = ProvisionReport(results={"CoCo": "ok", "Agents": "some error"})
        assert r.all_ok is False
        assert r.failures == {"Agents": "some error"}

    def test_empty(self):
        r = ProvisionReport()
        assert r.all_ok is True


class TestDtReadSqlPredicate:
    """Verify the DT read predicate shape."""

    def test_predicate_uses_date_bounds(self):
        # Mock load_settings to return a settings object without hitting Snowflake
        mock_settings = Settings(
            enabled=True, database="DB", schema="S", object_prefix="CORTEX_DASH"
        )
        with unittest.mock.patch("utils.config.load_settings", return_value=mock_settings):
            sql, params = _dt_read_sql("CoCo", dt.date(2026, 8, 1), dt.date(2026, 8, 14))

        assert "USAGE_DATE >= ?::DATE" in sql
        assert "USAGE_DATE < ?::DATE" in sql
        assert params[0] == "2026-08-01"
        assert params[1] == "2026-08-15"  # end + 1 day
        assert '"CORTEX_DASH_COCO"' in sql


class TestCircularImport:
    """Verify no circular import at module load time."""

    def test_all_three_import_without_error(self):
        import importlib

        import utils.config
        import utils.queries
        import utils.snowflake

        importlib.reload(utils.snowflake)
        importlib.reload(utils.config)
        importlib.reload(utils.queries)
