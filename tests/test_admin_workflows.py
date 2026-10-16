"""Synthetic administration form and confirmation behavior without Snowflake writes."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from utils import config
from utils import queries as q

pytestmark = pytest.mark.usefixtures("authorized_admin")


@pytest.fixture
def admin(monkeypatch):
    settings = config.Settings()
    settings.owned_objects = list(config.configured_objects(settings).values())
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(config, "_reconcile_owned_objects", lambda _settings: [])
    monkeypatch.setattr(config, "_invalidate_settings_cache", lambda: None)
    monkeypatch.setattr(
        config,
        "_health_cache",
        lambda *_args: {
            surface: config.SurfaceStatus(
                surface,
                table_exists=True,
                task_exists=True,
                scheduling_state="ACTIVE",
                data_timestamp="2026-09-20T01:00:00+00:00",
            )
            for surface in config._DT_SURFACE_KEYS
        },
    )
    monkeypatch.setattr(q, "clear_cache", lambda: None)
    action = Mock(return_value={settings.owned_objects[2].fqn: "Failed: denied"})
    monkeypatch.setattr(config, "run_owned_action", action)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_pages/config.py"))
    return app, action


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_setup_editing_disables_lifecycle(admin):
    app, action = admin
    app.run()
    app.toggle(key="editing_setup").set_value(True).run()
    assert not app.exception
    for label in (
        "Provision aggregate tables",
        "Suspend refreshes",
        "Resume refreshes",
        "Refresh aggregate tables",
        "Tear down all acceleration objects",
    ):
        assert button(app, label).disabled
    action.assert_not_called()


def test_teardown_cancel_is_non_mutating(admin):
    app, action = admin
    app.run()
    button(app, "Tear down all acceleration objects").click().run()
    assert not app.exception
    button(app, "Cancel").click().run()
    assert not app.exception
    action.assert_not_called()


def test_invalid_setup_identifies_field_and_performs_no_write(admin, monkeypatch):
    app, action = admin
    save = Mock()
    monkeypatch.setattr(config, "save_settings", save)
    app.run()
    app.toggle(key="editing_setup").set_value(True).run()
    next(item for item in app.text_input if item.label == "Database").set_value("bad;name")
    button(app, "Save configuration").click().run()
    assert not app.exception
    assert any(item.value.startswith("Database:") for item in app.error)
    save.assert_not_called()
    action.assert_not_called()


def test_failed_setup_save_preserves_saved_settings(admin, monkeypatch):
    app, _action = admin
    original = config.load_settings()
    monkeypatch.setattr(config, "save_settings", Mock(side_effect=RuntimeError("denied")))
    app.run()
    app.toggle(key="editing_setup").set_value(True).run()
    next(item for item in app.selectbox if item.label == "Warehouse size").set_value("SMALL")
    button(app, "Save configuration").click().run()
    assert not app.exception
    assert any("not saved" in item.value for item in app.error)
    assert original.warehouse_size == "XSMALL"


def test_failure_survives_rerun_and_retry_targets_only_failed(admin):
    app, action = admin
    app.run()
    button(app, "Suspend refreshes").click().run()
    assert not app.exception
    failed = list(action.return_value)
    app.run()
    assert any("failed" in item.value for item in app.warning)
    button(app, "Retry failed targets").click().run()
    assert action.call_args.args[0] == "suspend"
    assert action.call_args.args[1] == failed


def test_setup_exit_discards_all_drafts_without_persistence(admin, monkeypatch):
    app, action = admin
    save = Mock()
    monkeypatch.setattr(config, "save_settings", save)
    app.run()
    app.toggle(key="editing_setup").set_value(True).run()
    next(item for item in app.text_input if item.label == "Database").set_value("DRAFT")
    next(item for item in app.selectbox if item.label == "Warehouse size").set_value("SMALL")
    app.toggle(key="editing_setup").set_value(False).run()
    assert not app.exception
    assert (
        next(item for item in app.text_input if item.label == "Database").value
        == config.load_settings().database
    )
    assert next(item for item in app.selectbox if item.label == "Warehouse size").value == "XSMALL"
    app.toggle(key="editing_setup").set_value(True).run()
    assert next(item for item in app.text_input if item.label == "Database").value != "DRAFT"
    save.assert_not_called()
    action.assert_not_called()


def test_persistence_failure_keeps_completed_drop_outcomes_visible(admin):
    app, action = admin
    targets = [obj.fqn for obj in config.load_settings().owned_objects][:2]
    results = {targets[0]: "Succeeded", targets[1]: "Failed: denied"}
    action.side_effect = config.ActionPersistenceError(results, RuntimeError("save denied"))
    app.run()
    button(app, "Tear down all acceleration objects").click().run()
    button(app, "Drop all objects").click().run()
    assert not app.exception
    action.assert_called_once()
    assert app.session_state.lifecycle_report["error"] == str(action.side_effect)
    app.run()
    assert any("persistence failed" in item.value for item in app.error)
    assert app.session_state.lifecycle_report["results"] == results
    assert button(app, "Retry failed targets").disabled


def test_retry_keeps_successful_target_history(admin):
    app, action = admin
    targets = [
        obj.fqn for obj in config.load_settings().owned_objects if obj.object_type == "TASK"
    ][:2]
    action.return_value = {targets[0]: "Succeeded", targets[1]: "Failed: denied"}
    app.run()
    button(app, "Suspend refreshes").click().run()
    action.return_value = {targets[1]: "Succeeded"}
    button(app, "Retry failed targets").click().run()
    assert not app.exception
    assert action.call_args.args[1] == [targets[1]]
    assert app.session_state.lifecycle_report["results"] == dict.fromkeys(targets, "Succeeded")


def test_provision_warehouse_failure_survives_rerun_and_retries(admin, monkeypatch):
    app, _action = admin
    monkeypatch.setattr(config, "validate_provision_targets", lambda _settings: None)
    monkeypatch.setattr(config, "_invalidate_health_cache", lambda: None)
    execute = Mock(side_effect=RuntimeError("warehouse denied"))
    monkeypatch.setattr(config, "_execute_raw", execute)
    app.run()
    button(app, "Provision aggregate tables").click().run()
    assert not app.exception
    app.run()
    assert any("warehouse denied" in item.value for item in app.error)
    assert app.session_state.provision_report.steps == {"Task warehouse": "warehouse denied"}
    execute.side_effect = None
    execute.reset_mock()
    button(app, "Retry failed provisioning").click().run()
    assert not app.exception
    assert app.session_state.provision_report.all_ok
    statements = [call.args[0] for call in execute.call_args_list]
    assert statements[0].startswith("CREATE WAREHOUSE")
    assert not any(sql.startswith("USE WAREHOUSE") for sql in statements)
