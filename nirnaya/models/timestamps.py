"""Reading timestamps from record fields. One parser, shared by derive and the verifier."""

import re
from datetime import UTC, date, datetime

from nirnaya.models.evidence import FieldValue

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_instant(value: FieldValue) -> datetime:
    """An ISO-8601 timestamp with an offset, as UTC. Naive or date-only values are refused.

    Raises ValueError for anything that is not such a timestamp, including one that cannot be
    expressed in UTC (e.g. year 1 with a positive offset).
    """
    if not isinstance(value, str) or DATE_ONLY.match(value):
        raise ValueError(f"record value {value!r} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"record value {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("record timestamp has no time zone, so it cannot be normalised")
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError(f"record value {value!r} is out of range in UTC") from exc


def parse_date_only(value: FieldValue) -> date | None:
    """A bare YYYY-MM-DD record value, or None if the value is not date-only."""
    if isinstance(value, str) and DATE_ONLY.match(value):
        return date.fromisoformat(value)
    return None
