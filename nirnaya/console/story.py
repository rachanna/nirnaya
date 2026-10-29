"""A case's run as a feed of plain messages, in the order things happened (web demo).

Built from the audit log and the decisions on record; wording comes from console.plain. Nothing
here decides or recomputes anything: it narrates what the engine, verifier and gateway did.
"""

from dataclasses import dataclass
from typing import Any

from nirnaya.console.plain import (
    decision_sentence,
    lookup_sentence,
    reason_sentences,
    reason_type_text,
    stage_name,
    verdict,
    write_sentence,
)
from nirnaya.console.session import KIT, DemoSession, M
from nirnaya.console.views import KIT_GOALS, KIT_HEADLINES, kit_card
from nirnaya.models.enums import DisputeStatus, Stage


@dataclass(frozen=True)
class Message:
    who: str  # airwallex | nirnaya | ai | check | person
    kind: str
    title: str
    text: str
    tone: str = "neutral"  # neutral | good | warn | stop | info
    cycle: int = 1


@dataclass(frozen=True)
class Story:
    case_id: str
    label: str
    headline: str
    amount: str
    question: str
    state: str
    verdict: str | None
    messages: tuple[Message, ...]
    can_reject: bool
    needs_person: bool


def _sentence(text: str) -> str:
    text = str(text).strip()
    return (text[:1].upper() + text[1:]).rstrip(".") + "."


