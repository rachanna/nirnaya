"""Regression tests for the pre-submission audit findings H1, M1, M2 and L1.

Each test reproduces the failure the audit found against the real pipeline.
"""

from datetime import timedelta

import pytest

from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.evidence.derive import contradictory_record_ids
from nirnaya.guardrails.gateway import GuardFailure, WriteGateway
from nirnaya.guardrails.request_ids import request_id
from nirnaya.ingest import ingest
from nirnaya.integrations.mocks import SourceRecord, SyntheticWorld
from nirnaya.models import Action, CardBrand, Money
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve
from nirnaya.orchestrator.states import CaseState, check
from nirnaya.render.evidence_doc import RenderError, check_limits
from nirnaya.store import verify_chain
from tests.factories import carrier_record, make_dispute
from tests.harness import CASES, Harness

M = "merchant-demo"


def orch(h, systems=None, drafter=None):
    return Orchestrator(
        h.store,
        h.aw,
        h.aw,
        systems or h.world,
        ScriptedInvestigator(),
        drafter or ScriptedDrafter(),
        h.settings,
    )


def world_with(changes):
    """The synthetic world with some records' fields changed."""
    records = []
    for case in CASES.values():
        for s in case.source_records():
            if s.id in changes:
                s = SourceRecord(**{**s.model_dump(), "fields": {**s.fields, **changes[s.id]}})
            records.append(s)
    return SyntheticWorld(records)


# --- H1: a changed record never stalls anything ----------------------------------------------


def test_h1_a_record_changed_between_cycles_is_versioned_and_nothing_stalls():
    h = Harness(dry_run=False)
    h.stage(CASES["K1"])
    o = orch(h)
    now = h.now(CASES["K1"])
    o.run_until_idle(now)
    o.systems = world_with({"K1-order": {"refund_status": "NONE"}})
    h.stage(CASES["D2"])
    h.aw.simulate_escalate("K1", due_at=now + timedelta(days=20))
    o.run_until_idle(now + timedelta(days=5))  # used to raise StoreError

    assert [(w.action, w.cycle, w.status) for w in h.store.writes(M, "K1")] == [
        ("CHALLENGE", 1, "CONFIRMED"),
        ("CHALLENGE", 2, "CONFIRMED"),
    ]
    assert [(w.action, w.status) for w in h.store.writes(M, "D2")] == [("ACCEPT", "CONFIRMED")]
    ids = {r.id for r in h.store.evidence(M, "K1")}
    assert "K1-order" in ids and any(i.startswith("K1-order@") for i in ids)
    assert "EVIDENCE_REVISED" in [e["event"] for e in h.store.audit_events(M, "K1")]
    assert verify_chain(h.store.conn) is None


def test_h1_revisions_are_idempotent_and_keep_the_original():
    h = Harness()
    case = CASES["N1"]
    h.stage(case)
    ingest(h.store, h.aw, M, h.now(case))
    original = carrier_record(id="N1-carrier", tracking_number="TRK-N1")
    original = original.model_copy(update={"case_id": "N1"})
    from nirnaya.models import EvidenceRecord

    def capture(status, hours):
        return EvidenceRecord.create(
            **{
                **original.model_dump(exclude={"sha256", "captured_at"}),
                "fields": {**original.fields, "status": status},
            },
            captured_at=h.now(case) + timedelta(hours=hours),
        )

    first = h.store.put_evidence_or_revise(M, capture("DELIVERED", 0), h.now(case))
    lost = h.store.put_evidence_or_revise(M, capture("LOST", 1), h.now(case))
    again = h.store.put_evidence_or_revise(M, capture("LOST", 2), h.now(case))
    assert first.id == "N1-carrier"
    assert lost.id.startswith("N1-carrier@") and again.id == lost.id
    records = h.store.evidence(M, "N1")
    assert {r.id for r in records} == {"N1-carrier", lost.id}
    # DELIVERED then LOST: the versions contradict each other, so the case is referred.
    assert contradictory_record_ids(records, case.dispute) == (lost.id,)


class ExplodingDrafter:
    def draft(self, dispute, records):  # noqa: ANN001, ANN201
        raise RuntimeError("drafter exploded")


def test_h1_a_failing_case_is_isolated_and_referred_after_three_attempts():
    h = Harness()
    h.stage(CASES["N1"])  # will fail in DRAFTING
    h.stage(CASES["D2"])  # unrelated: must still complete
    o = orch(h, drafter=ExplodingDrafter())
    now = h.now(CASES["N1"])
    for _ in range(12):
        o.tick(now)
    failures = [e for e in h.store.audit_events(M, "N1") if e["event"] == "STEP_FAILED"]
    assert len(failures) == 3
    assert failures[0]["body"] == {"state": "DRAFTING", "error": "RuntimeError"}
    case = h.store.case(M, "N1")
    assert case.state == CaseState.REFERRED and case.urgent
    assert h.store.open_referral_for(M, "N1").reasons == ["STEP_FAILED"]
    assert [(w.action, w.status) for w in h.store.writes(M, "D2")] == [("ACCEPT", "DRY_RUN")]


def test_h1_a_write_in_flight_is_never_referred_by_step_failures():
    h = Harness(dry_run=False)
    h.stage(CASES["D2"])
    o = orch(h)

    def broken_accept(*args, **kwargs):
        raise ValueError("unexpected client failure")

    h.aw.accept = broken_accept
    now = h.now(CASES["D2"])
    for _ in range(10):
        o.tick(now)
    assert h.store.case(M, "D2").state == CaseState.SUBMITTING
    failures = [e for e in h.store.audit_events(M, "D2") if e["event"] == "STEP_FAILED"]
    assert len(failures) >= 3
    assert h.store.open_referral_for(M, "D2") is None


