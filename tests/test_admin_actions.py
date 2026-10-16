"""Operational readiness and exact-ownership lifecycle boundaries."""

import datetime as dt
from dataclasses import replace

import pandas as pd
import pytest

from utils import config

pytestmark = pytest.mark.usefixtures("authorized_admin")


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, "Ready"),
        ({"probe_errors": ("denied",)}, "Unknown"),
        ({"table_exists": False}, "Not created"),
        ({"task_exists": False}, "Task missing"),
        ({"scheduling_state": "SUSPENDED"}, "Suspended"),
        ({"latest_state": "FAILED"}, "Refresh failed"),
        ({"data_timestamp": ""}, "Awaiting first refresh"),
        ({"data_timestamp": "2026-09-18T00:00:00+00:00"}, "Stale snapshot"),
        ({"data_timestamp": "2026-09-20T00:00:00"}, "Unknown"),
    ],
)
def test_readiness_distinguishes_presence_freshness_and_failures(changes, expected):
    baseline = config.SurfaceStatus(
        surface_key="CoCo",
        table_exists=True,
        task_exists=True,
        scheduling_state="ACTIVE",
        data_timestamp="2026-09-20T00:00:00+00:00",
    )
    assert (
        config.readiness(replace(baseline, **changes), dt.datetime(2026, 9, 20, 12, tzinfo=dt.UTC))
        == expected
    )


@pytest.fixture
def lifecycle(monkeypatch):
    settings = config.Settings()
    settings.owned_objects = list(config.configured_objects(settings).values())
    calls = []
    monkeypatch.setattr(config, "_execute_raw", lambda statement: calls.append(statement))
    monkeypatch.setattr(config, "save_settings", lambda _settings: None)
    monkeypatch.setattr(config, "_invalidate_health_cache", lambda: None)
    monkeypatch.setattr(config, "_invalidate_settings_cache", lambda: None)
    monkeypatch.setattr(
        config,
        "_health_cache",
        lambda *_args: {
            surface: config.SurfaceStatus(surface, task_exists=True, scheduling_state="ACTIVE")
            for surface in config._DT_SURFACE_KEYS
        },
    )
    return settings, calls


def test_unregistered_target_rejects_entire_action_before_mutation(lifecycle):
    settings, calls = lifecycle
    registered = next(obj.fqn for obj in settings.owned_objects if obj.object_type == "TASK")
    with pytest.raises(ValueError, match="registered target"):
        config.run_owned_action(
            "suspend", [registered, '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_UNRELATED"'], settings
        )
    assert calls == []


def test_empty_cancelled_action_performs_no_mutations(lifecycle):
    settings, calls = lifecycle
    assert config.run_owned_action("suspend", [], settings) == {}
    assert calls == []


def test_refresh_reports_queued_not_successful_ingestion(lifecycle):
    settings, calls = lifecycle
    target = next(obj.fqn for obj in settings.owned_objects if obj.object_type == "TASK")
    assert config.run_owned_action("refresh", [target], settings) == {target: "Queued"}
    assert calls == [f"EXECUTE TASK {target}"]


def test_unknown_task_state_blocks_mutation(lifecycle, monkeypatch):
    settings, calls = lifecycle
    monkeypatch.setattr(config, "_health_cache", lambda *_args: {})
    target = next(obj.fqn for obj in settings.owned_objects if obj.object_type == "TASK")
    with pytest.raises(ValueError, match="not verified"):
        config.run_owned_action("suspend", [target], settings)
    assert calls == []


def test_provision_refuses_existing_unregistered_objects(monkeypatch):
    settings = config.Settings()
    monkeypatch.setattr(
        config, "_fetch_raw", lambda *_args: pd.DataFrame({"name": ["COST_MGMT_WH"]})
    )
    with pytest.raises(ValueError, match="not registered"):
        config.validate_provision_targets(settings)
    assert settings.owned_objects == []


def test_provision_refuses_unknown_inventory(monkeypatch):
    monkeypatch.setattr(config, "_fetch_raw", lambda *_args: None)
    with pytest.raises(ValueError, match="Unknown"):
        config.validate_provision_targets(config.Settings())


