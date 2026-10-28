"""Airwallex JSON -> Nirnaya domain objects (architecture section 9.3).

Field names and documented values are from the API reference (checked 2026-10-27):
payment_disputes (retrieve, list), payment_attempts (retrieve) and refunds (list).
A value outside the documented set is never guessed: an unknown stage or status rejects the
dispute, and an unknown authentication result maps to UNKNOWN, which establishes nothing.
"""

import json
from decimal import Decimal
from typing import Any

from nirnaya.integrations.airwallex.http import parse_time
from nirnaya.models.dispute import AiRecommendation, Dispute
from nirnaya.models.enums import (
    Action,
    AvsResult,
    CardBrand,
    CvcResult,
    DisputeStatus,
    Stage,
    ThreeDSResult,
)
from nirnaya.models.money import Money
from nirnaya.models.payment import PaymentAttempt
from nirnaya.models.reasons import DisputeReason

# Refunds in these states did not (or will not) move money back.
_REFUND_NOT_PAID = frozenset({"FAILED"})


class UnmappableRecord(ValueError):
    """An Airwallex object Nirnaya cannot represent faithfully. It is surfaced, never guessed."""


def _text(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _required(data: dict[str, Any], key: str, what: str) -> str:
    value = _text(data, key)
    if value is None:
        raise UnmappableRecord(f"{what} has no {key}")
    return value


def _enum(enum: type, value: Any, what: str) -> Any:  # noqa: ANN401
    try:
        return enum(value)
    except ValueError as e:
        raise UnmappableRecord(f"{what}: undocumented value {value!r}") from e


def _decimal(value: Any, what: str) -> Decimal:  # noqa: ANN401
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError as e:
            raise UnmappableRecord(f"{what}: not a number {value!r}") from e
    raise UnmappableRecord(f"{what}: not a number {value!r}")


def refunded_total(refunds: list[dict[str, Any]], dispute_amount: Money) -> Money | None:
    """Refunds already issued on the disputed payment, in its currency, capped at the amount.

    Refunds in another currency are not counted (TODO(verify-airwallex-docs): whether a refund
    can differ in currency from its payment).
    """
    total = Decimal(0)
    for refund in refunds:
        if refund.get("status") in _REFUND_NOT_PAID:
            continue
        if refund.get("currency") != dispute_amount.currency:
            continue
        total += _decimal(refund.get("amount"), "refund amount")
    if total <= 0:
        return None
    return Money(amount=min(total, dispute_amount.amount), currency=dispute_amount.currency)


_AI_ACTIONS = {"Challenge": Action.CHALLENGE, "Accept": Action.ACCEPT}


def ai_recommendation_from_wire(data: Any) -> AiRecommendation | None:  # noqa: ANN401
    """`ai_dispute_automation` -> AiRecommendation; anything malformed is simply absent.

    TODO(verify-airwallex-docs): the reference does not give the item format of
    evidence_to_submit; strings are kept as they are and anything else as compact JSON.
    """
    if not isinstance(data, dict) or not _text(data, "status"):
        return None
    rec = data.get("recommendation") if isinstance(data.get("recommendation"), dict) else {}
    raw = _text(rec, "action")
    items = rec.get("evidence_to_submit") if isinstance(rec.get("evidence_to_submit"), list) else []
    return AiRecommendation(
        status=data["status"],
        unavailable_reason=_text(data, "unavailable_reason"),
        action=_AI_ACTIONS.get(raw) if raw else None,
        action_raw=raw,
        evidence_to_submit=tuple(
            i if isinstance(i, str) else json.dumps(i, sort_keys=True, default=str) for i in items
        ),
    )


def _unreadable(what: str, call: Any) -> Any:  # noqa: ANN401
    """Any value the domain models refuse (precision, currency, naive or unordered timestamps,
    negative amounts) makes the record unreadable: surfaced, never guessed, never a crash."""
    try:
        return call()
    except UnmappableRecord:
        raise
    except (ValueError, ArithmeticError) as e:  # includes pydantic's ValidationError
        raise UnmappableRecord(f"{what}: {str(e).splitlines()[0][:200]}") from e


def dispute_from_wire(
    data: dict[str, Any], *, merchant_id: str, refunds: list[dict[str, Any]] | None = None
) -> Dispute:
    return _unreadable(
        f"dispute {data.get('id')!r}",
        lambda: _dispute_from_wire(data, merchant_id=merchant_id, refunds=refunds),
    )


def _dispute_from_wire(
    data: dict[str, Any], *, merchant_id: str, refunds: list[dict[str, Any]] | None = None
) -> Dispute:
    what = f"dispute {data.get('id')!r}"
    amount = Money(
        amount=_decimal(data.get("amount"), f"{what} amount"),
        currency=_required(data, "currency", what),
    )
    reason = data.get("reason") if isinstance(data.get("reason"), dict) else {}
    brand = _text(data, "card_brand")
    return Dispute(
        id=_required(data, "id", what),
        merchant_id=merchant_id,
        payment_intent_id=_required(data, "payment_intent_id", what),
        payment_attempt_id=_text(data, "payment_attempt_id"),
        merchant_order_id=_text(data, "merchant_order_id"),
        stage=_enum(Stage, data.get("stage"), f"{what} stage"),
        status=_enum(DisputeStatus, data.get("status"), f"{what} status"),
        reason=DisputeReason(
            type=_text(reason, "type"),
            original_code=_text(reason, "original_code"),
            description=_text(reason, "description"),
        ),
        card_brand=None if brand is None else _enum(CardBrand, brand.lower(), f"{what} brand"),
        amount=amount,
        refunded_amount=refunded_total(refunds or [], amount),
        # TODO(verify-airwallex-docs): created_at is taken as when the dispute was received.
        opened_at=parse_time(_required(data, "created_at", what)),
        due_at=parse_time(_required(data, "due_at", what)),
        ai_recommendation=ai_recommendation_from_wire(data.get("ai_dispute_automation")),
    )


# 3DS: authentication_data.ds_data.pa_res_status (Y, N, U, A, C, R, null).
_PA_RES = {
    "Y": ThreeDSResult.AUTHENTICATED,
    "A": ThreeDSResult.ATTEMPTED,
    "N": ThreeDSResult.FAILED,
    "R": ThreeDSResult.FAILED,
}
# TODO(verify-airwallex-docs): the reference documents avs_result and cvc_result without a
# value list; its example shows "pass". Anything not listed here is UNKNOWN (fails closed).
_AVS = {"pass": AvsResult.MATCH, "fail": AvsResult.NO_MATCH}
_CVC = {"pass": CvcResult.MATCH, "fail": CvcResult.NO_MATCH}


def _three_ds(auth: dict[str, Any]) -> ThreeDSResult:
    ds = auth.get("ds_data")
    if auth.get("authentication_type") == "none" or not isinstance(ds, dict) or not ds:
        return ThreeDSResult.NOT_PERFORMED
    return _PA_RES.get(ds.get("pa_res_status"), ThreeDSResult.UNKNOWN)


def attempt_from_wire(data: dict[str, Any], *, merchant_id: str) -> PaymentAttempt:
    return _unreadable(
        f"payment attempt {data.get('id')!r}",
        lambda: _attempt_from_wire(data, merchant_id=merchant_id),
    )


def _attempt_from_wire(data: dict[str, Any], *, merchant_id: str) -> PaymentAttempt:
    what = f"payment attempt {data.get('id')!r}"
    auth = (
        data.get("authentication_data") if isinstance(data.get("authentication_data"), dict) else {}
    )
    avs = auth.get("avs_result")
    cvc = auth.get("cvc_result")
    return PaymentAttempt(
        id=_required(data, "id", what),
        merchant_id=merchant_id,
        payment_intent_id=_required(data, "payment_intent_id", what),
        amount=Money(
            amount=_decimal(data.get("amount"), f"{what} amount"),
            currency=_required(data, "currency", what),
        ),
        three_ds=_three_ds(auth),
        avs=_AVS.get(avs.lower() if isinstance(avs, str) else avs, AvsResult.UNKNOWN),
        cvc=_CVC.get(cvc.lower() if isinstance(cvc, str) else cvc, CvcResult.UNKNOWN),
        created_at=parse_time(_required(data, "created_at", what)),
        raw={"status": data.get("status"), "authentication_data": auth},
    )
