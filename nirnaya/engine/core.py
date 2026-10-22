"""Single-pass decision evaluation. Deterministic: no I/O, no clock, no randomness."""

from collections.abc import Iterable
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
    AUTOMATED_STAGES,
    Action,
    DisputeStatus,
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
    ReferralReason.UNKNOWN_REASON_CHALLENGE: (
        "Reason is unknown: the maths favours challenging, but unknown-reason disputes are "
        "never challenged automatically."
    ),
}


class TenantMismatchError(ValueError):
    """The policy belongs to a different merchant than the dispute."""


def _s(value: Decimal | Money | None) -> str | None:
    return None if value is None else str(value)


def evaluate(
    dispute: Dispute,
    policy: MerchantPolicy,
    evidence_present: frozenset[EvidenceType],
    *,
    ownership_resolved: bool,
    contradictory_record_ids: Iterable[str] = (),
    fx: FxTable | None = None,
) -> Decision:
    """Decide ACCEPT / CHALLENGE / REFER for one dispute, without value of information."""
    if policy.merchant_id != dispute.merchant_id:
        raise TenantMismatchError(
            f"policy for {policy.merchant_id!r} used on dispute of {dispute.merchant_id!r}"
        )
    conflicts = sorted(set(contradictory_record_ids))
    evidence = frozenset(evidence_present)

    referrals: set[ReferralReason] = set()
    reasons: list[str] = []
    p: Decimal | None = None
    prior_key: frozenset[EvidenceType] | None = None
    amount_pc: Money | None = None
    rate: Decimal | None = None
    g_cb = ev_challenge = ev_accept = margin = None
    maths: Action | None = None

    if dispute.status is not DisputeStatus.AWAITING_RESPONSE:
        referrals.add(ReferralReason.STATUS_NOT_ACTIONABLE)
    if conflicts:
        referrals.add(ReferralReason.CONTRADICTORY_RECORDS)
    if not ownership_resolved:
        referrals.add(ReferralReason.OWNERSHIP_UNRESOLVED)

    if dispute.stage not in AUTOMATED_STAGES:
        referrals.add(ReferralReason.STAGE_NOT_AUTOMATED)
    else:
        try:
            amount_pc, rate = convert(dispute.amount, policy.currency, fx)
        except MissingFxRateError:
            referrals.add(ReferralReason.MISSING_FX_RATE)

    if amount_pc is not None:
        A = amount_pc.amount
        h, q, F_pa, m = (
            policy.handling_cost,
            policy.escalation_probability,
            policy.fee_pre_arbitration,
            policy.min_gain,
        )
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

        if dispute.stage is Stage.CHARGEBACK:
            ev_challenge = chargeback_gain(p=p, A=A, h=h, q=q, F_pa=F_pa)
            ev_accept = Decimal(0)
            margin = chargeback_margin(p=p, A=A, h=h, q=q, F_pa=F_pa, m=m)
            reasons.append(
                f"Chargeback: p*A - h - q*F_pa = {p}*{A} - {h} - {q}*{F_pa} = {ev_challenge}; "
                f"challenge iff that exceeds m={m}; margin {margin}."
            )
        else:
            h_pre, F_cb, R, F_pre = (
                policy.handling_cost_pre,
                policy.fee_chargeback,
                policy.counted_chargeback_cost,
                policy.fee_pre_resolution,
            )
            g_cb = expected_chargeback_gain(p=p, A=A, h=h, q=q, F_pa=F_pa)
            ev_challenge = g_cb - h_pre - F_cb - R
            ev_accept = -F_pre
            margin = pre_chargeback_margin(g_cb=g_cb, h_pre=h_pre, F_cb=F_cb, R=R, F_pre=F_pre, m=m)
            reasons.append(
                f"Pre-chargeback: G_cb = max(0, {p}*{A} - {h} - {q}*{F_pa}) = {g_cb}; "
                f"challenge iff G_cb - h_pre ({g_cb} - {h_pre}) > F_cb + R - F_pre + m "
                f"({F_cb} + {R} - {F_pre} + {m}); margin {margin}."
            )

        maths = math_action(margin)
        reasons.append(f"The maths says {maths}.")
        if A > policy.referral_amount_threshold:
            referrals.add(ReferralReason.AMOUNT_ABOVE_THRESHOLD)
        if abs(margin) < policy.marginal_band:
            referrals.add(ReferralReason.MARGINAL)
        if dispute.reason_category is ReasonCategory.UNKNOWN and maths is Action.CHALLENGE:
            referrals.add(ReferralReason.UNKNOWN_REASON_CHALLENGE)

    ordered = tuple(sorted(referrals, key=_REFERRAL_ORDER.__getitem__))
    context = {
        "stage": dispute.stage,
        "status": dispute.status,
        "currency": dispute.amount.currency,
        "policy_currency": policy.currency,
        "amount_pc": amount_pc,
        "threshold": policy.referral_amount_threshold,
        "margin": margin,
        "band": policy.marginal_band,
        "conflicts": ", ".join(conflicts),
    }
    reasons.extend(_REFERRAL_TEXT[r].format(**context) for r in ordered)
    action = Action.REFER if ordered else maths

    inputs: dict[str, Any] = {
        "policy_merchant_id": policy.merchant_id,
        "policy_version": policy.version,
        "policy_currency": policy.currency,
        "stage": str(dispute.stage),
        "status": str(dispute.status),
        "reason_category": str(dispute.reason_category),
        "amount": str(dispute.amount),
        "amount_in_policy_currency": _s(amount_pc),
        "fx_rate": _s(rate),
        "fx_label": fx.label if fx is not None and rate not in (None, Decimal(1)) else None,
        "evidence_present": sorted(evidence),
        "ownership_resolved": ownership_resolved,
        "contradictory_record_ids": conflicts,
        "p": _s(p),
        "prior_key": None if prior_key is None else sorted(prior_key),
        "F_pre": str(policy.fee_pre_resolution),
        "F_cb": str(policy.fee_chargeback),
        "F_pa": str(policy.fee_pre_arbitration),
        "h": str(policy.handling_cost),
        "h_pre": str(policy.handling_cost_pre),
        "R": str(policy.counted_chargeback_cost),
        "m": str(policy.min_gain),
        "q": str(policy.escalation_probability),
        "unknown_reason_prior": str(policy.unknown_reason_prior),
        "referral_amount_threshold": str(policy.referral_amount_threshold),
        "marginal_band": str(policy.marginal_band),
    }

    return Decision(
        case_id=dispute.id,
        merchant_id=dispute.merchant_id,
        stage=dispute.stage,
        reason_category=dispute.reason_category,
        action=action,
        math_action=maths,
        p=p,
        prior_key=prior_key,
        g_cb=g_cb,
        ev_challenge=ev_challenge,
        ev_accept=ev_accept,
        margin=margin,
        reasons=tuple(reasons),
        referral_reasons=ordered,
        inputs=inputs,
    )
