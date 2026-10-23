"""Milestone 3 proof: every evaluation case, run through the real pipeline (ingest, state
machine, tool gateway, scripted investigator and drafter, verifier, renderer, write gateway)
against the fake Airwallex and the synthetic world, lands on its expected route. No LLM."""

from datetime import timedelta

import pytest

from nirnaya.agent.runs import DraftResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.evaluation.cases import EvalCase, Route
from nirnaya.models import Action, DisputeStatus, Stage
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, close_manually
from nirnaya.orchestrator.states import CaseState
from nirnaya.store import verify_chain
from tests.harness import CASES, Harness


class FixtureDrafter:
    """Drafts exactly the case's fixture claims: the fabrication a careless drafter makes."""

    def __init__(self, case: EvalCase) -> None:
        self._claims = case.claim_objects()

    def draft(self, dispute, records):  # noqa: ANN001, ANN201
        return DraftResult(claims=tuple(self._claims))


def run(case: EvalCase, *, dry_run: bool = True, delegated: bool = True):
    h = Harness(
        dry_run=dry_run,
        delegated=delegated,
        auto_accept_pre_chargeback="sandbox_behaviour" in case.tags,
    )
    h.stage(case)
    drafter = FixtureDrafter(case) if "fabrication" in case.tags else ScriptedDrafter()
    orch = Orchestrator(h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), drafter, h.settings)
    orch.run_until_idle(h.now(case))
    return h, orch


def route_of(h: Harness, case: EvalCase) -> Route:
    m, cid = case.dispute.merchant_id, case.dispute.id
    done = [w for w in h.store.writes(m, cid) if w.status in {"CONFIRMED", "DRY_RUN"}]
    if done:
        return Route(done[-1].action)
    state = h.store.case(m, cid).state
    if state == CaseState.REFERRED:
        return Route.REFER
    if state in {CaseState.DEFERRED, CaseState.CLOSED}:
        return Route.DEFER
    raise AssertionError(f"{cid} stopped in {state}")


@pytest.mark.parametrize("case", list(CASES.values()), ids=lambda c: c.id)
def test_every_case_lands_on_its_expected_route(case):
    h, _ = run(case)
    assert route_of(h, case) is case.expected.route
    assert verify_chain(h.store.conn) is None


@pytest.mark.parametrize("case", list(CASES.values()), ids=lambda c: c.id)
def test_dry_run_never_writes_to_airwallex(case):
    h, _ = run(case)
    assert not {op for op, _ in h.aw.calls} & {"accept", "challenge", "upload"}


def test_engine_decision_on_record_matches_the_expected_engine_action():
    for case in CASES.values():
        exp = case.expected.engine
        if exp is None or case.expected.route is Route.DEFER:
            continue
        h, _ = run(case)
        _, decision = h.store.latest_decision(case.dispute.merchant_id, case.dispute.id, 1)
        assert decision.action is exp.action, case.id


# --- the kit 4 rejection cycle, for real (not dry run) ---------------------------------------


def test_kit_case_1_challenge_rejection_and_second_challenge():
    case = CASES["K1"]
    h, orch = run(case, dry_run=False)
    m, cid = case.dispute.merchant_id, case.dispute.id
    first = h.store.writes(m, cid)
    assert [(w.action, w.status) for w in first] == [("CHALLENGE", "CONFIRMED")]
    assert h.aw.retrieve_dispute(m, cid).status is DisputeStatus.CHALLENGED

    # The issuer rejects the evidence: the sandbox escalates RFI -> CHARGEBACK.
    later = h.now(case) + timedelta(days=5)
    h.aw.simulate_escalate(cid, due_at=later + timedelta(days=15))
    orch.run_until_idle(later)

    case_row = h.store.case(m, cid)
    assert case_row.cycle == 2
    assert h.store.cycle(m, cid, 2).stage == Stage.CHARGEBACK
    writes = h.store.writes(m, cid)
    assert [(w.action, w.cycle, w.status) for w in writes] == [
        ("CHALLENGE", 1, "CONFIRMED"),
        ("CHALLENGE", 2, "CONFIRMED"),
    ]
    assert writes[0].request_id != writes[1].request_id
    assert case_row.state == CaseState.AWAITING_OUTCOME

    h.aw.simulate_resolve(cid, in_favor_of="MERCHANT")
    orch.run_until_idle(later + timedelta(days=1))
    assert h.store.case(m, cid).state == CaseState.CLOSED
    assert verify_chain(h.store.conn) is None


# --- people in the loop ----------------------------------------------------------------------


def test_rfi_accept_is_referred_then_approved_and_accepted():
    case = CASES["F10"]
    h, orch = run(case, dry_run=False)
    m, cid = case.dispute.merchant_id, case.dispute.id
    assert h.store.case(m, cid).state == CaseState.REFERRED
    approve(h.store, m, cid, Action.ACCEPT, approver="ops@example.test", now=h.now(case))
    orch.run_until_idle(h.now(case))
    assert [(w.action, w.status) for w in h.store.writes(m, cid)] == [("ACCEPT", "CONFIRMED")]
    assert h.aw.retrieve_dispute(m, cid).status is DisputeStatus.ACCEPTED


