from collections.abc import Iterable

from nirnaya.engine.core import evaluate
from nirnaya.engine.voi import value_of_information
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType
from nirnaya.models.money import FxTable
from nirnaya.models.policy import MerchantPolicy


def decide(
    dispute: Dispute,
    policy: MerchantPolicy,
    evidence_present: frozenset[EvidenceType],
    *,
    ownership_resolved: bool,
    contradictory_record_ids: Iterable[str] = (),
    unanswered_contact_ids: Iterable[str] = (),
    fx: FxTable | None = None,
) -> Decision:
    """The engine's decision, with the evidence worth searching for attached.

    This is the only place an action is authorised. Same inputs, same Decision.
    """
    conflicts = tuple(contradictory_record_ids)
    unanswered = tuple(unanswered_contact_ids)
    decision = evaluate(
        dispute,
        policy,
        evidence_present,
        ownership_resolved=ownership_resolved,
        contradictory_record_ids=conflicts,
        unanswered_contact_ids=unanswered,
        fx=fx,
    )
    voi = value_of_information(
        dispute,
        policy,
        evidence_present,
        ownership_resolved=ownership_resolved,
        contradictory_record_ids=conflicts,
        unanswered_contact_ids=unanswered,
        fx=fx,
    )
    return decision.model_copy(update={"voi": voi})
