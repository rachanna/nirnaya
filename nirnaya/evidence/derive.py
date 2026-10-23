"""Derive engine inputs from records. Evidence counts only if a record field establishes it."""

from collections.abc import Iterable
from datetime import datetime

from nirnaya.evidence.catalogue import (
    TRUSTED_AUTHORS,
    ContradictionRule,
    contradiction_rules_for,
    record_establishes,
)
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType, RecordAuthor, SourceSystem
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


def _before(value: object, cutoff: datetime) -> bool:
    """True if `value` is an ISO timestamp before `cutoff`. Undated or unparseable: True."""
    if not isinstance(value, str):
        return True
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        return True
    return parsed < cutoff


def unanswered_customer_contacts(
    records: Iterable[EvidenceRecord], dispute: Dispute
) -> tuple[str, ...]:
    """Customer helpdesk messages sent before the dispute that the merchant never answered.

    A message is answered only by a merchant-authored helpdesk record on the same
    `ticket_id` with a `replied_at`. Only which records exist is used, never what the customer
    wrote. An undated message counts as sent before the dispute (fail-safe).
    """
    in_case = [
        r for r in case_records(records, dispute) if r.source_system is SourceSystem.HELPDESK
    ]
    answered = {
        r.fields.get("ticket_id")
        for r in in_case
        if r.author is RecordAuthor.MERCHANT
        and r.fields.get("ticket_id") not in (None, "")
        and r.fields.get("replied_at") not in (None, "")
    }
    return tuple(
        r.id
        for r in in_case
        if r.author is RecordAuthor.CUSTOMER
        and _before(r.fields.get("sent_at"), dispute.opened_at)
        and r.fields.get("ticket_id") not in answered
    )
