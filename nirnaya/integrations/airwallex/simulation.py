"""Sandbox-only calls, behind one interface (the kit guide's rule): test payments and dispute
simulation. Never used by the orchestrator; only by the sandbox runner and tests.

Endpoints and fields (API reference and kit 4 guide, checked 2026-10-27):
- POST /api/v1/pa/payment_intents/create   {request_id, amount, currency, merchant_order_id}
- GET  /api/v1/pa/payment_intents/{id}      (status SUCCEEDED once paid)
- POST /api/v1/pa/payment_intents/{id}/confirm
       {request_id, payment_method: {type: "card", card: {number, expiry_month, expiry_year,
        cvc, name}}}  -- test card 4035501000000008, any future expiry, any CVC
- POST /api/v1/simulation/pa/payment_disputes/create
       {payment_intent_id, reason_code, stage, due_at, amount?}
- POST /api/v1/simulation/pa/payment_disputes/{id}/escalate {due_at}
- POST /api/v1/simulation/pa/payment_disputes/{id}/resolve  {in_favor_of: MERCHANT|CUSTOMER}
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import quote

from nirnaya.integrations.airwallex.http import SANDBOX_API, AirwallexHttp
from nirnaya.models.enums import Stage
from nirnaya.models.money import Money
from nirnaya.orchestrator.ports import AirwallexError

TEST_CARD = "4035501000000008"
InFavorOf = Literal["MERCHANT", "CUSTOMER"]
# Request ids for staging are derived from a key, so re-running a step retries the same
# operation instead of creating a second payment or dispute.
_NAMESPACE = uuid.UUID("0b5e3c1a-7d2f-4f60-9c0e-5a1d2b3c4e5f")


def staging_id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def wire_time(at: datetime) -> str:
    """The documented format, e.g. 2025-05-20T13:14:00+0000."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


# A payment intent counts as paid in this status (Payment Intents API reference).
PAID = "SUCCEEDED"


@dataclass(frozen=True)
class StagedIntent:
    payment_intent_id: str
    # For the hosted checkout page; valid for 60 minutes (Payment Intents API reference).
    client_secret: str | None
    status: str


@dataclass(frozen=True)
class StagedPayment:
    payment_intent_id: str
    payment_attempt_id: str | None
    status: str


class SandboxSimulator(Protocol):
    def create_intent(
        self, merchant_id: str, *, key: str, amount: Money, merchant_order_id: str
    ) -> StagedIntent: ...
    def confirm_with_test_card(
        self, merchant_id: str, *, key: str, payment_intent_id: str
    ) -> StagedPayment: ...
    def intent_status(self, merchant_id: str, payment_intent_id: str) -> str: ...
    def create_dispute(
        self,
        merchant_id: str,
        *,
        payment_intent_id: str,
        reason_code: str,
        stage: Stage,
        due_at: datetime,
    ) -> str: ...
    def escalate(self, merchant_id: str, dispute_id: str, *, due_at: datetime) -> None: ...
    def resolve(self, merchant_id: str, dispute_id: str, *, in_favor_of: InFavorOf) -> None: ...


class AirwallexSimulator:
    """The real sandbox simulator. Constructible only against the sandbox host."""

    def __init__(self, http: AirwallexHttp, *, merchant_id: str, base_url: str) -> None:
        if base_url != SANDBOX_API:
            raise AirwallexError("host_not_allowed", "simulation runs only in the sandbox")
        self._http = http
        self.merchant_id = merchant_id

    def _own(self, merchant_id: str) -> None:
        if merchant_id != self.merchant_id:
            raise AirwallexError("wrong_merchant", "this simulator serves another merchant")

    def create_intent(
        self, merchant_id: str, *, key: str, amount: Money, merchant_order_id: str
    ) -> StagedIntent:
        self._own(merchant_id)
        intent = self._http.post(
            "/api/v1/pa/payment_intents/create",
            {
                "request_id": staging_id(key, "intent"),
                "amount": amount.amount,
                "currency": amount.currency,
                "merchant_order_id": merchant_order_id,
            },
            write=False,
        )
        intent_id = intent.get("id")
        if not isinstance(intent_id, str) or not intent_id:
            raise AirwallexError("staging_failed", "no payment intent id returned")
        secret = intent.get("client_secret")
        return StagedIntent(
            intent_id, secret if isinstance(secret, str) else None, str(intent.get("status"))
        )

    def confirm_with_test_card(
        self, merchant_id: str, *, key: str, payment_intent_id: str
    ) -> StagedPayment:
        """Server-side confirm with the raw test card ("native API"). Needs Airwallex to enable
        native API access on the account; otherwise pay through the hosted checkout page."""
        self._own(merchant_id)
        intent_id = payment_intent_id
        confirmed = self._http.post(
            f"/api/v1/pa/payment_intents/{quote(intent_id, safe='')}/confirm",
            {
                "request_id": staging_id(key, "confirm"),
                "payment_method": {
                    "type": "card",
                    "card": {
                        "number": TEST_CARD,
                        "expiry_month": "12",
                        "expiry_year": str(datetime.now(UTC).year + 3),
                        "cvc": "123",
                        "name": "Nirnaya Sandbox",
                    },
                },
            },
            write=False,
        )
        attempt = confirmed.get("latest_payment_attempt")
        attempt_id = attempt.get("id") if isinstance(attempt, dict) else None
        return StagedPayment(intent_id, attempt_id, str(confirmed.get("status")))

    def intent_status(self, merchant_id: str, payment_intent_id: str) -> str:
        self._own(merchant_id)
        body = self._http.get(f"/api/v1/pa/payment_intents/{quote(payment_intent_id, safe='')}")
        return str(body.get("status"))

    def create_dispute(
        self,
        merchant_id: str,
        *,
        payment_intent_id: str,
        reason_code: str,
        stage: Stage,
        due_at: datetime,
    ) -> str:
        self._own(merchant_id)
        body = self._http.post(
            "/api/v1/simulation/pa/payment_disputes/create",
            {
                "payment_intent_id": payment_intent_id,
                "reason_code": reason_code,
                "stage": stage.value,
                "due_at": wire_time(due_at),
            },
            write=False,
        )
        dispute_id = body.get("id")
        if not isinstance(dispute_id, str) or not dispute_id:
            raise AirwallexError("staging_failed", "no dispute id returned")
        return dispute_id

    def escalate(self, merchant_id: str, dispute_id: str, *, due_at: datetime) -> None:
        self._own(merchant_id)
        self._http.post(
            f"/api/v1/simulation/pa/payment_disputes/{quote(dispute_id, safe='')}/escalate",
            {"due_at": wire_time(due_at)},
            write=False,
        )

    def resolve(self, merchant_id: str, dispute_id: str, *, in_favor_of: InFavorOf) -> None:
        self._own(merchant_id)
        self._http.post(
            f"/api/v1/simulation/pa/payment_disputes/{quote(dispute_id, safe='')}/resolve",
            {"in_favor_of": in_favor_of},
            write=False,
        )
