"""A source-and-actions adapter limited to a set of dispute ids.

The sandbox runner wraps the client in it, so the orchestrator only ever sees and acts on the
disputes the runner staged; any other dispute in the sandbox account is invisible to it. It
forwards the write gateway's calls; it never initiates a write itself.
"""

from typing import Any

from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AcceptReason
from nirnaya.models.payment import PaymentAttempt
from nirnaya.orchestrator.ports import AirwallexError, ChallengeRequest


class ScopedAirwallex:
    """The client, limited to the disputes this runner staged. Anything else is invisible."""

    def __init__(self, inner: Any, allowed: set[str]) -> None:  # noqa: ANN401
        self._inner = inner
        self.allowed = allowed

    def _check(self, dispute_id: str) -> None:
        if dispute_id not in self.allowed:
            raise AirwallexError("not_staged", f"{dispute_id} was not staged by this runner")

    def list_disputes(self, merchant_id: str) -> list[Dispute]:
        return [d for d in self._inner.list_disputes(merchant_id) if d.id in self.allowed]

    def retrieve_dispute(self, merchant_id: str, dispute_id: str) -> Dispute:
        self._check(dispute_id)
        return self._inner.retrieve_dispute(merchant_id, dispute_id)

    def payment_attempt(self, merchant_id: str, attempt_id: str) -> PaymentAttempt | None:
        return self._inner.payment_attempt(merchant_id, attempt_id)

    def payment_intent_owner(self, merchant_id: str, intent_id: str) -> str | None:
        return self._inner.payment_intent_owner(merchant_id, intent_id)

    def accept(
        self, merchant_id: str, dispute_id: str, *, request_id: str, reason: AcceptReason
    ) -> Dispute:
        self._check(dispute_id)
        return self._inner.accept(merchant_id, dispute_id, request_id=request_id, reason=reason)

    def challenge(self, merchant_id: str, dispute_id: str, request: ChallengeRequest) -> Dispute:
        self._check(dispute_id)
        return self._inner.challenge(merchant_id, dispute_id, request)

    def upload_file(self, merchant_id: str, *, filename: str, content: bytes) -> str:
        return self._inner.upload_file(merchant_id, filename=filename, content=content)
