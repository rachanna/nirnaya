from decimal import Decimal as D
from typing import Any

from nirnaya.engine import decide
from nirnaya.models import (
    Decision,
    EvidenceType,
    MerchantPolicy,
    PriorEntry,
    ReasonCategory,
)
from nirnaya.models.enums import KNOWN_REASONS
from tests.factories import make_dispute, make_policy


def flat_policy(p: str, **overrides: Any) -> MerchantPolicy:
    """Every known reason has the same base prior p; no evidence-specific priors."""
    priors = tuple(PriorEntry(reason=r, p=D(p)) for r in sorted(KNOWN_REASONS))
    data: dict[str, Any] = {"priors": priors, "marginal_band": D(0)}
    data.update(overrides)
    return make_policy(**data)


def run(
    policy: MerchantPolicy,
    evidence: frozenset[EvidenceType] = frozenset(),
    *,
    ownership_resolved: bool = True,
    contradictory_record_ids: tuple[str, ...] = (),
    unanswered_contact_ids: tuple[str, ...] = (),
    fx: Any = None,
    **dispute_overrides: Any,
) -> Decision:
    dispute_overrides.setdefault("reason_category", ReasonCategory.NON_RECEIPT)
    return decide(
        make_dispute(**dispute_overrides),
        policy,
        evidence,
        ownership_resolved=ownership_resolved,
        contradictory_record_ids=contradictory_record_ids,
        unanswered_contact_ids=unanswered_contact_ids,
        fx=fx,
    )
