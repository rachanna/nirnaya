"""What the model is shown about a case.

Customer-authored records are shown as metadata only: their content is withheld. The model
never needs it (customer records establish nothing; the engine uses only their existence),
and withholding it removes the main prompt-injection path rather than merely labelling it.
Everything else is passed inside untrusted() data blocks, never in a system prompt.
"""

from typing import Any

from nirnaya.llm.untrusted import untrusted
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import RecordAuthor
from nirnaya.models.evidence import EvidenceRecord

WITHHELD = "withheld: customer-written; it establishes nothing and is not needed"


def case_facts(dispute: Dispute) -> dict[str, Any]:
    """Structured facts only. The reason's free-text description is not included."""
    return {
        "dispute_id": dispute.id,
        "stage": str(dispute.stage),
        "reason_category": str(dispute.reason_category),
        "reason_type": dispute.reason.type,
        "amount": str(dispute.amount),
        "card_brand": None if dispute.card_brand is None else str(dispute.card_brand),
        "due_at": dispute.due_at.isoformat(),
    }


def record_view(record: EvidenceRecord) -> dict[str, Any]:
    if record.author is RecordAuthor.CUSTOMER:
        return {
            "id": record.id,
            "system": str(record.source_system),
            "author": str(record.author),
            "content": WITHHELD,
        }
    return {
        "id": record.id,
        "system": str(record.source_system),
        "author": str(record.author),
        "fields": record.fields,
    }


def records_block(records: list[EvidenceRecord], *, source: str) -> str:
    return untrusted([record_view(r) for r in records], source=source)


def facts_block(dispute: Dispute) -> str:
    return untrusted(case_facts(dispute), source="dispute")
