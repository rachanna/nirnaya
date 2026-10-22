"""Blocking checks. Each is a pure function returning the failures it finds (empty = pass)."""

from collections.abc import Mapping, Sequence
from zoneinfo import ZoneInfo

from nirnaya.evidence.catalogue import (
    DENIAL_FACTS,
    REASON_SPECIFIC,
    REQUIRED_EVIDENCE,
    record_establishes,
    render_claim_text,
)
from nirnaya.models.claim import Claim, DateTimeValue, DateValue, MoneyValue, TextValue
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AmountBasis, EvidenceType, ReasonCategory, RecordAuthor
from nirnaya.models.evidence import EvidenceRecord, FieldValue, raw_text
from nirnaya.models.money import FxTable, MissingFxRateError, convert
from nirnaya.verifier.normalise import (
    contains_tokens,
    name_tokens,
    normalise_enum,
    normalise_identifier,
    parse_date_only,
    parse_instant,
    parse_money,
)
from nirnaya.verifier.result import CheckName, Failure


def _fail(check: CheckName, detail: str) -> list[Failure]:
    return [Failure(check=check, detail=detail)]


def check_claim_scope(claim: Claim, dispute: Dispute) -> list[Failure]:
    if claim.case_id != dispute.id or claim.merchant_id != dispute.merchant_id:
        return _fail(CheckName.CASE_MERCHANT_MISMATCH, "claim is not for this case and merchant")
    return []


def check_record_scope(record: EvidenceRecord, dispute: Dispute) -> list[Failure]:
    # Deliberately says nothing about the foreign record's content or owner.
    if record.case_id != dispute.id or record.merchant_id != dispute.merchant_id:
        return _fail(
            CheckName.CASE_MERCHANT_MISMATCH,
            f"record {record.id!r} does not belong to this case and merchant",
        )
    return []


def check_hash(record: EvidenceRecord, expected_hashes: Mapping[str, str]) -> list[Failure]:
    failures: list[Failure] = []
    if not record.hash_ok():
        failures += _fail(CheckName.HASH_MISMATCH, "record content no longer matches its hash")
    expected = expected_hashes.get(record.id)
    if expected is not None and expected != record.sha256:
        failures += _fail(CheckName.HASH_MISMATCH, "record hash differs from the captured hash")
    return failures


def check_field(claim: Claim, record: EvidenceRecord) -> list[Failure]:
    raw = raw_text(record.fields.get(claim.source_field))
    if raw is None or raw == "":
        return _fail(
            CheckName.FIELD_MISSING,
            f"record {record.id!r} has no value for field {claim.source_field!r}",
        )
    if claim.extracted_value != raw:
        return _fail(
            CheckName.EXTRACTED_VALUE_MISMATCH,
            f"extracted value {claim.extracted_value!r} is not the record's value {raw!r}",
        )
    return []


def check_value(
    claim: Claim, record: EvidenceRecord, dispute: Dispute, fx: FxTable | None
) -> list[Failure]:
    value = record.fields.get(claim.source_field)
    match claim.normalised_value:
        case MoneyValue() as mv:
            return _check_money(mv, value, record, dispute, fx)
        case DateTimeValue(at=at):
            try:
                instant = parse_instant(value)
            except ValueError as exc:
                return _fail(CheckName.DATE_MISMATCH, str(exc))
            if instant != at:
                return _fail(
                    CheckName.DATE_MISMATCH,
                    f"record instant {instant.isoformat()} is not {at.isoformat()}",
                )
            return []
        case DateValue(date=claimed, tz=tz):
            record_date = parse_date_only(value)
            if record_date is None:
                try:
                    record_date = parse_instant(value).astimezone(ZoneInfo(tz)).date()
                except ValueError as exc:
                    return _fail(CheckName.DATE_MISMATCH, str(exc))
            if record_date != claimed:
                return _fail(
                    CheckName.DATE_MISMATCH, f"record date in {tz} is {record_date}, not {claimed}"
                )
            return []
        case TextValue(kind="NAME", text=text):
            if not isinstance(value, str) or not contains_tokens(
                name_tokens(value), name_tokens(text)
            ):
                return _fail(CheckName.NAME_OR_ID_MISMATCH, f"name {text!r} is not in the record")
            return []
        case TextValue(kind="IDENTIFIER", text=text):
            if not isinstance(value, str) or normalise_identifier(value) != normalise_identifier(
                text
            ):
                return _fail(
                    CheckName.NAME_OR_ID_MISMATCH, f"identifier {text!r} is not the record's"
                )
            return []
        case TextValue(text=text):
            raw = raw_text(value) or ""
            if normalise_enum(raw) != normalise_enum(text):
                return _fail(CheckName.VALUE_MISMATCH, f"value {text!r} is not the record's")
            return []
    raise TypeError(f"unhandled value kind {claim.value_kind}")


