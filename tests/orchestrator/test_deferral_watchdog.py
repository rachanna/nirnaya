"""A deferral is a prediction that Airwallex will act; the watchdog checks it came true.

D3 is a chargeback already fully refunded: Nirnaya defers it ("Airwallex auto-defends"). If
the deadline buffer arrives and Airwallex has not acted (still REQUIRES_RESPONSE), a person
must look now: the case is referred, urgent, with the original deferral reason. If Airwallex
did act, the case closes or moves on as before and is never referred.
"""

from datetime import timedelta

from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness

CASE = CASES["D3"]
M, CID = CASE.dispute.merchant_id, CASE.dispute.id


def deferred():
    h = Harness()
    h.stage(CASE)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )
    orch.run_until_idle(h.now(CASE))
    assert h.store.case(M, CID).state == CaseState.DEFERRED
    return h, orch


def buffer_reached(h):
    return CASE.dispute.due_at - h.settings.deadline_buffer[CASE.dispute.stage]


def test_an_unconfirmed_deferral_is_referred_urgently_at_the_deadline_buffer():
    h, orch = deferred()
    orch.run_until_idle(buffer_reached(h))  # Airwallex never acted: still REQUIRES_RESPONSE
    case = h.store.case(M, CID)
    assert case.state == CaseState.REFERRED and case.urgent
    referral = h.store.open_referral_for(M, CID)
    assert referral.reasons == ["DEFERRAL_UNCONFIRMED"] and referral.urgent
    assert "auto-defends" in referral.detail["deferral"]
    assert referral.detail["status"] == "REQUIRES_RESPONSE"
    assert "directly in Airwallex" in referral.detail["action"]
    events = [e["event"] for e in h.store.audit_events(M, CID)]
    assert "DEFERRAL_UNCONFIRMED" in events


def test_before_the_buffer_a_deferred_case_waits():
    h, orch = deferred()
    orch.run_until_idle(buffer_reached(h) - timedelta(minutes=1))
    assert h.store.case(M, CID).state == CaseState.DEFERRED


def test_a_deferral_airwallex_honoured_closes_and_is_never_referred():
    h, orch = deferred()
    h.aw.simulate_resolve(CID, in_favor_of="MERCHANT")  # Airwallex defended it and won
    orch.run_until_idle(buffer_reached(h))
    assert h.store.case(M, CID).state == CaseState.CLOSED
    assert h.store.open_referral_for(M, CID) is None


def test_a_referred_deferral_is_not_referred_twice():
    h, orch = deferred()
    orch.run_until_idle(buffer_reached(h))
    orch.run_until_idle(buffer_reached(h) + timedelta(hours=6))
    events = [e["event"] for e in h.store.audit_events(M, CID)]
    assert events.count("DEFERRAL_UNCONFIRMED") == 1
