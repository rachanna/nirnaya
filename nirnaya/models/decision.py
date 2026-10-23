from enum import StrEnum
from typing import Any, Self

from pydantic import model_validator

from nirnaya.models.base import Dec, DomainModel, EvidenceSet, NonBlankStr
from nirnaya.models.enums import AcceptReason, Action, EvidenceType, ReasonCategory, Stage


class ReferralReason(StrEnum):
    STAGE_NOT_AUTOMATED = "STAGE_NOT_AUTOMATED"
    MISSING_FX_RATE = "MISSING_FX_RATE"
    STATUS_NOT_ACTIONABLE = "STATUS_NOT_ACTIONABLE"
    AMOUNT_ABOVE_THRESHOLD = "AMOUNT_ABOVE_THRESHOLD"
    MARGINAL = "MARGINAL"
    CONTRADICTORY_RECORDS = "CONTRADICTORY_RECORDS"
    OWNERSHIP_UNRESOLVED = "OWNERSHIP_UNRESOLVED"
    UNANSWERED_CUSTOMER_CONTACT = "UNANSWERED_CUSTOMER_CONTACT"
    UNKNOWN_REASON_CHALLENGE = "UNKNOWN_REASON_CHALLENGE"
    RFI_ACCEPT_NEEDS_APPROVAL = "RFI_ACCEPT_NEEDS_APPROVAL"


class VoIEntry(DomainModel):
    """A missing evidence type that, if found, would flip the maths action."""

    evidence_type: EvidenceType
    p_if_present: Dec
    margin_if_present: Dec
    math_action_if_present: Action
    final_action_if_present: Action


class Decision(DomainModel):
    """The engine's output.

    Expected values are measured against losing the disputed amount, which is common to
    both branches: at chargeback, accepting is 0 and challenging is p*A - h - q*F_pa; at
    pre-chargeback, accepting is -F_pre and challenging is G_cb - h_pre - F_cb - R.
    margin = ev_challenge - ev_accept - m; the maths says CHALLENGE iff margin > 0.
    """

    case_id: NonBlankStr
    merchant_id: NonBlankStr
    stage: Stage
    reason_category: ReasonCategory
    action: Action
    # The Airwallex accept reason to send; set exactly when action is ACCEPT.
    accept_reason: AcceptReason | None = None
    # None when no maths was run (stage not automated, or no FX rate).
    math_action: Action | None
    p: Dec | None
    # Evidence set of the prior entry that supplied p; None for the unknown-reason prior.
    prior_key: EvidenceSet | None
    g_cb: Dec | None
    ev_challenge: Dec | None
    ev_accept: Dec | None
    margin: Dec | None
    reasons: tuple[str, ...]
    referral_reasons: tuple[ReferralReason, ...]
    inputs: dict[str, Any]
    voi: tuple[VoIEntry, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.math_action is Action.REFER:
            raise ValueError("math_action is ACCEPT or CHALLENGE, never REFER")
        if self.referral_reasons and self.action is not Action.REFER:
            raise ValueError("a decision with referral reasons must REFER")
        if not self.referral_reasons and self.action is not self.math_action:
            raise ValueError("without referral reasons the action is the maths action")
        if self.action is Action.CHALLENGE and self.reason_category is ReasonCategory.UNKNOWN:
            raise ValueError("an unknown-reason dispute is never auto-challenged")
        if (self.accept_reason is not None) != (self.action is Action.ACCEPT):
            raise ValueError("accept_reason is set exactly when the action is ACCEPT")
        return self
