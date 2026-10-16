"""Offline tests for page-scoped cache warming and per-page snapshot identity.

These verify that a page warms only its own declared dependencies plus the
shared sidebar prerequisites, that statement selection matches those
dependencies, and that an unrelated surface's aggregate generation does not
change a page's snapshot identity.
"""

from __future__ import annotations

import datetime as dt

import utils.config as config
from utils import queries as q
from utils.config import Settings, aggregate_snapshot_identity_for

START = dt.date(2026, 8, 1)
END = dt.date(2026, 8, 7)

# The shared sidebar prerequisites every page must warm for load_unified_facts.
SIDEBAR_KEYS = {
    q.SURFACE_COCO,
    q.SURFACE_COWORK,
    q.SURFACE_AGENTS,
    q.SURFACE_AI_FUNCTIONS,
    f"{q.SURFACE_SEARCH} — Daily",
    q.SURFACE_MODEL_RATES,
    "user_tags",
    q.SURFACE_REST_API,
}

# Detailed facts that must stay cold unless their owning page is opened.
DEFERRED_KEYS = {
    f"{q.SURFACE_SEARCH} — Serving",
    f"{q.SURFACE_SEARCH} — Batch",
    q.SURFACE_WAREHOUSE_ATTRIBUTION,
    f"{q.SURFACE_COWORK} — Service Mix",
}


class TestWarmDependenciesFor:
    def test_total_consumption_warms_sidebar_prerequisites_only(self):
        deps = set(q.warm_dependencies_for(q.PAGE_TOTAL_CONSUMPTION))
        assert deps == SIDEBAR_KEYS

    def test_coco_page_warms_only_main_warehouse_and_tags(self):
        deps = set(q.warm_dependencies_for(q.SURFACE_COCO))
        assert deps == {q.SURFACE_COCO, q.SURFACE_WAREHOUSE_ATTRIBUTION, "user_tags"}

    def test_search_page_warms_its_detail_and_warehouse_facts(self):
        deps = set(q.warm_dependencies_for(q.SURFACE_SEARCH))
        assert f"{q.SURFACE_SEARCH} — Serving" in deps
        assert f"{q.SURFACE_SEARCH} — Batch" in deps
        assert q.SURFACE_WAREHOUSE_ATTRIBUTION in deps

    def test_cowork_page_warms_service_mix(self):
        deps = set(q.warm_dependencies_for(q.SURFACE_COWORK))
        assert f"{q.SURFACE_COWORK} — Service Mix" in deps

    def test_dependencies_are_deduplicated_and_ordered(self):
        deps = q.warm_dependencies_for(q.PAGE_TOTAL_CONSUMPTION)
        assert len(deps) == len(set(deps))
        # Model rates must precede REST so the REST loader finds rates cached.
        assert deps.index(q.SURFACE_MODEL_RATES) < deps.index(q.SURFACE_REST_API)


class TestAggregateDependenciesFor:
    def test_only_windowed_aggregate_keys(self):
        for page in (q.PAGE_TOTAL_CONSUMPTION, q.SURFACE_SEARCH, q.SURFACE_REST_API):
            for key in q.aggregate_dependencies_for(page):
                assert q.DEPENDENCY_KINDS[key] == "windowed_aggregate"

    def test_rest_page_has_no_unrelated_aggregate_dependencies(self):
        assert q.aggregate_dependencies_for(q.SURFACE_REST_API) == ()

    def test_search_page_includes_warehouse_attribution(self):
        assert q.SURFACE_WAREHOUSE_ATTRIBUTION in q.aggregate_dependencies_for(q.SURFACE_SEARCH)


class TestWindowStatementsSelection:
    def test_selects_only_requested_dependencies(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: Settings(enabled=False))
        statements = q.window_statements(START, END, (q.SURFACE_COCO,))
        assert len(statements) == 1
        sql, _ = statements[0]
        assert "SNOWFLAKE_COCO_USAGE_HISTORY" in sql

    def test_warehouse_attribution_adds_metering_probe(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: Settings(enabled=False))
        statements = q.window_statements(START, END, (q.SURFACE_WAREHOUSE_ATTRIBUTION,))
        joined = "\n".join(sql for sql, _ in statements)
        assert "QUERY_METERING_HISTORY" in joined

    def test_excludes_model_rates_and_rest(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: Settings(enabled=False))
        statements = q.window_statements(
            START, END, (q.SURFACE_MODEL_RATES, q.SURFACE_REST_API, "user_tags")
        )
        # Model rates and REST are not independently prefetchable here; only the
        # tag statement is.
        assert len(statements) == 1
        sql, _ = statements[0]
        assert "TAG_REFERENCES" in sql

    def test_none_returns_full_cold_start_set(self, monkeypatch):
        monkeypatch.setattr(config, "load_settings", lambda: Settings(enabled=False))
        statements = q.window_statements(START, END)
        assert len(statements) >= 10


