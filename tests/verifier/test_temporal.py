"""Dates must be consistent across records (audit finding M3).

A merchant record whose date is impossible against another record or the dispute is a
contradiction between records: the claim is CONFLICT and a person decides. Evidence dated
after the dispute opened still counts, but the approver is told.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from nirnaya.models import (
    EvidenceRecord,
    ReasonCategory,
    RecordAuthor,
    SourceSystem,
    ValidationStatus,
)
from nirnaya.verifier import VerificationOutcome
from tests.factories import T0, carrier_record, make_claim, make_dispute, make_record
from tests.verifier.helpers import order_record, run

# Defaults: order placed 2026-02-15T01:00Z, delivered 2026-02-20T20:30Z, dispute opened
# 2026-03-01T12:00Z, records captured 2026-03-02T12:00Z.


def delivery_records(**carrier: Any) -> list[EvidenceRecord]:
    return [order_record(), carrier_record(**carrier)]


def only_claim(result):
    (cr,) = result.claims
    return cr


def test_consistent_dates_pass():
    result = run([make_claim()], delivery_records())
    assert result.outcome is VerificationOutcome.PASSED
    assert result.notes == ()


def test_delivery_before_the_order_is_a_conflict():
    result = run([make_claim()], delivery_records(delivered_at="2026-02-08T10:00:00Z"))
    cr = only_claim(result)
    assert cr.failures == ()
    assert cr.claim.validation_status is ValidationStatus.CONFLICT
    assert cr.conflicting_record_ids == ("carrier-1",)
    assert result.outcome is VerificationOutcome.REFER
    (ce,) = result.counterevidence
    assert ce.record_id == "carrier-1" and "predates the order" in ce.description


def test_the_conflict_applies_whichever_field_is_cited():
    claim = make_claim(
        source_field="delivered_at",
        extracted_value="2026-02-08T10:00:00Z",
        normalised_value={"kind": "DATETIME", "at": "2026-02-08T10:00:00Z"},
    )
    result = run([claim], delivery_records(delivered_at="2026-02-08T10:00:00Z"))
    assert only_claim(result).claim.validation_status is ValidationStatus.CONFLICT


def test_a_record_dated_after_its_own_capture_is_a_conflict():
    result = run([make_claim()], delivery_records(delivered_at="2026-03-05T10:00:00Z"))
    assert result.outcome is VerificationOutcome.REFER
    assert "after it was captured" in result.counterevidence[0].description


def test_an_unreadable_timestamp_fails_closed():
    for bad in ("last tuesday", "2026-02-20T15:30:00"):  # unparseable; no time zone
        result = run([make_claim()], delivery_records(delivered_at=bad))
        assert result.outcome is VerificationOutcome.REFER, bad
        assert "unreadable" in result.counterevidence[0].description


def test_a_carrier_record_that_establishes_nothing_never_clashes():
    records = [
        order_record(),
        carrier_record(),
        carrier_record("carrier-2", status="IN_TRANSIT", delivered_at="2026-01-01T00:00:00Z"),
    ]
    assert run([make_claim()], records).outcome is VerificationOutcome.PASSED


def test_without_an_order_record_the_order_anchored_rules_are_skipped():
    result = run([make_claim()], [carrier_record(delivered_at="2026-01-01T00:00:00Z")])
    assert result.outcome is VerificationOutcome.PASSED


def test_two_orders_use_lenient_anchors():
    # Delivered after the earlier order but before the later one: possible, so no conflict.
    records = [
        order_record(),
        order_record("order-2", order_id="ORD-1002", placed_at="2026-02-25T00:00:00Z"),
        carrier_record(),
    ]
    assert run([make_claim()], records).outcome is VerificationOutcome.PASSED


def test_an_order_placed_after_the_dispute_opened_is_a_conflict():
    claim = make_claim(
        source_record_id="order-1",
        source_field="order_id",
        establishes="ORDER_PLACED",
        extracted_value="ORD-1001",
        normalised_value={"kind": "IDENTIFIER", "text": "ORD-1001"},
    )
    result = run([claim], [order_record(placed_at="2026-03-01T13:00:00Z")])
    assert only_claim(result).claim.validation_status is ValidationStatus.CONFLICT
    assert "after the dispute opened" in result.counterevidence[0].description


def test_late_delivery_still_counts_but_the_approver_is_told():
    # Delivered after the dispute opened (12:00Z) and before capture.
    result = run([make_claim()], delivery_records(delivered_at="2026-03-01T20:00:00Z"))
    assert result.outcome is VerificationOutcome.PASSED
    (note,) = result.notes
    assert (note.record_id, note.claim_id) == ("carrier-1", "claim-1")
    assert "after the dispute opened" in note.description


# --- subscription ------------------------------------------------------------------------------


def policy_record(accepted_at: str) -> EvidenceRecord:
    return make_record(
        SourceSystem.POLICY,
        {"customer_id": "CUST-1", "cancellation_terms_accepted_at": accepted_at},
        id="policy-1",
        author=RecordAuthor.MERCHANT,
    )


def login_record(active_at: str) -> EvidenceRecord:
    return make_record(
        SourceSystem.LOGIN,
        {"customer_id": "CUST-1", "active_after_renewal_at": active_at},
        id="login-1",
        author=RecordAuthor.MERCHANT,
    )


def dated_claim(id: str, record: str, field: str, fact: str, at: str):
    return make_claim(
        id=id,
        source_record_id=record,
        source_field=field,
        establishes=fact,
        extracted_value=at,
        normalised_value={"kind": "DATETIME", "at": at},
    )


def subscription(accepted_at: str, active_at: str):
    dispute = make_dispute(reason_category=ReasonCategory.SUBSCRIPTION)
    claims = [
        dated_claim(
            "c-terms", "policy-1", "cancellation_terms_accepted_at",
            "CANCELLATION_TERMS_ACCEPTED", accepted_at,
        ),
        dated_claim(
            "c-usage", "login-1", "active_after_renewal_at", "USAGE_AFTER_RENEWAL", active_at
        ),
    ]  # fmt: skip
    records = [order_record(), policy_record(accepted_at), login_record(active_at)]
    return run(claims, records, dispute)


def status_of(result, claim_id):
    return {c.claim.id: c.claim.validation_status for c in result.claims}[claim_id]


def test_subscription_with_consistent_dates_passes():
    result = subscription("2025-11-03T10:00:00Z", "2026-02-20T08:00:00Z")
    assert result.outcome is VerificationOutcome.PASSED


def test_usage_after_renewal_dated_before_the_renewal_is_a_conflict():
    # The S1/S5 fixture data before the audit: the renewal was placed after the "usage".
    result = subscription("2025-11-03T10:00:00Z", "2026-02-02T08:00:00Z")
    assert status_of(result, "c-usage") is ValidationStatus.CONFLICT
    assert status_of(result, "c-terms") is ValidationStatus.VERIFIED
    assert result.outcome is VerificationOutcome.REFER


def test_terms_accepted_after_the_charge_is_a_conflict():
    result = subscription("2026-02-16T00:00:00Z", "2026-02-20T08:00:00Z")
    assert status_of(result, "c-terms") is ValidationStatus.CONFLICT
    assert result.outcome is VerificationOutcome.REFER


# --- property ----------------------------------------------------------------------------------

instants = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2026, 4, 1), timezones=st.just(UTC)
)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@given(placed=instants, delivered=instants)
def test_a_passed_delivery_claim_is_never_dated_impossibly(placed, delivered):
    records = [order_record(placed_at=iso(placed)), carrier_record(delivered_at=iso(delivered))]
    captured = T0 + timedelta(days=1)
    result = run([make_claim()], records)
    if result.outcome is VerificationOutcome.PASSED:
        assert placed <= delivered <= captured
    else:
        assert not placed <= delivered <= captured
