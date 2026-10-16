"""Parity tests: windowed builders must produce identical SQL to golden fixtures.

This guards against the builder refactor (Task 1.1) silently changing output.
"""

import unittest.mock

import pytest

from tests.fixtures.golden_sql import (
    AGENT_FACTS_PARAMS,
    AGENT_FACTS_SQL,
    AI_FN_FACTS_PARAMS,
    AI_FN_FACTS_SQL,
    COWORK_FACTS_PARAMS,
    COWORK_FACTS_SQL,
    COWORK_SERVICE_MIX_PARAMS,
    COWORK_SERVICE_MIX_SQL,
    FIXTURE_END,
    FIXTURE_START,
    SEARCH_BATCH_FACTS_PARAMS,
    SEARCH_BATCH_FACTS_SQL,
    SEARCH_DAILY_FACTS_PARAMS,
    SEARCH_DAILY_FACTS_SQL,
    SEARCH_SERVING_FACTS_PARAMS,
    SEARCH_SERVING_FACTS_SQL,
    WAREHOUSE_CREDIT_FACTS_ATTRIBUTION_ONLY_PARAMS,
    WAREHOUSE_CREDIT_FACTS_ATTRIBUTION_ONLY_SQL,
    WAREHOUSE_CREDIT_FACTS_FULL_PARAMS,
    WAREHOUSE_CREDIT_FACTS_FULL_SQL,
)
from utils.config import Settings, create_aggregate_table_sql, create_refresh_task_sql
from utils.queries import (
    _DT_COLUMNS,
    DEPENDENCY_KINDS,
    DT_BODY_REGISTRY,
    PAGE_REFRESH_PLANS,
    SURFACE_REST_API,
    _dt_read_sql,
    _warehouse_body_sql_attribution_only,
    _warehouse_body_sql_full,
    agent_facts_sql,
    ai_fn_facts_sql,
    coco_facts_sql,
    cowork_facts_sql,
    cowork_service_mix_sql,
    search_batch_facts_sql,
    search_daily_facts_sql,
    search_serving_facts_sql,
    warehouse_credit_facts_attribution_only_sql,
    warehouse_credit_facts_sql,
)

CASES = [
    ("ai_fn_facts", ai_fn_facts_sql, AI_FN_FACTS_SQL, AI_FN_FACTS_PARAMS),
    ("agent_facts", agent_facts_sql, AGENT_FACTS_SQL, AGENT_FACTS_PARAMS),
    ("cowork_facts", cowork_facts_sql, COWORK_FACTS_SQL, COWORK_FACTS_PARAMS),
    (
        "cowork_service_mix",
        cowork_service_mix_sql,
        COWORK_SERVICE_MIX_SQL,
        COWORK_SERVICE_MIX_PARAMS,
    ),
    ("search_daily", search_daily_facts_sql, SEARCH_DAILY_FACTS_SQL, SEARCH_DAILY_FACTS_PARAMS),
    (
        "search_serving",
        search_serving_facts_sql,
        SEARCH_SERVING_FACTS_SQL,
        SEARCH_SERVING_FACTS_PARAMS,
    ),
    ("search_batch", search_batch_facts_sql, SEARCH_BATCH_FACTS_SQL, SEARCH_BATCH_FACTS_PARAMS),
    (
        "warehouse_full",
        warehouse_credit_facts_sql,
        WAREHOUSE_CREDIT_FACTS_FULL_SQL,
        WAREHOUSE_CREDIT_FACTS_FULL_PARAMS,
    ),
    (
        "warehouse_attr_only",
        warehouse_credit_facts_attribution_only_sql,
        WAREHOUSE_CREDIT_FACTS_ATTRIBUTION_ONLY_SQL,
        WAREHOUSE_CREDIT_FACTS_ATTRIBUTION_ONLY_PARAMS,
    ),
]


@pytest.mark.parametrize("name,fn,expected_sql,expected_params", CASES, ids=[c[0] for c in CASES])
def test_windowed_builder_matches_golden_fixture(name, fn, expected_sql, expected_params):
    """Post-refactor windowed SQL must byte-equal the pre-refactor golden fixture."""
    sql, params = fn(FIXTURE_START, FIXTURE_END)
    assert sql == expected_sql, f"{name}: SQL differs"
    assert params == expected_params, f"{name}: params differ"


def test_coco_builder_is_covered_by_the_unified_source_contract():
    """CoCo source details have a dedicated structural contract, not a legacy golden."""
    sql, params = coco_facts_sql(FIXTURE_START, FIXTURE_END)
    assert "SNOWFLAKE_COCO_USAGE_HISTORY" in sql
    assert tuple(params) == ("2026-08-01 00:00:00 +0000", "2026-08-15 00:00:00 +0000")


