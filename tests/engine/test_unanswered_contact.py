"""A customer who contacted support before the dispute and got no reply: a person decides."""

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models import Action, EvidenceType, ReasonCategory, ReferralReason, Stage
from tests.engine.helpers import run

RR = ReferralReason


@pytest.mark.parametrize("reason", list(ReasonCategory))
def test_unanswered_contact_refers_every_reason(reason):
    d = run(DEMO_POLICY, reason_category=reason, unanswered_contact_ids=("msg-1",))
    assert d.action is Action.REFER
    assert RR.UNANSWERED_CUSTOMER_CONTACT in d.referral_reasons


def test_unanswered_contact_overrides_a_challenge():
    d = run(
        DEMO_POLICY,
        frozenset({EvidenceType.DELIVERY_CONFIRMED}),
        unanswered_contact_ids=("msg-1",),
    )
    assert d.math_action is Action.CHALLENGE
    assert d.referral_reasons == (RR.UNANSWERED_CUSTOMER_CONTACT,)


@pytest.mark.parametrize("stage", [Stage.RFI, Stage.PRE_CHARGEBACK, Stage.CHARGEBACK])
def test_unanswered_contact_refers_every_automated_stage(stage):
    d = run(DEMO_POLICY, stage=stage, unanswered_contact_ids=("msg-1",))
    assert RR.UNANSWERED_CUSTOMER_CONTACT in d.referral_reasons


def test_ids_are_recorded_sorted_and_deduplicated():
    d = run(DEMO_POLICY, unanswered_contact_ids=("msg-2", "msg-1", "msg-2"))
    assert d.inputs["unanswered_contact_ids"] == ["msg-1", "msg-2"]
    assert any("msg-1, msg-2" in r for r in d.reasons)


def test_no_unanswered_contact_no_referral():
    assert RR.UNANSWERED_CUSTOMER_CONTACT not in run(DEMO_POLICY).referral_reasons
