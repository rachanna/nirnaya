"""Referral overrides the maths. Base case: chargeback, p=0.5, A=200 -> margin 20 (CHALLENGE)."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from nirnaya.config.demo import DEMO_FX
from nirnaya.models import (
    Action,
    DisputeStatus,
    FxTable,
    Money,
    ReasonCategory,
    ReferralReason,
    Stage,
)
from tests.engine.helpers import flat_policy, run
from tests.factories import make_dispute

RR = ReferralReason


def policy(**overrides):
    data = {
        "handling_cost": D("20"),
        "escalation_probability": D("0.1"),
        "fee_pre_arbitration": D("500"),
        "min_gain": D("10"),
        "referral_amount_threshold": D("1000"),
    }
    data.update(overrides)
    return flat_policy("0.5", **data)


def test_base_case_is_automated():
    d = run(policy())
    assert d.action is Action.CHALLENGE
    assert d.referral_reasons == ()


# --- Amount threshold: refer iff A > threshold ----------------------------------------------


@pytest.mark.parametrize(
    ("threshold", "referred"), [("200.00", False), ("199.99", True), ("200.01", False)]
)
def test_amount_threshold(threshold, referred):
    d = run(policy(referral_amount_threshold=D(threshold)))
    assert (RR.AMOUNT_ABOVE_THRESHOLD in d.referral_reasons) is referred
    assert d.action is (Action.REFER if referred else Action.CHALLENGE)


def test_amount_threshold_refers_an_accept_too():
    d = run(policy(min_gain=D("100"), referral_amount_threshold=D("100")))
    assert d.math_action is Action.ACCEPT
    assert d.action is Action.REFER


def test_amount_threshold_uses_policy_currency():
    # 200 EUR at 1.10 = 220 USD, above a 210 USD threshold.
    d = run(
        policy(referral_amount_threshold=D("210")),
        amount=Money(amount="200.00", currency="EUR"),
        fx=DEMO_FX,
    )
    assert RR.AMOUNT_ABOVE_THRESHOLD in d.referral_reasons
    assert d.inputs["amount_in_policy_currency"] == "220.00 USD"
    assert d.inputs["fx_rate"] == "1.10"


# --- Marginal band: refer iff |margin| < band --------------------------------------------------


@pytest.mark.parametrize(
    ("min_gain", "band", "referred"),
    [
        ("10", "20", False),  # margin +20, at the band edge: automated
        ("10", "20.01", True),  # margin +20, inside
        ("50", "20", False),  # margin -20, at the edge
        ("50", "20.01", True),  # margin -20, inside
        ("30", "0", False),  # margin 0, band 0 disables the rule
        ("30", "0.01", True),  # margin 0 inside a tiny band
    ],
)
def test_marginal_band(min_gain, band, referred):
    d = run(policy(min_gain=D(min_gain), marginal_band=D(band)))
    assert (RR.MARGINAL in d.referral_reasons) is referred


# --- Other triggers -------------------------------------------------------------------------


def test_contradictory_records_refer():
    d = run(policy(), contradictory_record_ids=("carrier-2",))
    assert d.referral_reasons == (RR.CONTRADICTORY_RECORDS,)
    assert d.action is Action.REFER
    assert d.inputs["contradictory_record_ids"] == ["carrier-2"]


def test_unresolved_ownership_refers():
    d = run(policy(), ownership_resolved=False)
    assert d.referral_reasons == (RR.OWNERSHIP_UNRESOLVED,)


@pytest.mark.parametrize("stage", [Stage.RFI, Stage.PRE_ARBITRATION, Stage.ARBITRATION])
def test_non_automated_stages_refer_without_maths(stage):
    d = run(policy(), stage=stage)
    assert d.action is Action.REFER
    assert d.referral_reasons == (RR.STAGE_NOT_AUTOMATED,)
    assert d.math_action is None
    assert (d.p, d.margin, d.ev_challenge, d.ev_accept) == (None, None, None, None)


@pytest.mark.parametrize("status", [DisputeStatus.RESPONDED, DisputeStatus.CLOSED])
def test_non_actionable_status_refers(status):
    d = run(policy(), status=status)
    assert d.referral_reasons == (RR.STATUS_NOT_ACTIONABLE,)
    assert d.math_action is Action.CHALLENGE


def test_missing_fx_rate_refers_without_maths():
    empty = FxTable(as_of=datetime(2026, 1, 1, tzinfo=UTC), label="empty")
    for fx in (None, empty):
        d = run(policy(), amount=Money(amount="200.00", currency="EUR"), fx=fx)
        assert d.referral_reasons == (RR.MISSING_FX_RATE,)
        assert d.math_action is None


def test_all_triggers_are_listed_in_a_fixed_order():
    d = run(
        policy(referral_amount_threshold=D("100"), marginal_band=D("50")),
        ownership_resolved=False,
        contradictory_record_ids=("b", "a"),
        status=DisputeStatus.RESPONDED,
    )
    assert d.referral_reasons == (
        RR.STATUS_NOT_ACTIONABLE,
        RR.AMOUNT_ABOVE_THRESHOLD,
        RR.MARGINAL,
        RR.CONTRADICTORY_RECORDS,
        RR.OWNERSHIP_UNRESOLVED,
    )
    assert d.math_action is Action.CHALLENGE  # the maths is preserved for the approver
    assert d.inputs["contradictory_record_ids"] == ["a", "b"]
    assert len(d.reasons) >= len(d.referral_reasons)


def test_policy_of_another_merchant_is_refused():
    from nirnaya.engine import decide
    from nirnaya.engine.core import TenantMismatchError

    with pytest.raises(TenantMismatchError):
        decide(
            make_dispute(merchant_id="merchant-other"),
            policy(),
            frozenset(),
            ownership_resolved=True,
        )


def test_inputs_snapshot_records_every_parameter():
    d = run(policy(), reason_category=ReasonCategory.NON_RECEIPT)
    for key in (
        "policy_version",
        "F_pre",
        "F_cb",
        "F_pa",
        "h",
        "h_pre",
        "R",
        "m",
        "q",
        "p",
        "prior_key",
        "evidence_present",
        "amount",
        "amount_in_policy_currency",
        "referral_amount_threshold",
        "marginal_band",
        "ownership_resolved",
        "stage",
        "status",
        "reason_category",
    ):
        assert key in d.inputs, key
    assert d.inputs["F_pa"] == "500"
