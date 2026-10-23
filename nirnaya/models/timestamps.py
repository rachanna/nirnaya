"""Reading timestamps from record fields. One parser, shared by derive and the verifier."""

import re
from datetime import UTC, datetime

from nirnaya.models.evidence import FieldValue

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
