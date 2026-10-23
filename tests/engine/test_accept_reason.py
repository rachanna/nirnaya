"""Every ACCEPT carries the Airwallex accept reason; nothing else does."""

from decimal import Decimal as D

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models import AcceptReason, Action, EvidenceType, Stage
from tests.engine.helpers import run
from tests.factories import make_policy


def test_accept_carries_policy_default_others():
    d = run(DEMO_POLICY)
    assert d.action is Action.ACCEPT
    assert d.accept_reason is AcceptReason.OTHERS
    assert any("accept reason: OTHERS" in r for r in d.reasons)


def test_merchant_can_choose_another_accept_reason():
    policy = make_policy(accept_reason=AcceptReason.LOW_VALUE_TRANSACTION)
    assert run(policy).accept_reason is AcceptReason.LOW_VALUE_TRANSACTION


def test_challenge_has_no_accept_reason():
    d = run(DEMO_POLICY, frozenset({EvidenceType.DELIVERY_CONFIRMED}))
    assert d.action is Action.CHALLENGE
    assert d.accept_reason is None


def test_refer_has_no_accept_reason_even_when_maths_accepts():
    d = run(make_policy(referral_amount_threshold=D("100")))
    assert d.math_action is Action.ACCEPT
    assert d.action is Action.REFER
    assert d.accept_reason is None


def test_non_automated_stage_has_no_accept_reason():
    assert run(DEMO_POLICY, stage=Stage.PRE_ARBITRATION).accept_reason is None


def test_rfi_accept_is_referred_so_has_no_accept_reason():
    assert run(DEMO_POLICY, stage=Stage.RFI).accept_reason is None


def test_collaboration_reason_cannot_be_a_policy_default():
    with pytest.raises(ValueError, match="case-specific"):
        make_policy(accept_reason=AcceptReason.COLLABORATION_ACCEPTED_MANUAL)
