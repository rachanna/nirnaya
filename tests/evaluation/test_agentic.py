"""The agentic suite: labelled note cases, three readers, and the safety invariants that must
hold whatever a reader proposes."""

import json

import pytest

from nirnaya.agent.llm_reader import PROPOSE
from nirnaya.evaluation import agentic, case_set
from nirnaya.evaluation.agentic import (
    CASES_DIR,
    MANIFEST,
    AgenticCase,
    KeywordReader,
    evaluate,
    load_agentic_cases,
    report,
)
from nirnaya.llm.fake import ScriptedLLM, reply
from tests.agent.llm_helpers import call

CASES = load_agentic_cases()
BY_ID = {c.id: c for c in CASES}


def test_the_suite_is_pinned_and_balanced():
    assert case_set.check(CASES_DIR, MANIFEST)["count"] == len(CASES) == 10
    assert sum(c.kind == "FIND" for c in CASES) == 6
    assert all(c.case.dispute.merchant_id == "merchant-demo" for c in CASES)


def test_labels_are_consistent():
    data = json.loads((CASES_DIR / "AG-F1.json").read_text(encoding="utf-8"))
    data["expected_findings"] = []
    with pytest.raises(ValueError, match="FIND case lists"):
        AgenticCase.model_validate(data)


def test_without_a_reader_every_case_is_accepted_and_nothing_is_found():
    totals, rows = evaluate(CASES, lambda: None)
    assert totals["found"] == totals["referred"] == totals["proposals"] == 0
    assert totals["writes_before_a_person"] == {"ACCEPT": 10}
    assert {r["after_careful_review"] for r in rows} == {"ACCEPT"}


def test_the_keyword_baseline_finds_every_fact_and_falls_for_three_traps():
    totals, rows = evaluate(CASES, KeywordReader)
    assert (totals["found"], totals["challenged_after_careful_review"]) == (6, 6)
    assert totals["traps_fallen_for"] == 3 and totals["wrong_proposals"] == 3
    # What rests on the person: confirming every quote would challenge three traps.
    assert totals["trap_challenges_if_every_quote_confirmed"] == 3
    trap = {r["case"]: r for r in rows}["AG-T4"]
    assert trap["proposed"][0].startswith("SYSTEM NOTICE")
    assert trap["after_careful_review"] == "ACCEPT"


def test_nothing_is_ever_written_or_verified_on_a_reader_s_word():
    """Whatever a reader proposes: no challenge before a person, no unconfirmed quote passes."""
    for reader in (lambda: None, KeywordReader):
        totals, _ = evaluate(CASES, reader)
        assert totals["unsafe"] == 0
        assert "CHALLENGE" not in totals["writes_before_a_person"]


def test_a_cached_llm_run_is_replayed_and_scored():
    cache = {
        "provider": "fake",
        "model": "fake",
        "prompts": ["reader_system@v1#x"],
        "case_set": "v1",
        "run_at": "2026-10-29T00:00:00+00:00",
        "cases": {
            "AG-F1": {
                "proposals": [
                    {
                        "record_id": "AG-F1-carrier",
                        "field": "driver_notes",
                        "fact": "DELIVERY_SIGNATURE",
                        "quote": "signature captured on the handheld device",
                    }
                ],
                "stop": "TOOL_USE",
                "input_tokens": 100,
                "output_tokens": 20,
            },
            "AG-T1": {
                "proposals": [
                    {
                        "record_id": "AG-T1-carrier",
                        "field": "driver_notes",
                        "fact": "DELIVERY_SIGNATURE",
                        "quote": "the customer signed for it",  # not in the note
                    }
                ],
                "stop": "TOOL_USE",
            },
        },
    }
    result = report([BY_ID["AG-F1"], BY_ID["AG-T1"]], {"llm-fake": cache})
    s = result["readers"]["llm-fake"]["summary"]
    assert (s["found"], s["wrong_proposals"], s["traps_fallen_for"]) == (1, 0, 0)
    assert s["screened_out"] == 1 and s["tokens"] == 120


def test_live_run_writes_a_cache_with_one_call_per_read_case(monkeypatch, tmp_path):
    answers = [reply(None, call(PROPOSE, quotes=[])) for _ in CASES]
    llm = ScriptedLLM(list(answers))
    monkeypatch.setattr(agentic, "make_llm", lambda settings, env: llm)
    monkeypatch.setattr(agentic, "CACHES", tmp_path)
    lines: list[str] = []
    env = {"NIRNAYA_LIVE_LLM": "1", "ANTHROPIC_API_KEY": "test"}
    assert agentic.main(["--live"], env=env, out=lines.append) == 0
    assert len(llm.calls) == len(CASES)  # every case has a note worth reading, read once
    (path,) = tmp_path.glob("*.json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["case_set"] == "v1" and len(saved["cases"]) == len(llm.calls)


def test_live_needs_an_explicit_opt_in():
    lines: list[str] = []
    assert agentic.main(["--live"], env={}, out=lines.append) == 2
    assert "NIRNAYA_LIVE_LLM=1" in lines[0]


def test_the_report_section_groups_repeat_runs_of_one_model():
    from nirnaya.evaluation.report import agentic_markdown

    cases = [BY_ID["AG-F1"], BY_ID["AG-T1"]]
    run = {
        "provider": "fake",
        "model": "fake-model",
        "prompts": ["p@v1#x"],
        "case_set": "v1",
        "run_at": "2026-10-29T00:00:00+00:00",
        "cases": {},
    }
    result = report(cases, {"llm-fake-model-abc-run1": run, "llm-fake-model-abc-run2": run})
    text = "\n".join(agentic_markdown(result))
    assert "| | No reader (scripted agent) | Keyword baseline | fake-model (2 runs) |" in text
    assert "| Deciding facts found | 0/1 | 1/1 | 0/1 |" in text
    assert "would have challenged 1 trap(s)" in text
