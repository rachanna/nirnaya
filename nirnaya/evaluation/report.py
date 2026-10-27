"""The evaluation report: Nirnaya against the baselines on the five metrics (spec section 9).

    uv run python -m nirnaya.evaluation.report

Writes eval/results/report.json (read by the demo UI) and eval/results/report.md, and prints
the comparison. Includes the LLM-only baseline when its cached answers exist
(nirnaya.evaluation.llm_baseline). Offline and deterministic otherwise.
"""

import json
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.evaluation.llm_baseline import CACHE, RESULTS, Cache, llm_only
from nirnaya.evaluation.metrics import (
    case_cost,
    compliance,
    coverage,
    financial,
    integrity,
    lifecycle,
    priced,
)
from nirnaya.evaluation.policies import CaseResult, accept_all, fight_all, nirnaya
from nirnaya.models.policy import MerchantPolicy

CAVEAT = (
    "Expected values under the expert policy's assumed priors and fees on SYNTHETIC cases "
    "(eval/EXPERT_POLICY.md); not real issuer or network outcomes."
)
NAMES = {
    "nirnaya": "Nirnaya",
    "fight_all": "Fight everything",
    "accept_all": "Accept everything",
    "llm_only": "LLM only",
}


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def run_policies(cases: list[EvalCase], cache: Cache | None) -> dict[str, dict[str, CaseResult]]:
    world = synthetic_world(cases)
    results = {
        "nirnaya": {c.id: nirnaya(c, world) for c in cases},
        "fight_all": {c.id: fight_all(c) for c in cases},
        "accept_all": {c.id: accept_all(c) for c in cases},
    }
    if cache is not None:
        results["llm_only"] = {c.id: llm_only(c, cache) for c in cases}
    return results


