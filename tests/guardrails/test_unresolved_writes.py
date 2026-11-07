"""FC-07 to FC-10 (docs/failure-cases.md): what the write path does when a write is in doubt.

A write that timed out may have landed. Nothing may go out on top of it, its "may have landed"
warning must survive a failed resend, and a dry run must not overwrite it. And one temporary
failure must not leave a case that no approval can ever send.
"""

from datetime import timedelta
from decimal import Decimal as D

import pytest

from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.guardrails.gateway import Ledger, WriteGateway
from nirnaya.models import Action, DisputeStatus, Money
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.ports import AirwallexError
from nirnaya.orchestrator.referrals import approve
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness

M = DEMO_POLICY.merchant_id
AP = "ops@example.test"


def orch(h, settings=None):
    return Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(),
        settings or h.settings,
    )  # fmt: skip


def staged(label):
    case = CASES[label]
    h = Harness(dry_run=False)
    h.stage(case)
    return case, h, orch(h), h.now(case)


def ledger(h, cid):
    return [(w.action, w.status) for w in h.store.writes(M, cid)]


def until_unknown(o, h, cid, now):
    for _ in range(30):
        case = h.store.case(M, cid)
        if case is not None and case.state == CaseState.SUBMITTING and ledger(h, cid):
            return
        o.tick(now)
    raise AssertionError("no write in doubt")


def sent(h, operation):
    return [c for c in h.aw.calls if c[0] == operation]


# --- FC-07: nothing goes out on top of a write in doubt -----------------------------------------


def amend_behind_the_listing(h, case):
    """The gateway sees an amendment before ingestion does, so it refuses and refers."""
    real = h.aw.list_disputes
    frozen = real(M)
    h.aw.list_disputes = lambda m: frozen
    amount = case.dispute.amount
    h.aw.set_amount(case.dispute.id, Money(amount=amount.amount - D("1.00"), currency="USD"))
    return real


def test_a_second_challenge_does_not_go_out_while_the_first_is_in_doubt():
    case, h, o, now = staged("N1")
    h.aw.fail_next("challenge", "timeout_before_apply")
    until_unknown(o, h, "N1", now)
    real = amend_behind_the_listing(h, case)
    o.tick(now + timedelta(minutes=1))
    h.aw.list_disputes = real
    o.run_until_idle(now + timedelta(minutes=2))
    assert ledger(h, "N1") == [("CHALLENGE", "UNKNOWN")]
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert "GUARD_WRITE_UNRESOLVED" in h.store.open_referral_for(M, "N1").reasons


def test_a_person_cannot_approve_an_accept_on_top_of_a_challenge_in_doubt():
    case, h, o, now = staged("N1")
    h.aw.fail_next("challenge", "timeout_before_apply")
    until_unknown(o, h, "N1", now)
    real = amend_behind_the_listing(h, case)
    o.tick(now + timedelta(minutes=1))
    h.aw.list_disputes = real
    o.run_until_idle(now + timedelta(minutes=2))
    later = now + timedelta(minutes=3)
    approve(h.store, M, "N1", Action.ACCEPT, approver=AP, now=later)
    o.run_until_idle(later)
    assert sent(h, "accept") == []


def test_a_write_in_doubt_that_landed_is_reconciled_under_any_later_attempt():
    case = CASES["N1"]
    h = Harness(dry_run=False)
    h.prepare(case)
    gateway = WriteGateway(h.store, h.aw, h.aw, h.settings)
    now = h.now(case)
    h.aw.fail_next("challenge", "timeout_after_apply")
    assert gateway.submit(M, "N1", 1, Action.CHALLENGE, now=now).ledger is Ledger.UNKNOWN
    result = gateway.submit(M, "N1", 1, Action.ACCEPT, now=now)
    assert result.ok is False
    assert ledger(h, "N1") == [("CHALLENGE", "CONFIRMED")]
    assert sent(h, "accept") == []


# --- FC-08: a failed resend keeps "may have landed" --------------------------------------------


