"""RFI: challenge iff G_cb - h_rfi > F_cb + R - F_rfi + m; never accepted automatically.

Base case: p=0.5, A=200, h=20, q=0.1, F_pa=500 -> G_cb = 30. With F_cb=5, R=10, F_rfi=0,
h_rfi=0, m=10 the margin is 30 - (5 + 10 - 0 + 10) = +5.
"""

from decimal import Decimal as D

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models import Action, ReasonCategory, ReferralReason, Stage
from tests.engine.helpers import flat_policy, run
from tests.factories import make_policy, usd

RR = ReferralReason


def rfi_policy(**overrides):
    data = {
        "handling_cost": D("20"),
        "escalation_probability": D("0.1"),
        "fee_pre_arbitration": D("500"),
        "fee_chargeback": D("5"),
        "counted_chargeback_cost": D("10"),
        "fee_rfi_accept": D("0"),
        "handling_cost_rfi": D("0"),
        "min_gain": D("10"),
    }
    data.update(overrides)
    return flat_policy("0.5", **data)


def test_rfi_challenge_is_automated():
    d = run(rfi_policy(), stage=Stage.RFI)
    assert d.action is Action.CHALLENGE
    assert d.referral_reasons == ()
    assert d.g_cb == D("30")
    # ev_challenge = G_cb - h_rfi - F_cb - R; ev_accept = -F_rfi
    assert (d.ev_challenge, d.ev_accept, d.margin) == (D("15"), D("0"), D("5"))


@pytest.mark.parametrize(
    ("m", "expected_math"),
    [("14.99", Action.CHALLENGE), ("15", Action.ACCEPT), ("15.01", Action.ACCEPT)],
)
def test_rfi_flips_at_the_boundary(m, expected_math):
    d = run(rfi_policy(min_gain=D(m)), stage=Stage.RFI)
    assert d.math_action is expected_math


def test_rfi_accept_is_referred_for_approval():
    d = run(rfi_policy(min_gain=D("15")), stage=Stage.RFI)
    assert d.math_action is Action.ACCEPT
    assert d.action is Action.REFER
    assert d.referral_reasons == (RR.RFI_ACCEPT_NEEDS_APPROVAL,)
    assert d.accept_reason is None
    assert any("full refund" in r for r in d.reasons)


def test_rfi_handling_cost_moves_the_result():
    assert run(rfi_policy(handling_cost_rfi=D("4.99")), stage=Stage.RFI).math_action is (
        Action.CHALLENGE
    )
    assert run(rfi_policy(handling_cost_rfi=D("5")), stage=Stage.RFI).math_action is Action.ACCEPT


def test_rfi_accept_fee_moves_the_result():
    # A fee on accepting the RFI makes challenging relatively better.
    d = run(rfi_policy(min_gain=D("15"), fee_rfi_accept=D("0.01")), stage=Stage.RFI)
    assert d.math_action is Action.CHALLENGE


def test_rfi_does_not_use_pre_chargeback_costs():
    base = run(rfi_policy(), stage=Stage.RFI)
    other = run(
        rfi_policy(handling_cost_pre=D("100"), fee_pre_resolution=D("100")), stage=Stage.RFI
    )
    assert base.margin == other.margin


def test_rfi_inputs_are_recorded():
    d = run(rfi_policy(), stage=Stage.RFI)
    assert d.inputs["h_rfi"] == "0"
    assert d.inputs["F_rfi"] == "0"


def test_unknown_reason_rfi_challenge_is_still_referred():
    d = run(
        make_policy(unknown_reason_prior=D("0.9")),
        reason_category=ReasonCategory.UNKNOWN,
        stage=Stage.RFI,
        amount=usd("1000.00"),
    )
    assert d.math_action is Action.CHALLENGE
    assert d.action is Action.REFER
    assert RR.UNKNOWN_REASON_CHALLENGE in d.referral_reasons


@pytest.mark.parametrize("stage", [Stage.PRE_ARBITRATION, Stage.ARBITRATION])
def test_later_stages_stay_non_automated(stage):
    assert run(DEMO_POLICY, stage=stage).referral_reasons == (RR.STAGE_NOT_AUTOMATED,)
