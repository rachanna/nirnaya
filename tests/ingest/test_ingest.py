from datetime import timedelta

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.ingest import ingest
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.models import DisputeStatus, Stage
from nirnaya.orchestrator.states import CaseState
from nirnaya.store import Store, StoreError, connect, verify_chain
from tests.factories import CASE, MERCHANT, OTHER_MERCHANT, T0, make_dispute


@pytest.fixture
def world():
    store = Store(connect())
    store.register_merchant(
        MERCHANT, airwallex_account_id="acct", policy=DEMO_POLICY, delegated_authority=True
    )
    return store, FakeAirwallex()


def test_new_dispute_opens_a_case_and_cycle(world):
    store, aw = world
    aw.add_dispute(make_dispute())
    assert ingest(store, aw, MERCHANT, T0) == [CASE]
    case = store.case(MERCHANT, CASE)
    assert (case.state, case.cycle) == (CaseState.INGESTED, 1)
    assert store.cycle(MERCHANT, CASE, 1).stage == "CHARGEBACK"
    assert [e["event"] for e in store.audit_events(MERCHANT, CASE)] == ["CASE_OPENED"]
    assert verify_chain(store.conn) is None


def test_unchanged_dispute_is_not_snapshotted_again(world):
    store, aw = world
    aw.add_dispute(make_dispute())
    ingest(store, aw, MERCHANT, T0)
    assert ingest(store, aw, MERCHANT, T0 + timedelta(minutes=1)) == []
    assert store.conn.execute("SELECT COUNT(*) FROM dispute_snapshots").fetchone()[0] == 1


def test_stage_change_opens_a_new_cycle(world):
    store, aw = world
    aw.add_dispute(make_dispute(stage=Stage.RFI))
    ingest(store, aw, MERCHANT, T0)
    store.update_case(MERCHANT, CASE, state=CaseState.AWAITING_OUTCOME, at=T0)
    aw.simulate_escalate(CASE, due_at=T0 + timedelta(days=30))
    ingest(store, aw, MERCHANT, T0 + timedelta(days=1))
    case = store.case(MERCHANT, CASE)
    assert (case.state, case.cycle) == (CaseState.INGESTED, 2)
    assert store.cycle(MERCHANT, CASE, 2).stage == "CHARGEBACK"
    assert case.due_at == T0 + timedelta(days=30)


def test_terminal_status_closes_the_case(world):
    store, aw = world
    aw.add_dispute(make_dispute())
    ingest(store, aw, MERCHANT, T0)
    store.update_case(MERCHANT, CASE, state=CaseState.AWAITING_OUTCOME, at=T0)
    aw.simulate_resolve(CASE, in_favor_of="MERCHANT")
    ingest(store, aw, MERCHANT, T0 + timedelta(days=1))
    assert store.case(MERCHANT, CASE).state == CaseState.CLOSED
    assert store.audit_events(MERCHANT, CASE)[-1]["event"] == "CLOSED_BY_AIRWALLEX"


def test_non_terminal_change_updates_without_moving_state(world):
    store, aw = world
    aw.add_dispute(make_dispute())
    ingest(store, aw, MERCHANT, T0)
    aw.add_dispute(make_dispute(status=DisputeStatus.CHALLENGED))
    ingest(store, aw, MERCHANT, T0 + timedelta(hours=1))
    assert store.case(MERCHANT, CASE).state == CaseState.INGESTED
    assert store.latest_dispute(MERCHANT, CASE).status is DisputeStatus.CHALLENGED


def test_snapshot_hash_is_stored(world):
    store, aw = world
    aw.add_dispute(make_dispute())
    ingest(store, aw, MERCHANT, T0)
    assert len(store.conn.execute("SELECT sha256 FROM dispute_snapshots").fetchone()[0]) == 64


def test_unknown_merchant_is_rejected(world):
    store, aw = world
    with pytest.raises(StoreError, match="unknown merchant"):
        ingest(store, aw, OTHER_MERCHANT, T0)


def test_dispute_id_owned_by_another_merchant_is_rejected(world):
    store, aw = world
    other = DEMO_POLICY.model_copy(update={"merchant_id": OTHER_MERCHANT})
    store.register_merchant(
        OTHER_MERCHANT, airwallex_account_id="a2", policy=other, delegated_authority=True
    )
    aw.add_dispute(make_dispute())
    ingest(store, aw, MERCHANT, T0)
    aw.add_dispute(make_dispute(merchant_id=OTHER_MERCHANT))  # same id, other tenant
    assert ingest(store, aw, OTHER_MERCHANT, T0) == []
    assert store.case(OTHER_MERCHANT, CASE) is None
    rows = store.conn.execute(
        "SELECT event FROM audit_events WHERE merchant_id = ?", (OTHER_MERCHANT,)
    ).fetchall()
    assert [r["event"] for r in rows] == ["INGEST_REJECTED"]
