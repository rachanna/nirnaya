"""FC-12 and FC-13 (docs/failure-cases.md): a dispute that needs an answer never goes quiet.

A case closed by hand swallowed a later escalation, and nothing watched a case after its write.
In both, Airwallex showed REQUIRES_RESPONSE while Nirnaya raised nothing, up to the deadline.
"""

from datetime import timedelta

import pytest

from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models.enums import DisputeStatus, Stage
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import close_manually, needs_fresh_decision, send_back
from nirnaya.orchestrator.states import CaseState
from nirnaya.trace import case_trace
from tests.harness import CASES, Harness

M = DEMO_POLICY.merchant_id
AP = "ops@example.test"


def orch(h):
    return Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )


def events(h, cid):
    return [e["event"] for e in h.store.audit_events(M, cid)]


def closed_by_hand():
    """K1 at RFI goes to a person, who closes it. Then the issuer escalates."""
    h = Harness(dry_run=False, delegated=False)
    h.stage(CASES["K1"])
    o = orch(h)
    now = h.now(CASES["K1"])
    o.run_until_idle(now)
    close_manually(h.store, M, "K1", approver=AP, note="handled outside", now=now)
    assert h.store.case(M, "K1").state == CaseState.CLOSED
    return h, o, now


# --- FC-12 ----------------------------------------------------------------------------------


def test_an_escalation_after_a_hand_close_goes_to_a_person():
    h, o, now = closed_by_hand()
    due = now + timedelta(days=20)
    h.aw.simulate_escalate("K1", due_at=due)
    o.run_until_idle(now + timedelta(days=1))

    case = h.store.case(M, "K1")
    assert (case.state, case.cycle, case.due_at, case.urgent) == (
        CaseState.REFERRED,
        2,
        due,
        True,
    )
    referral = h.store.open_referral_for(M, "K1")
    assert referral.reasons == ["REOPENED_AFTER_CLOSE"] and referral.urgent
    assert referral.detail["stage"] == "CHARGEBACK"
    assert "REOPENED" in events(h, "K1")
    assert any("reopened" in line for line in case_trace(h.store, M, "K1"))
    assert h.store.writes(M, "K1") == []


def test_try_again_decides_the_reopened_case_in_its_new_stage():
    h, o, now = closed_by_hand()
    h.aw.simulate_escalate("K1", due_at=now + timedelta(days=20))
    later = now + timedelta(days=1)
    o.run_until_idle(later)
    assert needs_fresh_decision(h.store, M, "K1")

    send_back(h.store, M, "K1", approver=AP, now=later)
    o.run_until_idle(later)
    decision = h.store.latest_decision(M, "K1", 2)[1]
    assert decision.stage is Stage.CHARGEBACK


def test_a_case_airwallex_closed_is_reopened_too():
    h = Harness(dry_run=False)
    h.stage(CASES["K2"])
    o = orch(h)
    now = h.now(CASES["K2"])
    o.run_until_idle(now)
    assert h.store.case(M, "K2").state == CaseState.CLOSED

    entry = h.aw._disputes["K2"]
    entry.dispute = entry.dispute.model_copy(
        update={
            "stage": Stage.PRE_ARBITRATION,
            "status": DisputeStatus.REQUIRES_RESPONSE,
            "due_at": now + timedelta(days=20),
        }
    )
    o.run_until_idle(now + timedelta(days=1))
    assert h.store.case(M, "K2").state == CaseState.REFERRED
    assert h.store.open_referral_for(M, "K2").reasons == ["REOPENED_AFTER_CLOSE"]


def test_a_change_on_a_closed_case_that_needs_no_answer_stays_closed():
    h, o, now = closed_by_hand()
    entry = h.aw._disputes["K1"]
    entry.dispute = entry.dispute.model_copy(update={"status": DisputeStatus.CHALLENGED})
    o.run_until_idle(now + timedelta(days=1))
    assert h.store.case(M, "K1").state == CaseState.CLOSED
    assert "DISPUTE_UPDATED_AFTER_CLOSE" in events(h, "K1")
    assert "REOPENED" not in events(h, "K1")


def test_a_new_stage_that_needs_no_answer_stays_closed():
    h, o, now = closed_by_hand()
    entry = h.aw._disputes["K1"]
    entry.dispute = entry.dispute.model_copy(
        update={"stage": Stage.CHARGEBACK, "status": DisputeStatus.CHALLENGED}
    )
    o.run_until_idle(now + timedelta(days=1))
    assert h.store.case(M, "K1").state == CaseState.CLOSED


# --- FC-13 ----------------------------------------------------------------------------------


def waiting(label="D2", dry_run=True):
    h = Harness(dry_run=dry_run)
    h.stage(CASES[label])
    o = orch(h)
    o.run_until_idle(h.now(CASES[label]))
    return h, o


def test_a_write_that_does_not_show_goes_to_a_person_near_the_deadline():
    h, o = waiting()
    case = h.store.case(M, "D2")
    assert case.state == CaseState.AWAITING_OUTCOME
    o.run_until_idle(case.due_at - timedelta(hours=1))

    case = h.store.case(M, "D2")
    assert case.state == CaseState.REFERRED and case.urgent
    referral = h.store.open_referral_for(M, "D2")
    assert referral.reasons == ["OUTCOME_UNCONFIRMED"] and referral.urgent
    assert referral.detail["ledger"] == "DRY_RUN"
    assert referral.detail["action"] == "ACCEPT"
    assert referral.detail["request_id"]
    assert "Airwallex" in referral.detail["what_to_do"]


def test_it_is_raised_once():
    h, o = waiting()
    due = h.store.case(M, "D2").due_at
    for hours in (2, 1):
        o.run_until_idle(due - timedelta(hours=hours))
    states = [e for e in h.store.audit_events(M, "D2") if e["event"] == "OUTCOME_UNCONFIRMED"]
    assert len(states) == 1


def test_it_waits_for_the_deadline_buffer():
    h, o = waiting()
    case = h.store.case(M, "D2")
    o.run_until_idle(case.due_at - timedelta(days=10))
    assert h.store.case(M, "D2").state == CaseState.AWAITING_OUTCOME


@pytest.mark.parametrize(
    "status", [DisputeStatus.CHALLENGED, DisputeStatus.ACCEPTED, DisputeStatus.WON]
)
def test_a_dispute_airwallex_moved_on_raises_nothing(status):
    h, o = waiting()
    entry = h.aw._disputes["D2"]
    entry.dispute = entry.dispute.model_copy(update={"status": status})
    case = h.store.case(M, "D2")
    o.run_until_idle(case.due_at - timedelta(hours=1))
    assert h.store.open_referral_for(M, "D2") is None


def test_a_challenge_that_landed_raises_nothing():
    h, o = waiting("N1", dry_run=False)
    case = h.store.case(M, "N1")
    assert case.state == CaseState.AWAITING_OUTCOME
    o.run_until_idle(case.due_at - timedelta(hours=1))
    assert h.store.case(M, "N1").state == CaseState.AWAITING_OUTCOME
    assert h.store.open_referral_for(M, "N1") is None
