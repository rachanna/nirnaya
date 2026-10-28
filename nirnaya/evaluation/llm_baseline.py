"""The LLM-only baseline: one model call per case decides and drafts, with nothing checking it.

    NIRNAYA_LIVE_LLM=1 uv run python -m nirnaya.evaluation.llm_baseline

It is shown everything an unguarded agent would get: the dispute and all the case's records,
customer messages included. Records are still passed as untrusted data blocks, never in the
system prompt. It answers ACCEPT, CHALLENGE or ESCALATE with the claims for a challenge. There
is no engine, no verifier and no investigation loop. The verifier is used afterwards only to
score its claims.

The answers are cached in eval/results/llm_only.json so the report is reproducible without
calling the model again.
"""

import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nirnaya import assets
from nirnaya.agent.llm_drafter import VALUE_KINDS, build_claims
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation.cases import EvalCase, load_cases
from nirnaya.evaluation.policies import Act, CaseResult, actionable, score_claims
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError, LLMError, Message, ToolSpec
from nirnaya.llm.untrusted import untrusted
from nirnaya.models.enums import AmountBasis, EvidenceType
from nirnaya.models.policy import MerchantPolicy

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
CACHE = RESULTS / "llm_only.json"
DECIDE = "decide"

# Prompt variants: each names its versioned system and user prompts (nirnaya/assets).
VARIANTS = {"naive": "baseline_naive"}


@dataclass(frozen=True)
class BaselinePrompts:
    variant: str
    system: assets.Asset
    user: assets.Asset
    tools: assets.Asset
    decide_description: str

    @property
    def refs(self) -> tuple[str, ...]:
        return (self.system.ref, self.user.ref, self.tools.ref)


def load_prompts(variant: str = "naive", env: Mapping[str, str] | None = None) -> BaselinePrompts:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; one of {sorted(VARIANTS)}")
    env = os.environ if env is None else env
    stem = VARIANTS[variant]
    tools_asset, tools = assets.prompt_table("tools", env)
    return BaselinePrompts(
        variant,
        assets.prompt(f"{stem}_system", env),
        assets.prompt(f"{stem}_user", env),
        tools_asset,
        tools["baseline"]["decide"]["description"],
    )


_ACTS = {"ACCEPT": Act.ACCEPT, "CHALLENGE": Act.CHALLENGE, "ESCALATE": Act.REFER}


def decide_spec(record_ids: list[str], description: str) -> ToolSpec:
    item = {
        "type": "object",
        "properties": {
            "source_record_id": {"type": "string", "enum": record_ids},
            "source_field": {"type": "string"},
            "establishes": {"type": "string", "enum": sorted(EvidenceType)},
            "extracted_value": {"type": "string"},
            "value_kind": {"type": "string", "enum": VALUE_KINDS},
            "text": {"type": "string"},
            "amount": {"type": "string"},
            "currency": {"type": "string"},
            "basis": {"type": "string", "enum": [b.value for b in AmountBasis]},
            "at": {"type": "string"},
            "date": {"type": "string"},
            "tz": {"type": "string"},
        },
        "required": [
            "source_record_id",
            "source_field",
            "establishes",
            "extracted_value",
            "value_kind",
        ],
    }
    return ToolSpec(
        name=DECIDE,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTS)},
                "rationale": {"type": "string"},
                "claims": {"type": "array", "items": item},
            },
            "required": ["action", "rationale", "claims"],
        },
    )


def _dispute(case: EvalCase) -> dict[str, Any]:
    d = case.dispute
    return {
        "dispute_id": d.id,
        "stage": str(d.stage),
        "status": str(d.status),
        "reason_type": d.reason.type,
        "reason_code": d.reason.original_code,
        "reason_description": d.reason.description,
        "amount": str(d.amount),
        "refunded": None if d.refunded_amount is None else str(d.refunded_amount),
        "card_brand": None if d.card_brand is None else str(d.card_brand),
        "due_at": d.due_at.isoformat(),
    }


def _fees(policy: MerchantPolicy) -> dict[str, str]:
    return {
        "currency": policy.currency,
        "fee_chargeback": str(policy.fee_chargeback),
        "fee_pre_resolution": str(policy.fee_pre_resolution),
        "fee_pre_arbitration": str(policy.fee_pre_arbitration),
        "handling_cost": str(policy.handling_cost),
    }


def system_prompt(prompts: BaselinePrompts, policy: MerchantPolicy = DEMO_POLICY) -> str:
    """Static instructions only: no case data, no customer text."""
    return assets.render(prompts.system)


