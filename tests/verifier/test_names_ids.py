"""Names and identifiers appear in the cited record."""

import pytest

from nirnaya.models import EvidenceType
from nirnaya.verifier import CheckName
from tests.factories import carrier_record, make_claim
from tests.verifier.helpers import checks, order_record, run

RECORDS = [carrier_record(), order_record()]


def name_claim(name: str, raw: str = "Mr John Smith"):
    return make_claim(
        id="claim-name",
        source_record_id="order-1",
        source_field="customer_name",
        establishes=EvidenceType.ORDER_PLACED,
        extracted_value=raw,
        normalised_value={"kind": "NAME", "text": name},
    )


@pytest.mark.parametrize("name", ["John Smith", "john smith", "JOHN  SMITH", "Smith", "Mr John"])
def test_name_present_in_record(name):
    assert checks(run([name_claim(name)], RECORDS), "claim-name") == set()


@pytest.mark.parametrize("name", ["J. Smith", "Jane Smith", "Smith John", "Johnny Smith", "Jo"])
def test_name_absent_from_record_is_blocked(name):
    assert checks(run([name_claim(name)], RECORDS), "claim-name") == {CheckName.NAME_OR_ID_MISMATCH}


def test_unicode_compatibility_forms_are_normalised():
    rec = order_record(customer_name="Ｊｏｈｎ Smith")  # full-width letters
    claim = name_claim("John Smith", raw="Ｊｏｈｎ Smith")
    assert checks(run([claim], [rec]), "claim-name") == set()


def id_claim(identifier: str):
    return make_claim(
        id="claim-id",
        source_record_id="order-1",
        source_field="order_id",
        establishes=EvidenceType.ORDER_PLACED,
        extracted_value="ORD-1001",
        normalised_value={"kind": "IDENTIFIER", "text": identifier},
    )


def test_identifier_matches():
    assert checks(run([id_claim("ORD-1001")], RECORDS), "claim-id") == set()


@pytest.mark.parametrize("identifier", ["ORD-1002", "ORD-100", "ord-1001", "ORD1001"])
def test_different_identifier_is_blocked(identifier):
    assert checks(run([id_claim(identifier)], RECORDS), "claim-id") == {
        CheckName.NAME_OR_ID_MISMATCH
    }


def test_enum_value_must_match():
    claim = make_claim(normalised_value={"kind": "ENUM", "text": "IN_TRANSIT"})
    assert checks(run([claim]), "claim-1") == {CheckName.VALUE_MISMATCH}


def test_extracted_value_must_quote_the_record():
    claim = make_claim(extracted_value="Delivered")
    assert checks(run([claim]), "claim-1") == {CheckName.EXTRACTED_VALUE_MISMATCH}
