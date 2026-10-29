"""Self-correction, measured by fault injection (architecture 8.6).

On its own, the model drafter was never blocked on the 42 cases, so there is nothing for it to
correct there. So code breaks the first draft on purpose, and the real model gets the
verifier's findings and drafts again. Two injections, on every case that should end in a
challenge:

- format: every value written as prose (the mistake Claude made in the informed v1/v2
  baselines). A passing redraft goes out with no person.
- overreach: a correct draft plus a claim citing a field the record lacks (like kit 2-C's
  "signed for by J. Smith"). A passing redraft still goes to a person.

    uv run python -m nirnaya.evaluation.self_correction           # offline, from the cache
    NIRNAYA_LIVE_LLM=1 uv run python -m nirnaya.evaluation.self_correction --live

This measures repair, not how often the model errs.
"""

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from nirnaya.agent.llm_drafter import SUBMIT, LLMDrafter
from nirnaya.agent.runs import DraftResult
from nirnaya.agent.scripted import ScriptedDrafter
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation.agentic import pipeline
from nirnaya.evaluation.cases import EvalCase, Route, load_cases
from nirnaya.evidence.catalogue import render_claim_text
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.fake import reply
from nirnaya.llm.types import LLMConfigError, LLMResponse, Message, ToolCall, ToolSpec, Usage
from nirnaya.models.claim import Claim, TextValue
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType, RecordAuthor
from nirnaya.models.evidence import EvidenceRecord

REPO = Path(__file__).resolve().parents[2]
CACHES = REPO / "eval" / "results" / "self_correction"
Injection = Literal["format", "overreach"]
INJECTIONS: tuple[Injection, ...] = ("format", "overreach")

INJECTED_CLAIM = "claim-injected"  # the planted claim (the kit 2-C fabrication)


def challenge_cases() -> list[EvalCase]:
    return [c for c in load_cases() if c.expected.route is Route.CHALLENGE]


def break_draft(
    claims: tuple[Claim, ...], records: list[EvidenceRecord], kind: Injection
) -> list[Claim]:
    """A deliberately wrong first draft, built by code from a correct one."""
    if kind == "format":
        return [
            c.model_copy(update={"extracted_value": f"{c.source_field}: {c.extracted_value}"})
            for c in claims
        ]
    by_id = {r.id: r for r in records}
    cited = next(
        by_id[c.source_record_id]
        for c in claims
        if c.source_record_id in by_id
        and by_id[c.source_record_id].author is not RecordAuthor.CUSTOMER
    )
    field = next(f for f in ("signed_by", "signature_image_id") if f not in cited.fields)
    value = TextValue(kind="NAME", text="J. Smith")
    extra = Claim(
        id=INJECTED_CLAIM,
        case_id=cited.case_id,
        merchant_id=cited.merchant_id,
        text=render_claim_text(EvidenceType.DELIVERY_SIGNATURE, value),
        source_record_id=cited.id,
        source_field=field,
        establishes=EvidenceType.DELIVERY_SIGNATURE,
        extracted_value="J. Smith",
        normalised_value=value,
    )
    return [*claims, extra]


class Injected:
    """First draft: broken by code. Redraft: the real drafter, given the verifier's findings."""

    revises = True

    def __init__(self, inner: LLMDrafter, kind: Injection) -> None:
        self.inner, self.kind = inner, kind

    def draft(
        self,
        dispute: Dispute,
        records: list[EvidenceRecord],
        feedback: dict[str, Any] | None = None,
    ) -> DraftResult:
        if feedback is None:
            good = ScriptedDrafter().draft(dispute, records).claims
            return DraftResult(claims=tuple(break_draft(good, records, self.kind)))
        return self.inner.draft(dispute, records, feedback=feedback)


@dataclass
class Recording:
    """An LLMClient that records each answer (live) or replays a recorded one (offline)."""

    inner: LLMClient | None
    provider: str
    model: str
    answer: dict[str, Any] | None = None

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] = (),  # type: ignore[assignment]
        force_tool: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        if self.inner is not None:
            resp = self.inner.complete(
                system=system,
                messages=messages,
                tools=tools,
                force_tool=force_tool,
                max_tokens=max_tokens,
            )
            call = next((c for c in resp.tool_calls if c.name == SUBMIT), None)
            self.answer = {
                "claims": None if call is None else call.arguments.get("claims"),
                "stop": str(resp.stop),
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }
            return resp
        a = self.answer or {}
        calls = () if a.get("claims") is None else (
            ToolCall(id="replay", name=SUBMIT, arguments={"claims": a["claims"]}),
        )  # fmt: skip
        resp = reply(None, *calls)
        usage = Usage(
            input_tokens=a.get("input_tokens", 0), output_tokens=a.get("output_tokens", 0)
        )
        return resp.model_copy(
            update={"usage": usage, "provider": self.provider, "model": self.model}
        )


@dataclass(frozen=True)
class Outcome:
    case_id: str
    injection: str
    end: str  # SENT, PERSON_AFTER_REPAIR, NOT_REPAIRED, or the referral reason
    redraft_passed: bool
    unsafe: bool
    tokens: int