def case_prompt(
    case: EvalCase, prompts: BaselinePrompts, policy: MerchantPolicy = DEMO_POLICY
) -> str:
    records = [
        {"id": r.id, "system": str(r.source_system), "author": str(r.author), "fields": r.fields}
        for r in case.evidence_records()
    ]
    return assets.render(
        prompts.user,
        dispute=untrusted(_dispute(case), source="dispute"),
        records=untrusted(records, source="case-records"),
        **_fees(policy),
    )


def ask(llm: LLMClient, case: EvalCase, prompts: BaselinePrompts | None = None) -> dict[str, Any]:
    """One call to the decide tool. Returns the raw answer, as cached."""
    prompts = prompts or load_prompts()
    ids = [r.id for r in case.evidence_records()]
    try:
        resp = llm.complete(
            system=system_prompt(prompts),
            messages=[Message.user(case_prompt(case, prompts))],
            tools=[decide_spec(ids, prompts.decide_description)],
            force_tool=DECIDE,
            # Reasoning models spend part of this on thinking; leave room for the answer.
            max_tokens=8192,
        )
    except LLMError as e:
        return {"error": str(e)[:300]}
    call = next((c for c in resp.tool_calls if c.name == DECIDE), None)
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    if call is None:
        return {"error": f"no decision ({resp.stop})", **usage}
    args = call.arguments
    return {
        "action": args.get("action"),
        "rationale": str(args.get("rationale", ""))[:1000],
        "claims": args.get("claims") if isinstance(args.get("claims"), list) else [],
        **usage,
    }


def run(
    llm: LLMClient,
    cases: list[EvalCase],
    *,
    now: datetime,
    out: Callable[[str], None] = print,
    prompts: BaselinePrompts | None = None,
) -> dict[str, Any]:
    prompts = prompts or load_prompts()
    answers = {}
    for case in cases:
        if not actionable(case.dispute):
            continue  # Airwallex is handling it; no policy acts
        answers[case.id] = ask(llm, case, prompts)
        out(f"  {case.id}: {answers[case.id].get('action') or answers[case.id].get('error')}")
    return {
        "provider": llm.provider,
        "model": llm.model,
        "variant": prompts.variant,
        "prompts": list(prompts.refs),
        "run_at": now.isoformat(),
        "cases": answers,
    }


@dataclass(frozen=True)
class Cache:
    provider: str
    model: str
    run_at: str
    cases: dict[str, dict[str, Any]]
    variant: str = "naive"
    # Prompt versions the answers came from; empty for a run made before prompts were versioned.
    prompts: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path = CACHE) -> "Cache | None":
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            data["provider"],
            data["model"],
            data["run_at"],
            data["cases"],
            data.get("variant", "naive"),
            tuple(data.get("prompts", ())),
        )


def complete_item(item: Any) -> Any:  # noqa: ANN401
    """Fill a value field the model left out from its own extracted_value, so the baseline is
    judged on what it claimed, not on a schema formality (the schema marks these optional)."""
    if not isinstance(item, dict) or not isinstance(item.get("extracted_value"), str):
        return item
    filled = dict(item)
    kind = item.get("value_kind")
    if kind in {"NAME", "IDENTIFIER", "ENUM"} and not item.get("text"):
        filled["text"] = item["extracted_value"]
    if kind == "DATETIME" and not item.get("at"):
        filled["at"] = item["extracted_value"]
    return filled


def llm_only(case: EvalCase, cache: Cache) -> CaseResult:
    """The cached answer as a policy result; its claims are built and scored, never trusted."""
    if not actionable(case.dispute):
        return CaseResult(case.id, Act.NONE)
    answer = cache.cases.get(case.id, {})
    act = _ACTS.get(str(answer.get("action")), Act.REFER)  # no usable answer: a person decides
    if act is not Act.CHALLENGE:
        return CaseResult(case.id, act)
    ids = {r.id for r in case.evidence_records()}
    items = [complete_item(i) for i in answer.get("claims") or []]
    claims, dropped = build_claims(case.dispute, items, ids)
    # A claim that cannot even be built is an unsupported statement that would have been sent.
    unsupported = score_claims(case, claims) + len(dropped)
    return CaseResult(case.id, act, tuple(claims), unsupported_submitted=unsupported)


def main(env: dict[str, str] | None = None, out: Callable[[str], None] = print) -> int:
    if env is None:
        load_dotenv()
        env = dict(os.environ)
    if env.get("NIRNAYA_LIVE_LLM") != "1":
        out("This calls the configured model once per case. Set NIRNAYA_LIVE_LLM=1 to run it.")
        return 2
    try:
        llm = make_llm(llm_settings_from_env(env), env)
    except LLMConfigError as e:
        out(f"Configuration error: {e}")
        return 2
    out(f"LLM-only baseline with {llm.provider}/{llm.model}")
    result = run(llm, load_cases(), now=datetime.now(UTC), out=out)
    RESULTS.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    out(f"Saved {len(result['cases'])} answers to {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
