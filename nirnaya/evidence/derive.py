"""Derive engine inputs from records. Evidence counts only if a record field establishes it."""

from collections.abc import Iterable

from nirnaya.evidence.catalogue import (
    TRUSTED_AUTHORS,
    ContradictionRule,
    contradiction_rules_for,
    record_establishes,
)
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType
from nirnaya.models.evidence import EvidenceRecord


def case_records(records: Iterable[EvidenceRecord], dispute: Dispute) -> list[EvidenceRecord]:
    """Only this case's records for this merchant, in id order. Everything else is ignored."""
    return sorted(
        (r for r in records if r.case_id == dispute.id and r.merchant_id == dispute.merchant_id),
        key=lambda r: r.id,
    )


def evidence_present(
    records: Iterable[EvidenceRecord], dispute: Dispute
) -> frozenset[EvidenceType]:
    """Evidence types established by this case's intact, trusted records."""
    found: set[EvidenceType] = set()
    for record in case_records(records, dispute):
        if not record.hash_ok():
            continue
        for field in record.fields:
            found |= record_establishes(record, field)
    return frozenset(found)


def find_contradictions(
    fact: EvidenceType, records: Iterable[EvidenceRecord], dispute: Dispute
) -> list[tuple[EvidenceRecord, ContradictionRule]]:
    """Every (record, rule) in this case contradicting `fact`, from any author, in id order."""
    return [
        (record, rule)
        for record in case_records(records, dispute)
        for rule in contradiction_rules_for(record, fact)
    ]


def contradictory_record_ids(
    records: Iterable[EvidenceRecord], dispute: Dispute
) -> tuple[str, ...]:
    """Merchant or third-party records contradicting evidence the case's records establish.

    These are records that disagree with each other, so the engine refers the case.
    Customer allegations are not included: they make a normal dispute, not a conflict.
    """
    records = list(records)
    ids = {
        record.id
        for fact in evidence_present(records, dispute)
        for record, _ in find_contradictions(fact, records, dispute)
        if record.author in TRUSTED_AUTHORS
    }
    return tuple(sorted(ids))
