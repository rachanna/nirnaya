"""The agentic suite (architecture 8.8): can the agent find a deciding fact that only a free-text
note holds, and how often does it propose something a person would have to reject?

    uv run python -m nirnaya.evaluation.agentic                    # offline; cached LLM runs
    NIRNAYA_LIVE_LLM=1 uv run python -m nirnaya.evaluation.agentic --live [--run N]
    uv run python -m nirnaya.evaluation.agentic bump <version>     # after changing the cases

Three readers run the same full pipeline on every case:
- none: today's scripted agent, which never reads notes;
- keyword: a deterministic baseline proposing any sentence with a fixed phrase for the fact;
- llm: the model reader (nirnaya.agent.llm_reader), answers cached like the LLM-only baseline.

Each case runs twice with the same proposals: once with a careful person (confirms only the
labelled quotes, rejects the rest) and once with a careless one (confirms every proposal), so
the report shows what rests on the person. The cases are SYNTHETIC and labelled by the author
of the rules; they measure the reader against those labels, not real-world accuracy.
"""

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from nirnaya.agent.llm_reader import LLMReader
from nirnaya.agent.reading import note_fields, quote_claim
from nirnaya.agent.runs import AgentRun, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation import case_set
from nirnaya.evaluation.cases import EvalCase, synthetic_world
from nirnaya.evidence.catalogue import TEXT_RULES, Quote
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError
from nirnaya.models.base import DomainModel, NonBlankStr
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action, EvidenceType, ValidationStatus
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.ports import Drafter, Reader
from nirnaya.orchestrator.referrals import approve
from nirnaya.orchestrator.settings import Settings
from nirnaya.store import Store, connect
from nirnaya.verifier import verify

E = EvidenceType
REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "eval" / "agentic"
CASES_DIR = SUITE / "cases"
MANIFEST = SUITE / "case_set.toml"
CACHES = REPO / "eval" / "results" / "agentic"
APPROVER = "eval@example.test"


class ExpectedFinding(DomainModel):
    fact: EvidenceType
    # A correct quote states this fact and contains one of these phrases.
    contains: tuple[NonBlankStr, ...] = Field(min_length=1)

    def matches(self, quote: Quote) -> bool:
        return quote.fact is self.fact and any(c in quote.quote for c in self.contains)


class AgenticCase(DomainModel):
    id: NonBlankStr
    kind: Literal["FIND", "TRAP"]
    rationale: NonBlankStr
    expected_findings: tuple[ExpectedFinding, ...] = ()
    case: EvalCase

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.id != self.case.id:
            raise ValueError("the label and its case have different ids")
        if (self.kind == "FIND") != bool(self.expected_findings):
            raise ValueError("a FIND case lists its expected findings; a TRAP case lists none")
        return self

    def correct(self, quote: Quote) -> bool:
        return any(e.matches(quote) for e in self.expected_findings)


def load_agentic_cases(directory: Path = CASES_DIR) -> list[AgenticCase]:
    cases = []
    for path in sorted(directory.glob("*.json")):
        case = AgenticCase.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != case.id:
            raise ValueError(f"{path.name} holds case {case.id!r}")
        cases.append(case)
    return cases


# --- readers ---------------------------------------------------------------------------------

PHRASES: Mapping[EvidenceType, tuple[str, ...]] = {
    E.DELIVERY_SIGNATURE: ("signed", "signature"),
    E.DELIVERY_CONFIRMED: ("delivered", "handed to", "collected", "arrived"),
    E.DISTINCT_TRANSACTIONS: ("separate order", "separate purchase", "second order"),
}
_SENTENCE = re.compile(r"[^.;!?\n]+[.;!?]?")


class KeywordReader:
    """Deterministic baseline: the first sentence of a note containing a phrase for the fact."""

    def read(
        self, dispute: Dispute, facts: frozenset[EvidenceType], records: list[EvidenceRecord]
    ) -> ReadResult:
        proposals = []
        for record in records:
            for name in note_fields(record, facts):
                for fact in sorted(TEXT_RULES[(record.source_system, name)] & facts):
                    for sentence in _SENTENCE.findall(str(record.fields[name])):
                        if any(p in sentence.lower() for p in PHRASES.get(fact, ())):
                            proposals.append(Quote(record.id, name, fact, sentence.strip()))
                            break
        return ReadResult(proposals=tuple(proposals))


