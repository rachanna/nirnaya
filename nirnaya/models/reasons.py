"""Airwallex dispute reason -> Nirnaya ReasonCategory.

The reason.type values are documented in the Payment Disputes API reference
(https://www.airwallex.com/docs/api/payments/payment_disputes/retrieve, checked 2026-10-23).

ASSUMPTION: the mapping itself is Nirnaya's expert policy, not Airwallex's. A type gets a
category only when that category's evidence rules are the right defence for it. Everything
else, including any value Airwallex adds later, is UNKNOWN: never auto-challenged, and no
reason-specific claim may be made.
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
    # The cardholder does not recognise the charge: the reason is known, and the defence is
    # fraud-style evidence (3DS, AVS/CVC, device login, delivery). As UNKNOWN those claims
    # would be blocked as reason-specific, even on a human-approved challenge.
    "UNRECOGNIZED_CHARGE": ReasonCategory.FRAUD,
    "NOT_RECOGNIZED": ReasonCategory.FRAUD,
    "PRODUCT_NOT_RECEIVED": ReasonCategory.NON_RECEIPT,
    "DUPLICATE_CHARGE": ReasonCategory.DUPLICATE,
    # Cancelled recurring payments, and possibly cancelled one-off orders. The SUBSCRIPTION
    # requirements (accepted cancellation terms, plus usage after renewal or no contact before
    # the dispute) are the right defence for both. Recurring and one-off cannot be told apart
    # by original_code until Airwallex's code format is verified.
    "CANCELLED_PRODUCT": ReasonCategory.SUBSCRIPTION,
    # Deliberately unmapped (-> UNKNOWN): quality, misrepresentation, processing and credit
    # disputes have no category or evidence rules yet.
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
