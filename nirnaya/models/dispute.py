from typing import Self

from pydantic import model_validator

from nirnaya.models.base import DomainModel, NonBlankStr, UtcDatetime
from nirnaya.models.enums import DisputeStatus, ReasonCategory, Stage
from nirnaya.models.money import Money


class Dispute(DomainModel):
    id: NonBlankStr
    merchant_id: NonBlankStr
    payment_attempt_id: NonBlankStr | None = None
    stage: Stage
    status: DisputeStatus
    reason_category: ReasonCategory
    # Raw network code, kept for audit only; decisions use reason_category.
    network_reason_code: str | None = None
    amount: Money
    # Partial refunds already issued on the disputed payment; None means none.
    refunded_amount: Money | None = None
    opened_at: UtcDatetime
    due_at: UtcDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.amount.amount <= 0:
            raise ValueError("dispute amount must be positive")
        if self.refunded_amount is not None:
            if self.refunded_amount.currency != self.amount.currency:
                raise ValueError("refunded_amount must be in the dispute currency")
            if not 0 <= self.refunded_amount.amount <= self.amount.amount:
                raise ValueError("refunded_amount must be between 0 and the dispute amount")
        if self.due_at < self.opened_at:
            raise ValueError("due_at precedes opened_at")
        return self

    @property
    def refunded(self) -> Money:
        return self.refunded_amount or Money.zero(self.amount.currency)
