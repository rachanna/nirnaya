"""Quoted evidence from free-text notes: the agent proposes a fact with an exact quote; code
checks the quote is word for word in an allowed note of the case's own record, and a person
must have confirmed it. Nothing about a quote is taken on the model's word."""

import pytest

from nirnaya.evidence.catalogue import TEXT_RULES, Quote
from nirnaya.models import EvidenceType, RecordAuthor, SourceSystem, ValidationStatus
from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import carrier_record, make_claim, make_dispute, make_record
from tests.verifier.helpers import checks, run

E = EvidenceType
NOTE = "Driver: handed to the customer at the door, signature captured on the handheld."
QUOTE = "signature captured on the handheld"
CARRIER = carrier_record(driver_notes=NOTE)  # delivered; no structured signed_by


def quote_claim(quote=QUOTE, fact=E.DELIVERY_SIGNATURE, **overrides):
    data = {
        "id": "claim-quote",
        "source_field": "driver_notes",
        "establishes": fact,
        "extracted_value": quote,
        "normalised_value": {"kind": "QUOTE", "text": quote},
    }
    data.update(overrides)
    return make_claim(**data)


def confirmed(quote=QUOTE, fact=E.DELIVERY_SIGNATURE, record="carrier-1", field="driver_notes"):
    return frozenset({Quote(record, field, fact, quote)})


def test_a_confirmed_verbatim_quote_from_an_allowed_note_verifies():
    result = run([quote_claim()], [CARRIER], confirmed_quotes=confirmed())
    (r,) = result.claims
    assert r.claim.validation_status is ValidationStatus.VERIFIED
    assert E.DELIVERY_SIGNATURE in result.established
    assert r.claim.text.endswith(f'"{QUOTE}"')


def test_an_unconfirmed_quote_is_blocked():
    result = run([quote_claim()], [CARRIER])
    assert checks(result, "claim-quote") == {CheckName.QUOTE_UNCONFIRMED}
    assert result.outcome is VerificationOutcome.BLOCKED


@pytest.mark.parametrize(
    "quote",
    [
        "signature was captured on the handheld",  # paraphrase, not verbatim
        "Signature captured on the handheld",  # case differs
        "captured",  # too short to mean anything on its own
    ],
)
def test_a_quote_that_is_not_word_for_word_in_the_note_is_blocked(quote):
    result = run([quote_claim(quote)], [CARRIER], confirmed_quotes=confirmed(quote))
    assert CheckName.QUOTE_NOT_IN_RECORD in checks(result, "claim-quote")


def test_whitespace_differences_do_not_matter():
    spaced = carrier_record(driver_notes=NOTE.replace(" signature", "\n  signature"))
    result = run([quote_claim()], [spaced], confirmed_quotes=confirmed())
    assert result.claims[0].claim.validation_status is ValidationStatus.VERIFIED


def test_a_note_cannot_establish_a_fact_outside_the_text_rules():
    fact = E.THREE_DS_AUTHENTICATED
    assert fact not in TEXT_RULES[(SourceSystem.CARRIER, "driver_notes")]
    claim = quote_claim(fact=fact)
    result = run([claim], [CARRIER], confirmed_quotes=confirmed(fact=fact))
    assert CheckName.TEXT_NOT_ALLOWED in checks(result, "claim-quote")


def test_a_structured_field_cannot_be_quoted():
    claim = quote_claim(quote="DELIVERED", source_field="status", fact=E.DELIVERY_CONFIRMED)
    result = run([claim], [CARRIER], confirmed_quotes=confirmed("DELIVERED", E.DELIVERY_CONFIRMED,
                                                                  field="status"))  # fmt: skip
    assert CheckName.TEXT_NOT_ALLOWED in checks(result, "claim-quote")


def test_a_customer_written_note_establishes_nothing():
    customer = make_record(
        SourceSystem.CARRIER,
        {"tracking_number": "T1", "status": "DELIVERED", "driver_notes": NOTE},
        id="carrier-1",
        author=RecordAuthor.CUSTOMER,
    )
    result = run([quote_claim()], [customer], confirmed_quotes=confirmed())
    assert CheckName.TEXT_NOT_ALLOWED in checks(result, "claim-quote")


def test_a_changed_record_fails_the_hash_check_even_with_a_confirmed_quote():
    result = run(
        [quote_claim()],
        [CARRIER],
        confirmed_quotes=confirmed(),
        expected_hashes={"carrier-1": "0" * 64},
    )
    assert CheckName.HASH_MISMATCH in checks(result, "claim-quote")


def test_a_confirmation_for_another_record_or_fact_does_not_count():
    other = frozenset({Quote("carrier-2", "driver_notes", E.DELIVERY_SIGNATURE, QUOTE)})
    result = run([quote_claim()], [CARRIER], confirmed_quotes=other)
    assert checks(result, "claim-quote") == {CheckName.QUOTE_UNCONFIRMED}


def test_quote_checks_are_deterministic():
    a = run([quote_claim()], [CARRIER], confirmed_quotes=confirmed())
    b = run([quote_claim()], [CARRIER], confirmed_quotes=confirmed())
    assert a == b


def test_the_engine_never_counts_a_note_by_itself():
    from nirnaya.evidence.derive import evidence_present

    assert E.DELIVERY_SIGNATURE not in evidence_present([CARRIER], make_dispute())
