"""Interfaces the orchestrator depends on. M3 uses fakes and scripted parts; M4 and M5
plug in Claude and the real Airwallex client behind the same Protocols."""

from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol

from nirnaya.models.base import DomainModel, NonBlankStr
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AcceptReason, ChallengeReason, EvidenceType, ProductType
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
    """What the gateway submits; the client maps it onto the challenge body. Every value is
    decided in code from verified facts and policy, never by the model."""

    request_id: NonBlankStr
    file_ids: tuple[NonBlankStr, ...]
    claim_ids: tuple[NonBlankStr, ...]
    reason: ChallengeReason
    # Required by Airwallex at CHARGEBACK; None at RFI.
    product_type: ProductType | None = None


class DisputeSource(Protocol):
    def list_disputes(self, merchant_id: str) -> list[Dispute]: ...
    def retrieve_dispute(self, merchant_id: str, dispute_id: str) -> Dispute: ...
    def payment_attempt(self, merchant_id: str, attempt_id: str) -> PaymentAttempt | None: ...
    def payment_intent_owner(self, merchant_id: str, intent_id: str) -> str | None: ...


class DisputeActions(Protocol):
    def accept(
        self, merchant_id: str, dispute_id: str, *, request_id: str, reason: AcceptReason
    ) -> Dispute: ...
    def challenge(
        self, merchant_id: str, dispute_id: str, request: ChallengeRequest
    ) -> Dispute: ...
    def upload_file(self, merchant_id: str, *, filename: str, content: bytes) -> str: ...


class Investigator(Protocol):
    def investigate(self, dispute: Dispute, targets: list[EvidenceType], tools: Any) -> Any:
        """Fetch evidence for these targets through the tool gateway (one round).
        Returns an InvestigationResult (nirnaya.agent.runs)."""
        ...


class Drafter(Protocol):
    def draft(self, dispute: Dispute, records: list[EvidenceRecord]) -> Any:
        """Propose claims. Returns a DraftResult (nirnaya.agent.runs); never trusted."""
        ...


class StepObserver(Protocol):
    """Watches the state machine without taking part: told when a case's step runs and when
    the case changes state. Used for tracing; the machine ignores anything it raises."""

    def step(self, case: Any) -> AbstractContextManager[None]:  # noqa: ANN401 - a CaseRow
        """Wraps one handler run; model calls the handler makes happen inside it."""
        ...

    def transition(
        self,
        case: Any,  # noqa: ANN401 - a CaseRow
        frm: str,
        to: str,
        body: dict[str, Any],
        external: bool,
    ) -> None:
        """A committed state change (external: the watchdog or a failure referral)."""
        ...


class _NullObserver:
    def step(self, case: Any) -> AbstractContextManager[None]:  # noqa: ANN401
        return nullcontext()

    def transition(
        self,
        case: Any,  # noqa: ANN401
        frm: str,
        to: str,
        body: dict[str, Any],
        external: bool,
    ) -> None:
        return None


NULL_OBSERVER: StepObserver = _NullObserver()
