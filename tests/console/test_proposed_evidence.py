"""The review queue shows quotes the agent proposed from a note, in context, and acts on a
person's confirmation or rejection. Note text is untrusted, so it is shown literally."""

from nirnaya.agent.runs import AgentRun, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.console.session import DemoSession
from nirnaya.console.views import md_escape, queue
from nirnaya.evaluation.cases import load_cases, synthetic_world
from nirnaya.evidence.catalogue import Quote
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.models import EvidenceType
from nirnaya.models.enums import Action
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.settings import Settings
from nirnaya.store import Store, connect
from tests.harness import Harness
from tests.notes import QUOTE, noted_case

E = EvidenceType
M = DEMO_POLICY.merchant_id


class Proposing:
    def __init__(self, *proposals):
        self.proposals = proposals

    def read(self, dispute, facts, records):
        run = AgentRun(kind="READ", provider="fake", model="fake", turns=1, stop="TOOL_USE")
        return ReadResult(proposals=self.proposals, run=run)


def session(note=None):
    case = noted_case() if note is None else noted_case(driver_notes=note)
    quote = Quote("NOTED-carrier", "driver_notes", E.DELIVERY_SIGNATURE, QUOTE)
    store = Store(connect(check_same_thread=False))  # the console runs on worker threads
    store.register_merchant(
        M, airwallex_account_id="acct-demo", policy=DEMO_POLICY, delegated_authority=True
    )
    aw = FakeAirwallex()
    aw.add_dispute(case.dispute, attempt=case.payment_attempt, intent_owner="same")
    orch = Orchestrator(
        store, aw, aw, synthetic_world([case]), ScriptedInvestigator(), ScriptedDrafter(),
        Settings(dry_run=False), reader=Proposing(quote),
    )  # fmt: skip
    now = Harness.now(case)
    cases = {c.id: c for c in load_cases()} | {case.id: case}  # the page shows the kit too
    s = DemoSession(store, cases, {}, now, aw=aw, orch=orch)
    orch.run_until_idle(now)
    return s


def test_the_queue_shows_the_quote_inside_its_note_and_what_it_would_change():
    (item,) = queue(session())
    assert item.reasons == ("PROPOSED_EVIDENCE",)
    (q,) = item.proposed
    assert (q.fact, q.quote, q.found) == ("DELIVERY_SIGNATURE", QUOTE, True)
    assert q.source == "NOTED-carrier.driver_notes (CARRIER, THIRD_PARTY)"
    assert f"**{md_escape(QUOTE)}**" in q.note_markdown
    assert q.note_markdown.startswith("Handed to the customer")
    assert item.if_confirmed.startswith("If confirmed: ACCEPT → CHALLENGE · p 0.60 → 0.80")


def test_confirm_and_challenge_submits_a_challenge_citing_the_quote():
    s = session()
    (item,) = queue(s)
    s.approve(item.case_id, Action.CHALLENGE, "ops@example.test", (item.proposed[0].finding_id,))
    assert queue(s) == []
    assert [(w.action, w.status) for w in s.store.writes(M, "NOTED")] == [
        ("CHALLENGE", "CONFIRMED")
    ]
    assert "confirming 1 quote(s)" in s.log[-1]


def test_rejecting_the_quotes_keeps_the_case_with_a_person():
    s = session()
    (item,) = queue(s)
    s.reject_quotes(item.case_id, tuple(q.finding_id for q in item.proposed), "ops@example.test")
    (after,) = queue(s)
    assert after.proposed == ()  # rejected quotes are no longer offered
    assert after.reasons == ("PROPOSED_EVIDENCE",)


def test_note_text_is_shown_literally_never_as_markdown():
    note = "[click here](http://evil.example) *signature captured on the handheld device*"
    (item,) = queue(session(note))
    shown = item.proposed[0].note_markdown
    assert "[click here](http" not in shown
    assert "\\[click here\\]\\(http://evil\\.example\\)" in shown


CACHE = {
    "provider": "anthropic",
    "model": "claude-sonnet-5-5",
    "prompts": ["reader_system@v1#x"],
    "case_set": "v1",
    "run_at": "2026-10-29T00:00:00+00:00",
    "cases": {
        "AG-F1": {
            "proposals": [
                {
                    "record_id": "AG-F1-carrier",
                    "field": "driver_notes",
                    "fact": "DELIVERY_SIGNATURE",
                    "quote": "signature captured on the handheld device",
                }
            ],
            "stop": "TOOL_USE",
        }
    },
}


def test_the_note_beat_replays_a_labelled_cached_reading_and_a_confirmation_challenges():
    s = DemoSession.offline(env={"NIRNAYA_AGENT": "scripted"})
    s.run_kit()
    s.run_note_case(caches={"llm-run1": CACHE})
    assert "cached answer by anthropic/claude-sonnet-5-5" in s.note_source
    assert "no model call" in s.note_source
    (item,) = [i for i in queue(s) if i.case_id == "AG-F1"]
    assert item.reasons == ("PROPOSED_EVIDENCE",)
    s.approve("AG-F1", Action.CHALLENGE, "ops@example.test", (item.proposed[0].finding_id,))
    assert [(w.action, w.status) for w in s.store.writes(M, "AG-F1")] == [
        ("CHALLENGE", "CONFIRMED")
    ]
    # The kit cases are untouched by the reader: they have no notes.
    assert [(w.action, w.status) for w in s.store.writes(M, "K2")] == [("ACCEPT", "CONFIRMED")]


def test_without_a_cached_reading_the_note_beat_says_how_to_get_one():
    s = DemoSession.offline(env={"NIRNAYA_AGENT": "scripted"})
    try:
        s.run_note_case(caches={})
    except RuntimeError as e:
        assert "--live" in str(e)
    else:
        raise AssertionError("expected a clear error")


REDRAFT_RUN = {
    "provider": "anthropic",
    "model": "claude-sonnet-5-5",
    "prompts": ["drafter_feedback@v1#x"],
    "run_at": "2026-10-29T00:00:00+00:00",
    "cases": {
        "N1:overreach": {
            "claims": [
                {
                    "source_record_id": "N1-carrier",
                    "source_field": "status",
                    "establishes": "DELIVERY_CONFIRMED",
                    "extracted_value": "DELIVERED",
                    "value_kind": "ENUM",
                    "text": "DELIVERED",
                }
            ],
            "stop": "TOOL_USE",
        }
    },
}


def test_the_self_correction_beat_shows_both_drafts_and_sends_the_reviewed_one():
    s = DemoSession.offline(env={"NIRNAYA_AGENT": "scripted"})
    s.run_kit()
    s.run_redraft_case(cache=REDRAFT_RUN)
    assert "cached answer by anthropic/claude-sonnet-5-5" in s.redraft_source
    (item,) = [i for i in queue(s) if i.case_id == "N1"]
    assert item.reasons == ("REDRAFT_AFTER_OVERREACH",)
    (blocked,) = item.redraft.blocked
    assert "N1-carrier.signed_by" in blocked and "FIELD_MISSING" in blocked
    assert item.redraft.redraft == ("Delivery confirmed by carrier: DELIVERED (N1-carrier.status)",)
    s.approve("N1", Action.CHALLENGE, "ops@example.test")
    assert [(w.action, w.status) for w in s.store.writes(M, "N1")] == [("CHALLENGE", "CONFIRMED")]
    assert s.store.latest_draft(M, "N1", 1) == 2  # the reviewed redraft, not a new one
    # Kit cases still draft as configured.
    assert [(w.action, w.status) for w in s.store.writes(M, "K2")] == [("ACCEPT", "CONFIRMED")]
