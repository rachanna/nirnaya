"""Commands a human approver issues on a referred case.

An approval is bound to what the approver saw (guardrails.binding). Approving a challenge sends
the case back to drafting: a person can override the economics, never the verifier.

Quotes the reader proposed from notes (architecture 8.8) are confirmed with the approval of a
challenge, or rejected. Approving resolves every proposed quote of the cycle: the ones named are
CONFIRMED, the rest REJECTED, so none is left counting for nothing or waiting forever.

A case with no decision on the current facts has nothing to approve: every approval would fail
at the gateway. A person sends it back to be decided again instead.
"""

from collections.abc import Iterable
from datetime import datetime

from nirnaya.guardrails.binding import binding
from nirnaya.models.enums import Action
from nirnaya.orchestrator import states
from nirnaya.orchestrator.states import CaseState
from nirnaya.store.repository import Finding, Store, StoreError


def _referred(store: Store, merchant_id: str, case_id: str):  # noqa: ANN202
    case = store.case(merchant_id, case_id)
    if case is None or case.state != CaseState.REFERRED:
        raise StoreError(f"case {case_id!r} is not awaiting a person")
    referral = store.open_referral_for(merchant_id, case_id)
    if referral is None:
        raise StoreError(f"case {case_id!r} has no open referral")
    return case, referral


def _proposed(
    store: Store, merchant_id: str, case_id: str, cycle: int, ids: Iterable[str]
) -> tuple[dict[str, Finding], list[str]]:
    """The cycle's proposed findings, and the named ones (each must be among them)."""
    findings = store.findings(merchant_id, case_id, cycle)
    proposed = {f.finding_id: f for f in findings if f.status == "PROPOSED"}
    wanted = list(dict.fromkeys(ids))
    if unknown := [i for i in wanted if i not in proposed]:
        raise StoreError(f"not a proposed finding of case {case_id!r}: {unknown}")
    return proposed, wanted


def needs_fresh_decision(store: Store, merchant_id: str, case_id: str) -> bool:
    """The case waits for a person but has no decision on the current facts: none was reached, or
    the amount or refund changed since (the gateway's own comparison). Not while a write may
    have landed: a new decision means a new request id, which Airwallex cannot deduplicate."""
    case = store.case(merchant_id, case_id)
    if case is None or case.state != CaseState.REFERRED:
        return False
    if any(
        w.cycle == case.cycle and w.status in {"PENDING", "UNKNOWN"}
        for w in store.writes(merchant_id, case_id)
    ):
        return False
    latest = store.latest_decision(merchant_id, case_id, case.cycle)
    if latest is None:
        return True
    dispute = store.latest_dispute(merchant_id, case_id)
    refunded = str(dispute.refunded)
    inputs = latest[1].inputs
    return str(dispute.amount) != inputs["amount"] or refunded != inputs.get("refunded", refunded)


def approve(
    store: Store,
    merchant_id: str,
    case_id: str,
    action: Action,
    *,
    approver: str,
    now: datetime,
    confirm_findings: Iterable[str] = (),
) -> None:
    if action not in {Action.ACCEPT, Action.CHALLENGE}:
        raise ValueError("a person approves ACCEPT or CHALLENGE")
    case, referral = _referred(store, merchant_id, case_id)
    if needs_fresh_decision(store, merchant_id, case_id):
        raise StoreError(f"case {case_id!r} has no decision on the current facts: send it back")
    proposed, confirmed = _proposed(store, merchant_id, case_id, case.cycle, confirm_findings)
    if confirmed and action is not Action.CHALLENGE:
        raise ValueError("quotes are confirmed only to support a challenge")
    dispute = store.latest_dispute(merchant_id, case_id)
    quotes = [proposed[i].as_quote() for i in confirmed]
    # A referral from a guard names the guard, not the reasons a person already accepted.
    earlier = store.latest_approval(merchant_id, case_id, case.cycle)
    accepted = (earlier.approval or {}).get("referral_reasons", []) if earlier else []
    reasons = sorted({*referral.reasons, *accepted})
    approval = binding(store, dispute, action, reasons, approver, quotes)
    to = CaseState.PRE_WRITE_CHECK if action is Action.ACCEPT else CaseState.DRAFTING
    states.check(CaseState.REFERRED, to)
    with store.transaction():
        for finding_id in proposed:
            status = "CONFIRMED" if finding_id in confirmed else "REJECTED"
            store.resolve_finding(merchant_id, case_id, finding_id, status, by=approver, at=now)
        store.resolve_referral(
            merchant_id,
            referral.referral_id,
            resolution=f"APPROVED_{action}",
            approval=approval,
            at=now,
        )
        store.update_case(merchant_id, case_id, state=to, urgent=False, at=now)
        store.audit(
            merchant_id,
            case_id,
            "APPROVED",
            {
                "action": action,
                "approver": approver,
                "to": to,
                "referral_id": referral.referral_id,
                "confirmed_findings": confirmed,
                "rejected_findings": [i for i in proposed if i not in confirmed],
            },
            now,
        )


def send_back(
    store: Store, merchant_id: str, case_id: str, *, approver: str, now: datetime
) -> None:
    """A person asks for a new decision. As with an amendment, what stood on the old facts is
    void, and the case goes through the normal flow again."""
    case, referral = _referred(store, merchant_id, case_id)
    if not needs_fresh_decision(store, merchant_id, case_id):
        raise StoreError(
            f"case {case_id!r} cannot be sent back: its decision stands, or a write may have landed"
        )
    states.check(CaseState.REFERRED, CaseState.INGESTED)
    with store.transaction():
        store.resolve_referral(
            merchant_id, referral.referral_id, resolution="SENT_BACK", approval=None, at=now
        )
        store.supersede_referrals(merchant_id, case_id, case.cycle, now)
        store.reopen_findings(merchant_id, case_id, case.cycle)
        store.update_case(merchant_id, case_id, state=CaseState.INGESTED, urgent=False, at=now)
        store.audit(
            merchant_id,
            case_id,
            "SENT_BACK",
            {
                "approver": approver,
                "referral_id": referral.referral_id,
                "reasons": referral.reasons,
            },
            now,
        )


def reject_findings(
    store: Store,
    merchant_id: str,
    case_id: str,
    finding_ids: Iterable[str],
    *,
    approver: str,
    now: datetime,
) -> None:
    """A person says these quotes do not show what was proposed. The case stays with a person,
    who then approves the decision or handles it manually."""
    case, _ = _referred(store, merchant_id, case_id)
    _, rejected = _proposed(store, merchant_id, case_id, case.cycle, finding_ids)
    with store.transaction():
        for finding_id in rejected:
            store.resolve_finding(merchant_id, case_id, finding_id, "REJECTED", by=approver, at=now)
        store.audit(
            merchant_id,
            case_id,
            "FINDINGS_REJECTED",
            {"approver": approver, "findings": rejected},
            now,
        )


def close_manually(
    store: Store, merchant_id: str, case_id: str, *, approver: str, note: str, now: datetime
) -> None:
    case, referral = _referred(store, merchant_id, case_id)
    states.check(CaseState.REFERRED, CaseState.CLOSED)
    with store.transaction():
        store.resolve_referral(
            merchant_id, referral.referral_id, resolution="HANDLED_MANUALLY", approval=None, at=now
        )
        store.update_case(merchant_id, case_id, state=CaseState.CLOSED, at=now)
        store.audit(
            merchant_id, case_id, "CLOSED_MANUALLY", {"approver": approver, "note": note}, now
        )
