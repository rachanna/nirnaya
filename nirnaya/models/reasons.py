"""Airwallex dispute reason -> Nirnaya ReasonCategory.

The reason.type values are documented in the Payment Disputes API reference
(https://www.airwallex.com/docs/api/payments/payment_disputes/retrieve, checked 2026-10-23).

ASSUMPTION: the mapping itself is Nirnaya's expert policy, not Airwallex's. It is
deliberately conservative: only unambiguous types get a category, and everything else,
including any value Airwallex adds later, is UNKNOWN, which is never auto-challenged.
"""

from collections.abc import Mapping

from nirnaya.models.base import DomainModel, NonBlankStr
from nirnaya.models.enums import ReasonCategory

DOCUMENTED_REASON_TYPES = frozenset(
    {
        "CREDIT_NOT_PROCESSED",
        "FRAUDULENT",
        "DUPLICATE_CHARGE",
        "PRODUCT_NOT_RECEIVED",
        "PRODUCT_UNACCEPTABLE",
        "UNRECOGNIZED_CHARGE",
        "CANCELLED_PRODUCT",
        "MISREPRESENTATION",
        "COUNTERFEIT_PRODUCT",
        "PROCESSING_ERRORS",
        "AUTHORIZATION",
        "NOT_RECOGNIZED",
        "BANK_REJECTION",
        "FUND_REVERSAL",
        "CONSUMER_DISPUTE",
        "POINT_OF_INTERACTION_ERROR",
        "UNKNOWN",
    }
)

REASON_TYPE_CATEGORY: Mapping[str, ReasonCategory] = {
    "FRAUDULENT": ReasonCategory.FRAUD,
    "PRODUCT_NOT_RECEIVED": ReasonCategory.NON_RECEIPT,
    "DUPLICATE_CHARGE": ReasonCategory.DUPLICATE,
    # ASSUMPTION: covers cancelled recurring payments; may also include cancelled one-off
    # orders. The SUBSCRIPTION evidence requirements (accepted cancellation terms) apply.
    "CANCELLED_PRODUCT": ReasonCategory.SUBSCRIPTION,
    # Deliberately unmapped (-> UNKNOWN): UNRECOGNIZED_CHARGE and NOT_RECOGNIZED may be
    # fraud or a cardholder who forgot the purchase; the rest have no category yet.
}


def category_for(reason_type: str | None) -> ReasonCategory:
    if reason_type is None:
        return ReasonCategory.UNKNOWN
    return REASON_TYPE_CATEGORY.get(reason_type, ReasonCategory.UNKNOWN)


class DisputeReason(DomainModel):
    """Airwallex `reason`, kept verbatim. `type` is free text so a new value degrades to
    UNKNOWN instead of failing ingestion."""

    type: NonBlankStr | None = None
    original_code: str | None = None
    description: str | None = None

    @property
    def category(self) -> ReasonCategory:
        return category_for(self.type)
