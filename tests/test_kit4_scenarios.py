"""Hackathon kit 4 (Dispute Response Agent) cases, end to end through the deterministic core.

records -> evidence_present / contradictory_record_ids / unanswered_customer_contacts
-> decide -> verify. Demo policy; all amounts USD. Visa codes are the kit's: 10.4, 13.1, 13.6.
"""

from datetime import timedelta
from decimal import Decimal as D

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evidence.derive import (
    contradictory_record_ids,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.models import (
    AcceptReason,
    Action,
    AvsResult,
    CvcResult,
    DisputeReason,
    EvidenceType,
    PaymentAttempt,
    RecordAuthor,
    ReferralReason,
    SourceSystem,
    Stage,
    ThreeDSResult,
)
from nirnaya.verifier import CheckName, VerificationOutcome, verify
from tests.factories import (
    CASE,
    MERCHANT,
    T0,
    carrier_record,
    make_claim,
    make_dispute,
    make_record,
    usd,
)

E = EvidenceType


def run(dispute, records):
    return decide(
        dispute,
        DEMO_POLICY,
        evidence_present(records, dispute),
        ownership_resolved=True,
        contradictory_record_ids=contradictory_record_ids(records, dispute),
        unanswered_contact_ids=unanswered_customer_contacts(records, dispute),
    )


# --- Case 1: large fraud claim -> challenge ------------------------------------------------


def fraud_dispute(stage=Stage.RFI):
    return make_dispute(
        stage=stage,
        reason=DisputeReason(type="FRAUDULENT", original_code="10.4"),
        amount=usd("1500.00"),
    )


def weak_payment_attempt():
    attempt = PaymentAttempt(
        id="att-1",
        merchant_id=MERCHANT,
        payment_intent_id="int-1",
        amount=usd("1500.00"),
        three_ds=ThreeDSResult.NOT_PERFORMED,
        avs=AvsResult.NOT_CHECKED,
        cvc=CvcResult.MATCH,
        created_at=T0 - timedelta(days=20),
    )
    return attempt.to_evidence_record(case_id=CASE, captured_at=T0, synthetic=True)


def order_history():
    return make_record(
        SourceSystem.ORDER,
        {"order_id": "ORD-7731", "prior_undisputed_orders_matching_device_ip": 3},
        id="order-1",
        author=RecordAuthor.MERCHANT,
    )


SIGNED_DELIVERY = carrier_record(signed_by="J. Smith")

CASE1_CLAIMS = [
    make_claim(
        id="claim-history",
        source_record_id="order-1",
        source_field="prior_undisputed_orders_matching_device_ip",
        establishes=E.PRIOR_UNDISPUTED_ORDERS_MATCH,
        extracted_value="3",
        normalised_value={"kind": "ENUM", "text": "3"},
    ),
    make_claim(
        id="claim-signed",
        source_field="signed_by",
        establishes=E.DELIVERY_SIGNATURE,
        extracted_value="J. Smith",
        normalised_value={"kind": "NAME", "text": "J. Smith"},
    ),
]


def test_case1_before_search_refers_and_points_at_the_missing_evidence():
    d = run(fraud_dispute(), [weak_payment_attempt()])
    # p = 0.10 (CVC alone unlocks nothing): G_cb = 150 - 70 = 80; margin 80 - 85 = -5.
    assert (d.p, d.g_cb, d.margin) == (D("0.10"), D("80.0000"), D("-5.0000"))
    assert d.math_action is Action.ACCEPT
    assert d.referral_reasons == (ReferralReason.RFI_ACCEPT_NEEDS_APPROVAL,)
    assert {E.PRIOR_UNDISPUTED_ORDERS_MATCH} in [set(v.evidence) for v in d.voi]


def test_case1_after_search_challenges_with_a_verified_package():
    dispute = fraud_dispute()
    records = [weak_payment_attempt(), order_history(), SIGNED_DELIVERY]
    d = run(dispute, records)
    # p = 0.75 (prior orders + signature): G_cb = 1125 - 70 = 1055; margin 1055 - 85 = 970.
    assert d.p == D("0.75")
    assert d.margin == D("970.0000")
    assert d.action is Action.CHALLENGE

    result = verify(dispute, CASE1_CLAIMS, records)
    assert result.outcome is VerificationOutcome.PASSED
    # Consistency rule (architecture section 6): the verified facts alone still say CHALLENGE.
    recheck = decide(dispute, DEMO_POLICY, result.established, ownership_resolved=True)
    assert recheck.action is Action.CHALLENGE


def test_case1b_rejection_starts_a_chargeback_cycle_that_challenges_again():
    dispute = fraud_dispute(stage=Stage.CHARGEBACK)
    records = [weak_payment_attempt(), order_history(), SIGNED_DELIVERY]
    d = run(dispute, records)
    # 0.75 * 1500 - 20 - 50 - 10 = 1045.
    assert d.margin == D("1045.0000")
    assert d.action is Action.CHALLENGE
    assert verify(dispute, CASE1_CLAIMS, records).outcome is VerificationOutcome.PASSED


# --- Case 2: small not-received claim -> accept (low value); demo C on the same case -------


def small_non_receipt():
    return make_dispute(
        reason=DisputeReason(type="PRODUCT_NOT_RECEIVED", original_code="13.1"),
        amount=usd("15.00"),
    )


UNSIGNED_DELIVERY = carrier_record()  # DELIVERED, recipient name on the label, no signature


def test_case2_accepts_as_low_value():
    d = run(small_non_receipt(), [UNSIGNED_DELIVERY])
    # p = 0.60: 9 - 20 - 50 - 10 = -71.
    assert d.margin == D("-71.0000")
    assert d.action is Action.ACCEPT
    assert d.accept_reason is AcceptReason.LOW_VALUE_TRANSACTION


def test_case2c_unguarded_signature_claim_is_blocked():
    fabricated = make_claim(
        id="claim-signed",
        source_field="signed_by",
        establishes=E.DELIVERY_SIGNATURE,
        extracted_value="J. Smith",
        normalised_value={"kind": "NAME", "text": "J. Smith"},
    )
    result = verify(small_non_receipt(), [make_claim(), fabricated], [UNSIGNED_DELIVERY])
    assert result.outcome is VerificationOutcome.BLOCKED
    (blocked,) = result.blocked
    assert blocked.claim.text == "Delivery signed for by: J. Smith"
    assert {f.check for f in blocked.failures} == {CheckName.FIELD_MISSING}


# --- Case 3: credit not processed, two unanswered emails -> escalate -----------------------


def test_case3_unanswered_customer_contact_is_escalated():
    dispute = make_dispute(
        reason=DisputeReason(type="CREDIT_NOT_PROCESSED", original_code="13.6"),
        amount=usd("120.00"),
    )
    emails = [
        make_record(
            SourceSystem.HELPDESK,
            {"ticket_id": f"T{i}", "sent_at": f"2026-02-2{i}T09:00:00Z"},
            id=f"email-{i}",
            author=RecordAuthor.CUSTOMER,
        )
        for i in (1, 2)
    ]
    d = run(dispute, emails)
    assert d.math_action is Action.ACCEPT  # the maths alone would accept (unknown prior 0.10)
    assert d.action is Action.REFER
    assert d.referral_reasons == (ReferralReason.UNANSWERED_CUSTOMER_CONTACT,)
    assert d.inputs["unanswered_contact_ids"] == ["email-1", "email-2"]
