"""The step observer: the machine reports each step and transition, and observing changes
nothing. LangSmith's per-case trace (nirnaya/integrations/langsmith/cases.py) is built on it."""

from contextlib import contextmanager

from nirnaya.agent.llm_drafter import LLMDrafter
from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.llm.types import LLMResponse, StopReason, ToolCall, Usage
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness


class Recorder:
    def __init__(self, fail=False):
        self.events, self.current, self.fail = [], None, fail

    @contextmanager
    def step(self, case):
        self.events.append(("step", case.case_id, case.state))
        self.current = case.state
        try:
            yield
        finally:
            self.current = None

    def transition(self, case, frm, to, body, external):
        self.events.append(("move", case.case_id, str(frm), str(to), external))
        if self.fail:
            raise RuntimeError("observer broke")


class StepAwareLLM:
    """Answers finish / submit-with-nothing, recording which step each call happened in."""

    provider = model = "fake"

    def __init__(self, observer):
        self.observer, self.seen = observer, []

    def complete(self, *, system, messages, tools=(), force_tool=None, max_tokens=2048):
        names = [t.name for t in tools]
        self.seen.append((self.observer.current, "finish" if "finish" in names else names[0]))
        name, args = (
            ("finish", {"reason": "done"}) if "finish" in names else (names[0], {"claims": []})
        )
        return LLMResponse(
            provider="fake",
            model="fake",
            text=None,
            tool_calls=(ToolCall(id="c", name=name, arguments=args),),
            stop=StopReason.TOOL_USE,
            usage=Usage(input_tokens=1, output_tokens=1),
        )


def run(case_id, observer, *, investigator=None, drafter=None, at=None):
    h = Harness()
    case = CASES[case_id]
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world,
        investigator or ScriptedInvestigator(), drafter or ScriptedDrafter(), h.settings,
        observer=observer,
    )  # fmt: skip
    orch.run_until_idle(at or h.now(case))
    return h, case


def state_events(h, case):
    events = h.store.audit_events(case.dispute.merchant_id, case.dispute.id)
    return [(e["body"]["from"], e["body"]["to"]) for e in events if e["event"] == "STATE"]


def test_every_transition_is_reported_in_audit_order():
    rec = Recorder()
    h, case = run("K1", rec)
    moves = [(e[2], e[3]) for e in rec.events if e[0] == "move"]
    assert moves == state_events(h, case)
    steps = [e[2] for e in rec.events if e[0] == "step"]
    assert steps[0] == CaseState.INGESTED and len(steps) >= len(moves)


def test_model_calls_happen_inside_the_step_that_makes_them():
    rec = Recorder()
    llm = StepAwareLLM(rec)
    run("N1", rec, investigator=LLMInvestigator(llm), drafter=LLMDrafter(llm))
    assert ("INVESTIGATING", "finish") in [(str(s), k) for s, k in llm.seen]
    assert all(str(state) in {"INVESTIGATING", "DRAFTING"} for state, _ in llm.seen)


def test_a_watchdog_referral_is_an_external_transition():
    rec = Recorder()
    case = CASES["N1"]
    h, _ = run("N1", rec, at=case.dispute.due_at)  # already inside the deadline buffer
    assert h.store.case(case.dispute.merchant_id, "N1").state == CaseState.REFERRED
    assert ("move", "N1", "INGESTED", "REFERRED", True) in rec.events


def test_a_failing_observer_changes_nothing():
    quiet, broken = Recorder(), Recorder(fail=True)
    h1, case = run("K1", quiet)
    h2, _ = run("K1", broken)
    m = case.dispute.merchant_id
    assert h1.store.case(m, "K1").state == h2.store.case(m, "K1").state
    assert state_events(h1, case) == state_events(h2, case)
    assert [w.action for w in h1.store.writes(m, "K1")] == [
        w.action for w in h2.store.writes(m, "K1")
    ]
