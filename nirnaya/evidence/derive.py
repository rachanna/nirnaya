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
    contradiction_rules_matching,
    record_establishes,
)
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType, RecordAuthor, SourceSystem
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.models.timestamps import parse_instant


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


@dataclass(frozen=True)
class Contradiction:
    """`record` contradicts the fact `contradicts`, for the reason `reason`."""

    record: EvidenceRecord
    contradicts: EvidenceType
    reason: ContradictionRule | TemporalClash

    @property
    def record_id(self) -> str:
        return self.record.id

    @property
    def description(self) -> str:
        return self.reason.description


@dataclass(frozen=True)
class CrossCheck:
    """Everything the case's records say about each other, computed in one pass."""

    # Every contradiction, from any author, in record id order.
    contradictions: tuple[Contradiction, ...]
    # Trusted evidence dated after the dispute opened: (record, description).
    late_notes: tuple[tuple[EvidenceRecord, str], ...]


def cross_check(records: Iterable[EvidenceRecord], dispute: Dispute) -> CrossCheck:
    """Rule contradictions and date clashes for every record of the case, plus late notes.

    Rule contradictions come from any author; date clashes only from trusted, intact records.
    """
    in_case = case_records(records, dispute)
    dates = _case_dates(in_case, dispute)
    found: list[Contradiction] = []
    for record in in_case:
        found += [
            Contradiction(record, rule.contradicts, rule)
            for rule in contradiction_rules_matching(record)
        ]
        found += [
            Contradiction(record, fact, TemporalClash(fact, description))
            for rule, description in dates.clashes.get(record.id, ())
            for fact in sorted(rule.facts)
        ]
    return CrossCheck(contradictions=tuple(found), late_notes=dates.notes)


def contradictions(
    records: Iterable[EvidenceRecord],
    dispute: Dispute,
    also: frozenset[EvidenceType] = frozenset(),
) -> tuple[Contradiction, ...]:
    """Merchant or third-party records contradicting evidence the case's records establish.

    These are records that disagree with each other, so the engine refers the case.
    Customer allegations are not included: they make a normal dispute, not a conflict.
    `also`: facts relied on beyond the structured fields (a quote from a note), so a record
    contradicting a quoted fact is found too.
    """
    records = list(records)
    present = evidence_present(records, dispute) | also
    return tuple(
        c
        for c in cross_check(records, dispute).contradictions
        if c.contradicts in present and c.record.author in TRUSTED_AUTHORS
    )


def contradictory_record_ids(
    records: Iterable[EvidenceRecord], dispute: Dispute
) -> tuple[str, ...]:
    """The ids of the records in `contradictions()`, sorted and without repeats."""
    return tuple(sorted({c.record_id for c in contradictions(records, dispute)}))


def _instant(value: object) -> datetime | None:
    """The value as a UTC instant, or None if it is not a timestamp with a time zone."""
    try:
        return parse_instant(value)  # type: ignore[arg-type]
    except ValueError:
        return None


def _before(value: object, cutoff: datetime) -> bool:
    """True if `value` is a timestamp before `cutoff`. Undated or unparseable: True."""
    at = _instant(value)
    return True if at is None else at < cutoff


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


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


_Orders = list[tuple[EvidenceRecord, datetime]]


def _orders(in_case: list[EvidenceRecord]) -> _Orders:
    """The case's trusted, intact orders with a readable `placed_at`."""
    return [
        (r, t)
        for r in in_case
        if r.source_system is SourceSystem.ORDER
        and r.author in TRUSTED_AUTHORS
        and r.hash_ok()
        and (t := _instant(r.fields.get("placed_at"))) is not None
    ]


def _order_anchors(
    record: EvidenceRecord, rule: TemporalRule, orders: _Orders, dispute: Dispute
) -> dict[Anchor, datetime]:
    """The order dates `record` is compared with under `rule` (see TemporalRule)."""
    link = record.fields.get(rule.order_link) if rule.order_link else None
    linked = [
        t for r, t in orders if link not in (None, "") and r.fields.get(rule.order_link) == link
    ]
    disputed = [
        t
        for r, t in orders
        if dispute.merchant_order_id is not None
        and r.fields.get("order_id") == dispute.merchant_order_id
    ]
    placed = sorted(linked or disputed or [t for _, t in orders])
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


@dataclass(frozen=True)
class _CaseDates:
    clashes: dict[str, list[tuple[TemporalRule, str]]]
    notes: tuple[tuple[EvidenceRecord, str], ...]


def _case_dates(in_case: list[EvidenceRecord], dispute: Dispute) -> _CaseDates:
    """Date clashes per record id, and late-evidence notes. Tampered records are skipped."""
    orders = _orders(in_case)
    opened = _iso(dispute.opened_at)
    clashes: dict[str, list[tuple[TemporalRule, str]]] = {}
    notes: list[tuple[EvidenceRecord, str]] = []
    for record in in_case:
        if not record.hash_ok():
            continue
        for rule in _applicable(record):
            bounds = {
                **_order_anchors(record, rule, orders, dispute),
                Anchor.DISPUTE_OPENED: dispute.opened_at,
                Anchor.CAPTURED: record.captured_at,
            }
            raw = record.fields[rule.field]
            at = _instant(raw)
            if at is None:
                why = f"{rule.label} timestamp unreadable: {raw!r}"
            else:
                early = [a for a in rule.not_before if a in bounds and at < bounds[a]]
                late = [a for a in rule.not_after if a in bounds and at > bounds[a]]
                if early:
                    placed = _iso(bounds[early[0]])
                    why = f"{rule.label} {_iso(at)} predates the order (placed {placed})"
                elif late:
                    why = f"{rule.label} {_iso(at)} {_AFTER[late[0]]} ({_iso(bounds[late[0]])})"
                else:
                    if rule.note_if_late and at > dispute.opened_at:
                        description = (
                            f"{rule.label} {_iso(at)} is after the dispute opened ({opened})"
                        )
                        notes.append((record, description))
                    continue
            clashes.setdefault(record.id, []).append((rule, why))
    return _CaseDates(clashes=clashes, notes=tuple(notes))
