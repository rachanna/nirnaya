"""The LLM-only baseline, offline: a scripted model, the cache, and how its answers are scored."""

import json
from datetime import UTC, datetime

from nirnaya.evaluation.cases import load_cases
from nirnaya.evaluation.llm_baseline import DECIDE, SYSTEM, Cache, llm_only, main, run
from nirnaya.evaluation.policies import Act
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.llm.types import ToolCall

CASES = {c.id: c for c in load_cases()}
NOW = datetime(2026, 10, 27, tzinfo=UTC)


def decision(action, claims=(), call_id="t1"):
    return reply(
        None,
        ToolCall(
            id=call_id,
            name=DECIDE,
            arguments={"action": action, "rationale": "because", "claims": list(claims)},
        ),
    )


FAKE_SIGNATURE = {
    "source_record_id": "K2C-carrier",
    "source_field": "signed_by",
    "establishes": "DELIVERY_SIGNATURE",
    "extracted_value": "J. Smith",
    "value_kind": "NAME",
    "text": "J. Smith",
}


def test_one_call_per_actionable_case_with_static_instructions():
    cases = [CASES["K1"], CASES["X2"]]  # X2 is pre-arbitration under Airwallex review: skipped
    llm = ScriptedLLM([decision("CHALLENGE")])
    result = run(llm, cases, now=NOW, out=lambda _: None)
    assert list(result["cases"]) == ["K1"]
    (call,) = llm.calls
    assert call["system"] == SYSTEM  # no case data or customer text in the system prompt
    assert call["force_tool"] == DECIDE


def test_customer_text_reaches_the_model_only_inside_untrusted_blocks():
    llm = ScriptedLLM([decision("ACCEPT")])
    run(llm, [CASES["A1"]], now=NOW, out=lambda _: None)  # A1: a prompt injection email
    (call,) = llm.calls
    text = "".join(m.text or "" for m in call["messages"])
    assert "<untrusted" in text
    assert "ignore previous instructions" not in call["system"].lower()


def cache_of(answers):
    return Cache("fake", "fake", NOW.isoformat(), answers)


def test_a_fabricated_signature_is_scored_unsupported():
    cache = cache_of({"K2C": {"action": "CHALLENGE", "claims": [FAKE_SIGNATURE]}})
    result = llm_only(CASES["K2C"], cache)
    assert result.action is Act.CHALLENGE
    assert result.unsupported_submitted == 1


def test_unbuildable_claims_count_as_unsupported_and_escalate_maps_to_refer():
    junk = {"source_record_id": "nope", "value_kind": "NAME"}
    cache = cache_of(
        {
            "N1": {"action": "CHALLENGE", "claims": [junk]},
            "K3": {"action": "ESCALATE", "claims": []},
        }
    )
    assert llm_only(CASES["N1"], cache).unsupported_submitted == 1
    assert llm_only(CASES["K3"], cache).action is Act.REFER


def test_a_missing_or_failed_answer_is_left_to_a_person():
    cache = cache_of({"N1": {"error": "rate limited"}})
    assert llm_only(CASES["N1"], cache).action is Act.REFER
    assert llm_only(CASES["K1"], cache).action is Act.REFER  # no answer at all


def test_the_cache_round_trips(tmp_path):
    llm = ScriptedLLM([decision("ACCEPT")])
    result = run(llm, [CASES["K2"]], now=NOW, out=lambda _: None)
    path = tmp_path / "llm_only.json"
    path.write_text(json.dumps(result, default=str), encoding="utf-8")
    cache = Cache.load(path)
    assert cache.model == "fake" and cache.cases["K2"]["action"] == "ACCEPT"
    assert llm_only(CASES["K2"], cache).action is Act.ACCEPT


def test_the_run_is_opt_in():
    lines = []
    assert main(env={}, out=lines.append) == 2
    assert "NIRNAYA_LIVE_LLM=1" in lines[0]


def test_a_missing_value_field_is_filled_from_the_models_own_extracted_value():
    # The model gave the signature name in extracted_value but left `text` out: judged on the
    # claim itself (still unsupported, because the record has no signature), not on the format.
    item = {k: v for k, v in FAKE_SIGNATURE.items() if k != "text"}
    cache = cache_of({"K2C": {"action": "CHALLENGE", "claims": [item]}})
    result = llm_only(CASES["K2C"], cache)
    assert len(result.submitted) == 1  # built, not dropped
    assert result.unsupported_submitted == 1  # and the verifier still rejects it
