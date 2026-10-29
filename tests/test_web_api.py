"""The web demo's API: run one case at a time, read its story, the bank's rejection for case 1,
a person's decision for case 3, reset. A thin layer over nirnaya.console."""

import pytest
from fastapi.testclient import TestClient

from nirnaya.console.session import DemoSession
from nirnaya.web.api import create_app

# The test client runs an asyncio loop, which on Windows needs a local socket pair; outbound
# connections and DNS stay blocked (tests/conftest.py).
pytestmark = pytest.mark.app


def client():
    return TestClient(create_app(lambda: DemoSession.offline(env={"NIRNAYA_AGENT": "scripted"})))


def test_the_three_cases_are_listed_before_anything_runs():
    c = client()
    cases = c.get("/api/cases").json()
    assert [x["label"] for x in cases] == ["K1", "K2", "K3"]
    assert all(x["state"] == "NOT_RUN" and x["messages"] == [] for x in cases)
    assert cases[0]["headline"] == "A large fraud claim"


def test_running_one_case_returns_its_story_and_leaves_the_others():
    c = client()
    story = c.post("/api/cases/K2/run").json()
    assert story["verdict"] == "ACCEPT" and story["messages"][0]["who"] == "airwallex"
    assert c.get("/api/cases/K1").json()["state"] == "NOT_RUN"


def test_case_1_rejection_and_case_3_decision():
    c = client()
    c.post("/api/cases/K1/run")
    assert c.get("/api/cases/K1").json()["can_reject"]
    after = c.post("/api/cases/K1/reject").json()
    assert any(m["kind"] == "rejected" for m in after["messages"]) and not after["can_reject"]
    c.post("/api/cases/K3/run")
    decided = c.post("/api/cases/K3/decide", json={"choice": "fight", "approver": "a@b.test"})
    assert decided.status_code == 200 and not decided.json()["needs_person"]


def test_errors_are_plain_and_nothing_is_guessed():
    c = client()
    assert c.post("/api/cases/K9/run").status_code == 404
    assert c.post("/api/cases/K2/reject").status_code == 409  # only case 1, after its challenge
    assert c.post("/api/cases/K3/decide", json={"choice": "fight"}).status_code == 409  # not run
    c.post("/api/cases/K3/run")
    bad = c.post("/api/cases/K3/decide", json={"choice": "maybe"})
    assert bad.status_code == 422


def test_reset_starts_over_and_info_names_the_agent():
    c = client()
    c.post("/api/cases/K1/run")
    c.post("/api/reset")
    assert c.get("/api/cases/K1").json()["state"] == "NOT_RUN"
    info = c.get("/api/info").json()
    assert info["agent"] == "off (rule-based)" and info["synthetic"] is True
