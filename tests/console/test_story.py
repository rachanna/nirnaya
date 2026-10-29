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


def test_every_message_is_plain_words_and_the_feed_is_deterministic(session):
    session.run_case("K1")
    a, b = case_story(session, "K1"), case_story(session, "K1")
    assert a == b
    for m in a.messages:
        assert m.title and m.text and "_" not in m.title
