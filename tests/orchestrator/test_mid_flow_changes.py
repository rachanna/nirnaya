"""FC-02 and FC-06 (docs/failure-cases.md): a dispute that changes while a case is in progress.

A refund used to be looked at once, at ingestion, so an accept could go out on a dispute already
refunded in full, paying the customer twice. An amendment used to re-decide without the quotes a
person had seen or confirmed, so it could override them.
"""

from datetime import timedelta
from decimal import Decimal as D

import pytest

from nirnaya.agent.runs import AgentRun, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.evaluation.cases import synthetic_world
from nirnaya.evidence.catalogue import Quote
from nirnaya.guardrails.binding import binding, binding_still_holds
from nirnaya.guardrails.gateway import GuardFailure, WriteGateway
from nirnaya.models import Action, EvidenceType, Money
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness
from tests.notes import QUOTE, noted_case

AP = "ops@example.test"
M = CASES["N1"].dispute.merchant_id


def harness(label):
    case = CASES[label]
    h = Harness(dry_run=False)
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )
    return case, h, orch, h.now(case)


def refund(h, dispute, amount):
    entry = h.aw._disputes[dispute.id]
    refunded = Money(amount=D(amount), currency=dispute.amount.currency)
    entry.dispute = entry.dispute.model_copy(update={"refunded_amount": refunded})


def until(orch, h, cid, state, now):
    for _ in range(40):
        current = h.store.case(M, cid)
        if current is not None and current.state == state:
            return
        orch.tick(now)
    raise AssertionError(f"never reached {state}")


def writes(h, cid):
    return [w.action for w in h.store.writes(M, cid)]


# --- FC-02: refunds -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "state"),
    [
        ("N2", CaseState.PRE_WRITE_CHECK),  # about to accept
        ("N1", CaseState.DRAFTING),  # about to draft a challenge
        ("N1", CaseState.RENDERING),  # verified, about to challenge
    ],
)
def test_a_full_refund_mid_flow_stops_the_write_and_defers(label, state):
    case, h, orch, now = harness(label)
    until(orch, h, case.dispute.id, state, now)
    refund(h, case.dispute, case.dispute.amount.amount)
    orch.run_until_idle(now)
    assert writes(h, case.dispute.id) == []
    assert h.store.case(M, case.dispute.id).state == CaseState.DEFERRED


def test_a_full_refund_while_waiting_for_a_person_closes_the_referral():
    case, h, orch, now = harness("F10")
    orch.run_until_idle(now)
    assert h.store.case(M, "F10").state == CaseState.REFERRED
    refund(h, case.dispute, case.dispute.amount.amount)
    orch.run_until_idle(now + timedelta(hours=1))
    assert h.store.case(M, "F10").state == CaseState.DEFERRED
    assert h.store.open_referral_for(M, "F10") is None
    assert writes(h, "F10") == []


def test_a_partial_refund_voids_an_approval_and_decides_again():
    case, h, orch, now = harness("F10")
    orch.run_until_idle(now)
    approve(h.store, M, "F10", Action.ACCEPT, approver=AP, now=now)
    refund(h, case.dispute, "1.00")
    orch.run_until_idle(now + timedelta(hours=1))
    assert writes(h, "F10") == []  # nobody approved an accept on these facts
    events = [e["event"] for e in h.store.audit_events(M, "F10")]
    assert "DISPUTE_AMENDED" in events


def test_the_gateway_refuses_a_refund_the_decision_did_not_see():
    case = CASES["D2"]
    h = Harness(dry_run=False)
    h.prepare(case)
    refund(h, case.dispute, "10.00")
    gateway = WriteGateway(h.store, h.aw, h.aw, h.settings)
    result = gateway.submit(M, "D2", 1, Action.ACCEPT, now=h.now(case))
    assert result.failure is GuardFailure.DISPUTE_CHANGED
    assert writes(h, "D2") == []


