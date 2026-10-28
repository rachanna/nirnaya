"""Regression tests for the /code-review findings on audit batch 2 (dates, escalation, detail).

Each test is named after its finding number.
"""

from datetime import UTC, datetime, timedelta

import pytest

from nirnaya.evidence import derive
from nirnaya.evidence.derive import contradictions
from nirnaya.models import (
    EvidenceRecord,
    ReasonCategory,
    RecordAuthor,
    SourceSystem,
    ValidationStatus,
)
from nirnaya.models.timestamps import parse_instant
from nirnaya.orchestrator.states import CaseState
from nirnaya.store import Store, connect
from nirnaya.store import db as store_db
from nirnaya.verifier import VerificationOutcome, verify
from tests.factories import carrier_record, make_claim, make_dispute, make_record
from tests.harness import CASES, Harness
from tests.test_audit_fixes import M, orch, world_with
from tests.verifier.helpers import order_record, run

# --- shared builders ---------------------------------------------------------------------------


def login_record(**fields) -> EvidenceRecord:
    return make_record(
        SourceSystem.LOGIN,
        {"customer_id": "CUST-1", **fields},
        id="login-1",
        author=RecordAuthor.MERCHANT,
    )


def policy_record(accepted_at: str) -> EvidenceRecord:
    return make_record(
        SourceSystem.POLICY,
        {"customer_id": "CUST-1", "cancellation_terms_accepted_at": accepted_at},
        id="policy-1",
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


def tampered(record: EvidenceRecord, **fields) -> EvidenceRecord:
    """The record with fields edited after capture: its stored hash no longer matches."""
    return record.model_copy(update={"fields": {**record.fields, **fields}})


# --- #1: anchor on the disputed order ----------------------------------------------------------


def renewal_case(merchant_order_id):
    dispute = make_dispute(
        reason_category=ReasonCategory.SUBSCRIPTION, merchant_order_id=merchant_order_id
    )
    signup = order_record("order-1", order_id="ORD-SIGNUP", placed_at="2025-11-03T00:00:00Z")
    renewal = order_record("order-2", order_id="ORD-RENEW", placed_at="2026-02-10T09:00:00Z")
    usage_at = "2026-02-02T08:00:00Z"  # after the signup, before the renewal
    records = [
        signup,
        renewal,
        policy_record("2025-11-03T00:00:00Z"),
        login_record(active_after_renewal_at=usage_at),
    ]
    claims = [
        dated_claim(
            "c-terms", "policy-1", "cancellation_terms_accepted_at",
            "CANCELLATION_TERMS_ACCEPTED", "2025-11-03T00:00:00Z",
        ),
        dated_claim(
            "c-usage", "login-1", "active_after_renewal_at", "USAGE_AFTER_RENEWAL", usage_at
        ),
    ]  # fmt: skip
    return run(claims, records, dispute)


def test_1_usage_before_the_disputed_renewal_clashes_even_with_an_earlier_signup_order():
    result = renewal_case("ORD-RENEW")
    statuses = {c.claim.id: c.claim.validation_status for c in result.claims}
    assert statuses["c-usage"] is ValidationStatus.CONFLICT
    assert result.outcome is VerificationOutcome.REFER


def test_1_without_a_disputed_order_reference_the_anchors_stay_lenient():
    assert renewal_case(None).outcome is VerificationOutcome.PASSED


def test_1_delivery_before_the_disputed_order_clashes():
    dispute = make_dispute(merchant_order_id="ORD-1002")
    records = [
        order_record(),  # ORD-1001, placed 2026-02-15T01:00Z
        order_record("order-2", order_id="ORD-1002", placed_at="2026-02-25T00:00:00Z"),
        carrier_record(),  # delivered 2026-02-20T20:30Z
    ]
    assert run([make_claim()], records, dispute).outcome is VerificationOutcome.REFER


# --- #2: every reason a record contradicts a claim is surfaced ---------------------------------


def test_2_a_rule_contradiction_and_a_date_clash_on_one_record_are_both_surfaced():
    dispute = make_dispute(reason_category=ReasonCategory.SUBSCRIPTION)
    usage_at = "2026-02-02T08:00:00Z"  # before the order (2026-02-15)
    records = [
        order_record(),
        login_record(active_after_renewal_at=usage_at, cancelled_before_renewal=True),
    ]
    claim = dated_claim(
        "c-usage", "login-1", "active_after_renewal_at", "USAGE_AFTER_RENEWAL", usage_at
    )
    result = run([claim], records, dispute)
    descriptions = [c.description for c in result.counterevidence if c.record_id == "login-1"]
    assert len(descriptions) == 2
    assert any("cancelled before renewal" in d for d in descriptions)
    assert any("predates the order" in d for d in descriptions)


# --- #3: tampered records neither anchor nor clash ---------------------------------------------


def test_3_a_tampered_uncited_order_cannot_move_the_anchor():
    records = [
        order_record(),  # intact, placed 2026-02-15T01:00Z
        tampered(order_record("order-2", order_id="ORD-2"), placed_at="2026-01-01T00:00:00Z"),
        carrier_record(delivered_at="2026-02-08T10:00:00Z"),
    ]
    assert run([make_claim()], records).outcome is VerificationOutcome.REFER


def test_3_a_tampered_record_never_creates_a_date_clash():
    records = [
        order_record(),
        carrier_record(),
        tampered(carrier_record("carrier-2"), delivered_at="2026-02-08T10:00:00Z"),
    ]
    assert run([make_claim()], records).outcome is VerificationOutcome.PASSED
    assert derive.contradictory_record_ids(records, make_dispute()) == ()


# --- #4: migrations are transactional and step-wise --------------------------------------------


def make_v1(path):
    conn = connect(path)
    conn.execute("ALTER TABLE referrals DROP COLUMN detail_json")
    conn.execute("UPDATE schema_version SET version = 1")
    conn.close()


def version_and_columns(conn):
    version = conn.execute("SELECT version FROM schema_version").fetchall()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(referrals)")}
    return [r["version"] for r in version], columns


def test_4_a_migration_interrupted_after_the_alter_still_completes(tmp_path):
    path = tmp_path / "half.db"
    conn = connect(path)
    conn.execute("UPDATE schema_version SET version = 1")  # column present, version not bumped
    conn.close()
    conn = connect(path)
    versions, columns = version_and_columns(conn)
    assert versions == [store_db.SCHEMA_VERSION] and "detail_json" in columns
    conn.close()


def test_4_successive_openers_of_a_v1_database_both_succeed(tmp_path):
    path = tmp_path / "old.db"
    make_v1(path)
    first, second = connect(path), connect(path)
    for conn in (first, second):
        versions, columns = version_and_columns(conn)
        assert versions == [store_db.SCHEMA_VERSION] and "detail_json" in columns
    first.close()
    second.close()


def test_4_migrations_run_one_version_at_a_time(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    make_v1(path)

    def to_v3(conn):
        conn.execute("CREATE TABLE future_table (x INTEGER)")

    future = store_db.SCHEMA_VERSION + 1
    monkeypatch.setattr(store_db, "SCHEMA_VERSION", future)
    monkeypatch.setitem(store_db.MIGRATIONS, future - 1, to_v3)
    conn = connect(path)
    versions, columns = version_and_columns(conn)
    assert versions == [future] and "detail_json" in columns
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'future_table'").fetchone()
    conn.close()


def test_4_a_new_database_has_exactly_one_version_row(tmp_path):
    conn = connect(tmp_path / "new.db")
    assert version_and_columns(conn)[0] == [store_db.SCHEMA_VERSION]
    conn.close()


# --- #5: late-evidence notes are recorded on the delegated path --------------------------------


def test_5_a_delegated_challenge_with_late_evidence_is_submitted_and_the_note_recorded():
    h = Harness(delegated=True)
    case = CASES["N1"]
    h.stage(case)
    # Delivered after the dispute opened (12:00Z), before capture (13:00Z).
    o = orch(h, systems=world_with({"N1-carrier": {"delivered_at": "2026-03-01T12:30:00Z"}}))
    o.run_until_idle(h.now(case))
    (write,) = h.store.writes(M, "N1")
    assert (write.action, write.status) == ("CHALLENGE", "DRY_RUN")
    (event,) = [e for e in h.store.audit_events(M, "N1") if e["event"] == "SUBMITTED_WITH_NOTES"]
    assert event["body"]["request_id"] == write.request_id
    (note,) = event["body"]["notes"]
    assert note["record_id"] == "N1-carrier" and "after the dispute opened" in note["description"]


def test_5_a_challenge_without_notes_records_no_note_event():
    h = Harness()
    case = CASES["N1"]
    h.stage(case)
    orch(h).run_until_idle(h.now(case))
    events = [e["event"] for e in h.store.audit_events(M, "N1")]
    assert "SUBMITTED_WITH_NOTES" not in events


# --- #6: the engine referral says why the records contradict -----------------------------------


def test_6_contradictions_carry_their_description():
    records = [order_record(), carrier_record(delivered_at="2026-02-08T10:00:00Z")]
    (c,) = contradictions(records, make_dispute())
    assert (c.record_id, c.contradicts) == ("carrier-1", "DELIVERY_CONFIRMED")
    assert "predates the order" in c.description


def test_6_the_referral_detail_names_the_date_clash():
    h = Harness()
    case = CASES["N1"]
    h.stage(case)
    o = orch(h, systems=world_with({"N1-carrier": {"delivered_at": "2026-02-08T10:00:00Z"}}))
    o.run_until_idle(h.now(case))
    detail = h.store.open_referral_for(M, "N1").detail
    (c,) = detail["contradictions"]
    assert c["record_id"] == "N1-carrier" and "predates the order" in c["description"]


# --- #7: tenant scoping in the store -----------------------------------------------------------


def test_7_referral_ids_and_counts_are_scoped_by_merchant():
    from nirnaya.config.demo import DEMO_POLICY

    store = Store(connect())
    other = DEMO_POLICY.model_copy(update={"merchant_id": "merchant-other"})
    for policy in (DEMO_POLICY, other):
        store.register_merchant(
            policy.merchant_id,
            airwallex_account_id=f"acct-{policy.merchant_id}",
            policy=policy,
            delegated_authority=True,
        )
    at = datetime(2026, 3, 1, tzinfo=UTC)
    with store.transaction():
        a1 = store.open_referral(DEMO_POLICY.merchant_id, "c1", 1, ["X"], False, at)
        a2 = store.open_referral(DEMO_POLICY.merchant_id, "c1", 1, ["X"], False, at)
        b1 = store.open_referral("merchant-other", "c1", 1, ["X"], False, at)
    assert len({a1, a2, b1}) == 3
    assert b1.endswith(":ref:1")  # merchant-other's count ignores the other tenant's referrals


# --- #8: date clashes are computed once per case -----------------------------------------------


def test_8_verify_scans_the_case_dates_once(monkeypatch):
    calls = []
    original = derive._case_dates

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(derive, "_case_dates", spy)
    claims = [make_claim(id=f"claim-{i}") for i in range(5)]
    verify(make_dispute(), claims, [order_record(), carrier_record()])
    assert len(calls) == 1


# --- #9: escalation reads only what it may escalate --------------------------------------------


def test_9_escalatable_referrals_skips_urgent_and_resolved_referrals():
    h = Harness()
    case = CASES["K3"]
    h.stage(case)
    o = orch(h)
    o.run_until_idle(h.now(case))
    rows = h.store.escalatable_referrals(M)
    assert [r.case_id for r in rows] == ["K3"]
    o.tick(case.dispute.due_at - timedelta(days=1))
    assert h.store.escalatable_referrals(M) == []


# --- #10: one timestamp parser, UTC everywhere -------------------------------------------------


def test_10_descriptions_are_in_utc():
    records = [order_record(), carrier_record(delivered_at="2026-02-08T05:00:00-05:00")]
    (c,) = contradictions(records, make_dispute())
    assert "2026-02-08T10:00:00Z" in c.description and "+08:00" not in c.description


def test_10_the_shared_parser_is_the_verifiers():
    from nirnaya.verifier import normalise

    assert normalise.parse_instant is parse_instant
    with pytest.raises(ValueError):
        parse_instant("2026-02-08")


# =================================================================================================
# Second review (commits 8b5402e, eb34cfa)
# =================================================================================================


# --- R2#1: an out-of-range timestamp is unreadable, never a crash ------------------------------

FAR_PAST = "0001-01-01T00:00:00+05:00"  # converting to UTC overflows


def test_r2_1_parse_instant_turns_overflow_into_value_error():
    with pytest.raises(ValueError):
        parse_instant(FAR_PAST)


def test_r2_1_derive_and_verify_treat_an_overflowing_timestamp_as_unreadable():
    records = [order_record(), carrier_record(delivered_at=FAR_PAST)]
    result = run([make_claim()], records)
    assert result.outcome is VerificationOutcome.REFER
    assert "unreadable" in result.counterevidence[0].description
    assert derive.contradictory_record_ids(records, make_dispute()) == ("carrier-1",)


# --- R2#2: a new cycle closes the old cycle's referral; escalation sees only the current one ---


def escalated_u4():
    h = Harness()
    case = CASES["U4"]  # RFI, referred (an RFI accept needs approval)
    h.stage(case)
    o = orch(h)
    now = h.now(case)
    o.run_until_idle(now)
    old = h.store.open_referral_for(M, "U4")
    assert old is not None and old.cycle == 1
    h.aw.simulate_escalate("U4", due_at=now + timedelta(days=20))
    o.run_until_idle(now + timedelta(hours=1))
    return h, old, now


def test_r2_2_a_new_cycle_supersedes_the_previous_cycles_open_referral():
    h, old, _ = escalated_u4()
    row = h.store.conn.execute(
        "SELECT resolution FROM referrals WHERE referral_id = ?", (old.referral_id,)
    ).fetchone()
    assert row["resolution"] == "SUPERSEDED_BY_NEW_CYCLE"
    current = h.store.open_referral_for(M, "U4")
    assert current is None or current.cycle == 2


def test_r2_2_escalation_ignores_referrals_of_earlier_cycles():
    h, old, now = escalated_u4()
    # Force the stale cycle-1 referral open again on a case now referred in cycle 2.
    with h.store.transaction():
        h.store.conn.execute(
            "UPDATE referrals SET resolution = NULL, resolved_at = NULL WHERE referral_id = ?",
            (old.referral_id,),
        )
        h.store.update_case(M, "U4", state=CaseState.REFERRED, at=now)
    assert old.referral_id not in {r.referral_id for r in h.store.escalatable_referrals(M)}


# --- R2#3: each record is anchored to its own order --------------------------------------------


def linked_case(carrier_tracking):
    dispute = make_dispute(merchant_order_id="ORD-NEW")
    records = [
        order_record(
            "order-new", order_id="ORD-NEW", placed_at="2026-02-25T00:00:00Z",
            tracking_number="T-NEW",
        ),
        order_record(
            "order-old", order_id="ORD-OLD", placed_at="2026-02-01T00:00:00Z",
            tracking_number="T-OLD",
        ),
        carrier_record(tracking_number=carrier_tracking, delivered_at="2026-02-05T00:00:00Z"),
    ]  # fmt: skip
    return run([make_claim()], records, dispute)


def test_r2_3_a_delivery_for_an_earlier_order_on_file_is_anchored_to_that_order():
    assert linked_case("T-OLD").outcome is VerificationOutcome.PASSED


def test_r2_3_an_unlinkable_delivery_falls_back_to_the_disputed_order():
    assert linked_case("T-UNKNOWN").outcome is VerificationOutcome.REFER


def test_r2_3_a_delivery_linked_to_the_disputed_order_is_checked_against_it():
    assert linked_case("T-NEW").outcome is VerificationOutcome.REFER


# --- R2#4: the note event states the authority behind the write --------------------------------


def test_r2_4_an_approved_challenge_with_late_evidence_records_the_approver():
    from nirnaya.models import Action
    from nirnaya.orchestrator.referrals import approve

    h = Harness(delegated=False)
    case = CASES["N1"]
    h.stage(case)
    o = orch(h, systems=world_with({"N1-carrier": {"delivered_at": "2026-03-01T12:30:00Z"}}))
    now = h.now(case)
    o.run_until_idle(now)
    assert h.store.case(M, "N1").state == CaseState.REFERRED  # no delegated authority
    approve(h.store, M, "N1", Action.CHALLENGE, approver="ops@example.test", now=now)
    o.run_until_idle(now)
    (event,) = [e for e in h.store.audit_events(M, "N1") if e["event"] == "SUBMITTED_WITH_NOTES"]
    assert event["body"]["authority"] == "APPROVED"
    assert event["body"]["approver"] == "ops@example.test"


def test_r2_4_a_delegated_challenge_says_so():
    h = Harness(delegated=True)
    case = CASES["N1"]
    h.stage(case)
    o = orch(h, systems=world_with({"N1-carrier": {"delivered_at": "2026-03-01T12:30:00Z"}}))
    o.run_until_idle(h.now(case))
    (event,) = [e for e in h.store.audit_events(M, "N1") if e["event"] == "SUBMITTED_WITH_NOTES"]
    assert event["body"]["authority"] == "DELEGATED" and event["body"]["approver"] is None


# --- R2#5: a record id taken by another merchant is a StoreError, never revised -----------------


def two_tenant_store():
    from nirnaya.config.demo import DEMO_POLICY

    store = Store(connect())
    for policy in (DEMO_POLICY, DEMO_POLICY.model_copy(update={"merchant_id": "merchant-other"})):
        store.register_merchant(
            policy.merchant_id,
            airwallex_account_id=f"acct-{policy.merchant_id}",
            policy=policy,
            delegated_authority=True,
        )
    return store


def test_r2_5_a_cross_tenant_record_id_collision_is_a_store_error():
    from nirnaya.store.repository import StoreError

    store = two_tenant_store()
    mine = carrier_record()
    theirs = make_record(
        SourceSystem.CARRIER, dict(mine.fields), id=mine.id, merchant_id="merchant-other"
    )
    at = datetime(2026, 3, 2, tzinfo=UTC)
    with store.transaction():
        store.put_evidence(mine.merchant_id, mine)
    with pytest.raises(StoreError, match="another merchant"):
        store.put_evidence("merchant-other", theirs)
    with pytest.raises(StoreError, match="another merchant"):
        store.put_evidence_or_revise("merchant-other", theirs, at)
    assert store.evidence("merchant-other", mine.case_id) == []


# --- R2#6: the referral's descriptions are the decision's ---------------------------------------


def test_r2_6_contradictions_are_recorded_inputs_that_never_change_the_action():
    from nirnaya.engine.core import evaluate
    from tests.factories import make_policy

    kwargs = dict(ownership_resolved=True, contradictory_record_ids=["carrier-1"])
    plain = evaluate(make_dispute(), make_policy(), frozenset(), **kwargs)
    described = evaluate(
        make_dispute(),
        make_policy(),
        frozenset(),
        contradictions=[("carrier-1", "DELIVERY_CONFIRMED", "delivery scan predates the order")],
        **kwargs,
    )
    assert described.action is plain.action
    assert described.inputs["contradictions"] == [
        {
            "record_id": "carrier-1",
            "contradicts": "DELIVERY_CONFIRMED",
            "description": "delivery scan predates the order",
        }
    ]


def test_r2_6_the_referral_detail_is_the_decisions_record():
    h = Harness()
    case = CASES["N1"]
    h.stage(case)
    o = orch(h, systems=world_with({"N1-carrier": {"delivered_at": "2026-02-08T10:00:00Z"}}))
    o.run_until_idle(h.now(case))
    _, decision = h.store.latest_decision(M, "N1", 1)
    detail = h.store.open_referral_for(M, "N1").detail
    assert detail["contradictions"] == decision.inputs["contradictions"]
    assert detail["contradictions"][0]["record_id"] == "N1-carrier"


# --- R2#9: one date-only rule -------------------------------------------------------------------


def test_r2_9_the_date_only_rule_is_shared():
    from nirnaya.models import timestamps
    from nirnaya.verifier import normalise

    assert normalise.parse_date_only is timestamps.parse_date_only


# --- R2#10: an up-to-date database is opened without a write lock -------------------------------


def test_r2_10_connect_does_not_wait_for_a_writer_when_the_schema_is_current(tmp_path):
    import time

    path = tmp_path / "busy.db"
    writer = connect(path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        reader = connect(path)
        assert time.monotonic() - started < 1.0
        reader.close()
    finally:
        writer.execute("ROLLBACK")
        writer.close()
