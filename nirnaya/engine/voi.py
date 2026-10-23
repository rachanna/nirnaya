from collections.abc import Iterable

from nirnaya.engine.core import evaluate
from nirnaya.models.decision import Decision, VoIEntry
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
    unanswered_contact_ids: Iterable[str] = (),
    fx: FxTable | None = None,
) -> tuple[VoIEntry, ...]:
    """The minimal sets of missing evidence that would flip the maths action.

    p is the highest prior among entries whose evidence is all present, and p is the only
    input evidence affects. So new evidence can change the decision only by completing some
    prior entry: testing each unsatisfied entry's missing set finds every possible flip,
    including evidence that flips only jointly. A single type is a set of one. Sets that
    contain a smaller flipping set are dropped. Order: fewest types, then the largest margin
    gain, then names. The agent investigates only these.
    """
    kwargs = {
        "ownership_resolved": ownership_resolved,
        "contradictory_record_ids": tuple(contradictory_record_ids),
        "unanswered_contact_ids": tuple(unanswered_contact_ids),
        "fx": fx,
    }
    present = frozenset(evidence_present)
    base = evaluate(dispute, policy, present, **kwargs)
    if base.math_action is None:
        return ()
    candidates = {
        entry.evidence - present
        for entry in policy.priors
        if entry.reason is dispute.reason_category and not entry.evidence <= present
    }
    flips: dict[frozenset[EvidenceType], Decision] = {}
    for missing in candidates:
        alt = evaluate(dispute, policy, present | missing, **kwargs)
        if alt.math_action is not base.math_action:
            flips[missing] = alt
    minimal = [s for s in flips if not any(other < s for other in flips)]
    minimal.sort(key=lambda s: (len(s), -(flips[s].margin - base.margin), sorted(s)))
    return tuple(
        VoIEntry(
            evidence=s,
            p_if_present=flips[s].p,
            margin_if_present=flips[s].margin,
            math_action_if_present=flips[s].math_action,
            final_action_if_present=flips[s].action,
        )
        for s in minimal
    )
