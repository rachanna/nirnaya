"""The LLM-only baseline, offline: a scripted model, the cache, and how its answers are scored."""

import json
from datetime import UTC, datetime

from nirnaya import assets
from nirnaya.evaluation.cases import load_cases
from nirnaya.evaluation.llm_baseline import DECIDE, Cache, llm_only, main, run
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
    # no case data or customer text in the system prompt
    assert call["system"] == assets.prompt("baseline_naive_system", {}).text
    assert call["force_tool"] == DECIDE
    assert result["variant"] == "naive"
    assert result["prompts"][0].startswith("baseline_naive_system@v1#")


def test_the_fee_sentence_is_rendered_from_the_policy():
    llm = ScriptedLLM([decision("ACCEPT")])
    run(llm, [CASES["K2"]], now=NOW, out=lambda _: None)
    text = llm.calls[0]["messages"][0].text
    assert (
        "Merchant costs: chargeback fee 25 USD, pre-chargeback resolution fee 15 USD, "
        "pre-arbitration fee 500 USD, handling 20 USD per challenge.\nDecide."
    ) in text


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


# --- the informed variant: the same model, told the merchant's rules --------------------------


def informed():
    from nirnaya.evaluation.llm_baseline import load_prompts

    return load_prompts("informed", {})


def test_the_informed_system_prompt_states_the_policy_and_no_case_data():
    from nirnaya.evaluation.llm_baseline import system_prompt

    text = system_prompt(informed())
    for expected in (
        "merchant-demo",  # whose records may be cited
        "above 2000 USD",  # referral threshold
        "5 USD",  # marginal band
        "FRAUD with PRIOR_UNDISPUTED_ORDERS_MATCH: p = 0.60",  # a prior, shown as a prior
        "1 EUR = 1.10 USD",  # FX
        "got no reply",  # the unanswered-contact rule
    ):
        assert expected in text
    assert all(case_id not in text for case_id in ("K1", "N1", "A1"))
    assert "$" not in text  # every placeholder filled


def test_the_informed_case_prompt_shows_merchants_and_a_field_guide_outside_the_data():
    from nirnaya.evaluation.llm_baseline import case_prompt

    text = case_prompt(CASES["A2"], informed())  # A2: another merchant's carrier record
    data, guide = text.split("Which record fields establish which facts", 1)
    assert '"merchant_id": "merchant-other"' in data and "<untrusted" in data
    assert "<untrusted" not in guide  # the guide is code output, not case data
    assert "establishes DELIVERY_CONFIRMED" in guide


def test_informed_customer_text_stays_inside_untrusted_blocks():
    from nirnaya.evaluation.llm_baseline import case_prompt, system_prompt

    p = informed()
    text = case_prompt(CASES["A1"], p)
    at = text.lower().index("ignore previous instructions")
    assert text.rfind("<untrusted", 0, at) > text.rfind("</untrusted", 0, at)
    assert "ignore previous" not in system_prompt(p).lower()


def test_each_variant_has_its_own_cache_and_records_its_prompts():
    from nirnaya.evaluation.llm_baseline import cache_path, load_prompts

    assert cache_path(load_prompts("naive", {})).name == "naive-v1.json"
    assert cache_path(informed()).name == f"informed-{informed().system.version}.json"
    llm = ScriptedLLM([decision("ACCEPT")])
    result = run(llm, [CASES["K2"]], now=NOW, out=lambda _: None, prompts=informed())
    assert result["variant"] == "informed"
    assert result["prompts"][0] == informed().system.ref
    import pytest

    with pytest.raises(ValueError, match="unknown variant"):
        load_prompts("clever", {})


def test_informed_v2_maps_airwallex_reason_types_and_states_the_strict_band():
    from nirnaya.evaluation.llm_baseline import load_prompts, system_prompt

    p = load_prompts("informed", {"NIRNAYA_PROMPTS": "baseline_informed_system:v2"})
    text = system_prompt(p)
    assert "- CANCELLED_PRODUCT: SUBSCRIPTION" in text
    assert "- UNRECOGNIZED_CHARGE: FRAUD" in text
    assert "less than 5 USD" in text and "exactly 5 USD" in text
    assert "$" not in text


def test_informed_v1_still_renders_unchanged():
    """Old versions stay usable: v1 renders from the same pool and has no reason table."""
    from nirnaya.evaluation.llm_baseline import load_prompts, system_prompt

    p = load_prompts("informed", {"NIRNAYA_PROMPTS": "baseline_informed_system:v1"})
    text = system_prompt(p)
    assert "CANCELLED_PRODUCT" not in text and "within 5 USD of zero" in text


def test_a_repeat_run_gets_its_own_cache_file():
    from nirnaya.evaluation.llm_baseline import cache_path

    p = informed()
    assert cache_path(p, run=1).name == f"informed-{p.system.version}.json"
    assert cache_path(p, run=2).name == f"informed-{p.system.version}.run2.json"
    lines = []
    assert main(env={}, out=lines.append, argv=["--variant", "informed", "--run", "0"]) == 2


def test_a_run_records_the_case_set_it_answered():
    from nirnaya.evaluation import case_set

    llm = ScriptedLLM([decision("ACCEPT")])
    result = run(llm, [CASES["K2"]], now=NOW, out=lambda _: None)
    assert result["case_set"] == case_set.check()["version"]
