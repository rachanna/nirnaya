"""View models for the console: plain data the UI renders, built from the store.

Each kit case is told as the guide's arc: the goal, the initial plan, the new information, the
revised decision and the resulting status. The initial plan is the engine's decision on what
Airwallex alone provides (the dispute and its payment attempt); the new information is what
Nirnaya then fetched from the merchant's systems; the revised decision is the one on record.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nirnaya.airwallex_ai import AiComparison, comparison
from nirnaya.console.session import DemoSession, M
from nirnaya.engine import decide
from nirnaya.evidence.derive import (
    contradictory_record_ids,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action, DisputeStatus, SourceSystem, Stage, ValidationStatus
from nirnaya.store import Store
from nirnaya.trace import case_trace, customer_info_text
from nirnaya.verifier import verify

REPORT = Path(__file__).resolve().parents[2] / "eval" / "results" / "report.json"


def money_text(value: object) -> str | None:
    """A margin or amount for display: two decimal places (the engine keeps full precision)."""
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


@dataclass(frozen=True)
class DecisionView:
    action: str
    accept_reason: str | None
    p: str | None
    margin: str | None
    math_action: str | None
    referral_reasons: tuple[str, ...]
    reasons: tuple[str, ...]
    worth_finding: tuple[str, ...] = ()  # value of information: evidence that would flip it

    @classmethod
    def of(cls, d: Decision) -> "DecisionView":
        return cls(
            action=str(d.action),
            accept_reason=None if d.accept_reason is None else str(d.accept_reason),
            p=None if d.p is None else str(d.p),
            margin=money_text(d.margin),
            math_action=None if d.math_action is None else str(d.math_action),
            referral_reasons=tuple(str(r) for r in d.referral_reasons),
            reasons=d.reasons,
            worth_finding=tuple(
                " + ".join(sorted(str(e) for e in v.evidence))
                + f" (p {v.p_if_present} -> {v.final_action_if_present})"
                for v in d.voi
            ),
        )


# The merchant's question for each kit case (the guide's "goal"). Numbers are left to the
# decisions shown under it: the evaluation rationale assumes every record was fetched, so its
# prior and margin can differ from what the engine decided on the records it actually needed.
KIT_GOALS = {
    "K1": "A large fraud claim at the information-request stage. Is it worth fighting, and can "
    "the merchant's own records prove the customer made this purchase?",
    "K2": "A small not-received claim. Is it worth what fighting would cost?",
    "K3": "A credit-not-processed claim from a customer who wrote to support. Should Nirnaya act "
    "on its own, or does a person need to look first?",
}


def agents_in_run(session: DemoSession) -> str:
    """The agent the stored run actually used, read from its agent steps (not this console's
    own setting, which may differ when viewing a sandbox run)."""
    models = session.store.agent_models(M)
    if not models:
        return "scripted (no model calls)"
    return "llm (" + ", ".join(f"{p}/{m}" for p, m in models) + ")"


@dataclass(frozen=True)
class CycleArc:
    cycle: int
    stage: str
    initial: DecisionView | None
    new_information: tuple[str, ...]
    refused: tuple[str, ...]
    revised: DecisionView | None
    writes: tuple[str, ...]
    referral: tuple[str, ...]
    # Airwallex's AI recommendation at this stage beside Nirnaya's decision (comparison only).
    airwallex_ai: AiComparison | None = None
    # Why the cycle ended without a decision: Airwallex already settled it, or acts on its own.
    ended: str | None = None


@dataclass(frozen=True)
class KitCard:
    label: str
    case_id: str
    title: str
    goal: str
    amount: str
    reason: str
    state: str
    dispute_status: str
    cycles: tuple[CycleArc, ...]


def _airwallex_only(store: Store, case_id: str, dispute: Dispute, cycle: int) -> Decision | None:
    """What the engine decides with only the dispute and its payment attempt."""
    row = store.cycle(M, case_id, cycle)
    attempt = [r for r in store.evidence(M, case_id)
               if r.source_system is SourceSystem.PAYMENT_ATTEMPT]  # fmt: skip
    policy = store.policy(M, row.policy_version)
    return decide(
        dispute,
        policy,
        evidence_present(attempt, dispute),
        ownership_resolved=bool(row.ownership_resolved),
        contradictory_record_ids=contradictory_record_ids(attempt, dispute),
        unanswered_contact_ids=unanswered_customer_contacts(attempt, dispute),
    )


def _events_by_cycle(store: Store, case_id: str) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {1: []}
    cycle = 1
    for e in store.audit_events(M, case_id):
        if e["event"] == "NEW_CYCLE":
            cycle = int(e["body"].get("cycle", cycle + 1))
            out.setdefault(cycle, [])
        out[cycle].append(e)
    return out


def kit_card(session: DemoSession, label: str) -> KitCard | None:
    store, case_id = session.store, session.kit_ids.get(label, label)
    case_row = store.case(M, case_id)
    if case_row is None:
        return None
    dispute = store.latest_dispute(M, case_id)
    assert dispute is not None
    events = _events_by_cycle(store, case_id)
    writes = store.writes(M, case_id)
    referral = store.open_referral_for(M, case_id)
    history = store.disputes(M, case_id)
    arcs = []
    for cycle in range(1, case_row.cycle + 1):
        stage = store.cycle(M, case_id, cycle).stage
        latest = store.latest_decision(M, case_id, cycle)
        # The dispute as the cycle opened: at its stage, awaiting the merchant's response.
        opened = dispute.model_copy(
            update={"stage": Stage(stage), "status": DisputeStatus.REQUIRES_RESPONSE}
        )
        initial = _airwallex_only(store, case_id, opened, cycle) if latest else None
        at_stage = [d for d in history if d.stage == Stage(stage)]
        rec = at_stage[-1].ai_recommendation if at_stage else None
        ai = (
            None
            if latest is None
            else comparison(
                rec,
                Action(latest[1].action),
                [str(r) for r in latest[1].referral_reasons],
            )
        )
        evs = events.get(cycle, [])
        ends = [
            f"{'Deferred' if e['body']['to'] == 'DEFERRED' else 'Closed'}: {e['body']['why']}"
            for e in evs
            if e["event"] == "STATE"
            and e["body"].get("to") in ("DEFERRED", "CLOSED")
            and e["body"].get("why")
        ]
        arcs.append(
            CycleArc(
                cycle=cycle,
                stage=stage,
                initial=None if initial is None else DecisionView.of(initial),
                new_information=tuple(
                    f"{e['body']['tool']}({e['body']['key']}) -> "
                    + (", ".join(e["body"]["records"]) or "nothing")
                    for e in evs
                    if e["event"] == "TOOL_CALL"
                ),
                refused=tuple(
                    f"{e['body']['tool']}({e['body']['key']}): {e['body']['why']}"
                    for e in evs
                    if e["event"] == "TOOL_REFUSED"
                ),
                revised=None if latest is None else DecisionView.of(latest[1]),
                writes=tuple(
                    f"{w.action} {w.status}"
                    + (" (dry run, not sent)" if w.dry_run else "")
                    + (
                        f"; customer_info: {customer_info_text(w.request['customer_info'])}"
                        if w.request.get("customer_info")
                        else ""
                    )
                    for w in writes
                    if w.cycle == cycle
                ),
                referral=()
                if referral is None or referral.cycle != cycle
                else tuple(referral.reasons),
                airwallex_ai=ai,
                ended=ends[-1] if ends else None,
            )
        )
    case = session.cases.get(label)
    return KitCard(
        label=label,
        case_id=case_id,
        title=case.title if case else label,
        goal=KIT_GOALS.get(label, case.title if case else ""),
        amount=str(dispute.amount),
        reason=f"{dispute.reason.type} (code {dispute.reason.original_code})",
        state=case_row.state,
        dispute_status=f"{dispute.stage} {dispute.status}",
        cycles=tuple(arcs),
    )


@dataclass(frozen=True)
class ClaimRow:
    claim_id: str
    text: str
    source: str
    status: str
    failures: tuple[str, ...]


@dataclass(frozen=True)
class CaseDetail:
    case_id: str
    state: str
    timeline: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    claims: tuple[ClaimRow, ...]
    counterevidence: tuple[str, ...]
    notes: tuple[str, ...]


def case_detail(session: DemoSession, case_id: str) -> CaseDetail | None:
    store = session.store
    case_row = store.case(M, case_id)
    if case_row is None:
        return None
    evidence = tuple(
        {
            "id": r.id,
            "system": str(r.source_system),
            "author": str(r.author),
            "sha256": r.sha256[:12],
            "intact": r.hash_ok(),
            "fields": r.fields,
        }
        for r in store.evidence(M, case_id)
    )
    claims: list[ClaimRow] = []
    counter: list[str] = []
    notes: list[str] = []
    for cycle in range(1, case_row.cycle + 1):
        draft = store.latest_draft(M, case_id, cycle)
        result = store.verification(M, case_id, cycle, draft) if draft else None
        if result is None:
            continue
        claims += [
            ClaimRow(
                c.claim.id,
                c.claim.text,
                f"{c.claim.source_record_id}.{c.claim.source_field}",
                str(c.claim.validation_status),
                tuple(f"{f.check}: {f.detail}" for f in c.failures),
            )
            for c in result.claims
        ]
        counter += [f"{c.record_id} ({c.author}): {c.description}" for c in result.counterevidence]
        notes += [f"{n.record_id}: {n.description}" for n in result.notes]
    return CaseDetail(
        case_id=case_id,
        state=case_row.state,
        timeline=tuple(case_trace(store, M, case_id)),
        evidence=evidence,
        claims=tuple(claims),
        counterevidence=tuple(counter),
        notes=tuple(notes),
    )


_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")


def md_escape(text: str) -> str:
    """Record text is untrusted: shown literally, never as markdown (no links, no emphasis)."""
    return _MARKDOWN.sub(r"\\\1", text)


@dataclass(frozen=True)
class ProposedQuote:
    """A quote the agent proposed from a note, as the approver sees it."""

    finding_id: str
    fact: str
    source: str
    quote: str
    # The whole note, markdown-escaped, with the quote in bold; a person reads it in context.
    note_markdown: str
    # The quote is still word for word in the note as stored now.
    found: bool


def proposed_quotes(store: Store, case_id: str, cycle: int) -> tuple[ProposedQuote, ...]:
    records = {r.id: r for r in store.evidence(M, case_id)}
    out = []
    for f in store.findings(M, case_id, cycle):
        if f.status != "PROPOSED":
            continue
        record = records.get(f.record_id)
        raw = record.fields.get(f.field) if record is not None else None
        note = " ".join(raw.split()) if isinstance(raw, str) else ""
        at = note.find(f.quote) if note else -1
        if at < 0:
            shown = md_escape(note)
        else:
            end = at + len(f.quote)
            shown = md_escape(note[:at]) + "**" + md_escape(f.quote) + "**" + md_escape(note[end:])
        system = f"{record.source_system}, {record.author}" if record is not None else "missing"
        out.append(
            ProposedQuote(
                finding_id=f.finding_id,
                fact=str(f.fact),
                source=f"{f.record_id}.{f.field} ({system})",
                quote=f.quote,
                note_markdown=shown,
                found=at >= 0,
            )
        )
    return tuple(out)


def _if_confirmed(detail: dict[str, Any]) -> str | None:
    if "action_if_confirmed" not in detail:
        return None
    text = (
        f"If confirmed: {detail['action_now']} → {detail['action_if_confirmed']} · "
        f"p {detail['p_now']} → {detail['p_if_confirmed']} · "
        f"margin {money_text(detail['margin_now'])} → {money_text(detail['margin_if_confirmed'])}"
        " USD (prior, not a prediction)"
    )
    conflicts = detail.get("contradictions_if_confirmed") or []
    if conflicts:
        text += " · conflicts with " + ", ".join(
            f"{c['record_id']} ({c['description']})" for c in conflicts
        )
    return text


@dataclass(frozen=True)
class RedraftView:
    """A redraft after an overreach: what the first draft claimed and why it was blocked, and
    the redraft that passed. Text is the agent's and the records', so the UI escapes it."""

    blocked: tuple[str, ...]
    redraft: tuple[str, ...]


def _redraft(detail: dict[str, Any]) -> RedraftView | None:
    if "redraft" not in detail:
        return None
    blocked = tuple(
        f"{c['establishes']} = {c['extracted_value']!r} from {c['source_record_id']}."
        f"{c['source_field']}: " + "; ".join(f"{f['check']} ({f['detail']})" for f in c["failures"])
        for d in detail.get("earlier_drafts", [])
        for c in d["blocked_claims"]
    )
    redraft = tuple(f"{c['text']} ({c['source']})" for c in detail["redraft"]["claims"])
    return RedraftView(blocked, redraft)


@dataclass(frozen=True)
class QueueItem:
    case_id: str
    urgent: bool
    reasons: tuple[str, ...]
    detail: dict[str, Any]
    due_at: datetime
    hours_left: int
    proposed: tuple[ProposedQuote, ...] = ()
    if_confirmed: str | None = None
    redraft: RedraftView | None = None


def queue(session: DemoSession) -> list[QueueItem]:
    items = []
    for case in session.store.cases(M, {"REFERRED"}):
        referral = session.store.open_referral_for(M, case.case_id)
        if referral is None:
            continue
        detail = referral.detail or {}
        items.append(
            QueueItem(
                case_id=case.case_id,
                urgent=referral.urgent,
                reasons=tuple(referral.reasons),
                detail=detail,
                due_at=case.due_at,
                hours_left=int((case.due_at - session.now).total_seconds() // 3600),
                proposed=proposed_quotes(session.store, case.case_id, case.cycle),
                if_confirmed=_if_confirmed(detail),
                redraft=_redraft(detail),
            )
        )
    return sorted(items, key=lambda i: (not i.urgent, i.due_at, i.case_id))


@dataclass(frozen=True)
class StalledWrite:
    case_id: str
    causes: tuple[str, ...]
    request_id: str | None
    ledger: str | None
    due_at: datetime
    hours_left: int


def stalled_writes(session: DemoSession) -> list[StalledWrite]:
    """Writes in flight that stopped progressing (SUBMISSION_STALLED). Read-only: the write
    may have landed, so a person checks in Airwallex rather than resending."""
    items = []
    for case in session.store.cases(M, {"SUBMITTING"}):
        events = [
            e["body"]
            for e in session.store.audit_events(M, case.case_id)
            if e["event"] == "SUBMISSION_STALLED" and e["body"].get("cycle") == case.cycle
        ]
        if events:
            items.append(
                StalledWrite(
                    case_id=case.case_id,
                    causes=tuple(e["cause"] for e in events),
                    request_id=events[-1].get("request_id"),
                    ledger=events[-1].get("ledger"),
                    due_at=case.due_at,
                    hours_left=int((case.due_at - session.now).total_seconds() // 3600),
                )
            )
    return sorted(items, key=lambda i: (i.due_at, i.case_id))


@dataclass(frozen=True)
class UnreadableDispute:
    dispute_id: str
    why: str
    due_at: str | None
    seen_at: str


def unreadable_disputes(session: DemoSession) -> list[UnreadableDispute]:
    """Disputes Nirnaya could not read or refused at ingestion. They have no case, so a person
    must handle them in Airwallex before their deadline."""
    events = session.store.merchant_events(M, {"INGEST_UNMAPPABLE", "INGEST_REJECTED"})
    latest: dict[str, UnreadableDispute] = {}
    for e in events:
        b = e["body"]
        dispute_id = str(b.get("dispute_id"))
        latest[dispute_id] = UnreadableDispute(
            dispute_id=dispute_id,
            why=str(b.get("why")),
            due_at=b.get("due_at"),
            seen_at=e["at"],
        )
    return sorted(latest.values(), key=lambda d: (d.due_at or "", d.dispute_id))


def airwallex_unreachable(session: DemoSession) -> dict[str, Any] | None:
    """The current ingestion outage, if any: when it started and why."""
    events = session.store.merchant_events(M, {"INGEST_FAILED", "INGEST_RECOVERED"})
    if not events or events[-1]["event"] != "INGEST_FAILED":
        return None
    return {"since": events[-1]["at"], **events[-1]["body"]}


@dataclass(frozen=True)
class FabricationCheck:
    title: str
    outcome: str
    claims: tuple[ClaimRow, ...] = field(default=())


def fabrication_check(session: DemoSession, label: str = "K2C") -> FabricationCheck:
    """Kit beat 2-C: an unguarded agent's draft, checked by the verifier before anything ships."""
    case = session.cases[label]
    result = verify(case.dispute, case.claim_objects(), case.evidence_records())
    return FabricationCheck(
        title=case.title,
        outcome=str(result.outcome),
        claims=tuple(
            ClaimRow(
                c.claim.id,
                c.claim.text,
                f"{c.claim.source_record_id}.{c.claim.source_field}",
                str(c.claim.validation_status),
                tuple(f"{f.check}: {f.detail}" for f in c.failures),
            )
            for c in result.claims
        ),
    )


def blocked(check: FabricationCheck) -> tuple[ClaimRow, ...]:
    return tuple(c for c in check.claims if c.status == str(ValidationStatus.BLOCKED))


def evaluation(path: Path = REPORT) -> dict[str, Any] | None:
    """The evaluation report written by `python -m nirnaya.evaluation.report`, if present."""
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
