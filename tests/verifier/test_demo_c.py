"""Demo C: the draft says "delivered and signed for by J. Smith".

The carrier record shows delivery but no signature. Whatever shape the fabrication takes,
the verifier blocks it, names the exact claim, and leaves the true delivery claim verified.
"""

import pytest

from nirnaya.models import EvidenceType, ValidationStatus
from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import carrier_record, make_claim
from tests.verifier.helpers import checks, run

E = EvidenceType

# Delivered, recipient name on the label, no signature captured.
CARRIER = carrier_record()
DELIVERED = make_claim(id="claim-delivered")


def signature_claim(**overrides):
    data = {
        "id": "claim-signed",
        "establishes": E.DELIVERY_SIGNATURE,
        "normalised_value": {"kind": "NAME", "text": "J. Smith"},
        "extracted_value": "J. Smith",
    }
    data.update(overrides)
    return make_claim(**data)


VARIANTS = {
    # (a) cites the signature field, which does not exist on the record.
    "cites missing signed_by": (
        signature_claim(source_field="signed_by"),
        {CheckName.FIELD_MISSING},
    ),
    # (b) cites the recipient name, which is real but does not establish a signature.
    "cites recipient_name": (
        signature_claim(source_field="recipient_name"),
        {CheckName.EXCEEDS_RECORD},
    ),
    # (c) structured as delivery, with the signature smuggled into the text.
    "smuggled in text": (
        make_claim(id="claim-signed", text="Delivered and signed for by J. Smith"),
        {CheckName.TEXT_MISMATCH},
    ),
}


@pytest.mark.parametrize("name", list(VARIANTS))
def test_fabricated_signature_is_blocked(name):
    fabricated, expected_checks = VARIANTS[name]
    result = run([DELIVERED, fabricated], [CARRIER])

    assert result.outcome is VerificationOutcome.BLOCKED
    assert not result.submittable
    assert checks(result, "claim-signed") == expected_checks

    (blocked,) = result.blocked
    assert blocked.claim.id == "claim-signed"
    assert blocked.claim.text == fabricated.text  # the exact unsupported claim, for the referral
    assert blocked.claim.validation_status is ValidationStatus.BLOCKED
    assert all(f.detail for f in blocked.failures)

    delivered = next(c for c in result.claims if c.claim.id == "claim-delivered")
    assert delivered.claim.validation_status is ValidationStatus.VERIFIED


def test_the_true_delivery_claim_alone_passes():
    result = run([DELIVERED], [CARRIER])
    assert result.outcome is VerificationOutcome.PASSED
    assert result.submittable


def test_a_real_signature_would_pass():
    signed = carrier_record(signed_by="J. Smith")
    claim = signature_claim(source_field="signed_by")
    result = run([DELIVERED, claim], [signed])
    assert result.outcome is VerificationOutcome.PASSED
