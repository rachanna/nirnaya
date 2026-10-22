"""Airwallex reason.type -> ReasonCategory, conservatively."""

import pytest

from nirnaya.models import DisputeReason, ReasonCategory
from nirnaya.models.reasons import DOCUMENTED_REASON_TYPES, REASON_TYPE_CATEGORY, category_for
from tests.factories import make_dispute

R = ReasonCategory


@pytest.mark.parametrize(
    ("reason_type", "category"),
    [
        ("FRAUDULENT", R.FRAUD),
        ("PRODUCT_NOT_RECEIVED", R.NON_RECEIPT),
        ("DUPLICATE_CHARGE", R.DUPLICATE),
        ("CANCELLED_PRODUCT", R.SUBSCRIPTION),
        ("UNKNOWN", R.UNKNOWN),
        ("UNRECOGNIZED_CHARGE", R.UNKNOWN),
        ("NOT_RECOGNIZED", R.UNKNOWN),
        ("PRODUCT_UNACCEPTABLE", R.UNKNOWN),
    ],
)
def test_mapping(reason_type, category):
    assert category_for(reason_type) is category


def test_only_documented_types_are_mapped():
    assert set(REASON_TYPE_CATEGORY) <= DOCUMENTED_REASON_TYPES


def test_undocumented_or_missing_type_is_unknown_not_an_error():
    assert category_for("SOME_NEW_TYPE_2027") is R.UNKNOWN
    assert category_for(None) is R.UNKNOWN
    assert (
        make_dispute(reason=DisputeReason(type="SOME_NEW_TYPE_2027")).reason_category is R.UNKNOWN
    )


def test_dispute_category_is_derived_from_reason_type():
    d = make_dispute(reason=DisputeReason(type="FRAUDULENT", original_code="10.4"))
    assert d.reason_category is R.FRAUD
    assert d.reason.original_code == "10.4"


def test_category_cannot_be_set_independently():
    with pytest.raises(ValueError):
        make_dispute().model_validate({**make_dispute().model_dump(), "reason_category": "FRAUD"})