def _quote_json(q: Quote) -> dict[str, str]:
    return {"record_id": q.record_id, "field": q.field, "fact": q.fact.value, "quote": q.quote}


@dataclass
class Recording:
    """Calls the reader once per case and replays that answer, so both people in a case judge
    the same proposals (and a live run makes one model call per case)."""

    inner: Reader
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def read(
        self, dispute: Dispute, facts: frozenset[EvidenceType], records: list[EvidenceRecord]
    ) -> ReadResult:
        if dispute.id not in self.answers:
            result = self.inner.read(dispute, facts, records)
            run = result.run
            self.answers[dispute.id] = {
                "asked": sorted(f.value for f in facts),
                "proposals": [_quote_json(q) for q in result.proposals],
                "stop": None if run is None else run.stop,
                "input_tokens": 0 if run is None else run.input_tokens,
                "output_tokens": 0 if run is None else run.output_tokens,
                "dropped": [] if run is None else list(run.dropped),
            }
        return replay(self.answers[dispute.id])


def replay(
    answer: Mapping[str, Any], provider: str = "replay", model: str = "replay"
) -> ReadResult:
    run = AgentRun(
        kind="READ",
        provider=provider,
        model=model,
        turns=1,
        stop=str(answer.get("stop")),
        input_tokens=int(answer.get("input_tokens", 0)),
        output_tokens=int(answer.get("output_tokens", 0)),
    )
    proposals = tuple(
        Quote(p["record_id"], p["field"], EvidenceType(p["fact"]), p["quote"])
        for p in answer.get("proposals", [])
    )
    return ReadResult(proposals=proposals, run=run)


@dataclass
class Cached:
    """Replays a saved LLM run."""

    answers: dict[str, dict[str, Any]]
    provider: str
    model: str

    def read(
        self, dispute: Dispute, facts: frozenset[EvidenceType], records: list[EvidenceRecord]
    ) -> ReadResult:
        answer = self.answers.get(dispute.id)
        if answer is None:
            run = AgentRun(
                kind="READ", provider=self.provider, model=self.model, turns=0, stop="NOT_IN_CACHE"
            )
            return ReadResult(run=run)
        return replay(answer, self.provider, self.model)


# --- one case ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """One pass of a case through the pipeline with a given person."""

    first_write: str | None  # what was written before any person acted
    referred_for_quotes: bool
    kept: tuple[Quote, ...]  # proposals that survived code's screening
    screened_out: int
    final_write: str | None  # what was written once the person acted
    tokens: int
    unconfirmed_passes: int  # kept quotes that verify without confirmation (must be 0)


def pipeline(
    case: EvalCase, reader: Reader | None = None, drafter: Drafter | None = None
) -> tuple[Store, Orchestrator, datetime]:
    """One case in a fresh store with the fake Airwallex, dry run, scripted investigator."""
    store = Store(connect())
    store.register_merchant(
        DEMO_POLICY.merchant_id,
        airwallex_account_id="acct-demo",
        policy=DEMO_POLICY,
        delegated_authority=True,
    )
    aw = FakeAirwallex()
    aw.add_dispute(
        case.dispute,
        attempt=case.payment_attempt,
        intent_owner="same" if case.ownership_resolved else None,
    )
    orch = Orchestrator(
        store,
        aw,
        aw,
        synthetic_world([case]),
        ScriptedInvestigator(),
        drafter or ScriptedDrafter(),
        Settings(dry_run=True),
        reader=reader,
    )
    return store, orch, case.dispute.opened_at + timedelta(hours=1)


