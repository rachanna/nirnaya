"""Builders for test data. Defaults describe one non-receipt chargeback for the demo merchant."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models import (
    Claim,
    Dispute,
    DisputeStatus,
    EvidenceRecord,
    MerchantPolicy,
    Money,
    ReasonCategory,
    RecordAuthor,
    SourceSystem,
    Stage,
)

MERCHANT = DEMO_POLICY.merchant_id
OTHER_MERCHANT = "merchant-other"
CASE = "case-1"
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def usd(amount: str | int) -> Money:
    return Money(amount=D(str(amount)), currency="USD")


def make_dispute(**overrides: Any) -> Dispute:
    data: dict[str, Any] = {
        "id": CASE,
        "merchant_id": MERCHANT,
        "payment_attempt_id": "att-1",
        "stage": Stage.CHARGEBACK,
        "status": DisputeStatus.AWAITING_RESPONSE,
        "reason_category": ReasonCategory.NON_RECEIPT,
        "amount": usd("200.00"),
        "opened_at": T0,
        "due_at": T0 + timedelta(days=15),
    }
    data.update(overrides)
    return Dispute(**data)


def policy_kwargs(**overrides: Any) -> dict[str, Any]:
    data = DEMO_POLICY.model_dump()
    data.update(overrides)
    return data


def make_policy(**overrides: Any) -> MerchantPolicy:
    return MerchantPolicy(**policy_kwargs(**overrides))


def make_record(
    source_system: SourceSystem,
    fields: dict[str, Any],
    *,
    id: str = "rec-1",
    author: RecordAuthor = RecordAuthor.THIRD_PARTY,
    case_id: str = CASE,
    merchant_id: str = MERCHANT,
) -> EvidenceRecord:
    return EvidenceRecord.create(
        id=id,
        merchant_id=merchant_id,
        case_id=case_id,
        source_system=source_system,
        author=author,
        synthetic=True,
        fields=fields,
        captured_at=T0 + timedelta(days=1),
    )


def carrier_record(id: str = "carrier-1", **fields: Any) -> EvidenceRecord:
    base: dict[str, Any] = {
        "tracking_number": "1Z999AA10123456784",
        "status": "DELIVERED",
        "delivered_at": "2026-02-20T15:30:00-05:00",
        "recipient_name": "J. Smith",
    }
    base.update(fields)
    return make_record(SourceSystem.CARRIER, base, id=id)


def make_claim(**overrides: Any) -> Claim:
    from nirnaya.evidence.catalogue import render_claim_text

    data: dict[str, Any] = {
        "id": "claim-1",
        "case_id": CASE,
        "merchant_id": MERCHANT,
        "source_record_id": "carrier-1",
        "source_field": "status",
        "establishes": "DELIVERY_CONFIRMED",
        "extracted_value": "DELIVERED",
        "normalised_value": {"kind": "ENUM", "text": "DELIVERED"},
    }
    data.update(overrides)
    if "text" not in data:
        provisional = Claim(**data, text="pending")
        data["text"] = render_claim_text(provisional.establishes, provisional.normalised_value)
    return Claim(**data)
