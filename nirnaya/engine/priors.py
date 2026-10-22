from decimal import Decimal

from nirnaya.models.enums import EvidenceType, ReasonCategory
from nirnaya.models.policy import MerchantPolicy, PriorEntry


def _rank(entry: PriorEntry) -> tuple[Decimal, int, tuple[str, ...]]:
    # Highest p wins; ties go to the larger set, then to sorted names, so the choice never
    # depends on set iteration order.
    return entry.p, len(entry.evidence), tuple(sorted(entry.evidence))


def lookup_prior(
    policy: MerchantPolicy, reason: ReasonCategory, evidence: frozenset[EvidenceType]
) -> tuple[Decimal, frozenset[EvidenceType] | None]:
    """Win prior p and the evidence set of the entry that supplied it.

    p is the maximum over entries for this reason whose evidence set is a subset of the
    evidence present, so finding more evidence can never lower p. An UNKNOWN reason uses
    the conservative unknown_reason_prior and ignores evidence (key None).
    """
    if reason is ReasonCategory.UNKNOWN:
        return policy.unknown_reason_prior, None
    candidates = [e for e in policy.priors if e.reason is reason and e.evidence <= evidence]
    # Always non-empty: the policy validator requires a base (empty-set) entry per reason.
    best = max(candidates, key=_rank)
    return best.p, best.evidence
