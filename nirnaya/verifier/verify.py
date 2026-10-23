"""The verifier: the only component that can clear a draft for submission."""

from collections import Counter
from collections.abc import Iterable, Mapping

from nirnaya.evidence.catalogue import TRUSTED_AUTHORS
from nirnaya.evidence.derive import case_records, cross_check
from nirnaya.models.claim import Claim
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import RecordAuthor, ValidationStatus
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.models.money import FxTable
from nirnaya.verifier.checks import (
    check_claim_scope,
    check_denial,
    check_establishes,
    check_field,
    check_hash,
    check_reason_specific,
    check_record_scope,
    check_required_evidence,
    check_text,
    check_value,
)
from nirnaya.verifier.result import (
    ApproverNote,
    CheckName,
    ClaimResult,
    Counterevidence,
    Failure,
    VerificationOutcome,
    VerificationResult,
)


def _claim_failures(
    claim: Claim,
    by_id: Mapping[str, list[EvidenceRecord]],
    in_case: list[EvidenceRecord],
    dispute: Dispute,
    fx: FxTable | None,
    expected_hashes: Mapping[str, str],
) -> list[Failure]:
    failures = check_claim_scope(claim, dispute)
    failures += check_text(claim)
    failures += check_reason_specific(claim, dispute)
    failures += check_denial(claim, in_case, dispute)

    matches = by_id.get(claim.source_record_id, [])
    if len(matches) != 1:
        problem = "no record" if not matches else "more than one record"
        return failures + [
            Failure(
                check=CheckName.RECORD_NOT_FOUND,
                detail=f"{problem} with id {claim.source_record_id!r}",
            )
        ]
    (record,) = matches
    scope = check_record_scope(record, dispute)
    if scope:
        # Fail closed and stop: nothing about another tenant's record goes further.
        return failures + scope

    failures += check_hash(record, expected_hashes)
    field = check_field(claim, record)
    if field and field[0].check is CheckName.FIELD_MISSING:
        return failures + field
    failures += field
    failures += check_value(claim, record, dispute, fx)
    failures += check_establishes(claim, record)
    return failures


def verify(
    dispute: Dispute,
    claims: Iterable[Claim],
    records: Iterable[EvidenceRecord],
    *,
    fx: FxTable | None = None,
    expected_hashes: Mapping[str, str] | None = None,
) -> VerificationResult:
    """Run every blocking check on every claim and the case-level checks.

    Never mutates its inputs. Statuses and contradicting ids already on incoming claims
    are ignored and replaced: a drafter cannot pre-approve its own claim.
    """
    claims = list(claims)
    records = list(records)
    expected_hashes = expected_hashes or {}
    in_case = case_records(records, dispute)
    by_id: dict[str, list[EvidenceRecord]] = {}
    for record in records:
        by_id.setdefault(record.id, []).append(record)

    case_failures: list[Failure] = []
    duplicates = sorted(i for i, n in Counter(c.id for c in claims).items() if n > 1)
    if duplicates:
        case_failures.append(
            Failure(check=CheckName.DUPLICATE_CLAIM_ID, detail=f"duplicate claim ids {duplicates}")
        )

    # What the records say about each other, computed once for the whole case.
    checked = cross_check(records, dispute)
    results: list[ClaimResult] = []
    # Keyed by (record, claim, description): one record can contradict a claim for two reasons.
    counterevidence: dict[tuple[str, str | None, str], Counterevidence] = {}
    for claim in claims:
        failures = _claim_failures(claim, by_id, in_case, dispute, fx, expected_hashes)

        contradicting: list[str] = []
        conflicting: list[str] = []
        for c in checked.contradictions:
            if c.contradicts is not claim.establishes:
                continue
            record = c.record
            contradicting.append(record.id)
            if record.author in TRUSTED_AUTHORS:
                conflicting.append(record.id)
            counterevidence[(record.id, claim.id, c.description)] = Counterevidence(
                record_id=record.id,
                source_system=record.source_system,
                author=record.author,
                claim_id=claim.id,
                description=c.description,
            )

        if failures:
            status = ValidationStatus.BLOCKED
        elif conflicting:
            status = ValidationStatus.CONFLICT
        else:
            status = ValidationStatus.VERIFIED
        verified_claim = claim.model_copy(
            update={
                "validation_status": status,
                "contradicting_record_ids": tuple(sorted(set(contradicting))),
            }
        )
        results.append(
            ClaimResult(
                claim=verified_claim,
                failures=tuple(failures),
                conflicting_record_ids=tuple(sorted(set(conflicting))),
            )
        )

    # Every customer-authored record is material: the approver sees each allegation.
    surfaced = {record_id for record_id, _, _ in counterevidence}
    for record in in_case:
        if record.author is RecordAuthor.CUSTOMER and record.id not in surfaced:
            description = "customer-authored record on file"
            counterevidence[(record.id, None, description)] = Counterevidence(
                record_id=record.id,
                source_system=record.source_system,
                author=record.author,
                claim_id=None,
                description=description,
            )

    established = frozenset(
        r.claim.establishes
        for r in results
        if r.claim.validation_status is ValidationStatus.VERIFIED
    )
    # A CONFLICT claim is supported by its record but contested by another; it still meets
    # the requirement (the case is referred for the conflict, not blocked as unsupported).
    supported = established | {
        r.claim.establishes
        for r in results
        if r.claim.validation_status is ValidationStatus.CONFLICT
    }
    case_failures += check_required_evidence(dispute, supported)

    statuses = {r.claim.validation_status for r in results}
    if case_failures or ValidationStatus.BLOCKED in statuses:
        outcome = VerificationOutcome.BLOCKED
    elif ValidationStatus.CONFLICT in statuses:
        outcome = VerificationOutcome.REFER
    else:
        outcome = VerificationOutcome.PASSED

    notes = tuple(
        ApproverNote(record_id=record.id, claim_id=r.claim.id, description=description)
        for r in results
        for record, description in checked.late_notes
        if record.id == r.claim.source_record_id
    )

    return VerificationResult(
        case_id=dispute.id,
        merchant_id=dispute.merchant_id,
        outcome=outcome,
        claims=tuple(results),
        case_failures=tuple(case_failures),
        counterevidence=tuple(
            counterevidence[k]
            for k in sorted(counterevidence, key=lambda k: (k[0], k[1] or "", k[2]))
        ),
        established=established,
        notes=notes,
    )
