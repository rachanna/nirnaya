"""Reading free-text notes (architecture section 8.8): what the reader may be asked about, what
it may see, and what code keeps of what it proposes.

All of it is decided by code. The reader is asked only about facts that value of information
says could change the decision and that some note may support (TEXT_RULES); it sees only this
case's intact merchant and carrier records that hold such a note, never customer-written text;
and a proposal survives only if its quote is word for word in an allowed note. A surviving
quote is still only a proposal: a person confirms it before it counts for anything.
"""

from collections.abc import Iterable
from typing import Any

from nirnaya.evidence.catalogue import (
    TEXT_RULES,
    TRUSTED_AUTHORS,
    Quote,
    quote_in_record,
    render_claim_text,
    text_allows,
)
from nirnaya.evidence.derive import case_records
from nirnaya.models.claim import Claim, QuoteValue
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType
from nirnaya.models.evidence import EvidenceRecord

NOTE_FACTS = frozenset().union(*TEXT_RULES.values())


def readable_facts(decision: Decision, present: frozenset[EvidenceType]) -> frozenset[EvidenceType]:
    """Missing facts in a flipping set (VoI) that a note could support."""
    wanted = {t for v in decision.voi for t in v.evidence}
    return frozenset(wanted - present) & NOTE_FACTS


def note_fields(record: EvidenceRecord, facts: frozenset[EvidenceType]) -> list[str]:
    """The record's non-blank notes that may support one of these facts."""
    return sorted(
        name
        for (system, name), allowed in TEXT_RULES.items()
        if system is record.source_system
        and allowed & facts
        and isinstance(record.fields.get(name), str)
        and record.fields[name].strip()
    )


def readable_records(
    dispute: Dispute, records: Iterable[EvidenceRecord], facts: frozenset[EvidenceType]
) -> list[EvidenceRecord]:
    """This case's intact merchant and third-party records holding a note worth reading."""
    return [
        r
        for r in case_records(records, dispute)
        if r.hash_ok() and r.author in TRUSTED_AUTHORS and note_fields(r, facts)
    ]


def screen(
    proposals: Iterable[Quote], readable: list[EvidenceRecord], facts: frozenset[EvidenceType]
) -> tuple[list[Quote], list[dict[str, Any]]]:
    """Keep proposals whose quote is word for word in an allowed note of a record the reader
    was shown, for a fact it was asked about. Everything else is dropped, with why."""
    by_id = {r.id: r for r in readable}
    kept: list[Quote] = []
    dropped: list[dict[str, Any]] = []
    for p in proposals:
        quote = " ".join(str(p.quote).split())
        record = by_id.get(p.record_id)
        if record is None:
            why = "not a record the reader was shown"
        elif p.fact not in facts:
            why = "not a fact the reader was asked about"
        elif not text_allows(record, p.field, p.fact):
            why = "this note cannot establish this fact"
        elif not quote_in_record(record, p.field, quote):
            why = "the quote is not word for word in the note"
        else:
            q = Quote(p.record_id, p.field, p.fact, quote)
            if q not in kept:
                kept.append(q)
            continue
        dropped.append({"item": p._asdict(), "why": why})
    return kept, dropped


def quote_claim(dispute: Dispute, quote: Quote, n: int) -> Claim:
    """The claim a confirmed quote supports. Built by code, never by the model; the verifier
    still checks the quote word for word against the record (check_quote)."""
    value = QuoteValue(text=quote.quote)
    return Claim(
        id=f"quote-{n}",
        case_id=dispute.id,
        merchant_id=dispute.merchant_id,
        text=render_claim_text(quote.fact, value),
        source_record_id=quote.record_id,
        source_field=quote.field,
        establishes=quote.fact,
        extracted_value=quote.quote,
        normalised_value=value,
    )
