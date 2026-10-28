"""The deadline backstop runs whatever Airwallex does, every unreadable dispute is visible, and
a write in flight is never interrupted or replaced under its id. From an independent review
(NIR-003, NIR-007, NIR-004)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from nirnaya.agent.runs import DraftResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.ingest import ingest
from nirnaya.integrations.airwallex.http import parse_json
from nirnaya.integrations.airwallex.mapping import UnmappableRecord, dispute_from_wire
from nirnaya.models import AcceptReason, Action
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.ports import AirwallexError, WriteOutcomeUnknown
from nirnaya.orchestrator.referrals import approve
from nirnaya.orchestrator.states import CaseState
from nirnaya.store import Store, connect
from tests.harness import CASES, Harness
from tests.integrations.airwallex.test_client import client, ok, wire_dispute

N1 = CASES["N1"]
M = N1.dispute.merchant_id


# --- 1. an ingestion failure never stops the watchdog ---------------------------------------


class Flaky:
    """Wraps the fake Airwallex; listing fails while `down` is set."""

    def __init__(self, inner):
        self.inner, self.down = inner, False

    def list_disputes(self, merchant_id):
        if self.down:
            raise AirwallexError("unreachable", "list endpoint down")
        return self.inner.list_disputes(merchant_id)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def orchestrator(h, source):
    return Orchestrator(
        h.store, source, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )


def merchant_events(h, *names):
    rows = h.store.conn.execute(
        "SELECT event, body_json FROM audit_events WHERE merchant_id = ? AND case_id IS NULL"
        " ORDER BY seq",
        (M,),
    ).fetchall()
    return [(r["event"], json.loads(r["body_json"])) for r in rows if r["event"] in names]


def test_the_watchdog_still_refers_while_airwallex_cannot_be_listed():
    h = Harness()
    h.stage(N1)
    source = Flaky(h.aw)
    orch = orchestrator(h, source)
    orch.tick(h.now(N1))  # ingested while Airwallex was up
    source.down = True
    late = N1.dispute.due_at - timedelta(hours=1)
    for _ in range(3):
        orch.tick(late)  # does not raise
    case = h.store.case(M, "N1")
    assert case.state == CaseState.REFERRED and case.urgent
    assert [e for e, _ in merchant_events(h, "INGEST_FAILED")] == ["INGEST_FAILED"]  # once
    source.down = False
    orch.tick(late)
    orch.tick(late)
    assert [e for e, _ in merchant_events(h, "INGEST_RECOVERED")] == ["INGEST_RECOVERED"]


def test_one_merchants_failure_does_not_stop_another():
    h = Harness()
    h.store.register_merchant(
        "merchant-b", airwallex_account_id="acct-b", policy=DEMO_POLICY.model_copy(
            update={"merchant_id": "merchant-b"}), delegated_authority=True,
    )  # fmt: skip
    h.stage(N1)

    class OneDown(Flaky):
        def list_disputes(self, merchant_id):
            if merchant_id == "merchant-b":
                raise AirwallexError("unauthorized", "bad credentials")
            return self.inner.list_disputes(merchant_id)

    orch = orchestrator(h, OneDown(h.aw))
    orch.run_until_idle(h.now(N1))
    assert h.store.case(M, "N1").state == CaseState.AWAITING_OUTCOME  # merchant A unaffected


# --- 2. a bad value is an unreadable record, never a crash ----------------------------------

BASE = dict(
    id="dst_1", stage="CHARGEBACK", status="REQUIRES_RESPONSE", amount=100, currency="USD",
    payment_intent_id="int_1", card_brand="visa", reason={"type": "FRAUDULENT"},
    created_at="2026-10-01T10:00:00+0000", due_at="2026-11-01T10:00:00+0000",
)  # fmt: skip


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": 10.001},  # finer than the currency's minor unit
        {"currency": "usd"},
        {"currency": "XXX1"},
        {"created_at": "2026-10-01T10:00:00"},  # naive
        {"due_at": "not a date"},
        {"amount": -5},
        {"due_at": "2026-09-01T10:00:00+0000"},  # before created_at
    ],
)
def test_every_bad_wire_value_is_an_unmappable_record(changes):
    data = parse_json(json.dumps({**BASE, **changes}).encode())
    with pytest.raises(UnmappableRecord):
        dispute_from_wire(data, merchant_id="m1")


def test_one_bad_dispute_does_not_abort_the_listing():
    no_refunds = ok({"items": [], "has_more": False})
    bad = wire_dispute("dst_bad", created_at="2026-10-27T10:00:00")  # naive timestamp
    c, _ = client(ok({"items": [bad, wire_dispute("dst_ok")]}), no_refunds)
    assert [d.id for d in c.list_disputes(M)] == ["dst_ok"]
    assert "dst_bad" in c.refused(M)


def test_an_unreadable_write_response_is_an_unknown_outcome_not_a_crash():
    c, _ = client(ok(wire_dispute(status="ACCEPTED", due_at="2026-11-10T00:00:00")))  # naive
    with pytest.raises(WriteOutcomeUnknown):
        c.accept(M, "dst_1", request_id="r", reason=AcceptReason.OTHERS)


# --- 3. unreadable disputes are surfaced, once ------------------------------------------------


def test_an_unmappable_dispute_is_audited_once_and_listed_for_a_person():
    from nirnaya.console.views import unreadable_disputes

    c, _ = client(
        ok({"items": [wire_dispute(status="RECEIVED")]}),
        ok({"items": [wire_dispute(status="RECEIVED")]}),
    )
    store = Store(connect())
    store.register_merchant(
        M, airwallex_account_id="x", policy=DEMO_POLICY, delegated_authority=True
    )
    now = datetime(2026, 10, 27, 11, tzinfo=UTC)
    ingest(store, c, M, now)
    ingest(store, c, M, now + timedelta(minutes=5))
    events = store.merchant_events(M, {"INGEST_UNMAPPABLE"})
    assert len(events) == 1  # not repeated every poll
    body = events[0]["body"]
    assert body["dispute_id"] == "dst_1" and body["due_at"] == "2026-11-10T00:00:00+0000"

    class Session:
        pass

    s = Session()
    s.store, s.now = store, now
    (row,) = unreadable_disputes(s)
    assert row.dispute_id == "dst_1" and "RECEIVED" in row.why


# --- 4. a write in flight is never interrupted or replaced ------------------------------------


class ChangingDrafter:
    """A re-draft drops a claim, as a model drafter might."""

    def __init__(self):
        self.n = 0

    def draft(self, dispute, records):
        self.n += 1
        claims = ScriptedDrafter().draft(dispute, records).claims
        if self.n > 1:
            claims = tuple(c for c in claims if c.establishes == "DELIVERY_CONFIRMED")
        return DraftResult(claims=claims)


def in_flight():
    h = Harness(dry_run=False)
    h.stage(N1)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ChangingDrafter(), h.settings
    )
    h.aw.fail_next("challenge", "timeout_before_apply")
    orch.run_until_idle(h.now(N1))
    assert h.store.case(M, "N1").state == CaseState.SUBMITTING
    assert [w.status for w in h.store.writes(M, "N1")] == ["UNKNOWN"]
    return h, orch


def test_a_failed_refetch_does_not_interrupt_a_write_in_flight():
    h, orch = in_flight()
    h.aw.fail_next("retrieve", "error")
    orch.tick(h.now(N1))
    assert h.store.case(M, "N1").state == CaseState.SUBMITTING  # still waiting
    assert h.store.open_referral_for(M, "N1") is None
    orch.run_until_idle(h.now(N1))  # the re-fetch works again: completed under the same id
    (write,) = h.store.writes(M, "N1")
    assert write.status == "CONFIRMED"


def test_a_request_id_is_never_sent_with_a_different_payload():
    from nirnaya.guardrails.gateway import GuardFailure

    h, orch = in_flight()
    rid = h.store.writes(M, "N1")[0].request_id
    # Another refusal (not a re-fetch failure) takes the case to a person, who re-approves.
    with h.store.transaction():
        h.store.update_case(M, "N1", state=CaseState.REFERRED, at=h.now(N1))
        h.store.open_referral(M, "N1", 1, ["GUARD_TEST"], False, h.now(N1), detail={})
    approve(h.store, M, "N1", Action.CHALLENGE, approver="ops@example.test", now=h.now(N1))
    sent_before = len(h.aw.challenges)
    orch.run_until_idle(h.now(N1))
    assert len(h.aw.challenges) == sent_before  # nothing resent under the old id
    referral = h.store.open_referral_for(M, "N1")
    assert referral.reasons == [f"GUARD_{GuardFailure.REQUEST_CONFLICT}"]
    assert h.store.writes(M, "N1")[0].request_id == rid