def case_story(session: DemoSession, label: str) -> Story:
    store = session.store
    case_id = session.kit_ids.get(label, label)
    case = store.case(M, case_id)
    card = kit_card(session, label) if case is not None else None
    arcs = {a.cycle: a for a in card.cycles} if card else {}
    writes = {w.request_id: w for w in store.writes(M, case_id)}
    snapshots = store.disputes(M, case_id)
    messages: list[Message] = []
    ai_drafted = False
    drafted = 0

    def say(who: str, kind: str, title: str, text: str, tone: str = "neutral", cycle: int = 1):
        messages.append(Message(who, kind, title, _sentence(text), tone, cycle))

    def plan(cycle: int) -> None:
        arc = arcs.get(cycle)
        if arc is not None and arc.initial is not None:
            word = verdict(arc.initial.action)[0].lower()
            say(
                "nirnaya",
                "plan",
                "First look, with Airwallex's data only",
                f"On its own this would be: {word}. {decision_sentence(arc.initial)}",
                cycle=cycle,
            )

    for e in store.audit_events(M, case_id) if case is not None else []:
        b: dict[str, Any] = e["body"]
        cycle = int(b.get("cycle", 1) or 1)
        event = e["event"]
        if event == "CASE_OPENED":
            d = snapshots[0]
            say(
                "airwallex",
                "dispute",
                "New dispute",
                f"{d.amount}, reason: {reason_type_text(d.reason.type)} "
                f"({(d.card_brand or 'card').title()} {d.reason.original_code or 'no code'}), "
                f"at the {stage_name(str(d.stage))} stage. Reply due {d.due_at:%d %b %Y}",
            )
            plan(1)
        elif event == "NEW_CYCLE":
            stage = str(b.get("stage"))
            if stage == Stage.PRE_ARBITRATION:
                say(
                    "airwallex",
                    "update",
                    "Moved to pre-arbitration",
                    "Airwallex moved the dispute to pre-arbitration, its own review",
                    cycle=cycle,
                )
            else:
                say(
                    "airwallex",
                    "rejected",
                    "The bank rejected the evidence",
                    f"The dispute moved to the {stage_name(stage)} stage and needs a new answer",
                    "warn",
                    cycle,
                )
            plan(cycle)
        elif event == "DISPUTE_UPDATED":
            status = str(b.get("status", "")).lower().replace("_", " ")
            if b.get("status") != DisputeStatus.REQUIRES_RESPONSE:
                say(
                    "airwallex",
                    "update",
                    "Airwallex updated the dispute",
                    f"It now shows the dispute as {status}",
                    cycle=cycle,
                )
        elif event == "TOOL_CALL":
            say(
                "nirnaya",
                "lookup",
                "Checking the merchant's records",
                lookup_sentence(b["tool"], b["key"], b.get("records") or []),
                cycle=cycle,
            )
        elif event == "TOOL_REFUSED":
            say("nirnaya", "lookup", "A lookup was refused", str(b.get("why")), "warn", cycle)
        elif event == "COUNTEREVIDENCE_SWEEP":
            n = len(b.get("fetched") or [])
            say(
                "nirnaya",
                "sweep",
                "Looking for anything that points the other way",
                f"Checked every record that could contradict the response ({n} more looked up)",
                cycle=cycle,
            )
        elif event == "AGENT_RUN" and b.get("kind") == "DRAFT":
            ai_drafted = True
        elif event == "APPROVED":
            word = verdict(str(b.get("action")))[0].lower()
            say(
                "person",
                "approved",
                "A person decided",
                f"{b.get('approver')} chose: {word}",
                "info",
                cycle,
            )
        elif event == "CLOSED_MANUALLY":
            say(
                "person",
                "closed",
                "Handled outside Nirnaya",
                f"{b.get('approver')} took the case over",
                "info",
                cycle,
            )
        elif event == "STATE":
            frm, to = b.get("from"), b.get("to")
            arc = arcs.get(cycle)
            if to == "INVESTIGATING" and frm == "ASSESSED":
                say(
                    "nirnaya",
                    "search",
                    "Looking for evidence that could change the answer",
                    "Only the records that could flip the decision are fetched",
                    cycle=cycle,
                )
            elif to == "DECIDED" and arc is not None and arc.revised is not None:
                say(
                    "nirnaya",
                    "decision",
                    f"Decision: {verdict(arc.revised.action)[0].lower()}",
                    decision_sentence(arc.revised),
                    "info",
                    cycle,
                )
            elif frm == "DRAFTING" and to == "VERIFYING":
                drafted = len(b.get("claims") or [])
                by = "Claude drafted" if ai_drafted else "Drafted"
                say(
                    "ai" if ai_drafted else "nirnaya",
                    "draft",
                    "Response drafted",
                    f"{by} {drafted} claims, each citing one field of one record",
                    cycle=cycle,
                )
                ai_drafted = False
            elif frm == "VERIFYING" and to == "RENDERING":
                say(
                    "check",
                    "check",
                    "Every claim checked",
                    f"All {drafted} claims match the records they cite",
                    "good",
                    cycle,
                )
            elif frm == "VERIFYING" and to == "DRAFTING":
                say(
                    "check",
                    "blocked",
                    "A claim was blocked",
                    "A claim didn't match its record. The AI is told why and drafts once more",
                    "warn",
                    cycle,
                )
            elif frm == "VERIFYING" and to == "REFERRED":
                failed = "; ".join(
                    f"{claim}: {', '.join(str(c).lower().replace('_', ' ') for c in checks)}"
                    for claim, checks in (b.get("blocked") or {}).items()
                )
                say(
                    "check",
                    "blocked",
                    "A claim failed its check",
                    failed or "The records behind the response disagree",
                    "stop",
                    cycle,
                )
            elif frm == "RENDERING" and to == "PRE_WRITE_CHECK":
                say(
                    "nirnaya",
                    "evidence",
                    "Evidence document ready",
                    "A PDF of the checked claims, each with its source record",
                    cycle=cycle,
                )
            elif frm == "PRE_WRITE_CHECK" and to == "SUBMITTING":
                say(
                    "check",
                    "guard",
                    "Final safety checks passed",
                    "Re-read the dispute from Airwallex and re-checked its stage, amount, "
                    "deadline and authority before writing",
                    "good",
                    cycle,
                )
            elif frm == "SUBMITTING" and to == "AWAITING_OUTCOME":
                w = writes.get(str(b.get("request_id")))
                info = bool(w and w.request.get("customer_info"))
                text = write_sentence(
                    str(b.get("action")),
                    str(b.get("ledger")),
                    dry_run=bool(b.get("dry_run")),
                    customer_info=info,
                )
                say("nirnaya", "sent", "Sent to Airwallex", text, "good", cycle)
            if to == "REFERRED":
                codes = b.get("reasons") or (
                    [f"VERIFICATION_{b['outcome']}"]
                    if b.get("outcome")
                    else [str(b["failure"])]
                    if b.get("failure")
                    else []
                )
                why = " ".join(reason_sentences(codes)) or str(b.get("why", "A person decides"))
                if frm == "DECIDED":  # the decision just said why
                    why = "Waiting for a person to accept, fight, or handle it outside Nirnaya"
                say("person", "person", "Over to a person", why, "info", cycle)
            elif to in ("DEFERRED", "CLOSED") and b.get("why"):
                title = "Handed back to Airwallex" if to == "DEFERRED" else "Nothing to do"
                say("nirnaya", to.lower(), title, str(b["why"]), cycle=cycle)

    decided = [a for a in arcs.values() if a.revised is not None]
    latest = snapshots[-1] if snapshots else None
    return Story(
        case_id=case_id,
        label=label,
        headline=KIT_HEADLINES.get(label, label),
        amount=str(session.cases[label].dispute.amount) if label in session.cases else "",
        question=KIT_GOALS.get(label, ""),
        state="NOT_RUN" if case is None else case.state,
        verdict=decided[-1].revised.action if decided else None,
        messages=tuple(messages),
        can_reject=(
            label == KIT[0]
            and session.can_act
            and not session.rejected
            and latest is not None
            and latest.stage is Stage.RFI
            and latest.status is DisputeStatus.CHALLENGED
        ),
        needs_person=case is not None and case.state == "REFERRED",
    )
