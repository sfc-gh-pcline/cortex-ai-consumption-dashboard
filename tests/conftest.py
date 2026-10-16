"""Keep offline tests from reaching services through local credentials."""

import socket

import pytest


@pytest.fixture
def authorized_admin(monkeypatch):
    """Supply a platform viewer and owner-managed allowlist for admin tests."""
    from utils import admin

    monkeypatch.setenv("DASHBOARD_ADMIN_USERS", '["TEST_ADMIN"]')
    monkeypatch.setattr(admin.st, "user", {"user_name": "TEST_ADMIN"})


@pytest.fixture(autouse=True)
def deny_offline_network(request, monkeypatch):
    """Reject socket connections unless a test explicitly requires Snowflake."""
    if request.node.get_closest_marker("snowflake") is not None:
        return

    def deny_connect(*_args, **_kwargs):
        pytest.fail("Offline test attempted network access", pytrace=False)

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect)
    monkeypatch.setattr(socket, "create_connection", deny_connect)
