"""The web demo's API: the recorded live run, and the live tab (one case at a time, in the
background, with each model call recorded). A thin layer over nirnaya.console."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nirnaya.console.session import DemoSession
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.llm.tracing import annotate
from nirnaya.llm.types import LLMError, Message, ToolCall, ToolSpec
from nirnaya.web import recorded
from nirnaya.web.api import Live, create_app, live_session
from nirnaya.web.recording import RecordingLLM

# The test client runs an asyncio loop, which on Windows needs a local socket pair; outbound
# connections and DNS stay blocked (tests/conftest.py).
pytestmark = pytest.mark.app


def client() -> TestClient:
    # The scripted agent: the same flow, no model calls.
    return TestClient(
        create_app(lambda: Live(DemoSession.offline(env={"NIRNAYA_AGENT": "scripted"}), None))
    )


def settle(c: TestClient, label: str) -> dict:
    for _ in range(400):
        story = c.get(f"/api/cases/{label}").json()
        if not story["running"]:
            return story
        time.sleep(0.05)
    raise AssertionError(f"case {label} still running")


def test_the_three_cases_are_listed_before_anything_runs():
    cases = client().get("/api/cases").json()
    assert [x["label"] for x in cases] == ["K1", "K2", "K3"]
    assert all(x["state"] == "NOT_RUN" and x["messages"] == [] for x in cases)
    assert all(x["running"] is False and x["calls"] == [] for x in cases)
    assert cases[0]["headline"] == "A large fraud claim"


def test_a_run_goes_to_the_background_and_leaves_the_other_cases():
    c = client()
    started = c.post("/api/cases/K2/run")
    assert started.status_code == 200
    story = settle(c, "K2")
    assert story["verdict"] == "ACCEPT" and story["messages"][0]["who"] == "airwallex"
    assert story["error"] is None
    assert c.get("/api/cases/K1").json()["state"] == "NOT_RUN"
    assert c.post("/api/cases/K2/run").status_code == 409  # already run


def test_case_1_rejection_and_case_3_decision():
    c = client()
    c.post("/api/cases/K1/run")
    assert settle(c, "K1")["can_reject"]
    c.post("/api/cases/K1/reject")
    after = settle(c, "K1")
    assert any(m["kind"] == "rejected" for m in after["messages"]) and not after["can_reject"]
    c.post("/api/cases/K3/run")
    assert settle(c, "K3")["needs_person"]
    ok = c.post("/api/cases/K3/decide", json={"choice": "fight", "approver": "a@b.test"})
    assert ok.status_code == 200
    assert not settle(c, "K3")["needs_person"]


def test_errors_are_plain_and_nothing_is_guessed():
    c = client()
    assert c.post("/api/cases/K9/run").status_code == 404
    assert c.post("/api/cases/K2/reject").status_code == 409  # only case 1, after its challenge
    assert c.post("/api/cases/K3/decide", json={"choice": "fight"}).status_code == 409  # not run
    c.post("/api/cases/K3/run")
    settle(c, "K3")
    assert c.post("/api/cases/K3/decide", json={"choice": "maybe"}).status_code == 422


def test_reset_starts_over():
    c = client()
    c.post("/api/cases/K1/run")
    settle(c, "K1")
    c.post("/api/reset")
    assert c.get("/api/cases/K1").json()["state"] == "NOT_RUN"


def test_without_a_model_the_live_tab_says_why_and_the_recording_still_plays():
    c = TestClient(create_app(lambda: live_session(env={})))
    info = c.get("/api/info").json()
    assert info["live"]["available"] is False and "ANTHROPIC_API_KEY" in info["live"]["why_not"]
    assert c.get("/api/cases").status_code == 503
    rec = c.get("/api/recorded").json()
    assert [x["label"] for x in rec["cases"]] == ["K1", "K2", "K3"]
    assert info["recorded"]["models"] == rec["models"]


def test_a_missing_recording_is_a_plain_404(tmp_path: Path):
    c = TestClient(create_app(lambda: live_session(env={}), recorded_path=tmp_path / "none.json"))
    assert c.get("/api/recorded").status_code == 404
    assert c.get("/api/info").json()["recorded"] is None


# --- the recorder ---------------------------------------------------------------------------------

TOOLS = [ToolSpec(name="fetch_order", description="d", input_schema={"type": "object"})]


def test_the_recorder_lists_each_call_with_what_the_model_saw_and_said():
    inner = ScriptedLLM(
        [reply(None, ToolCall(id="1", name="fetch_order", arguments={"key": "ORD-1"}))]
    )
    rec = RecordingLLM(inner, clock=iter([0.0, 1.5]).__next__)
    msgs = [Message.user("case facts")]
    with annotate(step="investigate", case_id="K1", turn=1, prompts=("inv@v1#abc",)):
        out = rec.complete(system="static", messages=msgs, tools=TOOLS)
    assert out.tool_calls[0].name == "fetch_order"
    (call,) = rec.calls("K1")
    assert call["status"] == "done" and call["step"] == "investigate" and call["ms"] == 1500
    assert call["prompts"] == ["inv@v1"] and call["tools"] == ["fetch_order"]
    assert call["sent"] == [{"role": "user", "text": "case facts"}]
    assert call["tool_calls"] == [{"name": "fetch_order", "arguments": {"key": "ORD-1"}}]
    assert (call["input_tokens"], call["output_tokens"]) == (10, 5)
    assert rec.calls("K2") == [] and (rec.provider, rec.model) == ("fake", "fake")


def test_the_recorder_shows_only_the_new_turn_and_records_failures():
    first = reply(None, ToolCall(id="1", name="fetch_order", arguments={"key": "ORD-1"}))
    rec = RecordingLLM(ScriptedLLM([]))
    msgs = [Message.user("facts"), first.as_message(), Message.tool_result("1", "order found")]
    with annotate(case_id="K1"), pytest.raises(LLMError):
        rec.complete(system="s", messages=msgs)
    (call,) = rec.calls("K1")
    assert call["status"] == "failed" and "no more responses" in call["error"]
    assert call["sent"] == [{"role": "tool", "text": "order found"}]


# --- the recorded run ----------------------------------------------------------------------------


def test_the_recorded_run_is_the_kit_and_takes_no_actions():
    rec = recorded.load()
    assert rec is not None and rec["recorded_on"] == "2026-10-29"
    k1, k2, k3 = rec["cases"]
    assert (k1["verdict"], k2["verdict"], k3["verdict"]) == ("CHALLENGE", "ACCEPT", "REFER")
    assert any(m["kind"] == "rejected" for m in k1["messages"])
    assert all(not c["can_reject"] and not c["needs_person"] for c in rec["cases"])
    assert [c["step"] for c in k1["calls"]] == ["draft", "draft"]
    assert all(c["model"] == "anthropic/claude-sonnet-5-5" for c in k1["calls"])


@pytest.mark.skipif(not (Path(".nirnaya") / "sandbox.db").is_file(), reason="no local run stores")
def test_the_recorded_file_matches_the_run_stores():
    import json

    assert json.loads(json.dumps(recorded.build())) == recorded.load()
