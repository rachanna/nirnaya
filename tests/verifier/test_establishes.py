"""No claim goes beyond what its record establishes; reason-specific claims under UNKNOWN."""

from nirnaya.models import EvidenceType, ReasonCategory, RecordAuthor, SourceSystem
from nirnaya.verifier import CheckName
from tests.factories import carrier_record, make_claim, make_dispute, make_record
from tests.verifier.helpers import checks, order_record, run

E = EvidenceType


def test_field_that_establishes_the_fact_passes():
    assert checks(run([make_claim()]), "claim-1") == set()


def test_in_transit_status_does_not_establish_delivery():
    rec = carrier_record(status="IN_TRANSIT")
    claim = make_claim(extracted_value="IN_TRANSIT",
                       normalised_value={"kind": "ENUM", "text": "IN_TRANSIT"})  # fmt: skip
    assert checks(run([claim], [rec]), "claim-1") == {CheckName.EXCEEDS_RECORD}


def test_wrong_field_for_the_fact_is_blocked():
    # The tracking number exists, but a tracking number does not prove delivery.
    claim = make_claim(
        source_field="tracking_number",
        extracted_value="1Z999AA10123456784",
        normalised_value={"kind": "IDENTIFIER", "text": "1Z999AA10123456784"},
    )
    assert checks(run([claim]), "claim-1") == {CheckName.EXCEEDS_RECORD}


def test_order_record_cannot_establish_delivery():
    claim = make_claim(
        source_record_id="order-1",
        source_field="order_id",
        extracted_value="ORD-1001",
        normalised_value={"kind": "IDENTIFIER", "text": "ORD-1001"},
    )
    assert checks(run([claim], [order_record()]), "claim-1") == {CheckName.EXCEEDS_RECORD}


def test_missing_field_is_blocked_once():
    claim = make_claim(source_field="proof_of_delivery_photo")
    assert checks(run([claim]), "claim-1") == {CheckName.FIELD_MISSING}


def test_null_field_is_blocked():
    rec = carrier_record(status=None)
    assert checks(run([make_claim()], [rec]), "claim-1") == {CheckName.FIELD_MISSING}


def test_customer_authored_record_cannot_support_a_claim():
    rec = make_record(
        SourceSystem.CARRIER, {"status": "DELIVERED"}, id="carrier-1", author=RecordAuthor.CUSTOMER
    )
    assert checks(run([make_claim()], [rec]), "claim-1") == {CheckName.EXCEEDS_RECORD}


def test_reason_specific_claim_under_unknown_reason_is_blocked():
    dispute = make_dispute(reason_category=ReasonCategory.UNKNOWN)
    assert checks(run([make_claim()], dispute=dispute), "claim-1") == {
        CheckName.REASON_SPECIFIC_UNDER_UNKNOWN
    }


def test_general_claim_under_unknown_reason_passes():
    dispute = make_dispute(reason_category=ReasonCategory.UNKNOWN)
    claim = make_claim(
        source_record_id="order-1",
        source_field="order_id",
        establishes=E.ORDER_PLACED,
        extracted_value="ORD-1001",
        normalised_value={"kind": "IDENTIFIER", "text": "ORD-1001"},
    )
    assert checks(run([claim], [order_record()], dispute), "claim-1") == set()


def test_cross_reason_claim_is_allowed_for_a_known_reason():
    # Delivery evidence in a fraud dispute is fine; only UNKNOWN restricts reason-specific facts.
    dispute = make_dispute(reason_category=ReasonCategory.FRAUD)
    assert checks(run([make_claim()], dispute=dispute), "claim-1") == set()


def prior_orders_claim():
    return make_claim(
        source_record_id="order-1",
        source_field="prior_undisputed_orders_matching_device_ip",
        establishes=E.PRIOR_UNDISPUTED_ORDERS_MATCH,
        extracted_value="3",
        normalised_value={"kind": "ENUM", "text": "3"},
    )


def test_prior_undisputed_orders_claim_verifies_and_meets_fraud_requirement():
    from nirnaya.verifier import VerificationOutcome

    rec = order_record(prior_undisputed_orders_matching_device_ip=3)
    result = run([prior_orders_claim()], [rec], make_dispute(reason_category=ReasonCategory.FRAUD))
    assert result.outcome is VerificationOutcome.PASSED


def test_prior_undisputed_orders_claim_is_blocked_under_unknown_reason():
    rec = order_record(prior_undisputed_orders_matching_device_ip=3)
    result = run(
        [prior_orders_claim()], [rec], make_dispute(reason_category=ReasonCategory.UNKNOWN)
    )
    assert checks(result, "claim-1") == {CheckName.REASON_SPECIFIC_UNDER_UNKNOWN}


def test_wrong_count_is_blocked():
    rec = order_record(prior_undisputed_orders_matching_device_ip=2)
    result = run([prior_orders_claim()], [rec], make_dispute(reason_category=ReasonCategory.FRAUD))
    assert CheckName.EXTRACTED_VALUE_MISMATCH in checks(result, "claim-1")
