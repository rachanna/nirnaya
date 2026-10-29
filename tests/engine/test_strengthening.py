"""Strengthening: once the case is worth fighting, which missing evidence would raise p.

Value of information asks what could *flip* the decision; this asks what would make a fight
stronger (a higher prior, a larger margin) without changing it. Code fetches it before the
decision is recorded (architecture 8.2), so the numbers include what the response will cite.

Demo policy, fraud at RFI, 1500 USD (kit case 1):
  prior orders            p=0.60 -> G_cb 900 - 70 = 830; margin 830 - 85 = 745
  + signed delivery       p=0.75 -> G_cb 1125 - 70 = 1055; margin 970
  + 3-D Secure instead    p=0.70
"""

from decimal import Decimal as D

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import strengthening_sets
from nirnaya.engine.core import evaluate
from nirnaya.models import EvidenceType, PriorEntry, ReasonCategory, Stage
from nirnaya.models.enums import KNOWN_REASONS
from tests.factories import make_dispute, make_policy, usd

E = EvidenceType
CASE1 = frozenset({E.PRIOR_UNDISPUTED_ORDERS_MATCH, E.CVC_MATCH})


def fraud(**overrides):
    overrides.setdefault("reason_category", ReasonCategory.FRAUD)
    overrides.setdefault("stage", Stage.RFI)
    overrides.setdefault("amount", usd("1500.00"))
    return make_dispute(**overrides)


def strengthen(dispute, present, policy=DEMO_POLICY):
    return strengthening_sets(dispute, policy, present, ownership_resolved=True)


def test_case_1_signed_delivery_raises_the_odds_and_the_margin():
    entries = strengthen(fraud(), CASE1)
    first = entries[0]
    assert set(first.evidence) == {E.DELIVERY_SIGNATURE}
    assert first.p_if_present == D("0.75")
    assert first.margin_if_present == D("970.0000")


def test_every_higher_entry_is_reported_strongest_gain_first():
    # 3-D Secure (p 0.70) is engine-level evidence too; tools decide later what is fetchable.
    assert [set(e.evidence) for e in strengthen(fraud(), CASE1)] == [
        {E.DELIVERY_SIGNATURE},
        {E.THREE_DS_AUTHENTICATED},
    ]


def test_entries_that_would_not_raise_p_are_ignored():
    # Delivery confirmed (p 0.60): only delivery + signature (0.80) is higher.
    present = frozenset({E.DELIVERY_CONFIRMED})
    assert [set(e.evidence) for e in strengthen(make_dispute(), present)] == [
        {E.DELIVERY_SIGNATURE}
    ]


def test_nothing_to_add_at_the_strongest_entry():
    present = frozenset({E.DELIVERY_CONFIRMED, E.DELIVERY_SIGNATURE})
    assert strengthen(make_dispute(), present) == ()


def test_present_evidence_is_never_part_of_a_reported_set():
    for entry in strengthen(fraud(), CASE1):
        assert not entry.evidence & CASE1


def test_a_set_is_dropped_when_part_of_it_gives_as_much():
    # {A} gives 0.80 and {A, B} only 0.70: B adds nothing, so {A, B} is not reported.
    bases = tuple(PriorEntry(reason=r, p=D("0.10")) for r in sorted(KNOWN_REASONS))
    policy = make_policy(
        priors=bases
        + (
            PriorEntry(
                reason=ReasonCategory.NON_RECEIPT, evidence={E.DELIVERY_CONFIRMED}, p=D("0.80")
            ),
            PriorEntry(
                reason=ReasonCategory.NON_RECEIPT,
                evidence={E.DELIVERY_CONFIRMED, E.DELIVERY_SIGNATURE},
                p=D("0.70"),
            ),
        )
    )
    assert [set(e.evidence) for e in strengthen(make_dispute(), frozenset(), policy)] == [
        {E.DELIVERY_CONFIRMED}
    ]


def test_a_larger_set_stays_when_it_raises_p_further():
    # From nothing: delivery alone (0.60) and delivery + signature (0.80) both raise p.
    sets = [set(e.evidence) for e in strengthen(make_dispute(), frozenset())]
    assert sets == [{E.DELIVERY_CONFIRMED}, {E.DELIVERY_CONFIRMED, E.DELIVERY_SIGNATURE}]


def test_unknown_reason_and_non_automated_stage_have_none():
    assert strengthen(make_dispute(reason_category=ReasonCategory.UNKNOWN), frozenset()) == ()
    assert strengthen(fraud(stage=Stage.PRE_ARBITRATION), CASE1) == ()


def test_each_entry_matches_the_engine_on_that_evidence():
    dispute = fraud()
    for entry in strengthen(dispute, CASE1):
        alt = evaluate(dispute, DEMO_POLICY, CASE1 | entry.evidence, ownership_resolved=True)
        assert (alt.p, alt.margin) == (entry.p_if_present, entry.margin_if_present)


def test_deterministic():
    assert strengthen(fraud(), CASE1) == strengthen(fraud(), CASE1)
