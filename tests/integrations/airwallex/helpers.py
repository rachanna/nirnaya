"""A scripted transport: records every request and answers from a queue. No sockets."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nirnaya.integrations.airwallex.http import AirwallexHttp, Credentials
from nirnaya.integrations.airwallex.transport import Response

T0 = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
LOGIN = Response(
    201, json.dumps({"token": "tok-1", "expires_at": "2026-10-27T10:30:00+0000"}).encode()
)


def ok(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:  # noqa: ANN401
    return Response(status, json.dumps(body).encode(), headers or {})


def error(status: int, code: str, message: str = "") -> Response:
    return Response(status, json.dumps({"code": code, "message": message}).encode())


@dataclass
class Sent:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None

    def json(self) -> Any:  # noqa: ANN401
        return json.loads(self.body or b"{}")


@dataclass
class ScriptedTransport:
    replies: list[Response | Exception | Callable[[Sent], Response]] = field(default_factory=list)
    sent: list[Sent] = field(default_factory=list)

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        call = Sent(method, url, dict(headers), body)
        self.sent.append(call)
        if not self.replies:
            raise AssertionError(f"unexpected request {method} {url}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(call) if callable(reply) else reply

    def calls(self) -> list[tuple[str, str]]:
        return [(s.method, s.url) for s in self.sent]


class Clock:
    def __init__(self, at: datetime = T0) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def session(*replies, clock: Clock | None = None):  # noqa: ANN001, ANN201
    transport = ScriptedTransport(list(replies))
    slept: list[float] = []
    http = AirwallexHttp(
        Credentials("client-123", "key-456"),
        transport=transport,
        clock=clock or Clock(),
        sleep=slept.append,
    )
    return http, transport, slept
