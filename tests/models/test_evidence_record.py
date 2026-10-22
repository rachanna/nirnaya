from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from nirnaya.models import (
    AvsResult,
    CvcResult,
    EvidenceRecord,
    PaymentAttempt,
    RecordAuthor,
    SourceSystem,
    ThreeDSResult,
)
from nirnaya.models.evidence import raw_text
from tests.factories import CASE, MERCHANT, T0, make_record, usd


def test_create_stamps_a_valid_hash():
    rec = make_record(SourceSystem.ORDER, {"order_id": "o-1"})
    assert len(rec.sha256) == 64
    assert rec.hash_ok()


def test_hash_is_independent_of_field_insertion_order():
    a = make_record(SourceSystem.ORDER, {"a": "1", "b": "2"})
    b = make_record(SourceSystem.ORDER, {"b": "2", "a": "1"})
    assert a.sha256 == b.sha256


@pytest.mark.parametrize(
    "update",
    [
        {"fields": {"order_id": "o-2"}},
        {"merchant_id": "merchant-other"},
        {"case_id": "case-2"},
        {"author": RecordAuthor.CUSTOMER},
        {"source_system": SourceSystem.CARRIER},
        {"captured_at": datetime(2030, 1, 1, tzinfo=UTC)},
        {"synthetic": False},
    ],
)
def test_any_change_breaks_the_hash(update):
    rec = make_record(SourceSystem.ORDER, {"order_id": "o-1"})
    tampered = rec.model_copy(update=update)
    assert not tampered.hash_ok()


def test_hash_is_stable_across_equal_instants_in_different_offsets():
    a = make_record(SourceSystem.ORDER, {"x": "1"})
    b = EvidenceRecord.create(
        **a.model_dump(exclude={"sha256", "captured_at"}),
        captured_at="2026-03-02T17:30:00+05:30",
    )
    assert a.captured_at == b.captured_at
    assert a.sha256 == b.sha256


def test_float_field_values_are_rejected():
    with pytest.raises(ValidationError):
        make_record(SourceSystem.ORDER, {"total_amount": 10.5})


def test_bad_hash_format_is_rejected():
    rec = make_record(SourceSystem.ORDER, {"x": "1"})
    with pytest.raises(ValidationError):
        EvidenceRecord(**{**rec.model_dump(), "sha256": "not-a-hash"})


@pytest.mark.parametrize(
    ("value", "expected"), [("a", "a"), (True, "true"), (False, "false"), (3, "3"), (None, None)]
)
def test_raw_text(value, expected):
    assert raw_text(value) == expected


def test_payment_attempt_snapshot_is_hashed_third_party_evidence():
    attempt = PaymentAttempt(
        id="att-1",
        merchant_id=MERCHANT,
        payment_intent_id="int-1",
        amount=usd("200.00"),
        three_ds=ThreeDSResult.AUTHENTICATED,
        avs=AvsResult.MATCH,
        cvc=CvcResult.NO_MATCH,
        created_at=T0,
    )
    rec = attempt.to_evidence_record(case_id=CASE, captured_at=T0, synthetic=True)
    assert rec.hash_ok()
    assert rec.source_system is SourceSystem.PAYMENT_ATTEMPT
    assert rec.fields["three_ds"] == "AUTHENTICATED"
    assert rec.fields["cvc"] == "NO_MATCH"
    assert D(rec.fields["amount"]) == D("200")