def test_approval_is_void_if_the_amount_changes_before_the_write():
    case = CASES["F10"]
    h, orch = run(case, dry_run=False)
    m, cid = case.dispute.merchant_id, case.dispute.id
    approve(h.store, m, cid, Action.ACCEPT, approver="ops@example.test", now=h.now(case))
    h.aw.set_amount(
        cid, case.dispute.amount.model_copy(update={"amount": case.dispute.amount.amount + 1})
    )
    orch.run_until_idle(h.now(case))
    assert h.store.case(m, cid).state == CaseState.REFERRED
    assert h.store.writes(m, cid) == []


def test_an_approved_challenge_is_still_blocked_by_the_verifier():
    case = CASES["K2C"]  # accepted on economics; the drafter fabricates a signature
    h = Harness(dry_run=False)
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), FixtureDrafter(case), h.settings
    )
    m, cid = case.dispute.merchant_id, case.dispute.id
    # Force the referral path: an approver wants to fight it anyway.
    h.store.register_merchant(
        m,
        airwallex_account_id="acct-demo",
        policy=h.store.policy(m, "demo-2026-10"),
        delegated_authority=False,
    )
    orch.run_until_idle(h.now(case))
    assert h.store.case(m, cid).state == CaseState.REFERRED  # no authority to accept on its own
    approve(h.store, m, cid, Action.CHALLENGE, approver="ops@example.test", now=h.now(case))
    orch.run_until_idle(h.now(case))
    assert h.store.case(m, cid).state == CaseState.REFERRED
    assert h.store.writes(m, cid) == []
    assert not {op for op, _ in h.aw.calls} & {"challenge", "upload"}


def test_a_person_can_close_a_referred_case():
    case = CASES["K3"]
    h, orch = run(case)
    m, cid = case.dispute.merchant_id, case.dispute.id
    close_manually(
        h.store, m, cid, approver="ops@example.test", note="refund issued by hand", now=h.now(case)
    )
    assert h.store.case(m, cid).state == CaseState.CLOSED


# --- deadlines, crashes and timeouts ---------------------------------------------------------


def test_watchdog_refers_a_case_too_close_to_its_deadline():
    case = CASES["N1"]
    h = Harness()
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )
    late = case.dispute.due_at - timedelta(days=1)
    orch.run_until_idle(late)
    m, cid = case.dispute.merchant_id, case.dispute.id
    assert h.store.case(m, cid).state == CaseState.REFERRED
    assert h.store.case(m, cid).urgent
    assert h.store.open_referral_for(m, cid).reasons == ["DEADLINE_NEAR"]


def test_a_crash_mid_step_leaves_the_previous_state_and_reruns_cleanly():
    case = CASES["N1"]
    h = Harness()
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )
    m, cid = case.dispute.merchant_id, case.dispute.id
    now = h.now(case)
    orch.tick(now)  # ingest + INGESTED step
    orch.tick(now)  # ASSESSED -> ...
    state_before = h.store.case(m, cid).state
    decisions_before = h.store.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    original = h.store.update_case

    def crash(*args, **kwargs):
        raise RuntimeError("power cut")

    h.store.update_case = crash
    orch.tick(now)  # the failure is isolated to this case and audited, not raised
    h.store.update_case = original

    assert h.store.case(m, cid).state == state_before
    failed = [e for e in h.store.audit_events(m, cid) if e["event"] == "STEP_FAILED"]
    assert [e["body"] for e in failed] == [{"state": state_before, "error": "RuntimeError"}]
    assert h.store.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == decisions_before
    orch.run_until_idle(now)
    assert route_of(h, case) is case.expected.route
    assert verify_chain(h.store.conn) is None


def test_a_timed_out_write_is_reconciled_on_the_next_tick():
    case = CASES["D2"]
    h = Harness(dry_run=False)
    h.stage(case)
    h.aw.fail_next("accept", "timeout_after_apply")
    orch = Orchestrator(
        h.store, h.aw, h.aw, h.world, ScriptedInvestigator(), ScriptedDrafter(), h.settings
    )
    orch.run_until_idle(h.now(case))
    m, cid = case.dispute.merchant_id, case.dispute.id
    assert [(w.action, w.status) for w in h.store.writes(m, cid)] == [("ACCEPT", "UNKNOWN")]
    assert h.store.case(m, cid).state == CaseState.SUBMITTING  # waits; never a blind resend
    orch.tick(h.now(case) + timedelta(minutes=1))  # the next scheduled tick
    assert [(w.action, w.status) for w in h.store.writes(m, cid)] == [("ACCEPT", "CONFIRMED")]
    assert [op for op, _ in h.aw.calls].count("accept") == 1
    assert h.store.case(m, cid).state == CaseState.AWAITING_OUTCOME
    orch.tick(h.now(case) + timedelta(minutes=2))  # Airwallex shows ACCEPTED: the case closes
    assert h.store.case(m, cid).state == CaseState.CLOSED
