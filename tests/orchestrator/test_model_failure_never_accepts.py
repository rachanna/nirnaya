"""FC-01 (docs/failure-cases.md): a failed or lazy model must not cause an accept.

An accept gives money up and cannot be undone. Code decides on the records it has, so when the
agent stopped early the records were thin, the engine said accept, and the model's failure had
in effect authorised a refund. Now code makes the lookups that could flip the decision whatever
the agent did, and a search it cannot finish goes to a person.
"""

from datetime import timedelta

import pytest

from nirnaya.agent.runs import AgentRun, InvestigationResult, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import load_cases, synthetic_world
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.llm.fake import reply
from nirnaya.llm.types import LLMError, ToolCall
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import CaseState
from nirnaya.runtime import build_orchestrator
from nirnaya.store import Store, connect
from tests.harness import CASES, Harness
from tests.notes import noted_case

CASE_LIST = load_cases()
WORLD = synthetic_world(CASE_LIST)
M = DEMO_POLICY.merchant_id


class Dead:
    """Every call fails, as with a rate limit or a bad key."""

    provider = "fake"
    model = "fake"

    def complete(self, **_: object) -> None:
        raise LLMError("call failed: RateLimitError")


class Lazy:
    """The model finishes at once and submits nothing."""

    provider = "fake"
    model = "fake"

    def complete(self, *, tools=(), force_tool=None, **_: object):
        if force_tool:
            return reply(None)
        return reply(None, ToolCall(id="f", name="finish", arguments={"reason": "nothing more"}))


def outcome(case, env, llm=None):
    store = Store(connect())
    store.register_merchant(
        M, airwallex_account_id="a", policy=DEMO_POLICY, delegated_authority=True
    )
    aw = FakeAirwallex()
    aw.add_dispute(
        case.dispute,
        attempt=case.payment_attempt,
        intent_owner="same" if case.ownership_resolved else None,
    )
    orch = build_orchestrator(store, aw, aw, WORLD, Settings(dry_run=False), env=env, llm=llm)
    orch.run_until_idle(case.dispute.opened_at + timedelta(hours=1))
    writes = [w.action for w in store.writes(M, case.dispute.id)]
    return store.case(M, case.dispute.id).state, writes


@pytest.mark.parametrize("model", [Dead(), Lazy()], ids=["dead", "lazy"])
@pytest.mark.parametrize("case", CASE_LIST, ids=lambda c: c.id)
def test_a_model_failure_never_adds_an_accept(case, model):
    _, working = outcome(case, {"NIRNAYA_AGENT": "scripted"})
    _, failed = outcome(case, {"NIRNAYA_AGENT": "llm"}, model)
    if "ACCEPT" in failed:
        assert "ACCEPT" in working


def run(label, *, settings=None, investigator=None, reader=None):
    h = Harness(dry_run=False)
    if settings is not None:
        h.settings = settings
    case = CASES[label]
    h.stage(case)
    orch = Orchestrator(
        h.store,
        h.aw,
        h.aw,
        h.world,
        investigator or ScriptedInvestigator(),
        ScriptedDrafter(),
        h.settings,
        reader=reader,
    )
    orch.run_until_idle(h.now(case))
    return h, case.dispute


class Stops:
    """An agent that looks at nothing."""

    def investigate(self, dispute, targets, tools):
        return InvestigationResult(calls=0)


def test_code_finishes_the_lookups_the_agent_skipped():
    h, dispute = run("N1", investigator=Stops())
    assert "get_carrier_tracking:TRK-N1" in h.store.tools_called(M, dispute.id, 1)
    assert [w.action for w in h.store.writes(M, dispute.id)] == ["CHALLENGE"]


def test_the_finished_lookups_are_on_the_record():
    h, dispute = run("N1", investigator=Stops())
    assert any(e["body"].get("finished") for e in h.store.audit_events(M, dispute.id))


def test_a_search_that_cannot_finish_goes_to_a_person_not_to_an_accept():
    h, dispute = run("N1", investigator=Stops(), settings=Settings(dry_run=False, max_tool_calls=2))
    assert h.store.case(M, dispute.id).state == CaseState.REFERRED
    assert h.store.open_referral_for(M, dispute.id).reasons == ["INVESTIGATION_INCOMPLETE"]
    assert h.store.writes(M, dispute.id) == []


class FailedReader:
    def read(self, dispute, facts, records):
        run_ = AgentRun(kind="READ", provider="f", model="f", turns=1, stop="LLM_ERROR: down")
        return ReadResult(run=run_)


def test_notes_that_could_not_be_read_go_to_a_person_not_to_an_accept():
    case = noted_case()
    h = Harness(dry_run=False)
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, synthetic_world([case]), ScriptedInvestigator(), ScriptedDrafter(),
        h.settings, reader=FailedReader(),
    )  # fmt: skip
    orch.run_until_idle(h.now(case))
    assert h.store.case(M, case.dispute.id).state == CaseState.REFERRED
    assert h.store.open_referral_for(M, case.dispute.id).reasons == ["INVESTIGATION_INCOMPLETE"]
    assert h.store.writes(M, case.dispute.id) == []


def test_a_normal_accept_is_untouched():
    h, dispute = run("K2")
    assert [w.action for w in h.store.writes(M, dispute.id)] == ["ACCEPT"]


def test_an_engine_referral_is_untouched():
    h, dispute = run("K3")
    assert h.store.open_referral_for(M, dispute.id).reasons == ["UNANSWERED_CUSTOMER_CONTACT"]
