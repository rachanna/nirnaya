"""In-memory fake of the Airwallex dispute APIs, for tests and offline demos. SYNTHETIC.

Models documented sandbox behaviour:
- challenge only at RFI or CHARGEBACK with REQUIRES_RESPONSE (Challenge API reference);
- request_id reused for the same operation returns the first result; reused for a different
  operation fails with duplicate_request_id (kit 4 guide);
- simulate escalate (RFI or PRE_CHARGEBACK -> CHARGEBACK; not from CHARGEBACK) and resolve;
- optional instant auto-accept of pre-chargebacks, as the sandbox does.
Failures can be injected: an error, or a timeout before or after the write lands.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AcceptReason, DisputeStatus, Stage
from nirnaya.models.payment import PaymentAttempt
from nirnaya.orchestrator.ports import AirwallexError, ChallengeRequest, WriteOutcomeUnknown

FailureKind = Literal["error", "timeout_before_apply", "timeout_after_apply"]


@dataclass
class _Entry:
    dispute: Dispute
    attempt: PaymentAttempt | None
    intent_owner: str | None


@dataclass
class FakeAirwallex:
    auto_accept_pre_chargeback: bool = False
    _disputes: dict[str, _Entry] = field(default_factory=dict)
    _requests: dict[str, tuple[str, Dispute]] = field(default_factory=dict)
    _failures: dict[str, FailureKind] = field(default_factory=dict)
    _files: dict[str, tuple[str, str, bytes]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    # --- seeding and simulation -----------------------------------------------------------

    def add_dispute(
        self,
        dispute: Dispute,
        *,
        attempt: PaymentAttempt | None = None,
        intent_owner: str | None = "same",
    ) -> None:
        if self.auto_accept_pre_chargeback and dispute.stage is Stage.PRE_CHARGEBACK:
            dispute = dispute.model_copy(update={"status": DisputeStatus.ACCEPTED})
        owner = dispute.merchant_id if intent_owner == "same" else intent_owner
        self._disputes[dispute.id] = _Entry(dispute, attempt, owner)

    def simulate_escalate(self, dispute_id: str, *, due_at: datetime) -> Dispute:
        entry = self._disputes[dispute_id]
        if entry.dispute.stage not in {Stage.RFI, Stage.PRE_CHARGEBACK}:
            raise AirwallexError("validation_error", "escalation not supported from this stage")
        entry.dispute = entry.dispute.model_copy(
            update={
                "stage": Stage.CHARGEBACK,
                "status": DisputeStatus.REQUIRES_RESPONSE,
                "due_at": due_at,
            }
        )
        return entry.dispute

    def set_ai_recommendation(self, dispute_id: str, rec: Any) -> None:  # noqa: ANN401
        """What Airwallex's AI says about the dispute now (an AiRecommendation, or None)."""
        entry = self._disputes[dispute_id]
        entry.dispute = entry.dispute.model_copy(update={"ai_recommendation": rec})

    def simulate_resolve(
        self, dispute_id: str, *, in_favor_of: Literal["MERCHANT", "CUSTOMER"]
    ) -> Dispute:
        entry = self._disputes[dispute_id]
        status = DisputeStatus.WON if in_favor_of == "MERCHANT" else DisputeStatus.LOST
        entry.dispute = entry.dispute.model_copy(update={"status": status})
        return entry.dispute

    def fail_next(self, operation: str, kind: FailureKind) -> None:
        self._failures[operation] = kind

    def set_amount(self, dispute_id: str, amount) -> None:  # noqa: ANN001 - test helper
        entry = self._disputes[dispute_id]
        entry.dispute = entry.dispute.model_copy(update={"amount": amount})

    def files(self) -> dict[str, tuple[str, str, bytes]]:
        return dict(self._files)

    # --- DisputeSource --------------------------------------------------------------------

    def _entry(self, merchant_id: str, dispute_id: str) -> _Entry:
        entry = self._disputes.get(dispute_id)
        if entry is None or entry.dispute.merchant_id != merchant_id:
            raise AirwallexError("not_found", dispute_id)
        return entry

    def list_disputes(self, merchant_id: str) -> list[Dispute]:
        self.calls.append(("list", merchant_id))
        return sorted(
            (e.dispute for e in self._disputes.values() if e.dispute.merchant_id == merchant_id),
            key=lambda d: d.id,
        )

    def retrieve_dispute(self, merchant_id: str, dispute_id: str) -> Dispute:
        self.calls.append(("retrieve", dispute_id))
        if self._failures.pop("retrieve", None):
            raise AirwallexError("internal_error", "injected")
        return self._entry(merchant_id, dispute_id).dispute

    def payment_attempt(self, merchant_id: str, attempt_id: str) -> PaymentAttempt | None:
        for e in self._disputes.values():
            if e.attempt is not None and e.attempt.id == attempt_id:
                return e.attempt if e.attempt.merchant_id == merchant_id else None
        return None

    def payment_intent_owner(self, merchant_id: str, intent_id: str) -> str | None:
        for e in self._disputes.values():
            if e.dispute.payment_intent_id == intent_id and e.dispute.merchant_id == merchant_id:
                return e.intent_owner
        return None

    # --- DisputeActions -------------------------------------------------------------------

    def _idempotent(self, request_id: str, signature: str) -> Dispute | None:
        prior = self._requests.get(request_id)
        if prior is None:
            return None
        if prior[0] != signature:
            raise AirwallexError("duplicate_request_id", request_id)
        return prior[1]

    def _apply(
        self,
        operation: str,
        request_id: str,
        signature: str,
        entry: _Entry,
        new_status: DisputeStatus,
    ) -> Dispute:
        failure = self._failures.pop(operation, None)
        if failure == "error":
            raise AirwallexError("internal_error", "injected")
        if failure == "timeout_before_apply":
            raise WriteOutcomeUnknown(f"{operation} timed out before reaching Airwallex")
        entry.dispute = entry.dispute.model_copy(update={"status": new_status})
        self._requests[request_id] = (signature, entry.dispute)
        if failure == "timeout_after_apply":
            raise WriteOutcomeUnknown(f"{operation} timed out")
        return entry.dispute

    def accept(
        self, merchant_id: str, dispute_id: str, *, request_id: str, reason: AcceptReason
    ) -> Dispute:
        self.calls.append(("accept", dispute_id))
        signature = f"accept:{dispute_id}"
        if (prior := self._idempotent(request_id, signature)) is not None:
            return prior
        entry = self._entry(merchant_id, dispute_id)
        if entry.dispute.status is not DisputeStatus.REQUIRES_RESPONSE:
            raise AirwallexError("validation_error", "dispute does not require a response")
        return self._apply("accept", request_id, signature, entry, DisputeStatus.ACCEPTED)

    def challenge(self, merchant_id: str, dispute_id: str, request: ChallengeRequest) -> Dispute:
        self.calls.append(("challenge", dispute_id))
        signature = f"challenge:{dispute_id}"
        if (prior := self._idempotent(request.request_id, signature)) is not None:
            return prior
        entry = self._entry(merchant_id, dispute_id)
        if (
            entry.dispute.status is not DisputeStatus.REQUIRES_RESPONSE
            or entry.dispute.stage not in {Stage.RFI, Stage.CHARGEBACK}
        ):
            raise AirwallexError(
                "validation_error", "challenge needs RFI or CHARGEBACK awaiting response"
            )
        if entry.dispute.stage is Stage.CHARGEBACK and request.product_type is None:
            raise AirwallexError("validation_error", "product_type is required at CHARGEBACK")
        for file_id in request.file_ids:
            if file_id not in self._files or self._files[file_id][0] != merchant_id:
                raise AirwallexError("validation_error", f"unknown file {file_id}")
        return self._apply(
            "challenge", request.request_id, signature, entry, DisputeStatus.CHALLENGED
        )

    def upload_file(self, merchant_id: str, *, filename: str, content: bytes) -> str:
        self.calls.append(("upload", filename))
        if self._failures.pop("upload", None):
            raise AirwallexError("internal_error", "injected")
        if filename.lower().endswith(".png"):
            raise AirwallexError("validation_error", "PNG evidence is rejected")
        file_id = f"file_{len(self._files) + 1:04d}"
        self._files[file_id] = (merchant_id, filename, content)
        return file_id
