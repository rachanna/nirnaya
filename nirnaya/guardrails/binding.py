"""What a human approval is bound to (architecture 7.2): the action, the money, the dispute,
the merchant, the evidence the approver saw, the referral reasons they accepted and any quotes
they confirmed. A confirmed quote's record is covered by the evidence hash: if it changes, the
approval is void."""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from nirnaya.evidence.catalogue import Quote
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action
from nirnaya.models.money import quantize_amount
from nirnaya.store.repository import Store


def evidence_hash(store: Store, merchant_id: str, case_id: str) -> str:
    """Hash of every captured record of the case (id and capture hash)."""
    pairs = sorted(store.capture_hashes(merchant_id, case_id).items())
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()


def binding(
    store: Store,
    dispute: Dispute,
    action: Action,
    referral_reasons: list[str],
    approver: str,
    confirmed_quotes: Sequence[Quote] = (),
) -> dict[str, Any]:
    bound: dict[str, Any] = {
        "action": action.value,
        "amount": str(quantize_amount(dispute.amount.amount, dispute.amount.currency)),
        "currency": dispute.amount.currency,
        "refunded": str(dispute.refunded),
        "dispute_id": dispute.id,
        "merchant_id": dispute.merchant_id,
        "evidence_hash": evidence_hash(store, dispute.merchant_id, dispute.id),
        "referral_reasons": sorted(referral_reasons),
        "approver": approver,
    }
    if confirmed_quotes:
        bound["confirmed_quotes"] = [
            {"record_id": q.record_id, "field": q.field, "fact": q.fact.value, "quote": q.quote}
            for q in confirmed_quotes
        ]
    return bound


def binding_still_holds(
    approval: dict[str, Any], store: Store, fresh: Dispute, action: Action
) -> str | None:
    """None if the approval still matches; otherwise what changed."""
    checks = {
        "action": action.value,
        "amount": str(quantize_amount(fresh.amount.amount, fresh.amount.currency)),
        "currency": fresh.amount.currency,
        "refunded": str(fresh.refunded),
        "dispute_id": fresh.id,
        "merchant_id": fresh.merchant_id,
        "evidence_hash": evidence_hash(store, fresh.merchant_id, fresh.id),
    }
    changed = sorted(k for k, v in checks.items() if approval.get(k) != v)
    return None if not changed else f"approval no longer matches: {', '.join(changed)}"