class TestPerPageSnapshotIdentity:
    def _status(self, timestamp: str) -> config.SurfaceStatus:
        return config.SurfaceStatus(
            surface_key="x",
            table_exists=True,
            scheduling_state="ACTIVE",
            data_timestamp=timestamp,
        )

    def test_disabled_is_direct(self):
        assert aggregate_snapshot_identity_for((q.SURFACE_COCO,), Settings(enabled=False)) == (
            "direct",
            "",
        )

    def test_unrelated_task_completion_does_not_change_identity(self, monkeypatch):
        health = {
            q.SURFACE_COCO: self._status("2026-08-20 01:00:00"),
            q.SURFACE_WAREHOUSE_ATTRIBUTION: self._status("2026-08-21 09:00:00"),
        }
        monkeypatch.setattr(config, "_health_cache", lambda *_a, **_k: health)
        settings = Settings(enabled=True, database="DB", schema="S", object_prefix="P")

        # A CoCo-only page is keyed on CoCo's completion, not the later,
        # unrelated Warehouse Attribution refresh.
        identity = aggregate_snapshot_identity_for((q.SURFACE_COCO,), settings)
        assert identity == ("aggregate", "2026-08-20 01:00:00")

    def test_relevant_surface_generation_is_used(self, monkeypatch):
        health = {
            q.SURFACE_COCO: self._status("2026-08-20 01:00:00"),
            q.SURFACE_WAREHOUSE_ATTRIBUTION: self._status("2026-08-21 09:00:00"),
        }
        monkeypatch.setattr(config, "_health_cache", lambda *_a, **_k: health)
        settings = Settings(enabled=True, database="DB", schema="S", object_prefix="P")

        identity = aggregate_snapshot_identity_for(
            (q.SURFACE_COCO, q.SURFACE_WAREHOUSE_ATTRIBUTION), settings
        )
        assert identity == ("aggregate", "2026-08-21 09:00:00")

    def test_no_healthy_relevant_surface_is_direct(self, monkeypatch):
        health = {q.SURFACE_COCO: self._status("2026-08-20 01:00:00")}
        monkeypatch.setattr(config, "_health_cache", lambda *_a, **_k: health)
        settings = Settings(enabled=True, database="DB", schema="S", object_prefix="P")

        # A page whose relevant surface has no healthy snapshot falls back to direct.
        assert aggregate_snapshot_identity_for((q.SURFACE_AGENTS,), settings) == ("direct", "")


class TestDependencyGeneration:
    def _status(self, timestamp: str) -> config.SurfaceStatus:
        return config.SurfaceStatus(
            surface_key="x",
            table_exists=True,
            scheduling_state="ACTIVE",
            data_timestamp=timestamp,
        )

    def test_non_windowed_deps_are_stable_empty(self):
        # Model rates (global_cached), user tags (global_cached) and REST
        # (direct_only) are window-stable, so they carry no generation.
        for dep in (q.SURFACE_MODEL_RATES, "user_tags", q.SURFACE_REST_API):
            assert q.dependency_generation(dep, Settings(enabled=True)) == ""

    def test_windowed_dep_carries_snapshot_timestamp(self, monkeypatch):
        health = {q.SURFACE_COCO: self._status("2026-08-20 01:00:00")}
        monkeypatch.setattr(config, "_health_cache", lambda *_a, **_k: health)
        settings = Settings(enabled=True, database="DB", schema="S", object_prefix="P")
        assert q.dependency_generation(q.SURFACE_COCO, settings) == "2026-08-20 01:00:00"

    def test_windowed_dep_is_empty_when_disabled(self):
        assert q.dependency_generation(q.SURFACE_COCO, Settings(enabled=False)) == ""


class TestDependenciesToWarm:
    NEEDED = (q.SURFACE_COCO, q.SURFACE_MODEL_RATES, q.SURFACE_REST_API)

    def test_all_missing_when_nothing_warmed(self):
        generations = {
            q.SURFACE_COCO: "2026-08-20",
            q.SURFACE_MODEL_RATES: "",
            q.SURFACE_REST_API: "",
        }
        assert q.dependencies_to_warm(self.NEEDED, {}, generations) == list(self.NEEDED)

    def test_warm_subset_needs_nothing(self):
        generations = {
            q.SURFACE_COCO: "2026-08-20",
            q.SURFACE_MODEL_RATES: "",
            q.SURFACE_REST_API: "",
        }
        warmed = dict(generations)
        assert q.dependencies_to_warm(self.NEEDED, warmed, generations) == []

    def test_stale_generation_is_rewarmed(self):
        generations = {
            q.SURFACE_COCO: "2026-08-21",
            q.SURFACE_MODEL_RATES: "",
            q.SURFACE_REST_API: "",
        }
        warmed = {q.SURFACE_COCO: "2026-08-20", q.SURFACE_MODEL_RATES: "", q.SURFACE_REST_API: ""}
        # Only the dependency whose generation advanced is re-warmed.
        assert q.dependencies_to_warm(self.NEEDED, warmed, generations) == [q.SURFACE_COCO]

    def test_order_follows_needed(self):
        generations = dict.fromkeys(self.NEEDED, "")
        assert q.dependencies_to_warm(self.NEEDED, {}, generations) == list(self.NEEDED)