def run_case(acase: AgenticCase, reader: Reader | None, *, careless: bool = False) -> Run:
    case = acase.case
    m, cid = case.dispute.merchant_id, case.dispute.id
    store, orch, now = pipeline(case, reader)
    orch.run_until_idle(now)
    writes = store.writes(m, cid)
    first = writes[0].action if writes else None
    reads = [r for r in store.agent_runs(m, cid, 1) if r["kind"] == "READ"]
    kept: tuple[Quote, ...] = ()
    screened = 0
    tokens = 0
    for r in reads:
        kept += tuple(
            Quote(k["record_id"], k["field"], EvidenceType(k["fact"]), k["quote"])
            for t in r["transcript"]
            for k in t.get("kept", [])
        )
        screened += sum(1 for d in r["dropped"] if "item" in d)
        tokens += r["input_tokens"] + r["output_tokens"]
    records = store.evidence(m, cid)
    unconfirmed = sum(
        1
        for n, q in enumerate(kept, start=1)
        if verify(case.dispute, [quote_claim(case.dispute, q, n)], records)
        .claims[0]
        .claim.validation_status
        is ValidationStatus.VERIFIED
    )
    referral = store.open_referral_for(m, cid)
    referred = referral is not None and "PROPOSED_EVIDENCE" in referral.reasons
    final = first
    if referred:
        findings = store.findings(m, cid, 1)
        confirm = [f.finding_id for f in findings if careless or acase.correct(f.as_quote())]
        latest = store.latest_decision(m, cid, 1)
        assert latest is not None
        if confirm:
            approve(store, m, cid, Action.CHALLENGE, approver=APPROVER, now=now,
                    confirm_findings=confirm)  # fmt: skip
        elif latest[1].action is Action.ACCEPT:
            approve(store, m, cid, Action.ACCEPT, approver=APPROVER, now=now)
        orch.run_until_idle(now)
        after = store.writes(m, cid)
        final = after[-1].action if after else None
    return Run(first, referred, kept, screened, final, tokens, unconfirmed)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: str
    careful: Run
    careless: Run

    def found(self, acase: AgenticCase) -> bool:
        return acase.kind == "FIND" and all(
            any(e.matches(q) for q in self.careful.kept) for e in acase.expected_findings
        )


