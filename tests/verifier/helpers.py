from typing import Any

from nirnaya.models import Claim, Dispute, EvidenceRecord, SourceSystem
from nirnaya.verifier import CheckName, VerificationResult, verify
from tests.factories import carrier_record, make_claim, make_dispute, make_record


def order_record(id: str = "order-1", **fields: Any) -> EvidenceRecord:
    base: dict[str, Any] = {
        "order_id": "ORD-1001",
        "placed_at": "2026-02-15T09:00:00+08:00",
        "total_amount": "200.00",
        "currency": "USD",
        "customer_name": "Mr John Smith",
    }
    base.update(fields)
    return make_record(SourceSystem.ORDER, base, id=id)


def delivery_claim(**overrides: Any) -> Claim:
    """A valid claim: carrier-1 status DELIVERED establishes delivery."""
    return make_claim(**overrides)


def run(
    claims: list[Claim],
    records: list[EvidenceRecord] | None = None,
    dispute: Dispute | None = None,
    **kwargs: Any,
) -> VerificationResult:
    return verify(
        dispute or make_dispute(),
        claims,
        [carrier_record()] if records is None else records,
        **kwargs,
    )


def checks(result: VerificationResult, claim_id: str) -> set[CheckName]:
    (claim_result,) = [c for c in result.claims if c.claim.id == claim_id]
    return {f.check for f in claim_result.failures}
