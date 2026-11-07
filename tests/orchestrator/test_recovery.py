"""FC-19 and FC-11 (docs/failure-cases.md): a case in trouble reaches a person, with its record
intact, and that person can always do something about it.

A failing step used to stop `run_until_idle` after one attempt, so a case sat mid-flow with no
error and no buttons, and a tool error in the model's loop lost the model calls already made.
A referred case with no decision on the current facts failed every approval, for ever.
"""

from decimal import Decimal as D

import pytest

from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.runs import AgentRun, DraftResult, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import synthetic_world
from nirnaya.llm.fake import reply
from nirnaya.llm.types import ToolCall
from nirnaya.models import Action, Money
from nirnaya.orchestrator import machine
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, needs_fresh_decision, send_back
from nirnaya.orchestrator.states import CaseState
from nirnaya.store.repository import StoreError
from tests.harness import CASES, Harness
from tests.notes import noted_case

M = DEMO_POLICY.merchant_id
AP = "ops@example.test"


def orch(h, investigator=None, drafter=None, world=None, reader=None):
    return Orchestrator(
        h.store, h.aw, h.aw, world or h.world, investigator or ScriptedInvestigator(),
        drafter or ScriptedDrafter(), h.settings, reader=reader,
    )  # fmt: skip


def staged(label="N1", **kw):
    h = Harness(**kw)
    h.stage(CASES[label])
    return h, h.now(CASES[label])


def failures(h, cid):
    return [e for e in h.store.audit_events(M, cid) if e["event"] == "STEP_FAILED"]


def boom(*_a, **_k):
    raise RuntimeError("exploded")


class Exploding:
    def investigate(self, *_a, **_k):
        boom()


class AsksForTheFirstTool:
    """A model that always looks up the first thing it is offered."""

    provider = "fake"
    model = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, *, tools=(), **_):
        self.calls += 1
        spec = tools[0]
        key = spec.input_schema["properties"]["key"]["enum"][0]
        return reply(None, ToolCall(id=f"c{self.calls}", name=spec.name, arguments={"key": key}))


def test_a_failing_step_ends_with_a_person_not_frozen_mid_flow():
    h, now = staged()
    orch(h, investigator=Exploding()).run_until_idle(now)
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert h.store.open_referral_for(M, "N1").reasons == ["STEP_FAILED"]
    assert len(failures(h, "N1")) == 3
    assert h.store.writes(M, "N1") == []


def test_a_failing_write_still_never_leaves_submitting_and_the_loop_ends(monkeypatch):
    h, now = staged("D2", dry_run=False)
    monkeypatch.setattr(h.aw, "accept", boom)
    orch(h).run_until_idle(now)
    assert h.store.case(M, "D2").state == CaseState.SUBMITTING
    assert h.store.open_referral_for(M, "D2") is None


def test_a_tool_error_ends_the_models_round_and_keeps_its_run(monkeypatch):
    h, now = staged()
    monkeypatch.setattr(h.world, "tracking_events", boom)
    llm = AsksForTheFirstTool()
    orch(h, investigator=LLMInvestigator(llm, env={})).run_until_idle(now)

    runs = h.store.agent_runs(M, "N1", 1)
    assert runs and all(r["kind"] == "INVESTIGATE" for r in runs)
    assert llm.calls == len(runs)  # one turn each: the failure ended the round
    outcomes = [c["outcome"] for r in runs for c in r["tool_calls"]]
    assert outcomes and set(outcomes) == {"FAILED"}
    # The search could not finish, so no accept: a person takes over.
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert h.store.writes(M, "N1") == []


def test_a_crash_after_a_draft_keeps_the_model_run(monkeypatch):
    h, now = staged()
    run = AgentRun(kind="DRAFT", provider="f", model="f", turns=1, stop="TOOL_USE")

    class Drafter:
        def draft(self, dispute, records, **_):
            return DraftResult(claims=ScriptedDrafter().draft(dispute, records).claims, run=run)

    monkeypatch.setattr(h.store, "put_claims", boom)
    orch(h, drafter=Drafter()).run_until_idle(now)
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert [r["kind"] for r in h.store.agent_runs(M, "N1", 1)] == ["DRAFT"] * 3


def test_a_crashed_reading_is_not_recorded_so_the_notes_are_read_again(monkeypatch):
    """A stored READ run means the notes were read. A crash must not leave one, or the retry
    would skip the notes and an accept could rest on notes nobody screened (FC-01)."""
    case = noted_case()
    h = Harness(dry_run=False)
    h.stage(case)
    run = AgentRun(kind="READ", provider="f", model="f", turns=1, stop="TOOL_USE")

    class Reader:
        calls = 0

        def read(self, dispute, facts, records):
            Reader.calls += 1
            return ReadResult(run=run)

    real = machine.screen
    crashes = iter([True])

    def screen(*a, **k):
        if next(crashes, False):
            boom()
        return real(*a, **k)

    monkeypatch.setattr(machine, "screen", screen)
    world = synthetic_world([case])
    orch(h, reader=Reader(), world=world).run_until_idle(h.now(case))
    assert Reader.calls == 2
    assert len(failures(h, case.dispute.id)) == 1
    runs = h.store.agent_runs(M, case.dispute.id, 1)
    assert [r["kind"] for r in runs].count("READ") == 1