# --- M1: new attempt, new request id ---------------------------------------------------------


def test_m1_request_id_differs_per_attempt_and_is_stable_per_attempt():
    base = dict(
        merchant_id="m", case_id="c", cycle=1, stage="RFI", action="ACCEPT", decision_id="d"
    )
    assert request_id(**base) == request_id(**base, attempt=1)
    assert request_id(**base, attempt=2) != request_id(**base, attempt=1)
    assert request_id(**base, attempt=2) == request_id(**base, attempt=2)


def test_m1_a_new_attempt_after_a_rejected_write_gets_a_new_request_id():
    h = Harness(dry_run=False)
    case = CASES["D2"]
    h.stage(case)
    h.aw.fail_next("accept", "error")
    o = orch(h)
    o.run_until_idle(h.now(case))
    assert h.store.case(M, "D2").state == CaseState.REFERRED
    approve(h.store, M, "D2", Action.ACCEPT, approver="ops@example.test", now=h.now(case))
    o.run_until_idle(h.now(case))
    rows = h.store.writes(M, "D2")
    assert [w.status for w in rows] == ["FAILED", "CONFIRMED"]
    assert rows[0].request_id != rows[1].request_id


# --- M2: the write matches the dispute the decision used -------------------------------------


def run_to(h, o, case_id, state, now):
    for _ in range(30):
        if h.store.case(M, case_id) and h.store.case(M, case_id).state == state:
            return
        o.tick(now)
    raise AssertionError(f"{case_id} never reached {state}")


def test_m2_an_amount_change_before_the_write_triggers_a_re_decision_and_a_new_pdf():
    h = Harness()
    case = CASES["N1"]
    h.stage(case)
    o = orch(h)
    now = h.now(case)
    run_to(h, o, "N1", CaseState.PRE_WRITE_CHECK, now)
    h.aw.set_amount("N1", Money(amount="180.00", currency="USD"))
    o.run_until_idle(now)

    assert "DISPUTE_AMENDED" in [e["event"] for e in h.store.audit_events(M, "N1")]
    _, decision = h.store.latest_decision(M, "N1", 1)
    assert decision.inputs["amount"] == "180.00 USD"
    pdf = h.store.render(M, "N1", 1, h.store.latest_draft(M, "N1", 1))["content"]
    assert b"180.00 USD" in pdf and b"200.00 USD" not in pdf
    assert [(w.action, w.status) for w in h.store.writes(M, "N1")] == [("CHALLENGE", "DRY_RUN")]


def test_m2_gateway_compares_against_the_decisions_dispute_not_the_latest_snapshot():
    h = Harness()
    case = CASES["D2"]
    h.prepare(case)  # decided on 300.00 USD, case still INGESTED
    h.aw.set_amount("D2", Money(amount="250.00", currency="USD"))
    ingest(h.store, h.aw, M, h.now(case))  # the new amount is now the latest snapshot
    gw = WriteGateway(h.store, h.aw, h.aw, h.settings)
    result = gw.submit(M, "D2", 1, Action.ACCEPT, now=h.now(case))
    assert result.failure is GuardFailure.DISPUTE_CHANGED


def test_m2_an_amended_dispute_supersedes_the_referral_and_any_approval():
    h = Harness()
    case = CASES["K3"]
    h.stage(case)
    o = orch(h)
    now = h.now(case)
    o.run_until_idle(now)
    assert h.store.case(M, "K3").state == CaseState.REFERRED
    approve(h.store, M, "K3", Action.ACCEPT, approver="ops@example.test", now=now)
    assert h.store.latest_approval(M, "K3", 1) is not None
    h.aw.set_amount("K3", Money(amount="110.00", currency="USD"))
    o.run_until_idle(now)
    assert h.store.latest_approval(M, "K3", 1) is None  # the stale approval can't be used
    assert h.store.writes(M, "K3") == []
    assert h.store.case(M, "K3").state == CaseState.REFERRED  # re-decided: still a person's call
    assert h.store.open_referral_for(M, "K3").reasons == ["UNANSWERED_CUSTOMER_CONTACT"]


@pytest.mark.parametrize(
    "state",
    [
        "ASSESSED",
        "INVESTIGATING",
        "DECIDED",
        "DRAFTING",
        "VERIFYING",
        "RENDERING",
        "PRE_WRITE_CHECK",
        "REFERRED",
    ],
)
def test_m2_reassess_is_allowed_from_pre_write_states(state):
    check(CaseState(state), CaseState.ASSESSED, external=True)


@pytest.mark.parametrize("state", ["SUBMITTING", "AWAITING_OUTCOME", "DEFERRED", "CLOSED"])
def test_m2_reassess_is_not_allowed_once_a_write_is_in_flight_or_done(state):
    with pytest.raises(Exception, match="->"):
        check(CaseState(state), CaseState.ASSESSED, external=True)


# --- L1 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("brand", list(CardBrand) + [None])
def test_l1_png_is_rejected_for_every_brand(brand):
    with pytest.raises(RenderError):
        check_limits(brand, [("scan.png", b"x")])


def test_l1_pdf_still_accepted():
    check_limits(CardBrand.MASTERCARD, [("e.pdf", b"%PDF-")])
    assert make_dispute().card_brand is CardBrand.VISA
