"""When Airwallex acts on its own, Nirnaya records the case and does nothing (architecture 4.3)."""

from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import CardBrand, DisputeStatus, Stage
from nirnaya.models.money import Money

TERMINAL_STATUSES = frozenset(
    {
        DisputeStatus.ACCEPTED,
        DisputeStatus.REVERSED,
        DisputeStatus.WON,
        DisputeStatus.LOST,
        DisputeStatus.EXPIRED,
    }
)


def closed_reason(dispute: Dispute) -> str | None:
    """The dispute is already resolved; there is nothing to decide."""
    if dispute.status in TERMINAL_STATUSES:
        return f"dispute already {dispute.status}"
    return None


def deferral_reason(dispute: Dispute, auto_accept_threshold: Money | None) -> str | None:
    """Why Airwallex, not Nirnaya, will act on this dispute; None if Nirnaya should."""
    if dispute.refunded == dispute.amount and dispute.stage is not Stage.ARBITRATION:
        return "fully refunded: Airwallex auto-defends"
    if dispute.stage is Stage.ARBITRATION:
        return "arbitration: no merchant action"
    if (
        dispute.stage is Stage.PRE_ARBITRATION
        and dispute.card_brand is not CardBrand.AMERICAN_EXPRESS
    ):
        return "pre-arbitration: Airwallex reviews it"
    if (
        dispute.stage is Stage.PRE_CHARGEBACK
        and auto_accept_threshold is not None
        # Only compared in the same currency; otherwise Nirnaya decides as normal.
        and auto_accept_threshold.currency == dispute.amount.currency
        and dispute.amount.amount < auto_accept_threshold.amount
    ):
        return "pre-chargeback below the auto-accept threshold: Airwallex accepts it"
    return None