# ---------------------------------------------------------------------------
# Schema contract tests (Phase 2)
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = Settings(enabled=True, database="DB", schema="S", object_prefix="CORTEX_DASH")


class TestDtReadNoSelectStar:
    """DT read path must enumerate explicit columns, never SELECT *."""

    @pytest.mark.parametrize("surface_key", list(_DT_COLUMNS.keys()))
    def test_no_select_star(self, surface_key):
        with unittest.mock.patch("utils.config.load_settings", return_value=_MOCK_SETTINGS):
            sql, _params = _dt_read_sql(surface_key, FIXTURE_START, FIXTURE_END)
        assert "SELECT *" not in sql, f"SELECT * found in DT read SQL for {surface_key!r}"

    @pytest.mark.parametrize("surface_key", list(_DT_COLUMNS.keys()))
    def test_all_declared_columns_present(self, surface_key):
        """Every column in _DT_COLUMNS must appear in the SELECT list."""
        with unittest.mock.patch("utils.config.load_settings", return_value=_MOCK_SETTINGS):
            sql, _ = _dt_read_sql(surface_key, FIXTURE_START, FIXTURE_END)
        for col in _DT_COLUMNS[surface_key]:
            assert col in sql, f"{col!r} missing from DT read SQL for {surface_key!r}"

    @pytest.mark.parametrize("surface_key", list(_DT_COLUMNS.keys()))
    def test_date_predicate_intact(self, surface_key):
        with unittest.mock.patch("utils.config.load_settings", return_value=_MOCK_SETTINGS):
            sql, params = _dt_read_sql(surface_key, FIXTURE_START, FIXTURE_END)
        assert "USAGE_DATE >= ?::DATE" in sql
        assert "USAGE_DATE < ?::DATE" in sql
        assert params == ("2026-08-01", "2026-08-15")


class TestDtBodyRegistryColumns:
    """Every DT_BODY_REGISTRY entry must declare a non-empty columns contract."""

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_spec_has_columns(self, surface_key):
        spec = DT_BODY_REGISTRY[surface_key]
        assert spec.columns, f"{surface_key!r}: DtBodySpec.columns is empty"

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_columns_match_dt_columns(self, surface_key):
        """spec.columns must equal the _DT_COLUMNS contract for that surface."""
        spec = DT_BODY_REGISTRY[surface_key]
        assert spec.columns == _DT_COLUMNS[surface_key], (
            f"{surface_key!r}: spec.columns != _DT_COLUMNS"
        )

    def test_dt_columns_covers_all_registry_keys(self):
        """_DT_COLUMNS must contain an entry for every key in DT_BODY_REGISTRY."""
        for key in DT_BODY_REGISTRY:
            assert key in _DT_COLUMNS, f"{key!r} missing from _DT_COLUMNS"


class TestCreateTableNoSelectStar:
    """create_aggregate_table_sql wrapper must not use SELECT * on the outer subquery."""

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_no_select_star_wrapper(self, surface_key):
        """The outer 'AS SELECT ... FROM ({body}) _init' must enumerate columns."""
        spec = DT_BODY_REGISTRY[surface_key]
        sql = create_aggregate_table_sql(spec, "DB", "S", "P")
        # The specific pattern we eliminated: SELECT * FROM ({body}) _init
        assert "SELECT * FROM (" not in sql, (
            f"Outer SELECT * wrapper found in aggregate table SQL for {surface_key!r}"
        )

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_all_declared_columns_in_create_select(self, surface_key):
        spec = DT_BODY_REGISTRY[surface_key]
        sql = create_aggregate_table_sql(spec, "DB", "S", "P")
        for col in spec.columns:
            assert col in sql, f"{col!r} missing from CREATE TABLE SQL for {surface_key!r}"


