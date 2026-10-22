"""Evidence records and their SHA-256 snapshot hashes.

A hash proves a record has not changed since capture. It says nothing about whether
the source was telling the truth.
"""

import hashlib
import json
from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr

from nirnaya.models.base import DomainModel, NonBlankStr, UtcDatetime
from nirnaya.models.enums import RecordAuthor, SourceSystem

# Field values are JSON scalars. Amounts and timestamps are stored as strings so that
# no float ever enters a record.
FieldValue = StrictStr | StrictBool | StrictInt | None

_PLACEHOLDER_HASH = "0" * 64


class EvidenceRecord(DomainModel):
    id: NonBlankStr
    merchant_id: NonBlankStr
    case_id: NonBlankStr
    source_system: SourceSystem
    author: RecordAuthor
    synthetic: bool
    fields: dict[str, FieldValue]
    captured_at: UtcDatetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, **data: Any) -> "EvidenceRecord":
        """Build a record and stamp it with the hash of its content."""
        record = cls(**data, sha256=_PLACEHOLDER_HASH)
        return record.model_copy(update={"sha256": record.compute_sha256()})

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"sha256"})
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def compute_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def hash_ok(self) -> bool:
        return self.sha256 == self.compute_sha256()


def raw_text(value: FieldValue) -> str | None:
    """The canonical text form of a field value, as a claim's extracted_value must quote it."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
