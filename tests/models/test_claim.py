import pytest
from pydantic import ValidationError

from nirnaya.models import Claim, ValidationStatus

VALID = {
    "id": "claim-1",
    "case_id": "case-1",
    "merchant_id": "merchant-demo",
    "text": "Delivery confirmed by carrier: DELIVERED",
    "source_record_id": "carrier-1",
    "source_field": "status",
    "establishes": "DELIVERY_CONFIRMED",
    "extracted_value": "DELIVERED",
    "normalised_value": {"kind": "ENUM", "text": "DELIVERED"},
}


def test_valid_claim_defaults_to_pending():
    claim = Claim(**VALID)
    assert claim.validation_status is ValidationStatus.PENDING
    assert claim.contradicting_record_ids == ()
    assert claim.value_kind == "ENUM"


@pytest.mark.parametrize("field", ["source_record_id", "source_field"])
def test_claim_without_source_binding_cannot_exist(field):
    data = {k: v for k, v in VALID.items() if k != field}
    with pytest.raises(ValidationError, match=field):
        Claim(**data)


@pytest.mark.parametrize("field", ["source_record_id", "source_field"])
@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_claim_with_blank_source_binding_cannot_exist(field, blank):
    with pytest.raises(ValidationError):
        Claim(**{**VALID, field: blank})


def test_source_binding_cannot_be_none():
    with pytest.raises(ValidationError):
        Claim(**{**VALID, "source_record_id": None})


def test_money_value_requires_basis():
    with pytest.raises(ValidationError, match="basis"):
        Claim(
            **{
                **VALID,
                "normalised_value": {
                    "kind": "MONEY",
                    "money": {"amount": "1", "currency": "USD"},
                },
            }
        )


def test_unknown_value_kind_is_rejected():
    with pytest.raises(ValidationError):
        Claim(**{**VALID, "normalised_value": {"kind": "VIBES", "text": "x"}})


def test_date_value_requires_a_real_time_zone():
    with pytest.raises(ValidationError, match="time zone"):
        Claim(
            **{
                **VALID,
                "normalised_value": {"kind": "DATE", "date": "2026-02-20", "tz": "Mars/Olympus"},
            }
        )


def test_naive_datetime_value_is_rejected():
    with pytest.raises(ValidationError):
        Claim(**{**VALID, "normalised_value": {"kind": "DATETIME", "at": "2026-02-20T15:30:00"}})


def test_claim_round_trips_through_json():
    claim = Claim(
        **{
            **VALID,
            "normalised_value": {"kind": "DATETIME", "at": "2026-02-20T15:30:00-05:00"},
        }
    )
    assert Claim.model_validate_json(claim.model_dump_json()) == claim


def test_unknown_evidence_type_is_rejected():
    with pytest.raises(ValidationError):
        Claim(**{**VALID, "establishes": "VIBES"})
