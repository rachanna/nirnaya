"""The evaluation report: Nirnaya against the baselines on the five metrics (spec section 9).

    uv run python -m nirnaya.evaluation.report

Writes eval/results/report.json (read by the demo UI) and eval/results/report.md, and prints
the comparison. Includes each LLM-only variant whose cached answers exist
(nirnaya.evaluation.llm_baseline). Offline and deterministic otherwise. Every report states
the versions it was built from: git commit, case set, policy, FX, economics and, for each
LLM-only variant, the model and prompt versions.
"""

import json
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from nirnaya.config.demo import DEMO_POLICY, FX_ASSET, POLICY_ASSET
from nirnaya.evaluation import case_set
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.evaluation.economics import ECONOMICS_ASSET
from nirnaya.evaluation.llm_baseline import CACHES, RESULTS, VARIANTS, Cache, llm_only
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
}
REPO = Path(__file__).resolve().parents[2]


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def run_policies(
    cases: list[EvalCase], caches: dict[str, Cache]
) -> dict[str, dict[str, CaseResult]]:
    world = synthetic_world(cases)
    results = {
        "nirnaya": {c.id: nirnaya(c, world) for c in cases},
        "fight_all": {c.id: fight_all(c) for c in cases},
        "accept_all": {c.id: accept_all(c) for c in cases},
    }
    for variant, cache in caches.items():
        results[f"llm_only_{variant}"] = {c.id: llm_only(c, cache) for c in cases}
    return results


def _git() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10, check=True
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip()

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def versions(cases_dir: Path | None = None) -> dict[str, Any]:
    """What this report was built from, so any result can be traced and reproduced."""
    try:
        cases = case_set.check() if cases_dir is None else case_set.check(cases_dir)
    except case_set.CaseSetChanged as e:
        cases = {"version": None, "error": str(e)}
    return {
        "git": _git(),
        "case_set": cases,
        "policy": POLICY_ASSET.ref,
        "fx": FX_ASSET.ref,
        "economics": ECONOMICS_ASSET.ref,
    }


