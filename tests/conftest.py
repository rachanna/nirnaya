import socket

import pytest


class NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Milestone 1 is offline: any socket use is a test failure."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkBlockedError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
