"""LOW_VALUE_TRANSACTION: an accept where even the highest allowed p (1 - q) would not pay."""

from decimal import Decimal as D

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models import AcceptReason, Action, EvidenceType, ReasonCategory, Stage
from tests.engine.helpers import flat_policy, run
from tests.factories import make_policy, usd


def test_small_claim_is_accepted_as_low_value():
    # 15 USD, delivered: p=0.60 -> 9 - 20 - 50 - 10 = -71. At p_max=0.9: 13.5 - 80 < 0.
    d = run(DEMO_POLICY, frozenset({EvidenceType.DELIVERY_CONFIRMED}), amount=usd("15.00"))
    assert d.action is Action.ACCEPT
    assert d.accept_reason is AcceptReason.LOW_VALUE_TRANSACTION
    assert d.inputs["low_value"] is True
    assert d.inputs["p_max"] == "0.90"
    assert any("LOW_VALUE_TRANSACTION" in r for r in d.reasons)


def test_weak_but_valuable_claim_uses_the_policy_reason():
    # 200 USD with no evidence: accepted at p=0.10, but p_max=0.9 would pay (180 - 80 > 0).
    d = run(DEMO_POLICY)
    assert d.action is Action.ACCEPT
    assert d.accept_reason is AcceptReason.OTHERS
    assert d.inputs["low_value"] is False


def test_low_value_boundary():
    # p_max = 1 - 0.5 = 0.5; margin at p_max = 0.5*A - 20 - 50 - 10 = 0 at A = 160.
    policy = flat_policy(
        "0.1",
        escalation_probability=D("0.5"),
        fee_pre_arbitration=D("100"),
        handling_cost=D("20"),
        min_gain=D("10"),
    )
    at = run(policy, amount=usd("160.00"))
    above = run(policy, amount=usd("160.02"))
    assert (at.action, above.action) == (Action.ACCEPT, Action.ACCEPT)
    assert at.accept_reason is AcceptReason.LOW_VALUE_TRANSACTION
    assert above.accept_reason is AcceptReason.OTHERS


def test_low_value_applies_at_pre_chargeback():
    # G_cb(p_max) = max(0, 13.5 - 70) = 0; 0 - (25 + 50 - 15 + 10) < 0.
    d = run(DEMO_POLICY, amount=usd("15.00"), stage=Stage.PRE_CHARGEBACK)
    assert d.accept_reason is AcceptReason.LOW_VALUE_TRANSACTION


def test_low_value_applies_to_unknown_reasons():
    d = run(DEMO_POLICY, amount=usd("15.00"), reason_category=ReasonCategory.UNKNOWN)
    assert d.accept_reason is AcceptReason.LOW_VALUE_TRANSACTION


def test_low_value_overrides_the_policy_default():
    policy = make_policy(accept_reason=AcceptReason.VALID_CUSTOMER_DISPUTE)
    assert run(policy, amount=usd("15.00")).accept_reason is AcceptReason.LOW_VALUE_TRANSACTION
    assert run(policy).accept_reason is AcceptReason.VALID_CUSTOMER_DISPUTE


def test_challenge_and_refer_never_carry_an_accept_reason():
    challenge = run(DEMO_POLICY, frozenset({EvidenceType.DELIVERY_CONFIRMED}))
    refer = run(make_policy(referral_amount_threshold=D("10")), amount=usd("15.00"))
    assert challenge.accept_reason is None
    assert refer.action is Action.REFER
    assert refer.accept_reason is None
