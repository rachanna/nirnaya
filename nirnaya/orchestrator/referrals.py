"""Commands a human approver issues on a referred case.

An approval is bound to what the approver saw (guardrails.binding). Approving a challenge sends
the case back to drafting: a person can override the economics, never the verifier.
"""

from datetime import datetime

from nirnaya.guardrails.binding import binding
from nirnaya.models.enums import Action
from nirnaya.orchestrator import states
from nirnaya.orchestrator.states import CaseState
from nirnaya.store.repository import Store, StoreError


def _referred(store: Store, merchant_id: str, case_id: str):  # noqa: ANN202
    case = store.case(merchant_id, case_id)
    if case is None or case.state != CaseState.REFERRED:
        raise StoreError(f"case {case_id!r} is not awaiting a person")
    referral = store.open_referral_for(merchant_id, case_id)
    if referral is None:
        raise StoreError(f"case {case_id!r} has no open referral")
    return case, referral


def approve(
    store: Store, merchant_id: str, case_id: str, action: Action, *, approver: str, now: datetime
) -> None:
    if action not in {Action.ACCEPT, Action.CHALLENGE}:
        raise ValueError("a person approves ACCEPT or CHALLENGE")
    case, referral = _referred(store, merchant_id, case_id)
    dispute = store.latest_dispute(merchant_id, case_id)
    approval = binding(store, dispute, action, referral.reasons, approver)
    to = CaseState.PRE_WRITE_CHECK if action is Action.ACCEPT else CaseState.DRAFTING
    states.check(CaseState.REFERRED, to)
    with store.transaction():
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
            {"action": action, "approver": approver, "to": to, "referral_id": referral.referral_id},
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
