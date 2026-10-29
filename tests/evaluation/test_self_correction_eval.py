"""The fault-injection evaluation of self-correction, offline: scored from a cached run."""

from nirnaya.agent.scripted import ScriptedDrafter
from nirnaya.evaluation.self_correction import (
    INJECTIONS,
    break_draft,
    challenge_cases,
    evaluate,
)
from nirnaya.verifier import VerificationOutcome, verify


def item(claim):
    """A claim as the model would submit it."""
    v = claim.normalised_value
    out = {
        "source_record_id": claim.source_record_id,
        "source_field": claim.source_field,
        "establishes": str(claim.establishes),
        "extracted_value": claim.extracted_value,
        "value_kind": "NAME" if v.kind == "NAME" else v.kind,
    }
    if v.kind == "MONEY":
        out |= {"amount": str(v.money.amount), "currency": v.money.currency, "basis": str(v.basis)}
    elif v.kind == "DATETIME":
        out["at"] = v.at.isoformat()
    elif v.kind == "DATE":
        out |= {"date": v.date.isoformat(), "tz": v.tz}
    else:
        out["text"] = v.text
    return out


def cache(answer_for):
    return {
        "provider": "fake",
        "model": "fake",
        "prompts": ["drafter_feedback@v1#x"],
        "run_at": "2026-10-29T00:00:00+00:00",
        "cases": {f"{c.id}:{k}": answer_for(c) for c in challenge_cases() for k in INJECTIONS},
    }


def perfect(case):
    claims = ScriptedDrafter().draft(case.dispute, case.evidence_records()).claims
    return {"claims": [item(c) for c in claims], "stop": "TOOL_USE", "input_tokens": 1}


def test_every_injected_first_draft_is_blocked():
    for case in challenge_cases():
        records = case.evidence_records()
        good = ScriptedDrafter().draft(case.dispute, records).claims
        for kind in INJECTIONS:
            broken = break_draft(good, records, kind)
            assert verify(case.dispute, broken, records).outcome is VerificationOutcome.BLOCKED


def test_a_perfect_repair_is_sent_for_format_and_sent_to_a_person_for_overreach():
    result = evaluate(cache(perfect))
    n = len(challenge_cases())
    fmt, over = result["summary"]["format"], result["summary"]["overreach"]
    assert (fmt["repaired"], fmt["sent_automatically"], fmt["unsafe"]) == (n, n, 0)
    assert (over["repaired"], over["sent_to_a_person_after_repair"], over["unsafe"]) == (n, n, 0)


def test_a_model_that_never_answers_repairs_nothing_and_nothing_is_written():
    result = evaluate(cache(lambda case: None))
    for kind in INJECTIONS:
        s = result["summary"][kind]
        assert s["repaired"] == s["sent_automatically"] == s["unsafe"] == 0
        assert s["not_repaired"] == s["cases"]
