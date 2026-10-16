"""Tests for the Query Acceleration SQL educational catalogue.

Verify the catalogue covers every materialized surface, generated DDL uses the
selected object prefix and matches the app's builders, and Model Rates is
classified as a global aggregate. Offline: no Snowflake connection needed.
"""

from __future__ import annotations

from utils import queries as q
from utils.config import (
    Settings,
    create_aggregate_table_sql,
    create_refresh_task_sql,
)

_SETTINGS = Settings(
    enabled=True,
    database="DB",
    schema="S",
    object_prefix="PREFIX",
    warehouse_name="WH",
)


class TestCatalogueCoverage:
    def test_covers_every_dt_body_spec(self):
        assert {e.surface_key for e in q.ACCELERATION_SQL_REFERENCE} == set(q.DT_BODY_REGISTRY)

    def test_every_entry_has_source_views_and_explanation(self):
        for entry in q.ACCELERATION_SQL_REFERENCE:
            assert entry.source_views
            assert entry.explanation

    def test_model_rates_is_global_aggregate(self):
        entry = next(
            e for e in q.ACCELERATION_SQL_REFERENCE if e.surface_key == q.SURFACE_MODEL_RATES
        )
        assert entry.object_type == "GLOBAL_AGGREGATE"

    def test_windowed_surfaces_are_windowed_aggregate(self):
        for entry in q.ACCELERATION_SQL_REFERENCE:
            if entry.surface_key != q.SURFACE_MODEL_RATES:
                assert entry.object_type == "WINDOWED_AGGREGATE"


class TestGeneratedDDLParity:
    def test_table_sql_matches_builder_and_uses_prefix(self):
        for key, spec in q.DT_BODY_REGISTRY.items():
            sql = q.acceleration_table_sql(key, _SETTINGS)
            assert sql == create_aggregate_table_sql(spec, "DB", "S", "PREFIX")

    def test_task_sql_matches_builder_and_uses_prefix(self):
        for key, spec in q.DT_BODY_REGISTRY.items():
            sql = q.acceleration_task_sql(key, _SETTINGS)
            assert sql == create_refresh_task_sql(spec, "DB", "S", "PREFIX", "WH")

    def test_generated_task_sql_uses_selected_names(self):
        sql = q.acceleration_task_sql(q.SURFACE_COCO, _SETTINGS)
        assert '"PREFIX_TASK_COCO"' in sql
        assert '"PREFIX_COCO"' in sql
        assert 'WAREHOUSE = "WH"' in sql


class TestLifecycleSql:
    def test_lifecycle_has_all_labelled_operations(self):
        lifecycle = q.acceleration_lifecycle_sql(q.SURFACE_COCO, _SETTINGS)
        assert set(lifecycle) == {
            "Create task warehouse",
            "Create aggregate table",
            "Create refresh task",
            "Enable schedule",
            "Run now",
            "Pause schedule",
            "Drop resource",
        }

    def test_enable_run_pause_target_the_task(self):
        lifecycle = q.acceleration_lifecycle_sql(q.SURFACE_COCO, _SETTINGS)
        task_fqn = '"DB"."S"."PREFIX_TASK_COCO"'
        assert lifecycle["Enable schedule"] == f"ALTER TASK {task_fqn} RESUME"
        assert lifecycle["Run now"] == f"EXECUTE TASK {task_fqn}"
        assert lifecycle["Pause schedule"] == f"ALTER TASK {task_fqn} SUSPEND"

    def test_drop_resource_is_a_drop(self):
        lifecycle = q.acceleration_lifecycle_sql(q.SURFACE_COCO, _SETTINGS)
        assert lifecycle["Drop resource"].startswith("DROP TASK IF EXISTS")


class TestPageRegistration:
    def test_query_acceleration_page_has_empty_refresh_plan(self):
        plan = q.PAGE_REFRESH_PLANS[q.PAGE_QUERY_ACCELERATION_SQL]
        assert plan.windowed == ()
        assert plan.global_cached == ()
        assert plan.direct_only == ()
        assert plan.uses_settled_window is False
