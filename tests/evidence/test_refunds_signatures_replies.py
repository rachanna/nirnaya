"""Failure cases FC-03, FC-04 and FC-05 (docs/failure-cases.md): evidence that only looked true."""

import pytest

from nirnaya.evidence.catalogue import record_establishes
from nirnaya.evidence.derive import evidence_present, unanswered_customer_contacts
from nirnaya.models.claim import MoneyValue
from nirnaya.models.enums import AmountBasis, EvidenceType, RecordAuthor, SourceSystem
from nirnaya.verifier import CheckName, verify
from tests.factories import make_claim, make_dispute, make_record, usd

E = EvidenceType


def order(refunded: str, total: str = "200.00", **extra: object):
    fields = {"order_id": "O1", "refunded_amount": refunded, "total_amount": total}
    return make_record(
        SourceSystem.ORDER,
        {"currency": "USD", **fields, **extra},
        id="order-1",
        author=RecordAuthor.MERCHANT,
    )


# --- FC-03: a refund counts only if it covers what is disputed ---------------------------------


@pytest.mark.parametrize(
    ("refunded", "total", "status", "expected"),
    [
        ("200.00", "200.00", "REFUNDED", True),
        ("200.00", "200.00", None, True),
        ("0.01", "200.00", "REFUNDED", False),  # a token refund is not a refund of the order
        ("199.99", "200.00", "REFUNDED", False),
        ("900.00", "200.00", "REFUNDED", False),  # more than the order: the record is wrong
        ("200.00", "200.00", "REVERSED", False),
        ("200.00", "200.00", "PENDING", False),
        ("Infinity", "Infinity", "REFUNDED", False),
        ("200.00", "x", "REFUNDED", False),
        ("0.00", "0.00", "REFUNDED", False),
    ],
)
def test_only_a_full_completed_refund_of_the_order_is_a_refund(refunded, total, status, expected):
    extra = {} if status is None else {"refund_status": status}
    rec = order(refunded, total, **extra)
    assert (E.REFUND_ISSUED in record_establishes(rec, "refunded_amount")) is expected


def test_a_full_refund_of_a_smaller_order_does_not_cover_the_dispute():
    rec = order("20.00", "20.00")
    assert E.REFUND_ISSUED not in evidence_present([rec], make_dispute(amount=usd("200.00")))


def test_a_refund_covering_the_dispute_counts():
    rec = order("200.00")
    assert E.REFUND_ISSUED in evidence_present([rec], make_dispute(amount=usd("200.00")))


def test_a_refund_in_another_currency_does_not_cover_the_dispute():
    rec = order("200.00", currency="EUR")
    assert E.REFUND_ISSUED not in evidence_present([rec], make_dispute(amount=usd("200.00")))


def test_the_verifier_blocks_a_refund_claim_that_does_not_cover_the_dispute():
    rec = order("20.00", "20.00")
    dispute = make_dispute(amount=usd("200.00"))
    claim = make_claim(
        source_record_id=rec.id,
        source_field="refunded_amount",
        establishes=E.REFUND_ISSUED,
        normalised_value=MoneyValue(money=usd("20.00"), basis=AmountBasis.GROSS),
        text="Refund issued: 20.00 USD",
    )
    result = verify(dispute, [claim], [rec])
    assert CheckName.EXCEEDS_RECORD in {f.check for r in result.claims for f in r.failures}


# --- FC-04: a placeholder is not a signature ---------------------------------------------------


def carrier(signed_by: object):
    return make_record(SourceSystem.CARRIER, {"status": "DELIVERED", "signed_by": signed_by})


@pytest.mark.parametrize(
    "value",
    ["NOT SIGNED", "not signed", "Not-Signed", "N/A", "n/a", "NA", "NONE", "None", "UNSIGNED",
     "NO SIGNATURE", "NO SIGNATURE REQUIRED", "UNKNOWN", "LEFT AT DOOR", "   ", "-", "123"],
)  # fmt: skip
def test_a_placeholder_is_not_a_signature(value):
    assert E.DELIVERY_SIGNATURE not in record_establishes(carrier(value), "signed_by")


@pytest.mark.parametrize("value", ["J. Smith", "Rahul Sharma", "राहुल", "O'Brien"])
def test_a_name_is_a_signature(value):
    assert E.DELIVERY_SIGNATURE in record_establishes(carrier(value), "signed_by")


def test_a_blank_value_establishes_nothing():
    rec = make_record(SourceSystem.ORDER, {"order_id": "   "}, author=RecordAuthor.MERCHANT)
    assert record_establishes(rec, "order_id") == frozenset()


# --- FC-05: each message needs its own answer --------------------------------------------------


def customer_msg(id: str, sent_at: object = "2026-02-25T10:00:00Z", ticket: str = "T1"):
    return make_record(
        SourceSystem.HELPDESK,
        {"ticket_id": ticket, "sent_at": sent_at},
        id=id,
        author=RecordAuthor.CUSTOMER,
    )


def reply(replied_at: object = "2026-02-25T12:00:00Z", ticket: str = "T1"):
    return make_record(
        SourceSystem.HELPDESK,
        {"ticket_id": ticket, "replied_at": replied_at},
        id="reply-1",
        author=RecordAuthor.MERCHANT,
    )


def test_a_reply_before_a_later_email_does_not_answer_it():
    records = [customer_msg("m1"), reply(), customer_msg("m2", "2026-02-26T09:00:00Z")]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m2",)


def test_a_reply_before_the_only_message_does_not_answer_it():
    records = [customer_msg("m1"), reply("2026-02-25T09:00:00Z")]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1",)


def test_a_reply_that_is_not_a_date_does_not_answer():
    records = [customer_msg("m1"), reply("PENDING")]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1",)


def test_a_reply_after_the_dispute_opened_does_not_answer():
    records = [customer_msg("m1"), reply("2026-03-02T09:00:00Z")]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1",)


def test_an_undated_message_cannot_be_shown_to_be_answered():
    records = [customer_msg("m1", None), reply()]
    assert unanswered_customer_contacts(records, make_dispute()) == ("m1",)


def test_a_tampered_reply_does_not_answer():
    rec = reply()
    tampered = rec.model_copy(
        update={"fields": {**rec.fields, "replied_at": "2026-02-25T13:00:00Z"}}
    )
    assert unanswered_customer_contacts([customer_msg("m1"), tampered], make_dispute()) == ("m1",)


def test_a_reply_after_each_message_answers_them_all():
    records = [customer_msg("m1"), customer_msg("m2", "2026-02-25T11:00:00Z"), reply()]
    assert unanswered_customer_contacts(records, make_dispute()) == ()
