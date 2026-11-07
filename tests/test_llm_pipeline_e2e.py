"""Milestone 4: the LLM investigator and drafter (driven by a scripted model) through the real
pipeline. The model chooses lookups and proposes claims; the engine, verifier and gateway
still decide what happens."""

from nirnaya.agent.llm_drafter import SUBMIT
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import CaseState
from nirnaya.runtime import build_orchestrator
from nirnaya.store import verify_chain
from tests.agent.llm_helpers import all_text_sent, call, finish
from tests.harness import CASES, Harness

LLM_MODE = {"NIRNAYA_AGENT": "llm"}


def run(case_id, responses, settings=None):
    h = Harness()
    if settings is not None:
        h.settings = settings
    case = CASES[case_id]
    h.stage(case)
    llm = ScriptedLLM(list(responses))
    orch = build_orchestrator(h.store, h.aw, h.aw, h.world, h.settings, env=LLM_MODE, llm=llm)
    orch.run_until_idle(h.now(case))
    m, cid = case.dispute.merchant_id, case.dispute.id
    return h, llm, m, cid


def submit(*items):
    return reply(None, call(SUBMIT, 50, claims=list(items)))


def writes(h, m, cid):
    return [(w.action, w.status) for w in h.store.writes(m, cid)]


def test_n1_model_fetches_the_carrier_then_the_case_challenges():
    h, llm, m, cid = run(
        "N1",
        [
            reply(None, call("get_carrier_tracking", key="TRK-N1")),
            finish(),
            submit(
                {
                    "source_record_id": "N1-carrier",
                    "source_field": "status",
                    "establishes": "DELIVERY_CONFIRMED",
                    "extracted_value": "DELIVERED",
                    "value_kind": "ENUM",
                    "text": "DELIVERED",
                }
            ),
        ],
    )
    assert writes(h, m, cid) == [("CHALLENGE", "DRY_RUN")]
    runs = h.store.agent_runs(m, cid, 1)
    assert [r["kind"] for r in runs] == ["INVESTIGATE", "DRAFT"]
    assert h.store.agent_tokens(m, cid, 1) == 45  # three model calls, 15 tokens each
    assert verify_chain(h.store.conn) is None


def test_s5_model_fetches_the_pair_then_the_case_challenges():
    h, llm, m, cid = run(
        "S5",
        [
            reply(
                None,
                call("get_login_history", 1, key="CUST-S5"),
                call("get_policy_acceptance", 2, key="CUST-S5"),
            ),
            finish(),
            submit(
                {
                    "source_record_id": "S5-policy",
                    "source_field": "cancellation_terms_accepted_at",
                    "establishes": "CANCELLATION_TERMS_ACCEPTED",
                    "extracted_value": "2025-11-03T10:00:00Z",
                    "value_kind": "DATETIME",
                    "at": "2025-11-03T10:00:00Z",
                },
                {
                    "source_record_id": "S5-login",
                    "source_field": "active_after_renewal_at",
                    "establishes": "USAGE_AFTER_RENEWAL",
                    "extracted_value": "2026-02-20T08:00:00Z",
                    "value_kind": "DATETIME",
                    "at": "2026-02-20T08:00:00Z",
                },
            ),
        ],
    )
    assert writes(h, m, cid) == [("CHALLENGE", "DRY_RUN")]


def test_k3_the_model_is_not_called_and_never_sees_the_emails():
    h, llm, m, cid = run("K3", [])
    assert h.store.case(m, cid).state == CaseState.REFERRED
    assert llm.calls == []  # unknown reason, nothing worth fetching, no drafting


def test_a1_injection_in_customer_email_never_reaches_the_model():
    h, llm, m, cid = run("A1", [reply(None, call("get_carrier_tracking", key="TRK-A1")), finish()])
    assert "ignore previous instructions" not in all_text_sent(llm).lower()
    assert writes(h, m, cid) == [("ACCEPT", "DRY_RUN")]  # in transit: accepted


