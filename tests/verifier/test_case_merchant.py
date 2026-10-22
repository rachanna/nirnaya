"""Cited records belong to this case and merchant (tenant isolation)."""

from nirnaya.models import SourceSystem, ValidationStatus
from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import OTHER_MERCHANT, carrier_record, make_record
from tests.verifier.helpers import checks, delivery_claim, run


def test_record_of_this_case_and_merchant_passes():
    result = run([delivery_claim()])
    assert result.outcome is VerificationOutcome.PASSED
    assert result.claims[0].claim.validation_status is ValidationStatus.VERIFIED


def test_unknown_record_id_is_blocked():
    result = run([delivery_claim(source_record_id="carrier-404")])
    assert checks(result, "claim-1") == {CheckName.RECORD_NOT_FOUND}
    assert result.outcome is VerificationOutcome.BLOCKED


def test_record_from_another_merchant_is_blocked():
    foreign = make_record(
        SourceSystem.CARRIER, {"status": "DELIVERED"}, id="carrier-1", merchant_id=OTHER_MERCHANT
    )
    result = run([delivery_claim()], [foreign])
    assert checks(result, "claim-1") == {CheckName.CASE_MERCHANT_MISMATCH}


def test_foreign_record_values_are_not_echoed_in_failures():
    foreign = make_record(
        SourceSystem.CARRIER,
        {"status": "DELIVERED", "secret": "ACME-PRIVATE"},
        id="carrier-1",
        merchant_id=OTHER_MERCHANT,
    )
    result = run([delivery_claim()], [foreign])
    assert "ACME-PRIVATE" not in result.model_dump_json()
    assert OTHER_MERCHANT not in result.model_dump_json()


def test_record_from_another_case_is_blocked():
    other_case = make_record(SourceSystem.CARRIER, {"status": "DELIVERED"}, id="carrier-1",
                             case_id="case-2")  # fmt: skip
    result = run([delivery_claim()], [other_case])
    assert checks(result, "claim-1") == {CheckName.CASE_MERCHANT_MISMATCH}


def test_claim_for_another_merchant_is_blocked():
    result = run([delivery_claim(merchant_id=OTHER_MERCHANT)])
    assert CheckName.CASE_MERCHANT_MISMATCH in checks(result, "claim-1")


def test_claim_for_another_case_is_blocked():
    result = run([delivery_claim(case_id="case-2")])
    assert CheckName.CASE_MERCHANT_MISMATCH in checks(result, "claim-1")


def test_ambiguous_record_id_is_blocked():
    twin = carrier_record()
    result = run([delivery_claim()], [carrier_record(), twin.model_copy()])
    assert checks(result, "claim-1") == {CheckName.RECORD_NOT_FOUND}
