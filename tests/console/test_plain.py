"""Plain-language wording for the console: verdicts, reasons, lookups and writes in words a
person outside payments can follow. Wording only; every decision comes from the engine."""

from nirnaya.console.plain import (
    REASON_TEXT,
    decision_sentence,
    fact_text,
    lookup_sentence,
    reason_sentences,
    stage_name,
    status_text,
    verdict,
    write_sentence,
)
from nirnaya.console.views import DecisionView
from nirnaya.models.decision import ReferralReason


def view(action, *, margin=None, accept_reason=None, reasons=()):
    return DecisionView(action=action, accept_reason=accept_reason, p="0.60", margin=margin,
                        math_action=action, referral_reasons=reasons, reasons=())  # fmt: skip


def test_each_action_has_a_plain_verdict_and_colour():
    assert verdict("CHALLENGE") == ("Fight it", "green")
    assert verdict("ACCEPT") == ("Accept it", "orange")
    assert verdict("REFER") == ("A person decides", "blue")


def test_the_why_is_one_plain_sentence_with_the_engines_own_number():
    assert decision_sentence(view("CHALLENGE", margin="745.00")) == (
        "Fighting is worth about 745.00 USD more than accepting, after fees and the risk of losing."
    )
    low = view("ACCEPT", margin="-78.50", accept_reason="LOW_VALUE_TRANSACTION")
    assert decision_sentence(low) == "Fighting would cost more than it could win back."
    refer = view("REFER", reasons=("UNANSWERED_CUSTOMER_CONTACT",))
    assert decision_sentence(refer) == REASON_TEXT["UNANSWERED_CUSTOMER_CONTACT"]


def test_every_engine_referral_reason_has_a_sentence():
    assert {r.value for r in ReferralReason} <= set(REASON_TEXT)
    assert reason_sentences(["SOMETHING_NEW"]) == ["SOMETHING_NEW"]  # unknown codes stay visible


def test_lookups_and_writes_read_as_sentences():
    assert lookup_sentence("get_order", "ORD-K1", ["K1-order"]) == "Order ORD-K1: found"
    assert lookup_sentence("get_helpdesk_contacts", "CUST-K3", ["e1", "e2"]) == (
        "Support messages from CUST-K3: 2 found"
    )
    assert lookup_sentence("get_login_history", "CUST-K1", []) == (
        "Login history for CUST-K1: nothing found"
    )
    assert write_sentence("CHALLENGE", "CONFIRMED", dry_run=False, customer_info=True) == (
        "Challenge sent; Airwallex confirmed it (with the customer's device and IP)"
    )
    dry = write_sentence("ACCEPT", "DRY_RUN", dry_run=True)
    assert dry == "Accept prepared (dry run: not sent)"
    failed = write_sentence("CHALLENGE", "FAILED", dry_run=False)
    assert failed == "Challenge failed; a person was told"
    assert stage_name("RFI") == "information request" and stage_name("CHARGEBACK") == "chargeback"
    assert status_text("CHARGEBACK REQUIRES_RESPONSE") == "chargeback, requires response"
    assert fact_text("DELIVERY_SIGNATURE") == "the customer signed for the delivery"
