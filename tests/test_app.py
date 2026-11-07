"""Smoke test: the Streamlit console renders headless and runs the kit flow without errors."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

pytestmark = pytest.mark.app

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("NIRNAYA_AGENT", "scripted")  # never a model call from a test
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception
    return at


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


def test_the_console_renders_every_tab(app):
    assert [t.label for t in app.tabs] == ["Demo", "Needs a person", "Case history", "Results"]
    assert "Run the kit cases" in "\n".join(i.value for i in app.info)


def test_running_the_kit_then_the_rejection_and_an_approval(app):
    app.button(key="run_kit").click().run()
    assert not app.exception
    text = all_text(app)
    assert "Case 1 · " in text and "Decided: **fight it**" in text
    assert "never got a reply" in text  # case 3's reason, in plain words
    app.button(key="reject").click().run()
    assert not app.exception
    assert "At the chargeback stage" in all_text(app)
    app.button(key="acc-K3").click().run()
    assert not app.exception
    assert "Nothing waiting for a person." in "\n".join(i.value for i in app.info)


def test_a_proposed_quote_is_confirmed_from_the_review_queue(monkeypatch):
    from tests.console.test_proposed_evidence import session

    monkeypatch.setenv("NIRNAYA_AGENT", "scripted")
    s = session()
    finding = s.store.findings(s.store.merchant_ids()[0], "NOTED", 1)[0].finding_id
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["session"] = s
    at.run()
    assert not at.exception
    assert "Evidence the agent found in a note" in all_text(at)
    assert at.button(key="qchal-NOTED").disabled  # nothing confirmed yet
    at.checkbox(key=f"q-{finding}").check().run()
    at.button(key="qchal-NOTED").click().run()
    assert not at.exception
    assert "Nothing waiting for a person." in "\n".join(i.value for i in at.info)


def test_the_note_beat_runs_from_the_kit_tab(app):
    app.button(key="note_case").click().run()
    assert not app.exception
    text = all_text(app)
    assert "cached answer by" in text  # the committed Claude run, replayed and labelled
    assert "AG-F1 is waiting for a person" in "\n".join(i.value for i in app.info)


def test_the_self_correction_beat_runs_and_is_approved_from_the_queue(app):
    app.button(key="redraft_case").click().run()
    assert not app.exception
    assert "cached answer by" in all_text(app)  # the committed Claude redraft, replayed
    assert any("First draft, blocked" in e.value for e in app.error)
    assert any("Redraft, verified" in x.value for x in app.success)
    app.button(key="chal-N1").click().run()
    assert not app.exception
    assert "**Result:** Challenge confirmed" in all_text(app)


def test_the_console_still_runs_with_no_model_key(monkeypatch):
    """FC-17: a missing key falls back to the scripted agent, with a warning, not a traceback."""
    monkeypatch.setenv("NIRNAYA_AGENT", "llm")
    monkeypatch.setenv("NIRNAYA_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("scripted" in w.value for w in at.warning)
