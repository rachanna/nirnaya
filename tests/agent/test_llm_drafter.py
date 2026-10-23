from nirnaya.agent.llm_drafter import SUBMIT, LLMDrafter
from nirnaya.agent.prompts import DRAFTER_SYSTEM
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.models import EvidenceType
from nirnaya.verifier import CheckName, VerificationOutcome, verify
from tests.agent.llm_helpers import all_text_sent, call
from tests.harness import CASES

E = EvidenceType


def submission(*items):
    return reply(None, call(SUBMIT, claims=list(items)))


DELIVERED = {
    "source_record_id": "N1-carrier",
    "source_field": "status",
    "establishes": "DELIVERY_CONFIRMED",
    "extracted_value": "DELIVERED",
    "value_kind": "ENUM",
    "text": "DELIVERED",
}


def draft(case_id, *items):
    case = CASES[case_id]
    llm = ScriptedLLM([submission(*items)])
    result = LLMDrafter(llm).draft(case.dispute, case.evidence_records())
    return case, llm, result


def test_claims_are_built_by_code_with_rendered_text_and_code_ids():
    case, llm, result = draft("N1", DELIVERED)
    (claim,) = result.claims
    assert claim.id == "claim-1"
    assert claim.text == "Delivery confirmed by carrier: DELIVERED"
    assert (claim.case_id, claim.merchant_id) == ("N1", case.dispute.merchant_id)
    assert verify(case.dispute, result.claims, case.evidence_records()).outcome is (
        VerificationOutcome.PASSED
    )
    assert llm.calls[0]["force_tool"] == SUBMIT
    assert llm.calls[0]["system"] == DRAFTER_SYSTEM


def test_items_code_cannot_build_are_dropped_and_recorded():
    bad = [
        {**DELIVERED, "value_kind": "VIBES"},
        {**DELIVERED, "value_kind": "MONEY", "amount": "lots", "currency": "USD"},
        {**DELIVERED, "source_record_id": "N1-somewhere-else"},
        {**DELIVERED, "value_kind": "DATETIME", "at": "2026-02-14T15:30:00"},  # naive
        {"source_record_id": "N1-carrier"},  # missing fields
    ]
    case, llm, result = draft("N1", DELIVERED, *bad)
    assert [c.establishes for c in result.claims] == [E.DELIVERY_CONFIRMED]
    assert len(result.run.dropped) == 5
    assert all(d["why"] for d in result.run.dropped)


def test_only_this_cases_merchant_and_third_party_records_are_citable():
    case, llm, _ = draft("A1", DELIVERED)
    spec = llm.calls[0]["tools"][0]
    record_ids = spec.input_schema["properties"]["claims"]["items"]["properties"][
        "source_record_id"
    ]["enum"]
    assert "A1-email-1" not in record_ids and "A1-carrier" in record_ids
    assert "ignore previous instructions" not in all_text_sent(llm).lower()


def test_reason_specific_facts_are_not_offered_under_unknown():
    case, llm, _ = draft("U1", DELIVERED)
    facts = llm.calls[0]["tools"][0].input_schema["properties"]["claims"]["items"]["properties"][
        "establishes"
    ]["enum"]
    assert "DELIVERY_CONFIRMED" not in facts and "ORDER_PLACED" in facts


def test_a_fabricated_signature_from_the_model_is_blocked_downstream():
    signed = {
        "source_record_id": "K2C-carrier",
        "source_field": "signed_by",
        "establishes": "DELIVERY_SIGNATURE",
        "extracted_value": "J. Smith",
        "value_kind": "NAME",
        "text": "J. Smith",
    }
    case, llm, result = draft("K2C", signed)
    assert len(result.claims) == 1  # the drafter does not judge; the verifier does
    check = verify(case.dispute, result.claims, case.evidence_records())
    assert check.outcome is VerificationOutcome.BLOCKED
    assert {f.check for f in check.blocked[0].failures} == {CheckName.FIELD_MISSING}


def test_no_submission_or_a_model_failure_gives_no_claims():
    case = CASES["N1"]
    no_call = LLMDrafter(ScriptedLLM([reply("Here are the claims: ...")])).draft(
        case.dispute, case.evidence_records()
    )
    assert no_call.claims == () and no_call.run.stop.startswith("NO_SUBMISSION")
    failed = LLMDrafter(ScriptedLLM([])).draft(case.dispute, case.evidence_records())
    assert failed.claims == () and failed.run.stop.startswith("LLM_ERROR")


def test_the_field_guide_tells_the_model_which_fields_support_which_facts():
    case, llm, _ = draft("N1", DELIVERED)
    prompt = llm.calls[0]["messages"][0].text
    assert "DELIVERY_SIGNATURE: CARRIER.signed_by (NAME)" in prompt
