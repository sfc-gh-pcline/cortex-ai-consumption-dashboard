"""Guards that sql/config.sql stays in parity with the app's DDL builders.

The script is generated from create_aggregate_table_sql / create_refresh_task_sql,
so if a body or column contract changes, this test fails until the checked-in
script is regenerated. Offline: no Snowflake connection needed.
"""

from __future__ import annotations

from pathlib import Path

from utils import queries as q
from utils.config import create_aggregate_table_sql, create_refresh_task_sql
from utils.snowflake import surface_to_suffix

CONFIG_SQL = Path(__file__).resolve().parents[1] / "sql" / "config.sql"
DB, S, P, WH = "COST_MGMT_DB", "PUBLIC", "COST_MGMT", "COST_MGMT_WH"


def _script() -> str:
    return CONFIG_SQL.read_text()


def test_config_sql_exists():
    assert CONFIG_SQL.exists()


def test_every_surface_table_and_task_ddl_is_present():
    script = _script()
    for key, spec in q.DT_BODY_REGISTRY.items():
        table_sql = create_aggregate_table_sql(
            spec,
            DB,
            S,
            P,
        )
        task_sql = create_refresh_task_sql(spec, DB, S, P, WH)
        assert table_sql in script, f"table DDL missing/out of date for {key!r}"
        assert task_sql in script, f"task DDL missing/out of date for {key!r}"


def test_each_task_is_resumed():
    script = _script()
    for key in q.DT_BODY_REGISTRY:
        task_fqn = f'"{DB}"."{S}"."{P}_TASK_{surface_to_suffix(key)}"'
        assert f"ALTER TASK {task_fqn} RESUME;" in script


def test_each_task_is_executed_once_for_initial_load():
    script = _script()
    for key in q.DT_BODY_REGISTRY:
        task_fqn = f'"{DB}"."{S}"."{P}_TASK_{surface_to_suffix(key)}"'
        assert f"EXECUTE TASK {task_fqn};" in script


def test_seeds_enabled_and_owned_objects_registry():
    script = _script()
    assert "'enabled', 'true'" in script
    # owned_objects must register every table and task plus the warehouse so
    # teardown and reconciliation stay accurate.
    assert '"COST_MGMT_WH"' in script
    for key in q.DT_BODY_REGISTRY:
        suffix = surface_to_suffix(key)
        assert f"COST_MGMT_{suffix}" in script
        assert f"COST_MGMT_TASK_{suffix}" in script


def test_owned_objects_is_dollar_quoted_not_backslash_single_quoted():
    # Snowflake processes backslash escapes in single-quoted literals, which
    # would corrupt the JSON. The registry must use $$-quoting.
    script = _script()
    assert "'owned_objects', $$" in script
