import socket

import pytest


class NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests are offline: any socket use is a failure. Only opt-in live tests may connect."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("live_airwallex"):
        return

    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkBlockedError("network access attempted in an offline test")

    # UI tests run an asyncio loop, which on Windows needs a local socket pair (a loopback
    # pipe). They may create sockets, but outbound connections and DNS stay blocked.
    if not request.node.get_closest_marker("app"):
        monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
