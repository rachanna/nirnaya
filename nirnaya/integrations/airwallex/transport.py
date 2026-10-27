"""The wire, behind a small interface: urllib in production, a scripted fake in tests.

No dependency beyond the standard library. A transport moves bytes and reports what happened;
it never interprets Airwallex responses (that is http.py) and never retries.
"""

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class TransportTimeout(Exception):
    """No response arrived in time: the request may or may not have been processed."""


class TransportUnreachable(Exception):
    """The connection failed before the request could be sent."""


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response: ...


class UrllibTransport:
    """HTTPS via the standard library. HTTP error statuses are returned, not raised."""

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        request = urllib.request.Request(url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:  # noqa: S310 (https only)
                return Response(r.status, r.read(), {k.lower(): v for k, v in r.headers.items()})
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), {k.lower(): v for k, v in e.headers.items()})
        except TimeoutError as e:
            raise TransportTimeout(str(e)) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError | socket.timeout):
                raise TransportTimeout(str(e.reason)) from e
            raise TransportUnreachable(str(e.reason)) from e
        except ConnectionError as e:
            raise TransportTimeout(str(e)) from e