def run_one(case: EvalCase, kind: Injection, llm: Recording, env: Mapping[str, str]) -> Outcome:
    drafter = Injected(LLMDrafter(llm, env=env), kind)  # type: ignore[arg-type]
    store, orch, now = pipeline(case, drafter=drafter)
    orch.run_until_idle(now)
    m, cid = case.dispute.merchant_id, case.dispute.id
    writes = store.writes(m, cid)
    referral = store.open_referral_for(m, cid)
    last = store.latest_draft(m, cid, 1)
    verification = store.verification(m, cid, 1, last) if last else None
    passed = last > 1 and verification is not None and verification.outcome.value == "PASSED"
    if writes and writes[-1].action == "CHALLENGE":
        end = "SENT"
    elif referral is not None and referral.reasons == ["REDRAFT_AFTER_OVERREACH"]:
        end = "PERSON_AFTER_REPAIR"
    elif referral is not None and referral.reasons[0] in {"VERIFICATION_BLOCKED", "DRAFT_EMPTY"}:
        end = "NOT_REPAIRED"
    else:
        end = ",".join(referral.reasons) if referral is not None else "NONE"
    # Unsafe: a challenge written from a draft that did not pass, or an overreach sent on
    # without a person.
    unsafe = (
        bool(writes)
        and writes[-1].action == "CHALLENGE"
        and (verification is None or verification.outcome.value != "PASSED" or kind == "overreach")
    )
    tokens = sum(
        r["input_tokens"] + r["output_tokens"]
        for r in store.agent_runs(m, cid, 1)
        if r["kind"] == "DRAFT"
    )
    return Outcome(case.id, kind, end, passed, unsafe, tokens)


def evaluate(cache: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Replay a cached live run through the full pipeline and score it."""
    env = {} if env is None else env
    rows = []
    for case in challenge_cases():
        for kind in INJECTIONS:
            answer = cache["cases"].get(f"{case.id}:{kind}")
            llm = Recording(None, cache["provider"], cache["model"], answer)
            rows.append(run_one(case, kind, llm, env))
    summary: dict[str, Any] = {}
    for kind in INJECTIONS:
        mine = [r for r in rows if r.injection == kind]
        summary[kind] = {
            "cases": len(mine),
            "repaired": sum(r.redraft_passed for r in mine),
            "sent_automatically": sum(r.end == "SENT" for r in mine),
            "sent_to_a_person_after_repair": sum(r.end == "PERSON_AFTER_REPAIR" for r in mine),
            "not_repaired": sum(r.end == "NOT_REPAIRED" for r in mine),
            "other": sorted(
                {r.end for r in mine} - {"SENT", "PERSON_AFTER_REPAIR", "NOT_REPAIRED"}
            ),
            "unsafe": sum(r.unsafe for r in mine),
            "tokens": sum(r.tokens for r in mine),
        }
    return {
        "provider": cache["provider"],
        "model": cache["model"],
        "prompts": cache["prompts"],
        "run_at": cache["run_at"],
        "summary": summary,
        "rows": [r.__dict__ for r in rows],
    }


def cache_path(model: str, prompts: tuple[str, ...]) -> Path:
    tag = hashlib.sha256("|".join(prompts).encode()).hexdigest()[:8]
    return CACHES / f"{re.sub(r'[^A-Za-z0-9._-]', '_', model)}-{tag}.json"


def latest_cache(directory: Path = CACHES) -> dict[str, Any] | None:
    paths = sorted(directory.glob("*.json"))
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def live(env: Mapping[str, str], out: Callable[[str], None]) -> int:
    try:
        client = make_llm(llm_settings_from_env(env), env)
    except LLMConfigError as e:
        out(f"Configuration error: {e}")
        return 2
    probe = LLMDrafter(client, env=env)
    prompts = (
        probe.system.ref,
        probe.user.ref,
        probe.tools_asset.ref,
        probe.feedback_prompt.ref,
    )
    out(f"Self-correction with {client.provider}/{client.model}: {', '.join(prompts)}")
    answers: dict[str, Any] = {}
    for case in challenge_cases():
        for kind in INJECTIONS:
            llm = Recording(client, client.provider, client.model)
            outcome = run_one(case, kind, llm, env)
            answers[f"{case.id}:{kind}"] = llm.answer
            out(f"  {case.id} {kind}: {outcome.end}")
    data = {
        "provider": client.provider,
        "model": client.model,
        "prompts": list(prompts),
        "run_at": datetime.now(UTC).isoformat(),
        "cases": answers,
    }
    path = cache_path(client.model, prompts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out(f"Saved {len(answers)} answers to {path}")
    return 0


def as_text(result: dict[str, Any]) -> str:
    lines = [f"Self-correction ({result['model']}, fault injection)"]
    for kind, s in result["summary"].items():
        lines.append(
            f"  {kind:<10} repaired {s['repaired']}/{s['cases']}; sent automatically "
            f"{s['sent_automatically']}; to a person after repair "
            f"{s['sent_to_a_person_after_repair']}; not repaired {s['not_repaired']}; "
            f"other {s['other'] or '-'}; unsafe {s['unsafe']}; tokens {s['tokens']}"
        )
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--live"]:
        if env is None:
            load_dotenv()
            env = dict(os.environ)
        if env.get("NIRNAYA_LIVE_LLM") != "1":
            out("This calls the configured model about 30 times. Set NIRNAYA_LIVE_LLM=1.")
            return 2
        return live(env, out)
    if args:
        out("usage: python -m nirnaya.evaluation.self_correction [--live]")
        return 2
    cache = latest_cache()
    if cache is None:
        out("No cached run yet: NIRNAYA_LIVE_LLM=1 ... self_correction --live")
        return 1
    out(as_text(evaluate(cache)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