@pytest.mark.parametrize("label", ["N1", "K2", "K3"])
def test_a_healthy_case_never_records_a_failure(label):
    h, now = staged(label)
    orch(h).run_until_idle(now)
    assert failures(h, label) == []


# --- FC-11: a referred case that needs a new decision can be sent back ---------------------------


def referred_before_any_decision(monkeypatch, h, o, now):
    """Three failures at ingestion, then the system recovers."""
    with monkeypatch.context() as m:
        m.setattr(h.aw, "payment_intent_owner", boom)
        o.run_until_idle(now)
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert h.store.latest_decision(M, "N1", 1) is None


def amended_during_submission(h, o, now):
    for _ in range(30):
        case = h.store.case(M, "N1")
        if case is not None and case.state == CaseState.SUBMITTING:
            break
        o.tick(now)
    amount = CASES["N1"].dispute.amount.amount - D("1.00")
    h.aw.set_amount("N1", Money(amount=amount, currency="USD"))
    o.run_until_idle(now)
    assert h.store.open_referral_for(M, "N1").reasons[0] == "GUARD_DISPUTE_CHANGED"
    return amount


def test_a_case_with_no_decision_cannot_be_approved_only_sent_back(monkeypatch):
    h, now = staged(dry_run=False)
    o = orch(h)
    referred_before_any_decision(monkeypatch, h, o, now)
    assert needs_fresh_decision(h.store, M, "N1")
    for action in (Action.ACCEPT, Action.CHALLENGE):
        with pytest.raises(StoreError, match="send it back"):
            approve(h.store, M, "N1", action, approver=AP, now=now)

    send_back(h.store, M, "N1", approver=AP, now=now)
    o.run_until_idle(now)
    assert not needs_fresh_decision(h.store, M, "N1")
    assert [(w.action, w.status) for w in h.store.writes(M, "N1")] == [("CHALLENGE", "CONFIRMED")]
    events = [e for e in h.store.audit_events(M, "N1") if e["event"] == "SENT_BACK"]
    assert [e["body"]["approver"] for e in events] == [AP]


def test_an_amendment_during_submission_is_decided_again_when_sent_back():
    h, now = staged(dry_run=False)
    o = orch(h)
    amount = amended_during_submission(h, o, now)
    assert needs_fresh_decision(h.store, M, "N1")
    with pytest.raises(StoreError, match="send it back"):
        approve(h.store, M, "N1", Action.CHALLENGE, approver=AP, now=now)

    send_back(h.store, M, "N1", approver=AP, now=now)
    o.run_until_idle(now)
    assert h.store.latest_decision(M, "N1", 1)[1].inputs["amount"] == f"{amount} USD"
    assert h.store.open_referral_for(M, "N1") is None
    assert [(w.action, w.status) for w in h.store.writes(M, "N1")] == [("CHALLENGE", "CONFIRMED")]


def test_a_write_in_doubt_is_never_sent_back():
    """A new decision means a new request id, which Airwallex could not deduplicate (FC-07)."""
    h, now = staged(dry_run=False)
    o = orch(h)
    h.aw.fail_next("challenge", "timeout_before_apply")
    for _ in range(30):
        if h.store.writes(M, "N1"):
            break
        o.tick(now)
    assert h.store.writes(M, "N1")[0].status == "UNKNOWN"
    amount = CASES["N1"].dispute.amount.amount - D("1.00")
    h.aw.set_amount("N1", Money(amount=amount, currency="USD"))
    o.run_until_idle(now)
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert not needs_fresh_decision(h.store, M, "N1")
    with pytest.raises(StoreError, match="cannot be sent back"):
        send_back(h.store, M, "N1", approver=AP, now=now)
    assert h.store.case(M, "N1").state == CaseState.REFERRED


def test_a_decision_that_still_holds_is_not_sent_back():
    h, now = staged("K3")
    orch(h).run_until_idle(now)
    assert h.store.case(M, "K3").state == CaseState.REFERRED
    assert not needs_fresh_decision(h.store, M, "K3")
    with pytest.raises(StoreError, match="cannot be sent back"):
        send_back(h.store, M, "K3", approver=AP, now=now)


def test_a_case_not_with_a_person_needs_nothing():
    h, now = staged("K2")
    assert not needs_fresh_decision(h.store, M, "K2")  # not ingested yet
    orch(h).run_until_idle(now)
    assert not needs_fresh_decision(h.store, M, "K2")


def test_sending_back_voids_what_was_confirmed_on_the_old_facts(monkeypatch):
    h, now = staged(dry_run=False)
    o = orch(h)
    amended_during_submission(h, o, now)
    reopened = []
    monkeypatch.setattr(h.store, "reopen_findings", lambda *a: reopened.append(a))
    send_back(h.store, M, "N1", approver=AP, now=now)
    assert reopened == [(M, "N1", 1)]
