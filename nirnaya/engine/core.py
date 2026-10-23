"""Single-pass decision evaluation. Deterministic: no I/O, no clock, no randomness."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nirnaya.engine.formulas import (
    chargeback_gain,
    chargeback_margin,
    expected_chargeback_gain,
    math_action,
    pre_chargeback_margin,
)
from nirnaya.engine.priors import lookup_prior
from nirnaya.models.decision import Decision, ReferralReason
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import (
    ACTIONABLE_STATUS,
    AUTOMATED_STAGES,
    AcceptReason,
    Action,
    EvidenceType,
    ReasonCategory,
    Stage,
)
from nirnaya.models.money import FxTable, MissingFxRateError, Money, convert
from nirnaya.models.policy import MerchantPolicy

_REFERRAL_ORDER = {reason: i for i, reason in enumerate(ReferralReason)}

_REFERRAL_TEXT = {
    ReferralReason.STAGE_NOT_AUTOMATED: "Stage {stage} is not automated; a human decides.",
    ReferralReason.MISSING_FX_RATE: "No FX rate {currency}->{policy_currency}; maths not run.",
    ReferralReason.STATUS_NOT_ACTIONABLE: "Dispute status {status} does not allow an action.",
    ReferralReason.AMOUNT_ABOVE_THRESHOLD: (
        "Amount {amount_pc} exceeds the referral threshold {threshold} {policy_currency}."
    ),
    ReferralReason.MARGINAL: "Margin {margin} is inside the marginal band ±{band}.",
    ReferralReason.CONTRADICTORY_RECORDS: "Records contradict each other: {conflicts}.",
    ReferralReason.OWNERSHIP_UNRESOLVED: "Payment ownership or merchant liability is unresolved.",
    ReferralReason.UNANSWERED_CUSTOMER_CONTACT: (
        "The customer contacted support before the dispute and got no reply: {unanswered}."
    ),
    ReferralReason.UNKNOWN_REASON_CHALLENGE: (
        "Reason is unknown: the maths favours challenging, but unknown-reason disputes are "
        "never challenged automatically."
    ),
    ReferralReason.RFI_ACCEPT_NEEDS_APPROVAL: (
        "The maths favours accepting at RFI, which issues a full refund; a person approves that."
    ),
}


class TenantMismatchError(ValueError):
    """The policy belongs to a different merchant than the dispute."""


@dataclass(frozen=True)
class _Economics:
    g_cb: Decimal | None
    ev_challenge: Decimal
    ev_accept: Decimal
    margin: Decimal
    explanation: str


def _economics(stage: Stage, p: Decimal, A: Decimal, policy: MerchantPolicy) -> _Economics:
    """The stage formula for one win prior p. The maths says CHALLENGE iff margin > 0."""
    h, q, F_pa, m = (
        policy.handling_cost,
        policy.escalation_probability,
        policy.fee_pre_arbitration,
        policy.min_gain,
    )
    if stage is Stage.CHARGEBACK:
        gain = chargeback_gain(p=p, A=A, h=h, q=q, F_pa=F_pa)
        margin = chargeback_margin(p=p, A=A, h=h, q=q, F_pa=F_pa, m=m)
        return _Economics(
            None,
            gain,
            Decimal(0),
            margin,
            f"Chargeback: p*A - h - q*F_pa = {p}*{A} - {h} - {q}*{F_pa} = {gain}; "
            f"challenge iff that exceeds m={m}; margin {margin}.",
        )

    F_cb, R = policy.fee_chargeback, policy.counted_chargeback_cost
    g_cb = expected_chargeback_gain(p=p, A=A, h=h, q=q, F_pa=F_pa)
    if stage is Stage.PRE_CHARGEBACK:
        label, h_stage, F_accept = (
            "Pre-chargeback",
            policy.handling_cost_pre,
            policy.fee_pre_resolution,
        )
        names = "h_pre", "F_pre"
    elif stage is Stage.RFI:
        # Conservative: assumes the RFI response never settles the case and the issuer
        # escalates to a chargeback. Any chance it does settle only helps.
        label, h_stage, F_accept = "RFI", policy.handling_cost_rfi, policy.fee_rfi_accept
        names = "h_rfi", "F_rfi"
    else:
        raise ValueError(f"no formula for stage {stage}")
    margin = pre_chargeback_margin(g_cb=g_cb, h_pre=h_stage, F_cb=F_cb, R=R, F_pre=F_accept, m=m)
    return _Economics(
        g_cb,
        g_cb - h_stage - F_cb - R,
        -F_accept,
        margin,
        f"{label}: G_cb = max(0, {p}*{A} - {h} - {q}*{F_pa}) = {g_cb}; "
        f"challenge iff G_cb - {names[0]} ({g_cb} - {h_stage}) > F_cb + R - {names[1]} + m "
        f"({F_cb} + {R} - {F_accept} + {m}); margin {margin}.",
    )


def _s(value: Decimal | Money | None) -> str | None:
    return None if value is None else str(value)


def evaluate(
    dispute: Dispute,
    policy: MerchantPolicy,
    evidence_present: frozenset[EvidenceType],
    *,
    ownership_resolved: bool,
    contradictory_record_ids: Iterable[str] = (),
    unanswered_contact_ids: Iterable[str] = (),
    fx: FxTable | None = None,
    contradictions: Iterable[tuple[str, str, str]] = (),
) -> Decision:
    """Decide ACCEPT / CHALLENGE / REFER for one dispute, without value of information.

    `contradictions` (record id, fact, description) are recorded in the inputs so a referral can
    say why; the decision itself depends only on `contradictory_record_ids`.
    """
    if policy.merchant_id != dispute.merchant_id:
        raise TenantMismatchError(
            f"policy for {policy.merchant_id!r} used on dispute of {dispute.merchant_id!r}"
        )
    conflicts = sorted(set(contradictory_record_ids))
    unanswered = sorted(set(unanswered_contact_ids))
    evidence = frozenset(evidence_present)

    referrals: set[ReferralReason] = set()
    reasons: list[str] = []
    p: Decimal | None = None
    prior_key: frozenset[EvidenceType] | None = None
    amount_pc: Money | None = None
    rate: Decimal | None = None
    econ: _Economics | None = None
    maths: Action | None = None

    if dispute.status is not ACTIONABLE_STATUS:
        referrals.add(ReferralReason.STATUS_NOT_ACTIONABLE)
    if conflicts:
        referrals.add(ReferralReason.CONTRADICTORY_RECORDS)
    if not ownership_resolved:
        referrals.add(ReferralReason.OWNERSHIP_UNRESOLVED)
    if unanswered:
        referrals.add(ReferralReason.UNANSWERED_CUSTOMER_CONTACT)

    if dispute.stage not in AUTOMATED_STAGES:
        referrals.add(ReferralReason.STAGE_NOT_AUTOMATED)
    else:
        try:
            amount_pc, rate = convert(dispute.amount, policy.currency, fx)
        except MissingFxRateError:
            referrals.add(ReferralReason.MISSING_FX_RATE)

    if amount_pc is not None:
        A = amount_pc.amount
        p, prior_key = lookup_prior(policy, dispute.reason_category, evidence)
        if prior_key is None:
            reasons.append(
                f"Reason unknown: conservative prior p={p}; evidence is not used and "
                "no reason-specific claim may be made."
            )
        else:
            names = ", ".join(sorted(prior_key)) or "none"
            reasons.append(
                f"Win prior p={p} for {dispute.reason_category} with evidence [{names}]: "
                "a fixed prior, not a prediction."
            )
        econ = _economics(dispute.stage, p, A, policy)
        reasons.append(econ.explanation)

        maths = math_action(econ.margin)
        reasons.append(f"The maths says {maths}.")
        if A > policy.referral_amount_threshold:
            referrals.add(ReferralReason.AMOUNT_ABOVE_THRESHOLD)
        if abs(econ.margin) < policy.marginal_band:
            referrals.add(ReferralReason.MARGINAL)
        if dispute.reason_category is ReasonCategory.UNKNOWN and maths is Action.CHALLENGE:
            referrals.add(ReferralReason.UNKNOWN_REASON_CHALLENGE)
        if dispute.stage is Stage.RFI and maths is Action.ACCEPT:
            referrals.add(ReferralReason.RFI_ACCEPT_NEEDS_APPROVAL)

    ordered = tuple(sorted(referrals, key=_REFERRAL_ORDER.__getitem__))
    context = {
        "stage": dispute.stage,
        "status": dispute.status,
        "currency": dispute.amount.currency,
        "policy_currency": policy.currency,
        "amount_pc": amount_pc,
        "threshold": policy.referral_amount_threshold,
        "margin": None if econ is None else econ.margin,
        "band": policy.marginal_band,
        "conflicts": ", ".join(conflicts),
        "unanswered": ", ".join(unanswered),
    }
    reasons.extend(_REFERRAL_TEXT[r].format(**context) for r in ordered)
    action = Action.REFER if ordered else maths

    # Low value: even the highest p the policy allows (p + q <= 1) would not pay.
    p_max = Decimal(1) - policy.escalation_probability
    low_value: bool | None = None
    accept_reason: AcceptReason | None = None
    if action is Action.ACCEPT and amount_pc is not None:
        low_value = _economics(dispute.stage, p_max, amount_pc.amount, policy).margin <= 0
        if low_value:
            accept_reason = AcceptReason.LOW_VALUE_TRANSACTION
            reasons.append(
                f"Even at the highest allowed win prior p={p_max} challenging would not pay: "
                f"Airwallex accept reason {accept_reason}."
            )
        else:
            accept_reason = policy.accept_reason
            reasons.append(f"Airwallex accept reason: {accept_reason} (merchant policy).")

    inputs: dict[str, Any] = {
        "policy_merchant_id": policy.merchant_id,
        "policy_version": policy.version,
        "policy_currency": policy.currency,
        "stage": str(dispute.stage),
        "status": str(dispute.status),
        "reason_category": str(dispute.reason_category),
        "reason_type": dispute.reason.type,
        "reason_original_code": dispute.reason.original_code,
        "card_brand": None if dispute.card_brand is None else str(dispute.card_brand),
        "amount": str(dispute.amount),
        "amount_in_policy_currency": _s(amount_pc),
        "fx_rate": _s(rate),
        "fx_label": fx.label if fx is not None and rate not in (None, Decimal(1)) else None,
        "evidence_present": sorted(evidence),
        "ownership_resolved": ownership_resolved,
        "contradictory_record_ids": conflicts,
        "contradictions": [
            {"record_id": r, "contradicts": str(f), "description": d}
            for r, f, d in sorted(set(contradictions))
        ],
        "unanswered_contact_ids": unanswered,
        "p": _s(p),
        "prior_key": None if prior_key is None else sorted(prior_key),
        "p_max": str(p_max),
        "low_value": low_value,
        "F_pre": str(policy.fee_pre_resolution),
        "F_cb": str(policy.fee_chargeback),
        "F_pa": str(policy.fee_pre_arbitration),
        "F_rfi": str(policy.fee_rfi_accept),
        "h": str(policy.handling_cost),
        "h_pre": str(policy.handling_cost_pre),
        "h_rfi": str(policy.handling_cost_rfi),
        "R": str(policy.counted_chargeback_cost),
        "m": str(policy.min_gain),
        "q": str(policy.escalation_probability),
        "unknown_reason_prior": str(policy.unknown_reason_prior),
        "referral_amount_threshold": str(policy.referral_amount_threshold),
        "marginal_band": str(policy.marginal_band),
        "accept_reason": str(policy.accept_reason),
    }

    return Decision(
        case_id=dispute.id,
        merchant_id=dispute.merchant_id,
        stage=dispute.stage,
        reason_category=dispute.reason_category,
        action=action,
        accept_reason=accept_reason,
        math_action=maths,
        p=p,
        prior_key=prior_key,
        g_cb=None if econ is None else econ.g_cb,
        ev_challenge=None if econ is None else econ.ev_challenge,
        ev_accept=None if econ is None else econ.ev_accept,
        margin=None if econ is None else econ.margin,
        reasons=tuple(reasons),
        referral_reasons=ordered,
        inputs=inputs,
    )