def _check_money(
    mv: MoneyValue,
    value: FieldValue,
    record: EvidenceRecord,
    dispute: Dispute,
    fx: FxTable | None,
) -> list[Failure]:
    target = dispute.amount.currency
    if mv.money.currency != target:
        return _fail(
            CheckName.AMOUNT_MISMATCH,
            f"claimed amount must be normalised to the dispute currency {target}",
        )
    try:
        recorded = parse_money(value, record.fields.get("currency"))
        normalised, _ = convert(recorded, target, fx)
    except (ValueError, MissingFxRateError) as exc:
        return _fail(CheckName.AMOUNT_MISMATCH, str(exc))
    if mv.basis is AmountBasis.NET_OF_REFUNDS:
        normalised = normalised - dispute.refunded
    if normalised.amount != mv.money.amount:
        return _fail(
            CheckName.AMOUNT_MISMATCH,
            f"record amount normalises to {normalised} ({mv.basis}), not {mv.money}",
        )
    return []


def check_establishes(claim: Claim, record: EvidenceRecord) -> list[Failure]:
    if claim.establishes not in record_establishes(record, claim.source_field):
        return _fail(
            CheckName.EXCEEDS_RECORD,
            f"field {claim.source_field!r} of a {record.source_system} record does not "
            f"establish {claim.establishes}",
        )
    return []


def check_text(claim: Claim) -> list[Failure]:
    expected = render_claim_text(claim.establishes, claim.normalised_value)
    if claim.text != expected:
        return _fail(CheckName.TEXT_MISMATCH, f"claim text must read exactly {expected!r}")
    return []


def check_reason_specific(claim: Claim, dispute: Dispute) -> list[Failure]:
    if dispute.reason_category is ReasonCategory.UNKNOWN and claim.establishes in REASON_SPECIFIC:
        return _fail(
            CheckName.REASON_SPECIFIC_UNDER_UNKNOWN,
            f"{claim.establishes} is specific to {REASON_SPECIFIC[claim.establishes]}; the "
            "reason is unknown",
        )
    return []


def check_denial(
    claim: Claim, case_records: Sequence[EvidenceRecord], dispute: Dispute
) -> list[Failure]:
    """A claim must not deny that a complaint exists when the customer wrote before the dispute."""
    if claim.establishes not in DENIAL_FACTS:
        return []
    for record in case_records:
        if record.author is not RecordAuthor.CUSTOMER:
            continue
        try:
            prior = parse_instant(record.fields.get("sent_at")) < dispute.opened_at
        except ValueError:
            prior = True  # undated: assume it may predate the dispute
        if prior:
            return _fail(
                CheckName.DENIES_CUSTOMER_COMPLAINT,
                f"customer record {record.id!r} predates the dispute; the draft must not deny it",
            )
    return []


def check_required_evidence(
    dispute: Dispute, established: frozenset[EvidenceType]
) -> list[Failure]:
    failures: list[Failure] = []
    for group in REQUIRED_EVIDENCE[dispute.reason_category]:
        if not group & established:
            failures += _fail(
                CheckName.REQUIRED_EVIDENCE_MISSING,
                f"{dispute.reason_category} needs one of {sorted(group)} established",
            )
    return failures
