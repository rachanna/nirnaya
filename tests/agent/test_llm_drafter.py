from nirnaya.agent.llm_drafter import SUBMIT, LLMDrafter
from nirnaya.agent.prompts import DRAFTER_SYSTEM
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.models import EvidenceRecord, EvidenceType
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
    # The customer's email is never citable; only records that establish a fact are offered
    # (A1's carrier record says IN_TRANSIT, so it establishes nothing).
    assert "A1-email-1" not in record_ids and record_ids == ["A1-order"]
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


def offered(llm):
    props = llm.calls[0]["tools"][0].input_schema["properties"]["claims"]["items"]["properties"]
    return set(props["establishes"]["enum"])


def test_the_guide_lists_exactly_the_fields_of_this_case_that_establish_a_fact():
    case, llm, _ = draft("N1", DELIVERED)
    prompt = llm.calls[0]["messages"][0].text
    assert "N1-carrier.status (ENUM) establishes DELIVERY_CONFIRMED" in prompt
    assert "signed_by" not in prompt  # N1's carrier record has no signature to offer


def test_only_facts_the_records_establish_are_offered():
    # K1's order has no is_distinct_purchase flag, so "separate purchase" is never offered:
    # the live model once claimed it from order_id and the whole draft was blocked.
    case, llm, _ = draft("K1", DELIVERED)
    facts = offered(llm)
    assert "PRIOR_UNDISPUTED_ORDERS_MATCH" in facts and "ORDER_PLACED" in facts
    assert "DISTINCT_TRANSACTIONS" not in facts
    assert "DELIVERY_SIGNATURE" in facts  # K1's delivery really was signed for
    prompt = llm.calls[0]["messages"][0].text
    assert "K1-order.prior_undisputed_orders_matching_device_ip" in prompt


def test_nothing_to_offer_means_no_model_call():
    case = CASES["N1"]
    order_only = [r for r in case.evidence_records() if r.id == "N1-carrier"]
    # A citable, intact record that establishes nothing.
    bare = [
        EvidenceRecord.create(
            **r.model_dump(exclude={"sha256", "fields"}), fields={"tracking_number": "TRK"}
        )
        for r in order_only
    ]
    assert all(r.hash_ok() for r in bare)
    llm = ScriptedLLM([])
    result = LLMDrafter(llm).draft(case.dispute, bare)
    assert result.claims == () and llm.calls == []
