"""Per-merchant decision policy. Every number here is merchant configuration."""

from decimal import Decimal
from typing import Self

from pydantic import field_validator, model_validator

from nirnaya.models.base import (
    DomainModel,
    EvidenceSet,
    NonBlankStr,
    NonNegativeDec,
    Probability,
)
from nirnaya.models.enums import KNOWN_REASONS, ReasonCategory
from nirnaya.models.money import validate_currency


class PriorEntry(DomainModel):
    """Win prior p for a reason when at least this evidence is present.

    A fixed prior shown to the approver, never presented as a prediction.
    """

    reason: ReasonCategory
    evidence: EvidenceSet = frozenset()
    p: Probability


class MerchantPolicy(DomainModel):
    merchant_id: NonBlankStr
    version: NonBlankStr
    # All amounts below are in this currency.
    currency: str
    fee_pre_resolution: NonNegativeDec  # F_pre
    fee_chargeback: NonNegativeDec  # F_cb
    fee_pre_arbitration: NonNegativeDec  # F_pa
    handling_cost: NonNegativeDec  # h, chargeback-stage challenge
    handling_cost_pre: NonNegativeDec = Decimal(0)  # h_pre
    counted_chargeback_cost: NonNegativeDec  # R
    min_gain: NonNegativeDec  # m
    escalation_probability: Probability  # q
    priors: tuple[PriorEntry, ...]
    unknown_reason_prior: Probability
    referral_amount_threshold: NonNegativeDec
    marginal_band: NonNegativeDec
    assumptions_note: str = ""

    _check_currency = field_validator("currency")(validate_currency)

    @model_validator(mode="after")
    def _priors_well_formed(self) -> Self:
        keys = [(e.reason, e.evidence) for e in self.priors]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate prior entry for the same (reason, evidence set)")
        if any(e.reason is ReasonCategory.UNKNOWN for e in self.priors):
            raise ValueError("UNKNOWN reason uses unknown_reason_prior, not the priors table")
        with_base = {e.reason for e in self.priors if not e.evidence}
        missing = sorted(KNOWN_REASONS - with_base)
        if missing:
            raise ValueError(f"no base prior (empty evidence set) for {missing}")
        return self
