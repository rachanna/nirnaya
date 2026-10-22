"""Dates match after time-zone normalisation.

carrier-1 delivered_at = 2026-02-20T15:30:00-05:00 = 2026-02-20T20:30:00Z.
"""

from datetime import UTC, datetime, timedelta, timezone

from nirnaya.verifier import CheckName
from tests.factories import carrier_record, make_claim
from tests.verifier.helpers import checks, run


def datetime_claim(at: datetime, **overrides):
    data = {
        "id": "claim-dt",
        "source_field": "delivered_at",
        "extracted_value": "2026-02-20T15:30:00-05:00",
        "normalised_value": {"kind": "DATETIME", "at": at},
    }
    data.update(overrides)
    return make_claim(**data)


def date_claim(date: str, tz: str, **overrides):
    data = {
        "id": "claim-d",
        "source_field": "delivered_at",
        "extracted_value": "2026-02-20T15:30:00-05:00",
        "normalised_value": {"kind": "DATE", "date": date, "tz": tz},
    }
    data.update(overrides)
    return make_claim(**data)


def test_same_instant_in_utc_matches():
    result = run([datetime_claim(datetime(2026, 2, 20, 20, 30, tzinfo=UTC))])
    assert checks(result, "claim-dt") == set()


def test_same_instant_in_another_offset_matches():
    ist = timezone(timedelta(hours=5, minutes=30))
    result = run([datetime_claim(datetime(2026, 2, 21, 2, 0, tzinfo=ist))])
    assert checks(result, "claim-dt") == set()


def test_wall_clock_time_without_offset_conversion_is_blocked():
    # 15:30 read as if it were UTC: a classic time-zone slip.
    result = run([datetime_claim(datetime(2026, 2, 20, 15, 30, tzinfo=UTC))])
    assert checks(result, "claim-dt") == {CheckName.DATE_MISMATCH}


def test_naive_record_timestamp_is_blocked():
    rec = carrier_record(delivered_at="2026-02-20T15:30:00")
    claim = datetime_claim(
        datetime(2026, 2, 20, 15, 30, tzinfo=UTC), extracted_value="2026-02-20T15:30:00"
    )
    result = run([claim], [rec])
    assert checks(result, "claim-dt") == {CheckName.DATE_MISMATCH}


def test_unparseable_record_timestamp_is_blocked():
    rec = carrier_record(delivered_at="last Tuesday")
    claim = datetime_claim(datetime(2026, 2, 20, tzinfo=UTC), extracted_value="last Tuesday")
    result = run([claim], [rec])
    assert checks(result, "claim-dt") == {CheckName.DATE_MISMATCH}


def test_date_in_the_delivery_time_zone_matches():
    result = run([date_claim("2026-02-20", "America/New_York")])
    assert checks(result, "claim-d") == set()


def test_date_depends_on_the_stated_time_zone():
    # 20:30Z is already 21 February in Singapore.
    assert checks(run([date_claim("2026-02-21", "Asia/Singapore")]), "claim-d") == set()
    assert checks(run([date_claim("2026-02-20", "Asia/Singapore")]), "claim-d") == {
        CheckName.DATE_MISMATCH
    }


def test_date_only_record_compares_dates_directly():
    rec = carrier_record(delivered_at="2026-02-20")
    claim = date_claim("2026-02-20", "Europe/London", extracted_value="2026-02-20")
    assert checks(run([claim], [rec]), "claim-d") == set()
    wrong = date_claim("2026-02-19", "Europe/London", extracted_value="2026-02-20")
    assert checks(run([wrong], [rec]), "claim-d") == {CheckName.DATE_MISMATCH}


def test_date_only_record_cannot_support_an_instant():
    rec = carrier_record(delivered_at="2026-02-20")
    claim = datetime_claim(datetime(2026, 2, 20, tzinfo=UTC), extracted_value="2026-02-20")
    assert checks(run([claim], [rec]), "claim-dt") == {CheckName.DATE_MISMATCH}
