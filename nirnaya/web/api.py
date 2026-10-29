"""The web demo's API: one kit case at a time, told as a story (nirnaya.console.story).

    uv run python -m nirnaya.web            # API + the built React app on http://localhost:8000

Thin, like the Streamlit console: every action goes through the demo session's commands, so the
engine, verifier and write gateway behave exactly as they do everywhere else. Offline by
default (the fake Airwallex, SYNTHETIC merchant records); the agent follows NIRNAYA_AGENT.
"""

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from nirnaya.console.session import KIT, DemoSession
from nirnaya.console.story import case_story

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class Decision(BaseModel):
    choice: Literal["accept", "fight", "manual"]
    approver: str = "ops@example.test"


def create_app(
    session_factory: Callable[[], DemoSession] = DemoSession.offline,
    *,
    static_dir: Path = WEB_DIST,
    env: Mapping[str, str] = os.environ,
) -> FastAPI:
    app = FastAPI(title="Nirnaya demo API")
    state = {"session": session_factory()}
    lock = threading.Lock()  # one demo session; requests take turns

    def story(label: str) -> dict[str, Any]:
        if label not in KIT:
            raise HTTPException(404, f"no case {label!r}: the cases are {', '.join(KIT)}")
        return asdict(case_story(state["session"], label))

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        on = env.get("NIRNAYA_AGENT", "scripted") == "llm"
        model = env.get("NIRNAYA_MODEL", "claude-sonnet-5-5")
        return {
            "agent": f"Claude ({model})" if on else "off (rule-based)",
            "airwallex": "simulated (offline)",
            "synthetic": True,
        }

    @app.get("/api/cases")
    def cases() -> list[dict[str, Any]]:
        with lock:
            return [story(label) for label in KIT]

    @app.get("/api/cases/{label}")
    def get_case(label: str) -> dict[str, Any]:
        with lock:
            return story(label)

    @app.post("/api/cases/{label}/run")
    def run(label: str) -> dict[str, Any]:
        with lock:
            story(label)  # 404 before acting
            state["session"].run_case(label)
            return story(label)

    @app.post("/api/cases/{label}/reject")
    def reject(label: str) -> dict[str, Any]:
        with lock:
            if not story(label)["can_reject"]:
                raise HTTPException(409, "only case 1, after its challenge, can be rejected")
            state["session"].reject_k1()
            return story(label)

    @app.post("/api/cases/{label}/decide")
    def decide(label: str, decision: Decision) -> dict[str, Any]:
        with lock:
            current = story(label)
            if not current["needs_person"]:
                raise HTTPException(409, "this case is not waiting for a person")
            state["session"].decide(current["case_id"], decision.choice, decision.approver)
            return story(label)

    @app.post("/api/reset")
    def reset() -> dict[str, str]:
        with lock:
            state["session"] = session_factory()
            return {"status": "reset"}

    if static_dir.is_dir():  # the built React app (npm run build in web/)
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app
