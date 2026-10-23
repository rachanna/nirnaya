from datetime import timedelta

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.models import SourceSystem
from nirnaya.store import Store, StoreError, connect, verify_chain
from nirnaya.verifier import verify
from tests.factories import (
    CASE,
    MERCHANT,
    OTHER_MERCHANT,
    T0,
    carrier_record,
    make_claim,
    make_dispute,
    make_record,
)


@pytest.fixture
def store():
    s = Store(connect())
    s.register_merchant(
        MERCHANT, airwallex_account_id="acct-demo", policy=DEMO_POLICY, delegated_authority=True
    )
    other = DEMO_POLICY.model_copy(update={"merchant_id": OTHER_MERCHANT})
    s.register_merchant(
        OTHER_MERCHANT, airwallex_account_id="acct-other", policy=other, delegated_authority=False
    )
    with s.transaction():
        s.create_case(MERCHANT, CASE, "INGESTED", T0 + timedelta(days=15), T0)
        s.start_cycle(
            MERCHANT, CASE, 1, stage="CHARGEBACK", policy_version=DEMO_POLICY.version, at=T0
        )
        s.create_case(OTHER_MERCHANT, "case-x", "INGESTED", T0 + timedelta(days=15), T0)
    return s


# --- tenant isolation -----------------------------------------------------------------------


def test_cases_are_invisible_to_other_merchants(store):
    assert store.case(MERCHANT, CASE) is not None
    assert store.case(OTHER_MERCHANT, CASE) is None
    assert [c.case_id for c in store.cases(MERCHANT)] == [CASE]
    assert [c.case_id for c in store.cases(OTHER_MERCHANT)] == ["case-x"]


def test_updates_cannot_touch_another_merchants_case(store):
    with pytest.raises(StoreError):
        store.update_case(OTHER_MERCHANT, CASE, state="CLOSED", at=T0)
    assert store.case(MERCHANT, CASE).state == "INGESTED"


def test_evidence_is_scoped_and_cannot_be_stored_for_another_merchant(store):
    store.put_evidence(MERCHANT, carrier_record())
    assert store.evidence(OTHER_MERCHANT, CASE) == []
    foreign = make_record(SourceSystem.CARRIER, {"status": "DELIVERED"}, merchant_id=OTHER_MERCHANT)
    with pytest.raises(StoreError, match="another merchant"):
        store.put_evidence(MERCHANT, foreign)


def test_snapshots_and_decisions_are_scoped(store):
    store.add_snapshot(MERCHANT, make_dispute(), T0)
    assert store.latest_dispute(OTHER_MERCHANT, CASE) is None
    d = decide(make_dispute(), DEMO_POLICY, frozenset(), ownership_resolved=True)
    store.add_decision(MERCHANT, CASE, 1, d, T0)
    assert store.latest_decision(OTHER_MERCHANT, CASE, 1) is None


def test_dispute_of_another_merchant_cannot_be_snapshotted(store):
    with pytest.raises(StoreError):
        store.add_snapshot(OTHER_MERCHANT, make_dispute(), T0)


def test_case_owner_reports_the_owning_merchant(store):
    assert store.case_owner(CASE) == MERCHANT
    assert store.case_owner("nope") is None


# --- round trips ----------------------------------------------------------------------------


def test_domain_objects_round_trip(store):
    dispute = make_dispute()
    store.add_snapshot(MERCHANT, dispute, T0)
    assert store.latest_dispute(MERCHANT, CASE) == dispute

    rec = carrier_record()
    assert store.put_evidence(MERCHANT, rec) is True
    assert store.evidence(MERCHANT, CASE) == [rec]
    assert store.capture_hashes(MERCHANT, CASE) == {rec.id: rec.sha256}

    decision = decide(dispute, DEMO_POLICY, frozenset(), ownership_resolved=True)
    first = store.add_decision(MERCHANT, CASE, 1, decision, T0)
    second = store.add_decision(MERCHANT, CASE, 1, decision, T0)
    assert (first, second) == (f"{CASE}:1:1", f"{CASE}:1:2")
    assert store.latest_decision(MERCHANT, CASE, 1) == (second, decision)

    claims = [make_claim()]
    draft = store.put_claims(MERCHANT, CASE, 1, claims)
    assert store.claims(MERCHANT, CASE, 1, draft) == claims
    result = verify(dispute, claims, [rec])
    store.put_verification(MERCHANT, CASE, 1, draft, result)
    assert store.verification(MERCHANT, CASE, 1, draft) == result


def test_policy_round_trips_by_version(store):
    assert store.policy(MERCHANT, DEMO_POLICY.version) == DEMO_POLICY
    assert store.merchant(MERCHANT).delegated_authority is True


def test_evidence_is_immutable(store):
    rec = carrier_record()
    store.put_evidence(MERCHANT, rec)
    assert store.put_evidence(MERCHANT, rec) is False  # identical: no-op
    changed = carrier_record(status="LOST")
    with pytest.raises(StoreError, match="changed after capture"):
        store.put_evidence(MERCHANT, changed)


def test_drafts_are_numbered_per_cycle(store):
    assert store.put_claims(MERCHANT, CASE, 1, [make_claim()]) == 1
    assert store.put_claims(MERCHANT, CASE, 1, [make_claim()]) == 2
    assert store.latest_draft(MERCHANT, CASE, 1) == 2


# --- transactions ---------------------------------------------------------------------------


def test_transaction_rolls_back_everything_on_error(store):
    with pytest.raises(RuntimeError), store.transaction():
        store.update_case(MERCHANT, CASE, state="ASSESSED", at=T0)
        store.audit(MERCHANT, CASE, "STATE", {"to": "ASSESSED"}, T0)
        raise RuntimeError("boom")
    assert store.case(MERCHANT, CASE).state == "INGESTED"
    assert store.audit_events(MERCHANT, CASE) == []


def test_nested_transactions_join_the_outer_one(store):
    with pytest.raises(RuntimeError), store.transaction():
        with store.transaction():
            store.update_case(MERCHANT, CASE, state="ASSESSED", at=T0)
        raise RuntimeError("outer fails")
    assert store.case(MERCHANT, CASE).state == "INGESTED"


# --- audit chain ----------------------------------------------------------------------------


def test_audit_chain_verifies(store):
    with store.transaction():
        for i in range(5):
            store.audit(MERCHANT, CASE, "STEP", {"i": i}, T0 + timedelta(minutes=i))
    assert verify_chain(store.conn) is None
    assert [e["body"]["i"] for e in store.audit_events(MERCHANT, CASE)] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    "column, value",
    [("body_json", '{"i":99}'), ("event", "EDITED"), ("at", "2030-01-01T00:00:00+00:00")],
)
def test_editing_any_audit_row_breaks_the_chain(store, column, value):
    with store.transaction():
        for i in range(3):
            store.audit(MERCHANT, CASE, "STEP", {"i": i}, T0)
    store.conn.execute(f"UPDATE audit_events SET {column} = ? WHERE seq = 2", (value,))
    assert verify_chain(store.conn) == 2


def test_deleting_an_audit_row_breaks_the_chain(store):
    with store.transaction():
        for i in range(3):
            store.audit(MERCHANT, CASE, "STEP", {"i": i}, T0)
    store.conn.execute("DELETE FROM audit_events WHERE seq = 2")
    assert verify_chain(store.conn) == 3


def test_policy_for_another_merchant_is_refused(store):
    with pytest.raises(StoreError):
        store.register_merchant(
            "m-3", airwallex_account_id="a", policy=DEMO_POLICY, delegated_authority=True
        )