def build(
    cases: list[EvalCase] | None = None,
    *,
    cache: Cache | None = None,
    policy: MerchantPolicy = DEMO_POLICY,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases()
    results = run_policies(cases, cache)
    policies: dict[str, Any] = {}
    for key, res in results.items():
        fighting = key == "fight_all"
        fin = financial(cases, res, policy, keep_fighting=fighting)
        integ = integrity(cases, res)
        comp = compliance(cases, res)
        cov = coverage(cases, res)
        life = lifecycle(cases, res) if key == "nirnaya" else None
        policies[key] = {
            "name": NAMES[key],
            "financial": {
                "expected_loss": _money(fin.expected_loss),
                "by_part": {k: _money(v) for k, v in fin.by_part.items()},
                "priced_cases": fin.priced_cases,
                "agreement": fin.agreement,
                "cases": fin.decided,
            },
            "integrity": {
                "challenges": integ.challenges,
                "claims_submitted": integ.claims_submitted,
                "unsupported_submitted": integ.unsupported_submitted,
                "challenges_without_evidence": integ.challenges_without_evidence,
                "fabrications_caught": integ.fabrications_caught,
            },
            "compliance": {
                "violations": comp.violations,
                "by_kind": comp.by_kind,
                "cases": list(comp.cases),
            },
            "lifecycle": None
            if life is None
            else {"writes": life.writes, "ok": life.ok, "problems": list(life.problems)},
            "coverage": {
                "actionable": cov.actionable,
                "automated": cov.automated,
                "referred": cov.referred,
                "automated_share": str(cov.automated_share.quantize(Decimal("0.001"))),
            },
        }
    base = Decimal(policies["nirnaya"]["financial"]["expected_loss"])
    for p in policies.values():
        p["financial"]["vs_nirnaya"] = _money(Decimal(p["financial"]["expected_loss"]) - base)

    rows = []
    for case in cases:
        row: dict[str, Any] = {
            "id": case.id,
            "title": case.title,
            "stage": str(case.dispute.stage),
            "amount": str(case.dispute.amount),
            "expected": str(case.expected.route),
            "priced": priced(case),
        }
        for key, res in results.items():
            cost = case_cost(case, res[case.id], policy, keep_fighting=key == "fight_all")
            row[key] = {
                "action": str(res[case.id].action),
                "cost": None if cost is None else _money(cost.total),
                "unsupported": res[case.id].unsupported_submitted,
            }
        rows.append(row)

    return {
        "caveat": CAVEAT,
        "cases": len(cases),
        "llm_only": None
        if cache is None
        else {"provider": cache.provider, "model": cache.model, "run_at": cache.run_at},
        "policies": policies,
        "rows": rows,
    }


def as_text(report: dict[str, Any]) -> str:
    keys = list(report["policies"])
    width = 18
    head = f"{'':34}" + "".join(f"{report['policies'][k]['name']:>{width}}" for k in keys)

    def line(label: str, get: Callable[[dict[str, Any]], Any]) -> str:
        return f"{label:34}" + "".join(f"{get(report['policies'][k])!s:>{width}}" for k in keys)

    out = [
        f"Nirnaya evaluation: {report['cases']} SYNTHETIC cases",
        report["caveat"],
        "",
        head,
        "1. Financial decision quality",
        line("   expected net loss (USD)", lambda p: p["financial"]["expected_loss"]),
        line("   vs Nirnaya (USD)", lambda p: p["financial"]["vs_nirnaya"]),
        line(
            "   agrees with expert route",
            lambda p: f"{p['financial']['agreement']}/{p['financial']['cases']}",
        ),
        "2. Evidence integrity",
        line("   unsupported claims submitted", lambda p: p["integrity"]["unsupported_submitted"]),
        line(
            "   challenges with no evidence",
            lambda p: p["integrity"]["challenges_without_evidence"],
        ),
        line("   fabrications caught", lambda p: p["integrity"]["fabrications_caught"]),
        "3. Merchant-policy compliance",
        line("   violations", lambda p: p["compliance"]["violations"]),
        "4. API lifecycle correctness",
        line(
            "   problems in the ledger",
            lambda p: "n/a" if p["lifecycle"] is None else len(p["lifecycle"]["problems"]),
        ),
        "5. Safe automation coverage",
        line(
            "   automated / referred",
            lambda p: f"{p['coverage']['automated']}/{p['coverage']['referred']}",
        ),
    ]
    if report["llm_only"] is None:
        out += [
            "",
            "LLM only: not run (NIRNAYA_LIVE_LLM=1 python -m nirnaya.evaluation.llm_baseline)",
        ]
    else:
        m = report["llm_only"]
        out += ["", f"LLM only: {m['provider']}/{m['model']}, answers cached {m['run_at']}"]
    return "\n".join(out)


def as_markdown(report: dict[str, Any]) -> str:
    keys = list(report["policies"])
    names = [report["policies"][k]["name"] for k in keys]
    lines = [
        "# Nirnaya evaluation",
        "",
        f"{report['cases']} SYNTHETIC cases. {report['caveat']}",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(keys),
    ]

    def row(label: str, get: Callable[[dict[str, Any]], Any]) -> None:
        lines.append(
            f"| {label} | " + " | ".join(str(get(report["policies"][k])) for k in keys) + " |"
        )

    row("Expected net loss (USD)", lambda p: p["financial"]["expected_loss"])
    row("vs Nirnaya (USD)", lambda p: p["financial"]["vs_nirnaya"])
    row(
        "Agrees with expert route",
        lambda p: f"{p['financial']['agreement']}/{p['financial']['cases']}",
    )
    row("Unsupported claims submitted", lambda p: p["integrity"]["unsupported_submitted"])
    row("Challenges with no evidence", lambda p: p["integrity"]["challenges_without_evidence"])
    row("Fabrications caught", lambda p: p["integrity"]["fabrications_caught"])
    row("Policy violations", lambda p: p["compliance"]["violations"])
    row(
        "Lifecycle problems",
        lambda p: "n/a" if p["lifecycle"] is None else len(p["lifecycle"]["problems"]),
    )
    row(
        "Automated / referred",
        lambda p: f"{p['coverage']['automated']}/{p['coverage']['referred']}",
    )
    lines += [
        "",
        "## Per case",
        "",
        "| Case | Stage | Amount | Expert | " + " | ".join(names) + " |",
    ]
    lines.append("|---|---|---|---|" + "---|" * len(keys))
    for r in report["rows"]:
        cells = [
            f"{r[k]['action']}" + (f" ({r[k]['cost']})" if r[k]["cost"] is not None else "")
            for k in keys
        ]
        lines.append(
            f"| {r['id']} | {r['stage']} | {r['amount']} | {r['expected']} | "
            + " | ".join(cells)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main(out: Callable[[str], None] = print, results: Path = RESULTS) -> int:
    report = build(cache=Cache.load(results / CACHE.name))
    results.mkdir(parents=True, exist_ok=True)
    (results / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (results / "report.md").write_text(as_markdown(report), encoding="utf-8")
    out(as_text(report))
    out(f"\nWrote {results / 'report.json'} and {results / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