def test_an_approval_is_bound_to_the_refund_it_saw():
    case = CASES["D2"]
    h = Harness()
    h.prepare(case)
    approval = binding(h.store, case.dispute, Action.ACCEPT, [], AP)
    refunded = Money(amount=D("1.00"), currency="USD")
    fresh = case.dispute.model_copy(update={"refunded_amount": refunded})
    assert "refunded" in binding_still_holds(approval, h.store, fresh, Action.ACCEPT)
    assert binding_still_holds(approval, h.store, case.dispute, Action.ACCEPT) is None


def test_an_unchanged_refund_changes_nothing():
    case, h, orch, now = harness("K2")
    orch.run_until_idle(now)
    orch.run_until_idle(now + timedelta(hours=1))
    assert writes(h, "K2") == ["ACCEPT"]


# --- FC-06: amendments and quotes ---------------------------------------------------------------

NOTED = noted_case()
CID = NOTED.dispute.id


class Proposing:
    def read(self, dispute, facts, records):
        quote = Quote(f"{CID}-carrier", "driver_notes", EvidenceType.DELIVERY_SIGNATURE, QUOTE)
        run = AgentRun(kind="READ", provider="fake", model="fake", turns=1, stop="TOOL_USE")
        return ReadResult(proposals=(quote,), run=run)


def noted():
    h = Harness(dry_run=False)
    h.stage(NOTED)
    orch = Orchestrator(
        h.store, h.aw, h.aw, synthetic_world([NOTED]), ScriptedInvestigator(), ScriptedDrafter(),
        h.settings, reader=Proposing(),
    )  # fmt: skip
    now = h.now(NOTED)
    orch.run_until_idle(now)
    return h, orch, now


def amend(h, amount):
    h.aw.set_amount(CID, Money(amount=D(amount), currency="USD"))


def statuses(h):
    return [f.status for f in h.store.findings(M, CID, 1)]


def test_an_amendment_does_not_drop_a_quote_nobody_has_seen():
    h, orch, now = noted()
    amend(h, "121.00")
    orch.run_until_idle(now + timedelta(hours=1))
    assert writes(h, CID) == []
    assert h.store.case(M, CID).state == CaseState.REFERRED
    referral = h.store.open_referral_for(M, CID)
    assert "PROPOSED_EVIDENCE" in referral.reasons
    assert [q["quote"] for q in referral.detail["proposed_evidence"]] == [QUOTE]
    assert statuses(h) == ["PROPOSED"]


def test_an_amendment_does_not_turn_a_persons_challenge_into_an_accept():
    h, orch, now = noted()
    (finding,) = h.store.findings(M, CID, 1)
    approve(
        h.store, M, CID, Action.CHALLENGE, approver=AP, now=now,
        confirm_findings=[finding.finding_id],
    )  # fmt: skip
    amend(h, "121.00")
    orch.run_until_idle(now + timedelta(hours=1))
    assert writes(h, CID) == []
    assert h.store.case(M, CID).state == CaseState.REFERRED
    assert statuses(h) == ["PROPOSED"]  # the person is asked again, on the new amount


def test_a_voided_confirmation_is_never_sent():
    h, orch, now = noted()
    (finding,) = h.store.findings(M, CID, 1)
    approve(
        h.store, M, CID, Action.CHALLENGE, approver=AP, now=now,
        confirm_findings=[finding.finding_id],
    )  # fmt: skip
    amend(h, "600.00")  # the maths alone now says challenge
    orch.run_until_idle(now + timedelta(hours=1))
    assert writes(h, CID) == []
    assert h.store.confirmed_quotes(M, CID, 1) == frozenset()


def test_a_person_can_confirm_again_after_the_amendment():
    h, orch, now = noted()
    amend(h, "121.00")
    later = now + timedelta(hours=1)
    orch.run_until_idle(later)
    (finding,) = h.store.findings(M, CID, 1)
    approve(
        h.store, M, CID, Action.CHALLENGE, approver=AP, now=later,
        confirm_findings=[finding.finding_id],
    )  # fmt: skip
    orch.run_until_idle(later)
    assert writes(h, CID) == ["CHALLENGE"]
