"""The case state machine and scheduler (architecture section 4).

One step per case per tick. A step's own effects and its state transition commit in one
transaction with an audit event, so a crash leaves the case in its previous state and the
step simply runs again. Steps that reach outside (tool calls, the write gateway) are
idempotent: evidence is immutable, tool calls are remembered per cycle, and writes go through
the request_id ledger. Time is always passed in; nothing here reads a clock.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nirnaya.agent.runs import AgentRun
from nirnaya.agent.targets import investigation_targets, tools_for
from nirnaya.agent.tools import MerchantSystems, Tool, ToolGateway
from nirnaya.engine import decide
from nirnaya.evidence.derive import (
    contradictory_record_ids,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.guardrails.gateway import GuardFailure, WriteGateway
from nirnaya.ingest import ingest
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action, Stage, ValidationStatus
from nirnaya.models.money import FxTable, Money
from nirnaya.orchestrator import states
from nirnaya.orchestrator.deferral import closed_reason, deferral_reason
from nirnaya.orchestrator.ports import DisputeActions, DisputeSource, Drafter, Investigator
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import RUNNABLE, WATCHDOG_REFERS, CaseState
from nirnaya.render.evidence_doc import RenderError, render_evidence
from nirnaya.store.repository import CaseRow, Store
from nirnaya.verifier import VerificationOutcome, VerificationResult, verify

S = CaseState


@dataclass
class _Step:
    to: CaseState
    body: dict[str, Any] = field(default_factory=dict)
    apply: Callable[[], None] | None = None
    referral: list[str] | None = None  # reasons, when moving to REFERRED
    detail: dict[str, Any] | None = None  # why, for the referral record (default: the body)


class Orchestrator:
    def __init__(
        self,
        store: Store,
        source: DisputeSource,
        actions: DisputeActions,
        systems: MerchantSystems,
        investigator: Investigator,
        drafter: Drafter,
        settings: Settings,
        *,
        fx: FxTable | None = None,
        synthetic: bool = True,
    ) -> None:
        self.store = store
        self.source = source
        self.systems = systems
        self.investigator = investigator
        self.drafter = drafter
        self.settings = settings
        self.fx = fx
        self.synthetic = synthetic
        self.gateway = WriteGateway(store, source, actions, settings)
        self._handlers: dict[CaseState, Callable[[CaseRow, datetime], _Step | None]] = {
            S.INGESTED: self._ingested,
            S.ASSESSED: self._assessed,
            S.INVESTIGATING: self._investigating,
            S.DECIDED: self._decided,
            S.DRAFTING: self._drafting,
            S.VERIFYING: self._verifying,
            S.RENDERING: self._rendering,
            S.PRE_WRITE_CHECK: self._pre_write_check,
            S.SUBMITTING: self._submitting,
        }

    # --- scheduler ----------------------------------------------------------------------------

    def tick(self, now: datetime) -> int:
        """Ingest, run the watchdog, then advance each runnable case one step."""
        changed = 0
        for merchant_id in self.store.merchant_ids():
            changed += len(ingest(self.store, self.source, merchant_id, now))
        changed += self.watchdog(now)
        for merchant_id in self.store.merchant_ids():
            for case in self.store.cases(merchant_id, {s.value for s in RUNNABLE}):
                if not self.settings.isolate_step_failures:
                    changed += int(self.step(case, now))
                    continue
                try:
                    changed += int(self.step(case, now))
                except Exception as e:  # one case never stalls the others
                    self._step_failed(case, now, e)
        return changed

    def _step_failed(self, case: CaseRow, now: datetime, error: Exception) -> None:
        """Audit a failed step; after repeated failures, a person takes over (fail closed)."""
        with self.store.transaction():
            self.store.audit(
                case.merchant_id,
                case.case_id,
                "STEP_FAILED",
                {"state": case.state, "error": type(error).__name__},
                now,
            )
        failures = sum(
            1
            for e in self.store.audit_events(case.merchant_id, case.case_id)
            if e["event"] == "STEP_FAILED" and e["body"].get("state") == case.state
        )
        if failures >= self.settings.max_step_failures and CaseState(case.state) in WATCHDOG_REFERS:
            self._move(
                case,
                S.REFERRED,
                now,
                _Step(
                    S.REFERRED, {"why": f"step failed {failures} times"}, referral=["STEP_FAILED"]
                ),
                external=True,
                urgent=True,
            )

    def run_until_idle(self, now: datetime, max_ticks: int = 100) -> int:
        for n in range(1, max_ticks + 1):
            if self.tick(now) == 0:
                return n
        raise RuntimeError(f"not idle after {max_ticks} ticks")

    def watchdog(self, now: datetime) -> int:
        """Refer any case still in progress too close to Airwallex's deadline."""
        moved = 0
        for merchant_id in self.store.merchant_ids():
            for case in self.store.cases(merchant_id, {s.value for s in WATCHDOG_REFERS}):
                stage = Stage(self.store.cycle(merchant_id, case.case_id, case.cycle).stage)
                if now >= case.due_at - self.settings.deadline_buffer[stage]:
                    self._move(
                        case,
                        S.REFERRED,
                        now,
                        _Step(
                            S.REFERRED,
                            {"why": "deadline buffer reached"},
                            referral=["DEADLINE_NEAR"],
                        ),
                        external=True,
                        urgent=True,
                    )
                    moved += 1
            moved += self._escalate_referrals(merchant_id, now)
        return moved

    def _escalate_referrals(self, merchant_id: str, now: datetime) -> int:
        """A referral still waiting inside the deadline buffer becomes urgent (no transition)."""
        escalated = 0
        for case in self.store.cases(merchant_id, {S.REFERRED.value}):
            referral = self.store.open_referral_for(merchant_id, case.case_id)
            if referral is None or referral.urgent:
                continue
            stage = Stage(self.store.cycle(merchant_id, case.case_id, case.cycle).stage)
            if now < case.due_at - self.settings.deadline_buffer[stage]:
                continue
            with self.store.transaction():
                self.store.mark_referral_urgent(merchant_id, referral.referral_id)
                self.store.update_case(merchant_id, case.case_id, urgent=True, at=now)
                self.store.audit(
                    merchant_id,
                    case.case_id,
                    "REFERRAL_ESCALATED",
                    {"referral": referral.referral_id, "due_at": case.due_at},
                    now,
                )
            escalated += 1
        return escalated

    def step(self, case: CaseRow, now: datetime) -> bool:
        handler = self._handlers.get(CaseState(case.state))
        if handler is None:
            return False
        result = handler(case, now)
        if result is None:  # waiting (e.g. a write whose outcome is not yet known)
            return False
        self._move(case, result.to, now, result)
        return True

    def _move(
        self,
        case: CaseRow,
        to: CaseState,
        now: datetime,
        step: _Step,
        *,
        external: bool = False,
        urgent: bool = False,
    ) -> None:
        frm = CaseState(case.state)
        states.check(frm, to, external=external)
        with self.store.transaction():
            if step.apply is not None:
                step.apply()
            if to is S.REFERRED:
                self.store.open_referral(
                    case.merchant_id,
                    case.case_id,
                    case.cycle,
                    step.referral or [],
                    urgent,
                    now,
                    detail=step.detail if step.detail is not None else step.body,
                )
            self.store.update_case(case.merchant_id, case.case_id, state=to, urgent=urgent, at=now)
            self.store.audit(
                case.merchant_id,
                case.case_id,
                "STATE",
                {"from": frm, "to": to, "cycle": case.cycle, **step.body},
                now,
            )

    # --- helpers ------------------------------------------------------------------------------

    def _dispute(self, case: CaseRow) -> Dispute:
        dispute = self.store.latest_dispute(case.merchant_id, case.case_id)
        assert dispute is not None, "a case always has a snapshot"
        return dispute

    def _tools(self, case: CaseRow, dispute: Dispute, now: datetime) -> ToolGateway:
        return ToolGateway(
            self.store,
            self.systems,
            dispute,
            case.cycle,
            now=now,
            max_calls=self.settings.max_tool_calls,
        )

    def _decide(self, case: CaseRow, dispute: Dispute) -> Decision:
        cycle = self.store.cycle(case.merchant_id, case.case_id, case.cycle)
        policy = self.store.policy(case.merchant_id, cycle.policy_version)
        records = self.store.evidence(case.merchant_id, case.case_id)
        return decide(
            dispute,
            policy,
            evidence_present(records, dispute),
            ownership_resolved=bool(cycle.ownership_resolved),
            contradictory_record_ids=contradictory_record_ids(records, dispute),
            unanswered_contact_ids=unanswered_customer_contacts(records, dispute),
            fx=self.fx,
        )

    def _pending_calls(
        self, decision: Decision, dispute: Dispute, tools: ToolGateway, case: CaseRow
    ) -> list[str]:
        records = self.store.evidence(case.merchant_id, case.case_id)
        targets = investigation_targets(decision, dispute, evidence_present(records, dispute))
        called = tools.called()
        return [
            f"{t}:{k}"
            for t in tools_for(targets)
            for k in tools.lookup_keys(t)
            if f"{t}:{k}" not in called
        ]

    def _may_investigate(
        self, case: CaseRow, dispute: Dispute, tools: ToolGateway, now: datetime
    ) -> bool:
        """Tool calls, model tokens and time all have to remain."""
        buffer = self.settings.investigation_buffer[dispute.stage]
        tokens = self.store.agent_tokens(case.merchant_id, case.case_id, case.cycle)
        return (
            tools.calls_used < self.settings.max_tool_calls
            and tokens < self.settings.max_tokens_per_cycle
            and now < dispute.due_at - buffer
        )

    def _record_run(self, case: CaseRow, run: AgentRun | None, now: datetime) -> None:
        if run is None:
            return
        self.store.add_agent_run(case.merchant_id, case.case_id, case.cycle, run, now)
        self.store.audit(
            case.merchant_id,
            case.case_id,
            "AGENT_RUN",
            {
                "kind": run.kind,
                "provider": run.provider,
                "model": run.model,
                "stop": run.stop,
                "turns": run.turns,
                "tokens": run.tokens,
                "tool_calls": [f"{c.tool}:{c.key}:{c.outcome}" for c in run.tool_calls],
                "dropped": len(run.dropped),
            },
            now,
        )

    def _decision_step(
        self,
        case: CaseRow,
        dispute: Dispute,
        decision: Decision,
        to: CaseState,
        now: datetime,
        run: AgentRun | None = None,
        **body: Any,
    ) -> _Step:
        def apply() -> None:
            self._record_run(case, run, now)
            self.store.add_decision(case.merchant_id, case.case_id, case.cycle, decision, now)

        return _Step(to, {"action": decision.action, "margin": decision.margin, **body}, apply)

    # --- handlers -----------------------------------------------------------------------------

    def _ingested(self, case: CaseRow, now: datetime) -> _Step:
        dispute = self._dispute(case)
        if (why := closed_reason(dispute)) is not None:
            return _Step(S.CLOSED, {"why": why})
        merchant = self.store.merchant(case.merchant_id)
        policy = self.store.policy(case.merchant_id, merchant.active_policy_version)
        threshold = (
            None
            if merchant.auto_accept_threshold is None
            else Money(amount=merchant.auto_accept_threshold, currency=policy.currency)
        )
        if (why := deferral_reason(dispute, threshold)) is not None:
            return _Step(S.DEFERRED, {"why": why})

        # Baseline lookups by code, not the agent: payment attempt, order, helpdesk.
        owner = self.source.payment_intent_owner(case.merchant_id, dispute.payment_intent_id)
        ownership = owner == case.merchant_id
        attempt = (
            self.source.payment_attempt(case.merchant_id, dispute.payment_attempt_id)
            if dispute.payment_attempt_id
            else None
        )
        tools = self._tools(case, dispute, now)
        called = tools.called()
        for tool in (Tool.ORDER, Tool.HELPDESK):
            for key in tools.lookup_keys(tool):
                if f"{tool}:{key}" not in called:
                    tools.fetch(tool, key)

        def apply() -> None:
            self.store.set_ownership(case.merchant_id, case.case_id, case.cycle, ownership)
            if attempt is not None:
                record = attempt.to_evidence_record(
                    case_id=case.case_id, captured_at=now, synthetic=self.synthetic
                )
                self.store.put_evidence_or_revise(case.merchant_id, record, now)

        return _Step(S.ASSESSED, {"ownership_resolved": ownership}, apply)

    def _assessed(self, case: CaseRow, now: datetime) -> _Step:
        dispute = self._dispute(case)
        decision = self._decide(case, dispute)
        tools = self._tools(case, dispute, now)
        pending = self._pending_calls(decision, dispute, tools, case)
        if pending and self._may_investigate(case, dispute, tools, now):
            return self._decision_step(
                case, dispute, decision, S.INVESTIGATING, now, pending=pending
            )
        return self._decision_step(case, dispute, decision, S.DECIDED, now)

    def _investigating(self, case: CaseRow, now: datetime) -> _Step:
        dispute = self._dispute(case)
        tools = self._tools(case, dispute, now)
        before = self._decide(case, dispute)
        records = self.store.evidence(case.merchant_id, case.case_id)
        targets = investigation_targets(before, dispute, evidence_present(records, dispute))
        result = self.investigator.investigate(dispute, targets, tools)
        used, run = result.calls, result.run
        decision = self._decide(case, dispute)
        pending = self._pending_calls(decision, dispute, tools, case)
        tokens = run.tokens if run is not None else 0
        within = self._may_investigate(case, dispute, tools, now) and (
            self.store.agent_tokens(case.merchant_id, case.case_id, case.cycle) + tokens
            < self.settings.max_tokens_per_cycle
        )
        if used and pending and within:
            return self._decision_step(
                case,
                dispute,
                decision,
                S.INVESTIGATING,
                now,
                run=run,
                tool_calls=used,
                pending=pending,
            )
        return self._decision_step(
            case, dispute, decision, S.DECIDED, now, run=run, tool_calls=used
        )

    def _decided(self, case: CaseRow, now: datetime) -> _Step:
        latest = self.store.latest_decision(case.merchant_id, case.case_id, case.cycle)
        assert latest is not None
        decision = latest[1]
        if decision.action is Action.ACCEPT:
            return _Step(S.PRE_WRITE_CHECK, {"action": Action.ACCEPT})
        if decision.action is Action.CHALLENGE:
            return _Step(S.DRAFTING, {"action": Action.CHALLENGE})
        reasons = [r.value for r in decision.referral_reasons]
        detail = {
            "reasons": reasons,
            "contradictory_record_ids": list(decision.inputs.get("contradictory_record_ids", [])),
            "unanswered_contact_ids": list(decision.inputs.get("unanswered_contact_ids", [])),
        }
        return _Step(S.REFERRED, {"reasons": reasons}, referral=reasons, detail=detail)

    def _drafting(self, case: CaseRow, now: datetime) -> _Step:
        dispute = self._dispute(case)
        records = self.store.evidence(case.merchant_id, case.case_id)
        result = self.drafter.draft(dispute, records)
        claims = list(result.claims)
        if not claims:

            def record() -> None:
                self._record_run(case, result.run, now)

            return _Step(S.REFERRED, {"why": "no claims drafted"}, record, referral=["DRAFT_EMPTY"])

        def apply() -> None:
            self._record_run(case, result.run, now)
            self.store.put_claims(case.merchant_id, case.case_id, case.cycle, claims)

        return _Step(S.VERIFYING, {"claims": [c.id for c in claims]}, apply)

    def _verifying(self, case: CaseRow, now: datetime) -> _Step:
        m, cid = case.merchant_id, case.case_id
        dispute = self._dispute(case)
        draft = self.store.latest_draft(m, cid, case.cycle)
        claims = self.store.claims(m, cid, case.cycle, draft)
        result = verify(
            dispute,
            claims,
            self.store.evidence(m, cid),
            fx=self.fx,
            expected_hashes=self.store.capture_hashes(m, cid),
        )

        def apply() -> None:
            self.store.put_verification(m, cid, case.cycle, draft, result)

        body = {
            "draft": draft,
            "outcome": result.outcome,
            "blocked": {c.claim.id: [f.check for f in c.failures] for c in result.blocked},
        }
        if result.outcome is VerificationOutcome.PASSED:
            return _Step(S.RENDERING, body, apply)
        return _Step(
            S.REFERRED,
            body,
            apply,
            referral=[f"VERIFICATION_{result.outcome}"],
            detail=_verification_detail(draft, result),
        )

    def _rendering(self, case: CaseRow, now: datetime) -> _Step:
        m, cid = case.merchant_id, case.case_id
        dispute = self._dispute(case)
        draft = self.store.latest_draft(m, cid, case.cycle)
        result = self.store.verification(m, cid, case.cycle, draft)
        try:
            name, content = render_evidence(
                dispute, result, self.store.evidence(m, cid), cycle=case.cycle, draft=draft
            )
        except RenderError as e:
            return _Step(S.REFERRED, {"why": str(e)}, referral=["RENDER_FAILED"])

        def apply() -> None:
            self.store.put_render(m, cid, case.cycle, draft, name, content)

        return _Step(S.PRE_WRITE_CHECK, {"file": name, "bytes": len(content)}, apply)

    def _intended_action(self, case: CaseRow) -> Action:
        approval = self.store.latest_approval(case.merchant_id, case.case_id, case.cycle)
        if approval is not None and approval.approval is not None:
            return Action(approval.approval["action"])
        latest = self.store.latest_decision(case.merchant_id, case.case_id, case.cycle)
        assert latest is not None
        return latest[1].action

    def _pre_write_check(self, case: CaseRow, now: datetime) -> _Step:
        action = self._intended_action(case)
        check = self.gateway.preflight(case.merchant_id, case.case_id, case.cycle, action, now=now)
        if not check.ok:
            return _Step(
                S.REFERRED,
                {"failure": check.failure, "detail": check.detail},
                referral=[f"GUARD_{check.failure}"],
            )
        return _Step(S.SUBMITTING, {"action": action})

    def _submitting(self, case: CaseRow, now: datetime) -> _Step | None:
        action = self._intended_action(case)
        result = self.gateway.submit(case.merchant_id, case.case_id, case.cycle, action, now=now)
        if result.ok:
            return _Step(
                S.AWAITING_OUTCOME,
                {
                    "action": action,
                    "request_id": result.request_id,
                    "ledger": result.ledger,
                    "dry_run": result.dry_run,
                },
            )
        if result.failure is GuardFailure.OUTCOME_UNKNOWN:
            return None  # re-fetch and reconcile on the next tick; never a blind resend
        return _Step(
            S.REFERRED,
            {"failure": result.failure, "detail": result.detail},
            referral=[f"GUARD_{result.failure}"],
        )


def _verification_detail(draft: str, result: VerificationResult) -> dict[str, Any]:
    """What the approver needs: each claim that did not verify, why, and what contradicts it."""
    return {
        "draft": draft,
        "outcome": result.outcome,
        "claims": [
            {
                "claim_id": c.claim.id,
                "source_record_id": c.claim.source_record_id,
                "source_field": c.claim.source_field,
                "establishes": c.claim.establishes,
                "status": c.claim.validation_status,
                "failures": [{"check": f.check, "detail": f.detail} for f in c.failures],
                "conflicting_record_ids": list(c.conflicting_record_ids),
            }
            for c in result.claims
            if c.claim.validation_status is not ValidationStatus.VERIFIED
        ],
        "case_failures": [{"check": f.check, "detail": f.detail} for f in result.case_failures],
        "counterevidence": [c.model_dump(mode="json") for c in result.counterevidence],
        "notes": [n.model_dump(mode="json") for n in result.notes],
    }
