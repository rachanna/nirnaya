"""Demo merchant policy and FX snapshot.

ASSUMPTION: every number in this module is a demo value, chosen to make the three demo
scenarios legible. None of it is real Airwallex pricing, network fee or win rate.
TODO(verify-airwallex-docs): replace F_pre, F_cb, F_pa with current Airwallex pricing.
"""

from datetime import UTC, datetime
from decimal import Decimal as D

from nirnaya.models import (
    EvidenceType,
    FxRate,
    FxTable,
    MerchantPolicy,
    PriorEntry,
    ReasonCategory,
)

E = EvidenceType
R = ReasonCategory

DEMO_PRIORS: tuple[PriorEntry, ...] = (
    PriorEntry(reason=R.NON_RECEIPT, p=D("0.10")),
    PriorEntry(reason=R.NON_RECEIPT, evidence=frozenset({E.DELIVERY_CONFIRMED}), p=D("0.60")),
    PriorEntry(
        reason=R.NON_RECEIPT,
        evidence=frozenset({E.DELIVERY_CONFIRMED, E.DELIVERY_SIGNATURE}),
        p=D("0.80"),
    ),
    PriorEntry(reason=R.FRAUD, p=D("0.10")),
    PriorEntry(reason=R.FRAUD, evidence=frozenset({E.THREE_DS_AUTHENTICATED}), p=D("0.70")),
    PriorEntry(reason=R.FRAUD, evidence=frozenset({E.AVS_MATCH, E.CVC_MATCH}), p=D("0.30")),
    PriorEntry(reason=R.FRAUD, evidence=frozenset({E.ACCOUNT_LOGIN_MATCH}), p=D("0.45")),
    PriorEntry(
        reason=R.FRAUD,
        evidence=frozenset({E.AVS_MATCH, E.CVC_MATCH, E.ACCOUNT_LOGIN_MATCH}),
        p=D("0.55"),
    ),
    PriorEntry(reason=R.DUPLICATE, p=D("0.10")),
    PriorEntry(reason=R.DUPLICATE, evidence=frozenset({E.DISTINCT_TRANSACTIONS}), p=D("0.65")),
    PriorEntry(reason=R.DUPLICATE, evidence=frozenset({E.REFUND_ISSUED}), p=D("0.85")),
    PriorEntry(reason=R.SUBSCRIPTION, p=D("0.10")),
    PriorEntry(
        reason=R.SUBSCRIPTION, evidence=frozenset({E.CANCELLATION_TERMS_ACCEPTED}), p=D("0.40")
    ),
    PriorEntry(
        reason=R.SUBSCRIPTION,
        evidence=frozenset({E.CANCELLATION_TERMS_ACCEPTED, E.USAGE_AFTER_RENEWAL}),
        p=D("0.60"),
    ),
)

DEMO_POLICY = MerchantPolicy(
    merchant_id="merchant-demo",
    version="demo-2026-10",
    currency="USD",
    fee_pre_resolution=D("15"),
    fee_chargeback=D("25"),
    fee_pre_arbitration=D("500"),
    handling_cost=D("20"),
    handling_cost_pre=D("0"),
    counted_chargeback_cost=D("50"),
    min_gain=D("10"),
    escalation_probability=D("0.10"),
    priors=DEMO_PRIORS,
    unknown_reason_prior=D("0.10"),
    referral_amount_threshold=D("2000"),
    marginal_band=D("5"),
    assumptions_note="ASSUMPTION: demo values only; not Airwallex pricing or real win rates.",
)

DEMO_FX = FxTable(
    rates=(
        FxRate(base="EUR", quote="USD", rate=D("1.10")),
        FxRate(base="JPY", quote="USD", rate=D("0.0067")),
        FxRate(base="USD", quote="JPY", rate=D("150")),
        FxRate(base="GBP", quote="USD", rate=D("1.27")),
    ),
    as_of=datetime(2026, 10, 1, tzinfo=UTC),
    label="SYNTHETIC/ASSUMPTION demo FX snapshot",
)
