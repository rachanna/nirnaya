"""A case's run as a feed of plain messages, in the order things happened (web demo).

Built from the audit log and the decisions on record; wording comes from console.plain. Nothing
here decides or recomputes anything: it narrates what the engine, verifier and gateway did.
"""

from dataclasses import dataclass, replace
from typing import Any

from nirnaya.console.plain import (
    decision_sentence,
    fact_text,
    lookup_sentence,
    reason_sentences,
    reason_type_text,
    stage_name,
    verdict,
    write_sentence,
)
from nirnaya.console.session import EARLY_CASE, KIT, NOTE_CASE, REDRAFT_CASE, DemoSession, M
from nirnaya.console.views import KIT_GOALS, KIT_HEADLINES, kit_card, proposed_quotes
from nirnaya.evaluation.self_correction import INJECTED_CLAIM
from nirnaya.models.enums import Action, DisputeStatus, Stage

# The two cases beyond the kit: what each shows, in the merchant's terms.
EXTRA_HEADLINES = {
    EARLY_CASE: "Cheaper to settle early",
    NOTE_CASE: "Proof only in a driver's note",
    REDRAFT_CASE: "A draft that claims too much",
}
EXTRA_GOALS = {
    EARLY_CASE: (
        "A 1,000 USD fraud claim at the pre-chargeback stage, with nothing on file to fight it."
        " Is it cheaper to settle now than to let it become a chargeback? Offline only: the"
        " Airwallex sandbox accepts pre-chargebacks by itself within a second."
    ),
    NOTE_CASE: (
        'An "it never arrived" claim. The carrier record has no signature, but the driver wrote'
        " one in a note. Can Claude find it, and does Nirnaya wait for a person to confirm it?"
    ),
    REDRAFT_CASE: (
        'An "it never arrived" claim worth fighting. For this demo the first draft is broken on'
        " purpose: it says the parcel was signed for. Do the checks catch it, and can Claude fix"
        " it?"
    ),
}


@dataclass(frozen=True)
class Message:
    who: str  # airwallex | nirnaya | ai | check | person
    kind: str
    title: str
    text: str
    tone: str = "neutral"  # neutral | good | warn | stop | info
    cycle: int = 1


