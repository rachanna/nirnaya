from collections.abc import Iterable

from nirnaya.engine.core import evaluate
from nirnaya.models.decision import VoIEntry
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType
from nirnaya.models.money import FxTable
from nirnaya.models.policy import MerchantPolicy


def value_of_information(
    dispute: Dispute,
    policy: MerchantPolicy,
    evidence_present: frozenset[EvidenceType],
    *,
    ownership_resolved: bool,
    contradictory_record_ids: Iterable[str] = (),
    fx: FxTable | None = None,
) -> tuple[VoIEntry, ...]:
    """Missing evidence types that, each found on its own, would flip the maths action.

    The agent investigates only these. Combinations are not explored: a pair that flips
    only together is not reported.
    """
    conflicts = tuple(contradictory_record_ids)
    kwargs = {
        "ownership_resolved": ownership_resolved,
        "contradictory_record_ids": conflicts,
        "fx": fx,
    }
    base = evaluate(dispute, policy, evidence_present, **kwargs)
    if base.math_action is None:
        return ()
    entries = []
    for evidence_type in sorted(set(EvidenceType) - evidence_present):
        alt = evaluate(dispute, policy, evidence_present | {evidence_type}, **kwargs)
        if alt.math_action is not base.math_action:
            entries.append(
                VoIEntry(
                    evidence_type=evidence_type,
                    p_if_present=alt.p,
                    margin_if_present=alt.margin,
                    math_action_if_present=alt.math_action,
                    final_action_if_present=alt.action,
                )
            )
    return tuple(entries)
