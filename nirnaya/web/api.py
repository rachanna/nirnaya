"""The web demo's API: kit 4 told as stories, in two tabs.

    uv run python -m nirnaya.web            # API + the built React app on http://localhost:8000

Recorded: the live run of 29 Oct 2026 (Claude on the Airwallex sandbox), replayed from
nirnaya/web/recorded/. Live: the kit run now, with real model calls (NIRNAYA_AGENT=llm on the
provider and model the environment names) against the in-memory fake Airwallex and SYNTHETIC
merchant records. A live action runs in the background; the page polls the case and sees each
model call the moment it starts (RecordingLLM). Results: the evaluation report, every approach on
the same SYNTHETIC cases.

Thin, like the Streamlit console: every action goes through the demo session's commands, so the
engine, verifier and write gateway behave exactly as they do everywhere else.
"""

import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from nirnaya.console.results import results_view
from nirnaya.console.session import EARLY_CASE, KIT, NOTE_CASE, REDRAFT_CASE, DemoSession
from nirnaya.console.story import case_story
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError
from nirnaya.web import recorded
from nirnaya.web.recording import RecordingLLM

ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = ROOT / "web" / "dist"
RESULTS = ROOT / "eval" / "results" / "report.json"
# The live tab: the kit, then cases beyond it: an early accept at pre-chargeback (offline only:
# the sandbox auto-accepts those), and two that show Claude's other jobs (reading a note and
# proposing quotes; fixing a draft the checks blocked).
CASES = (*KIT, EARLY_CASE, NOTE_CASE, REDRAFT_CASE)


@dataclass
class Live:
    """A live demo session, or why there is none (no model configured)."""

    session: DemoSession | None
    recorder: RecordingLLM | None
    why_not: str | None = None


def live_session(env: Mapping[str, str] = os.environ) -> Live:
    """The kit with the model doing the agent steps; every call recorded for the page."""
    try:
        recorder = RecordingLLM(make_llm(llm_settings_from_env(env), env))
    except LLMConfigError as e:
        return Live(None, None, f"No model is configured: {e}")
    session = DemoSession.offline(env={**env, "NIRNAYA_AGENT": "llm"}, llm=recorder)
    return Live(session, recorder)


class Decision(BaseModel):
    choice: Literal["accept", "fight", "confirm", "manual"]
    approver: str = "ops@example.test"


def create_app(
    live_factory: Callable[[], Live] = live_session,
    *,
    static_dir: Path = WEB_DIST,
    recorded_path: Path = recorded.RECORDED,
    results_path: Path = RESULTS,
) -> FastAPI:
    app = FastAPI(title="Nirnaya demo API")
    lock = threading.Lock()  # the session: one action at a time
    guard = threading.Lock()  # the fields below
    state: dict[str, Any] = {}

    def snapshot() -> dict[str, dict[str, Any]]:
        session = state["live"].session
        return {} if session is None else {k: asdict(case_story(session, k)) for k in CASES}

    def reset() -> None:
        with lock:
            live = live_factory()
            with guard:
                state.update(live=live, busy=None, errors={})
            stories = snapshot()
            with guard:
                state["stories"] = stories

    reset()

    def view(label: str) -> dict[str, Any]:
        if label not in CASES:
            raise HTTPException(404, f"no case {label!r}: the cases are {', '.join(CASES)}")
        live: Live = state["live"]
        if live.session is None:
            raise HTTPException(503, live.why_not or "the live tab is not available")
        with guard:
            story = dict(state["stories"][label])
            busy, error = state["busy"], state["errors"].get(label)
        story.update(
            running=busy == label,
            error=error,
            calls=live.recorder.calls(story["case_id"]) if live.recorder else [],
        )
        return story

    def start(label: str, check: Callable[[dict[str, Any]], str | None], work: Callable) -> Any:
        current = view(label)
        with guard:
            if state["busy"] is not None:
                n = CASES.index(state["busy"]) + 1
                raise HTTPException(409, f"Nirnaya is still working on case {n}")
            problem = check(current)
            if problem:
                raise HTTPException(409, problem)
            state["busy"] = label
            state["errors"].pop(label, None)

        def job() -> None:
            try:
                with lock:
                    work(state["live"].session)
            except Exception as e:  # shown on the page; the case itself failed safe
                with guard:
                    state["errors"][label] = f"{type(e).__name__}: {e}"
            finally:
                with lock:
                    stories = snapshot()
                with guard:
                    state["stories"], state["busy"] = stories, None

        threading.Thread(target=job, daemon=True, name=f"nirnaya-{label}").start()
        return view(label)

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        live: Live = state["live"]
        rec = recorded.load(recorded_path)
        return {
            "live": {
                "available": live.session is not None,
                "model": f"{live.recorder.provider}/{live.recorder.model}"
                if live.recorder
                else None,
                "why_not": live.why_not,
                "airwallex": "simulated (offline)",
            },
            "recorded": None
            if rec is None
            else {k: rec[k] for k in ("recorded_on", "airwallex", "models")},
            "results": results_path.is_file(),
        }

    @app.get("/api/results")
    def results() -> dict[str, Any]:
        if not results_path.is_file():
            raise HTTPException(404, "no evaluation report: python -m nirnaya.evaluation.report")
        return asdict(results_view(json.loads(results_path.read_text(encoding="utf-8"))))

    @app.get("/api/recorded")
    def recorded_run() -> dict[str, Any]:
        rec = recorded.load(recorded_path)
        if rec is None:
            raise HTTPException(404, "no recorded run: python -m nirnaya.web.recorded")
        return rec

    @app.get("/api/cases")
    def cases() -> list[dict[str, Any]]:
        return [view(label) for label in CASES]

    @app.get("/api/cases/{label}")
    def get_case(label: str) -> dict[str, Any]:
        return view(label)

    @app.post("/api/cases/{label}/run")
    def run(label: str) -> dict[str, Any]:
        return start(
            label,
            lambda s: None if s["state"] == "NOT_RUN" else "this case has already run",
            lambda session: session.run_case(label),
        )

    @app.post("/api/cases/{label}/reject")
    def reject(label: str) -> dict[str, Any]:
        return start(
            label,
            lambda s: (
                None if s["can_reject"] else "only case 1, after its challenge, can be rejected"
            ),
            lambda session: session.reject_k1(),
        )

    @app.post("/api/cases/{label}/decide")
    def decide(label: str, decision: Decision) -> dict[str, Any]:
        return start(
            label,
            lambda s: (
                "this case is not waiting for a person"
                if not s["needs_person"]
                else "there is no proposed quote to confirm"
                if decision.choice == "confirm" and not s["proposed_quotes"]
                else None
            ),
            lambda session: session.decide(
                state["stories"][label]["case_id"], decision.choice, decision.approver
            ),
        )

    @app.post("/api/reset")
    def reset_all() -> dict[str, str]:
        with guard:
            if state["busy"] is not None:
                raise HTTPException(409, "wait for the current case to finish")
        reset()
        return {"status": "reset"}

    if static_dir.is_dir():  # the built React app (npm run build in web/)
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app
