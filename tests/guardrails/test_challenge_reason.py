"""The challenge reason sent to Airwallex follows the verified facts (architecture 9.5)."""

import pytest

from nirnaya.guardrails.challenge_reason import challenge_reason
from nirnaya.models import ChallengeReason, EvidenceType, ReasonCategory

E, R, C = EvidenceType, ReasonCategory, ChallengeReason


@pytest.mark.parametrize(
    "category,facts,expected",
    [
        (R.NON_RECEIPT, {E.DELIVERY_CONFIRMED}, C.PRODUCT_RECEIVED),
        (R.DUPLICATE, {E.DISTINCT_TRANSACTIONS}, C.SEPARATE_PRODUCT),
        (R.FRAUD, {E.PRIOR_UNDISPUTED_ORDERS_MATCH}, C.PURCHASE_HISTORY),
        # Kit case 1: fraud with a signed delivery is still argued on purchase history.
        (R.FRAUD, {E.PRIOR_UNDISPUTED_ORDERS_MATCH, E.DELIVERY_CONFIRMED}, C.PURCHASE_HISTORY),
        (R.FRAUD, {E.DELIVERY_CONFIRMED}, C.OTHER_REASONS),
        (R.DUPLICATE, {E.REFUND_ISSUED, E.DISTINCT_TRANSACTIONS}, C.CUSTOMER_REFUNDED),
        (R.SUBSCRIPTION, {E.CANCELLATION_TERMS_ACCEPTED}, C.OTHER_REASONS),
        (R.FRAUD, set(), C.OTHER_REASONS),
    ],
)
def test_reason_table(category, facts, expected):
    assert challenge_reason(category, frozenset(facts)) is expected
