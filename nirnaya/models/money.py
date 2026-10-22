"""Money and FX. Decimal only, always paired with an ISO-4217 currency code."""

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Self

from pydantic import Field, field_validator, model_validator

from nirnaya.models.base import Dec, DomainModel, NonBlankStr, UtcDatetime

_ZERO_DECIMAL = "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF"
_THREE_DECIMAL = "BHD IQD JOD KWD LYD OMR TND"
_FOUR_DECIMAL = "CLF UYW"
_TWO_DECIMAL = (
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BMD BND BOB BRL BSD BTN BWP "
    "BYN BZD CAD CDF CHF CNY COP CRC CUP CVE CZK DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP "
    "GEL GHS GIP GMD GTQ GYD HKD HNL HTG HUF IDR ILS INR IRR JMD KES KGS KHR KPW KYD KZT "
    "LAK LBP LKR LRD LSL MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN "
    "NIO NOK NPR NZD PAB PEN PGK PHP PKR PLN QAR RON RSD RUB SAR SBD SCR SDG SEK SGD SHP "
    "SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TOP TRY TTD TWD TZS UAH USD UYU UZS VED "
    "VES WST XCD YER ZAR ZMW ZWG"
)

# ISO-4217 active currency codes -> number of minor-unit digits.
MINOR_UNITS: dict[str, int] = {
    **{code: 0 for code in _ZERO_DECIMAL.split()},
    **{code: 2 for code in _TWO_DECIMAL.split()},
    **{code: 3 for code in _THREE_DECIMAL.split()},
    **{code: 4 for code in _FOUR_DECIMAL.split()},
}


class CurrencyMismatchError(ValueError):
    pass


class MissingFxRateError(LookupError):
    pass


def validate_currency(code: str) -> str:
    if code not in MINOR_UNITS:
        raise ValueError(f"{code!r} is not an ISO-4217 currency code")
    return code


def quantize_amount(amount: Decimal, currency: str) -> Decimal:
    """Round to the currency's minor units, banker's rounding."""
    return amount.quantize(Decimal(1).scaleb(-MINOR_UNITS[currency]), rounding=ROUND_HALF_EVEN)


class Money(DomainModel):
    amount: Dec
    currency: str

    _check_currency = field_validator("currency")(validate_currency)

    @model_validator(mode="after")
    def _fits_minor_units(self) -> Self:
        if quantize_amount(self.amount, self.currency) != self.amount:
            raise ValueError(
                f"{self.amount} has more precision than {self.currency} minor units allow"
            )
        return self

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(amount=Decimal(0), currency=currency)

    def _same_currency(self, other: "Money") -> None:
        if other.currency != self.currency:
            raise CurrencyMismatchError(f"{self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __str__(self) -> str:
        return f"{quantize_amount(self.amount, self.currency)} {self.currency}"


class FxRate(DomainModel):
    base: str
    quote: str
    rate: Dec = Field(gt=0)

    _check_base = field_validator("base")(validate_currency)
    _check_quote = field_validator("quote")(validate_currency)


class FxTable(DomainModel):
    """A fixed snapshot of FX rates. Only direct rates are used; inverses are never derived."""

    rates: tuple[FxRate, ...] = ()
    as_of: UtcDatetime
    label: NonBlankStr

    @model_validator(mode="after")
    def _unique_pairs(self) -> Self:
        pairs = [(r.base, r.quote) for r in self.rates]
        if len(pairs) != len(set(pairs)):
            raise ValueError("duplicate FX currency pair")
        return self

    def rate(self, base: str, quote: str) -> Decimal | None:
        if base == quote:
            return Decimal(1)
        for r in self.rates:
            if r.base == base and r.quote == quote:
                return r.rate
        return None


def convert(money: Money, quote: str, fx: FxTable | None) -> tuple[Money, Decimal]:
    """Convert to `quote`, rounded to its minor units. Returns (converted, rate used)."""
    if money.currency == quote:
        return money, Decimal(1)
    rate = fx.rate(money.currency, quote) if fx is not None else None
    if rate is None:
        raise MissingFxRateError(f"no FX rate {money.currency}->{quote}")
    return Money(amount=quantize_amount(money.amount * rate, quote), currency=quote), rate
