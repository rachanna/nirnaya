"""The LLM-only baseline: one model call per case decides and drafts, with nothing checking it.

    NIRNAYA_LIVE_LLM=1 uv run python -m nirnaya.evaluation.llm_baseline [--variant informed]

Two prompt variants (versioned in nirnaya/assets/prompts):
- naive: the task, the dispute, every record and the fees.
- informed: also the merchant's rules, rendered from its policy: when to escalate, the costs,
  the win priors, FX rates, the stage formulas, whose records may be cited, and a field guide
  saying which record field establishes which fact. It shows how far prompting alone gets.

It is shown everything an unguarded agent would get: the dispute and all the case's records,
customer messages included. Records are still passed as untrusted data blocks, never in the
system prompt. It answers ACCEPT, CHALLENGE or ESCALATE with the claims for a challenge. There
is no engine, no verifier and no investigation loop. The verifier is used afterwards only to
score its claims.

The answers are cached per variant in eval/results/llm_only/<variant>.json, with the model
and prompt versions, so the report is reproducible without calling the model again.
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
from nirnaya.agent.llm_drafter import VALUE_KINDS, build_claims, field_guide
from nirnaya.config.demo import DEMO_FX, DEMO_POLICY
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation.cases import EvalCase, load_cases
from nirnaya.evaluation.policies import Act, CaseResult, actionable, score_claims
from nirnaya.evidence.catalogue import record_establishes
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.tracing import annotate
from nirnaya.llm.types import LLMConfigError, LLMError, Message, ToolSpec
from nirnaya.llm.untrusted import untrusted
from nirnaya.models.enums import AmountBasis, EvidenceType, RecordAuthor
from nirnaya.models.money import FxTable
from nirnaya.models.policy import MerchantPolicy
from nirnaya.models.reasons import REASON_TYPE_CATEGORY

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
CACHES = RESULTS / "llm_only"
DECIDE = "decide"

# Prompt variants: each names its versioned system and user prompts (nirnaya/assets).
VARIANTS = {"naive": "baseline_naive", "informed": "baseline_informed"}


def cache_path(prompts: "BaselinePrompts", run: int = 1) -> Path:
    """One cache per variant and system-prompt version, so versions sit side by side; a repeat
    run of the same prompts (to see whether the model answers the same way) is .runN."""
    suffix = "" if run == 1 else f".run{run}"
    return CACHES / f"{prompts.label}{suffix}.json"


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

    @property
    def label(self) -> str:
        """variant-version, named after the system prompt's version (all refs are recorded)."""
        return f"{self.variant}-{self.system.version}"


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


def _priors(policy: MerchantPolicy) -> str:
    lines = []
    for e in policy.priors:
        has = f" with {' + '.join(sorted(e.evidence))}" if e.evidence else ", no evidence"
        lines.append(f"- {e.reason}{has}: p = {e.p}")
    return "\n".join(lines)


def _fx(fx: FxTable) -> str:
    return "\n".join(f"- 1 {r.base} = {r.rate} {r.quote}" for r in fx.rates)


def _reasons() -> str:
    return "\n".join(f"- {t}: {c}" for t, c in sorted(REASON_TYPE_CATEGORY.items()))


def _policy_values(policy: MerchantPolicy, fx: FxTable) -> dict[str, str]:
    """Every value any informed system-prompt version may use; each takes what it names."""
    return {
        "reasons": _reasons(),
        "merchant_id": policy.merchant_id,
        "currency": policy.currency,
        "referral_threshold": str(policy.referral_amount_threshold),
        "marginal_band": str(policy.marginal_band),
        "F_pre": str(policy.fee_pre_resolution),
        "F_cb": str(policy.fee_chargeback),
        "F_pa": str(policy.fee_pre_arbitration),
        "F_rfi": str(policy.fee_rfi_accept),
        "h": str(policy.handling_cost),
        "h_pre": str(policy.handling_cost_pre),
        "h_rfi": str(policy.handling_cost_rfi),
        "R": str(policy.counted_chargeback_cost),
        "m": str(policy.min_gain),
        "q": str(policy.escalation_probability),
        "unknown_p": str(policy.unknown_reason_prior),
        "priors": _priors(policy),
        "fx": _fx(fx),
    }


