"""Nirnaya demo console. Thin: everything it shows or does goes through nirnaya.console.

uv run streamlit run app/streamlit_app.py
"""

import os

import altair as alt
import streamlit as st

from nirnaya.config.env import load_dotenv
from nirnaya.console.session import KIT, NOTE_CASE, DemoSession, M
from nirnaya.console.views import (
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
from nirnaya.models import Action

ACCENT, MUTED = "#2a78d6", "#898781"  # emphasis: Nirnaya in the accent, baselines in gray

load_dotenv()
st.set_page_config(page_title="Nirnaya", page_icon="⚖️", layout="wide")


def session() -> DemoSession:
    if "session" not in st.session_state:
        st.session_state.session = DemoSession.offline()
    return st.session_state.session


# --- sidebar ------------------------------------------------------------------------------------

with st.sidebar:
    st.title("Nirnaya")
    st.caption("Dispute decisions for Airwallex merchants. Every claim verified.")
    source = st.radio(
        "Data",
        ["Simulated Airwallex", "Sandbox run (view only)"],
        help="The sandbox run is what `python -m nirnaya.sandbox_demo` wrote.",
    )
    if source == "Sandbox run (view only)":
        sandbox = DemoSession.from_sandbox_run()
        if sandbox is None:
            st.warning("No completed sandbox run found in .nirnaya/.")
        else:
            st.session_state.session = sandbox
    elif session().source != "offline":
        st.session_state.session = DemoSession.offline()
    agent = os.environ.get("NIRNAYA_AGENT", "scripted")
    model = (
        f"{os.environ.get('NIRNAYA_LLM_PROVIDER', 'anthropic')}/"
        f"{os.environ.get('NIRNAYA_MODEL', 'default')}"
    )
    st.markdown(f"**Agent:** {agent}" + (f" ({model})" if agent == "llm" else ""))
    st.markdown("**Merchant systems:** SYNTHETIC")
    if st.button("Reset", key="reset"):
        st.session_state.session = DemoSession.offline()
        st.rerun()
    for line in session().log[-5:]:
        st.caption(line)

s = session()
demo, review, detail, evals = st.tabs(["Kit 4 demo", "Review queue", "Case detail", "Evaluation"])


# --- kit 4 demo ---------------------------------------------------------------------------------


def decision_line(view) -> str:  # noqa: ANN001
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


with demo:
    st.subheader("Kit 4: a chargeback queue, decided on evidence and economics")
    left, right, _ = st.columns([1, 1, 3])
    if left.button("Run kit cases 1–3", key="run_kit", disabled=not s.can_act):
        s.run_kit()
        st.rerun()
    k1 = kit_card(s, "K1")
    can_reject = (
        s.can_act and not s.rejected and k1 is not None and k1.dispute_status.endswith("CHALLENGED")
    )
    if right.button("Issuer rejects case 1", key="reject", disabled=not can_reject):
        s.reject_k1()
        st.rerun()

    cards = [c for c in (kit_card(s, k) for k in KIT) if c is not None]
    if not cards:
        st.info("Run the kit cases to see each one decided step by step.")
    for card in cards:
        with st.container(border=True):
            st.markdown(f"### {card.label} · {card.title}")
            st.caption(
                f"{card.amount} · {card.reason} · Airwallex: {card.dispute_status} · "
                f"Nirnaya: {card.state}"
            )
            st.markdown(f"**Goal.** {card.goal}")
            for arc in card.cycles:
                if len(card.cycles) > 1:
                    st.markdown(f"**Cycle {arc.cycle} · {arc.stage}**")
                cols = st.columns(4)
                cols[0].markdown("**1. Initial plan** (Airwallex data only)")
                cols[0].markdown(decision_line(arc.initial))
                if arc.initial and arc.initial.worth_finding:
                    cols[0].caption("Worth finding: " + "; ".join(arc.initial.worth_finding))
                cols[1].markdown("**2. New information** (merchant systems)")
                for info in arc.new_information or ("nothing needed",):
                    cols[1].caption(info)
                for why in arc.refused:
                    cols[1].caption(f"refused: {why}")
                cols[2].markdown("**3. Revised decision** (engine)")
                cols[2].markdown(decision_line(arc.revised))
                cols[3].markdown("**4. Status**")
                for w in arc.writes:
                    cols[3].markdown(f"✅ {w}")
                if arc.referral:
                    cols[3].markdown("🧑‍⚖️ Referred: " + ", ".join(arc.referral))
                if not arc.writes and not arc.referral:
                    cols[3].caption("no action")
                if arc.airwallex_ai is not None:
                    mark = {True: "🤝", False: "↔️", None: "🤖"}[arc.airwallex_ai.agree]
                    st.caption(f"{mark} {arc.airwallex_ai.text}")
                    if arc.airwallex_ai.evidence_to_submit:
                        st.caption(
                            "Airwallex AI would submit: "
                            + ", ".join(arc.airwallex_ai.evidence_to_submit)
                        )

    with st.container(border=True):
        check = fabrication_check(s)
        st.markdown(f"### 2-C · {check.title}")
        st.caption("An unguarded agent drafted this. The verifier checks it before anything ships.")
        st.markdown(f"Verification: **{check.outcome}**")
        st.dataframe(
            [
                {
                    "claim": c.text,
                    "cites": c.source,
                    "status": c.status,
                    "why": "; ".join(c.failures),
                }  # fmt: skip
                for c in check.claims
            ],
            hide_index=True,
            use_container_width=True,
        )
        for c in blocked(check):
            st.error(f"Blocked: {c.text} ({c.source})")

    with st.container(border=True):
        st.markdown("### Beyond the kit · evidence only a driver's note holds")
        st.caption(
            "A 120 USD not-received chargeback. The carrier shows DELIVERED with no structured "
            "signature, so the engine accepts (prior 0.60: 72 − 80 < 0). The driver's note says "
            "a signature was captured. The agent may only propose the exact words; a person "
            "confirms them in the review queue, and the verifier checks them word for word."
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
                f"{NOTE_CASE} is waiting in the review queue: "
                f"{len(item.proposed)} proposed quote(s). {item.if_confirmed or ''}"
            )
        if staged and not any(i.case_id == NOTE_CASE for i in queue(s)):
            done = [f"{w.action} {w.status}" for w in s.store.writes(M, NOTE_CASE)]
            st.markdown("Status: " + (", ".join(done) or "no write"))


# --- review queue -------------------------------------------------------------------------------

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
    st.subheader("Referrals: a person decides")
    items = queue(s)
    if not items:
        st.info("Nothing waiting for a person.")
    approver = st.text_input("Approver", "ops@example.test", key="approver")
    for item in items:
        with st.container(border=True):
            flag = "🔴 URGENT · " if item.urgent else ""
            st.markdown(f"**{flag}{item.case_id}** · {', '.join(item.reasons)}")
            st.caption(f"Due {item.due_at:%Y-%m-%d %H:%M} UTC · {item.hours_left} h left")
            if item.proposed:
                st.markdown(
                    "**Evidence the agent found in a note.** Confirm a quote only if it shows "
                    "the fact; the verifier then checks it word for word. Nothing counts until "
                    "you confirm it."
                )
                if item.if_confirmed:
                    st.caption(item.if_confirmed)
                confirmed = []
                for q in item.proposed:
                    st.markdown(f"`{q.fact}` · from {md_escape(q.source)}")
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
                ):
                    s.approve(item.case_id, Action.CHALLENGE, approver, tuple(confirmed))
                    st.rerun()
                if qb.button(
                    "Reject the quotes", key=f"qrej-{item.case_id}", disabled=not s.can_act
                ):
                    ids = tuple(q.finding_id for q in item.proposed)
                    s.reject_quotes(item.case_id, ids, approver)
                    st.rerun()
            with st.expander("Why it was referred"):
                st.json(item.detail)
            st.caption(
                "An approval is bound to the amount, currency and evidence shown here; "
                "an approved challenge must still pass verification."
            )
            a, b, c = st.columns(3)
            if a.button("Approve accept", key=f"acc-{item.case_id}", disabled=not s.can_act):
                s.approve(item.case_id, Action.ACCEPT, approver)
                st.rerun()
            if b.button("Approve challenge", key=f"chal-{item.case_id}", disabled=not s.can_act):
                s.approve(item.case_id, Action.CHALLENGE, approver)
                st.rerun()
            if c.button("Handle manually", key=f"man-{item.case_id}", disabled=not s.can_act):
                s.close(item.case_id, approver, "handled outside Nirnaya")
                st.rerun()


