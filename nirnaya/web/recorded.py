"""The recorded live run: kit 4 on the Airwallex sandbox with Claude, 29 Oct 2026, kept as a file.

    uv run python -m nirnaya.web.recorded      # rebuild the file from the run's stores in .nirnaya/

The run's stores stay out of git (.nirnaya/), so the web demo replays this export instead: each
case's story (nirnaya.console.story, the same narration as the live tab) and a summary of its
model calls from the stored agent runs. Case 1's two rounds and case 3 come from the final store;
case 2's accept was sent in the first submission of the same run, whose store was kept as
sandbox-submit1-20261029.db (the final run only saw it already accepted).
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nirnaya.console.session import KIT, SANDBOX_DIR, DemoSession, M
from nirnaya.console.story import case_story
from nirnaya.store.repository import Store

RECORDED = Path(__file__).with_name("recorded") / "kit-live-20261029.json"
SOURCES = {"K1": "sandbox.db", "K2": "sandbox-submit1-20261029.db", "K3": "sandbox.db"}


def _summary(run: dict[str, Any]) -> str:
    if run["kind"] == "DRAFT":
        sent = sum(t.get("submitted", 0) for t in run.get("transcript", ()))
        redraft = any(t.get("redraft") for t in run.get("transcript", ()))
        return f"{'Redrafted' if redraft else 'Drafted'} {sent} claims, each citing a record."
    if run["kind"] == "INVESTIGATE":
        return f"Made {len(run.get('tool_calls', ()))} tool calls; stopped: {run['stop']}."
    return f"Stopped: {run['stop']}."


def recorded_calls(store: Store, case_id: str, cycles: int) -> list[dict[str, Any]]:
    """The stored agent runs of a case, in the shape the live tab uses for a call."""
    calls = []
    for cycle in range(1, cycles + 1):
        for run in store.agent_runs(M, case_id, cycle):
            calls.append(
                {
                    "n": len(calls) + 1,
                    "case_id": case_id,
                    "cycle": cycle,
                    "step": run["kind"].lower(),
                    "model": f"{run['provider']}/{run['model']}",
                    "prompts": [p.split("#")[0] for p in run.get("prompts", ())],
                    "status": "done",
                    "stop": run["stop"],
                    "turns": run.get("turns"),
                    "tool_calls": [
                        {"name": c.get("tool"), "arguments": {"key": c.get("key")}}
                        for c in run.get("tool_calls", ())
                    ],
                    "summary": _summary(run),
                    "input_tokens": run["input_tokens"],
                    "output_tokens": run["output_tokens"],
                }
            )
    return calls


def build(directory: Path = SANDBOX_DIR, sources: dict[str, str] = SOURCES) -> dict[str, Any]:
    cases = []
    models: set[str] = set()
    for label in KIT:
        session = DemoSession.from_sandbox_run(directory, db_name=sources[label])
        if session is None:
            raise FileNotFoundError(f"no sandbox run in {directory} ({sources[label]})")
        story = asdict(case_story(session, label))
        cycles = max((m["cycle"] for m in story["messages"]), default=0)
        story["calls"] = recorded_calls(session.store, story["case_id"], cycles)
        story.update(can_reject=False, needs_person=False)  # a recording takes no actions
        models |= {f"{p}/{m}" for p, m in session.store.agent_models(M)}
        cases.append(story)
    return {
        "recorded_on": "2026-10-29",
        "airwallex": "Airwallex sandbox (live API, hosted checkout)",
        "models": sorted(models),
        "cases": cases,
    }


def load(path: Path = RECORDED) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def main() -> None:
    RECORDED.parent.mkdir(exist_ok=True)
    data = build()
    RECORDED.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(c["messages"]) for c in data["cases"])
    print(f"Wrote {RECORDED} ({n} messages, models: {', '.join(data['models'])})")


if __name__ == "__main__":
    main()
