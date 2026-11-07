"""FC-19 (docs/failure-cases.md): a case in trouble reaches a person, with its record intact.

A failing step used to stop `run_until_idle` after one attempt, so a case sat mid-flow with no
error and no buttons, and a tool error in the model's loop lost the model calls already made.
"""

import pytest

from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.runs import AgentRun, DraftResult, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import synthetic_world
from nirnaya.llm.fake import reply
from nirnaya.llm.types import ToolCall
from nirnaya.orchestrator import machine
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness
from tests.notes import noted_case

M = DEMO_POLICY.merchant_id


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
