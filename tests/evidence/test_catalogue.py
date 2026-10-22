from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from nirnaya.evidence.catalogue import (
    CLAIM_TEMPLATES,
    CONTRADICTION_RULES,
    DENIAL_FACTS,
    FIELD_RULES,
    REASON_SPECIFIC,
    REQUIRED_EVIDENCE,
    record_establishes,
    render_claim_text,
)
from nirnaya.models import (
    AmountBasis,
    DateTimeValue,
    DateValue,
    EvidenceType,
    MoneyValue,
    ReasonCategory,
    RecordAuthor,
    SourceSystem,
    TextValue,
)
from tests.factories import carrier_record, make_record, usd

E = EvidenceType


# --- Table completeness ------------------------------------------------------------------


def test_every_evidence_type_has_a_template():
    assert set(CLAIM_TEMPLATES) == set(EvidenceType)


def test_every_evidence_type_can_be_established_by_some_field():
    assert {r.establishes for r in FIELD_RULES} == set(EvidenceType)


def test_required_evidence_covers_every_reason():
    assert set(REQUIRED_EVIDENCE) == set(ReasonCategory)
    assert REQUIRED_EVIDENCE[ReasonCategory.UNKNOWN] == ()
    for reason, groups in REQUIRED_EVIDENCE.items():
        if reason is not ReasonCategory.UNKNOWN:
            assert groups and all(groups), reason


def test_reason_specific_facts_never_map_to_unknown():
    assert ReasonCategory.UNKNOWN not in REASON_SPECIFIC.values()


def test_denial_facts_are_not_reason_specific():
    assert not DENIAL_FACTS & set(REASON_SPECIFIC)


def test_contradiction_rules_reference_known_facts():
    assert {r.contradicts for r in CONTRADICTION_RULES} <= set(EvidenceType)


# --- record_establishes ------------------------------------------------------------------


def test_delivered_status_establishes_delivery():
    rec = carrier_record()
    assert record_establishes(rec, "status") == {E.DELIVERY_CONFIRMED}
    assert record_establishes(rec, "delivered_at") == {E.DELIVERY_CONFIRMED}


@pytest.mark.parametrize("status", ["IN_TRANSIT", "RETURNED_TO_SENDER", "delivered"])
def test_other_carrier_statuses_establish_nothing(status):
    rec = carrier_record(status=status)
    assert record_establishes(rec, "status") == frozenset()
    assert record_establishes(rec, "delivered_at") == frozenset()


def test_recipient_name_establishes_nothing():
    assert record_establishes(carrier_record(), "recipient_name") == frozenset()


def test_missing_or_null_field_establishes_nothing():
    rec = carrier_record(signed_by=None)
    assert record_establishes(rec, "signed_by") == frozenset()
    assert record_establishes(carrier_record(), "signed_by") == frozenset()


def test_signature_needs_delivered_status_and_a_name():
    assert record_establishes(carrier_record(signed_by="J. Smith"), "signed_by") == {
        E.DELIVERY_SIGNATURE
    }
    assert (
        record_establishes(carrier_record(status="IN_TRANSIT", signed_by="J. Smith"), "signed_by")
        == frozenset()
    )


def test_customer_authored_records_establish_nothing():
    rec = make_record(SourceSystem.CARRIER, {"status": "DELIVERED"}, author=RecordAuthor.CUSTOMER)
    assert record_establishes(rec, "status") == frozenset()


def test_bool_is_not_confused_with_int():
    rec = make_record(
        SourceSystem.HELPDESK, {"contacts_before_dispute": False}, author=RecordAuthor.MERCHANT
    )
    assert record_establishes(rec, "contacts_before_dispute") == frozenset()
    rec0 = make_record(
        SourceSystem.HELPDESK, {"contacts_before_dispute": 0}, author=RecordAuthor.MERCHANT
    )
    assert record_establishes(rec0, "contacts_before_dispute") == {E.NO_PRIOR_CUSTOMER_CONTACT}


@pytest.mark.parametrize(("amount", "expected"), [("5.00", True), ("0.00", False), ("x", False)])
def test_refund_requires_a_positive_amount(amount, expected):
    rec = make_record(SourceSystem.ORDER, {"refunded_amount": amount, "currency": "USD"})
    assert (E.REFUND_ISSUED in record_establishes(rec, "refunded_amount")) is expected


def test_distinct_purchase_flag_is_required():
    yes = make_record(SourceSystem.ORDER, {"order_id": "o-2", "is_distinct_purchase": True})
    no = make_record(SourceSystem.ORDER, {"order_id": "o-2", "is_distinct_purchase": False})
    assert record_establishes(yes, "order_id") == {E.ORDER_PLACED, E.DISTINCT_TRANSACTIONS}
    assert record_establishes(no, "order_id") == {E.ORDER_PLACED}


# --- render_claim_text -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fact", "value", "expected"),
    [
        (
            E.DELIVERY_SIGNATURE,
            TextValue(kind="NAME", text="J. Smith"),
            "Delivery signed for by: J. Smith",
        ),
        (
            E.DELIVERY_CONFIRMED,
            DateTimeValue(at=datetime(2026, 2, 20, 20, 30, tzinfo=UTC)),
            "Delivery confirmed by carrier: 2026-02-20T20:30:00Z",
        ),
        (
            E.DELIVERY_CONFIRMED,
            DateValue(date=date(2026, 2, 20), tz="America/New_York"),
            "Delivery confirmed by carrier: 2026-02-20 (America/New_York)",
        ),
        (
            E.ORDER_PLACED,
            MoneyValue(money=usd("200"), basis=AmountBasis.GROSS),
            "Order record: 200.00 USD",
        ),
        (
            E.ORDER_PLACED,
            MoneyValue(money=usd(D("150.5")), basis=AmountBasis.NET_OF_REFUNDS),
            "Order record: 150.50 USD net of refunds",
        ),
    ],
)
def test_render_claim_text(fact, value, expected):
    assert render_claim_text(fact, value) == expected