def test_a_failed_resend_does_not_erase_the_doubt():
    case, h, o, now = staged("N1")
    h.aw.fail_next("challenge", "timeout_after_apply")
    until_unknown(o, h, "N1", now)
    real_retrieve, real_list, real_challenge = (
        h.aw.retrieve_dispute,
        h.aw.list_disputes,
        h.aw.challenge,
    )

    def lag(d):
        return d.model_copy(update={"status": DisputeStatus.REQUIRES_RESPONSE})

    def limited(*_a, **_k):
        raise AirwallexError("http_429", "rate limited")

    h.aw.retrieve_dispute = lambda m, i: lag(real_retrieve(m, i))
    h.aw.list_disputes = lambda m: [lag(d) for d in real_list(m)]
    h.aw.challenge = limited
    o.tick(now)
    h.aw.retrieve_dispute, h.aw.list_disputes, h.aw.challenge = (
        real_retrieve,
        real_list,
        real_challenge,
    )
    assert ledger(h, "N1") == [("CHALLENGE", "UNKNOWN")]
    referral = h.store.open_referral_for(M, "N1")
    assert referral is not None
    events = h.store.audit_events(M, "N1")
    assert any(e["body"].get("in_flight") for e in events)


def test_a_first_attempt_that_is_rejected_is_still_a_rejection():
    _case, h, o, now = staged("N1")
    h.aw.fail_next("challenge", "error")
    o.run_until_idle(now)
    assert ledger(h, "N1") == [("CHALLENGE", "FAILED")]


# --- FC-09: a dry run never touches a real write ------------------------------------------------


def test_a_dry_run_does_not_overwrite_a_real_write_in_doubt():
    _case, h, o, now = staged("D2")
    h.aw.fail_next("accept", "timeout_before_apply")
    o.run_until_idle(now)
    assert ledger(h, "D2") == [("ACCEPT", "UNKNOWN")]
    orch(h, Settings(dry_run=True)).run_until_idle(now + timedelta(minutes=5))
    assert ledger(h, "D2") == [("ACCEPT", "UNKNOWN")]
    assert h.store.case(M, "D2").state == CaseState.SUBMITTING
    orch(h, Settings(dry_run=False)).run_until_idle(now + timedelta(minutes=9))
    assert ledger(h, "D2") == [("ACCEPT", "CONFIRMED")]  # resent under the same id, and landed
    assert h.aw._disputes["D2"].dispute.status is DisputeStatus.ACCEPTED


# --- FC-10: one temporary failure does not void every later approval ----------------------------


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("K3", Action.ACCEPT),
        ("F7", Action.CHALLENGE),
        ("F10", Action.ACCEPT),
        ("F4", Action.CHALLENGE),
    ],
)
def test_approval_works_again_after_a_temporary_read_failure(label, action):
    _case, h, o, now = staged(label)
    o.run_until_idle(now)
    approve(h.store, M, label, action, approver=AP, now=now)
    for _ in range(20):
        if h.store.case(M, label).state == CaseState.PRE_WRITE_CHECK:
            break
        o.tick(now)
    h.aw.fail_next("retrieve", "error")
    o.run_until_idle(now)
    assert h.store.open_referral_for(M, label).reasons == ["GUARD_REFETCH_FAILED"]
    later = now + timedelta(minutes=1)
    approve(h.store, M, label, action, approver=AP, now=later)
    o.run_until_idle(later)
    assert ledger(h, label)[-1] == (action.value, "CONFIRMED")


def test_approval_works_again_after_a_rejected_write():
    _case, h, o, now = staged("F7")
    o.run_until_idle(now)
    approve(h.store, M, "F7", Action.CHALLENGE, approver=AP, now=now)
    h.aw.fail_next("challenge", "error")
    o.run_until_idle(now)
    later = now + timedelta(minutes=1)
    approve(h.store, M, "F7", Action.CHALLENGE, approver=AP, now=later)
    o.run_until_idle(later)
    assert ledger(h, "F7") == [("CHALLENGE", "FAILED"), ("CHALLENGE", "CONFIRMED")]


def test_a_reason_that_appears_after_approval_is_shown_and_can_then_be_approved():
    """The new reason voids the approval, but the person is told it and can accept it."""
    case = CASES["D2"]
    h = Harness(dry_run=False)
    h.stage(case)
    o = orch(h)
    now = h.now(case)
    for _ in range(20):
        if h.store.case(M, "D2") and h.store.case(M, "D2").state == CaseState.PRE_WRITE_CHECK:
            break
        o.tick(now)
    h.store.set_ownership(M, "D2", 1, False)  # the engine would now refer on ownership
    o.run_until_idle(now)
    reasons = h.store.open_referral_for(M, "D2").reasons
    assert reasons == ["GUARD_DECISION_CHANGED", "OWNERSHIP_UNRESOLVED"]
    later = now + timedelta(minutes=1)
    approve(h.store, M, "D2", Action.ACCEPT, approver=AP, now=later)
    o.run_until_idle(later)
    assert ledger(h, "D2") == [("ACCEPT", "CONFIRMED")]
