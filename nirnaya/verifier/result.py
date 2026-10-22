from enum import StrEnum

from nirnaya.models.base import DomainModel, EvidenceSet, NonBlankStr
from nirnaya.models.claim import Claim
from nirnaya.models.enums import RecordAuthor, SourceSystem, ValidationStatus


class CheckName(StrEnum):
    # Per claim
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
    CASE_MERCHANT_MISMATCH = "CASE_MERCHANT_MISMATCH"
    HASH_MISMATCH = "HASH_MISMATCH"
    FIELD_MISSING = "FIELD_MISSING"
    EXTRACTED_VALUE_MISMATCH = "EXTRACTED_VALUE_MISMATCH"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"
    NAME_OR_ID_MISMATCH = "NAME_OR_ID_MISMATCH"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    EXCEEDS_RECORD = "EXCEEDS_RECORD"
    TEXT_MISMATCH = "TEXT_MISMATCH"
    REASON_SPECIFIC_UNDER_UNKNOWN = "REASON_SPECIFIC_UNDER_UNKNOWN"
    DENIES_CUSTOMER_COMPLAINT = "DENIES_CUSTOMER_COMPLAINT"
    # Per case
    DUPLICATE_CLAIM_ID = "DUPLICATE_CLAIM_ID"
    REQUIRED_EVIDENCE_MISSING = "REQUIRED_EVIDENCE_MISSING"


class Failure(DomainModel):
    check: CheckName
    detail: NonBlankStr


class Counterevidence(DomainModel):
    """A record the human approver must see. The draft must never contradict it."""

    record_id: NonBlankStr
    source_system: SourceSystem
    author: RecordAuthor
    # The claim it bears on; None for a customer record that matched no specific rule.
    claim_id: str | None
    description: NonBlankStr


class ClaimResult(DomainModel):
    # The claim as verified: validation_status and contradicting_record_ids set by the verifier.
    claim: Claim
    failures: tuple[Failure, ...]
    # Merchant or third-party records that disagree with this claim's record.
    conflicting_record_ids: tuple[str, ...]


class VerificationOutcome(StrEnum):
    PASSED = "PASSED"
    # An unsupported or unprovable claim: never submitted; the case goes to a human with it.
    BLOCKED = "BLOCKED"
    # Every claim is supported, but merchant records contradict each other: a human decides.
    REFER = "REFER"


class VerificationResult(DomainModel):
    case_id: NonBlankStr
    merchant_id: NonBlankStr
    outcome: VerificationOutcome
    claims: tuple[ClaimResult, ...]
    case_failures: tuple[Failure, ...]
    counterevidence: tuple[Counterevidence, ...]
    # Facts established by VERIFIED claims.
    established: EvidenceSet

    @property
    def submittable(self) -> bool:
        return self.outcome is VerificationOutcome.PASSED

    @property
    def blocked(self) -> tuple[ClaimResult, ...]:
        return tuple(
            c for c in self.claims if c.claim.validation_status is ValidationStatus.BLOCKED
        )
