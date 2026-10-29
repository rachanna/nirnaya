"""The case state machine and scheduler (architecture section 4).

One step per case per tick. A step's own effects and its state transition commit in one
transaction with an audit event, so a crash leaves the case in its previous state and the
step simply runs again. Steps that reach outside (tool calls, the write gateway) are
idempotent: evidence is immutable, tool calls are remembered per cycle, and writes go through
the request_id ledger. Time is always passed in; nothing here reads a clock.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nirnaya.agent.reading import quote_claim, readable_facts, readable_records, screen
from nirnaya.agent.runs import AgentRun
from nirnaya.agent.targets import counterevidence_lookups, investigation_targets, tools_for
from nirnaya.agent.tools import MerchantSystems, Tool, ToolGateway
from nirnaya.engine import decide
from nirnaya.evidence.catalogue import Quote
from nirnaya.evidence.derive import (
    contradictions,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.guardrails.gateway import GuardFailure, WriteGateway
from nirnaya.ingest import ingest
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import (
    ACTIONABLE_STATUS,
    Action,
    EvidenceType,
    Stage,
    ValidationStatus,
)
from nirnaya.models.money import FxTable, Money
from nirnaya.orchestrator import states
from nirnaya.orchestrator.deferral import closed_reason, deferral_reason
from nirnaya.orchestrator.ports import (
    NULL_OBSERVER,
    DisputeActions,
    DisputeSource,
    Drafter,
    Investigator,
    Reader,
    StepObserver,
)
from nirnaya.orchestrator.redraft import redraft_plan
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import DEFERRAL_CHECK, RUNNABLE, WATCHDOG_REFERS, CaseState
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
        observer: StepObserver = NULL_OBSERVER,
        reader: Reader | None = None,
    ) -> None:
        self.store = store
        self.reader = reader  # None: notes are not read (the scripted default)
        self.observer = observer
        self._ingest_failing: set[str] = set()
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
            changed += self._ingest(merchant_id, now)
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

    def _ingest(self, merchant_id: str, now: datetime) -> int:
        """Read one merchant's disputes. A failure (Airwallex unreachable, bad credentials, a
        listing that cannot be read) is audited once when it starts and once when it ends, and
        never stops the watchdog or another merchant's cases."""
        try:
            touched = len(ingest(self.store, self.source, merchant_id, now))
        except Exception as e:
            if not self.settings.isolate_step_failures:
                raise
            if merchant_id not in self._ingest_failing:
                self._ingest_failing.add(merchant_id)
                with self.store.transaction():
                    self.store.audit(
                        merchant_id,
                        None,
                        "INGEST_FAILED",
                        {"error": type(e).__name__, "detail": str(e)[:300]},
                        now,
                    )
            return 0
        if merchant_id in self._ingest_failing:
            self._ingest_failing.discard(merchant_id)
            with self.store.transaction():
                self.store.audit(merchant_id, None, "INGEST_RECOVERED", {}, now)
        return touched

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
        if (
            CaseState(case.state) is S.SUBMITTING
            and failures >= self.settings.max_submitting_failures
        ):
            self._stalled(case, now, "STEP_FAILED", failures=failures, error=type(error).__name__)
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
            for case in self.store.cases(merchant_id, {s.value for s in DEFERRAL_CHECK}):
                moved += int(self._deferral_unconfirmed(case, now))
            for case in self.store.cases(merchant_id, {S.SUBMITTING.value}):
                stage = Stage(self.store.cycle(merchant_id, case.case_id, case.cycle).stage)
                if now >= case.due_at - self.settings.deadline_buffer[stage]:
                    moved += int(self._stalled(case, now, "DEADLINE_BUFFER"))
            moved += self._escalate_referrals(merchant_id, now)
        return moved

    def _stalled(self, case: CaseRow, now: datetime, cause: str, **extra: Any) -> bool:
        """A write in flight that is not progressing: make it visible, once per cause and
        cycle. No transition, no cancel, no resend: the write may already have landed."""
        if any(
            e["event"] == "SUBMISSION_STALLED"
            and e["body"].get("cause") == cause
            and e["body"].get("cycle") == case.cycle
            for e in self.store.audit_events(case.merchant_id, case.case_id)
        ):
            return False
        writes = [
            w for w in self.store.writes(case.merchant_id, case.case_id) if w.cycle == case.cycle
        ]
        last = writes[-1] if writes else None
        with self.store.transaction():
            self.store.update_case(case.merchant_id, case.case_id, urgent=True, at=now)
            self.store.audit(
                case.merchant_id,
                case.case_id,
                "SUBMISSION_STALLED",
                {
                    "cause": cause,
                    "cycle": case.cycle,
                    "request_id": None if last is None else last.request_id,
                    "ledger": None if last is None else last.status,
                    **extra,
                },
                now,
            )
        return True

    def _deferral_unconfirmed(self, case: CaseRow, now: datetime) -> bool:
        """A deferred case at its deadline buffer that Airwallex has not acted on is referred.

        Deferral rests on inputs Nirnaya cannot fully verify (the refunded amount, the merchant's
        configured auto-accept threshold), so it is checked, not trusted: without this the
        deadline would pass with no referral and no alert.
        """
        stage = Stage(self.store.cycle(case.merchant_id, case.case_id, case.cycle).stage)
        if now < case.due_at - self.settings.deadline_buffer[stage]:
            return False
        dispute = self.store.latest_dispute(case.merchant_id, case.case_id)
        if dispute is None or dispute.status is not ACTIONABLE_STATUS:
            return False  # Airwallex acted: ingestion closes the case or opens the next stage
        why = next(
            (
                e["body"].get("why")
                for e in reversed(self.store.audit_events(case.merchant_id, case.case_id))
                if e["event"] == "STATE" and e["body"].get("to") == S.DEFERRED
            ),
            None,
        )
        detail = {
            "deferral": why,
            "status": str(dispute.status),
            "due_at": dispute.due_at.isoformat(),
            "action": "Nirnaya expected Airwallex to act and it has not. Writes are closed "
            "inside the deadline buffer: act directly in Airwallex.",
        }

        def audit() -> None:
            self.store.audit(case.merchant_id, case.case_id, "DEFERRAL_UNCONFIRMED", detail, now)

        self._move(
            case,
            S.REFERRED,
            now,
            _Step(
                S.REFERRED,
                {"why": "deferral unconfirmed", "deferral": why},
                audit,
                referral=["DEFERRAL_UNCONFIRMED"],
                detail=detail,
            ),
            external=True,
            urgent=True,
        )
        return True

    def _escalate_referrals(self, merchant_id: str, now: datetime) -> int:
        """A referral still waiting inside the deadline buffer becomes urgent (no transition)."""
        escalated = 0
        for referral in self.store.escalatable_referrals(merchant_id):
            if now < referral.due_at - self.settings.deadline_buffer[referral.stage]:
                continue
            with self.store.transaction():
                self.store.mark_referral_urgent(merchant_id, referral.referral_id)
                self.store.update_case(merchant_id, referral.case_id, urgent=True, at=now)
                self.store.audit(
                    merchant_id,
                    referral.case_id,
                    "REFERRAL_ESCALATED",
                    {"referral": referral.referral_id, "due_at": referral.due_at},
                    now,
                )
            escalated += 1
        return escalated

    def step(self, case: CaseRow, now: datetime) -> bool:
        handler = self._handlers.get(CaseState(case.state))
        if handler is None:
            return False
        with self._observed(case):
            result = handler(case, now)
            if result is None:  # waiting (e.g. a write whose outcome is not yet known)
                return False
            self._move(case, result.to, now, result)
            return True

    @contextmanager
    def _observed(self, case: CaseRow) -> Iterator[None]:
        """Run a step inside the observer's context. The observer only watches: anything it
        raises is ignored, and the step's own exceptions propagate unchanged."""
        context = None
        with suppress(Exception):
            entering = self.observer.step(case)
            entering.__enter__()
            context = entering  # only a context that entered is exited
        try:
            yield
        except BaseException as e:
            if context is not None:
                with suppress(Exception):
                    context.__exit__(type(e), e, e.__traceback__)
            raise
        if context is not None:
            with suppress(Exception):
                context.__exit__(None, None, None)

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
        with suppress(Exception):  # reported only once committed; an observer never interferes
            self.observer.transition(case, str(frm), str(to), step.body, external)

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

    def _decide(
        self, case: CaseRow, dispute: Dispute, also: frozenset[EvidenceType] = frozenset()
    ) -> Decision:
        """The engine's decision on the case's records. `also` adds facts for a counterfactual
        (what the decision would be if proposed quotes were confirmed); it never decides."""
        cycle = self.store.cycle(case.merchant_id, case.case_id, case.cycle)
        policy = self.store.policy(case.merchant_id, cycle.policy_version)
        records = self.store.evidence(case.merchant_id, case.case_id)
        found = contradictions(records, dispute, also)
        return decide(
            dispute,
            policy,
            evidence_present(records, dispute) | also,
            ownership_resolved=bool(cycle.ownership_resolved),
            contradictory_record_ids=sorted({c.record_id for c in found}),
            unanswered_contact_ids=unanswered_customer_contacts(records, dispute),
            fx=self.fx,
            contradictions=[(c.record_id, c.contradicts, c.description) for c in found],
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
        read = self._read_notes(case, decision, now)
        if read is not None and read.if_confirmed is not None:
            return self._proposed_evidence(case, decision, read, now)
        step = self._decided_step(decision)
        if read is not None:  # nothing that would change the decision: the reading is recorded
            assert step.apply is None
            run = read.run
            step.apply = lambda: self._record_run(case, run, now)
        return step

    @staticmethod
    def _decided_step(decision: Decision) -> _Step:
        if decision.action is Action.ACCEPT:
            return _Step(S.PRE_WRITE_CHECK, {"action": Action.ACCEPT})
        if decision.action is Action.CHALLENGE:
            return _Step(S.DRAFTING, {"action": Action.CHALLENGE})
        reasons = [r.value for r in decision.referral_reasons]
        return _Step(
            S.REFERRED, {"reasons": reasons}, referral=reasons, detail=_referral_detail(decision)
        )

    def _read_notes(self, case: CaseRow, decision: Decision, now: datetime) -> "_Read | None":
        """Ask the reader for quotes from merchant and carrier notes that would show a fact
        value of information says could change the decision (architecture 8.8). Code screens
        what it proposes; the reading never decides. Once per cycle, within the budget."""
        if self.reader is None or decision.action is Action.CHALLENGE:
            return None
        m, cid = case.merchant_id, case.case_id
        if any(r["kind"] == "READ" for r in self.store.agent_runs(m, cid, case.cycle)):
            return None
        dispute = self._dispute(case)
        records = self.store.evidence(m, cid)
        facts = readable_facts(decision, evidence_present(records, dispute))
        readable = readable_records(dispute, records, facts)
        tokens = self.store.agent_tokens(m, cid, case.cycle)
        buffer = self.settings.investigation_buffer[dispute.stage]
        if (
            not facts
            or not readable
            or tokens >= self.settings.max_tokens_per_cycle
            or now >= dispute.due_at - buffer
        ):
            return None
        result = self.reader.read(dispute, facts, readable)
        kept, dropped = screen(result.proposals, readable, facts)
        run = result.run or AgentRun(kind="READ", provider="none", model="none", turns=0, stop="-")
        run = run.model_copy(
            update={
                "dropped": run.dropped + tuple(dropped),
                "transcript": run.transcript
                + ({"asked": sorted(facts), "kept": [q._asdict() for q in kept]},),
            }
        )
        if_confirmed = None
        if kept:
            would = self._decide(case, dispute, also=frozenset(q.fact for q in kept))
            if would.action is not decision.action:
                if_confirmed = would
        return _Read(run=run, kept=kept, if_confirmed=if_confirmed)

    def _proposed_evidence(
        self, case: CaseRow, decision: Decision, read: "_Read", now: datetime
    ) -> _Step:
        """Quotes that would change the decision go to a person. Nothing is decided on them."""
        would = read.if_confirmed
        assert would is not None
        reasons = [r.value for r in decision.referral_reasons] + ["PROPOSED_EVIDENCE"]
        proposed = [
            {"record_id": q.record_id, "field": q.field, "fact": q.fact.value, "quote": q.quote}
            for q in read.kept
        ]
        detail = _referral_detail(decision) | {
            "reasons": reasons,
            "proposed_evidence": proposed,
            "action_now": decision.action.value,
            "action_if_confirmed": would.action.value,
            "p_now": None if decision.p is None else str(decision.p),
            "p_if_confirmed": None if would.p is None else str(would.p),
            "margin_now": None if decision.margin is None else str(decision.margin),
            "margin_if_confirmed": None if would.margin is None else str(would.margin),
            "referral_reasons_if_confirmed": [r.value for r in would.referral_reasons],
            "contradictions_if_confirmed": list(would.inputs.get("contradictions", [])),
            "note": "The agent read these words in a merchant or carrier note. Confirm a quote "
            "only if it shows the fact; the verifier then checks it word for word.",
        }

        def apply() -> None:
            self._record_run(case, read.run, now)
            for item, q in zip(proposed, read.kept, strict=True):
                # Ids exist only once stored; the referral (opened next, same transaction)
                # carries them so the approver can confirm by id.
                item["finding_id"] = self.store.add_finding(
                    case.merchant_id, case.case_id, case.cycle, q, now
                )

        body = {"reasons": reasons, "proposed": len(proposed)}
        return _Step(S.REFERRED, body, apply, referral=reasons, detail=detail)

    def _drafting(self, case: CaseRow, now: datetime) -> _Step:
        dispute = self._dispute(case)
        confirmed = sorted(self.store.confirmed_quotes(case.merchant_id, case.case_id, case.cycle))
        unfetched = self._counterevidence_sweep(case, dispute, now, {q.fact for q in confirmed})
        if unfetched:
            detail = {
                "unfetched": unfetched,
                "why": "the tool budget ran out before every record that could contradict "
                "the challenge was looked at",
            }
            return _Step(S.REFERRED, detail, referral=["COUNTEREVIDENCE_INCOMPLETE"], detail=detail)
        records = self.store.evidence(case.merchant_id, case.case_id)
        feedback = self._redraft_feedback(case, dispute, records)
        if feedback is None:
            result = self.drafter.draft(dispute, records)
        else:
            result = self.drafter.draft(dispute, records, feedback=feedback)
        # Quotes a person confirmed become claims built by code (architecture 8.8).
        claims = list(result.claims) + [
            quote_claim(dispute, q, n) for n, q in enumerate(confirmed, start=1)
        ]
        if not claims:

            def record() -> None:
                self._record_run(case, result.run, now)

            body: dict[str, Any] = {"why": "no claims drafted"}
            if feedback is not None:  # a failed redraft: the approver still sees what failed
                draft = self.store.latest_draft(case.merchant_id, case.case_id, case.cycle)
                body["earlier_drafts"] = self._earlier_drafts(case, dispute, draft + 1, records)
            return _Step(S.REFERRED, body, record, referral=["DRAFT_EMPTY"])

        def apply() -> None:
            self._record_run(case, result.run, now)
            self.store.put_claims(case.merchant_id, case.case_id, case.cycle, claims)

        return _Step(S.VERIFYING, {"claims": [c.id for c in claims]}, apply)

    def _counterevidence_sweep(
        self,
        case: CaseRow,
        dispute: Dispute,
        now: datetime,
        also: set[EvidenceType] | None = None,
    ) -> list[str]:
        """Before a challenge is drafted, code fetches every record that could contradict it
        (every key of each tool serving a fact the case relies on, and of each system with a
        contradiction rule against one), whatever the agent fetched. A fetched record can name
        new keys (another parcel), so it repeats until nothing is left. Returns what could not
        be fetched within the tool budget; the verifier and gateway then see the full record.
        `also`: facts confirmed quotes establish, which the challenge relies on too."""
        tools = self._tools(case, dispute, now)
        fetched: list[str] = []
        while missing := counterevidence_lookups(
            dispute, tools.case_evidence(), tools.called(), also or ()
        ):
            if tools.calls_used + len(missing) > self.settings.max_tool_calls:
                return missing
            for lookup in missing:
                name, _, key = lookup.partition(":")
                tools.fetch(Tool(name), key)
                fetched.append(lookup)
        if fetched:
            with self.store.transaction():
                self.store.audit(
                    case.merchant_id,
                    case.case_id,
                    "COUNTEREVIDENCE_SWEEP",
                    {"cycle": case.cycle, "fetched": fetched},
                    now,
                )
        return []

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
            confirmed_quotes=self.store.confirmed_quotes(m, cid, case.cycle),
        )

        def apply() -> None:
            self.store.put_verification(m, cid, case.cycle, draft, result)

        body = {
            "draft": draft,
            "outcome": result.outcome,
            "blocked": {c.claim.id: [f.check for f in c.failures] for c in result.blocked},
            "notes": [n.model_dump(mode="json") for n in result.notes],
        }
        records = self.store.evidence(m, cid)
        earlier = self._earlier_drafts(case, dispute, draft, records)
        if result.outcome is VerificationOutcome.BLOCKED and self._may_redraft(
            case, dispute, draft, now
        ):
            plan = redraft_plan(dispute, result, records)
            if plan.retry:

                def redraft() -> None:
                    apply()
                    self.store.audit(
                        m,
                        cid,
                        "REDRAFT_REQUESTED",
                        {
                            "cycle": case.cycle,
                            "draft": draft,
                            "kinds": sorted(plan.kinds),
                            "checks": sorted(
                                {
                                    f["check"]
                                    for c in plan.feedback["blocked_claims"]
                                    for f in c["failures"]
                                }
                            ),  # fmt: skip
                        },
                        now,
                    )

                return _Step(S.DRAFTING, {**body, "redraft": sorted(plan.kinds)}, redraft)
        if result.outcome is VerificationOutcome.PASSED:
            overreached = [d for d in earlier if any(c["kind"] == "OVERREACH"
                                                     for c in d["blocked_claims"])]  # fmt: skip
            if overreached and not self._overreach_approved(case):
                detail = {
                    "earlier_drafts": earlier,
                    "redraft": {
                        "draft": draft,
                        "claims": [
                            {
                                "claim_id": c.claim.id,
                                "text": c.claim.text,
                                "source": f"{c.claim.source_record_id}.{c.claim.source_field}",
                            }
                            for c in result.claims
                        ],
                    },
                    "note": "The agent's first draft claimed something its records do not "
                    "support; its redraft passed verification. Approve the challenge only if "
                    "the redraft is right.",
                }
                return _Step(
                    S.REFERRED,
                    {**body, "earlier_drafts": [d["draft"] for d in earlier]},
                    apply,
                    referral=["REDRAFT_AFTER_OVERREACH"],
                    detail=detail,
                )
            return _Step(S.RENDERING, body, apply)
        detail = _verification_detail(draft, result)
        if earlier:
            detail["earlier_drafts"] = earlier
        return _Step(
            S.REFERRED,
            body,
            apply,
            referral=[f"VERIFICATION_{result.outcome}"],
            detail=detail,
        )

    # --- self-correction (architecture 8.6) -------------------------------------------------

    def _may_redraft(self, case: CaseRow, dispute: Dispute, draft: int, now: datetime) -> bool:
        tokens = self.store.agent_tokens(case.merchant_id, case.case_id, case.cycle)
        return (
            bool(getattr(self.drafter, "revises", False))
            and draft <= self.settings.max_redrafts
            and tokens < self.settings.max_tokens_per_cycle
            and now < dispute.due_at - self.settings.investigation_buffer[dispute.stage]
        )

    def _redraft_feedback(
        self, case: CaseRow, dispute: Dispute, records: list[Any]
    ) -> dict[str, Any] | None:
        """The verifier's findings on this cycle's latest draft, if it was blocked and the
        drafter revises; otherwise None (a first draft)."""
        if not getattr(self.drafter, "revises", False):
            return None
        m, cid = case.merchant_id, case.case_id
        draft = self.store.latest_draft(m, cid, case.cycle)
        previous = self.store.verification(m, cid, case.cycle, draft) if draft else None
        if previous is None or previous.outcome is not VerificationOutcome.BLOCKED:
            return None
        return redraft_plan(dispute, previous, records).feedback

    def _earlier_drafts(
        self, case: CaseRow, dispute: Dispute, draft: int, records: list[Any]
    ) -> list[dict[str, Any]]:
        """Each earlier blocked draft of this cycle, with its blocked claims and their kind."""
        out = []
        for n in range(1, draft):
            v = self.store.verification(case.merchant_id, case.case_id, case.cycle, n)
            if v is not None and v.outcome is VerificationOutcome.BLOCKED:
                plan = redraft_plan(dispute, v, records)
                out.append({"draft": n, **plan.feedback})
        return out

    def _overreach_approved(self, case: CaseRow) -> bool:
        """A person approved a challenge after seeing the failed draft."""
        approval = self.store.latest_approval(case.merchant_id, case.case_id, case.cycle)
        if approval is None or approval.approval is None:
            return False
        seen = set(approval.approval.get("referral_reasons", []))
        return approval.approval.get("action") == Action.CHALLENGE.value and bool(
            seen & {"REDRAFT_AFTER_OVERREACH", "VERIFICATION_BLOCKED"}
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

    def _submitted_notes(self, case: CaseRow, action: Action) -> list[dict[str, Any]]:
        """Approver notes of the verification behind a challenge (none for an accept)."""
        if action is not Action.CHALLENGE:
            return []
        draft = self.store.latest_draft(case.merchant_id, case.case_id, case.cycle)
        verification = self.store.verification(case.merchant_id, case.case_id, case.cycle, draft)
        if verification is None:
            return []
        return [n.model_dump(mode="json") for n in verification.notes]

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
            notes = self._submitted_notes(case, action)
            approval = self.store.latest_approval(case.merchant_id, case.case_id, case.cycle)
            approver = None if approval is None else (approval.approval or {}).get("approver")

            def record_notes() -> None:
                # The challenge cites late evidence; whoever authorised it, a person can review it.
                self.store.audit(
                    case.merchant_id,
                    case.case_id,
                    "SUBMITTED_WITH_NOTES",
                    {
                        "request_id": result.request_id,
                        "authority": "DELEGATED" if approval is None else "APPROVED",
                        "approver": approver,
                        "notes": notes,
                    },
                    now,
                )

            return _Step(
                S.AWAITING_OUTCOME,
                {
                    "action": action,
                    "request_id": result.request_id,
                    "ledger": result.ledger,
                    "dry_run": result.dry_run,
                },
                record_notes if notes else None,
            )
        if result.failure is GuardFailure.OUTCOME_UNKNOWN:
            return None  # re-fetch and reconcile on the next tick; never a blind resend
        in_flight = [
            w
            for w in self.store.writes(case.merchant_id, case.case_id)
            if w.cycle == case.cycle and w.status in {"PENDING", "UNKNOWN"}
        ]
        if in_flight and result.failure is GuardFailure.REFETCH_FAILED:
            # A write may have landed: wait and reconcile, never leave SUBMITTING over one
            # failed read. A long stall is flagged by SUBMISSION_STALLED (section 4.2).
            return None
        body: dict[str, Any] = {"failure": result.failure, "detail": result.detail}
        if in_flight:
            body["in_flight"] = {
                "request_id": in_flight[-1].request_id,
                "ledger": in_flight[-1].status,
                "note": "a write may have landed: check Airwallex before acting",
            }
        return _Step(S.REFERRED, body, referral=[f"GUARD_{result.failure}"])


@dataclass
class _Read:
    """One reading of notes: the run, the quotes code kept, and the decision they would give
    if a person confirmed them (None when they would not change it)."""

    run: AgentRun
    kept: list[Quote]
    if_confirmed: Decision | None


def _referral_detail(decision: Decision) -> dict[str, Any]:
    """Everything comes from the decision itself, so the referral matches what was decided."""
    return {
        "reasons": [r.value for r in decision.referral_reasons],
        "contradictory_record_ids": list(decision.inputs.get("contradictory_record_ids", [])),
        "contradictions": list(decision.inputs.get("contradictions", [])),
        "unanswered_contact_ids": list(decision.inputs.get("unanswered_contact_ids", [])),
    }


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
