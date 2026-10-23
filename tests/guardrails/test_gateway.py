from datetime import timedelta

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.guardrails.binding import binding
from nirnaya.guardrails.gateway import GuardFailure, Ledger, WriteGateway
from nirnaya.guardrails.request_ids import request_id
from nirnaya.models import Action, DisputeStatus, Money, SourceSystem
from nirnaya.store import verify_chain
from tests.factories import make_claim, make_record
from tests.harness import CASES, Harness

M = DEMO_POLICY.merchant_id
ACCEPT_CASE = CASES["D2"]  # duplicate, no evidence: ACCEPT (OTHERS)
CHALLENGE_CASE = CASES["N1"]  # delivered: CHALLENGE


def gw(h: Harness) -> WriteGateway:
    return WriteGateway(h.store, h.aw, h.aw, h.settings)


def ready(case=ACCEPT_CASE, **harness_kwargs):
    h = Harness(**harness_kwargs)
    h.prepare(case)
    return h, gw(h), case, h.now(case)


def submit(g, case, action, now):
    return g.submit(M, case.dispute.id, 1, action, now=now)


# --- request ids -----------------------------------------------------------------------------


def test_request_ids_are_deterministic_uuids_and_differ_per_operation():
    base = dict(
        merchant_id="m", case_id="c", cycle=1, stage="RFI", action="ACCEPT", decision_id="d"
    )
    rid = request_id(**base)
    assert rid == request_id(**base)
    assert len(rid) == 36 and rid[14] == "5"  # UUID version 5
    assert rid != request_id(**{**base, "action": "CHALLENGE"})
    assert rid != request_id(**{**base, "cycle": 2})
    assert rid != request_id(**{**base, "decision_id": "d2"})


# --- happy paths -----------------------------------------------------------------------------


def test_dry_run_records_the_request_and_sends_nothing():
    h, g, case, now = ready()
    result = submit(g, case, Action.ACCEPT, now)
    assert (result.ok, result.ledger, result.dry_run) == (True, Ledger.DRY_RUN, True)
    assert ("accept", case.dispute.id) not in h.aw.calls
    (row,) = h.store.writes(M, case.dispute.id)
    assert row.status == Ledger.DRY_RUN and row.request["reason"] == "OTHERS"
    assert verify_chain(h.store.conn) is None


def test_accept_is_sent_with_its_reason_when_not_dry_run():
    h, g, case, now = ready(dry_run=False)
    result = submit(g, case, Action.ACCEPT, now)
    assert (result.ok, result.ledger) == (True, Ledger.CONFIRMED)
    assert h.aw.retrieve_dispute(M, case.dispute.id).status is DisputeStatus.ACCEPTED


def test_challenge_uploads_the_rendered_pdf_then_challenges():
    h, g, case, now = ready(CHALLENGE_CASE, dry_run=False)
    result = submit(g, case, Action.CHALLENGE, now)
    assert (result.ok, result.ledger) == (True, Ledger.CONFIRMED)
    ops = [op for op, _ in h.aw.calls if op in {"upload", "challenge"}]
    assert ops == ["upload", "challenge"]
    (fid, (merchant, name, content)) = next(iter(h.aw.files().items()))
    assert merchant == M and name.endswith(".pdf") and content.startswith(b"%PDF-")
    assert h.store.render(M, case.dispute.id, 1, 1)["file_id"] == fid


def test_submitting_twice_writes_once():
    h, g, case, now = ready(dry_run=False)
    first = submit(g, case, Action.ACCEPT, now)
    second = submit(g, case, Action.ACCEPT, now)
    assert second.request_id == first.request_id and second.detail == "already submitted"
    assert [op for op, _ in h.aw.calls].count("accept") == 1


# --- every refusal ---------------------------------------------------------------------------


def test_refetch_failure_refuses():
    h, g, case, now = ready()
    h.aw.fail_next("retrieve", "error")
    assert submit(g, case, Action.ACCEPT, now).failure is GuardFailure.REFETCH_FAILED


def test_status_change_refuses():
    h, g, case, now = ready()
    h.aw.add_dispute(case.dispute.model_copy(update={"status": DisputeStatus.CHALLENGED}))
    assert submit(g, case, Action.ACCEPT, now).failure is GuardFailure.STATUS_CHANGED


def test_amount_change_refuses():
    h, g, case, now = ready()
    h.aw.set_amount(case.dispute.id, Money(amount="301.00", currency="USD"))
    assert submit(g, case, Action.ACCEPT, now).failure is GuardFailure.DISPUTE_CHANGED