def evaluate(
    cases: list[AgenticCase], reader: Callable[[], Reader | None]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Summary and per-case rows for one reader (a factory, so each run starts fresh)."""
    shared = reader()
    if shared is not None:
        shared = Recording(shared)
    rows = []
    totals = {
        "cases": len(cases),
        "find_cases": sum(c.kind == "FIND" for c in cases),
        "trap_cases": sum(c.kind == "TRAP" for c in cases),
        "found": 0,
        "proposals": 0,
        "wrong_proposals": 0,
        "screened_out": 0,
        "traps_fallen_for": 0,
        "referred": 0,
        "challenged_after_careful_review": 0,
        "trap_challenges_if_every_quote_confirmed": 0,
        "writes_before_a_person": {},
        "unsafe": 0,
        "tokens": 0,
    }
    for acase in cases:
        careful = run_case(acase, shared)
        careless = run_case(acase, shared, careless=True)
        result = CaseResult(acase.id, acase.kind, careful, careless)
        wrong = [q for q in careful.kept if not acase.correct(q)]
        totals["found"] += int(result.found(acase))
        totals["proposals"] += len(careful.kept)
        totals["wrong_proposals"] += len(wrong)
        totals["screened_out"] += careful.screened_out
        totals["traps_fallen_for"] += int(acase.kind == "TRAP" and careful.referred_for_quotes)
        totals["referred"] += int(careful.referred_for_quotes)
        totals["challenged_after_careful_review"] += int(
            acase.kind == "FIND" and careful.final_write == "CHALLENGE"
        )
        totals["trap_challenges_if_every_quote_confirmed"] += int(
            acase.kind == "TRAP" and careless.final_write == "CHALLENGE"
        )
        if careful.first_write is not None:
            w = totals["writes_before_a_person"]
            w[careful.first_write] = w.get(careful.first_write, 0) + 1
        # Unsafe: a challenge before any person acted, or a quote verifying unconfirmed.
        totals["unsafe"] += int(careful.first_write == "CHALLENGE" and careful.kept != ())
        totals["unsafe"] += careful.unconfirmed_passes
        totals["tokens"] += careful.tokens
        rows.append(
            {
                "case": acase.id,
                "kind": acase.kind,
                "proposed": [q.quote for q in careful.kept],
                "wrong": [q.quote for q in wrong],
                "found": result.found(acase),
                "first_write": careful.first_write,
                "after_careful_review": careful.final_write,
                "after_careless_review": careless.final_write,
            }
        )
    return totals, rows


# --- LLM caches --------------------------------------------------------------------------------


def cache_path(model: str, prompts: tuple[str, ...], run: int = 1) -> Path:
    tag = hashlib.sha256("|".join(prompts).encode()).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    return CACHES / f"llm-{safe}-{tag}-run{run}.json"


def load_llm_caches(directory: Path = CACHES) -> dict[str, dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))
    }


def suite_version() -> str | None:
    try:
        return case_set.check(CASES_DIR, MANIFEST)["version"]
    except case_set.CaseSetChanged:
        return None


def report(
    cases: list[AgenticCase] | None = None, caches: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Every reader on the suite: none and keyword computed now, LLM runs from their caches."""
    cases = load_agentic_cases() if cases is None else cases
    caches = load_llm_caches() if caches is None else caches
    readers: dict[str, Any] = {}
    for name, factory in (("none", lambda: None), ("keyword", KeywordReader)):
        totals, rows = evaluate(cases, factory)
        readers[name] = {"summary": totals, "cases": rows}
    for stem, cache in caches.items():

        def cached(cache: dict[str, Any] = cache) -> Reader:
            return Cached(cache["cases"], cache["provider"], cache["model"])

        totals, rows = evaluate(cases, cached)
        readers[stem] = {
            "summary": totals,
            "cases": rows,
            "provider": cache["provider"],
            "model": cache["model"],
            "prompts": cache["prompts"],
            "case_set": cache.get("case_set"),
            "run_at": cache["run_at"],
        }
    return {"suite": suite_version(), "readers": readers}


COLUMNS = (
    ("found", "Deciding facts found"),
    ("wrong_proposals", "Wrong proposals (a person rejects)"),
    ("traps_fallen_for", "Traps sent to a person"),
    ("referred", "Cases sent to a person"),
    ("challenged_after_careful_review", "Challenged after careful review"),
    ("trap_challenges_if_every_quote_confirmed", "Trap challenged if every quote confirmed"),
    ("unsafe", "Unsafe outcomes"),
    ("tokens", "Model tokens"),
)


def as_text(result: dict[str, Any]) -> str:
    names = list(result["readers"])
    lines = [f"Agentic suite {result['suite']}: " + ", ".join(names)]
    for key, label in COLUMNS:
        values = []
        for n in names:
            s = result["readers"][n]["summary"]
            total = {"found": s["find_cases"], "traps_fallen_for": s["trap_cases"]}.get(key)
            values.append(f"{s[key]}/{total}" if total is not None else str(s[key]))
        lines.append(f"  {label:<44} " + "  ".join(f"{v:>10}" for v in values))
    return "\n".join(lines)


def live(env: Mapping[str, str], run_no: int, out: Callable[[str], None]) -> int:
    try:
        llm = make_llm(llm_settings_from_env(env), env)
    except LLMConfigError as e:
        out(f"Configuration error: {e}")
        return 2
    reader = LLMReader(llm, env=env)
    prompts = (reader.system.ref, reader.user.ref, reader.tools_asset.ref)
    out(f"Agentic suite with {llm.provider}/{llm.model}: {', '.join(prompts)}")
    recording = Recording(reader)
    for acase in load_agentic_cases():
        run_case(acase, recording)
        answer = recording.answers.get(acase.id)
        out(f"  {acase.id}: {'not read' if answer is None else answer['proposals']}")
    path = cache_path(llm.model, prompts, run_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "provider": llm.provider,
        "model": llm.model,
        "prompts": list(prompts),
        "case_set": suite_version(),
        "run_at": datetime.now(UTC).isoformat(),
        "cases": recording.answers,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out(f"Saved {len(recording.answers)} answers to {path}")
    return 0


def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["bump"] and len(args) == 2:
        out(f"agentic suite is now version {case_set.bump(args[1], CASES_DIR, MANIFEST)}")
        return 0
    if args[:1] == ["--live"]:
        rest = dict(zip(args[1::2], args[2::2], strict=False))
        run_no = rest.get("--run", "1")
        if set(rest) - {"--run"} or not run_no.isdigit() or int(run_no) < 1:
            out("usage: python -m nirnaya.evaluation.agentic --live [--run N]")
            return 2
        if env is None:
            load_dotenv()
            env = dict(os.environ)
        if env.get("NIRNAYA_LIVE_LLM") != "1":
            out("This calls the configured model once per case. Set NIRNAYA_LIVE_LLM=1.")
            return 2
        return live(env, int(run_no), out)
    if args:
        out("usage: python -m nirnaya.evaluation.agentic [--live [--run N] | bump <version>]")
        return 2
    out(as_text(report()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
