from datetime import UTC, datetime

import pytest

from nirnaya.integrations.mocks import (
    CarrierTracking,
    Helpdesk,
    LoginHistory,
    OrderSystem,
    PolicyStore,
    SourceRecord,
    SyntheticWorld,
)
from nirnaya.models import RecordAuthor, SourceSystem

S = SourceSystem


def rec(id, system, merchant="m-1", author=RecordAuthor.MERCHANT, **fields):
    return SourceRecord(id=id, merchant_id=merchant, system=system, author=author, fields=fields)


WORLD = SyntheticWorld(
    [
        rec("o-1", S.ORDER, order_id="ORD-1", customer_id="C-1", tracking_number="T-1"),
        rec(
            "t-1b", S.CARRIER, author=RecordAuthor.THIRD_PARTY, tracking_number="T-1", status="LOST"
        ),
        rec(
            "t-1a",
            S.CARRIER,
            author=RecordAuthor.THIRD_PARTY,
            tracking_number="T-1",
            status="DELIVERED",
        ),
        rec("l-1", S.LOGIN, customer_id="C-1"),
        rec("h-1", S.HELPDESK, author=RecordAuthor.CUSTOMER, customer_id="C-1", ticket_id="TK"),
        rec("p-1", S.POLICY, customer_id="C-1"),
        rec("o-x", S.ORDER, merchant="m-2", order_id="ORD-1", customer_id="C-1"),
    ]
)


def test_world_satisfies_every_system_protocol():
    w: OrderSystem = WORLD
    for proto in (OrderSystem, CarrierTracking, LoginHistory, Helpdesk, PolicyStore):
        assert all(hasattr(w, name) for name in proto.__dict__ if not name.startswith("_"))


def test_lookups_are_scoped_by_merchant():
    assert [r.id for r in WORLD.find_orders("m-1", "ORD-1")] == ["o-1"]
    assert [r.id for r in WORLD.find_orders("m-2", "ORD-1")] == ["o-x"]
    assert WORLD.find_orders("m-3", "ORD-1") == []
    assert WORLD.tracking_events("m-2", "T-1") == []


def test_lookups_return_every_matching_record_in_id_order():
    assert [r.id for r in WORLD.tracking_events("m-1", "T-1")] == ["t-1a", "t-1b"]


def test_each_system_is_keyed_by_its_own_field():
    assert [r.id for r in WORLD.login_summary("m-1", "C-1")] == ["l-1"]
    assert [r.id for r in WORLD.customer_contacts("m-1", "C-1")] == ["h-1"]
    assert [r.id for r in WORLD.policy_acceptances("m-1", "C-1")] == ["p-1"]
    assert WORLD.login_summary("m-1", "ORD-1") == []


def test_unknown_keys_return_nothing():
    assert WORLD.find_orders("m-1", "ORD-404") == []


def test_returned_lists_are_copies():
    WORLD.find_orders("m-1", "ORD-1").clear()
    assert WORLD.find_orders("m-1", "ORD-1")


def test_record_without_its_key_field_is_rejected():
    with pytest.raises(ValueError, match="tracking_number"):
        SyntheticWorld([rec("t", S.CARRIER, status="DELIVERED")])


def test_duplicate_record_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        SyntheticWorld([rec("x", S.LOGIN, customer_id="a"), rec("x", S.LOGIN, customer_id="b")])


def test_payment_attempts_are_not_merchant_systems():
    with pytest.raises(ValueError, match="not a merchant system"):
        SyntheticWorld([rec("pa", S.PAYMENT_ATTEMPT, payment_attempt_id="a")])


def test_snapshot_is_hashed_synthetic_evidence_for_one_case():
    captured = datetime(2026, 3, 1, 13, tzinfo=UTC)
    ev = WORLD.find_orders("m-1", "ORD-1")[0].snapshot(case_id="case-9", captured_at=captured)
    assert ev.synthetic and ev.hash_ok()
    assert (ev.id, ev.merchant_id, ev.case_id, ev.source_system) == (
        "o-1",
        "m-1",
        "case-9",
        S.ORDER,
    )
    assert ev.author is RecordAuthor.MERCHANT
    assert ev.captured_at == captured
