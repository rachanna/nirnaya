"""Amounts match after currency normalisation and partial refunds."""

import pytest

from nirnaya.config.demo import DEMO_FX
from nirnaya.models import AmountBasis, EvidenceType, Money
from nirnaya.verifier import CheckName
from tests.factories import carrier_record, make_claim, make_dispute, usd
from tests.verifier.helpers import checks, delivery_claim, order_record, run


def amount_claim(amount: Money, basis: AmountBasis = AmountBasis.GROSS, **overrides):
    data = {
        "id": "claim-amt",
        "source_record_id": "order-1",
        "source_field": "total_amount",
        "establishes": EvidenceType.ORDER_PLACED,
        "extracted_value": "200.00",
        "normalised_value": {"kind": "MONEY", "money": amount, "basis": basis},
    }
    data.update(overrides)
    return make_claim(**data)


RECORDS = [carrier_record(), order_record()]


def test_same_currency_amount_matches():
    result = run([delivery_claim(), amount_claim(usd("200.00"))], RECORDS)
    assert checks(result, "claim-amt") == set()


def test_trailing_zeros_do_not_matter():
    result = run([amount_claim(usd("200"))], RECORDS)
    assert checks(result, "claim-amt") == set()


def test_wrong_amount_is_blocked():
    result = run([amount_claim(usd("210.00"))], RECORDS)
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_amount_matches_after_fx_normalisation():
    eur_order = order_record(total_amount="181.82", currency="EUR")  # x1.10 = 200.002 -> 200.00
    claim = amount_claim(usd("200.00"), extracted_value="181.82")
    result = run([claim], [carrier_record(), eur_order], fx=DEMO_FX)
    assert checks(result, "claim-amt") == set()


def test_fx_mismatch_is_blocked():
    eur_order = order_record(total_amount="181.82", currency="EUR")
    claim = amount_claim(usd("181.82"), extracted_value="181.82")
    result = run([claim], [carrier_record(), eur_order], fx=DEMO_FX)
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_missing_fx_rate_is_blocked():
    eur_order = order_record(total_amount="181.82", currency="EUR")
    claim = amount_claim(usd("200.00"), extracted_value="181.82")
    result = run([claim], [carrier_record(), eur_order])
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_claim_must_be_normalised_to_dispute_currency():
    eur_order = order_record(total_amount="181.82", currency="EUR")
    claim = amount_claim(Money(amount="181.82", currency="EUR"), extracted_value="181.82")
    result = run([claim], [carrier_record(), eur_order], fx=DEMO_FX)
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_net_of_partial_refund_matches():
    dispute = make_dispute(refunded_amount=usd("50.00"))
    claim = amount_claim(usd("150.00"), AmountBasis.NET_OF_REFUNDS)
    result = run([claim], RECORDS, dispute)
    assert checks(result, "claim-amt") == set()


def test_gross_amount_claimed_as_net_is_blocked():
    dispute = make_dispute(refunded_amount=usd("50.00"))
    claim = amount_claim(usd("200.00"), AmountBasis.NET_OF_REFUNDS)
    result = run([claim], RECORDS, dispute)
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_net_amount_claimed_as_gross_is_blocked():
    dispute = make_dispute(refunded_amount=usd("50.00"))
    claim = amount_claim(usd("150.00"), AmountBasis.GROSS)
    result = run([claim], RECORDS, dispute)
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_zero_decimal_currency_rounding():
    # 1.34 USD -> JPY at 150 = 201 JPY exactly; dispute is in JPY.
    dispute = make_dispute(amount=Money(amount="201", currency="JPY"))
    order = order_record(total_amount="1.34", currency="USD")
    claim = amount_claim(Money(amount="201", currency="JPY"), extracted_value="1.34")
    result = run([claim], [order], dispute, fx=DEMO_FX)
    assert checks(result, "claim-amt") == set()


def test_unparseable_record_amount_is_blocked():
    order = order_record(total_amount="two hundred")
    claim = amount_claim(usd("200.00"), extracted_value="two hundred")
    result = run([claim], [carrier_record(), order])
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


def test_record_without_currency_is_blocked():
    order = order_record(currency=None)
    result = run([amount_claim(usd("200.00"))], [carrier_record(), order])
    assert checks(result, "claim-amt") == {CheckName.AMOUNT_MISMATCH}


@pytest.mark.parametrize(
    "value", ["1E+6000", "1E+999999", "-1E+6000", "1E-6000", "NaN", "sNaN", "Infinity", "-Infinity"]
)
@pytest.mark.parametrize("basis", list(AmountBasis))
def test_an_impossible_record_amount_blocks_the_claim(value, basis):
    """FC-22: a huge amount raised, so the step failed where one claim should have been blocked."""
    record = order_record(total_amount=value)
    result = run([amount_claim(usd("200.00"), basis, extracted_value=value)], [record])
    assert CheckName.AMOUNT_MISMATCH in checks(result, "claim-amt")