@dataclass(frozen=True)
class QuoteNote:
    quote: str
    note: str
    source: str


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
    proposed_quotes: tuple[str, ...] = ()  # quotes waiting for a person to confirm
    quote_notes: tuple[QuoteNote, ...] = ()  # each one in its whole note: read it in context


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
    approved: str | None = None  # a person's decision in the latest round overrides the engine's
    lookups: dict[tuple[int, str, str], int] = {}  # (cycle, tool, key) -> its message's index

    def say(who: str, kind: str, title: str, text: str, tone: str = "neutral", cycle: int = 1):
        messages.append(Message(who, kind, title, _sentence(text), tone, cycle))

    def findings(cycle: int) -> list:
        return store.findings(M, case_id, cycle) if case is not None else []

    def plan(cycle: int) -> None:
        arc = arcs.get(cycle)
        if arc is not None and arc.initial is not None:
            word = verdict(arc.initial.action)[0].lower()
            say(
                "nirnaya",
                "plan",
                "First look, with Airwallex's data only",
                f"On its own this would be: {word}. {decision_sentence(arc.initial, arc.stage)}",
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
            approved = None
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
            lookups[(cycle, str(b["tool"]), str(b["key"]))] = len(messages)
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
        elif event == "AGENT_RUN" and b.get("kind") == "INVESTIGATE":
            for call in b.get("tool_calls") or []:  # "tool:key:outcome"; Claude chose these
                tool, key, _ = (str(call).split(":", 2) + ["", ""])[:3]
                i = lookups.get((cycle, tool, key))
                if i is not None:
                    messages[i] = replace(messages[i], who="ai", title="Claude looked up a record")
        elif event == "AGENT_RUN" and b.get("kind") == "READ":
            found = findings(cycle)
            say(
                "ai",
                "read",
                "Read the written notes",
                f"Claude proposed {len(found)} exact quote(s). A quote counts only after code finds"
                " it word for word in the note and a person confirms it"
                if found
                else "Claude found nothing in the notes that the response could use",
                cycle=cycle,
            )
            for f in found:
                say(
                    "ai",
                    "quote",
                    "Proposed quote",
                    f"From the {f.field.replace('_', ' ')} of {f.record_id}: \u201c{f.quote}\u201d "
                    f"It would show: {fact_text(str(f.fact))}",
                    "info",
                    cycle,
                )
        elif event == "APPROVED":
            approved = str(b.get("action"))
            word = verdict(approved)[0].lower()
            say(
                "person",
                "approved",
                "A person decided",
                f"The approver, {b.get('approver')}, chose: {word}"
                + (
                    f", confirming {len(b['confirmed_findings'])} quote(s) word for word"
                    if b.get("confirmed_findings")
                    else ""
                ),
                "info",
                cycle,
            )
        elif event == "CLOSED_MANUALLY":
            say(
                "person",
                "closed",
                "Handled outside Nirnaya",
                f"The approver, {b.get('approver')}, took the case over",
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
                if b.get("finished"):
                    say(
                        "nirnaya",
                        "search",
                        "Finishing the search",
                        f"The agent stopped early, so code made {len(b['finished'])} more "
                        "lookup(s) before deciding",
                        cycle=cycle,
                    )
                if b.get("support"):
                    n = len(b["support"])
                    odds = (
                        f". The assumed win rate goes from {_percent(b['p_before'])} to "
                        f"{_percent(arc.revised.p)}"
                        if b.get("p_before") and arc.revised.p
                        else ""
                    )
                    say(
                        "nirnaya",
                        "support",
                        "Making the fight stronger",
                        f"It is worth fighting, so code looked up {n} more record(s) the response"
                        f" can cite{odds}",
                        "good",
                        cycle,
                    )
                say(
                    "nirnaya",
                    "decision",
                    f"Decision: {verdict(arc.revised.action)[0].lower()}",
                    decision_sentence(arc.revised, arc.stage),
                    "info",
                    cycle,
                )
            elif frm == "DRAFTING" and to == "VERIFYING" and b.get("reuse_draft"):
                say(
                    "nirnaya",
                    "draft",
                    "The reviewed draft goes out",
                    "No new draft: what is sent is exactly what the person saw, checked again",
                    cycle=cycle,
                )
            elif frm == "DRAFTING" and to == "VERIFYING":
                drafted = len(b.get("claims") or [])
                by = "Claude drafted" if ai_drafted else "Drafted"
                if INJECTED_CLAIM in (b.get("claims") or []):
                    say(
                        "nirnaya",
                        "draft",
                        "A first draft, broken on purpose",
                        f"For this demo, code wrote a {drafted}-claim draft and planted one claim:"
                        " that the parcel was signed for, which the carrier record doesn't show",
                        "warn",
                        cycle,
                    )
                else:
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
                    f"{_blocked(b)}. Claude is told why and drafts once more",
                    "warn",
                    cycle,
                )
            elif frm == "VERIFYING" and to == "REFERRED" and b.get("outcome") == "PASSED":
                say(
                    "check",
                    "check",
                    "Every claim checked",
                    f"All {drafted} claims match the records they cite",
                    "good",
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
                    ["REDRAFT_AFTER_OVERREACH"]  # the one referral after a passing draft
                    if b.get("outcome") == "PASSED"
                    else [f"VERIFICATION_{b['outcome']}"]
                    if b.get("outcome")
                    else [str(b["failure"])]
                    if b.get("failure")
                    else []
                )
                why = " ".join(reason_sentences(codes)) or str(b.get("why", "A person decides"))
                said = arc is not None and arc.revised is not None
                if frm == "DECIDED" and said and arc.revised.action == Action.REFER:
                    # the decision just said why
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
        headline=KIT_HEADLINES.get(label) or EXTRA_HEADLINES.get(label, label),
        amount=_amount(session, label),
        question=KIT_GOALS.get(label) or EXTRA_GOALS.get(label, ""),
        state="NOT_RUN" if case is None else case.state,
        verdict=approved
        or ("REFER" if case is not None and case.state == "REFERRED" else None)
        or (decided[-1].revised.action if decided else None),
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
        proposed_quotes=tuple(f.quote for f in findings(case.cycle) if f.status == "PROPOSED")
        if case is not None and case.state == "REFERRED"
        else (),
        quote_notes=tuple(
            QuoteNote(q.quote, q.note, q.source)
            for q in proposed_quotes(session.store, case_id, case.cycle)
        )
        if case is not None and case.state == "REFERRED"
        else (),
    )


def _percent(p: object) -> str:
    return f"{round(float(str(p)) * 100)}%"


def _amount(session: DemoSession, label: str) -> str:
    if label in session.cases:
        return str(session.cases[label].dispute.amount)
    note = session.note_case
    return str(note.case.dispute.amount) if note is not None and note.case.id == label else ""


def _blocked(body: dict[str, Any]) -> str:
    blocked = body.get("blocked") or {}
    if INJECTED_CLAIM in blocked:
        return "The planted claim was blocked: its record has no field that shows a signature"
    n = len(blocked)
    return f"{n} claim(s) didn't match their records" if n else "A claim didn't match its record"
