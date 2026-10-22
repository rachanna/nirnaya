from datetime import datetime
from typing import Any

from nirnaya.models.base import DomainModel, NonBlankStr, UtcDatetime
from nirnaya.models.enums import AvsResult, CvcResult, RecordAuthor, SourceSystem, ThreeDSResult
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.models.money import Money


class PaymentAttempt(DomainModel):
    id: NonBlankStr
    merchant_id: NonBlankStr
    payment_intent_id: NonBlankStr
    amount: Money
    three_ds: ThreeDSResult
    avs: AvsResult
    cvc: CvcResult
    created_at: UtcDatetime
    # Unparsed source payload, kept for audit.
    raw: dict[str, Any] = {}

    def to_evidence_record(
        self, *, case_id: str, captured_at: datetime, synthetic: bool
    ) -> EvidenceRecord:
        """Snapshot this attempt as evidence, so claims about 3DS/AVS/CVC can cite it."""
        return EvidenceRecord.create(
            id=f"payment_attempt:{self.id}",
            merchant_id=self.merchant_id,
            case_id=case_id,
            source_system=SourceSystem.PAYMENT_ATTEMPT,
            author=RecordAuthor.THIRD_PARTY,
            synthetic=synthetic,
            fields={
                "payment_attempt_id": self.id,
                "payment_intent_id": self.payment_intent_id,
                "amount": str(self.amount.amount),
                "currency": self.amount.currency,
                "three_ds": self.three_ds.value,
                "avs": self.avs.value,
                "cvc": self.cvc.value,
                "created_at": self.created_at.isoformat(),
            },
            captured_at=captured_at,
        )
