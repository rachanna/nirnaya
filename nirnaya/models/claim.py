"""Claims: one fact, bound to one field of one evidence record.

A claim without source_record_id and source_field cannot be constructed. Its text is
not free prose: it must equal the deterministic rendering of (establishes, value),
which the verifier re-renders and compares.
"""

import datetime as dt
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from nirnaya.models.base import DomainModel, NonBlankStr, UtcDatetime
from nirnaya.models.enums import AmountBasis, EvidenceType, ValidationStatus
from nirnaya.models.money import Money


class MoneyValue(DomainModel):
    kind: Literal["MONEY"] = "MONEY"
    money: Money
    basis: AmountBasis


class DateTimeValue(DomainModel):
    kind: Literal["DATETIME"] = "DATETIME"
    at: UtcDatetime


class DateValue(DomainModel):
    """A calendar date as seen in a named time zone (e.g. the delivery address's zone)."""

    kind: Literal["DATE"] = "DATE"
    date: dt.date
    tz: str

    @field_validator("tz")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA time zone {value!r}") from exc
        return value


class TextValue(DomainModel):
    kind: Literal["NAME", "IDENTIFIER", "ENUM"]
    text: NonBlankStr


NormalisedValue = Annotated[
    MoneyValue | DateTimeValue | DateValue | TextValue, Field(discriminator="kind")
]


class Claim(DomainModel):
    id: NonBlankStr
    case_id: NonBlankStr
    merchant_id: NonBlankStr
    text: NonBlankStr
    source_record_id: NonBlankStr
    source_field: NonBlankStr
    establishes: EvidenceType
    # The field's value exactly as it appears in the record (see evidence.raw_text).
    extracted_value: str
    normalised_value: NormalisedValue
    validation_status: ValidationStatus = ValidationStatus.PENDING
    contradicting_record_ids: tuple[NonBlankStr, ...] = ()

    @property
    def value_kind(self) -> str:
        return self.normalised_value.kind