def test_health_preserves_latest_failure_and_previous_success(monkeypatch):
    statements = []

    def fetch(statement, params=()):
        statements.append((statement, params))
        if statement.startswith("SHOW TASKS"):
            return pd.DataFrame({"name": ["COST_MGMT_TASK_COCO"], "state": ["started"]})
        if "INFORMATION_SCHEMA.TABLES" in statement:
            return pd.DataFrame({"TABLE_NAME": ["COST_MGMT_COCO"], "ROW_COUNT": [5]})
        return pd.DataFrame(
            {
                "NAME": ["COST_MGMT_TASK_COCO"],
                "LAST_RUN": ["2026-09-19T01:00:00+00:00"],
                "STATE": ["FAILED"],
                "SCHEDULED_TIME": ["2026-09-20T01:00:00+00:00"],
                "ERROR_MESSAGE": ["denied"],
            }
        )

    monkeypatch.setattr(config, "_fetch_raw", fetch)
    config._health_cache.clear()
    try:
        status = config._health_cache("COST_MGMT_DB", "PUBLIC", "COST_MGMT")["CoCo"]
        assert status.task_exists and status.table_exists
        assert status.latest_state == "FAILED"
        assert status.latest_error == "denied"
        assert status.data_timestamp == "2026-09-19T01:00:00+00:00"
        assert config.readiness(status) == "Refresh failed"
        for statement, params in statements:
            if "TASK_HISTORY" in statement:
                assert "DATABASE_NAME => ?, SCHEMA_NAME => ?" in statement
                assert params == ("COST_MGMT_DB", "PUBLIC")
    finally:
        config._health_cache.clear()


def test_health_probe_failure_is_unknown_not_absent(monkeypatch):
    def denied(*_args):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(config, "_fetch_raw", denied)
    config._health_cache.clear()
    try:
        status = config._health_cache("COST_MGMT_DB", "PUBLIC", "COST_MGMT")["CoCo"]
        assert status.task_exists is None
        assert not status.table_probe_ok
        assert len(status.probe_errors) == 3
        assert config.readiness(status) == "Unknown"
    finally:
        config._health_cache.clear()


def test_failed_task_drop_blocks_table_and_warehouse_drops(lifecycle, monkeypatch):
    settings, calls = lifecycle
    targets = [obj.fqn for obj in settings.owned_objects[:3]]
    task = next(obj.fqn for obj in settings.owned_objects[:3] if obj.object_type == "TASK")

    def fail(statement):
        calls.append(statement)
        raise RuntimeError("denied")

    monkeypatch.setattr(config, "_execute_raw", fail)
    result = config.run_owned_action("drop", targets, settings)
    assert calls == [f"DROP TASK IF EXISTS {task}"]
    assert all(value.startswith("Failed:") for value in result.values())
    assert len(settings.owned_objects) == 21


def test_failed_drop_remains_registered_and_retry_excludes_success(lifecycle, monkeypatch):
    settings, calls = lifecycle
    targets = [obj.fqn for obj in settings.owned_objects if obj.object_type == "TABLE"][:2]

    def execute(statement):
        calls.append(statement)
        if targets[0] in statement:
            raise RuntimeError("permission denied")

    monkeypatch.setattr(config, "_execute_raw", execute)
    report = config.run_owned_action("drop", targets, settings)
    assert report[targets[0]].startswith("Failed:")
    assert report[targets[1]] == "Succeeded"
    assert targets[0] in {obj.fqn for obj in settings.owned_objects}
    assert targets[1] not in {obj.fqn for obj in settings.owned_objects}
    calls.clear()
    config.run_owned_action(
        "drop",
        [target for target, result in report.items() if result.startswith("Failed:")],
        settings,
    )
    assert len(calls) == 1 and targets[0] in calls[0]


