"""Airwallex AI Dispute Automation's recommendation: read from the dispute, shown beside
Nirnaya's decision, and never used to decide anything."""

from decimal import Decimal

import pytest

from nirnaya.airwallex_ai import comparison
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evidence.derive import evidence_present, unanswered_customer_contacts
from nirnaya.integrations.airwallex.mapping import dispute_from_wire
from nirnaya.models import Action, AiRecommendation
from tests.harness import CASES

WIRE = {
    "id": "dsp_1",
    "payment_intent_id": "int_1",
    "stage": "CHARGEBACK",
    "status": "REQUIRES_RESPONSE",
    "amount": Decimal("120"),
    "currency": "USD",
    "reason": {"type": "CREDIT_NOT_PROCESSED", "original_code": "13.6"},
    "created_at": "2026-10-01T00:00:00Z",
    "due_at": "2026-10-20T00:00:00Z",
}


def wire(ai):
    return dispute_from_wire({**WIRE, "ai_dispute_automation": ai}, merchant_id="m")


def test_an_available_recommendation_is_read_with_its_evidence():
    d = wire(
        {
            "status": "AVAILABLE",
            "recommendation": {
                "action": "Challenge",
                "evidence_to_submit": ["refund_policy_file_ids", "customer_communication_file_ids"],
            },
        }
    )
    rec = d.ai_recommendation
    assert (rec.status, rec.action, rec.simulated) == ("AVAILABLE", Action.CHALLENGE, False)
    assert rec.evidence_to_submit == ("refund_policy_file_ids", "customer_communication_file_ids")
    assert (
        wire(
            {"status": "AVAILABLE", "recommendation": {"action": "Accept"}}
        ).ai_recommendation.action
        is Action.ACCEPT
    )


def test_unavailable_absent_and_unknown_values_degrade_safely():
    off = wire({"status": "UNAVAILABLE", "unavailable_reason": "DISABLED"}).ai_recommendation
    assert (off.status, off.unavailable_reason, off.action) == ("UNAVAILABLE", "DISABLED", None)
    assert dispute_from_wire(WIRE, merchant_id="m").ai_recommendation is None
    assert wire("garbage").ai_recommendation is None
    odd = wire({"status": "AVAILABLE", "recommendation": {"action": "Escalate"}}).ai_recommendation
    assert (
        odd.action is None and odd.action_raw == "Escalate"
    )  # an unknown value is kept, not guessed


@pytest.mark.parametrize("case_id", ["K1", "K2", "K3", "N1", "F1", "A5"])
@pytest.mark.parametrize("action", [Action.ACCEPT, Action.CHALLENGE])
def test_the_engine_ignores_the_recommendation(case_id, action):
    case = CASES[case_id]
    records = case.evidence_records()
    rec = AiRecommendation(status="AVAILABLE", action=action, simulated=True)

    def run(dispute):
        return decide(
            dispute,
            DEMO_POLICY,
            evidence_present(records, dispute),
            ownership_resolved=case.ownership_resolved,
            unanswered_contact_ids=unanswered_customer_contacts(records, dispute),
        )

    plain = run(case.dispute)
    advised = run(case.dispute.model_copy(update={"ai_recommendation": rec}))
    assert (advised.action, advised.margin, advised.reasons, advised.referral_reasons) == (
        plain.action,
        plain.margin,
        plain.reasons,
        plain.referral_reasons,
    )


def test_comparison_states_agreement_disagreement_and_referral():
    challenge = AiRecommendation(status="AVAILABLE", action=Action.CHALLENGE)
    assert comparison(challenge, Action.CHALLENGE, ()).agree is True
    split = comparison(
        AiRecommendation(status="AVAILABLE", action=Action.ACCEPT), Action.CHALLENGE, ()
    )
    assert split.agree is False and "Accept" in split.text and "CHALLENGE" in split.text
    refer = comparison(challenge, Action.REFER, ("UNANSWERED_CUSTOMER_CONTACT",))
    assert refer.agree is False and "UNANSWERED_CUSTOMER_CONTACT" in refer.text
    off = comparison(
        AiRecommendation(status="UNAVAILABLE", unavailable_reason="NOT_SUPPORTED"), Action.REFER, ()
    )
    assert off.agree is None and "NOT_SUPPORTED" in off.text
    assert comparison(None, Action.ACCEPT, ()).agree is None
    sim = comparison(
        AiRecommendation(status="AVAILABLE", action=Action.ACCEPT, simulated=True),
        Action.ACCEPT,
        (),
    )
    assert sim.simulated and "SIMULATED" in sim.text


def test_the_offline_kit_shows_labelled_simulated_recommendations():
    from nirnaya.console.session import DemoSession
    from nirnaya.console.views import kit_card

    s = DemoSession.offline()
    s.run_kit()
    k1, k2, k3 = (kit_card(s, k).cycles[0].airwallex_ai for k in ("K1", "K2", "K3"))
    assert k1.agree is None and "NOT_SUPPORTED" in k1.text  # RFI: the docs say chargeback only
    assert k2.agree is True and k2.simulated
    assert k3.agree is False and "UNANSWERED_CUSTOMER_CONTACT" in k3.text
    s.reject_k1()
    k1b = kit_card(s, "K1").cycles[1].airwallex_ai
    assert k1b.agree is True and k1b.simulated  # at CHARGEBACK the recommendation is available
