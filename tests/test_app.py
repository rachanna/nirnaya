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
    assert [t.label for t in app.tabs] == [
        "Kit 4 demo",
        "Review queue",
        "Case detail",
        "Evaluation",
    ]
    assert "Run the kit cases" in "\n".join(i.value for i in app.info)


def test_running_the_kit_then_the_rejection_and_an_approval(app):
    app.button(key="run_kit").click().run()
    assert not app.exception
    text = all_text(app)
    assert "K1 · " in text and "CHALLENGE" in text and "UNANSWERED_CUSTOMER_CONTACT" in text
    app.button(key="reject").click().run()
    assert not app.exception
    assert "Cycle 2 · CHARGEBACK" in all_text(app)
    app.button(key="acc-K3").click().run()
    assert not app.exception
    assert "Nothing waiting for a person." in "\n".join(i.value for i in app.info)
