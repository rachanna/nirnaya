"""The evaluation report as the web demo's results view: every approach on the same SYNTHETIC
cases, in the report's own numbers (`python -m nirnaya.evaluation.report`). Nothing is
recomputed here; the report's caveat travels with it."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyResult:
    key: str
    name: str
    ours: bool
    expected_loss: str  # USD, lower is better
    vs_nirnaya: str  # USD more (negative: less) than Nirnaya's expected loss
    agreement: int  # cases where the action matches the expert's route
    unsupported: int  # submitted claims a record does not support
    no_evidence: int  # challenges sent with no evidence
    violations: int  # merchant-policy breaks
    automated: int
    referred: int
    model: str | None  # for an LLM-only baseline: which model, and when its answers were cached


@dataclass(frozen=True)
class Results:
    cases: int
    caveat: str
    policies: tuple[PolicyResult, ...]


def results_view(report: dict[str, Any]) -> Results:
    llm = report.get("llm_only") or {}

    def model(key: str) -> str | None:
        m = llm.get(key.removeprefix("llm_only_"))
        if not key.startswith("llm_only_") or m is None:
            return None
        return f"{m['provider']}/{m['model']}, answers cached {m['run_at'][:10]}"

    return Results(
        cases=report["cases"],
        caveat=report["caveat"],
        policies=tuple(
            PolicyResult(
                key=key,
                name=p["name"],
                ours=key == "nirnaya",
                expected_loss=p["financial"]["expected_loss"],
                vs_nirnaya=p["financial"]["vs_nirnaya"],
                agreement=p["financial"]["agreement"],
                unsupported=p["integrity"]["unsupported_submitted"],
                no_evidence=p["integrity"]["challenges_without_evidence"],
                violations=p["compliance"]["violations"],
                automated=p["coverage"]["automated"],
                referred=p["coverage"]["referred"],
                model=model(key),
            )
            for key, p in report["policies"].items()
        ),
    )
