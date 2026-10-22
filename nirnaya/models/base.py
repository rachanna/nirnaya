"""Shared base model and constrained field types."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
)

from nirnaya.models.enums import EvidenceType


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _reject_float(value: Any) -> Any:
    # bool is an int subclass; True must not silently become Decimal(1).
    if isinstance(value, float | bool):
        raise ValueError("must be a Decimal, int or numeric string, never a float or bool")
    return value


Dec = Annotated[Decimal, BeforeValidator(_reject_float)]
NonNegativeDec = Annotated[Dec, Field(ge=0)]
Probability = Annotated[Dec, Field(ge=0, le=1)]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankStr = Annotated[str, Field(min_length=1), AfterValidator(_not_blank)]


def _sorted_evidence(value: frozenset[EvidenceType]) -> list[EvidenceType]:
    # frozenset iteration order varies with hash seeding; serialise sorted for determinism.
    return sorted(value)


EvidenceSet = Annotated[
    frozenset[EvidenceType],
    PlainSerializer(_sorted_evidence, return_type=list[EvidenceType]),
]
