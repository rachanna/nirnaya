from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from nirnaya.models import FxRate, FxTable, Money
from nirnaya.models.money import CurrencyMismatchError, MissingFxRateError, convert


def test_float_amount_is_rejected():
    with pytest.raises(ValidationError, match="never a float"):
        Money(amount=10.5, currency="USD")


def test_bool_amount_is_rejected():
    with pytest.raises(ValidationError):
        Money(amount=True, currency="USD")


@pytest.mark.parametrize(
    ("amount", "expected"), [("10.50", "10.5"), (10, "10"), (D("10.5"), "10.5")]
)
def test_decimal_int_and_string_amounts_are_accepted(amount, expected):
    assert Money(amount=amount, currency="USD").amount == D(expected)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_amount_is_rejected(amount):
    with pytest.raises(ValidationError):
        Money(amount=amount, currency="USD")


@pytest.mark.parametrize("currency", ["usd", "XYZ", "US", ""])
def test_non_iso_currency_is_rejected(currency):
    with pytest.raises(ValidationError, match="ISO-4217"):
        Money(amount="1", currency=currency)


@pytest.mark.parametrize(
    ("amount", "currency"), [("1.001", "USD"), ("1.5", "JPY"), ("1.0001", "KWD")]
)
def test_amount_finer_than_minor_units_is_rejected(amount, currency):
    with pytest.raises(ValidationError, match="minor units"):
        Money(amount=amount, currency=currency)


def test_trailing_zeros_within_minor_units_are_accepted():
    assert Money(amount="1.500", currency="USD").amount == D("1.5")


def test_same_currency_arithmetic():
    assert Money(amount="10", currency="USD") - Money(amount="2.5", currency="USD") == Money(
        amount="7.5", currency="USD"
    )


def test_cross_currency_arithmetic_raises():
    with pytest.raises(CurrencyMismatchError):
        Money(amount="1", currency="USD") + Money(amount="1", currency="EUR")


def test_money_is_immutable():
    m = Money(amount="1", currency="USD")
    with pytest.raises(ValidationError):
        m.amount = D("2")


FX = FxTable(
    rates=(FxRate(base="EUR", quote="USD", rate=D("1.10")),),
    as_of=datetime(2026, 1, 1, tzinfo=UTC),
    label="test",
)


def test_convert_uses_direct_rate_and_rounds_to_minor_units():
    converted, rate = convert(Money(amount="10.01", currency="EUR"), "USD", FX)
    assert rate == D("1.10")
    assert converted == Money(amount="11.01", currency="USD")  # 11.011 -> 11.01


def test_convert_same_currency_needs_no_table():
    m = Money(amount="5", currency="USD")
    assert convert(m, "USD", None) == (m, D(1))


def test_inverse_rate_is_never_derived():
    with pytest.raises(MissingFxRateError):
        convert(Money(amount="1", currency="USD"), "EUR", FX)


def test_convert_without_table_raises():
    with pytest.raises(MissingFxRateError):
        convert(Money(amount="1", currency="EUR"), "USD", None)


def test_rounding_is_half_even():
    fx = FxTable(
        rates=(FxRate(base="USD", quote="JPY", rate=D("1")),),
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        label="test",
    )
    assert convert(Money(amount="2.50", currency="USD"), "JPY", fx)[0].amount == D(2)
    assert convert(Money(amount="3.50", currency="USD"), "JPY", fx)[0].amount == D(4)


def test_duplicate_fx_pairs_are_rejected():
    rate = FxRate(base="EUR", quote="USD", rate=D("1.1"))
    with pytest.raises(ValidationError, match="duplicate"):
        FxTable(rates=(rate, rate), as_of=datetime(2026, 1, 1, tzinfo=UTC), label="x")


def test_non_positive_fx_rate_is_rejected():
    with pytest.raises(ValidationError):
        FxRate(base="EUR", quote="USD", rate=D("0"))
