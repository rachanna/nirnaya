"""Which Airwallex challenge reason to send, from the facts the verifier established.

ASSUMPTION: Nirnaya's mapping (architecture section 9.5), not an Airwallex rule. Deterministic,
and chosen only from VERIFIED facts, so the reason never claims more than the evidence shows.
"""

from nirnaya.models.base import EvidenceSet
from nirnaya.models.enums import ChallengeReason, EvidenceType, ReasonCategory

E = EvidenceType

# (dispute category or None for any, fact) -> reason, in priority order.
_TABLE: tuple[tuple[ReasonCategory | None, EvidenceType, ChallengeReason], ...] = (
    (None, E.REFUND_ISSUED, ChallengeReason.CUSTOMER_REFUNDED),
    (ReasonCategory.NON_RECEIPT, E.DELIVERY_CONFIRMED, ChallengeReason.PRODUCT_RECEIVED),
    (ReasonCategory.DUPLICATE, E.DISTINCT_TRANSACTIONS, ChallengeReason.SEPARATE_PRODUCT),
    (ReasonCategory.FRAUD, E.PRIOR_UNDISPUTED_ORDERS_MATCH, ChallengeReason.PURCHASE_HISTORY),
)


def challenge_reason(category: ReasonCategory, established: EvidenceSet) -> ChallengeReason:
    for wanted, fact, reason in _TABLE:
        if (wanted is None or wanted is category) and fact in established:
            return reason
    return ChallengeReason.OTHER_REASONS
