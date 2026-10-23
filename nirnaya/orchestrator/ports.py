"""Interfaces the orchestrator depends on. M3 uses fakes and scripted parts; M4 and M5
plug in Claude and the real Airwallex client behind the same Protocols."""

from typing import Protocol

from nirnaya.models.base import DomainModel, NonBlankStr
from nirnaya.models.claim import Claim
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AcceptReason, EvidenceType
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.models.payment import PaymentAttempt


class AirwallexError(RuntimeError):
    """Airwallex rejected the request (validation_error, not_found, duplicate_request_id...)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class WriteOutcomeUnknown(RuntimeError):
    """The write may or may not have landed (timeout). Re-fetch before doing anything else."""


class ChallengeRequest(DomainModel):
    """What the gateway submits. M5 maps this onto the real challenge body."""

    request_id: NonBlankStr
    file_ids: tuple[NonBlankStr, ...]
    claim_ids: tuple[NonBlankStr, ...]


class DisputeSource(Protocol):
    def list_disputes(self, merchant_id: str) -> list[Dispute]: ...
    def retrieve_dispute(self, merchant_id: str, dispute_id: str) -> Dispute: ...
    def payment_attempt(self, merchant_id: str, attempt_id: str) -> PaymentAttempt | None: ...
    def payment_intent_owner(self, merchant_id: str, intent_id: str) -> str | None: ...


class DisputeActions(Protocol):
    def accept(self, merchant_id: str, dispute_id: str, *, request_id: str,
               reason: AcceptReason) -> Dispute: ...  # fmt: skip
    def challenge(
        self, merchant_id: str, dispute_id: str, request: ChallengeRequest
    ) -> Dispute: ...
    def upload_file(self, merchant_id: str, *, filename: str, content: bytes) -> str: ...


class Investigator(Protocol):
    def investigate(self, dispute: Dispute, targets: list[EvidenceType]) -> int:
        """Fetch evidence for these targets via the tool gateway; returns tool calls used."""
        ...


class Drafter(Protocol):
    def draft(self, dispute: Dispute, records: list[EvidenceRecord]) -> list[Claim]: ...
