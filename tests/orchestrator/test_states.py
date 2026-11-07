import pytest

from nirnaya.orchestrator.states import (
    FLOW,
    RUNNABLE,
    WATCHDOG_REFERS,
    CaseState,
    IllegalTransition,
    check,
)

S = CaseState


def test_every_state_has_a_flow_entry():
    assert set(FLOW) == set(CaseState)


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (S.INGESTED, S.ASSESSED),
        (S.INGESTED, S.DEFERRED),
        (S.ASSESSED, S.INVESTIGATING),
        (S.INVESTIGATING, S.DECIDED),
        (S.DECIDED, S.DRAFTING),
        (S.DECIDED, S.PRE_WRITE_CHECK),
        (S.DECIDED, S.REFERRED),
        (S.VERIFYING, S.RENDERING),
        (S.RENDERING, S.PRE_WRITE_CHECK),
        (S.PRE_WRITE_CHECK, S.SUBMITTING),
        (S.SUBMITTING, S.AWAITING_OUTCOME),
        (S.REFERRED, S.DRAFTING),
        (S.REFERRED, S.PRE_WRITE_CHECK),
        (S.REFERRED, S.CLOSED),
    ],
)
def test_legal_transitions(frm, to):
    check(frm, to)


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (S.DECIDED, S.SUBMITTING),  # never skip the checklist
        (S.DRAFTING, S.PRE_WRITE_CHECK),  # never skip verification
        (S.VERIFYING, S.SUBMITTING),
        (S.ASSESSED, S.PRE_WRITE_CHECK),
        (S.REFERRED, S.SUBMITTING),  # an approval still goes through the checklist
        (S.AWAITING_OUTCOME, S.DRAFTING),
        (S.CLOSED, S.INGESTED),
    ],
)
def test_illegal_transitions_raise(frm, to):
    with pytest.raises(IllegalTransition):
        check(frm, to)


def test_external_events_reach_new_cycle_close_and_watchdog():
    check(S.AWAITING_OUTCOME, S.INGESTED, external=True)
    check(S.REFERRED, S.CLOSED, external=True)
    check(S.INVESTIGATING, S.REFERRED, external=True)
    with pytest.raises(IllegalTransition):
        check(S.CLOSED, S.INGESTED, external=True)
    # The watchdog never interrupts a write in flight or touches a case waiting on others.
    assert {S.SUBMITTING, S.AWAITING_OUTCOME, S.DEFERRED}.isdisjoint(WATCHDOG_REFERS)


def test_waiting_states_are_not_runnable():
    assert RUNNABLE.isdisjoint({S.DEFERRED, S.AWAITING_OUTCOME, S.REFERRED, S.CLOSED})


def test_a_person_can_send_a_referred_case_back_to_ingestion():
    check(S.REFERRED, S.INGESTED)


@pytest.mark.parametrize("frm", [S.AWAITING_OUTCOME, S.CLOSED])
def test_only_an_external_event_refers_a_waiting_or_closed_case(frm):
    check(frm, S.REFERRED, external=True)
    with pytest.raises(IllegalTransition):
        check(frm, S.REFERRED)
