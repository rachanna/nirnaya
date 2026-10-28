"""Ingestion: read disputes from the source, snapshot every change, open cases and cycles.

The source is only read. Ingestion never decides and never writes to Airwallex.
"""

from datetime import datetime

from nirnaya.models.dispute import Dispute
from nirnaya.orchestrator import states
from nirnaya.orchestrator.deferral import closed_reason
from nirnaya.orchestrator.ports import DisputeSource
from nirnaya.orchestrator.states import CaseState
from nirnaya.store.repository import Store, StoreError


def ingest(store: Store, source: DisputeSource, merchant_id: str, now: datetime) -> list[str]:
    """Pull this merchant's disputes and record what changed. Returns the case ids touched."""
    merchant = store.merchant(merchant_id)
    if merchant is None:
        raise StoreError(f"unknown merchant {merchant_id!r}")
    touched = []
    for dispute in source.list_disputes(merchant_id):
        if dispute.merchant_id != merchant_id:
            _reject(store, merchant_id, dispute, "listed for another merchant", now)
            continue
        owner = store.case_owner(dispute.id)
        if owner is not None and owner != merchant_id:
            _reject(
                store, merchant_id, dispute, "dispute id already owned by another merchant", now
            )
            continue
        if store.latest_dispute(merchant_id, dispute.id) == dispute:
            # Unchanged, but a case that was mid-write when the dispute resolved closes now.
            case = store.case(merchant_id, dispute.id)
            if case is not None and _close_if_resolved(
                store, merchant_id, case.state, dispute, now
            ):
                touched.append(dispute.id)
            continue
        with store.transaction():
            _record(store, merchant_id, merchant.active_policy_version, dispute, now)
        touched.append(dispute.id)
    _surface_refused(store, source, merchant_id, now)
    return touched


def _surface_refused(store: Store, source: DisputeSource, merchant_id: str, now: datetime) -> None:
    """Disputes the source could not read never become cases, so their deadlines would pass
    unseen. Each is audited once per reason (with its raw due_at), for a person to handle."""
    refused = getattr(source, "refused", None)
    if refused is None:
        return
    seen = {
        (e["body"].get("dispute_id"), e["body"].get("why"))
        for e in store.merchant_events(merchant_id, {"INGEST_UNMAPPABLE"})
    }
    for dispute_id, info in refused(merchant_id).items():
        if (dispute_id, info.get("why")) in seen:
            continue
        with store.transaction():
            store.audit(
                merchant_id,
                None,
                "INGEST_UNMAPPABLE",
                {"dispute_id": dispute_id, "why": info.get("why"), "due_at": info.get("due_at")},
                now,
            )


# A write in flight is reconciled by the write gateway first; ingestion never closes it.
_NOT_CLOSED_BY_INGEST = {CaseState.CLOSED, CaseState.SUBMITTING}


def _close_if_resolved(
    store: Store, merchant_id: str, state: str, dispute: Dispute, now: datetime
) -> bool:
    current = CaseState(state)
    why = closed_reason(dispute)
    if why is None or current in _NOT_CLOSED_BY_INGEST:
        return False
    states.check(current, CaseState.CLOSED, external=True)
    with store.transaction():
        store.update_case(merchant_id, dispute.id, state=CaseState.CLOSED, at=now)
        store.audit(
            merchant_id,
            dispute.id,
            "CLOSED_BY_AIRWALLEX",
            {"status": dispute.status, "why": why, "from_state": current},
            now,
        )
    return True


def _reject(store: Store, merchant_id: str, dispute: Dispute, why: str, now: datetime) -> None:
    with store.transaction():
        store.audit(
            merchant_id, None, "INGEST_REJECTED", {"dispute_id": dispute.id, "why": why}, now
        )


def _record(
    store: Store, merchant_id: str, policy_version: str, dispute: Dispute, now: datetime
) -> None:
    previous = store.latest_dispute(merchant_id, dispute.id)
    digest = store.add_snapshot(merchant_id, dispute, now)
    case = store.case(merchant_id, dispute.id)
    snapshot = {"stage": dispute.stage, "status": dispute.status, "sha256": digest}

    if case is None:
        store.create_case(merchant_id, dispute.id, CaseState.INGESTED, dispute.due_at, now)
        store.start_cycle(
            merchant_id, dispute.id, 1, stage=dispute.stage, policy_version=policy_version, at=now
        )
        store.audit(merchant_id, dispute.id, "CASE_OPENED", snapshot, now)
        return

    current = CaseState(case.state)
    if current is CaseState.CLOSED:
        store.audit(merchant_id, dispute.id, "DISPUTE_UPDATED_AFTER_CLOSE", snapshot, now)
        return

    if previous is not None and previous.stage != dispute.stage:
        states.check(current, CaseState.INGESTED, external=True)
        cycle = case.cycle + 1
        store.start_cycle(
            merchant_id,
            dispute.id,
            cycle,
            stage=dispute.stage,
            policy_version=policy_version,
            at=now,
        )
        closed = store.close_earlier_cycle_referrals(merchant_id, dispute.id, cycle, now)
        store.update_case(
            merchant_id,
            dispute.id,
            state=CaseState.INGESTED,
            cycle=cycle,
            due_at=dispute.due_at,
            urgent=False,
            at=now,
        )
        store.audit(
            merchant_id,
            dispute.id,
            "NEW_CYCLE",
            {**snapshot, "cycle": cycle, "from_state": current, "referrals_closed": closed},
            now,
        )
        return

    if (why := closed_reason(dispute)) is not None and current not in _NOT_CLOSED_BY_INGEST:
        states.check(current, CaseState.CLOSED, external=True)
        store.update_case(merchant_id, dispute.id, state=CaseState.CLOSED, at=now)
        store.audit(
            merchant_id,
            dispute.id,
            "CLOSED_BY_AIRWALLEX",
            {**snapshot, "why": why, "from_state": current},
            now,
        )
        return

    amended = previous is not None and str(previous.amount) != str(dispute.amount)
    if amended and current in states.EXTERNAL_REASSESS:
        # Anything decided, approved or rendered on the old amount is void: decide again.
        states.check(current, CaseState.ASSESSED, external=True)
        superseded = store.supersede_referrals(merchant_id, dispute.id, case.cycle, now)
        store.update_case(
            merchant_id,
            dispute.id,
            state=CaseState.ASSESSED,
            due_at=dispute.due_at,
            urgent=False,
            at=now,
        )
        store.audit(
            merchant_id,
            dispute.id,
            "DISPUTE_AMENDED",
            {
                **snapshot,
                "amount": str(dispute.amount),
                "was": str(previous.amount),
                "from_state": current,
                "referrals_superseded": superseded,
            },
            now,
        )
        return

    store.update_case(merchant_id, dispute.id, due_at=dispute.due_at, at=now)
    store.audit(merchant_id, dispute.id, "DISPUTE_UPDATED", snapshot, now)
