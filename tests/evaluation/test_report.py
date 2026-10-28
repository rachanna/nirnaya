"""The report: built from the metric functions, with and without the LLM-only cache."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import load_cases
from nirnaya.evaluation.llm_baseline import Cache
from nirnaya.evaluation.metrics import financial, integrity
from nirnaya.evaluation.policies import fight_all, nirnaya
from nirnaya.evaluation.report import as_markdown, as_text, build, main

CASES = load_cases()


@pytest.fixture(scope="module")
def report():
    return build(CASES)


def test_the_report_numbers_are_the_metric_functions(report):
    n = {c.id: nirnaya(c) for c in CASES}
    f = {c.id: fight_all(c) for c in CASES}
    fin = financial(CASES, n, DEMO_POLICY)
    assert report["policies"]["nirnaya"]["financial"]["expected_loss"] == str(
        fin.expected_loss.quantize(fin.expected_loss.__class__("0.01"))
    )
    assert (
        report["policies"]["fight_all"]["integrity"]["unsupported_submitted"]
        == integrity(CASES, f).unsupported_submitted
    )
    assert report["policies"]["nirnaya"]["financial"]["vs_nirnaya"] == "0.00"


def test_without_the_cache_llm_only_is_not_run(report):
    assert set(report["policies"]) == {"nirnaya", "fight_all", "accept_all"}
    assert report["llm_only"] is None
    assert "LLM only: not run" in as_text(report)


def test_each_cached_llm_only_variant_is_its_own_column():
    at = datetime(2026, 10, 27, tzinfo=UTC).isoformat()
    naive = Cache("fake", "fake-model", at, {"N1": {"action": "CHALLENGE", "claims": []}})
    informed = Cache("fake", "fake-model", at, {"N1": {"action": "ACCEPT"}}, "informed",
                     ("baseline_informed_system@v1#abc",))  # fmt: skip
    report = build([c for c in CASES if c.id in {"N1", "K2"}],
                   caches={"naive-v1": naive, "informed-v1": informed})  # fmt: skip
    pol = report["policies"]
    assert pol["llm_only_naive-v1"]["integrity"]["challenges_without_evidence"] == 1
    assert pol["llm_only_informed-v1"]["name"] == "LLM only (informed v1)"
    assert report["llm_only"]["informed-v1"]["prompts"] == ["baseline_informed_system@v1#abc"]
    n1 = next(r for r in report["rows"] if r["id"] == "N1")
    assert n1["llm_only_informed-v1"]["action"] == "ACCEPT"


def test_the_report_loads_every_cached_version_in_variant_order(tmp_path):
    import shutil

    from nirnaya.evaluation.report import load_caches

    src = Path(__file__).resolve().parents[2] / "eval" / "results" / "llm_only"
    for name in ("naive-v1.json", "informed-v1.json"):
        shutil.copy(src / name, tmp_path / name)
    shutil.copy(src / "informed-v1.json", tmp_path / "informed-v2.json")
    (tmp_path / "archive").mkdir()
    shutil.copy(src / "naive-v1.json", tmp_path / "archive" / "naive-v1.json")  # not loaded
    assert list(load_caches(tmp_path)) == ["naive-v1", "informed-v1", "informed-v2"]


def test_the_report_states_the_versions_it_was_built_from(report):
    v = report["versions"]
    assert v["case_set"]["version"] == "v1" and v["case_set"]["count"] == 42
    assert v["policy"].startswith("merchant-demo@demo-2026-10#")
    assert v["fx"].startswith("fx@") and v["economics"].startswith("economics@v1#")
    assert set(v["git"]) == {"commit", "dirty"}
    assert "## Versions" in as_markdown(report)


def test_the_report_states_its_caveat_and_every_case(report):
    assert "SYNTHETIC" in report["caveat"] and "not real" in report["caveat"]
    assert len(report["rows"]) == len(CASES)
    md = as_markdown(report)
    assert "| Expected net loss (USD) |" in md and "| K1 |" in md


def test_main_writes_json_and_markdown(tmp_path):
    lines: list[str] = []
    assert main(out=lines.append, results=tmp_path) == 0
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["policies"]["nirnaya"]["compliance"]["violations"] == 0
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("# Nirnaya evaluation")
    assert any("Expected" in line or "expected net loss" in line for line in lines)


# --- repeat runs: does the model answer the same way twice? -------------------------------------


def test_repeat_runs_are_kept_apart_from_the_columns_and_compared_case_by_case(tmp_path):
    from nirnaya.evaluation.report import load_caches, load_repeats, variance

    at = datetime(2026, 10, 27, tzinfo=UTC).isoformat()
    first = {"N1": {"action": "CHALLENGE", "claims": []}, "K2": {"action": "ACCEPT"}}
    second = {"N1": {"action": "ESCALATE"}, "K2": {"action": "ACCEPT"}}
    for name, answers in [("informed-v2", first), ("informed-v2.run2", second)]:
        data = {"provider": "fake", "model": "m", "run_at": at, "cases": answers,
                "variant": "informed", "prompts": ["p@v2#1"]}  # fmt: skip
        (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    assert list(load_caches(tmp_path)) == ["informed-v2"]  # repeats are not extra columns
    repeats = load_repeats(tmp_path)
    assert {k: len(v) for k, v in repeats.items()} == {"informed-v2": 2}

    cases = [c for c in CASES if c.id in {"N1", "K2"}]
    v = variance(cases, repeats)["informed-v2"]
    assert v["runs"] == 2
    assert [r["agreement"] for r in v["per_run"]] == ["2/2", "1/2"]
    assert v["changed"] == [{"case": "N1", "actions": ["CHALLENGE", "REFER"]}]


def test_a_single_run_has_no_variance_section():
    from nirnaya.evaluation.report import variance

    assert variance(CASES, {}) == {}
