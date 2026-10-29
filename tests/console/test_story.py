"""A case's run told as a feed of plain messages, in the order things happened, built from the
audit log. Wording only: every fact comes from the store."""

import pytest

from nirnaya.console.session import DemoSession
from nirnaya.console.story import case_story

SCRIPTED = {"NIRNAYA_AGENT": "scripted"}


@pytest.fixture
def session():
    return DemoSession.offline(env=SCRIPTED)


def kinds(story):
    return [m.kind for m in story.messages]


def test_one_case_runs_alone(session):
    session.run_case("K2")
    assert session.store.case("merchant-demo", "K2") is not None
    assert session.store.case("merchant-demo", "K1") is None
    assert session.store.case("merchant-demo", "K3") is None


def test_case_1_reads_from_the_dispute_to_a_confirmed_challenge(session):
    session.run_case("K1")
    story = case_story(session, "K1")
    assert story.verdict == "CHALLENGE"
    k = kinds(story)
    assert k[0] == "dispute" and k[1] == "plan"
    assert "lookup" in k and "decision" in k and "draft" in k
    assert k.index("decision") < k.index("draft") < k.index("check") < k.index("sent")
    first = story.messages[0]
    assert first.who == "airwallex" and "1500.00 USD" in first.text
    decision = next(m for m in story.messages if m.kind == "decision")
    assert decision.title == "Decision: fight it" and "745.00 USD" in decision.text
    sent = next(m for m in story.messages if m.kind == "sent")
    assert sent.tone == "good" and "Airwallex confirmed it" in sent.text
    assert story.can_reject and not story.needs_person


def test_case_2_is_accepted_because_fighting_costs_more(session):
    session.run_case("K2")
    story = case_story(session, "K2")
    assert story.verdict == "ACCEPT"
    decision = next(m for m in story.messages if m.kind == "decision")
    assert decision.text == "Fighting would cost more than it could win back."
    assert next(m for m in story.messages if m.kind == "sent").text.startswith("Accept sent")


def test_case_3_ends_with_a_person_and_the_reason_in_words(session):
    session.run_case("K3")
    story = case_story(session, "K3")
    assert story.verdict == "REFER" and story.needs_person
    last = story.messages[-1]
    assert last.who == "person" and "accept, fight" in last.text
    decision = next(m for m in story.messages if m.kind == "decision")
    assert "never got a reply" in decision.text
    session.decide("K3", "accept", "ops@example.test")
    after = case_story(session, "K3")
    assert any(m.kind == "approved" and "accept" in m.text.lower() for m in after.messages)
    assert after.messages[-1].kind in {"sent", "update"} and not after.needs_person


def test_the_banks_rejection_starts_a_second_round(session):
    session.run_case("K1")
    session.reject_k1()
    story = case_story(session, "K1")
    rejected = next(m for m in story.messages if m.kind == "rejected")
    assert rejected.who == "airwallex" and "chargeback" in rejected.text
    later = story.messages[story.messages.index(rejected) :]
    assert [m.kind for m in later].count("sent") == 1  # the second challenge
    assert "device and IP" in next(m for m in later if m.kind == "sent").text
    assert not story.can_reject


def test_the_note_case_shows_the_proposed_quote_and_waits_for_a_person(session):
    session.run_case("AG-F1")  # scripted: a cached Claude reading, replayed
    story = case_story(session, "AG-F1")
    assert story.headline == "Proof only in a driver's note" and story.amount == "120.00 USD"
    k = kinds(story)
    assert k.index("decision") < k.index("read") < k.index("quote") < k.index("person")
    quote = next(m for m in story.messages if m.kind == "quote")
    assert quote.who == "ai" and "signature captured" in quote.text
    assert story.messages[-1].text.startswith("The agent found evidence in a written note")
    assert story.needs_person and story.proposed_quotes == (
        "Handed to the customer in person at the door; signature captured on the handheld device.",
    )
    session.decide("AG-F1", "confirm", "ops@example.test")
    after = case_story(session, "AG-F1")
    approved = next(m for m in after.messages if m.kind == "approved")
    assert "confirming 1 quote(s) word for word" in approved.text
    assert after.verdict == "CHALLENGE"  # the person's decision, over the engine's accept
    assert after.messages[-1].kind in {"sent", "update"} and not after.proposed_quotes


def test_lookups_claude_chose_are_credited_to_claude(session):
    session.run_case("AG-F1")  # scripted: code made every lookup
    before = case_story(session, "AG-F1")
    assert all(m.who == "nirnaya" for m in before.messages if m.kind == "lookup")
    # What a live investigation adds: an agent run naming the lookup the model chose.
    body = {"kind": "INVESTIGATE", "cycle": 1, "tool_calls": ["get_carrier_tracking:TRK-AG-F1:OK"]}
    session.store.audit("merchant-demo", "AG-F1", "AGENT_RUN", body, session.now)
    lookups = [m for m in case_story(session, "AG-F1").messages if m.kind == "lookup"]
    assert [m.who for m in lookups] == ["nirnaya", "nirnaya", "ai"]
    assert lookups[-1].title == "Claude looked up a record" and "TRK-AG-F1" in lookups[-1].text


def test_confirming_needs_a_proposed_quote(session):
    session.run_case("K3")
    with pytest.raises(ValueError, match="no proposed quote"):
        session.decide("K3", "confirm", "ops@example.test")


def test_the_self_correction_case_names_the_planted_claim_and_the_fix(session):
    session.run_case("N1")  # scripted: Claude's cached redraft, replayed
    story = case_story(session, "N1")
    titles = [m.title for m in story.messages]
    planted = titles.index("A first draft, broken on purpose")
    assert titles[planted + 1] == "A claim was blocked"
    assert "planted claim was blocked" in story.messages[planted + 1].text
    redraft = story.messages[planted + 2]
    assert redraft.who == "ai" and redraft.title == "Response drafted"
    assert titles[planted + 3] == "Every claim checked"
    assert story.messages[-1].text.startswith("The first draft claimed more than")
    session.decide("N1", "fight", "ops@example.test")
    after = case_story(session, "N1")
    assert any(m.title == "The reviewed draft goes out" for m in after.messages)
    assert after.verdict == "CHALLENGE" and after.messages[-1].kind in {"sent", "update"}


def test_every_message_is_plain_words_and_the_feed_is_deterministic(session):
    session.run_case("K1")
    a, b = case_story(session, "K1"), case_story(session, "K1")
    assert a == b
    for m in a.messages:
        assert m.title and m.text and "_" not in m.title
