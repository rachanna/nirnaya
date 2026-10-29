"""The evaluation report as the web demo's results view: every approach on the same cases, in
the report's own numbers. Wording only; nothing is recomputed."""

import json
from pathlib import Path

from nirnaya.console.results import results_view

REPORT = Path("eval/results/report.json")


def test_every_approach_is_shown_with_the_reports_numbers():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    view = results_view(report)
    assert view.cases == report["cases"] and view.caveat == report["caveat"]
    assert [p.key for p in view.policies] == list(report["policies"])
    ours = view.policies[0]
    assert ours.ours and ours.name == "Nirnaya" and not any(p.ours for p in view.policies[1:])
    for p in view.policies:
        src = report["policies"][p.key]
        assert p.expected_loss == src["financial"]["expected_loss"]
        assert p.vs_nirnaya == src["financial"]["vs_nirnaya"]
        assert p.unsupported == src["integrity"]["unsupported_submitted"]
        assert p.violations == src["compliance"]["violations"]


def test_the_model_behind_each_llm_baseline_is_named():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    by_key = {p.key: p for p in results_view(report).policies}
    assert by_key["nirnaya"].model is None and by_key["fight_all"].model is None
    llm = report["llm_only"]["informed-v3"]
    assert by_key["llm_only_informed-v3"].model == (
        f"{llm['provider']}/{llm['model']}, answers cached {llm['run_at'][:10]}"
    )
