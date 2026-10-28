"""Does the model investigator and drafter change anything? Every case through the full
pipeline twice, scripted agent and model agent, everything else identical.

    NIRNAYA_LIVE_LLM=1 uv run python -m nirnaya.evaluation.llm_agent

The scripted side is the evaluation's Nirnaya column (on the fabrication cases it uses the
careless fixture drafter). The model side uses LLMInvestigator and LLMDrafter on every case,
so a fabrication case measures whether the model drafts a false claim. Results are cached in
eval/results/llm_agent/<model>.json with the prompt versions and case set.
"""

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nirnaya.agent.llm_drafter import LLMDrafter
from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation import case_set
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.evaluation.llm_baseline import RESULTS
from nirnaya.evaluation.policies import nirnaya
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError
from nirnaya.store import Store, connect

CACHE_DIR = RESULTS / "llm_agent"


def run_case(case: EvalCase, llm: LLMClient | None, world: Any = None) -> dict[str, Any]:  # noqa: ANN401
    """One case through the pipeline: the scripted agent if llm is None, else the model."""
    store = Store(connect())
    agents = None if llm is None else (LLMInvestigator(llm, env={}), LLMDrafter(llm, env={}))
    result = nirnaya(case, world or synthetic_world([case]), agents=agents, store=store)
    m, cid = case.dispute.merchant_id, case.dispute.id
    row = store.case(m, cid)
    cycles = range(1, row.cycle + 1) if row else range(0)
    stops = [r["stop"] for c in cycles for r in store.agent_runs(m, cid, c)]
    return {
        "action": str(result.action),
        "unsupported": result.unsupported_submitted,
        "blocked": result.blocked_before_write,
        "lookups": sum(len(store.tools_called(m, cid, c)) for c in cycles),
        "tokens": sum(store.agent_tokens(m, cid, c) for c in cycles),
        "agent_stops": stops,
    }


def compare(scripted: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    def total(side: dict[str, Any], key: str) -> int:
        return sum(int(r.get(key) or 0) for r in side.values())

    keys = ("unsupported", "blocked", "lookups", "tokens")
    return {
        "route_differs": sorted(c for c in scripted if scripted[c]["action"] != llm[c]["action"]),
        "scripted": {k: total(scripted, k) for k in keys},
        "llm": {k: total(llm, k) for k in keys},
    }


def main(
    env: dict[str, str] | None = None,
    out: Callable[[str], None] = print,
    cache_dir: Path = CACHE_DIR,
) -> int:
    if env is None:
        load_dotenv()
        env = dict(os.environ)
    if env.get("NIRNAYA_LIVE_LLM") != "1":
        out("This calls the configured model for every case. Set NIRNAYA_LIVE_LLM=1 to run it.")
        return 2
    try:
        llm = make_llm(llm_settings_from_env(env), env)
    except LLMConfigError as e:
        out(f"Configuration error: {e}")
        return 2
    cases = load_cases()
    world = synthetic_world(cases)
    probe = (LLMInvestigator(llm, env={}), LLMDrafter(llm, env={}))
    scripted, model = {}, {}
    for case in cases:
        scripted[case.id] = run_case(case, None, world)
        try:
            model[case.id] = run_case(case, llm, world)
        except Exception as e:  # noqa: BLE001 - record and carry on; never hide a failure
            model[case.id] = {"action": "ERROR", "error": f"{type(e).__name__}: {e}"[:300]}
        mark = "" if model[case.id]["action"] == scripted[case.id]["action"] else "  <- differs"
        a, b = scripted[case.id]["action"], model[case.id]["action"]
        out(f"  {case.id}: scripted {a}, model {b}{mark}")
    summary = compare(scripted, model)
    result = {
        "provider": llm.provider,
        "model": llm.model,
        "prompts": [
            probe[0].system.ref,
            probe[0].user.ref,
            probe[1].system.ref,
            probe[1].user.ref,
            probe[0].tools_asset.ref,
        ],
        "case_set": case_set.check()["version"],
        "run_at": datetime.now(UTC).isoformat(),
        "fabrication_cases": sorted(c.id for c in cases if "fabrication" in c.tags),
        "summary": summary,
        "cases": {cid: {"scripted": scripted[cid], "model": model[cid]} for cid in scripted},
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{llm.model}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    out(f"\nRoute differs on: {summary['route_differs'] or 'none'}")
    out(f"Scripted: {summary['scripted']}\nModel:    {summary['llm']}\nSaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
