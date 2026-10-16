"""Verify the offline network guard rejects real connection attempts."""

import socket

import pytest


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_offline_socket_connections_are_blocked(method):
    with (
        socket.socket() as connection,
        pytest.raises(pytest.fail.Exception, match="Offline test attempted network access"),
    ):
        getattr(connection, method)(("127.0.0.1", 9))


def test_offline_create_connection_is_blocked():
    with pytest.raises(pytest.fail.Exception, match="Offline test attempted network access"):
        socket.create_connection(("127.0.0.1", 9))
