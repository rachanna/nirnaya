"""The two stage formulas, exactly as in CLAUDE.md, including every flip boundary.

Chargeback base case: p=0.5, A=200, h=20, q=0.1, F_pa=500 -> p*A - h - q*F_pa = 30.
"""

from decimal import Decimal as D

import pytest

from nirnaya.engine.formulas import (
    chargeback_gain,
    chargeback_margin,
    expected_chargeback_gain,
    math_action,
    pre_chargeback_margin,
)
from nirnaya.models import Action, Stage
from tests.engine.helpers import flat_policy, run
from tests.factories import usd

CB = {"p": D("0.5"), "A": D("200"), "h": D("20"), "q": D("0.1"), "F_pa": D("500")}


def test_chargeback_gain_is_p_a_minus_h_minus_q_fpa():
    assert chargeback_gain(**CB) == D("30")


@pytest.mark.parametrize(
    ("m", "expected"),
    [
        ("29.99", Action.CHALLENGE),
        ("30", Action.ACCEPT),  # strict ">": equality accepts
        ("30.01", Action.ACCEPT),
        ("0", Action.CHALLENGE),
    ],
)
def test_chargeback_flips_at_m(m, expected):
    assert math_action(chargeback_margin(**CB, m=D(m))) is expected


def test_g_cb_clamps_at_zero():
    assert expected_chargeback_gain(**{**CB, "p": D("0")}) == D("0")
    assert expected_chargeback_gain(**CB) == D("30")


# Pre-chargeback base case: G_cb=30, F_cb=5, R=10, F_pre=15 -> challenge iff 30 - h_pre > m.
PRE = {"g_cb": D("30"), "F_cb": D("5"), "R": D("10"), "F_pre": D("15")}


@pytest.mark.parametrize(
    ("m", "h_pre", "expected"),
    [
        ("29.99", "0", Action.CHALLENGE),
        ("30", "0", Action.ACCEPT),
        ("30.01", "0", Action.ACCEPT),
        ("10", "19.99", Action.CHALLENGE),
        ("10", "20", Action.ACCEPT),
    ],
)
def test_pre_chargeback_flips(m, h_pre, expected):
    margin = pre_chargeback_margin(**PRE, h_pre=D(h_pre), m=D(m))
    assert math_action(margin) is expected


def test_h_pre_zero_is_the_original_formula():
    # Original: challenge iff G_cb > F_cb + R - F_pre + m.
    for m in ("0", "10", "29.99", "30", "45"):
        original = PRE["g_cb"] - (PRE["F_cb"] + PRE["R"] - PRE["F_pre"] + D(m))
        assert pre_chargeback_margin(**PRE, h_pre=D(0), m=D(m)) == original


def test_negative_rhs_challenges_even_with_zero_g_cb():
    # The resolution fee exceeds the whole chargeback path cost: escalating is cheaper.
    margin = pre_chargeback_margin(
        g_cb=D("0"), F_cb=D("5"), R=D("10"), F_pre=D("90"), h_pre=D("0"), m=D("10")
    )
    assert margin == D("65")
    assert math_action(margin) is Action.CHALLENGE


# --- Through the engine -------------------------------------------------------------------


def cb_policy(**overrides):
    data = {
        "handling_cost": D("20"),
        "escalation_probability": D("0.1"),
        "fee_pre_arbitration": D("500"),
        "min_gain": D("10"),
    }
    data.update(overrides)
    return flat_policy("0.5", **data)


def test_chargeback_challenge():
    d = run(cb_policy())
    assert d.math_action is Action.CHALLENGE
    assert d.action is Action.CHALLENGE
    assert (d.ev_challenge, d.ev_accept, d.margin) == (D("30"), D("0"), D("20"))
    assert d.g_cb is None


def test_chargeback_equality_accepts():
    assert run(cb_policy(min_gain=D("30"))).action is Action.ACCEPT


@pytest.mark.parametrize(
    "override",
    [
        {"handling_cost": D("40.01")},
        {"escalation_probability": D("0.141")},
        {"fee_pre_arbitration": D("700.01")},
    ],
)
def test_raising_costs_flips_chargeback_to_accept(override):
    assert run(cb_policy()).action is Action.CHALLENGE
    assert run(cb_policy(**override)).action is Action.ACCEPT


def test_chargeback_flips_with_amount():
    # margin = 0.5*A - 80: A = 160 is the boundary.
    assert run(cb_policy(), amount=usd("160.00")).action is Action.ACCEPT
    assert run(cb_policy(), amount=usd("160.02")).action is Action.CHALLENGE


def pre_policy(**overrides):
    data = {
        "handling_cost": D("20"),
        "escalation_probability": D("0.1"),
        "fee_pre_arbitration": D("500"),
        "fee_chargeback": D("5"),
        "counted_chargeback_cost": D("10"),
        "fee_pre_resolution": D("15"),
        "min_gain": D("10"),
    }
    data.update(overrides)
    return flat_policy("0.5", **data)


def test_pre_chargeback_challenge():
    d = run(pre_policy(), stage=Stage.PRE_CHARGEBACK)
    assert d.action is Action.CHALLENGE
    assert d.g_cb == D("30")
    # ev_challenge = G_cb - h_pre - F_cb - R; ev_accept = -F_pre
    assert (d.ev_challenge, d.ev_accept, d.margin) == (D("15"), D("-15"), D("20"))


def test_pre_chargeback_equality_accepts():
    assert run(pre_policy(min_gain=D("30")), stage=Stage.PRE_CHARGEBACK).action is Action.ACCEPT


def test_pre_chargeback_h_pre_flips_to_accept():
    assert (
        run(pre_policy(handling_cost_pre=D("20")), stage=Stage.PRE_CHARGEBACK).action
        is Action.ACCEPT
    )


def test_pre_chargeback_g_cb_clamp_matters():
    # Unclamped: -70 + 65 = -5 (accept). Clamped: 0 + 65 = 65 (challenge).
    policy = pre_policy(fee_pre_resolution=D("90"))
    d = run(flat_policy("0", **policy.model_dump(exclude={"priors"})), stage=Stage.PRE_CHARGEBACK)
    assert d.g_cb == D("0")
    assert d.margin == D("65")
    assert d.action is Action.CHALLENGE


def test_pre_chargeback_counted_chargeback_cost_flips_to_accept():
    assert (
        run(pre_policy(counted_chargeback_cost=D("30")), stage=Stage.PRE_CHARGEBACK).action
        is Action.ACCEPT
    )
