"""Expected costs per action: hand-worked examples, and agreement with the engine on every case."""

from decimal import Decimal as D

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import load_cases
from nirnaya.evaluation.economics import (
    REVIEW_COST,
    accept_cost,
    challenge_cost,
    expert_action,
    price,
)
from nirnaya.models import Action, Stage

P = DEMO_POLICY  # F_pre 15, F_cb 25, F_pa 500, h 20, h_pre 0, h_rfi 0, R 50, m 10, q 0.10


def test_chargeback_costs():
    # N1: p 0.60, A 200 -> accept 200; challenge 0.4*200 + 20 + 0.1*500 = 150
    assert accept_cost(Stage.CHARGEBACK, D("200"), P).total == D("200")
    assert challenge_cost(Stage.CHARGEBACK, D("0.60"), D("200"), P).total == D("150")


def test_rfi_challenge_escalates_and_continues_as_an_expert():
    # K1: p 0.75, A 1500 at RFI. Chargeback continuation: challenge 375 + 20 + 50 = 445 < 1500.
    # Challenge = h_rfi 0 + F_cb 25 + R 50 + 445 = 520; accept = 1500 + F_rfi 0.
    fight = challenge_cost(Stage.RFI, D("0.75"), D("1500"), P)
    assert fight.total == D("520")
    assert (fight.fees, fight.counted_chargeback) == (D("25") + D("50"), D("50"))
    assert accept_cost(Stage.RFI, D("1500"), P).total == D("1500")


def test_fight_everything_keeps_fighting_a_losing_chargeback():
    # Unknown reason at RFI: p 0.10, A 200. Expert continuation accepts at chargeback (200 <
    # 180 + 20 + 50 = 250); a fight-everything policy pays 250 there.
    expert = challenge_cost(Stage.RFI, D("0.10"), D("200"), P)
    fighter = challenge_cost(Stage.RFI, D("0.10"), D("200"), P, keep_fighting=True)
    assert expert.total == D("75") + D("200")
    assert fighter.total == D("75") + D("250")


def test_pre_chargeback_accept_pays_the_resolution_fee():
    assert accept_cost(Stage.PRE_CHARGEBACK, D("200"), P).total == D("215")


def test_a_referral_costs_the_review_plus_the_reviewers_action():
    # K3: 120 USD chargeback, reviewer accepts.
    cost = price(Action.REFER, Stage.CHARGEBACK, D("0.10"), D("120"), P,
                 reviewer_action=Action.ACCEPT)  # fmt: skip
    assert cost.total == D("120") + REVIEW_COST
    assert cost.review == REVIEW_COST
    with pytest.raises(ValueError):
        price(Action.REFER, Stage.CHARGEBACK, D("0.1"), D("120"), P)


def test_costs_are_decimal_and_itemised():
    cost = challenge_cost(Stage.RFI, D("0.75"), D("1500"), P)
    for value in (cost.amount_lost, cost.handling, cost.fees, cost.counted_chargeback):
        assert isinstance(value, D)
    assert cost.total == cost.amount_lost + cost.handling + cost.fees + cost.counted_chargeback
    assert cost.parts  # a readable breakdown


PRICED = [
    c
    for c in load_cases()
    if c.expected.engine is not None
    and c.expected.engine.p is not None
    and c.expected.engine.margin is not None
    and c.dispute.stage in {Stage.RFI, Stage.PRE_CHARGEBACK, Stage.CHARGEBACK}
]


@pytest.mark.parametrize("case", PRICED, ids=lambda c: c.id)
def test_cost_difference_equals_the_engine_margin_plus_m_on_every_case(case):
    e, A, stage = case.expected.engine, case.dispute.amount.amount, case.dispute.stage
    gap = accept_cost(stage, A, P).total - challenge_cost(stage, e.p, A, P).total
    assert gap == e.margin + P.min_gain
    # And the expert action agrees with the expected maths action except inside the m hurdle.
    if e.margin > 0 or gap <= 0:
        assert expert_action(stage, e.p, A, P) is e.math_action
