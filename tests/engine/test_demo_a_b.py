"""Demo scenarios A and B from the spec, end to end through records -> evidence -> engine."""

from decimal import Decimal as D

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evidence.derive import contradictory_record_ids, evidence_present
from nirnaya.models import (
    Action,
    AvsResult,
    CvcResult,
    EvidenceType,
    PaymentAttempt,
    ReasonCategory,
    Stage,
    ThreeDSResult,
)
from tests.factories import CASE, MERCHANT, T0, carrier_record, make_dispute, usd


def decide_from_records(dispute, records):
    return decide(
        dispute,
        DEMO_POLICY,
        evidence_present(records, dispute),
        ownership_resolved=True,
        contradictory_record_ids=contradictory_record_ids(records, dispute),
    )


def test_demo_a_delivery_scan_flips_accept_to_challenge():
    dispute = make_dispute(reason_category=ReasonCategory.NON_RECEIPT, stage=Stage.CHARGEBACK)

    before = decide_from_records(dispute, [])
    assert before.action is Action.ACCEPT
    assert [v.evidence_type for v in before.voi] == [EvidenceType.DELIVERY_CONFIRMED]

    # The agent searches only for what VoI reported, and finds the carrier scan.
    after = decide_from_records(dispute, [carrier_record()])
    assert after.action is Action.CHALLENGE
    assert after.p == D("0.60")
    assert after.prior_key == {EvidenceType.DELIVERY_CONFIRMED}


def test_demo_a_in_transit_scan_does_not_flip():
    dispute = make_dispute()
    assert decide_from_records(dispute, [carrier_record(status="IN_TRANSIT")]).action is (
        Action.ACCEPT
    )


def test_demo_a_contradicting_scan_refers_instead():
    dispute = make_dispute()
    records = [carrier_record(), carrier_record("carrier-2", status="RETURNED_TO_SENDER")]
    d = decide_from_records(dispute, records)
    assert d.math_action is Action.CHALLENGE
    assert d.action is Action.REFER


def test_demo_b_weak_pre_chargeback_is_accepted_early():
    attempt = PaymentAttempt(
        id="att-1",
        merchant_id=MERCHANT,
        payment_intent_id="int-1",
        amount=usd("150.00"),
        three_ds=ThreeDSResult.NOT_PERFORMED,
        avs=AvsResult.MATCH,
        cvc=CvcResult.MATCH,
        created_at=T0,
    )
    dispute = make_dispute(
        reason_category=ReasonCategory.FRAUD,
        stage=Stage.PRE_CHARGEBACK,
        amount=usd("150.00"),
    )
    records = [attempt.to_evidence_record(case_id=CASE, captured_at=T0, synthetic=True)]
    d = decide_from_records(dispute, records)

    # p=0.30 (AVS+CVC): G_cb = max(0, 45 - 20 - 50) = 0; margin = 0 - (25+50-15+10) = -70
    assert d.p == D("0.30")
    assert d.g_cb == D("0")
    assert d.margin == D("-70")
    assert d.action is Action.ACCEPT
    assert d.referral_reasons == ()
    assert any("pre-chargeback" in r.lower() for r in d.reasons)