class TestRefreshTaskNoSelectStar:
    """create_refresh_task_sql wrapper must not use SELECT * on the outer subquery."""

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_no_select_star_wrapper(self, surface_key):
        """The outer 'SELECT ... FROM ({body}) _body' must enumerate columns."""
        spec = DT_BODY_REGISTRY[surface_key]
        sql = create_refresh_task_sql(spec, "DB", "S", "P", "WH")
        # The specific pattern we eliminated: SELECT * FROM ({body}) _body
        assert "SELECT * FROM (" not in sql, (
            f"Outer SELECT * wrapper found in refresh task SQL for {surface_key!r}"
        )

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_all_declared_columns_in_task_select(self, surface_key):
        spec = DT_BODY_REGISTRY[surface_key]
        sql = create_refresh_task_sql(spec, "DB", "S", "P", "WH")
        for col in spec.columns:
            assert col in sql, f"{col!r} missing from refresh task SQL for {surface_key!r}"

    @pytest.mark.parametrize("surface_key", list(DT_BODY_REGISTRY.keys()))
    def test_task_has_retention_filter(self, surface_key):
        spec = DT_BODY_REGISTRY[surface_key]
        sql = create_refresh_task_sql(spec, "DB", "S", "P", "WH")
        if spec.apply_outer_retention:
            assert "DATEADD" in sql
            assert "365" in sql
        else:
            assert "WHERE USAGE_DATE >= DATEADD" not in sql


class TestWarehouseBodyHelperBounds:
    """Warehouse body SQL helper subqueries must carry a 365-day retention bound."""

    @pytest.mark.parametrize(
        "fn_label,fn",
        [
            ("full", _warehouse_body_sql_full),
            ("attribution_only", _warehouse_body_sql_attribution_only),
        ],
    )
    def test_search_ids_bounded(self, fn_label, fn):
        sql = fn()
        # DATEADD bound must appear inside the search_ids CTE region
        search_block = sql[sql.index("search_ids") : sql.index("aif_ids")]
        assert "DATEADD" in search_block, (
            f"{fn_label}: search_ids CTE has no DATEADD retention bound"
        )

    @pytest.mark.parametrize(
        "fn_label,fn",
        [
            ("full", _warehouse_body_sql_full),
            ("attribution_only", _warehouse_body_sql_attribution_only),
        ],
    )
    def test_aif_ids_use_current_query_membership(self, fn_label, fn):
        sql = fn()
        aif_block = sql[sql.index("aif_ids") : sql.index("classified AS")]
        assert "SELECT DISTINCT QUERY_ID" in aif_block
        assert "CORTEX_AI_FUNCTIONS_USAGE_HISTORY" in aif_block
        assert "START_TIME" not in aif_block, f"{fn_label}: membership must span usage windows"
        assert "IS_COMPLETED" not in aif_block

    @pytest.mark.parametrize(
        "fn_label,fn",
        [
            ("full", _warehouse_body_sql_full),
            ("attribution_only", _warehouse_body_sql_attribution_only),
        ],
    )
    def test_no_params_in_body_sql(self, fn_label, fn):
        """Body functions must remain parameterless (no ? placeholders)."""
        sql = fn()
        assert "?" not in sql, f"{fn_label}: warehouse body SQL contains ? placeholder"


# ---------------------------------------------------------------------------
# Settled-window parity boundary tests
# ---------------------------------------------------------------------------


class TestSettledWindowParityBoundary:
    """Offline tests for the settled-day aggregate-eligible boundary.

    These verify the classification structure that the TEST parity queries rely
    on, without executing actual Snowflake queries.
    """

    def test_windowed_aggregate_sources_are_covered_by_dt_body_registry(self):
        """Every dep classified as windowed_aggregate must exist in DT_BODY_REGISTRY."""
        windowed_keys = [k for k, v in DEPENDENCY_KINDS.items() if v == "windowed_aggregate"]
        for key in windowed_keys:
            assert key in DT_BODY_REGISTRY, (
                f"{key!r} is windowed_aggregate in DEPENDENCY_KINDS but absent from DT_BODY_REGISTRY"
            )

    def test_only_model_rates_global_metadata_is_materialized(self):
        """Model Rates is materialized while User Tags remains live metadata."""
        global_keys = [k for k, v in DEPENDENCY_KINDS.items() if v == "global_cached"]
        for key in global_keys:
            assert (key in DT_BODY_REGISTRY) == (key == "Model Rates")

    def test_rest_is_not_registered_as_windowed_aggregate(self):
        """REST always reads AU; it must not appear as a windowed source."""
        assert DEPENDENCY_KINDS[SURFACE_REST_API] != "windowed_aggregate"
        assert SURFACE_REST_API not in DT_BODY_REGISTRY

    def test_direct_only_pages_do_not_have_uses_settled_window(self):
        """A page that is entirely direct_only should not use the settled window."""
        for page_key, plan in PAGE_REFRESH_PLANS.items():
            if plan.windowed or not plan.direct_only:
                continue  # mixed or empty plans are fine
            assert not plan.uses_settled_window, (
                f"{page_key!r} is direct_only only but uses_settled_window=True"
            )
