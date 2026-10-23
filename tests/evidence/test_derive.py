from nirnaya.evidence.derive import (
    case_records,
    contradictory_record_ids,
    cross_check,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.models import (
    AvsResult,
    CvcResult,
    EvidenceType,
    PaymentAttempt,
    RecordAuthor,
    SourceSystem,
    ThreeDSResult,
)
from tests.factories import (
    CASE,
    MERCHANT,
    OTHER_MERCHANT,
    T0,
    carrier_record,
    make_dispute,
    make_record,
    usd,
)

E = EvidenceType


def test_delivered_carrier_record_yields_delivery_evidence():
    assert evidence_present([carrier_record()], make_dispute()) == {E.DELIVERY_CONFIRMED}


def test_in_transit_record_is_not_delivery_evidence():
    assert evidence_present([carrier_record(status="IN_TRANSIT")], make_dispute()) == frozenset()


def test_other_merchants_records_are_ignored():
    rec = make_record(SourceSystem.CARRIER, {"status": "DELIVERED"}, merchant_id=OTHER_MERCHANT)
    assert evidence_present([rec], make_dispute()) == frozenset()
    assert case_records([rec], make_dispute()) == []


def test_other_cases_records_are_ignored():
    rec = make_record(SourceSystem.CARRIER, {"status": "DELIVERED"}, case_id="case-2")
    assert evidence_present([rec], make_dispute()) == frozenset()


def test_tampered_records_are_ignored():
    rec = carrier_record(status="IN_TRANSIT")
    tampered = rec.model_copy(update={"fields": {**rec.fields, "status": "DELIVERED"}})
    assert evidence_present([tampered], make_dispute()) == frozenset()


def test_customer_records_are_ignored():
    rec = make_record(SourceSystem.CARRIER, {"status": "DELIVERED"}, author=RecordAuthor.CUSTOMER)
    assert evidence_present([rec], make_dispute()) == frozenset()


def test_payment_attempt_snapshot_yields_fraud_evidence():
    attempt = PaymentAttempt(
        id="att-1",
        merchant_id=MERCHANT,
        payment_intent_id="int-1",
        amount=usd("200.00"),
        three_ds=ThreeDSResult.ATTEMPTED,
        avs=AvsResult.MATCH,
        cvc=CvcResult.MATCH,
        created_at=T0,
    )
    rec = attempt.to_evidence_record(case_id=CASE, captured_at=T0, synthetic=True)
    assert evidence_present([rec], make_dispute()) == {E.AVS_MATCH, E.CVC_MATCH}


def test_records_that_disagree_are_reported():
    delivered = carrier_record("carrier-1")
    returned = carrier_record("carrier-2", status="RETURNED_TO_SENDER")
    assert contradictory_record_ids([returned, delivered], make_dispute()) == ("carrier-2",)


def test_customer_allegation_is_not_a_record_conflict():
    allegation = make_record(
        SourceSystem.HELPDESK,
        {"reported_not_received": True, "sent_at": "2026-02-25T10:00:00Z"},
        id="ticket-1",
        author=RecordAuthor.CUSTOMER,
    )
    records = [carrier_record(), allegation]
    assert contradictory_record_ids(records, make_dispute()) == ()
    # It is still found as counterevidence for the verifier to surface.
    found = cross_check(records, make_dispute()).contradictions
    assert [c.record_id for c in found if c.contradicts is E.DELIVERY_CONFIRMED] == ["ticket-1"]


def test_contradiction_of_absent_evidence_is_not_a_conflict():
    # Nothing establishes delivery, so a "lost" scan contradicts nothing.
    lost = carrier_record("carrier-2", status="LOST")
    assert contradictory_record_ids([lost], make_dispute()) == ()


def test_other_merchants_contradictions_are_ignored():
    foreign = make_record(
        SourceSystem.CARRIER, {"status": "LOST"}, id="x", merchant_id=OTHER_MERCHANT
    )
    assert contradictory_record_ids([carrier_record(), foreign], make_dispute()) == ()


# --- unanswered_customer_contacts --------------------------------------------------------


def customer_msg(id, ticket="T1", sent_at="2026-02-25T10:00:00Z", **kw):
    fields = {"ticket_id": ticket, "sent_at": sent_at}
    return make_record(SourceSystem.HELPDESK, fields, id=id, author=RecordAuthor.CUSTOMER, **kw)


def merchant_reply(id, ticket="T1", replied_at="2026-02-25T12:00:00Z", **kw):
    fields = {"ticket_id": ticket, "replied_at": replied_at}
    return make_record(SourceSystem.HELPDESK, fields, id=id, author=RecordAuthor.MERCHANT, **kw)


def test_customer_message_without_reply_is_unanswered():
    assert unanswered_customer_contacts([customer_msg("m1")], make_dispute()) == ("m1",)


def test_two_unanswered_emails_are_both_listed():
    records = [customer_msg("m2", ticket="T2"), customer_msg("m1")]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1", "m2")


def test_merchant_reply_on_the_same_ticket_answers_it():
    records = [customer_msg("m1"), merchant_reply("r1")]
    assert unanswered_customer_contacts(records, make_dispute()) == ()


def test_reply_on_another_ticket_does_not_answer():
    records = [customer_msg("m1"), merchant_reply("r1", ticket="T9")]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1",)


def test_reply_without_replied_at_does_not_answer():
    records = [customer_msg("m1"), merchant_reply("r1", replied_at=None)]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1",)


def test_customer_authored_reply_does_not_answer():
    fake = make_record(
        SourceSystem.HELPDESK,
        {"ticket_id": "T1", "replied_at": "2026-02-25T12:00:00Z"},
        id="r1",
        author=RecordAuthor.CUSTOMER,
    )
    # The customer cannot mark their own ticket answered; the "reply" is itself a customer
    # message (undated, so treated as before the dispute) and is listed too.
    assert unanswered_customer_contacts([customer_msg("m1"), fake], make_dispute()) == ("m1", "r1")


def test_message_after_the_dispute_opened_does_not_count():
    later = customer_msg("m1", sent_at="2026-03-05T10:00:00Z")  # dispute opened 2026-03-01
    assert unanswered_customer_contacts([later], make_dispute()) == ()


def test_undated_message_counts_as_before_the_dispute():
    assert unanswered_customer_contacts([customer_msg("m1", sent_at=None)], make_dispute()) == (
        "m1",
    )


def test_other_merchants_messages_are_ignored():
    foreign = customer_msg("m1", merchant_id=OTHER_MERCHANT)
    assert unanswered_customer_contacts([foreign], make_dispute()) == ()


def test_other_helpdesk_records_are_not_messages():
    summary = make_record(
        SourceSystem.HELPDESK, {"contacts_before_dispute": 0}, id="s", author=RecordAuthor.MERCHANT
    )
    assert unanswered_customer_contacts([summary], make_dispute()) == ()


# --- temporal invariants (audit M3) -------------------------------------------------------------


def _order(placed_at="2026-02-15T09:00:00+08:00"):
    return make_record(
        SourceSystem.ORDER,
        {"order_id": "ORD-1", "placed_at": placed_at},
        id="order-1",
        author=RecordAuthor.MERCHANT,
    )


def test_a_delivery_scan_before_the_order_makes_the_records_contradictory():
    records = [_order(), carrier_record(delivered_at="2026-02-08T10:00:00Z")]
    dispute = make_dispute()
    assert contradictory_record_ids(records, dispute) == ("carrier-1",)
    found = cross_check(records, dispute).contradictions
    (clash,) = [c for c in found if c.contradicts is E.DELIVERY_CONFIRMED]
    assert clash.record_id == "carrier-1" and "predates the order" in clash.description


def test_an_order_placed_after_the_dispute_opened_is_contradictory():
    records = [_order(placed_at="2026-03-02T00:00:00Z")]
    assert contradictory_record_ids(records, make_dispute()) == ("order-1",)


def test_temporal_rules_ignore_records_that_establish_nothing():
    records = [_order(), carrier_record(status="LOST", delivered_at="2026-01-01T00:00:00Z")]
    # The LOST record contradicts delivery, but no record establishes delivery: nothing to refer.
    assert contradictory_record_ids(records, make_dispute()) == ()


def test_consistent_dates_are_not_contradictory():
    records = [_order(), carrier_record()]
    assert contradictory_record_ids(records, make_dispute()) == ()
    assert cross_check(records, make_dispute()).late_notes == ()


def test_late_evidence_is_noted_not_contradicted():
    records = [_order(), carrier_record(delivered_at="2026-03-01T20:00:00Z")]
    dispute = make_dispute()
    assert contradictory_record_ids(records, dispute) == ()
    ((record, description),) = cross_check(records, dispute).late_notes
    assert record.id == "carrier-1" and "after the dispute opened" in description