def test_drop_persistence_failure_keeps_actual_outcomes_and_saved_registry(lifecycle, monkeypatch):
    settings, calls = lifecycle
    targets = [obj.fqn for obj in settings.owned_objects if obj.object_type == "TABLE"][:2]
    original = list(settings.owned_objects)
    settings.enabled = True

    def execute(statement):
        calls.append(statement)
        if targets[0] in statement:
            raise RuntimeError("drop denied")

    def failed_save(_settings):
        raise RuntimeError("save denied")

    monkeypatch.setattr(config, "_execute_raw", execute)
    monkeypatch.setattr(config, "save_settings", failed_save)
    with pytest.raises(config.ActionPersistenceError, match="save denied") as failure:
        config.run_owned_action("drop", targets, settings)
    assert failure.value.results == {targets[0]: "Failed: drop denied", targets[1]: "Succeeded"}
    assert settings.owned_objects == original
    assert settings.enabled


@pytest.mark.parametrize(
    "failure_step", ["registration", "warehouse", "table", "task", "resume", "queue"]
)
def test_provision_retry_skips_successful_steps(lifecycle, monkeypatch, failure_step):
    from utils import queries as q

    settings, calls = lifecycle
    settings.owned_objects = []
    monkeypatch.setattr(config, "validate_provision_targets", lambda _settings: None)
    report = config.ProvisionReport()
    failing = True
    prefixes = {
        "warehouse": "CREATE WAREHOUSE",
        "table": "CREATE TABLE",
        "task": "CREATE OR REPLACE TASK",
        "resume": "ALTER TASK",
        "queue": "EXECUTE TASK",
    }
    successes = []

    def save(_settings):
        if failing and failure_step == "registration":
            raise RuntimeError("registration denied")

    def execute(statement):
        calls.append(statement)
        if failing and statement.startswith(prefixes.get(failure_step, "never")):
            raise RuntimeError("synthetic denied")
        successes.append(statement)

    monkeypatch.setattr(config, "save_settings", save)
    monkeypatch.setattr(config, "_execute_raw", execute)
    specs = {q.SURFACE_COCO: q.DT_BODY_REGISTRY[q.SURFACE_COCO]}
    config.provision_targets(settings, specs, report)
    assert report.error or report.failures
    if failure_step == "registration":
        assert not calls and not settings.owned_objects
    already_succeeded = list(successes)
    failing = False
    calls.clear()
    config.provision_targets(settings, specs, report)
    assert report.all_ok and report.results == {q.SURFACE_COCO: "ok"}
    assert not any(sql in already_succeeded for sql in calls)
    calls.clear()
    config.provision_targets(settings, specs, report)
    assert calls == []


def test_provision_retry_refuses_changed_target(lifecycle, monkeypatch):
    settings, calls = lifecycle
    monkeypatch.setattr(config, "validate_provision_targets", lambda _settings: None)
    report = config.ProvisionReport()
    config.provision_targets(settings, {}, report)
    calls.clear()
    settings.database = "OTHER"
    config.provision_targets(settings, {}, report)
    assert "target changed" in report.error
    assert not calls


def test_provision_retries_failed_surface_without_recreating_success(lifecycle, monkeypatch):
    from utils import queries as q

    settings, calls = lifecycle
    monkeypatch.setattr(config, "validate_provision_targets", lambda _settings: None)
    failing = True

    def execute(statement):
        calls.append(statement)
        if failing and statement.startswith("EXECUTE TASK") and "TASK_COCO" in statement:
            raise RuntimeError("queue denied")

    monkeypatch.setattr(config, "_execute_raw", execute)
    report = config.ProvisionReport()
    specs = {surface: q.DT_BODY_REGISTRY[surface] for surface in (q.SURFACE_COCO, q.SURFACE_AGENTS)}
    config.provision_targets(settings, specs, report)
    assert report.results[q.SURFACE_AGENTS] == "ok"
    assert report.failures == {q.SURFACE_COCO: "queue denied"}
    calls.clear()
    failing = False
    config.provision_targets(settings, specs, report)
    assert report.all_ok
    assert calls == [
        'EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"',
    ]


def test_provision_retry_refuses_lost_ownership(lifecycle, monkeypatch):
    settings, calls = lifecycle
    monkeypatch.setattr(config, "validate_provision_targets", lambda _settings: None)
    report = config.ProvisionReport()
    config.provision_targets(settings, {}, report)
    settings.owned_objects.pop()
    calls.clear()
    config.provision_targets(settings, {}, report)
    assert "ownership changed" in report.error
    assert not calls
