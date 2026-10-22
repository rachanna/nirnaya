"""Three conflicts, three treatments; material counterevidence is always surfaced."""

from nirnaya.models import EvidenceType, RecordAuthor, SourceSystem, ValidationStatus
from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import carrier_record, make_claim, make_record
from tests.verifier.helpers import checks, run

E = EvidenceType


def test_no_contradiction_passes():
    result = run([make_claim()])
    assert result.claims[0].conflicting_record_ids == ()
    assert result.counterevidence == ()


def test_merchant_records_that_disagree_go_to_a_human():
    returned = carrier_record("carrier-2", status="RETURNED_TO_SENDER")
    result = run([make_claim()], [carrier_record(), returned])
    (cr,) = result.claims
    assert cr.failures == ()  # the claim is supported by its own record
    assert cr.claim.validation_status is ValidationStatus.CONFLICT
    assert cr.conflicting_record_ids == ("carrier-2",)
    assert cr.claim.contradicting_record_ids == ("carrier-2",)
    assert result.outcome is VerificationOutcome.REFER
    assert [c.record_id for c in result.counterevidence] == ["carrier-2"]


def test_merchant_helpdesk_acknowledgement_conflicts():
    ack = make_record(
        SourceSystem.HELPDESK,
        {"acknowledged_non_delivery": True},
        id="hd-1",
        author=RecordAuthor.MERCHANT,
    )
    result = run([make_claim()], [carrier_record(), ack])
    assert result.outcome is VerificationOutcome.REFER


def customer_ticket(**fields):
    base = {"reported_not_received": True, "sent_at": "2026-02-25T10:00:00Z"}
    base.update(fields)
    return make_record(SourceSystem.HELPDESK, base, id="ticket-1", author=RecordAuthor.CUSTOMER)


def test_customer_allegation_is_a_normal_dispute_but_is_surfaced():
    result = run([make_claim()], [carrier_record(), customer_ticket()])
    (cr,) = result.claims
    assert cr.claim.validation_status is ValidationStatus.VERIFIED
    assert cr.conflicting_record_ids == ()
    assert cr.claim.contradicting_record_ids == ("ticket-1",)
    assert result.outcome is VerificationOutcome.PASSED
    (ce,) = result.counterevidence
    assert (ce.record_id, ce.author, ce.claim_id) == ("ticket-1", RecordAuthor.CUSTOMER, "claim-1")


def test_any_customer_record_is_surfaced_even_without_a_matching_rule():
    note = make_record(SourceSystem.HELPDESK, {"message": "I want my money back"}, id="ticket-9",
                       author=RecordAuthor.CUSTOMER)  # fmt: skip
    result = run([make_claim()], [carrier_record(), note])
    assert result.outcome is VerificationOutcome.PASSED
    assert [(c.record_id, c.claim_id) for c in result.counterevidence] == [("ticket-9", None)]


def no_contact_claim():
    return make_claim(
        id="claim-nc",
        source_record_id="hd-sum",
        source_field="contacts_before_dispute",
        establishes=E.NO_PRIOR_CUSTOMER_CONTACT,
        extracted_value="0",
        normalised_value={"kind": "ENUM", "text": "0"},
    )


def helpdesk_summary(contacts: int = 0):
    return make_record(
        SourceSystem.HELPDESK,
        {"contacts_before_dispute": contacts},
        id="hd-sum",
        author=RecordAuthor.MERCHANT,
    )


def test_denying_a_complaint_that_exists_is_blocked():
    # The merchant summary says 0 contacts, but the customer wrote before the dispute opened.
    records = [carrier_record(), helpdesk_summary(0), customer_ticket()]
    result = run([make_claim(), no_contact_claim()], records)
    assert checks(result, "claim-nc") == {CheckName.DENIES_CUSTOMER_COMPLAINT}
    assert result.outcome is VerificationOutcome.BLOCKED


def test_customer_message_after_the_dispute_does_not_block_no_prior_contact():
    later = customer_ticket(sent_at="2026-03-05T10:00:00Z")  # dispute opened 2026-03-01
    records = [carrier_record(), helpdesk_summary(0), later]
    result = run([make_claim(), no_contact_claim()], records)
    assert checks(result, "claim-nc") == set()


def test_customer_message_without_a_timestamp_is_treated_as_prior():
    undated = customer_ticket(sent_at=None)
    records = [carrier_record(), helpdesk_summary(0), undated]
    result = run([make_claim(), no_contact_claim()], records)
    assert checks(result, "claim-nc") == {CheckName.DENIES_CUSTOMER_COMPLAINT}


def test_merchant_record_contradicting_no_contact_conflicts():
    other_summary = make_record(
        SourceSystem.HELPDESK,
        {"contacts_before_dispute": 2},
        id="hd-sum-2",
        author=RecordAuthor.MERCHANT,
    )
    records = [carrier_record(), helpdesk_summary(0), other_summary]
    result = run([make_claim(), no_contact_claim()], records)
    nc = next(c for c in result.claims if c.claim.id == "claim-nc")
    assert nc.claim.validation_status is ValidationStatus.CONFLICT
    assert result.outcome is VerificationOutcome.REFER


def test_block_takes_precedence_over_conflict():
    returned = carrier_record("carrier-2", status="RETURNED_TO_SENDER")
    bad = make_claim(id="claim-bad", extracted_value="Delivered")
    result = run([make_claim(), bad], [carrier_record(), returned])
    assert result.outcome is VerificationOutcome.BLOCKED
