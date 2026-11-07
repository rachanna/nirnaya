"""Before presenting: everything the demo needs, in one command.

    uv run python -m nirnaya.preflight          # no network, no model call
    uv run python -m nirnaya.preflight --live   # also one tiny model call

It only reads and runs the offline fake. It never calls Airwallex and never writes anywhere.
A failure means the demo would break; a warning means one part of it is unavailable.
"""

import argparse
import json
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nirnaya.config.env import load_dotenv
from nirnaya.console.session import KIT, SANDBOX_DIR, DemoSession
from nirnaya.console.story import case_story
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError, LLMError, Message
from nirnaya.orchestrator.settings import Settings
from nirnaya.web import recorded
from nirnaya.web.api import RESULTS, WEB_DIST

OK, WARN, FAIL = "OK", "WARN", "FAIL"
SCRIPTED = {"NIRNAYA_AGENT": "scripted"}
# What the demo script says about the three kit cases.
KIT_VERDICTS = {"K1": "CHALLENGE", "K2": "ACCEPT", "K3": "REFER"}
PORTS = {8000: "web page", 8501: "Streamlit console"}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _web_page(web_dist: Path) -> tuple[str, str]:
    if (web_dist / "index.html").is_file():
        return OK, "built"
    return FAIL, "not built: cd web && npm install && npm run build"


def _recording() -> tuple[str, str]:
    run = recorded.load()
    if run is None:
        return FAIL, "the recorded run is missing"
    return OK, f"{len(run['cases'])} cases, recorded {run['recorded_on']}"


def _results(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return FAIL, "no report: uv run python -m nirnaya.evaluation.report"
    report = json.loads(path.read_text(encoding="utf-8"))
    financial = report["policies"]["nirnaya"]["financial"]
    score = f"{financial['agreement']}/{financial['cases']}"
    if financial["agreement"] != financial["cases"]:
        return FAIL, f"Nirnaya is at {score}, and the demo script says all of them"
    return OK, score


def _kit() -> tuple[str, str]:
    session = DemoSession.offline(env=SCRIPTED)
    session.run_kit()
    verdicts = {label: case_story(session, label).verdict for label in KIT}
    if verdicts != KIT_VERDICTS:
        return FAIL, f"expected {KIT_VERDICTS}, got {verdicts}"
    session.reject_k1()
    if case_story(session, "K1").verdict != "CHALLENGE":
        return FAIL, "case 1 did not fight again after the rejection"
    return OK, "fight, accept, a person; case 1 fights again after the rejection"


def _replays() -> tuple[str, str]:
    session = DemoSession.offline(env=SCRIPTED)
    session.run_note_case()
    session.run_redraft_case()
    return OK, "the driver's note and the fixed draft both replay"


def _sandbox_run(directory: Path) -> tuple[str, str]:
    if DemoSession.from_sandbox_run(directory) is None:
        return WARN, "no local run, so the console's sandbox view is empty; the web recording works"
    return OK, "the console's sandbox view can open it"


def _dry_run(env: Mapping[str, str]) -> tuple[str, str]:
    if Settings.from_env(env).dry_run:
        return OK, "DRY_RUN is on"
    return FAIL, "DRY_RUN=false: set it back to true before presenting"


def _model(env: Mapping[str, str], live: bool, llm: LLMClient | None) -> tuple[str, str]:
    try:
        client = llm or make_llm(llm_settings_from_env(env), env)
    except LLMConfigError as e:
        return (FAIL if live else WARN), f"the live tab is off: {e}"
    if not live:
        return OK, f"{client.provider}/{client.model} configured (--live also tries a call)"
    try:
        client.complete(system="Reply with OK.", messages=[Message.user("Ready?")], max_tokens=16)
    except LLMError as e:
        return FAIL, str(e)
    return OK, f"{client.provider}/{client.model} answered"


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _safely(name: str, work: Callable[[], tuple[str, str]]) -> Check:
    try:
        status, detail = work()
    except Exception as e:  # a check that crashes is a failed check, not a crashed preflight
        return Check(name, FAIL, f"{type(e).__name__}: {e}")
    return Check(name, status, detail)


def run_checks(
    env: Mapping[str, str],
    *,
    live: bool = False,
    web_dist: Path = WEB_DIST,
    results: Path = RESULTS,
    sandbox_dir: Path = SANDBOX_DIR,
    port_open: Callable[[int], bool] = _port_open,
    llm: LLMClient | None = None,
) -> list[Check]:
    checks = [
        _safely("Web page", lambda: _web_page(web_dist)),
        _safely("Recording", _recording),
        _safely("Results", lambda: _results(results)),
        _safely("Kit cases", _kit),
        _safely("Replayed note and redraft", _replays),
        _safely("Sandbox run", lambda: _sandbox_run(sandbox_dir)),
        _safely("Dry run", lambda: _dry_run(env)),
        _safely("Model key", lambda: _model(env, live, llm)),
    ]
    for port, what in PORTS.items():
        in_use = port_open(port)
        checks.append(
            Check(
                f"Port {port}",
                WARN if in_use else OK,
                f"in use: the {what} may already be running" if in_use else f"free for the {what}",
            )
        )
    return checks


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
    **options: Any,  # noqa: ANN401
) -> int:
    parser = argparse.ArgumentParser(prog="nirnaya.preflight", description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="also make one tiny model call")
    args = parser.parse_args(argv)
    if env is None:
        load_dotenv()
        env = dict(os.environ)
    checks = run_checks(env, live=args.live, **options)
    for c in checks:
        out(f"{c.status:<5} {c.name:<26} {c.detail}")
    failed = sum(c.status == FAIL for c in checks)
    warned = sum(c.status == WARN for c in checks)
    if failed:
        out(f"\nNot ready: {failed} failed.")
        return 1
    out(f"\nReady{f', with {warned} warning(s)' if warned else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
