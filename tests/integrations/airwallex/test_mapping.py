"""Airwallex JSON -> domain objects, using the API reference's own example as the fixture."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from nirnaya.integrations.airwallex.http import parse_json
from nirnaya.integrations.airwallex.mapping import (
    UnmappableRecord,
    attempt_from_wire,
    dispute_from_wire,
)
from nirnaya.models import (
    AvsResult,
    CardBrand,
    CvcResult,
    DisputeStatus,
    Money,
    ReasonCategory,
    Stage,
    ThreeDSResult,
)

# Verbatim example response of GET /api/v1/pa/payment_disputes/{id} (API reference, 2026-10-27).
DOC_DISPUTE = """{
  "id": "dst_hkpdw2eqp9oie", "stage": "CHARGEBACK", "status": "ACCEPTED", "amount": 100,
  "currency": "USD", "mode": "COLLABORATION", "merchant_order_id": "D202503210001",
  "payment_intent_id": "int_hkpdskz7vg1xc7uscdj", "payment_attempt_id": "att_hkpdw2eqp9oie",
  "acquirer_reference_number": "T1234567890", "payment_method_type": "VISA",
  "issuer_comment": "", "issuer_documents": [], "card_brand": "visa",
  "reason": {"original_code": "4837", "description": "Fraudulent transaction.",
             "type": "FRAUDULENT"},
  "accept_details": [], "challenge_details": [], "due_at": "2023-11-01T10:00:00+00:00",
  "transaction_type": "PAYMENT", "customer_name": "John Doe",
  "created_at": "2023-10-01T10:00:00+00:00", "updated_at": "2023-10-01T10:00:00+00:00"
}"""


def doc(**changes):
    data = parse_json(DOC_DISPUTE.encode())
    data.update(changes)
    return data


def test_the_documented_example_maps_exactly():
    d = dispute_from_wire(doc(), merchant_id="m1")
    assert d.id == "dst_hkpdw2eqp9oie" and d.merchant_id == "m1"
    assert (d.stage, d.status) == (Stage.CHARGEBACK, DisputeStatus.ACCEPTED)
    assert d.amount == Money(amount=Decimal("100"), currency="USD")
    assert d.card_brand is CardBrand.VISA
    assert d.reason.type == "FRAUDULENT" and d.reason.original_code == "4837"
    assert d.reason_category is ReasonCategory.FRAUD
    assert (d.payment_intent_id, d.payment_attempt_id) == (
        "int_hkpdskz7vg1xc7uscdj",
        "att_hkpdw2eqp9oie",
    )
    assert d.merchant_order_id == "D202503210001"
    assert d.opened_at == datetime(2023, 10, 1, 10, 0, tzinfo=UTC)
    assert d.due_at == datetime(2023, 11, 1, 10, 0, tzinfo=UTC)
    assert d.refunded_amount is None


def test_offsets_without_a_colon_are_parsed():
    d = dispute_from_wire(doc(due_at="2025-05-20T13:14:00+0000"), merchant_id="m1")
    assert d.due_at == datetime(2025, 5, 20, 13, 14, tzinfo=UTC)


@pytest.mark.parametrize("field,value", [("stage", "MEDIATION"), ("status", "RECEIVED")])
def test_an_undocumented_stage_or_status_is_never_guessed(field, value):
    with pytest.raises(UnmappableRecord, match="undocumented"):
        dispute_from_wire(doc(**{field: value}), merchant_id="m1")


def test_an_unknown_reason_type_degrades_to_unknown():
    d = dispute_from_wire(doc(reason={"type": "SOMETHING_NEW"}), merchant_id="m1")
    assert d.reason_category is ReasonCategory.UNKNOWN


def test_missing_required_fields_are_rejected():
    with pytest.raises(UnmappableRecord, match="payment_intent_id"):
        dispute_from_wire(doc(payment_intent_id=None), merchant_id="m1")


def test_refunds_are_netted_skipping_failed_and_other_currencies():
    refunds = [
        {"amount": Decimal("30"), "currency": "USD", "status": "SETTLED"},
        {"amount": Decimal("20"), "currency": "USD", "status": "RECEIVED"},
        {"amount": Decimal("40"), "currency": "USD", "status": "FAILED"},
        {"amount": Decimal("10"), "currency": "EUR", "status": "SETTLED"},
    ]
    d = dispute_from_wire(doc(status="REQUIRES_RESPONSE"), merchant_id="m1", refunds=refunds)
    assert d.refunded_amount == Money(amount=Decimal("50"), currency="USD")


def test_refunds_above_the_disputed_amount_are_capped():
    refunds = [{"amount": Decimal("150"), "currency": "USD", "status": "SETTLED"}]
    d = dispute_from_wire(doc(), merchant_id="m1", refunds=refunds)
    assert d.refunded_amount == d.amount


def attempt(**auth):
    return {
        "id": "att_1",
        "payment_intent_id": "int_1",
        "amount": Decimal("1500"),
        "currency": "USD",
        "status": "PAID",
        "created_at": "2026-10-27T10:00:00+0000",
        "authentication_data": auth,
    }


@pytest.mark.parametrize(
    "ds,expected",
    [
        ({"pa_res_status": "Y"}, ThreeDSResult.AUTHENTICATED),
        ({"pa_res_status": "A"}, ThreeDSResult.ATTEMPTED),
        ({"pa_res_status": "N"}, ThreeDSResult.FAILED),
        ({"pa_res_status": "R"}, ThreeDSResult.FAILED),
        ({"pa_res_status": "U"}, ThreeDSResult.UNKNOWN),
        ({"pa_res_status": "C"}, ThreeDSResult.UNKNOWN),
        ({"pa_res_status": None, "enrolled": "N"}, ThreeDSResult.UNKNOWN),
        ({}, ThreeDSResult.NOT_PERFORMED),
    ],
)
def test_three_ds_comes_from_pa_res_status(ds, expected):
    assert attempt_from_wire(attempt(ds_data=ds), merchant_id="m1").three_ds is expected


def test_no_authentication_means_not_performed():
    a = attempt_from_wire(
        attempt(authentication_type="none", ds_data={"pa_res_status": "Y"}), merchant_id="m1"
    )
    assert a.three_ds is ThreeDSResult.NOT_PERFORMED


@pytest.mark.parametrize(
    "value,avs,cvc",
    [
        ("pass", AvsResult.MATCH, CvcResult.MATCH),
        ("PASS", AvsResult.MATCH, CvcResult.MATCH),
        ("fail", AvsResult.NO_MATCH, CvcResult.NO_MATCH),
        ("unavailable", AvsResult.UNKNOWN, CvcResult.UNKNOWN),
        (None, AvsResult.UNKNOWN, CvcResult.UNKNOWN),
    ],
)
def test_avs_and_cvc_fail_closed_on_undocumented_values(value, avs, cvc):
    a = attempt_from_wire(attempt(avs_result=value, cvc_result=value), merchant_id="m1")
    assert (a.avs, a.cvc) == (avs, cvc)


def test_attempt_fields():
    a = attempt_from_wire(attempt(), merchant_id="m1")
    assert (a.id, a.payment_intent_id, a.merchant_id) == ("att_1", "int_1", "m1")
    assert a.amount == Money(amount=Decimal("1500"), currency="USD")
    assert a.created_at == datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    assert json.dumps(a.raw, default=str)  # kept for audit
