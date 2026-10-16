"""Offline contract tests for the page-to-dependency registry.

Every dependency key referenced in PAGE_REFRESH_PLANS must appear in
DEPENDENCY_KINDS, and every windowed dep must be backed by a DT_BODY_REGISTRY
entry. Tests here are the offline gate that rejects undeclared dependencies.
"""

from __future__ import annotations

from utils.queries import (
    DEPENDENCY_KINDS,
    DT_BODY_REGISTRY,
    PAGE_CONFIG,
    PAGE_QUERY_ACCELERATION_SQL,
    PAGE_REFRESH_PLANS,
    PAGE_SQL,
    PAGE_TOTAL_CONSUMPTION,
    SURFACE_MODEL_RATES,
    SURFACE_REST_API,
)


class TestDependencyKindsCoverage:
    """Every dep key in any plan must be registered in DEPENDENCY_KINDS."""

    def test_all_plan_dep_keys_are_registered(self):
        undeclared: list[str] = []
        for plan in PAGE_REFRESH_PLANS.values():
            for key in (*plan.windowed, *plan.global_cached, *plan.direct_only):
                if key not in DEPENDENCY_KINDS:
                    undeclared.append(f"{plan.page_key}: {key!r}")
        assert not undeclared, f"Undeclared dependency keys: {undeclared}"

    def test_all_windowed_deps_have_dt_registry_entry(self):
        missing: list[str] = []
        for plan in PAGE_REFRESH_PLANS.values():
            for key in plan.windowed:
                if key not in DT_BODY_REGISTRY:
                    missing.append(f"{plan.page_key}: {key!r}")
        assert not missing, f"Windowed dep without DT_BODY_REGISTRY entry: {missing}"


class TestDTRegistryCoverage:
    """Every DT_BODY_REGISTRY surface must appear in at least one page plan."""

    def test_every_dt_source_covered_by_some_page(self):
        all_materialized: set[str] = set()
        for plan in PAGE_REFRESH_PLANS.values():
            all_materialized.update(plan.windowed)
            all_materialized.update(plan.global_cached)

        uncovered = [k for k in DT_BODY_REGISTRY if k not in all_materialized]
        assert not uncovered, f"DT_BODY_REGISTRY surfaces not in any page plan: {uncovered}"


class TestSourceKindClassification:
    """Spot-checks on the fixed-category expectations for key dependency kinds."""

    def test_rest_api_is_direct_only(self):
        assert DEPENDENCY_KINDS[SURFACE_REST_API] == "direct_only"

    def test_model_rates_is_global_cached(self):
        assert DEPENDENCY_KINDS[SURFACE_MODEL_RATES] == "global_cached"

    def test_user_tags_is_global_cached(self):
        assert DEPENDENCY_KINDS["user_tags"] == "global_cached"

    def test_all_dt_body_registry_keys_are_materialized(self):
        for key in DT_BODY_REGISTRY:
            assert DEPENDENCY_KINDS[key] in {"windowed_aggregate", "global_cached"}, (
                f"{key!r} is in DT_BODY_REGISTRY but is not materialized"
            )


class TestRefreshPlanStructure:
    """Every navigable page has a declared plan and its metadata is consistent."""

    def test_every_page_has_a_plan(self):
        # These are the ten navigation entries from streamlit_app.py.
        from utils.queries import (
            SURFACE_AGENTS,
            SURFACE_AI_FUNCTIONS,
            SURFACE_COCO,
            SURFACE_COWORK,
            SURFACE_SEARCH,
            SURFACE_WAREHOUSE_ATTRIBUTION,
        )

        required = {
            PAGE_TOTAL_CONSUMPTION,
            SURFACE_COCO,
            SURFACE_COWORK,
            SURFACE_AGENTS,
            SURFACE_SEARCH,
            SURFACE_REST_API,
            SURFACE_AI_FUNCTIONS,
            SURFACE_WAREHOUSE_ATTRIBUTION,
            PAGE_SQL,
            PAGE_CONFIG,
        }
        missing = required - set(PAGE_REFRESH_PLANS)
        assert not missing, f"Pages without a RefreshPlan: {missing}"

    def test_no_page_plan_is_empty_for_data_pages(self):
        # Data pages must have at least one windowed or direct_only dep.
        no_data_pages = {PAGE_SQL, PAGE_CONFIG, PAGE_QUERY_ACCELERATION_SQL}
        for page_key, plan in PAGE_REFRESH_PLANS.items():
            if page_key in no_data_pages:
                continue
            has_data = bool(plan.windowed or plan.direct_only)
            assert has_data, f"{page_key!r} has no windowed or direct_only deps"

    def test_rest_page_uses_no_windowed_deps(self):
        plan = PAGE_REFRESH_PLANS[SURFACE_REST_API]
        assert not plan.windowed

    def test_rest_page_does_not_use_settled_window(self):
        plan = PAGE_REFRESH_PLANS[SURFACE_REST_API]
        assert not plan.uses_settled_window

    def test_no_plan_references_itself_as_both_windowed_and_direct(self):
        for plan in PAGE_REFRESH_PLANS.values():
            overlap = set(plan.windowed) & set(plan.direct_only)
            assert not overlap, (
                f"{plan.page_key!r}: dep in both windowed and direct_only: {overlap}"
            )
