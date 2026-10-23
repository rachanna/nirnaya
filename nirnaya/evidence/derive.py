"""Derive engine inputs from records. Evidence counts only if a record field establishes it."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from nirnaya.evidence.catalogue import (
    TEMPORAL_RULES,
    TRUSTED_AUTHORS,
    Anchor,
    ContradictionRule,
    TemporalRule,
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


@dataclass(frozen=True)
class TemporalClash:
    """A record establishing `contradicts` whose date is impossible against another record."""

    contradicts: EvidenceType
    description: str


def find_contradictions(
    fact: EvidenceType, records: Iterable[EvidenceRecord], dispute: Dispute
) -> list[tuple[EvidenceRecord, ContradictionRule | TemporalClash]]:
    """Every (record, reason) in this case contradicting `fact`, in id order.

    Rule contradictions come from any author; date clashes from trusted records only.
    """
    in_case = case_records(records, dispute)
    anchors = _order_anchors(in_case)
    found: list[tuple[EvidenceRecord, ContradictionRule | TemporalClash]] = []
    for record in in_case:
        found += [(record, rule) for rule in contradiction_rules_for(record, fact)]
        found += [
            (record, TemporalClash(fact, description))
            for rule, description in _temporal_clashes(record, dispute, anchors)
            if fact in rule.facts
        ]
    return found


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


# --- dates consistent across records ------------------------------------------------------------


def _instant(value: object) -> datetime | None:
    """A timezone-aware ISO timestamp, or None if the value cannot be read as one."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def _order_anchors(in_case: list[EvidenceRecord]) -> dict[Anchor, datetime]:
    placed = sorted(
        t
        for r in in_case
        if r.source_system is SourceSystem.ORDER and r.author in TRUSTED_AUTHORS
        if (t := _instant(r.fields.get("placed_at"))) is not None
    )
    if not placed:
        return {}
    return {Anchor.ORDER_PLACED_EARLIEST: placed[0], Anchor.ORDER_PLACED_LATEST: placed[-1]}


def _applicable(record: EvidenceRecord) -> list[TemporalRule]:
    """Temporal rules for facts this record actually establishes, on a field it carries."""
    established: set[EvidenceType] = set()
    for name in record.fields:
        established |= record_establishes(record, name)
    return [
        rule
        for rule in TEMPORAL_RULES
        if rule.source_system is record.source_system
        and rule.facts & established
        and record.fields.get(rule.field) not in (None, "")
    ]


_AFTER = {
    Anchor.ORDER_PLACED_LATEST: "is after the order was placed",
    Anchor.DISPUTE_OPENED: "is after the dispute opened",
    Anchor.CAPTURED: "is after it was captured",
}


def _temporal_clashes(
    record: EvidenceRecord, dispute: Dispute, anchors: dict[Anchor, datetime]
) -> list[tuple[TemporalRule, str]]:
    """(rule, description) for each rule whose date this record breaks (first bound broken)."""
    bounds = {
        **anchors,
        Anchor.DISPUTE_OPENED: dispute.opened_at,
        Anchor.CAPTURED: record.captured_at,
    }
    clashes = []
    for rule in _applicable(record):
        raw = record.fields[rule.field]
        at = _instant(raw)
        if at is None:
            clashes.append((rule, f"{rule.label} timestamp unreadable: {raw!r}"))
            continue
        early = [a for a in rule.not_before if a in bounds and at < bounds[a]]
        late = [a for a in rule.not_after if a in bounds and at > bounds[a]]
        if early:
            placed = _iso(bounds[early[0]])
            clashes.append((rule, f"{rule.label} {_iso(at)} predates the order (placed {placed})"))
        elif late:
            a = late[0]
            clashes.append((rule, f"{rule.label} {_iso(at)} {_AFTER[a]} ({_iso(bounds[a])})"))
    return clashes


def late_evidence_notes(
    records: Iterable[EvidenceRecord], dispute: Dispute
) -> tuple[tuple[EvidenceRecord, str], ...]:
    """Trusted evidence dated after the dispute opened. It still counts; the approver is told."""
    in_case = case_records(records, dispute)
    anchors = _order_anchors(in_case)
    opened = _iso(dispute.opened_at)
    notes = []
    for record in in_case:
        clashing = {rule for rule, _ in _temporal_clashes(record, dispute, anchors)}
        for rule in _applicable(record):
            at = _instant(record.fields[rule.field])
            late = at is not None and at > dispute.opened_at
            if rule.note_if_late and rule not in clashing and late:
                description = f"{rule.label} {_iso(at)} is after the dispute opened ({opened})"
                notes.append((record, description))
    return tuple(notes)