def build(
    cases: list[EvalCase] | None = None,
    *,
    caches: dict[str, Cache] | None = None,
    repeats: dict[str, list[Cache]] | None = None,
    policy: MerchantPolicy = DEMO_POLICY,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases()
    caches = caches or {}
    results = run_policies(cases, caches)
    policies: dict[str, Any] = {}
    for key, res in results.items():
        fighting = key == "fight_all"
        fin = financial(cases, res, policy, keep_fighting=fighting)
        integ = integrity(cases, res)
        comp = compliance(cases, res)
        cov = coverage(cases, res)
        life = lifecycle(cases, res) if key == "nirnaya" else None
        policies[key] = {
            "name": NAMES.get(key)
            or f"LLM only ({key.removeprefix('llm_only_').replace('-', ' ')})",
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
        "llm_only": {
            variant: {
                "provider": c.provider,
                "model": c.model,
                "run_at": c.run_at,
                "prompts": list(c.prompts),
            }
            for variant, c in caches.items()
        }
        or None,
        "versions": versions(),
        "variance": variance(cases, repeats or {}, policy),
        "policies": policies,
        "rows": rows,
    }


def as_text(report: dict[str, Any]) -> str:
    keys = list(report["policies"])
    width = max(18, *(len(report["policies"][k]["name"]) + 2 for k in keys))
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
        out.append("")
        for variant, m in report["llm_only"].items():
            out.append(
                f"LLM only ({variant}): {m['provider']}/{m['model']}, answers cached "
                f"{m['run_at']}; prompts {', '.join(m['prompts']) or 'not recorded'}"
            )
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
    for label, var in (report.get("variance") or {}).items():
        lines += [
            "",
            f"## Repeat runs: LLM only ({label.replace('-', ' ')}), same model and prompts",
            "",
            "| Run | Expected net loss (USD) | Agrees with expert | Unsupported claims "
            "| Policy violations |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {r['run']} | {r['expected_loss']} | {r['agreement']} | {r['unsupported']} "
            f"| {r['violations']} |"
            for r in var["per_run"]
        ]
        changed = ", ".join(f"{c['case']} ({' / '.join(c['actions'])})" for c in var["changed"])
        lines += [
            "",
            f"Cases whose answer changed between runs: {changed or 'none'}. "
            "Nirnaya gives the same result on every run.",
        ]
    v = report["versions"]
    lines += [
        "",
        "## Versions",
        "",
        f"- Code: git `{v['git']['commit']}`"
        + (" (uncommitted changes)" if v["git"]["dirty"] else ""),
        f"- Case set: {v['case_set'].get('version')} ({v['case_set'].get('count')} cases, "
        f"sha256 `{str(v['case_set'].get('sha256'))[:12]}`)",
        f"- Policy: `{v['policy']}`; FX: `{v['fx']}`; economics: `{v['economics']}`",
    ]
    for variant, m in (report["llm_only"] or {}).items():
        prompts = ", ".join(f"`{x}`" for x in m["prompts"]) or "not recorded"
        lines.append(
            f"- LLM only ({variant}): {m['provider']}/{m['model']}, {m['run_at'][:10]}; {prompts}"
        )
    return "\n".join(lines) + "\n"


def load_caches(directory: Path = CACHES) -> dict[str, Cache]:
    """Every cached LLM-only run (<variant>-<version>.json), in variant order, then version.
    Subfolders (an archive of other models' runs) are not loaded."""
    order = {v: i for i, v in enumerate(VARIANTS)}

    def key(path: Path) -> tuple[int, str]:
        variant = path.stem.split("-", 1)[0]
        return order.get(variant, len(order)), path.stem

    caches = {}
    for path in sorted((p for p in directory.glob("*.json") if ".run" not in p.stem), key=key):
        cache = Cache.load(path)
        if cache is not None:
            caches[path.stem] = cache
    return caches


def load_repeats(directory: Path = CACHES) -> dict[str, list[Cache]]:
    """label -> [run 1, run 2, ...] for every label run more than once."""
    runs: dict[str, dict[int, Cache]] = {}
    for path in directory.glob("*.json"):
        label, _, n = path.stem.partition(".run")
        cache = Cache.load(path)
        if cache is not None and (not n or n.isdigit()):
            runs.setdefault(label, {})[int(n or 1)] = cache
    return {
        label: [by_run[n] for n in sorted(by_run)]
        for label, by_run in sorted(runs.items())
        if len(by_run) > 1
    }


def variance(
    cases: list[EvalCase],
    repeats: dict[str, list[Cache]],
    policy: MerchantPolicy = DEMO_POLICY,
) -> dict[str, Any]:
    """The same model and prompts, run again: each run's numbers, and every case whose answer
    changed between runs. Nirnaya's own results are the same on every run by construction."""
    out: dict[str, Any] = {}
    for label, runs in repeats.items():
        results = [{c.id: llm_only(c, cache) for c in cases} for cache in runs]
        per_run = []
        for n, res in enumerate(results, start=1):
            fin = financial(cases, res, policy)
            per_run.append(
                {
                    "run": n,
                    "run_at": runs[n - 1].run_at,
                    "expected_loss": _money(fin.expected_loss),
                    "agreement": f"{fin.agreement}/{fin.decided}",
                    "unsupported": integrity(cases, res).unsupported_submitted,
                    "violations": compliance(cases, res).violations,
                }
            )
        changed = []
        for c in cases:
            actions = [str(res[c.id].action) for res in results]
            if len(set(actions)) > 1:
                changed.append({"case": c.id, "actions": actions})
        out[label] = {"runs": len(runs), "per_run": per_run, "changed": changed}
    return out


def main(out: Callable[[str], None] = print, results: Path = RESULTS) -> int:
    directory = results / CACHES.relative_to(RESULTS)
    report = build(caches=load_caches(directory), repeats=load_repeats(directory))
    results.mkdir(parents=True, exist_ok=True)
    (results / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (results / "report.md").write_text(as_markdown(report), encoding="utf-8")
    out(as_text(report))
    out(f"\nWrote {results / 'report.json'} and {results / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
