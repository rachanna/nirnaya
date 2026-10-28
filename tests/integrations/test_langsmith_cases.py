"""One LangSmith trace per case, offline: a fake run tree records what would be sent."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from nirnaya.integrations.langsmith.cases import CaseTracer
from nirnaya.llm.tracing import TracingSettings


class FakeRun:
    def __init__(self, name, parent=None, **kwargs):
        self.name, self.parent, self.kwargs = name, parent, kwargs
        self.children, self.outputs, self.error = [], None, None
        self.posted = self.patched = False

    def create_child(self, name, run_type="chain", **kwargs):
        child = FakeRun(name, parent=self, run_type=run_type, **kwargs)
        self.children.append(child)
        return child

    def post(self):
        self.posted = True

    def end(self, *, outputs=None, error=None):
        self.outputs, self.error = outputs, error

    def patch(self):
        self.patched = True


class Client:
    flushed = False

    def flush(self):
        self.flushed = True


def tracer():
    roots: list[FakeRun] = []

    def run_tree(name, **kwargs):
        roots.append(FakeRun(name, **kwargs))
        return roots[-1]

    t = CaseTracer(
        TracingSettings(api_key="k", endpoint=None, project="nirnaya"),
        client=Client(),
        run_tree=run_tree,
        parent_context=lambda parent: nullcontext(),
    )
    return t, roots


def case(state, cycle=1, case_id="K1"):
    return SimpleNamespace(merchant_id="m", case_id=case_id, cycle=cycle, state=state)


def test_a_case_is_one_root_with_a_child_per_step_closed_at_a_resting_state():
    t, roots = tracer()
    for frm, to in [("INGESTED", "ASSESSED"), ("ASSESSED", "DECIDED")]:
        with t.step(case(frm)):
            t.transition(case(frm), frm, to, {"note": frm}, False)
    with t.step(case("DECIDED")):
        t.transition(case("DECIDED"), "DECIDED", "REFERRED", {"reasons": ["X"]}, False)
    (root,) = roots
    assert root.name == "case K1 · cycle 1" and root.posted
    assert root.kwargs["session_name"] == "nirnaya"
    assert root.kwargs["extra"]["metadata"]["case_id"] == "K1"
    assert [c.name for c in root.children] == ["INGESTED", "ASSESSED", "DECIDED"]
    assert root.children[0].outputs == {"from": "INGESTED", "to": "ASSESSED", "external": False,
                                        "note": "INGESTED"}  # fmt: skip
    assert all(c.patched for c in root.children)
    assert root.outputs == {"state": "REFERRED"} and root.patched  # REFERRED is a resting state


def test_a_step_that_waits_is_ended_as_waiting_and_an_error_is_recorded():
    t, roots = tracer()
    with t.step(case("SUBMITTING")):
        pass  # no transition: waiting on the write outcome
    with pytest.raises(ValueError), t.step(case("SUBMITTING")):
        raise ValueError("boom")
    waiting, failed = roots[0].children
    assert waiting.outputs == {"waiting": True}
    assert failed.error == "ValueError: boom"
    assert roots[0].outputs is None  # the case is still in progress


def test_an_external_move_gets_its_own_child_and_a_new_cycle_a_new_root():
    t, roots = tracer()
    t.transition(case("INGESTED"), "INGESTED", "REFERRED", {"why": "deadline"}, True)
    assert roots[0].children[0].name == "INGESTED → REFERRED (external)"
    with t.step(case("INGESTED", cycle=2)):
        pass
    assert [r.name for r in roots] == ["case K1 · cycle 1", "case K1 · cycle 2"]


def test_outputs_are_json_safe():
    t, roots = tracer()
    body = {"amount": Decimal("1.50"), "at": datetime(2026, 1, 1, tzinfo=UTC)}
    with t.step(case("DECIDED")):
        t.transition(case("DECIDED"), "DECIDED", "DRAFTING", body, False)
    json.dumps(roots[0].children[0].outputs)  # no TypeError
    assert roots[0].children[0].outputs["amount"] == "1.50"


def test_closing_ends_open_cases_and_flushes():
    t, roots = tracer()
    with t.step(case("INGESTED")):
        t.transition(case("INGESTED"), "INGESTED", "ASSESSED", {}, False)
    t.close()
    assert roots[0].outputs["state"] == "ASSESSED" and "later run" in roots[0].outputs["note"]
    assert t.client.flushed


def test_the_orchestrator_is_traced_only_when_tracing_is_on():
    from nirnaya.orchestrator.ports import NULL_OBSERVER
    from nirnaya.runtime import build_orchestrator
    from tests.harness import Harness

    h = Harness()
    off = build_orchestrator(h.store, h.aw, h.aw, h.world, h.settings, env={})
    assert off.observer is NULL_OBSERVER
    on = build_orchestrator(
        h.store, h.aw, h.aw, h.world, h.settings,
        env={"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "k"},
    )  # fmt: skip
    assert isinstance(on.observer, CaseTracer)


def test_steps_get_strictly_increasing_start_times():
    t, roots = tracer()
    for state in ("INGESTED", "ASSESSED", "DECIDED"):
        with t.step(case(state)):
            pass
    starts = [roots[0].kwargs["start_time"]] + [c.kwargs["start_time"] for c in roots[0].children]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