# --- case detail --------------------------------------------------------------------------------

with detail:
    ids = [s.kit_ids[k] for k in KIT if kit_card(s, k) is not None]
    if s.store.case(M, NOTE_CASE) is not None:
        ids.append(NOTE_CASE)
    if not ids:
        st.info("Run the kit cases first.")
    else:
        chosen = st.selectbox("Case", ids, key="case")
        d = case_detail(s, chosen)
        if d is not None:
            st.markdown(f"**State:** {d.state}")
            st.markdown("**Timeline** (from the hash-chained audit log)")
            st.code("\n".join(d.timeline) or "(empty)", language=None)
            st.markdown("**Evidence** (hashed at capture)")
            st.dataframe(
                [
                    {k: v for k, v in e.items() if k != "fields"} | {"fields": str(e["fields"])}
                    for e in d.evidence
                ],  # fmt: skip
                hide_index=True,
                use_container_width=True,
            )
            st.markdown("**Claims and verification**")
            st.dataframe(
                [
                    {
                        "claim": c.text,
                        "cites": c.source,
                        "status": c.status,
                        "failures": "; ".join(c.failures),
                    }
                    for c in d.claims
                ],  # fmt: skip
                hide_index=True,
                use_container_width=True,
            )
            if d.counterevidence:
                st.markdown("**Counterevidence** (always shown to the approver)")
                for line in d.counterevidence:
                    st.warning(line)
            for note in d.notes:
                st.info(note)


# --- evaluation ---------------------------------------------------------------------------------

with evals:
    report = evaluation()
    if report is None:
        st.info("No report yet. Run `uv run python -m nirnaya.evaluation.report`.")
    else:
        st.subheader(f"Nirnaya against three baselines on {report['cases']} synthetic cases")
        st.caption(report["caveat"])
        policies = report["policies"]
        losses = [
            {
                "policy": p["name"],
                "loss": float(p["financial"]["expected_loss"]),
                "nirnaya": key == "nirnaya",
            }  # fmt: skip
            for key, p in policies.items()
        ]
        base = alt.Chart(alt.Data(values=losses)).encode(
            y=alt.Y("policy:N", sort="x", title=None),
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
        st.altair_chart((bars + label).properties(height=200), use_container_width=True)

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
        st.dataframe(
            [
                {"metric": label} | {p["name"]: str(get(p)) for p in policies.values()}
                for label, get in rows.items()
            ],  # fmt: skip
            hide_index=True,
            use_container_width=True,
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
                use_container_width=True,
            )
