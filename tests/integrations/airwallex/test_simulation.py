"""Sandbox-only staging: exact requests on the wire, and a fake with the same interface."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from nirnaya.integrations.airwallex.http import SANDBOX_API
from nirnaya.integrations.airwallex.simulation import (
    TEST_CARD,
    AirwallexSimulator,
    staging_id,
    wire_time,
)
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.integrations.mocks.simulator_fake import FakeSimulator
from nirnaya.models import DisputeStatus, Money, ReasonCategory, Stage
from nirnaya.orchestrator.ports import AirwallexError
from tests.integrations.airwallex.helpers import LOGIN, ok, session

M = "merchant-demo"
DUE = datetime(2026, 11, 10, tzinfo=UTC)
USD_1500 = Money(amount=Decimal("1500"), currency="USD")


def simulator(*replies):
    http, transport, _ = session(LOGIN, *replies)
    return AirwallexSimulator(http, merchant_id=M, base_url=SANDBOX_API), transport


def test_create_payment_creates_then_confirms_with_the_test_card():
    sim, t = simulator(
        ok({"id": "int_1", "status": "REQUIRES_PAYMENT_METHOD"}, status=201),
        ok({"id": "int_1", "status": "SUCCEEDED", "latest_payment_attempt": {"id": "att_1"}}),
    )
    staged = sim.create_payment(M, key="K1", amount=USD_1500, merchant_order_id="ORD-K1")
    assert (staged.payment_intent_id, staged.payment_attempt_id, staged.status) == (
        "int_1",
        "att_1",
        "SUCCEEDED",
    )
    create, confirm = t.sent[1], t.sent[2]
    assert create.url == f"{SANDBOX_API}/api/v1/pa/payment_intents/create"
    assert create.json() == {
        "request_id": staging_id("K1", "intent"),
        "amount": 1500,
        "currency": "USD",
        "merchant_order_id": "ORD-K1",
    }
    assert confirm.url == f"{SANDBOX_API}/api/v1/pa/payment_intents/int_1/confirm"
    card = confirm.json()["payment_method"]["card"]
    assert confirm.json()["payment_method"]["type"] == "card"
    assert card["number"] == TEST_CARD and len(card["expiry_year"]) == 4 and card["cvc"]


def test_staging_request_ids_are_stable_per_key():
    assert staging_id("K1", "intent") == staging_id("K1", "intent")
    assert staging_id("K1", "intent") != staging_id("K2", "intent")
    assert staging_id("K1", "intent") != staging_id("K1", "confirm")


def test_create_dispute_sends_the_documented_fields():
    sim, t = simulator(ok({"id": "dst_1", "status": "RECEIVED"}, status=201))
    dispute_id = sim.create_dispute(
        M, payment_intent_id="int_1", reason_code="10.4", stage=Stage.RFI, due_at=DUE
    )
    assert dispute_id == "dst_1"
    assert t.sent[1].url == f"{SANDBOX_API}/api/v1/simulation/pa/payment_disputes/create"
    assert t.sent[1].json() == {
        "payment_intent_id": "int_1",
        "reason_code": "10.4",
        "stage": "RFI",
        "due_at": "2026-11-10T00:00:00+0000",
    }


def test_escalate_and_resolve():
    sim, t = simulator(ok({}), ok({}))
    sim.escalate(M, "dst_1", due_at=DUE)
    sim.resolve(M, "dst_1", in_favor_of="MERCHANT")
    assert t.sent[1].url.endswith("/simulation/pa/payment_disputes/dst_1/escalate")
    assert t.sent[1].json() == {"due_at": "2026-11-10T00:00:00+0000"}
    assert t.sent[2].url.endswith("/simulation/pa/payment_disputes/dst_1/resolve")
    assert t.sent[2].json() == {"in_favor_of": "MERCHANT"}


def test_simulation_runs_only_in_the_sandbox_and_for_its_merchant():
    http, _, _ = session(LOGIN)
    with pytest.raises(AirwallexError, match="host_not_allowed"):
        AirwallexSimulator(http, merchant_id=M, base_url="https://api.airwallex.com")
    sim, t = simulator()
    with pytest.raises(AirwallexError, match="wrong_merchant"):
        sim.escalate("merchant-other", "dst_1", due_at=DUE)
    assert t.sent == []


def test_wire_time_is_utc_with_a_plain_offset():
    from datetime import timezone

    local = datetime(2026, 11, 10, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    assert wire_time(local) == "2026-11-10T00:00:00+0000"


def test_the_fake_simulator_stages_a_kit_case_end_to_end():
    aw = FakeAirwallex()
    now = datetime(2026, 10, 27, 10, tzinfo=UTC)
    sim = FakeSimulator(aw, now=lambda: now)
    staged = sim.create_payment(M, key="K1", amount=USD_1500, merchant_order_id="ORD-K1")
    again = sim.create_payment(M, key="K1", amount=USD_1500, merchant_order_id="ORD-K1")
    assert again == staged  # idempotent per key
    dispute_id = sim.create_dispute(
        M,
        payment_intent_id=staged.payment_intent_id,
        reason_code="10.4",
        stage=Stage.RFI,
        due_at=DUE,
    )
    d = aw.retrieve_dispute(M, dispute_id)
    assert (d.stage, d.status, d.reason_category) == (
        Stage.RFI,
        DisputeStatus.REQUIRES_RESPONSE,
        ReasonCategory.FRAUD,
    )
    assert d.merchant_order_id == "ORD-K1" and d.amount == USD_1500
    sim.escalate(M, dispute_id, due_at=DUE + timedelta(days=5))
    assert aw.retrieve_dispute(M, dispute_id).stage is Stage.CHARGEBACK
    with pytest.raises(AirwallexError, match="not supported"):
        sim.escalate(M, dispute_id, due_at=DUE)  # like the sandbox: not from CHARGEBACK
