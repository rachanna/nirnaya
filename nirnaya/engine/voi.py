from collections.abc import Iterable

from nirnaya.engine.core import evaluate
from nirnaya.models.decision import Decision, StrengthEntry, VoIEntry
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType, ReasonCategory
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


def strengthening_sets(
    dispute: Dispute,
    policy: MerchantPolicy,
    evidence_present: frozenset[EvidenceType],
    *,
    ownership_resolved: bool,
    contradictory_record_ids: Iterable[str] = (),
    unanswered_contact_ids: Iterable[str] = (),
    fx: FxTable | None = None,
) -> tuple[StrengthEntry, ...]:
    """The sets of missing evidence that would raise p: each unsatisfied prior entry with a
    higher p than the current one, as its missing part. A set is dropped when a smaller one
    already raises p as far. Order: fewest types, then the largest gain in p, then names.

    Value of information says what could change the decision; this says what would make a
    challenge stronger. Code fetches it once the case is worth fighting, never the agent.
    Never for an UNKNOWN reason (no reason-specific evidence) or a non-automated stage.
    """
    if dispute.reason_category is ReasonCategory.UNKNOWN:
        return ()
    kwargs = {
        "ownership_resolved": ownership_resolved,
        "contradictory_record_ids": tuple(contradictory_record_ids),
        "unanswered_contact_ids": tuple(unanswered_contact_ids),
        "fx": fx,
    }
    present = frozenset(evidence_present)
    base = evaluate(dispute, policy, present, **kwargs)
    if base.math_action is None or base.p is None:
        return ()
    found: dict[frozenset[EvidenceType], Decision] = {}
    for entry in policy.priors:
        if entry.reason is dispute.reason_category and entry.p > base.p:
            missing = entry.evidence - present
            if missing:
                found[missing] = evaluate(dispute, policy, present | missing, **kwargs)
    keep = [
        s for s in found if not any(other < s and found[other].p >= found[s].p for other in found)
    ]
    keep.sort(key=lambda s: (len(s), -(found[s].p - base.p), sorted(s)))
    return tuple(
        StrengthEntry(evidence=s, p_if_present=found[s].p, margin_if_present=found[s].margin)
        for s in keep
    )
