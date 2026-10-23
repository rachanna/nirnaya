import pytest

from nirnaya.models import CardBrand, DisputeStatus, Money, Stage
from nirnaya.orchestrator.deferral import closed_reason, deferral_reason
from tests.factories import make_dispute, usd


def test_full_refund_is_deferred():
    assert "auto-defends" in deferral_reason(make_dispute(refunded_amount=usd("200.00")), None)


def test_partial_refund_is_not_deferred():
    assert deferral_reason(make_dispute(refunded_amount=usd("50.00")), None) is None


def test_arbitration_is_deferred():
    assert deferral_reason(make_dispute(stage=Stage.ARBITRATION), None)


def test_non_amex_pre_arbitration_is_deferred_amex_is_not():
    assert deferral_reason(make_dispute(stage=Stage.PRE_ARBITRATION), None)
    amex = make_dispute(stage=Stage.PRE_ARBITRATION, card_brand=CardBrand.AMERICAN_EXPRESS)
    assert deferral_reason(amex, None) is None


@pytest.mark.parametrize(("amount", "deferred"), [("59.99", True), ("60.00", False)])
def test_pre_chargeback_below_threshold_is_deferred(amount, deferred):
    d = make_dispute(stage=Stage.PRE_CHARGEBACK, amount=usd(amount))
    assert (deferral_reason(d, usd("60.00")) is not None) is deferred


def test_threshold_in_another_currency_is_not_applied():
    d = make_dispute(stage=Stage.PRE_CHARGEBACK, amount=usd("10.00"))
    assert deferral_reason(d, Money(amount="60", currency="EUR")) is None


def test_ordinary_chargeback_is_not_deferred():
    assert deferral_reason(make_dispute(), usd("60.00")) is None


@pytest.mark.parametrize("status", ["ACCEPTED", "WON", "LOST", "REVERSED", "EXPIRED"])
def test_terminal_statuses_are_closed(status):
    assert closed_reason(make_dispute(status=status))


@pytest.mark.parametrize("status", ["REQUIRES_RESPONSE", "CHALLENGED", "PENDING_CLOSURE"])
def test_open_statuses_are_not_closed(status):
    assert closed_reason(make_dispute(status=DisputeStatus(status))) is None
