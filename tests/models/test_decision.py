from decimal import Decimal as D
from typing import Any

import pytest
from pydantic import ValidationError

from nirnaya.models import AcceptReason, Action, Decision, ReasonCategory, ReferralReason, Stage


def decision(**overrides: Any) -> Decision:
    data: dict[str, Any] = {
        "case_id": "case-1",
        "merchant_id": "merchant-demo",
        "stage": Stage.CHARGEBACK,
        "reason_category": ReasonCategory.FRAUD,
        "action": Action.ACCEPT,
        "math_action": Action.ACCEPT,
        "p": D("0.1"),
        "prior_key": frozenset(),
        "g_cb": None,
        "ev_challenge": D("-50"),
        "ev_accept": D("0"),
        "margin": D("-60"),
        "reasons": ("x",),
        "referral_reasons": (),
        "inputs": {},
    }
    data.update(overrides)
    if "accept_reason" not in overrides and data["action"] is Action.ACCEPT:
        data["accept_reason"] = AcceptReason.OTHERS
    return Decision(**data)


def test_valid_decision():
    assert decision().action is Action.ACCEPT


def test_math_action_cannot_be_refer():
    with pytest.raises(ValidationError, match="never REFER"):
        decision(math_action=Action.REFER, action=Action.REFER, referral_reasons=("MARGINAL",))


def test_referral_reasons_force_refer():
    with pytest.raises(ValidationError, match="must REFER"):
        decision(referral_reasons=(ReferralReason.MARGINAL,))


def test_action_must_equal_math_action_without_referral():
    with pytest.raises(ValidationError, match="maths action"):
        decision(action=Action.CHALLENGE)


def test_unknown_reason_challenge_cannot_be_constructed():
    with pytest.raises(ValidationError, match="never auto-challenged"):
        decision(
            reason_category=ReasonCategory.UNKNOWN,
            action=Action.CHALLENGE,
            math_action=Action.CHALLENGE,
        )


def test_prior_key_serialises_sorted():
    from nirnaya.models import EvidenceType as E

    d = decision(prior_key=frozenset({E.CVC_MATCH, E.AVS_MATCH}))
    assert d.model_dump(mode="json")["prior_key"] == ["AVS_MATCH", "CVC_MATCH"]


def test_accept_requires_an_accept_reason():
    with pytest.raises(ValidationError, match="exactly when"):
        decision(accept_reason=None)


def test_non_accept_cannot_carry_an_accept_reason():
    with pytest.raises(ValidationError, match="exactly when"):
        decision(
            action=Action.REFER,
            referral_reasons=(ReferralReason.MARGINAL,),
            accept_reason=AcceptReason.OTHERS,
        )
