"""The report: built from the metric functions, with and without the LLM-only cache."""

import json
from datetime import UTC, datetime

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


def test_with_the_cache_llm_only_is_included():
    cache = Cache("fake", "fake-model", datetime(2026, 10, 27, tzinfo=UTC).isoformat(),
                  {"N1": {"action": "CHALLENGE", "claims": []}})  # fmt: skip
    report = build([c for c in CASES if c.id in {"N1", "K2"}], cache=cache)
    assert report["policies"]["llm_only"]["integrity"]["challenges_without_evidence"] == 1
    assert report["llm_only"]["model"] == "fake-model"


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
