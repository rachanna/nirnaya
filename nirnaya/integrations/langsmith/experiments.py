"""Upload the evaluation report to LangSmith: one dataset per case-set version, one experiment
per policy column.

The dataset holds each case's id, title, stage and amount, with the expert route as the
reference output. An experiment replays a policy's stored results from the report (no model
is called), scores each case (agrees with the expert, unsupported claims, expected cost) and
attaches the report's totals as summary scores, with the versions it came from as metadata.

eval/results/langsmith_experiments.json records what was uploaded; a policy whose results and
versions are unchanged is skipped, so re-running does not duplicate experiments.
"""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from nirnaya.assets import digest
from nirnaya.evaluation.llm_baseline import RESULTS

RECORD = RESULTS / "langsmith_experiments.json"


class SyncError(RuntimeError):
    """The upload cannot proceed safely."""


def dataset_name(report: dict[str, Any]) -> str:
    return f"nirnaya-eval-cases-{report['versions']['case_set']['version']}"


def ensure_dataset(client: Any, report: dict[str, Any]) -> str:  # noqa: ANN401
    cases = report["versions"]["case_set"]
    if not cases.get("version"):
        raise SyncError("the eval cases do not match eval/case_set.toml; bump the case set first")
    name = dataset_name(report)
    if client.has_dataset(dataset_name=name):
        meta = client.read_dataset(dataset_name=name).metadata or {}
        if meta.get("sha256") != cases["sha256"]:
            raise SyncError(f"LangSmith dataset {name} holds different cases than this case set")
        return name
    client.create_dataset(
        name,
        description=(
            "Nirnaya evaluation cases (SYNTHETIC) with the expert route for each, derived by "
            "hand from eval/EXPERT_POLICY.md."
        ),
        metadata={"case_set": cases["version"], "sha256": cases["sha256"], "count": cases["count"]},
    )
    client.create_examples(
        dataset_name=name,
        examples=[
            {
                "inputs": {
                    "case_id": r["id"],
                    "title": r["title"],
                    "stage": r["stage"],
                    "amount": r["amount"],
                },
                "reference_outputs": {"expected": r["expected"]},
                "metadata": {"case_id": r["id"]},
            }
            for r in report["rows"]
        ],
    )
    return name


# --- per-case scores ----------------------------------------------------------------------------


def agrees(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    expected, action = reference_outputs.get("expected"), outputs.get("action")
    ok = action == expected or (expected == "DEFER" and action == "NONE")
    return {"key": "agrees_with_expert", "score": int(ok)}


def unsupported_claims(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    return {"key": "unsupported_claims", "score": int(outputs.get("unsupported") or 0)}


def expected_cost(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    cost = outputs.get("cost")
    return {"key": "expected_cost_usd", "score": None if cost is None else float(Decimal(cost))}


def summary_for(policy: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """The report's totals for this policy, as summary scores (not recomputed)."""
    fin = policy["financial"]

    def summary(outputs: list[Any], reference_outputs: list[Any]) -> dict[str, Any]:
        return {
            "results": [
                {"key": "expected_net_loss_usd", "score": float(Decimal(fin["expected_loss"]))},
                {"key": "agreement", "score": fin["agreement"] / fin["cases"]},
                {
                    "key": "unsupported_claims_submitted",
                    "score": policy["integrity"]["unsupported_submitted"],
                },
                {"key": "policy_violations", "score": policy["compliance"]["violations"]},
                {
                    "key": "automated_share",
                    "score": float(Decimal(policy["coverage"]["automated_share"])),
                },
            ]
        }

    return summary


def _target(rows: dict[str, Any]) -> Callable[[dict[str, Any]], Any]:
    def target(inputs: dict[str, Any]) -> Any:  # noqa: ANN401
        return rows[inputs["case_id"]]

    return target


def upload(
    client: Any,  # noqa: ANN401 - langsmith.Client
    report: dict[str, Any],
    *,
    record: Path = RECORD,
    evaluate: Callable[..., Any] | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    if evaluate is None:
        from langsmith import evaluate
    data = ensure_dataset(client, report)
    done: dict[str, Any] = (
        json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
    )
    v = report["versions"]
    for key, policy in report["policies"].items():
        rows = {r["id"]: r[key] for r in report["rows"]}
        meta: dict[str, Any] = {
            "policy": policy["name"],
            "case_set": v["case_set"]["version"],
            "merchant_policy": v["policy"],
            "fx": v["fx"],
            "economics": v["economics"],
        }
        label = key.removeprefix("llm_only_") if key.startswith("llm_only_") else None
        llm = (report.get("llm_only") or {}).get(label) if label else None
        if llm:
            meta |= {
                "model": f"{llm['provider']}/{llm['model']}",
                "prompts": llm["prompts"],
                "answers_cached": llm["run_at"],
            }
        fingerprint = digest(
            json.dumps({"rows": rows, "policy": policy, "meta": meta}, sort_keys=True)
        )
        if done.get(key, {}).get("fingerprint") == fingerprint:
            out(f"{key}: unchanged since the last upload, skipped")
            continue
        evaluate(
            _target(rows),
            data=data,
            evaluators=[agrees, unsupported_claims, expected_cost],
            summary_evaluators=[summary_for(policy)],
            experiment_prefix=key,
            description=f"{policy['name']} on {data}: stored results from the eval report.",
            metadata={**meta, "git_commit": v["git"]["commit"]},
            client=client,
            max_concurrency=0,
        )
        done[key] = {"fingerprint": fingerprint, "dataset": data, "metadata": meta}
        record.write_text(json.dumps(done, indent=2), encoding="utf-8")
        out(f"{key}: uploaded to {data}")
    return done