def system_prompt(
    prompts: BaselinePrompts, policy: MerchantPolicy = DEMO_POLICY, fx: FxTable = DEMO_FX
) -> str:
    """Static instructions only: no case data, no customer text. The informed variant adds the
    merchant's configuration, which is not case data."""
    if prompts.variant == "naive":
        return assets.render(prompts.system)
    return assets.render_from(prompts.system, _policy_values(policy, fx))


def _guide(case: EvalCase) -> str:
    """The evidence catalogue applied to every record that is not customer-written. Unlike the
    drafter's guide it is not filtered by merchant or hash: the model must apply that rule."""
    pairs = [
        (r, name, fact)
        for r in case.evidence_records()
        if r.author is not RecordAuthor.CUSTOMER
        for name in r.fields
        for fact in sorted(record_establishes(r, name))
    ]
    return field_guide(pairs) or "(no field of these records establishes a catalogued fact)"


def case_prompt(
    case: EvalCase, prompts: BaselinePrompts, policy: MerchantPolicy = DEMO_POLICY
) -> str:
    if prompts.variant == "naive":
        records = [
            {
                "id": r.id,
                "system": str(r.source_system),
                "author": str(r.author),
                "fields": r.fields,
            }
            for r in case.evidence_records()
        ]
        return assets.render(
            prompts.user,
            dispute=untrusted(_dispute(case), source="dispute"),
            records=untrusted(records, source="case-records"),
            **_fees(policy),
        )
    records = [
        {
            "id": r.id,
            "merchant_id": r.merchant_id,
            "system": str(r.source_system),
            "author": str(r.author),
            "fields": r.fields,
        }
        for r in case.evidence_records()
    ]
    return assets.render(
        prompts.user,
        dispute=untrusted(_dispute(case), source="dispute"),
        records=untrusted(records, source="case-records"),
        field_guide=_guide(case),
    )


def ask(llm: LLMClient, case: EvalCase, prompts: BaselinePrompts | None = None) -> dict[str, Any]:
    """One call to the decide tool. Returns the raw answer, as cached."""
    prompts = prompts or load_prompts()
    ids = [r.id for r in case.evidence_records()]
    try:
        with annotate(
            step="baseline",
            case_id=case.id,
            variant=prompts.variant,
            stage=str(case.dispute.stage),
            prompts=prompts.refs,
        ):
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
    def load(cls, path: Path) -> "Cache | None":
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


def main(
    env: dict[str, str] | None = None,
    out: Callable[[str], None] = print,
    argv: list[str] | None = None,
) -> int:
    args = list(argv or [])
    usage = (
        "usage: python -m nirnaya.evaluation.llm_baseline "
        f"[--variant {'|'.join(VARIANTS)}] [--run N]"
    )
    options = dict(zip(args[::2], args[1::2], strict=False))
    run_no = options.get("--run", "1")
    if (
        len(args) % 2
        or set(options) - {"--variant", "--run"}
        or options.get("--variant", "naive") not in VARIANTS
        or not run_no.isdigit()
        or int(run_no) < 1
    ):
        out(usage)
        return 2
    variant, run_no = options.get("--variant", "naive"), int(run_no)
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
    prompts = load_prompts(variant, env)
    out(f"LLM-only baseline ({variant}) with {llm.provider}/{llm.model}: {', '.join(prompts.refs)}")
    result = run(llm, load_cases(), now=datetime.now(UTC), out=out, prompts=prompts)
    path = cache_path(prompts, run_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    out(f"Saved {len(result['cases'])} answers to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
