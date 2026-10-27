"""Authenticated JSON calls to the Airwallex sandbox.

Sources (checked 2026-10-27): the kit 4 guide (https://airwallexdev.com/guide) and the API
reference (https://www.airwallex.com/docs/api/introduction).
- Login: POST /api/v1/authentication/login with x-client-id and x-api-key headers, no body;
  the bearer token lasts 30 minutes.
- Rate limits: 20 requests/s overall, 10 per endpoint, 10 concurrent; back off on 429.
- Amounts are major units, so JSON numbers are parsed as Decimal, never float.

Only the sandbox hosts are accepted in this milestone.
"""

import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlsplit

from nirnaya.integrations.airwallex.transport import (
    Response,
    Transport,
    TransportTimeout,
    TransportUnreachable,
    UrllibTransport,
)
from nirnaya.orchestrator.ports import AirwallexError, WriteOutcomeUnknown

SANDBOX_API = "https://api.sandbox.airwallex.com"
SANDBOX_FILES = "https://files.sandbox.airwallex.com"
_ALLOWED_HOSTS = frozenset({SANDBOX_API, SANDBOX_FILES})

# Refresh the 30-minute token this long before it expires.
TOKEN_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class Credentials:
    client_id: str
    api_key: str
    base_url: str = SANDBOX_API

    def __repr__(self) -> str:  # never print the key
        return f"Credentials(client_id={self.client_id[:4]}…, base_url={self.base_url!r})"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Credentials":
        client_id = env.get("AIRWALLEX_CLIENT_ID", "").strip()
        api_key = env.get("AIRWALLEX_API_KEY", "").strip()
        base_url = env.get("AIRWALLEX_BASE_URL", "").strip().rstrip("/") or SANDBOX_API
        if not client_id or not api_key:
            raise AirwallexError("missing_credentials", "set AIRWALLEX_CLIENT_ID and _API_KEY")
        return cls(client_id, api_key, base_url)


def check_host(url: str) -> None:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _ALLOWED_HOSTS:
        raise AirwallexError("host_not_allowed", f"{origin} is not a sandbox host")


def parse_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    data = json.loads(body, parse_float=Decimal, parse_int=Decimal)
    return data if isinstance(data, dict) else {"items": data}


def parse_time(value: str) -> datetime:
    """Airwallex timestamps, e.g. 2026-10-27T16:31:35+0000, as UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without a time zone: {value!r}")
    return parsed.astimezone(UTC)


def _wire(value: Any) -> Any:  # noqa: ANN401
    """Prepare a body for JSON: Decimal amounts become JSON numbers with the same digits.

    Airwallex takes amounts as JSON numbers. A Decimal is emitted as an int when whole, or as
    the float whose shortest text equals the Decimal's (true for money at real magnitudes);
    anything else is refused rather than rounded.
    """
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        as_float = float(value)
        if Decimal(repr(as_float)) != value:
            raise AirwallexError("amount_precision", f"{value} cannot be sent exactly")
        return as_float
    if isinstance(value, dict):
        return {k: _wire(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_wire(v) for v in value]
    return value


def _error(response: Response) -> AirwallexError:
    try:
        body = parse_json(response.body)
    except ValueError:
        body = {}
    code = str(body.get("code") or f"http_{response.status}")
    message = str(body.get("message") or "")
    # Airwallex support looks requests up by trace_id, so keep it with the error.
    if trace := body.get("trace_id"):
        message = f"{message} (trace_id {trace})".strip()
    return AirwallexError(code, message)


class AirwallexHttp:
    """One merchant's authenticated session. Not thread-safe; the demo makes one call at a time."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        transport: Transport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        check_host(credentials.base_url)
        self._credentials = credentials
        self._transport = transport or UrllibTransport()
        self._clock = clock
        self._sleep = sleep
        self._timeout = timeout
        self._max_retries = max_retries
        self._token: str | None = None
        self._expires_at: datetime | None = None

    # --- auth ---------------------------------------------------------------------------------

    def _login(self) -> None:
        url = f"{self._credentials.base_url}/api/v1/authentication/login"
        headers = {
            "x-client-id": self._credentials.client_id,
            "x-api-key": self._credentials.api_key,
            "Content-Type": "application/json",
        }
        response = self._send("POST", url, headers, None, write=False)
        if response.status not in (200, 201):
            raise _error(response)
        body = parse_json(response.body)
        token, expires = body.get("token"), body.get("expires_at")
        if not isinstance(token, str) or not token:
            raise AirwallexError("login_failed", "no token in the login response")
        self._token = token
        self._expires_at = (
            parse_time(expires)
            if isinstance(expires, str)
            else self._clock() + timedelta(minutes=30)
        )

    def _bearer(self) -> str:
        if (
            self._token is None
            or self._expires_at is None
            or (self._clock() >= self._expires_at - TOKEN_MARGIN)
        ):
            self._login()
        assert self._token is not None
        return self._token

    # --- calls --------------------------------------------------------------------------------

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        query = (
            f"?{urlencode({k: v for k, v in params.items() if v is not None})}" if params else ""
        )
        return self._call("GET", f"{self._credentials.base_url}{path}{query}", None, None)

    def post(self, path: str, body: Mapping[str, Any], *, write: bool) -> dict[str, Any]:
        """POST JSON. `write` marks calls that change state: their timeouts are ambiguous."""
        payload = json.dumps(_wire(body)).encode()
        url = f"{self._credentials.base_url}{path}"
        return self._call("POST", url, payload, "application/json", write=write)

    def upload(self, url: str, *, filename: str, content: bytes, notes: str) -> dict[str, Any]:
        """Multipart upload (File Service). Uploading is idempotent enough to retry: an orphan
        file is harmless, so a timeout here is an error, not an unknown write outcome."""
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="notes"\r\n\r\n{notes}\r\n',
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="file"; filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n',
        ]
        payload = (
            parts[0].encode() + parts[1].encode() + content + f"\r\n--{boundary}--\r\n".encode()
        )
        return self._call("POST", url, payload, f"multipart/form-data; boundary={boundary}")

    def _call(
        self,
        method: str,
        url: str,
        body: bytes | None,
        content_type: str | None,
        *,
        write: bool = False,
    ) -> dict[str, Any]:
        check_host(url)
        relogged = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self._bearer()}"}
            if content_type:
                headers["Content-Type"] = content_type
            response = self._send(method, url, headers, body, write=write)
            if response.status == 401 and not relogged:
                self._token = None  # expired early or revoked: log in once more
                relogged = True
                continue
            if response.status == 429 and attempt < self._max_retries:
                attempt += 1
                self._sleep(self._retry_after(response, attempt))
                continue
            if 200 <= response.status < 300:
                return parse_json(response.body)
            if write and response.status >= 500:
                # A server error on a write may still have been applied: reconcile, never resend.
                raise WriteOutcomeUnknown(f"{method} {urlsplit(url).path}: HTTP {response.status}")
            raise _error(response)

    def _send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, *, write: bool
    ) -> Response:
        try:
            return self._transport.send(
                method, url, headers=headers, body=body, timeout=self._timeout
            )
        except TransportTimeout as e:
            if write:
                raise WriteOutcomeUnknown(f"{method} {urlsplit(url).path}: {e}") from e
            raise AirwallexError("timeout", str(e)) from e
        except TransportUnreachable as e:
            raise AirwallexError("unreachable", str(e)) from e

    @staticmethod
    def _retry_after(response: Response, attempt: int) -> float:
        try:
            return max(0.0, float(response.headers.get("retry-after", "")))
        except ValueError:
            return 0.5 * 2 ** (attempt - 1)
