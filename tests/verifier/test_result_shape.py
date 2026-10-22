"""The result lists every failed claim and every reason, deterministically, without side effects."""

from nirnaya.models import EvidenceType, ValidationStatus
from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import carrier_record, make_claim
from tests.verifier.helpers import checks, run


def test_all_failures_of_a_claim_are_listed():
    tampered_rec = carrier_record(status="IN_TRANSIT")
    tampered = tampered_rec.model_copy(update={"fields": {**tampered_rec.fields, "note": "x"}})
    claim = make_claim(
        text="Delivered for sure",
        extracted_value="Delivered",
        normalised_value={"kind": "ENUM", "text": "DELIVERED"},
    )
    result = run([claim], [tampered])
    assert checks(result, "claim-1") == {
        CheckName.HASH_MISMATCH,
        CheckName.EXTRACTED_VALUE_MISMATCH,
        CheckName.VALUE_MISMATCH,
        CheckName.EXCEEDS_RECORD,
        CheckName.TEXT_MISMATCH,
    }


def test_every_failed_claim_is_listed_in_input_order():
    a = make_claim(id="b-claim", extracted_value="x")
    b = make_claim(id="a-claim", text="nope")
    result = run([a, b, make_claim(id="ok")])
    assert [c.claim.id for c in result.blocked] == ["b-claim", "a-claim"]
    assert [c.claim.id for c in result.claims] == ["b-claim", "a-claim", "ok"]


def test_duplicate_claim_ids_block_the_case():
    result = run([make_claim(), make_claim()])
    assert {f.check for f in result.case_failures} == {CheckName.DUPLICATE_CLAIM_ID}
    assert result.outcome is VerificationOutcome.BLOCKED


def test_inputs_are_not_mutated():
    claim = make_claim(extracted_value="nope")
    records = [carrier_record()]
    before = (claim.model_dump_json(), [r.model_dump_json() for r in records])
    result = run([claim], records)
    assert claim.validation_status is ValidationStatus.PENDING
    assert result.claims[0].claim.validation_status is ValidationStatus.BLOCKED
    assert (claim.model_dump_json(), [r.model_dump_json() for r in records]) == before


def test_same_inputs_same_result():
    records = [carrier_record(), carrier_record("carrier-2", status="LOST")]
    claims = [make_claim(), make_claim(id="x", text="bad")]
    first = run(claims, records).model_dump_json()
    assert all(run(claims, list(reversed(records))).model_dump_json() == first for _ in range(5))


def test_incoming_status_is_ignored():
    # A drafter cannot pre-approve its own claim.
    claim = make_claim(extracted_value="nope", validation_status=ValidationStatus.VERIFIED)
    result = run([claim])
    assert result.claims[0].claim.validation_status is ValidationStatus.BLOCKED


def test_incoming_contradicting_ids_are_replaced():
    claim = make_claim(contradicting_record_ids=("made-up",))
    result = run([claim])
    assert result.claims[0].claim.contradicting_record_ids == ()
    assert result.established == {EvidenceType.DELIVERY_CONFIRMED}
