"""Unsupported claims, split honestly: a true fact written in the wrong form is not the same as a
claim its record does not support. And how much the decisions depend on assumed numbers.
From an independent review (NIR-005, NIR-013)."""

from decimal import Decimal

from nirnaya.evaluation.cases import load_cases
from nirnaya.evaluation.llm_baseline import Cache, llm_only
from nirnaya.evaluation.policies import fight_all, score_claims_split

CASES = {c.id: c for c in load_cases()}
AT = "2026-10-27T00:00:00+00:00"


def answer(case_id, *claims):
    return Cache("fake", "fake", AT, {case_id: {"action": "CHALLENGE", "claims": list(claims)}})


def test_a_true_fact_in_prose_is_format_only():
    prose = {
        "source_record_id": "N1-carrier", "source_field": "status",
        "establishes": "DELIVERY_CONFIRMED", "extracted_value": "Carrier status is DELIVERED.",
        "value_kind": "ENUM", "text": "Carrier status is DELIVERED.",
    }  # fmt: skip
    result = llm_only(CASES["N1"], answer("N1", prose))
    assert (result.unsupported_submitted, result.unsupported_format) == (1, 1)


def test_a_field_that_does_not_establish_the_fact_is_substantive():
    overreach = {
        "source_record_id": "D4-order", "source_field": "customer_name",
        "establishes": "DISTINCT_TRANSACTIONS", "extracted_value": "J. Smith",
        "value_kind": "NAME", "text": "J. Smith",
    }  # fmt: skip
    result = llm_only(CASES["D4"], answer("D4", overreach))
    assert (result.unsupported_submitted, result.unsupported_format) == (1, 0)


def test_a_fabricated_signature_is_substantive():
    fake = {
        "source_record_id": "K2C-carrier", "source_field": "signed_by",
        "establishes": "DELIVERY_SIGNATURE", "extracted_value": "J. Smith",
        "value_kind": "NAME", "text": "J. Smith",
    }  # fmt: skip
    result = llm_only(CASES["K2C"], answer("K2C", fake))
    assert (result.unsupported_submitted, result.unsupported_format) == (1, 0)


def test_fight_all_fixture_fabrications_are_substantive():
    total, fmt = score_claims_split(CASES["K2C"], CASES["K2C"].claim_objects())
    assert total >= 1 and fmt == 0
    assert fight_all(CASES["K2C"]).unsupported_format == 0


def test_the_sensitivity_table_reports_flips_per_assumption():
    from nirnaya.evaluation.sensitivity import sensitivity

    table = sensitivity(list(CASES.values()))
    rows = {r["assumption"]: r for r in table["rows"]}
    assert table["cases"] > 30  # cases with a maths action
    assert rows["q = 0.25"]["flips"] > rows["q = 0.05"]["flips"]
    assert rows["as configured"]["flips"] == 0
    near = table["near_break_even"]
    assert all(abs(Decimal(r["gap"])) <= Decimal("0.05") for r in near)