def test_deadline_buffer_refuses():
    h, g, case, _ = ready()
    late = case.dispute.due_at - timedelta(days=2)
    assert submit(g, case, Action.ACCEPT, late).failure is GuardFailure.TOO_CLOSE_TO_DEADLINE
    just_in_time = late - timedelta(seconds=1)
    assert submit(g, case, Action.ACCEPT, just_in_time).ok


def test_no_authority_refuses():
    h, g, case, now = ready(delegated=False)
    assert submit(g, case, Action.ACCEPT, now).failure is GuardFailure.NO_AUTHORITY


def test_changed_decision_refuses():
    h, g, case, now = ready(CHALLENGE_CASE)
    contradicting = make_record(
        SourceSystem.CARRIER,
        {"tracking_number": "TRK-N1", "status": "RETURNED_TO_SENDER"},
        id="N1-carrier-late",
        case_id=case.dispute.id,
    )
    h.store.put_evidence(M, contradicting)
    assert submit(g, case, Action.CHALLENGE, now).failure is GuardFailure.DECISION_CHANGED


def test_action_other_than_the_decision_refuses():
    h, g, case, now = ready()
    assert submit(g, case, Action.CHALLENGE, now).failure is GuardFailure.NOT_VERIFIED
    h2, g2, case2, now2 = ready(CHALLENGE_CASE)
    assert submit(g2, case2, Action.ACCEPT, now2).failure is GuardFailure.DECISION_CHANGED


def test_challenge_not_supported_by_verified_facts_refuses():
    # F1 is decided on 3DS (p 0.70). A draft citing only AVS verifies (AVS meets the fraud
    # requirement) but AVS alone gives the base prior, so the verified facts say ACCEPT.
    case = CASES["F1"]
    avs_only = make_claim(
        id="claim-avs",
        case_id=case.dispute.id,
        source_record_id=case.payment_attempt_record_id,
        source_field="avs",
        establishes="AVS_MATCH",
        extracted_value="MATCH",
        normalised_value={"kind": "ENUM", "text": "MATCH"},
    )
    h = Harness()
    h.prepare(case, claims=[avs_only])
    result = gw(h).submit(M, case.dispute.id, 1, Action.CHALLENGE, now=h.now(case))
    assert result.failure is GuardFailure.DECISION_NOT_SUPPORTED_BY_VERIFIED_CLAIMS


def test_blocked_draft_refuses():
    case = CASES["A3"]  # draft overstates the order amount
    h = Harness()
    h.prepare(case, claims=case.claim_objects())
    result = gw(h).submit(M, case.dispute.id, 1, Action.CHALLENGE, now=h.now(case))
    assert result.failure is GuardFailure.NOT_VERIFIED


def test_tampered_evidence_refuses():
    h, g, case, now = ready(CHALLENGE_CASE)
    h.store.conn.execute(
        "UPDATE evidence_records SET record_json = replace(record_json, 'J. Smith', 'J. Smyth')"
        " WHERE record_id = 'N1-carrier'"
    )
    assert submit(g, case, Action.CHALLENGE, now).failure is GuardFailure.EVIDENCE_CHANGED


def test_missing_render_refuses():
    h = Harness()
    h.prepare(CHALLENGE_CASE, render=False)
    result = gw(h).submit(M, "N1", 1, Action.CHALLENGE, now=h.now(CHALLENGE_CASE))
    assert result.failure is GuardFailure.FILES_MISSING


def test_rejected_write_is_recorded_as_failed():
    h, g, case, now = ready(dry_run=False)
    h.aw.fail_next("accept", "error")
    result = submit(g, case, Action.ACCEPT, now)
    assert (result.failure, result.ledger) == (GuardFailure.WRITE_REJECTED, Ledger.FAILED)


# --- timeouts: re-fetch, never blind resend --------------------------------------------------


def test_timeout_after_landing_is_reconciled_without_resending():
    h, g, case, now = ready(dry_run=False)
    h.aw.fail_next("accept", "timeout_after_apply")
    first = submit(g, case, Action.ACCEPT, now)
    assert (first.failure, first.ledger) == (GuardFailure.OUTCOME_UNKNOWN, Ledger.UNKNOWN)
    second = submit(g, case, Action.ACCEPT, now)
    assert (second.ok, second.ledger) == (True, Ledger.CONFIRMED)
    assert [op for op, _ in h.aw.calls].count("accept") == 1
    assert h.store.audit_events(M, case.dispute.id)[-1]["event"] == "WRITE_RECONCILED"


