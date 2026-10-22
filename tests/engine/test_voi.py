"""Value of information: which single missing evidence type would flip the maths action.

Demo policy, non-receipt chargeback of 200 USD:
  no evidence    p=0.10 -> 20 - 20 - 50 - 10 = -60 (ACCEPT)
  + delivery     p=0.60 -> 120 - 20 - 50 - 10 = +40 (CHALLENGE)
"""

from decimal import Decimal as D

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import value_of_information
from nirnaya.models import Action, EvidenceType, ReasonCategory, Stage
from tests.engine.helpers import run
from tests.factories import make_dispute, make_policy, usd

E = EvidenceType


def test_delivery_scan_is_worth_searching_for():
    d = run(DEMO_POLICY)
    assert d.action is Action.ACCEPT
    assert [v.evidence_type for v in d.voi] == [E.DELIVERY_CONFIRMED]
    (entry,) = d.voi
    assert entry.p_if_present == D("0.60")
    assert entry.margin_if_present == D("40")
    assert entry.math_action_if_present is Action.CHALLENGE
    assert entry.final_action_if_present is Action.CHALLENGE


def test_present_evidence_is_not_reported():
    d = run(DEMO_POLICY, frozenset({E.DELIVERY_CONFIRMED}))
    assert d.action is Action.CHALLENGE
    assert E.DELIVERY_CONFIRMED not in [v.evidence_type for v in d.voi]


def test_evidence_that_would_not_flip_is_not_reported():
    # Signature alone does not unlock the (delivery + signature) prior; fraud evidence is
    # irrelevant to non-receipt.
    reported = {v.evidence_type for v in run(DEMO_POLICY).voi}
    assert E.DELIVERY_SIGNATURE not in reported
    assert E.THREE_DS_AUTHENTICATED not in reported


def test_nothing_to_search_when_no_single_type_flips():
    # 20 USD: even p=0.80 cannot cover the costs.
    d = run(DEMO_POLICY, amount=usd("20.00"))
    assert d.action is Action.ACCEPT
    assert d.voi == ()


def test_voi_can_flip_challenge_to_accept_never():
    # Evidence only raises p, so a CHALLENGE never flips to ACCEPT.
    d = run(DEMO_POLICY, frozenset({E.DELIVERY_CONFIRMED}))
    assert d.voi == ()


def test_unknown_reason_has_no_voi():
    assert run(DEMO_POLICY, reason_category=ReasonCategory.UNKNOWN).voi == ()


def test_non_automated_stage_has_no_voi():
    assert run(DEMO_POLICY, stage=Stage.PRE_ARBITRATION).voi == ()


def test_pre_chargeback_voi():
    # Pre-chargeback fraud, 1000 USD, demo policy:
    #   no evidence: p=0.10 -> G_cb=max(0, 100-20-50)=30; margin = 30 - (25+50-15+10) = -40
    #   + 3DS:       p=0.70 -> G_cb=630; margin = 560
    d = run(
        DEMO_POLICY,
        reason_category=ReasonCategory.FRAUD,
        stage=Stage.PRE_CHARGEBACK,
        amount=usd("1000.00"),
    )
    assert d.action is Action.ACCEPT
    assert d.margin == D("-40")
    reported = {v.evidence_type: v for v in d.voi}
    assert E.THREE_DS_AUTHENTICATED in reported
    assert E.ACCOUNT_LOGIN_MATCH in reported  # p=0.45 -> margin 310
    assert E.AVS_MATCH not in reported  # needs CVC too for the 0.30 prior


def test_flip_into_the_marginal_band_shows_refer_as_final_action():
    policy = make_policy(marginal_band=D("41"))
    d = run(policy)
    assert d.action is Action.ACCEPT  # margin -60 is outside the band
    (entry,) = d.voi
    assert entry.math_action_if_present is Action.CHALLENGE
    assert entry.final_action_if_present is Action.REFER


def test_voi_reported_even_when_case_is_referred_for_amount():
    policy = make_policy(referral_amount_threshold=D("100"))
    d = run(policy)
    assert d.action is Action.REFER
    assert [v.evidence_type for v in d.voi] == [E.DELIVERY_CONFIRMED]


def test_voi_order_is_deterministic_and_sorted():
    d = run(
        DEMO_POLICY,
        reason_category=ReasonCategory.FRAUD,
        stage=Stage.PRE_CHARGEBACK,
        amount=usd("1000.00"),
    )
    types = [v.evidence_type for v in d.voi]
    assert types == sorted(types)
    standalone = value_of_information(
        make_dispute(
            reason_category=ReasonCategory.FRAUD,
            stage=Stage.PRE_CHARGEBACK,
            amount=usd("1000.00"),
        ),
        DEMO_POLICY,
        frozenset(),
        ownership_resolved=True,
    )
    assert standalone == d.voi
