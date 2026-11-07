"""Nirnaya demo console. Thin: everything it shows or does goes through nirnaya.console.

uv run streamlit run app/streamlit_app.py

Written for people outside payments: each case reads as a verdict, one sentence of why, and
the steps that led there. The numbers and rule codes stay one click away under "Details".
"""

import os

import altair as alt
import streamlit as st

from nirnaya.config.env import load_dotenv
from nirnaya.console.plain import (
    decision_sentence,
    fact_text,
    reason_sentences,
    stage_name,
    status_text,
    verdict,
)
from nirnaya.console.session import KIT, NOTE_CASE, REDRAFT_CASE, DemoSession, M
from nirnaya.console.views import (
    agents_in_run,
    airwallex_unreachable,
    blocked,
    case_detail,
    evaluation,
    fabrication_check,
    kit_card,
    md_escape,
    queue,
    stalled_writes,
    unreadable_disputes,
)
from nirnaya.llm.types import LLMConfigError
from nirnaya.models import Action

ACCENT, MUTED = "#2F6B55", "#B9B2A6"  # Nirnaya in the theme's green, the baselines in stone

load_dotenv()
st.set_page_config(page_title="Nirnaya", page_icon="⚖️", layout="wide")


def session() -> DemoSession:
    if "session" not in st.session_state:
        try:
            st.session_state.session = DemoSession.offline()
        except LLMConfigError as e:
            st.session_state.no_model = str(e)
            scripted = {**os.environ, "NIRNAYA_AGENT": "scripted"}
            st.session_state.session = DemoSession.offline(env=scripted)
    if why := st.session_state.get("no_model"):
        st.warning(f"No model available ({why}), so this runs the scripted agent.")
    return st.session_state.session


# --- sidebar ------------------------------------------------------------------------------------

with st.sidebar:
    st.title("Nirnaya")
    st.caption("Fights the card disputes worth fighting, accepts the rest, and proves every claim.")
    source = st.radio(
        "What to show",
        ["Demo (offline)", "Live sandbox run (view only)"],
        help="The live run is what `python -m nirnaya.sandbox_demo` did on Airwallex's sandbox.",
    )
    if source == "Live sandbox run (view only)":
        sandbox = DemoSession.from_sandbox_run()
        if sandbox is None:
            st.warning("No live sandbox run found in .nirnaya/.")
        else:
            st.session_state.session = sandbox
    elif session().source != "offline":
        st.session_state.session = DemoSession.offline()
    if session().source == "sandbox":
        used = agents_in_run(session())
        st.markdown(
            "**AI agent in this run:** "
            + (used.replace("llm (", "Claude (") if used.startswith("llm") else "off (rule-based)")
        )
    else:
        on = os.environ.get("NIRNAYA_AGENT", "scripted") == "llm"
        model = os.environ.get("NIRNAYA_MODEL", "claude-sonnet-5-5")
        st.markdown("**AI agent:** " + (f"Claude ({model})" if on else "off (rule-based)"))
    st.caption(
        "Decisions, checks and every write to Airwallex are rule-based code. "
        "The AI only gathers evidence and drafts."
    )
    st.caption("Merchant records in this demo are synthetic.")
    if st.button("Start over", key="reset"):
        st.session_state.session = DemoSession.offline()
        st.rerun()
    if session().log:
        st.markdown("**Activity**")
        for line in session().log[-5:]:
            st.caption(line)

s = session()

# --- header -------------------------------------------------------------------------------------

st.title("Card disputes, decided on evidence and money")
steps = st.columns(3)
for col, (name, text) in zip(
    steps,
    (
        ("1 · Read", "Airwallex reports a dispute: how much, why, and the deadline."),
        ("2 · Check", "Nirnaya looks only for the merchant records that could change the answer."),
        (
            "3 · Decide and prove",
            "It fights, accepts or asks a person. Every claim it sends is checked first.",
        ),
    ),
    strict=True,
):
    with col.container(border=True):
        st.markdown(f"**{name}**")
        st.caption(text)

demo, review, detail, evals = st.tabs(["Demo", "Needs a person", "Case history", "Results"])


# --- demo ---------------------------------------------------------------------------------------


def badge(action: str) -> None:
    label, colour = verdict(action)
    st.badge(label, color=colour)