def test_timeout_before_landing_is_resent_with_the_same_request_id():
    h, g, case, now = ready(dry_run=False)
    h.aw.fail_next("accept", "timeout_before_apply")
    first = submit(g, case, Action.ACCEPT, now)
    assert first.ledger is Ledger.UNKNOWN
    second = submit(g, case, Action.ACCEPT, now)
    assert (second.ok, second.ledger, second.request_id) == (
        True,
        Ledger.CONFIRMED,
        first.request_id,
    )
    assert h.aw.retrieve_dispute(M, case.dispute.id).status is DisputeStatus.ACCEPTED


# --- approvals -------------------------------------------------------------------------------


def approve(h, case, action, reasons):
    fresh = h.aw.retrieve_dispute(M, case.dispute.id)
    approval = binding(h.store, fresh, action, reasons, approver="ops@example.test")
    with h.store.transaction():
        rid = h.store.open_referral(M, case.dispute.id, 1, reasons, False, h.now(case))
        h.store.resolve_referral(
            M, rid, resolution=f"APPROVED_{action}", approval=approval, at=h.now(case)
        )


def test_approval_gives_authority_when_not_delegated():
    h, g, case, now = ready(delegated=False)
    approve(h, case, Action.ACCEPT, [])
    assert submit(g, case, Action.ACCEPT, now).ok


def test_approval_lets_a_person_accept_an_rfi():
    case = CASES["F10"]  # RFI where the maths favours accepting: referred for approval
    h = Harness()
    h.prepare(case)
    approve(h, case, Action.ACCEPT, ["RFI_ACCEPT_NEEDS_APPROVAL"])
    assert gw(h).submit(M, case.dispute.id, 1, Action.ACCEPT, now=h.now(case)).ok


def test_approval_is_void_if_the_amount_changes():
    h, g, case, now = ready(delegated=False)
    approve(h, case, Action.ACCEPT, [])
    h.aw.set_amount(case.dispute.id, Money(amount="250.00", currency="USD"))
    result = submit(g, case, Action.ACCEPT, now)
    assert result.failure in {GuardFailure.DISPUTE_CHANGED, GuardFailure.APPROVAL_VOID}


def test_approval_is_void_if_new_evidence_arrives():
    h, g, case, now = ready(delegated=False)
    approve(h, case, Action.ACCEPT, [])
    late = make_record(
        SourceSystem.ORDER,
        {"order_id": "ORD-D2", "note": "late"},
        id="D2-late",
        case_id=case.dispute.id,
    )
    h.store.put_evidence(M, late)
    assert submit(g, case, Action.ACCEPT, now).failure is GuardFailure.APPROVAL_VOID


def test_approval_is_void_if_a_new_referral_reason_appears():
    case = CASES["F10"]
    h = Harness()
    h.prepare(case)
    approve(h, case, Action.ACCEPT, ["RFI_ACCEPT_NEEDS_APPROVAL"])
    h.store.set_ownership(M, case.dispute.id, 1, False)  # ownership now unresolved
    result = gw(h).submit(M, case.dispute.id, 1, Action.ACCEPT, now=h.now(case))
    assert result.failure is GuardFailure.APPROVAL_VOID


def test_an_accept_approval_does_not_authorise_a_challenge():
    h, g, case, now = ready(CHALLENGE_CASE, delegated=False)
    approve(h, case, Action.ACCEPT, [])
    assert submit(g, case, Action.CHALLENGE, now).failure is GuardFailure.NO_AUTHORITY


def test_an_approved_challenge_still_needs_a_passed_verification():
    case = CASES["K2C"]  # fabricated signature in the draft
    h = Harness(delegated=False)
    h.prepare(case, claims=case.claim_objects())
    approve(h, case, Action.CHALLENGE, [])
    result = gw(h).submit(M, case.dispute.id, 1, Action.CHALLENGE, now=h.now(case))
    assert result.failure is GuardFailure.NOT_VERIFIED


def test_preflight_never_writes():
    h, g, case, now = ready(dry_run=False)
    assert g.preflight(M, case.dispute.id, 1, Action.ACCEPT, now=now).ok
    assert h.store.writes(M, case.dispute.id) == []
    assert ("accept", case.dispute.id) not in h.aw.calls


@pytest.mark.parametrize("cid", ["D2", "N1"])
def test_audit_chain_stays_intact(cid):
    h, g, case, now = ready(CASES[cid], dry_run=False)
    submit(g, case, Action.ACCEPT if cid == "D2" else Action.CHALLENGE, now)
    assert verify_chain(h.store.conn) is None
