"""Self-correction: a blocked draft goes back to the model drafter once, with the verifier's
findings, and the redraft is verified from scratch. Format fixes go out on their own; a draft
that overreached needs a person even when the redraft passes."""

from nirnaya.agent.llm_drafter import SUBMIT, LLMDrafter
from nirnaya.agent.runs import DraftResult
from nirnaya.agent.scripted import ScriptedInvestigator
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.models import Action
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import FLOW, CaseState
from tests.agent.llm_helpers import call
from tests.harness import CASES, Harness

CASE = CASES["N1"]
M, CID = CASE.dispute.merchant_id, CASE.dispute.id
DELIVERED = {
    "source_record_id": "N1-carrier",
    "source_field": "status",
    "establishes": "DELIVERY_CONFIRMED",
    "extracted_value": "DELIVERED",
    "value_kind": "ENUM",
    "text": "DELIVERED",
}
PROSE = {**DELIVERED, "extracted_value": "Status: DELIVERED"}  # true, wrong form
SIGNED = {  # the carrier record has no signed_by: an overreach
    "source_record_id": "N1-carrier",
    "source_field": "signed_by",
    "establishes": "DELIVERY_SIGNATURE",
    "extracted_value": "J. Smith",
    "value_kind": "NAME",
    "text": "J. Smith",
}


def submission(*items):
    return reply(None, call(SUBMIT, claims=list(items)))


def run(*drafts, settings=None, drafter=None):
    h = Harness()
    h.stage(CASE)
    llm = ScriptedLLM([submission(*d) for d in drafts])
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(),
        drafter or LLMDrafter(llm, env={}), settings or h.settings,
    )  # fmt: skip
    orch.run_until_idle(h.now(CASE))
    return h, orch, llm


def writes(h):
    return [(w.action, w.status) for w in h.store.writes(M, CID)]


def events(h, name):
    return [e["body"] for e in h.store.audit_events(M, CID) if e["event"] == name]


def test_verifying_may_go_back_to_drafting_and_nothing_else_is_new():
    assert CaseState.DRAFTING in FLOW[CaseState.VERIFYING]


def test_a_value_in_the_wrong_form_is_fixed_and_the_challenge_goes_out_on_its_own():
    h, _, llm = run([PROSE], [DELIVERED])
    assert writes(h) == [("CHALLENGE", "DRY_RUN")]
    assert h.store.latest_draft(M, CID, 1) == 2
    (asked,) = events(h, "REDRAFT_REQUESTED")
    assert asked["draft"] == 1 and asked["kinds"] == ["FORMAT"]
    # The second call saw the verifier's findings, as data.
    prompt = llm.calls[1]["messages"][0].text
    block = prompt[prompt.index('<untrusted_data source="verifier-feedback">') :]
    assert "EXTRACTED_VALUE_MISMATCH" in block.split("</untrusted_data>")[0]
    assert "EXTRACTED_VALUE_MISMATCH" not in llm.calls[1]["system"]


def test_a_fixed_overreach_still_goes_to_a_person_who_sees_both_drafts():
    h, orch, llm = run([DELIVERED, SIGNED], [DELIVERED])
    assert writes(h) == []
    referral = h.store.open_referral_for(M, CID)
    assert referral.reasons == ["REDRAFT_AFTER_OVERREACH"]
    (earlier,) = referral.detail["earlier_drafts"]
    assert earlier["blocked_claims"][0]["source_field"] == "signed_by"
    assert earlier["blocked_claims"][0]["kind"] == "OVERREACH"
    assert [c["source"] for c in referral.detail["redraft"]["claims"]] == ["N1-carrier.status"]
    approve(h.store, M, CID, Action.CHALLENGE, approver="ops@example.test", now=h.now(CASE))
    orch.run_until_idle(h.now(CASE))
    assert writes(h) == [("CHALLENGE", "DRY_RUN")]
    # What goes out is the redraft the person reviewed (verified again), not a new draft.
    assert len(llm.calls) == 2 and h.store.latest_draft(M, CID, 1) == 2


def test_one_redraft_only_then_a_person_sees_every_draft():
    h, _, llm = run([PROSE], [PROSE])
    assert len(llm.calls) == 2 and writes(h) == []
    referral = h.store.open_referral_for(M, CID)
    assert referral.reasons == ["VERIFICATION_BLOCKED"]
    assert [d["draft"] for d in referral.detail["earlier_drafts"]] == [1]


def test_no_redraft_once_the_token_budget_is_spent():
    h, _, llm = run([PROSE], [DELIVERED], settings=Settings(dry_run=True, max_tokens_per_cycle=10))
    assert len(llm.calls) == 1
    assert h.store.open_referral_for(M, CID).reasons == ["VERIFICATION_BLOCKED"]


def test_a_drafter_that_cannot_revise_is_never_asked_to():
    class Careless:
        def __init__(self):
            self.calls = 0

        def draft(self, dispute, records):
            self.calls += 1
            built = LLMDrafter(ScriptedLLM([submission(PROSE)]), env={}).draft(dispute, records)
            return DraftResult(claims=built.claims)

    careless = Careless()
    h, _, _ = run(drafter=careless)
    assert careless.calls == 1
    assert h.store.open_referral_for(M, CID).reasons == ["VERIFICATION_BLOCKED"]
    assert events(h, "REDRAFT_REQUESTED") == []


def test_a_redraft_that_fails_outright_still_shows_the_first_draft():
    h, _, llm = run([SIGNED])  # no second answer: the model call fails
    assert len(llm.calls) == 2
    referral = h.store.open_referral_for(M, CID)
    assert referral.reasons == ["DRAFT_EMPTY"]
    assert referral.detail["earlier_drafts"][0]["blocked_claims"][0]["source_field"] == "signed_by"
