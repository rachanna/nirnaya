"""A write in flight is never interrupted, but a stalled one is never silent either.

SUBMITTING is exempt from the step-failure referral and the watchdog, because a write may
have landed. A handler that raises there on every tick used to retry forever unseen. Now the
case is marked urgent and SUBMISSION_STALLED is audited (once per cause), with no transition,
no cancel and no resend; the console lists it as a stalled write.
"""

from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness

CASE = CASES["K2"]  # a low-value accept: decided, then written
M, CID = CASE.dispute.merchant_id, CASE.dispute.id


def submitting():
    h = Harness()
    h.prepare(CASE)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )
    with h.store.transaction():
        h.store.update_case(M, CID, state=CaseState.SUBMITTING, at=h.now(CASE))

    def broken(*args, **kwargs):
        raise RuntimeError("store or transport fault")

    orch.gateway.submit = broken
    return h, orch


def stalled_events(h):
    return [e for e in h.store.audit_events(M, CID) if e["event"] == "SUBMISSION_STALLED"]


def test_repeated_failures_in_submitting_raise_one_alert_and_change_nothing_else():
    h, orch = submitting()
    now = h.now(CASE)
    for _ in range(5):
        orch.tick(now)
    case = h.store.case(M, CID)
    assert case.state == CaseState.SUBMITTING and case.urgent  # still in flight, now visible
    (event,) = stalled_events(h)
    assert event["body"]["cause"] == "STEP_FAILED" and event["body"]["failures"] == 3
    assert h.store.writes(M, CID) == []  # nothing cancelled, nothing resent
    assert h.store.open_referral_for(M, CID) is None


def test_a_submitting_case_past_the_deadline_buffer_is_flagged_once():
    h, orch = submitting()
    late = CASE.dispute.due_at - h.settings.deadline_buffer[CASE.dispute.stage]
    orch.tick(late)
    orch.tick(late)
    causes = [e["body"]["cause"] for e in stalled_events(h)]
    assert causes.count("DEADLINE_BUFFER") == 1
    assert h.store.case(M, CID).urgent


def test_the_console_lists_stalled_writes():
    from nirnaya.console.views import stalled_writes

    h, orch = submitting()
    for _ in range(3):
        orch.tick(h.now(CASE))

    class Session:
        store = h.store
        now = h.now(CASE)

    (row,) = stalled_writes(Session())
    assert row.case_id == CID and "STEP_FAILED" in row.causes
