"""Normalisation used to compare a claim's value with its record. Each parser raises ValueError."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from nirnaya.models.evidence import FieldValue
from nirnaya.models.money import Money
from nirnaya.models.timestamps import parse_date_only as parse_date_only
from nirnaya.models.timestamps import parse_instant as parse_instant

_NON_WORD = re.compile(r"[^\w]+")


def parse_money(value: FieldValue, currency: FieldValue) -> Money:
    if not isinstance(value, str) or not isinstance(currency, str):
        raise ValueError("record amount and currency must both be present as text")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"record amount {value!r} is not a number") from exc
    return Money(amount=amount, currency=currency)


def name_tokens(value: str) -> tuple[str, ...]:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return tuple(t for t in _NON_WORD.split(folded) if t)


def contains_tokens(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """True if `needle` occurs as a contiguous run of whole tokens in `haystack`."""
    if not needle:
        return False
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def normalise_identifier(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def normalise_enum(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().upper()
