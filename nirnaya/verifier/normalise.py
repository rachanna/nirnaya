"""Normalisation used to compare a claim's value with its record. Each parser raises ValueError."""

import re
import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from nirnaya.models.evidence import FieldValue
from nirnaya.models.money import Money

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NON_WORD = re.compile(r"[^\w]+")


def parse_money(value: FieldValue, currency: FieldValue) -> Money:
    if not isinstance(value, str) or not isinstance(currency, str):
        raise ValueError("record amount and currency must both be present as text")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"record amount {value!r} is not a number") from exc
    return Money(amount=amount, currency=currency)


def parse_instant(value: FieldValue) -> datetime:
    """An ISO-8601 timestamp with an offset, as UTC. Naive or date-only values are refused."""
    if not isinstance(value, str) or _DATE_ONLY.match(value):
        raise ValueError(f"record value {value!r} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"record value {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("record timestamp has no time zone, so it cannot be normalised")
    return parsed.astimezone(UTC)


def parse_date_only(value: FieldValue) -> date | None:
    """A bare YYYY-MM-DD record value, or None if the value is not date-only."""
    if isinstance(value, str) and _DATE_ONLY.match(value):
        return date.fromisoformat(value)
    return None


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
