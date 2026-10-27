"""The authenticated JSON layer: login, token reuse, retries and failure classification."""

from datetime import timedelta
from decimal import Decimal

import pytest

from nirnaya.integrations.airwallex.http import (
    SANDBOX_API,
    SANDBOX_FILES,
    AirwallexHttp,
    Credentials,
)
from nirnaya.integrations.airwallex.transport import (
    Response,
    TransportTimeout,
    TransportUnreachable,
)
from nirnaya.orchestrator.ports import AirwallexError, WriteOutcomeUnknown
from tests.integrations.airwallex.helpers import LOGIN, Clock, error, ok, session


def test_login_sends_the_documented_headers_and_no_body_then_uses_the_bearer_token():
    http, transport, _ = session(LOGIN, ok({"items": []}))
    assert http.get("/api/v1/pa/payment_disputes") == {"items": []}
    login, call = transport.sent
    assert (login.method, login.url) == ("POST", f"{SANDBOX_API}/api/v1/authentication/login")
    assert login.headers["x-client-id"] == "client-123"
    assert login.headers["x-api-key"] == "key-456"
    assert login.body is None
    assert call.headers["Authorization"] == "Bearer tok-1"
    assert "x-api-key" not in call.headers


def test_the_token_is_reused_and_refreshed_before_it_expires():
    clock = Clock()
    http, transport, _ = session(LOGIN, ok({}), ok({}), LOGIN, ok({}), clock=clock)
    http.get("/a")
    clock.at += timedelta(minutes=20)
    http.get("/b")  # still valid: no login
    clock.at += timedelta(minutes=6)  # 4 minutes left: inside the 5-minute margin
    http.get("/c")
    paths = [url.removeprefix(SANDBOX_API) for _, url in transport.calls()]
    assert paths == [
        "/api/v1/authentication/login",
        "/a",
        "/b",
        "/api/v1/authentication/login",
        "/c",
    ]


def test_a_401_logs_in_again_once():
    http, transport, _ = session(LOGIN, error(401, "credentials_expired"), LOGIN, ok({"x": 1}))
    assert http.get("/a") == {"x": Decimal(1)}
    http2, _, _ = session(LOGIN, error(401, "a"), LOGIN, error(401, "credentials_invalid"))
    with pytest.raises(AirwallexError, match="credentials_invalid"):
        http2.get("/a")


def test_a_failed_login_is_an_error():
    http, _, _ = session(error(401, "credentials_invalid", "bad key"))
    with pytest.raises(AirwallexError, match="credentials_invalid: bad key"):
        http.get("/a")


def test_429_backs_off_honouring_retry_after_then_succeeds():
    http, _, slept = session(
        LOGIN,
        Response(429, b"{}", {"retry-after": "2"}),
        Response(429, b"{}"),
        ok({"done": True}),
    )
    assert http.get("/a") == {"done": True}
    assert slept == [2.0, 1.0]


def test_429_beyond_the_retry_limit_is_an_error():
    http, _, slept = session(LOGIN, *[Response(429, b"{}")] * 4)
    with pytest.raises(AirwallexError, match="http_429"):
        http.get("/a")
    assert len(slept) == 3


def test_error_bodies_become_airwallex_errors():
    http, _, _ = session(
        LOGIN, error(400, "validation_error", "Dispute transition is not supported")
    )
    with pytest.raises(AirwallexError) as e:
        http.post("/x", {}, write=False)
    assert e.value.code == "validation_error"
    assert "not supported" in str(e.value)


def test_a_write_timeout_is_an_unknown_outcome_but_a_read_timeout_is_an_error():
    http, _, _ = session(LOGIN, TransportTimeout("read timed out"))
    with pytest.raises(WriteOutcomeUnknown):
        http.post("/accept", {"request_id": "r"}, write=True)
    http, _, _ = session(LOGIN, TransportTimeout("read timed out"))
    with pytest.raises(AirwallexError, match="timeout"):
        http.get("/disputes")


def test_a_server_error_on_a_write_is_an_unknown_outcome():
    http, _, _ = session(LOGIN, error(500, "internal_error"))
    with pytest.raises(WriteOutcomeUnknown):
        http.post("/challenge", {}, write=True)


def test_an_unreachable_host_is_an_error_even_for_a_write():
    # The connection failed before sending, so nothing can have been applied.
    http, _, _ = session(LOGIN, TransportUnreachable("dns failure"))
    with pytest.raises(AirwallexError, match="unreachable"):
        http.post("/accept", {}, write=True)


def test_only_sandbox_hosts_are_accepted():
    with pytest.raises(AirwallexError, match="host_not_allowed"):
        AirwallexHttp(Credentials("c", "k", "https://api.airwallex.com"))
    http, _, _ = session(LOGIN)
    with pytest.raises(AirwallexError, match="host_not_allowed"):
        http.upload("https://files.airwallex.com/api/v1/files/upload", filename="a.pdf",
                    content=b"%PDF", notes="n")  # fmt: skip


def test_amounts_are_decimal_never_float():
    http, _, _ = session(LOGIN, ok({"amount": 1500.25}))
    amount = http.get("/x")["amount"]
    assert isinstance(amount, Decimal) and amount == Decimal("1500.25")


def test_upload_is_multipart_to_the_files_host():
    http, transport, _ = session(LOGIN, ok({"file_id": "file_1"}, status=201))
    url = f"{SANDBOX_FILES}/api/v1/files/upload"
    assert http.upload(url, filename="evidence.pdf", content=b"%PDF-1.4", notes="case")["file_id"]
    call = transport.sent[-1]
    assert call.url == url
    assert call.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="evidence.pdf"' in call.body
    assert b"Content-Type: application/pdf" in call.body and b"%PDF-1.4" in call.body


def test_credentials_come_from_env_and_never_print_the_key():
    creds = Credentials.from_env(
        {"AIRWALLEX_CLIENT_ID": "abcd1234", "AIRWALLEX_API_KEY": "secret", "AIRWALLEX_BASE_URL": ""}
    )
    assert creds.base_url == SANDBOX_API
    assert "secret" not in repr(creds)
    with pytest.raises(AirwallexError, match="missing_credentials"):
        Credentials.from_env({})


def test_amounts_are_sent_as_json_numbers_with_their_exact_digits():
    http, transport, _ = session(LOGIN, ok({}))
    http.post("/x", {"amount": Decimal("1500"), "fee": Decimal("15.10"), "n": [Decimal("0.5")]},
              write=False)  # fmt: skip
    assert transport.sent[-1].body == b'{"amount": 1500, "fee": 15.1, "n": [0.5]}'


def test_the_trace_id_is_kept_for_support():
    body = b'{"code": "configuration_error", "message": "Invalid request", "trace_id": "t-123"}'
    http, _, _ = session(LOGIN, Response(400, body))
    with pytest.raises(AirwallexError, match="trace_id t-123"):
        http.post("/x", {}, write=False)


@pytest.mark.parametrize(
    "value",
    [
        "https://api.sandbox.airwallex.com",
        "https://api.sandbox.airwallex.com/",
        "https://api.sandbox.airwallex.com/api/v1",
        "https://api.sandbox.airwallex.com/api/v1/",
    ],
)
def test_the_base_url_may_be_given_with_or_without_api_v1(value):
    creds = Credentials.from_env(
        {"AIRWALLEX_CLIENT_ID": "c", "AIRWALLEX_API_KEY": "k", "AIRWALLEX_BASE_URL": value}
    )
    assert creds.base_url == SANDBOX_API
