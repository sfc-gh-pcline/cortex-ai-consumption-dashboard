"""Regression checks for acquisition lifetime, authorization, and UTC freshness."""

import datetime as dt
from unittest.mock import Mock

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from utils import admin, config
from utils import queries as q


@pytest.fixture
def acquisitions(monkeypatch):
    st.session_state.clear()
    clock = [100.0]
    monkeypatch.setattr(q.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(q, "dependency_generation", lambda *_args: "generation")
    window = (dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    q.prepare_acquisition(config.Settings(), *window)
    yield window, clock
    st.session_state.clear()


def test_failed_acquisition_shared_until_retry_or_expiry(acquisitions, monkeypatch):
    window, clock = acquisitions
    loader = Mock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(q, "load_dependency", loader)
    for _ in range(3):
        assert q.load_dependency_result(q.SURFACE_COCO, *window).coverage.state == "unavailable"
    loader.assert_called_once()
    assert q.dependency_is_ready(q.SURFACE_COCO, *window)
    q.reload_dependencies((q.SURFACE_COCO,))
    assert not q.dependency_is_ready(q.SURFACE_COCO, *window)
    q.load_dependency_result(q.SURFACE_COCO, *window)
    assert loader.call_count == 2
    clock[0] += 1801
    assert not q.dependency_is_ready(q.SURFACE_COCO, *window)
    q.load_dependency_result(q.SURFACE_COCO, *window)
    assert loader.call_count == 3


def test_acquisition_survives_sql_eviction_but_not_source_change(acquisitions, monkeypatch):
    window, _clock = acquisitions
    loader = Mock(return_value=pd.DataFrame({"CREDITS": [1.0]}))
    monkeypatch.setattr(q, "load_dependency", loader)
    q.load_dependency_result(q.SURFACE_COCO, *window)
    q.run_query.clear()
    assert q.dependency_is_ready(q.SURFACE_COCO, *window)
    q.load_dependency_result(q.SURFACE_COCO, *window)
    loader.assert_called_once()
    q.prepare_acquisition(config.Settings(database="OTHER"), *window)
    assert not q.dependency_is_ready(q.SURFACE_COCO, *window)


def test_acquisition_uses_remaining_query_lifetime(acquisitions, monkeypatch):
    window, clock = acquisitions
    frame = pd.DataFrame({"CREDITS": [1.0]})
    frame.attrs["cache_expires_at"] = 150.0
    monkeypatch.setattr(q, "load_dependency", lambda *_args: frame)
    q.load_dependency_result(q.SURFACE_COCO, *window)
    clock[0] = 151.0
    assert not q.dependency_is_ready(q.SURFACE_COCO, *window)


def test_acquisition_copies_and_supersession(acquisitions, monkeypatch):
    window, _clock = acquisitions
    monkeypatch.setattr(q, "load_dependency", lambda *_args: pd.DataFrame({"CREDITS": [1.0]}))
    first = q.load_dependency_result(q.SURFACE_COCO, *window)
    first.frame.loc[0, "CREDITS"] = 99.0
    assert q.load_dependency_result(q.SURFACE_COCO, *window).frame.loc[0, "CREDITS"] == 1.0
    q.reload_dependencies((q.SURFACE_COCO,))
    st.session_state.load_request_id = "original"

    def superseded(*_args):
        st.session_state.load_request_id = "replacement"
        return pd.DataFrame({"CREDITS": [7.0]})

    monkeypatch.setattr(q, "load_dependency", superseded)
    q.load_dependency_result(q.SURFACE_COCO, *window)
    assert not q.dependency_is_ready(q.SURFACE_COCO, *window)


def test_shared_connection_operations_are_serialized(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from utils import snowflake

    entered = Event()
    release = Event()
    second_started = Event()
    second_entered = Event()

    def collect():
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        return []

    connection = Mock()
    connection.session.return_value.sql.return_value.collect.side_effect = collect
    monkeypatch.setattr(snowflake, "_connection", lambda: connection)

    def second_read():
        second_started.set()
        snowflake._fetch_raw("SELECT 2")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(snowflake._fetch_raw, "SELECT 1")
        try:
            assert entered.wait(5)
            second = executor.submit(second_read)
            assert second_started.wait(5)
            assert not second_entered.wait(0.05)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert second_entered.is_set()


def test_reconciliation_cache_and_status_invalidation(monkeypatch):
    fetch = Mock(return_value=pd.DataFrame({"TABLE_NAME": ["COST_MGMT_COCO"]}))
    monkeypatch.setattr(config, "_fetch_raw", fetch)
    config._reconcile_tables.clear()
    try:
        settings = config.Settings()
        assert config._reconcile_owned_objects(settings)
        config._reconcile_owned_objects(settings)
        fetch.assert_called_once()
        config._invalidate_health_cache()
        config._reconcile_owned_objects(settings)
        assert fetch.call_count == 2
    finally:
        config._reconcile_tables.clear()


def test_raw_mutation_rejects_unauthorized_viewer(monkeypatch):
    from utils import snowflake

    monkeypatch.setenv("DASHBOARD_ADMIN_USERS", "[]")
    connection = Mock()
    monkeypatch.setattr(snowflake, "_connection", connection)
    with pytest.raises(PermissionError):
        snowflake._execute_raw("ALTER TASK ignored SUSPEND")
    connection.assert_not_called()


@pytest.mark.parametrize("allowed", ["[]", "bad-json", '"Alice"', '["Bob"]', "[1]"])
def test_admin_defaults_and_invalid_configuration_deny(monkeypatch, allowed):
    monkeypatch.setenv("DASHBOARD_ADMIN_USERS", allowed)
    monkeypatch.setattr(admin.st, "user", {"user_name": "Alice"})
    with pytest.raises(PermissionError):
        admin.require_admin()


def test_admin_uses_authenticated_identity_not_state(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ADMIN_USERS", '["Alice"]')
    monkeypatch.setattr(admin.st, "user", {"user_name": "Bob"})
    st.session_state["user_name"] = "Alice"
    st.session_state["is_admin"] = True
    assert not admin.is_admin()
    monkeypatch.setattr(admin.st, "user", {"user_name": "Alice"})
    admin.require_admin()
    st.session_state.clear()


def test_unauthorized_mutations_do_not_reach_database(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ADMIN_USERS", raising=False)
    execute = Mock()
    monkeypatch.setattr(config, "_execute_raw", execute)
    for action in (
        lambda: config.save_settings(config.Settings()),
        lambda: config.run_owned_action("drop", [], config.Settings()),
        lambda: config.provision_targets(config.Settings(), {}, config.ProvisionReport()),
    ):
        with pytest.raises(PermissionError):
            action()
    execute.assert_not_called()


def test_unauthorized_config_page_has_no_mutation_controls(monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("DASHBOARD_ADMIN_USERS", raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_pages/config.py")).run()
    assert not app.exception
    assert not app.button
    assert any("authorized dashboard administrator" in info.value for info in app.info)


def test_utc_clock_has_one_five_minute_cache(monkeypatch):
    from streamlit.runtime.caching.storage import in_memory_cache_storage_wrapper as storage

    clock = [0.0]
    monkeypatch.setattr(storage.cache_utils, "TTLCACHE_TIMER", lambda: clock[0])
    query = Mock(
        side_effect=[
            pd.DataFrame({"UTC_TODAY": [dt.date(2026, 9, 20)]}),
            pd.DataFrame({"UTC_TODAY": [dt.date(2026, 9, 21)]}),
        ]
    )
    monkeypatch.setattr(q, "_connection", lambda: Mock(query=query))
    st.cache_data.clear()
    try:
        assert q.utc_today() == dt.date(2026, 9, 20)
        clock[0] = 301.0
        assert q.utc_today() == dt.date(2026, 9, 21)
        assert query.call_count == 2
    finally:
        st.cache_data.clear()
