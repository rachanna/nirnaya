"""Claim text must be the deterministic rendering of its fact and value."""

from nirnaya.verifier import CheckName
from tests.factories import make_claim
from tests.verifier.helpers import checks, run


def test_rendered_text_passes():
    claim = make_claim()
    assert claim.text == "Delivery confirmed by carrier: DELIVERED"
    assert checks(run([claim]), "claim-1") == set()


def test_embellished_text_is_blocked():
    claim = make_claim(text="Delivery confirmed by carrier: DELIVERED to the front door")
    assert checks(run([claim]), "claim-1") == {CheckName.TEXT_MISMATCH}


def test_whitespace_or_case_drift_is_blocked():
    for text in (
        "Delivery confirmed by carrier:  DELIVERED",
        "delivery confirmed by carrier: DELIVERED",
    ):
        assert checks(run([make_claim(text=text)]), "claim-1") == {CheckName.TEXT_MISMATCH}


def test_text_for_another_fact_is_blocked():
    claim = make_claim(text="Delivery signed for by: DELIVERED")
    assert checks(run([claim]), "claim-1") == {CheckName.TEXT_MISMATCH}
