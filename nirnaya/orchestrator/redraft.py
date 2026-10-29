"""Self-correction: which verifier failures the drafter may fix by redrafting (architecture 8.6).

Deterministic. The verifier still clears every draft; this only decides whether a blocked draft
goes back to the drafter once, with the failures, and whether a passing redraft still needs a
person:

- FORMAT: the cited field of the case's own record does establish the fact; only the way the
  value was written failed. A passing redraft proceeds.
- OMISSION: a fact the reason requires was left out. A passing redraft proceeds.
- OVERREACH: any other blocked claim (wrong record, a field that does not establish the fact,
  more than the record shows, a wrong amount or date, another merchant's record). A passing
  redraft still goes to a person, who sees both drafts.

Never redrafted: records that contradict each other (a person decides), changed evidence, and
a failing quote claim (code builds those from a person's confirmation; the model cannot fix it).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from nirnaya.evidence.catalogue import REQUIRED_EVIDENCE, record_establishes
from nirnaya.evidence.derive import case_records
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import ValidationStatus
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.verifier.result import (
    CheckName,
    ClaimResult,
    VerificationOutcome,
    VerificationResult,
)

Kind = Literal["FORMAT", "OMISSION", "OVERREACH"]

# Failures about how a value was written, not whether the record supports it.
FORMAT_ONLY = frozenset(
    {CheckName.EXTRACTED_VALUE_MISMATCH, CheckName.VALUE_MISMATCH, CheckName.NAME_OR_ID_MISMATCH}
)
_QUOTE_CHECKS = frozenset(
    {CheckName.QUOTE_NOT_IN_RECORD, CheckName.QUOTE_UNCONFIRMED, CheckName.TEXT_NOT_ALLOWED}
)


def is_format_only(c: ClaimResult, records: Iterable[EvidenceRecord], dispute: Dispute) -> bool:
    """The cited field of the case's own record establishes the fact, and every failure is
    about how the value was written."""
    record = {r.id: r for r in case_records(records, dispute)}.get(c.claim.source_record_id)
    return (
        record is not None
        and c.claim.establishes in record_establishes(record, c.claim.source_field)
        and {f.check for f in c.failures} <= FORMAT_ONLY
    )


def classify(c: ClaimResult, records: Iterable[EvidenceRecord], dispute: Dispute) -> Kind:
    return "FORMAT" if is_format_only(c, records, dispute) else "OVERREACH"


@dataclass(frozen=True)
class RedraftPlan:
    retry: bool
    overreach: bool
    kinds: frozenset[str] = frozenset()
    feedback: dict[str, Any] = field(default_factory=dict)


def _missing_required(dispute: Dispute, result: VerificationResult) -> list[list[str]]:
    return [
        sorted(str(t) for t in group)
        for group in REQUIRED_EVIDENCE[dispute.reason_category]
        if not group & result.established
    ]


def redraft_plan(
    dispute: Dispute, result: VerificationResult, records: list[EvidenceRecord]
) -> RedraftPlan:
    blocked = [c for c in result.claims if c.claim.validation_status is ValidationStatus.BLOCKED]
    items = []
    kinds: set[str] = set()
    for c in blocked:
        kind = classify(c, records, dispute)
        kinds.add(kind)
        items.append(
            {
                "claim_id": c.claim.id,
                "source_record_id": c.claim.source_record_id,
                "source_field": c.claim.source_field,
                "establishes": str(c.claim.establishes),
                "extracted_value": c.claim.extracted_value,
                "kind": kind,
                "failures": [{"check": str(f.check), "detail": f.detail} for f in c.failures],
            }
        )
    missing = _missing_required(dispute, result)
    case_checks = {f.check for f in result.case_failures}
    # Omission: a required fact no claim even tried to state (a blocked attempt is not one).
    attempted = {str(c.claim.establishes) for c in blocked}
    if any(not set(group) & attempted for group in missing):
        kinds.add("OMISSION")
    feedback = {"blocked_claims": items, "missing_required": missing}
    claim_checks = {f.check for c in blocked for f in c.failures}
    retry = (
        result.outcome is VerificationOutcome.BLOCKED
        and CheckName.HASH_MISMATCH not in claim_checks
        and not claim_checks & _QUOTE_CHECKS
        and not any(c.claim.value_kind == "QUOTE" for c in blocked)
        and case_checks <= {CheckName.REQUIRED_EVIDENCE_MISSING}
        and bool(kinds)
    )
    return RedraftPlan(
        retry=retry, overreach="OVERREACH" in kinds, kinds=frozenset(kinds), feedback=feedback
    )
