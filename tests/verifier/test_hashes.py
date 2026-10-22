"""Evidence hashes still match."""

from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import carrier_record, make_claim
from tests.verifier.helpers import checks, run


def test_intact_record_passes():
    rec = carrier_record()
    result = run([make_claim()], [rec], expected_hashes={rec.id: rec.sha256})
    assert result.outcome is VerificationOutcome.PASSED


def test_fields_changed_after_capture_are_blocked():
    rec = carrier_record(status="IN_TRANSIT")
    tampered = rec.model_copy(update={"fields": {**rec.fields, "status": "DELIVERED"}})
    assert CheckName.HASH_MISMATCH in checks(run([make_claim()], [tampered]), "claim-1")


def test_record_rehashed_after_capture_is_blocked_by_the_stored_hash():
    original = carrier_record(status="IN_TRANSIT")
    forged = carrier_record(status="DELIVERED")  # internally consistent, but not what we captured
    result = run([make_claim()], [forged], expected_hashes={"carrier-1": original.sha256})
    assert checks(result, "claim-1") == {CheckName.HASH_MISMATCH}


def test_hash_check_is_skipped_only_for_records_without_a_stored_hash():
    rec = carrier_record()
    result = run([make_claim()], [rec], expected_hashes={"some-other-record": "0" * 64})
    assert checks(result, "claim-1") == set()
