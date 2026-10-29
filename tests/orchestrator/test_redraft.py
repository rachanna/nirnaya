"""Which verifier failures the drafter may fix by redrafting, and which need a person anyway.

Format (a true value in the wrong form) and omission (a required fact left out) may be fixed
and sent automatically. Overreach (a claim its record does not support) may be fixed, but a
person then approves. Contradicting records, changed evidence and code-built quote claims are
never redrafted."""

from nirnaya.evidence.catalogue import Quote
from nirnaya.models import EvidenceType
from nirnaya.models.claim import Claim, QuoteValue
from nirnaya.orchestrator.redraft import FORMAT_ONLY, classify, redraft_plan
from nirnaya.verifier import CheckName, verify
from tests.harness import CASES

E = EvidenceType


def plan_for(case_id, claims=None, **kw):
    case = CASES[case_id]
    records = case.evidence_records()
    drafted = case.claim_objects() if claims is None else claims
    result = verify(case.dispute, drafted, records, **kw)
    return redraft_plan(case.dispute, result, records), result


def n1_claim(**overrides):
    base = CASES["N1"].claim_objects()[0].model_dump()
    base.update(overrides)
    return Claim.model_validate(base)


def test_a_value_written_in_the_wrong_form_is_format_and_may_go_out_automatically():
    plan, result = plan_for("N1", [n1_claim(extracted_value="Status: DELIVERED")])
    assert {f.check for f in result.claims[0].failures} <= FORMAT_ONLY
    assert (plan.retry, plan.overreach, plan.kinds) == (True, False, frozenset({"FORMAT"}))


def test_a_claim_on_a_field_the_record_lacks_is_overreach():
    plan, _ = plan_for("K2C")  # "signed for by J. Smith"; the carrier has no signed_by
    assert (plan.retry, plan.overreach) == (True, True)
    (item,) = plan.feedback["blocked_claims"]
    assert item["source_field"] == "signed_by" and item["kind"] == "OVERREACH"
    assert [f["check"] for f in item["failures"]] == ["FIELD_MISSING"]


def test_a_wrong_amount_another_merchants_record_or_an_unknown_reason_claim_is_overreach():
    for case_id in ("A3", "A2", "U1"):
        plan, _ = plan_for(case_id)
        assert plan.retry and plan.overreach, case_id


def test_a_draft_that_leaves_out_a_required_fact_is_omission():
    order = CASES["N1"].evidence_records()
    order_id = next(r for r in order if r.id == "N1-order").fields["order_id"]
    claim = n1_claim(
        id="claim-order",
        source_record_id="N1-order",
        source_field="order_id",
        establishes=E.ORDER_PLACED,
        extracted_value=order_id,
        normalised_value={"kind": "IDENTIFIER", "text": order_id},
        text=f"Order record: {order_id}",
    )
    plan, result = plan_for("N1", [claim])
    assert {f.check for f in result.case_failures} == {CheckName.REQUIRED_EVIDENCE_MISSING}
    assert (plan.retry, plan.overreach, plan.kinds) == (True, False, frozenset({"OMISSION"}))
    assert plan.feedback["missing_required"] == [["DELIVERY_CONFIRMED"]]


def test_contradicting_records_are_never_redrafted():
    plan, result = plan_for("N4")
    assert str(result.outcome) == "REFER"
    assert not plan.retry


def test_a_passing_draft_needs_no_redraft():
    plan, _ = plan_for("N1")
    assert not plan.retry and plan.feedback == {"blocked_claims": [], "missing_required": []}


def test_changed_evidence_is_never_redrafted():
    plan, _ = plan_for("N1", expected_hashes={"N1-carrier": "0" * 64})
    assert not plan.retry


def test_a_failing_quote_claim_is_never_redrafted():
    quote = "not in the record at all, honestly"
    claim = n1_claim(
        id="quote-1",
        source_field="driver_notes",
        establishes=E.DELIVERY_SIGNATURE,
        extracted_value=quote,
        normalised_value=QuoteValue(text=quote),
        text=f'Delivery signed for by: the note states "{quote}"',
    )
    confirmed = frozenset({Quote("N1-carrier", "driver_notes", E.DELIVERY_SIGNATURE, quote)})
    plan, _ = plan_for("N1", [claim], confirmed_quotes=confirmed)
    assert not plan.retry


def test_feedback_is_deterministic_and_classify_matches_the_plan():
    a, result = plan_for("K2C")
    b, _ = plan_for("K2C")
    assert a == b
    case = CASES["K2C"]
    blocked = [c for c in result.claims if c.failures]
    assert [classify(c, case.evidence_records(), case.dispute) for c in blocked] == ["OVERREACH"]
