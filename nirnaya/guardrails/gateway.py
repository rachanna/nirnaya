"""The write gateway: the only code that can accept or challenge a dispute (architecture 7).

It takes ids, not objects, and reloads everything from the store, so a caller cannot pass a
doctored decision. Before any write it re-fetches the dispute and runs the checklist; every
write carries a deterministic request_id and goes through the ledger; DRY_RUN records the
exact request without sending it. A timed-out write is reconciled by re-fetching, never by a
blind resend.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from nirnaya.agent.targets import counterevidence_lookups
from nirnaya.engine import decide
from nirnaya.evidence.derive import (
    contradictory_record_ids,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.guardrails.binding import binding_still_holds
from nirnaya.guardrails.challenge_reason import challenge_reason
from nirnaya.guardrails.customer_info import customer_info
from nirnaya.guardrails.request_ids import request_id
from nirnaya.models.base import DomainModel
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import ACTIONABLE_STATUS, AcceptReason, Action, DisputeStatus, Stage
from nirnaya.orchestrator.ports import (
    AirwallexError,
    ChallengeRequest,
    DisputeActions,
    DisputeSource,
    WriteOutcomeUnknown,
)
from nirnaya.orchestrator.settings import Settings
from nirnaya.store.repository import Store, StoreError
from nirnaya.verifier.result import VerificationOutcome


class GuardFailure(StrEnum):
    REFETCH_FAILED = "REFETCH_FAILED"
    STATUS_CHANGED = "STATUS_CHANGED"
    DISPUTE_CHANGED = "DISPUTE_CHANGED"
    TOO_CLOSE_TO_DEADLINE = "TOO_CLOSE_TO_DEADLINE"
    NO_AUTHORITY = "NO_AUTHORITY"
    APPROVAL_VOID = "APPROVAL_VOID"
    DECISION_CHANGED = "DECISION_CHANGED"
    DECISION_NOT_SUPPORTED_BY_VERIFIED_CLAIMS = "DECISION_NOT_SUPPORTED_BY_VERIFIED_CLAIMS"
    NOT_VERIFIED = "NOT_VERIFIED"
    EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
    FILES_MISSING = "FILES_MISSING"
    WRITE_REJECTED = "WRITE_REJECTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    # A record that could contradict the challenge was never looked at (architecture 6).
    COUNTEREVIDENCE_INCOMPLETE = "COUNTEREVIDENCE_INCOMPLETE"
    # A write already exists under this request_id with a different payload (architecture 7.3).
    REQUEST_CONFLICT = "REQUEST_CONFLICT"
    # Another write of this cycle may have landed: nothing goes out on top of it.
    WRITE_UNRESOLVED = "WRITE_UNRESOLVED"


class Ledger(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DRY_RUN = "DRY_RUN"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


LANDED = {Action.ACCEPT: DisputeStatus.ACCEPTED, Action.CHALLENGE: DisputeStatus.CHALLENGED}


class WriteResult(DomainModel):
    ok: bool
    action: Action
    request_id: str | None = None
    ledger: Ledger | None = None
    failure: GuardFailure | None = None
    detail: str = ""
    dry_run: bool = False
    # The engine's referral reasons behind a refusal, so the person who decides next sees them.
    referral_reasons: tuple[str, ...] = ()


class _Refused(Exception):
    def __init__(
        self, failure: GuardFailure, detail: str, referral_reasons: Iterable[str] = ()
    ) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail
        self.referral_reasons = tuple(referral_reasons)

    def result(self, action: Action) -> WriteResult:
        return WriteResult(
            ok=False,
            action=action,
            failure=self.failure,
            detail=self.detail,
            referral_reasons=self.referral_reasons,
        )


class WriteGateway:
    def __init__(
        self, store: Store, source: DisputeSource, actions: DisputeActions, settings: Settings
    ) -> None:
        self._store = store
        self._source = source
        self._actions = actions
        self._settings = settings

    # --- public -----------------------------------------------------------------------------

    def preflight(
        self, merchant_id: str, case_id: str, cycle: int, action: Action, *, now: datetime
    ) -> WriteResult:
        """Run the checklist without writing anything."""
        try:
            self._check(merchant_id, case_id, cycle, action, now)
        except _Refused as r:
            return r.result(action)
        return WriteResult(ok=True, action=action, dry_run=self._settings.dry_run)

    def submit(
        self, merchant_id: str, case_id: str, cycle: int, action: Action, *, now: datetime
    ) -> WriteResult:
        """Checklist, ledger, then (unless dry run) the write. Safe to call again."""
        try:
            ctx = self._check(merchant_id, case_id, cycle, action, now, reconcile=True)
        except _Refused as r:
            return r.result(action)
        if isinstance(ctx, WriteResult):
            return ctx  # already done earlier: idempotent
        return self._write(merchant_id, case_id, cycle, action, ctx, now)

    # --- checklist ----------------------------------------------------------------------------

    def _check(
        self,
        merchant_id: str,
        case_id: str,
        cycle: int,
        action: Action,
        now: datetime,
        *,
        reconcile: bool = False,
    ) -> dict[str, Any] | WriteResult:
        store = self._store
        case = store.case(merchant_id, case_id)
        latest = store.latest_decision(merchant_id, case_id, cycle)
        decided_on = store.latest_dispute(merchant_id, case_id)
        merchant = store.merchant(merchant_id)
        if case is None or latest is None or decided_on is None or merchant is None:
            raise _Refused(GuardFailure.DECISION_CHANGED, "no decision on record for this cycle")
        decision_id, decision = latest
        cycle_row = store.cycle(merchant_id, case_id, cycle)
        # A rejected write is final for its id; the next attempt is a new operation. A timeout
        # (UNKNOWN or PENDING) is not counted, so its retry reuses the same id.
        failed = sum(
            1
            for w in store.writes(merchant_id, case_id)
            if w.cycle == cycle and w.action == action and w.status == Ledger.FAILED
        )
        rid = request_id(
            merchant_id=merchant_id,
            case_id=case_id,
            cycle=cycle,
            stage=cycle_row.stage,
            action=action.value,
            decision_id=decision_id,
            attempt=1 + failed,
        )

        # 9. Idempotency: an earlier write for this exact operation.
        prior = store.write(merchant_id, rid)
        if prior is not None and prior.status in {Ledger.CONFIRMED, Ledger.DRY_RUN}:
            return WriteResult(
                ok=True,
                action=action,
                request_id=rid,
                ledger=Ledger(prior.status),
                dry_run=prior.dry_run,
                detail="already submitted",
            )

        # 1. Re-fetch.
        try:
            fresh = self._source.retrieve_dispute(merchant_id, case_id)
        except AirwallexError as e:
            raise _Refused(GuardFailure.REFETCH_FAILED, str(e)) from e

        unresolved = prior is not None and prior.status in {Ledger.UNKNOWN, Ledger.PENDING}
        if reconcile and unresolved and fresh.status is LANDED[action]:
            with store.transaction():
                store.update_write(
                    merchant_id,
                    rid,
                    status=Ledger.CONFIRMED,
                    response={"reconciled": True, "status": fresh.status},
                    at=now,
                )
                store.audit(merchant_id, case_id, "WRITE_RECONCILED", {"request_id": rid}, now)
            return WriteResult(
                ok=True,
                action=action,
                request_id=rid,
                ledger=Ledger.CONFIRMED,
                detail="landed before the timeout",
            )

        # 1b. A write under another id that may have landed. A new decision gives a new id, which
        # Airwallex cannot deduplicate, so nothing goes out until it is known what landed.
        self._no_write_in_doubt(merchant_id, case_id, cycle, rid, fresh, now, reconcile=reconcile)

        # 2. Status.
        if fresh.status is not ACTIONABLE_STATUS:
            raise _Refused(GuardFailure.STATUS_CHANGED, f"status is {fresh.status}")
        # 3. Stage, amount and refund are those the decision was made on (not merely the last
        # snapshot). Decisions stored before the refund was recorded have nothing to compare.
        refunded = decision.inputs.get("refunded", str(fresh.refunded))
        if (
            fresh.stage is not decision.stage
            or str(fresh.amount) != decision.inputs["amount"]
            or str(fresh.refunded) != refunded
        ):
            raise _Refused(
                GuardFailure.DISPUTE_CHANGED, "stage, amount or refund changed since decision"
            )
        # 4. Deadline buffer.
        buffer = self._settings.deadline_buffer[fresh.stage]
        if now >= fresh.due_at - buffer:
            raise _Refused(GuardFailure.TOO_CLOSE_TO_DEADLINE, f"within {buffer} of {fresh.due_at}")

        # 5. Authority: delegated, or a human approval that still binds.
        referral = store.latest_approval(merchant_id, case_id, cycle)
        approval = referral.approval if referral is not None else None
        if approval is not None and approval.get("action") == action.value:
            if (why := binding_still_holds(approval, store, fresh, action)) is not None:
                raise _Refused(GuardFailure.APPROVAL_VOID, why)
        else:
            approval = None
            if not merchant.delegated_authority:
                raise _Refused(GuardFailure.NO_AUTHORITY, "merchant has not delegated authority")

        records = store.evidence(merchant_id, case_id)
        policy = store.policy(merchant_id, cycle_row.policy_version)
        ownership = bool(cycle_row.ownership_resolved)
        ctx: dict[str, Any] = {"rid": rid, "fresh": fresh, "decision": decision}

        # 7a-b. For a challenge: a PASSED verification whose cited records are intact. Checked
        # before the decision re-run, which would otherwise hide tampering as a changed decision.
        if action is Action.CHALLENGE:
            ctx |= self._check_verified(merchant_id, case_id, cycle, records)
            # 7c. Every record that could contradict the challenge was looked at. The drafting
            # sweep does this; checked again here so no path can skip it. Never fetches.
            called = store.tools_called(merchant_id, case_id, cycle)
            relied_on = ctx["verification"].established
            if unchecked := counterevidence_lookups(fresh, records, called, relied_on):
                raise _Refused(
                    GuardFailure.COUNTEREVIDENCE_INCOMPLETE, f"never looked at: {unchecked}"
                )

        # 6. The decision, re-derived on the fresh dispute, still says this action.
        rerun = self._decide(fresh, policy, records, ownership)
        if approval is None:
            if rerun.action is not action:
                raise _Refused(
                    GuardFailure.DECISION_CHANGED,
                    f"decision is now {rerun.action}",
                    [r.value for r in rerun.referral_reasons],
                )
        else:
            new = set(rerun.referral_reasons) - set(approval.get("referral_reasons", []))
            if new:
                raise _Refused(
                    GuardFailure.APPROVAL_VOID,
                    f"new referral reasons {sorted(new)}",
                    sorted(r.value for r in rerun.referral_reasons),
                )
        ctx["accept_reason"] = rerun.accept_reason or decision.accept_reason

        if action is Action.CHALLENGE:
            # Section 6: without an approval, the verified facts alone must still say CHALLENGE.
            if approval is None:
                established = ctx["verification"].established
                supported = decide(fresh, policy, established, ownership_resolved=ownership)
                if supported.action is not Action.CHALLENGE:
                    raise _Refused(
                        GuardFailure.DECISION_NOT_SUPPORTED_BY_VERIFIED_CLAIMS,
                        f"verified facts alone give {supported.action}",
                    )
            # 7c. Rendered evidence exists.
            render = store.render(merchant_id, case_id, cycle, ctx["draft"])
            if render is None:
                raise _Refused(GuardFailure.FILES_MISSING, "no rendered evidence for the draft")
            ctx["render"] = render
            # The request's reason and product type, decided in code (architecture 9.5).
            ctx["challenge_reason"] = challenge_reason(
                fresh.reason_category, ctx["verification"].established
            )
            ctx["product_type"] = policy.product_type if fresh.stage is Stage.CHARGEBACK else None
            # The customer's device and IP, only from an order record the verified claims cite.
            ctx["customer_info"] = customer_info(
                fresh.stage,
                records,
                cited={c.claim.source_record_id for c in ctx["verification"].claims},
            )
        return ctx

    def _no_write_in_doubt(
        self,
        merchant_id: str,
        case_id: str,
        cycle: int,
        rid: str,
        fresh: Dispute,
        now: datetime,
        *,
        reconcile: bool,
    ) -> None:
        store = self._store
        for w in store.writes(merchant_id, case_id):
            if w.cycle != cycle or w.request_id == rid:
                continue
            if w.status not in {Ledger.UNKNOWN, Ledger.PENDING}:
                continue
            if reconcile and fresh.status is LANDED[Action(w.action)]:
                with store.transaction():
                    store.update_write(
                        merchant_id,
                        w.request_id,
                        status=Ledger.CONFIRMED,
                        response={"reconciled": True, "status": fresh.status},
                        at=now,
                    )
                    store.audit(
                        merchant_id, case_id, "WRITE_RECONCILED", {"request_id": w.request_id}, now
                    )
                continue
            raise _Refused(
                GuardFailure.WRITE_UNRESOLVED,
                f"{w.action} {w.request_id} may have landed: check Airwallex before acting",
            )

    def _decide(self, dispute: Dispute, policy: Any, records: Any, ownership: bool) -> Decision:
        return decide(
            dispute,
            policy,
            evidence_present(records, dispute),
            ownership_resolved=ownership,
            contradictory_record_ids=contradictory_record_ids(records, dispute),
            unanswered_contact_ids=unanswered_customer_contacts(records, dispute),
        )

    def _check_verified(
        self, merchant_id: str, case_id: str, cycle: int, records: list[Any]
    ) -> dict[str, Any]:
        store = self._store
        draft = store.latest_draft(merchant_id, case_id, cycle)
        verification = store.verification(merchant_id, case_id, cycle, draft) if draft else None
        if verification is None or verification.outcome is not VerificationOutcome.PASSED:
            raise _Refused(GuardFailure.NOT_VERIFIED, "no PASSED verification for the latest draft")
        capture = store.capture_hashes(merchant_id, case_id)
        by_id = {r.id: r for r in records}
        for c in verification.claims:
            rec = by_id.get(c.claim.source_record_id)
            if rec is None or not rec.hash_ok() or capture.get(rec.id) != rec.sha256:
                raise _Refused(GuardFailure.EVIDENCE_CHANGED, f"record {c.claim.source_record_id}")
        return {
            "draft": draft,
            "verification": verification,
            "claim_ids": tuple(c.claim.id for c in verification.claims),
        }

    # --- the write ------------------------------------------------------------------------------

    def _write(
        self,
        merchant_id: str,
        case_id: str,
        cycle: int,
        action: Action,
        ctx: dict[str, Any],
        now: datetime,
    ) -> WriteResult:
        store, rid, dry = self._store, ctx["rid"], self._settings.dry_run
        request: dict[str, Any] = {"request_id": rid, "dispute_id": case_id, "action": action}
        if action is Action.ACCEPT:
            request["reason"] = ctx["accept_reason"] or AcceptReason.OTHERS
        else:
            request["claim_ids"] = list(ctx["claim_ids"])
            request["evidence_sha256"] = ctx["render"]["sha256"]
            request["challenge_reason"] = ctx["challenge_reason"]
            request["product_type"] = ctx["product_type"]
            info = ctx["customer_info"]
            request["customer_info"] = None if info is None else info.model_dump(mode="json")
        earlier = store.write(merchant_id, rid)
        in_doubt = earlier is not None and earlier.status in {Ledger.UNKNOWN, Ledger.PENDING}
        if dry and in_doubt and not earlier.dry_run:
            # A dry run must not record over a real write that may have landed.
            return WriteResult(
                ok=False,
                action=action,
                request_id=rid,
                ledger=Ledger(earlier.status),
                failure=GuardFailure.OUTCOME_UNKNOWN,
                detail="a real write may have landed: a dry run leaves it as it is",
            )
        if earlier is not None and earlier.request != json.loads(json.dumps(request, default=str)):
            # Retrying the same operation reuses its id; a different payload under that id
            # (e.g. a re-draft while the first write's outcome is unknown) is never sent.
            return WriteResult(
                ok=False,
                action=action,
                request_id=rid,
                ledger=Ledger(earlier.status),
                failure=GuardFailure.REQUEST_CONFLICT,
                detail=f"request {rid} was already used for a different payload "
                f"(ledger {earlier.status}); check Airwallex before acting",
            )
        if earlier is None:
            with store.transaction():
                store.insert_write(
                    merchant_id,
                    case_id,
                    cycle,
                    request_id=rid,
                    action=action,
                    request=request,
                    status=Ledger.PENDING,
                    dry_run=dry,
                    at=now,
                )
        if dry:
            with store.transaction():
                store.update_write(merchant_id, rid, status=Ledger.DRY_RUN, response=None, at=now)
                store.audit(merchant_id, case_id, "WRITE_DRY_RUN", request, now)
            return WriteResult(
                ok=True, action=action, request_id=rid, ledger=Ledger.DRY_RUN, dry_run=True
            )
        try:
            if action is Action.ACCEPT:
                result = self._actions.accept(
                    merchant_id, case_id, request_id=rid, reason=request["reason"]
                )
            else:
                file_id = self._upload(merchant_id, case_id, cycle, ctx)
                result = self._actions.challenge(
                    merchant_id,
                    case_id,
                    ChallengeRequest(
                        request_id=rid,
                        file_ids=(file_id,),
                        claim_ids=ctx["claim_ids"],
                        reason=ctx["challenge_reason"],
                        product_type=ctx["product_type"],
                        customer_info=ctx["customer_info"],
                    ),
                )
        except WriteOutcomeUnknown as e:
            with store.transaction():
                store.update_write(merchant_id, rid, status=Ledger.UNKNOWN, response=None, at=now)
                store.audit(merchant_id, case_id, "WRITE_OUTCOME_UNKNOWN", {"request_id": rid}, now)
            return WriteResult(
                ok=False,
                action=action,
                request_id=rid,
                ledger=Ledger.UNKNOWN,
                failure=GuardFailure.OUTCOME_UNKNOWN,
                detail=str(e),
            )
        except (AirwallexError, StoreError) as e:
            # A failed resend says nothing about the first send, which may still have landed.
            status = Ledger.UNKNOWN if in_doubt else Ledger.FAILED
            with store.transaction():
                store.update_write(
                    merchant_id, rid, status=status, response={"error": str(e)}, at=now
                )
                store.audit(
                    merchant_id,
                    case_id,
                    "WRITE_REJECTED",
                    {"request_id": rid, "error": str(e), "ledger": status},
                    now,
                )
            return WriteResult(
                ok=False,
                action=action,
                request_id=rid,
                ledger=status,
                failure=GuardFailure.WRITE_REJECTED,
                detail=str(e),
            )
        with store.transaction():
            store.update_write(
                merchant_id,
                rid,
                status=Ledger.CONFIRMED,
                response={"status": result.status},
                at=now,
            )
            store.audit(
                merchant_id,
                case_id,
                "WRITE_CONFIRMED",
                {"request_id": rid, "status": result.status},
                now,
            )
        return WriteResult(ok=True, action=action, request_id=rid, ledger=Ledger.CONFIRMED)

    def _upload(self, merchant_id: str, case_id: str, cycle: int, ctx: dict[str, Any]) -> str:
        render = ctx["render"]
        if render["file_id"]:
            return render["file_id"]
        file_id = self._actions.upload_file(
            merchant_id, filename=render["filename"], content=render["content"]
        )
        with self._store.transaction():
            self._store.set_render_file_id(merchant_id, case_id, cycle, ctx["draft"], file_id)
        return file_id
