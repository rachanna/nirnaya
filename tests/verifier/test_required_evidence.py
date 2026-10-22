"""The evidence the reason requires must be established by verified claims."""

from nirnaya.models import EvidenceType, ReasonCategory, RecordAuthor, SourceSystem
from nirnaya.verifier import CheckName, VerificationOutcome
from tests.factories import make_claim, make_dispute, make_record
from tests.verifier.helpers import order_record, run

E = EvidenceType


def case_checks(result):
    return {f.check for f in result.case_failures}


def test_required_evidence_present_passes():
    result = run([make_claim()])
    assert case_checks(result) == set()
    assert result.established == {E.DELIVERY_CONFIRMED}
    assert result.outcome is VerificationOutcome.PASSED


def test_required_evidence_missing_blocks_the_case():
    claim = make_claim(
        source_record_id="order-1",
        source_field="order_id",
        establishes=E.ORDER_PLACED,
        extracted_value="ORD-1001",
        normalised_value={"kind": "IDENTIFIER", "text": "ORD-1001"},
    )
    result = run([claim], [order_record()])
    assert result.claims[0].failures == ()
    assert case_checks(result) == {CheckName.REQUIRED_EVIDENCE_MISSING}
    assert result.outcome is VerificationOutcome.BLOCKED


def test_blocked_claims_do_not_count_towards_requirements():
    result = run([make_claim(extracted_value="Delivered")])
    assert result.established == frozenset()
    assert CheckName.REQUIRED_EVIDENCE_MISSING in case_checks(result)


def test_empty_draft_is_blocked_for_a_known_reason():
    assert case_checks(run([])) == {CheckName.REQUIRED_EVIDENCE_MISSING}


def test_unknown_reason_has_no_requirements():
    result = run([], dispute=make_dispute(reason_category=ReasonCategory.UNKNOWN))
    assert case_checks(result) == set()
    assert result.outcome is VerificationOutcome.PASSED


def payment_record(**fields):
    base = {"three_ds": "NOT_PERFORMED", "avs": "MATCH", "cvc": "MATCH", "currency": "USD"}
    base.update(fields)
    return make_record(SourceSystem.PAYMENT_ATTEMPT, base, id="pa-1")


def avs_claim():
    return make_claim(
        id="claim-avs",
        source_record_id="pa-1",
        source_field="avs",
        establishes=E.AVS_MATCH,
        extracted_value="MATCH",
        normalised_value={"kind": "ENUM", "text": "MATCH"},
    )


def test_any_member_of_a_group_satisfies_it():
    # Fraud needs one of 3DS, AVS match or a device login; AVS alone satisfies the group.
    dispute = make_dispute(reason_category=ReasonCategory.FRAUD)
    result = run([avs_claim()], [payment_record()], dispute)
    assert case_checks(result) == set()


def test_cvc_alone_does_not_satisfy_fraud():
    dispute = make_dispute(reason_category=ReasonCategory.FRAUD)
    cvc = make_claim(
        source_record_id="pa-1",
        source_field="cvc",
        establishes=E.CVC_MATCH,
        extracted_value="MATCH",
        normalised_value={"kind": "ENUM", "text": "MATCH"},
    )
    assert case_checks(run([cvc], [payment_record()], dispute)) == {
        CheckName.REQUIRED_EVIDENCE_MISSING
    }


def test_every_group_must_be_satisfied():
    dispute = make_dispute(reason_category=ReasonCategory.SUBSCRIPTION)
    policy_rec = make_record(
        SourceSystem.POLICY,
        {"cancellation_terms_accepted_at": "2025-12-01T10:00:00Z"},
        id="policy-1",
        author=RecordAuthor.MERCHANT,
    )
    terms = make_claim(
        id="claim-terms",
        source_record_id="policy-1",
        source_field="cancellation_terms_accepted_at",
        establishes=E.CANCELLATION_TERMS_ACCEPTED,
        extracted_value="2025-12-01T10:00:00Z",
        normalised_value={"kind": "DATETIME", "at": "2025-12-01T10:00:00Z"},
    )
    only_terms = run([terms], [policy_rec], dispute)
    assert len(only_terms.case_failures) == 1  # usage-or-no-contact group still missing

    helpdesk = make_record(
        SourceSystem.HELPDESK,
        {"contacts_before_dispute": 0},
        id="hd-1",
        author=RecordAuthor.MERCHANT,
    )
    no_contact = make_claim(
        id="claim-nc",
        source_record_id="hd-1",
        source_field="contacts_before_dispute",
        establishes=E.NO_PRIOR_CUSTOMER_CONTACT,
        extracted_value="0",
        normalised_value={"kind": "ENUM", "text": "0"},
    )
    both = run([terms, no_contact], [policy_rec, helpdesk], dispute)
    assert case_checks(both) == set()
