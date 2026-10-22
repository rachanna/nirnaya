from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from nirnaya.models import Money
from tests.factories import T0, make_dispute, usd


def test_naive_due_at_is_rejected():
    with pytest.raises(ValidationError):
        make_dispute(due_at=datetime(2026, 3, 10, 12, 0))


def test_aware_datetimes_are_normalised_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    d = make_dispute(opened_at=datetime(2026, 3, 1, 17, 30, tzinfo=ist))
    assert d.opened_at == T0
    assert d.opened_at.utcoffset() == timedelta(0)
    assert d.opened_at.tzinfo is UTC


def test_due_before_opened_is_rejected():
    with pytest.raises(ValidationError, match="precedes"):
        make_dispute(due_at=T0 - timedelta(seconds=1))


@pytest.mark.parametrize("amount", ["0", "-1.00"])
def test_non_positive_amount_is_rejected(amount):
    with pytest.raises(ValidationError, match="positive"):
        make_dispute(amount=usd(amount))


def test_refund_currency_must_match():
    with pytest.raises(ValidationError, match="dispute currency"):
        make_dispute(refunded_amount=Money(amount="1", currency="EUR"))


def test_refund_cannot_exceed_amount():
    with pytest.raises(ValidationError, match="between 0"):
        make_dispute(refunded_amount=usd("200.01"))


def test_refunded_defaults_to_zero():
    assert make_dispute().refunded == usd("0")


@pytest.mark.parametrize("field", ["id", "merchant_id"])
def test_blank_identifiers_are_rejected(field):
    with pytest.raises(ValidationError, match="blank"):
        make_dispute(**{field: "   "})


def test_unknown_stage_is_rejected():
    with pytest.raises(ValidationError):
        make_dispute(stage="CHARGEBACK_REVERSAL")


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        make_dispute(colour="red")


def test_payment_intent_id_is_required():
    from tests.factories import CASE

    data = make_dispute().model_dump()
    del data["payment_intent_id"]
    with pytest.raises(ValidationError, match="payment_intent_id"):
        type(make_dispute())(**data)
    assert make_dispute().id == CASE


def test_status_accepts_only_documented_airwallex_values():
    from nirnaya.models import DisputeStatus

    assert make_dispute(status="PENDING_CLOSURE").status is DisputeStatus.PENDING_CLOSURE
    with pytest.raises(ValidationError):
        make_dispute(status="AWAITING_RESPONSE")


def test_card_brand_uses_airwallex_values_and_is_optional():
    from nirnaya.models import CardBrand

    assert make_dispute(card_brand="union pay").card_brand is CardBrand.UNION_PAY
    assert make_dispute(card_brand=None).card_brand is None
    with pytest.raises(ValidationError):
        make_dispute(card_brand="VISA")


def test_dispute_round_trips_through_json():
    d = make_dispute()
    assert type(d).model_validate_json(d.model_dump_json()) == d