def details_line(view) -> str:  # noqa: ANN001
    """The engine's numbers, for the Details panel."""
    if view is None:
        return "—"
    parts = [f"**{view.action}**"]
    if view.accept_reason:
        parts.append(view.accept_reason)
    if view.p is not None:
        parts.append(f"prior p {view.p} (a prior, not a prediction)")
    if view.margin is not None:
        parts.append(f"margin {view.margin} USD")
    if view.referral_reasons:
        parts.append("a person decides: " + ", ".join(view.referral_reasons))
    return " · ".join(parts)


def result_of(case_id: str) -> None:
    """A finished scenario: what was sent and the claims it carried."""
    sent = [f"{w.action.title()} {w.status.lower()}" for w in s.store.writes(M, case_id)]
    st.markdown("**Result:** " + (", ".join(sent) or "nothing sent"))
    shown = case_detail(s, case_id)
    for c in shown.claims if shown is not None else ():
        mark = "✅" if c.status == "VERIFIED" else "⛔"
        st.caption(md_escape(f"{mark} {c.text} (from {c.source})"))


with demo:
    if s.can_act:
        st.markdown(
            "Three disputes from the Airwallex kit. Press **Run the kit cases** to watch Nirnaya "
            "decide each one."
        )
    else:
        st.markdown(
            "The recorded live run on Airwallex's sandbox: real disputes, real accept and "
            "challenges. View only."
        )
    left, right, _ = st.columns([1, 1, 2])
    if left.button("Run the kit cases", key="run_kit", disabled=not s.can_act, type="primary"):
        s.run_kit()
        st.rerun()
    k1 = kit_card(s, "K1")
    can_reject = (
        s.can_act and not s.rejected and k1 is not None and k1.dispute_status.endswith("CHALLENGED")
    )
    if right.button("The bank rejects case 1", key="reject", disabled=not can_reject):
        s.reject_k1()
        st.rerun()

    cards = [c for c in (kit_card(s, k) for k in KIT) if c is not None]
    if not cards:
        st.info("Run the kit cases to see each one decided, step by step.")
    for n, card in enumerate(cards, start=1):
        decided = [a for a in card.cycles if a.revised is not None]
        last = decided[-1].revised if decided else None
        with st.container(border=True):
            head, mark = st.columns([4, 1])
            head.markdown(f"### Case {n} · {card.headline}")
            head.caption(f"{card.amount} · at Airwallex: {status_text(card.dispute_status)}")
            with mark:
                if last is not None:
                    badge(last.action)
            st.markdown(f"**The question.** {card.goal}")
            for arc in card.cycles:
                if len(card.cycles) > 1:
                    st.markdown(f"**At the {stage_name(arc.stage)} stage**")
                lines = []
                if arc.initial is not None:
                    lines.append(
                        f"With Airwallex's data alone: *{verdict(arc.initial.action)[0].lower()}*."
                    )
                if arc.looked_up:
                    found = "; ".join(arc.looked_up)
                    lines.append(f"Checked the merchant's records: {found}.")
                if arc.revised is not None:
                    lines.append(
                        f"Decided: **{verdict(arc.revised.action)[0].lower()}**. "
                        + decision_sentence(arc.revised)
                    )
                if arc.sent:
                    lines.append("Result: " + "; ".join(arc.sent) + ".")
                elif arc.referral:
                    lines.append("Result: waiting for a person (see **Needs a person**).")
                elif arc.ended:
                    lines.append(f"Result: {arc.ended}.")
                st.markdown("\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1)))
                if arc.airwallex_ai is not None:
                    st.caption(arc.airwallex_ai.text)
            with st.expander("Details: the numbers and rules behind it"):
                for arc in card.cycles:
                    st.markdown(f"**Cycle {arc.cycle} · {arc.stage}**")
                    cols = st.columns(4)
                    cols[0].markdown("**Initial plan** (Airwallex data only)")
                    cols[0].markdown(details_line(arc.initial))
                    if arc.initial and arc.initial.worth_finding:
                        cols[0].caption("Worth finding: " + "; ".join(arc.initial.worth_finding))
                    cols[1].markdown("**New information**")
                    for info in arc.new_information or ("nothing needed",):
                        cols[1].caption(info)
                    for why in arc.refused:
                        cols[1].caption(f"refused: {why}")
                    cols[2].markdown("**Decision** (engine)")
                    cols[2].markdown(details_line(arc.revised))
                    cols[3].markdown("**Status**")
                    for w in arc.writes:
                        cols[3].markdown(w)
                    if arc.referral:
                        cols[3].markdown("Referred: " + ", ".join(arc.referral))
                    if not arc.writes and not arc.referral:
                        cols[3].caption(arc.ended or "no action")
                st.caption(f"{card.reason} · Nirnaya state: {card.state}")

    st.subheader("More scenarios")

    with st.container(border=True):
        check = fabrication_check(s)
        st.markdown("### A made-up claim is caught")
        st.markdown(
            "An unsupervised AI drafted a response that says the parcel was *signed for by "
            "J. Smith*. Before anything is sent, every claim is checked against the record it "
            "cites."
        )
        st.dataframe(
            [
                {
                    "Claim": c.text,
                    "Checked against": c.source,
                    "Result": "✅ supported" if c.status == "VERIFIED" else "⛔ blocked",
                    "Why": "; ".join(c.failures),
                }  # fmt: skip
                for c in check.claims
            ],
            hide_index=True,
            width="stretch",
        )
        for c in blocked(check):
            st.error(f"Blocked: {c.text}. The delivery record has no signature, so it never ships.")

    with st.container(border=True):
        st.markdown("### Evidence hidden in a driver's note")
        st.markdown(
            "A 120 USD *never arrived* claim. The delivery record says *delivered* but has no "
            "signature, so on its own Nirnaya would accept. The driver's note says a signature "
            "was taken. The AI may only quote the note's exact words; a person confirms them, "
            "and the quote is checked word for word."
        )
        staged = s.store.case(M, NOTE_CASE) is not None
        if st.button(
            "Read the driver's note",
            key="note_case",
            disabled=not s.can_act or s.note_case is None or staged,
        ):
            try:
                s.run_note_case()
            except RuntimeError as e:
                st.error(str(e))
            else:
                st.rerun()
        if s.note_source:
            st.caption(f"The note was {s.note_source}.")
        for item in (i for i in queue(s) if i.case_id == NOTE_CASE):
            st.info(
                f"{NOTE_CASE} is waiting for a person: {len(item.proposed)} quote(s) to confirm "
                "in **Needs a person**."
            )
        if staged and not any(i.case_id == NOTE_CASE for i in queue(s)):
            result_of(NOTE_CASE)

    with st.container(border=True):
        st.markdown("### The AI fixes its own mistake")
        st.markdown(
            "To show this, the AI's first draft is broken on purpose with the same made-up "
            "signature. The check blocks it, the AI is told why and drafts again. Because the "
            "first draft claimed too much, a person approves the fixed one."
        )
        staged = s.store.case(M, REDRAFT_CASE) is not None
        if st.button("Run with a broken first draft", key="redraft_case",
                     disabled=not s.can_act or staged):  # fmt: skip
            try:
                s.run_redraft_case()
            except RuntimeError as e:
                st.error(str(e))
            else:
                st.rerun()
        if s.redraft_source:
            st.caption(f"The draft was {s.redraft_source}.")
        if any(i.case_id == REDRAFT_CASE for i in queue(s)):
            st.info(f"{REDRAFT_CASE} is waiting for a person in **Needs a person**.")
        if staged and not any(i.case_id == REDRAFT_CASE for i in queue(s)):
            result_of(REDRAFT_CASE)


# --- needs a person -----------------------------------------------------------------------------

labels = {v: f"Case {i}" for i, v in enumerate((s.kit_ids.get(k, k) for k in KIT), start=1)}
labels |= {NOTE_CASE: "Evidence in a driver's note", REDRAFT_CASE: "The AI fixed its own mistake"}

with review:
    if outage := airwallex_unreachable(s):
        st.error(
            f"Airwallex unreachable since {outage['since']} ({outage['error']}). The deadline "
            "watchdog still runs on stored deadlines."
        )
    for unreadable in unreadable_disputes(s):
        st.warning(
            f"Dispute {unreadable.dispute_id} could not be read and has no case: "
            f"{unreadable.why}. Due {unreadable.due_at or 'unknown'}; handle it in Airwallex."
        )
    for stalled in stalled_writes(s):
        st.error(
            f"Write stalled: {stalled.case_id} ({', '.join(stalled.causes)}). The write may have "
            f"landed: check it in Airwallex, do not resend. Request {stalled.request_id}, "
            f"ledger {stalled.ledger}, {stalled.hours_left} h left."
        )
    st.subheader("Cases waiting for a person")
    items = queue(s)
    if not items:
        st.info("Nothing waiting for a person.")
    approver = st.text_input("Your email (recorded with every decision)", "ops@example.test",
                             key="approver")  # fmt: skip
    for item in items:
        with st.container(border=True):
            flag = "Urgent · " if item.urgent else ""
            name = labels.get(item.case_id, item.case_id)
            st.markdown(f"### {flag}{name}")
            due = f"{item.due_at:%d %b %Y %H:%M} UTC"
            st.caption(f"{item.case_id} · due {due} · {item.hours_left} h left")
            st.markdown("**Why it's here:** " + " ".join(reason_sentences(item.reasons)))
            if item.proposed:
                st.markdown(
                    "**Evidence the agent found in a note.** Confirm a quote only if it shows "
                    "the fact; the check then matches it word for word. Nothing counts until you "
                    "confirm it."
                )
                if item.if_confirmed:
                    st.markdown(
                        "If you confirm, Nirnaya would "
                        + (
                            "**fight** instead of accept."
                            if "→ CHALLENGE" in item.if_confirmed
                            else "change its decision."
                        )  # fmt: skip
                    )
                    st.caption(item.if_confirmed)
                confirmed = []
                for q in item.proposed:
                    st.markdown(
                        f"The note would show that **{fact_text(q.fact)}**. "
                        f"From {md_escape(q.source)}:"
                    )
                    st.markdown("> " + q.note_markdown)
                    if not q.found:
                        st.warning("The quote is no longer word for word in this note.")
                    if st.checkbox("Confirm this quote", key=f"q-{q.finding_id}"):
                        confirmed.append(q.finding_id)
                qa, qb = st.columns(2)
                if qa.button(
                    "Confirm and challenge",
                    key=f"qchal-{item.case_id}",
                    disabled=not s.can_act or not confirmed,
                    type="primary",
                ):
                    s.approve(item.case_id, Action.CHALLENGE, approver, tuple(confirmed))
                    st.rerun()
                if qb.button(
                    "Reject the quotes", key=f"qrej-{item.case_id}", disabled=not s.can_act
                ):
                    ids = tuple(q.finding_id for q in item.proposed)
                    s.reject_quotes(item.case_id, ids, approver)
                    st.rerun()
            if item.redraft:
                st.markdown(
                    "**The AI fixed a blocked draft.** Its first draft claimed something the "
                    "records don't show, so you approve the fixed one. **Fight it** sends "
                    "exactly this draft, checked again."
                )
                for line in item.redraft.blocked:
                    st.error("First draft, blocked: " + md_escape(line))
                for line in item.redraft.redraft:
                    st.success("Redraft, verified: " + md_escape(line))
            a, b, c = st.columns(3)
            if a.button("Accept (refund the customer)", key=f"acc-{item.case_id}",
                        disabled=not s.can_act):  # fmt: skip
                s.approve(item.case_id, Action.ACCEPT, approver)
                st.rerun()
            if b.button("Fight it", key=f"chal-{item.case_id}", disabled=not s.can_act):
                s.approve(item.case_id, Action.CHALLENGE, approver)
                st.rerun()
            if c.button("Handle it outside Nirnaya", key=f"man-{item.case_id}",
                        disabled=not s.can_act):  # fmt: skip
                s.close(item.case_id, approver, "handled outside Nirnaya")
                st.rerun()
            st.caption(
                "Your decision is tied to the amount and evidence shown here. A fight must "
                "still pass every check before it is sent."
            )
            with st.expander("Technical details"):
                st.caption(", ".join(item.reasons))
                st.json(item.detail)


# --- case history -------------------------------------------------------------------------------

with detail:
    ids = [s.kit_ids[k] for k in KIT if kit_card(s, k) is not None]
    for extra in (NOTE_CASE, REDRAFT_CASE):
        if s.store.case(M, extra) is not None:
            ids.append(extra)
    if not ids:
        st.info("Run the kit cases first.")
    else:
        chosen = st.selectbox(
            "Case", ids, key="case", format_func=lambda i: f"{labels.get(i, i)} ({i})"
        )
        d = case_detail(s, chosen)
        if d is not None:
            st.markdown(f"**Where it stands:** {d.state.lower().replace('_', ' ')}")
            st.markdown("**What happened, step by step** (from the tamper-evident audit log)")
            st.code("\n".join(d.timeline) or "(empty)", language=None)
            st.markdown("**Records Nirnaya looked at** (each fingerprinted when captured)")
            st.dataframe(
                [
                    {k: v for k, v in e.items() if k != "fields"} | {"fields": str(e["fields"])}
                    for e in d.evidence
                ],  # fmt: skip
                hide_index=True,
                width="stretch",
            )
            st.markdown("**What it claimed, and whether each claim checked out**")
            st.dataframe(
                [
                    {
                        "claim": c.text,
                        "from": c.source,
                        "result": c.status,
                        "why it failed": "; ".join(c.failures),
                    }
                    for c in d.claims
                ],  # fmt: skip
                hide_index=True,
                width="stretch",
            )
            if d.counterevidence:
                st.markdown("**Records that point the other way** (always shown to a person)")
                for line in d.counterevidence:
                    st.warning(line)
            for note in d.notes:
                st.info(note)


# --- results ------------------------------------------------------------------------------------

with evals:
    report = evaluation()
    if report is None:
        st.info("No report yet. Run `uv run python -m nirnaya.evaluation.report`.")
    else:
        policies = report["policies"]
        ours = policies["nirnaya"]
        st.subheader(f"How Nirnaya does on {report['cases']} test disputes")
        m1, m2, m3 = st.columns(3)
        m1.metric(
            "Same decision as the expert",
            f"{ours['financial']['agreement']}/{ours['financial']['cases']}",
        )
        unsupported = ours["integrity"]["unsupported_submitted"]
        m2.metric("Claims sent that a record doesn't support", unsupported)
        m3.metric("Merchant-policy breaks", ours["compliance"]["violations"])
        st.caption(report["caveat"])
        st.markdown("**Expected loss on the same disputes: lower is better**")
        losses = [
            {
                "policy": p["name"],
                "loss": float(p["financial"]["expected_loss"]),
                "nirnaya": key == "nirnaya",
            }  # fmt: skip
            for key, p in policies.items()
        ]
        # Highest loss at the top, lowest at the bottom. An explicit order: a computed sort is
        # ignored when the label layer shares the axis.
        order = [d["policy"] for d in sorted(losses, key=lambda d: -d["loss"])]
        base = alt.Chart(alt.Data(values=losses)).encode(
            y=alt.Y("policy:N", sort=order, title=None, axis=alt.Axis(labelLimit=320)),
            x=alt.X("loss:Q", title="Expected net loss (USD)", axis=alt.Axis(grid=False)),
        )
        bars = base.mark_bar(cornerRadiusEnd=4, height=22).encode(
            color=alt.condition("datum.nirnaya", alt.value(ACCENT), alt.value(MUTED)),
            tooltip=[alt.Tooltip("policy:N"), alt.Tooltip("loss:Q", format=",.2f")],
        )
        label = (
            base.transform_filter("datum.nirnaya")
            .mark_text(align="left", dx=6)
            .encode(text=alt.Text("loss:Q", format=",.2f"))
        )
        st.altair_chart(
            (bars + label).properties(height=44 * len(losses), background="transparent"),
            width="stretch",
        )

        rows = {
            "Expected net loss (USD)": lambda p: p["financial"]["expected_loss"],
            "vs Nirnaya (USD)": lambda p: p["financial"]["vs_nirnaya"],
            "Agrees with expert": lambda p: (
                f"{p['financial']['agreement']}/{p['financial']['cases']}"
            ),
            "Unsupported claims submitted": lambda p: p["integrity"]["unsupported_submitted"],
            "Challenges with no evidence": lambda p: p["integrity"]["challenges_without_evidence"],
            "Fabrications caught": lambda p: p["integrity"]["fabrications_caught"],
            "Policy violations": lambda p: p["compliance"]["violations"],
            "Lifecycle problems": lambda p: (
                "n/a" if p["lifecycle"] is None else len(p["lifecycle"]["problems"])
            ),
            "Automated / referred": lambda p: (
                f"{p['coverage']['automated']}/{p['coverage']['referred']}"
            ),
        }
        with st.expander("All numbers, every approach"):
            st.dataframe(
                [
                    {"metric": label} | {p["name"]: str(get(p)) for p in policies.values()}
                    for label, get in rows.items()
                ],  # fmt: skip
                hide_index=True,
                width="stretch",
            )
            for variant, m in (report.get("llm_only") or {}).items():
                st.caption(
                    f"LLM only ({variant}): {m['provider']}/{m['model']}, answers cached "
                    f"{m['run_at'][:10]}."
                )
        with st.expander("Per case"):
            st.dataframe(
                [
                    {
                        "case": r["id"],
                        "stage": r["stage"],
                        "amount": r["amount"],
                        "expert": r["expected"],
                    }
                    | {policies[k]["name"]: r[k]["action"] for k in policies}
                    for r in report["rows"]
                ],  # fmt: skip
                hide_index=True,
                width="stretch",
            )