def test_a_greedy_model_is_held_to_the_targets():
    h, llm, m, cid = run(
        "N1",
        [
            reply(
                None,
                call("get_login_history", 1, key="CUST-N1"),
                call("get_policy_acceptance", 2, key="CUST-N1"),
            ),
            reply(None, call("get_carrier_tracking", 3, key="TRK-N1")),
            finish(),
            submit(
                {
                    "source_record_id": "N1-carrier",
                    "source_field": "status",
                    "establishes": "DELIVERY_CONFIRMED",
                    "extracted_value": "DELIVERED",
                    "value_kind": "ENUM",
                    "text": "DELIVERED",
                }
            ),
        ],
    )
    refused = [e for e in h.store.audit_events(m, cid) if e["event"] == "TOOL_REFUSED"]
    assert len(refused) == 2
    ids = {r.id for r in h.store.evidence(m, cid)}
    assert "N1-login" not in ids and "N1-policy" not in ids
    assert writes(h, m, cid) == [("CHALLENGE", "DRY_RUN")]


def test_the_token_budget_stops_investigation():
    tight = Settings(max_tokens_per_cycle=10)
    h, llm, m, cid = run(
        "S5",
        [
            reply(None, call("get_login_history", 1, key="CUST-S5")),
            finish(),
        ],
        settings=tight,
    )
    # One round used 15 tokens > 10: no second round; the engine decides on what was found.
    assert len([r for r in h.store.agent_runs(m, cid, 1) if r["kind"] == "INVESTIGATE"]) == 1
    assert h.store.case(m, cid).state == CaseState.REFERRED  # RFI accept needs a person


def test_a_drafter_failure_refers_the_case_and_records_the_run():
    h, llm, m, cid = run(
        "N1",
        [
            reply(None, call("get_carrier_tracking", key="TRK-N1")),
            finish(),
            reply("I would rather describe it in prose."),
        ],
    )
    assert h.store.case(m, cid).state == CaseState.REFERRED
    assert h.store.open_referral_for(m, cid).reasons == ["DRAFT_EMPTY"]
    assert [r["kind"] for r in h.store.agent_runs(m, cid, 1)] == ["INVESTIGATE", "DRAFT"]


SIGNED_BY = {
    "source_record_id": "N1-carrier",
    "source_field": "signed_by",
    "establishes": "DELIVERY_SIGNATURE",
    "extracted_value": "J. Smith",
    "value_kind": "NAME",
    "text": "J. Smith",
}


def test_a_fabricating_model_never_gets_a_write():
    # It is told why its draft was blocked and drafts again (architecture 8.6); it fabricates
    # again, so a person decides, seeing every draft.
    h, llm, m, cid = run(
        "N1",
        [
            reply(None, call("get_carrier_tracking", key="TRK-N1")),
            finish(),
            submit(SIGNED_BY),
            submit(SIGNED_BY),
        ],
    )
    assert h.store.case(m, cid).state == CaseState.REFERRED
    assert writes(h, m, cid) == []
    # The referral itself shows the approver which claim failed and why (audit L4).
    detail = h.store.open_referral_for(m, cid).detail
    assert detail["outcome"] == "BLOCKED"
    assert detail["earlier_drafts"][0]["blocked_claims"][0]["kind"] == "OVERREACH"
    (blocked,) = [c for c in detail["claims"] if c["status"] == "BLOCKED"]
    assert (blocked["source_record_id"], blocked["source_field"]) == ("N1-carrier", "signed_by")
    assert blocked["failures"] and all(f["check"] and f["detail"] for f in blocked["failures"])


def test_a_failed_step_rolls_back_but_its_model_run_is_kept():
    """FC-19: the model call happened, so the audit shows it and its tokens count. The step's
    own effects still roll back: no decision, no move."""
    h = Harness()
    case = CASES["N1"]
    h.stage(case)
    llm = ScriptedLLM([reply(None, call("get_carrier_tracking", key="TRK-N1")), finish()])
    orch = build_orchestrator(h.store, h.aw, h.aw, h.world, h.settings, env=LLM_MODE, llm=llm)
    now = h.now(case)
    orch.tick(now)  # INGESTED
    orch.tick(now)  # ASSESSED -> INVESTIGATING
    original = h.store.update_case

    def crash(*a, **k):
        raise RuntimeError("power cut")

    h.store.update_case = crash
    orch.tick(now)  # INVESTIGATING step: the model runs, then the commit fails (and is isolated)
    h.store.update_case = original
    merchant = case.dispute.merchant_id
    assert [r["kind"] for r in h.store.agent_runs(merchant, "N1", 1)] == ["INVESTIGATE"]
    assert h.store.case(merchant, "N1").state == "INVESTIGATING"
    events = [e["event"] for e in h.store.audit_events(merchant, "N1")]
    assert "STEP_FAILED" in events
