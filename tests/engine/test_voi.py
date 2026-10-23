"""Value of information: which missing evidence would flip the maths action.

p comes only from prior entries whose evidence is all present, so the only evidence that can
change the decision is the missing part of some prior entry. Each unsatisfied entry's missing
set is tested as a unit; minimal flipping sets are reported (a single type is a set of one).

Demo policy, non-receipt chargeback of 200 USD:
  no evidence    p=0.10 -> 20 - 20 - 50 - 10 = -60 (ACCEPT)
  + delivery     p=0.60 -> 120 - 20 - 50 - 10 = +40 (CHALLENGE)
"""

from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import value_of_information
from nirnaya.engine.core import evaluate
from nirnaya.models import (
    Action,
    DisputeReason,
    EvidenceType,
    PriorEntry,
    ReasonCategory,
    Stage,
)
from nirnaya.models.enums import KNOWN_REASONS
from tests.engine.helpers import run
from tests.factories import REASON_TYPE_FOR, make_dispute, make_policy, usd

E = EvidenceType


def sets(d):
    return [set(v.evidence) for v in d.voi]


def test_delivery_scan_is_worth_searching_for():
    d = run(DEMO_POLICY)
    assert d.action is Action.ACCEPT
    assert sets(d) == [{E.DELIVERY_CONFIRMED}]
    (entry,) = d.voi
    assert entry.p_if_present == D("0.60")
    assert entry.margin_if_present == D("40")
    assert entry.math_action_if_present is Action.CHALLENGE
    assert entry.final_action_if_present is Action.CHALLENGE


def test_only_minimal_flipping_sets_are_reported():
    # Delivery alone flips, so "delivery + signature" (also flipping) is not reported too.
    assert {E.DELIVERY_CONFIRMED, E.DELIVERY_SIGNATURE} not in sets(run(DEMO_POLICY))


def test_present_evidence_is_never_part_of_a_reported_set():
    d = run(DEMO_POLICY, frozenset({E.DELIVERY_CONFIRMED}))
    assert d.action is Action.CHALLENGE
    assert all(E.DELIVERY_CONFIRMED not in s for s in sets(d))


def test_evidence_that_would_not_flip_is_not_reported():
    # Signature alone completes no entry; fraud evidence is irrelevant to non-receipt.
    reported = set().union(*sets(run(DEMO_POLICY)))
    assert E.DELIVERY_SIGNATURE not in reported
    assert E.THREE_DS_AUTHENTICATED not in reported


def test_nothing_to_search_when_no_entry_would_flip():
    # 20 USD: even the best entry (p=0.80) cannot cover the costs.
    d = run(DEMO_POLICY, amount=usd("20.00"))
    assert d.action is Action.ACCEPT
    assert d.voi == ()


def test_a_challenge_never_flips_to_accept():
    # Evidence only raises p.
    assert run(DEMO_POLICY, frozenset({E.DELIVERY_CONFIRMED})).voi == ()


def test_unknown_reason_has_no_voi():
    assert run(DEMO_POLICY, reason_category=ReasonCategory.UNKNOWN).voi == ()


def test_non_automated_stage_has_no_voi():
    assert run(DEMO_POLICY, stage=Stage.PRE_ARBITRATION).voi == ()


# --- joint evidence (case S5) ----------------------------------------------------------------


def test_evidence_that_flips_only_jointly_is_found():
    # Subscription at RFI, 300 USD. Terms alone (p 0.40): G_cb 50, margin -35, no flip.
    # Terms + usage (p 0.60): G_cb 110, margin +25, flips. Neither type flips alone.
    d = run(
        DEMO_POLICY,
        reason_category=ReasonCategory.SUBSCRIPTION,
        stage=Stage.RFI,
        amount=usd("300.00"),
    )
    assert d.math_action is Action.ACCEPT
    assert sets(d) == [{E.CANCELLATION_TERMS_ACCEPTED, E.USAGE_AFTER_RENEWAL}]
    (entry,) = d.voi
    assert (entry.p_if_present, entry.margin_if_present) == (D("0.60"), D("25"))


def test_a_flip_that_needs_three_pieces_of_evidence_is_found():
    triple = frozenset({E.AVS_MATCH, E.CVC_MATCH, E.ACCOUNT_LOGIN_MATCH})
    priors = tuple(PriorEntry(reason=r, p=D("0.10")) for r in sorted(KNOWN_REASONS)) + (
        PriorEntry(reason=ReasonCategory.FRAUD, evidence=triple, p=D("0.90")),
    )
    policy = make_policy(priors=priors)
    d = run(policy, reason_category=ReasonCategory.FRAUD)
    assert sets(d) == [set(triple)]


def test_partly_present_entries_report_only_what_is_missing():
    d = run(
        DEMO_POLICY,
        frozenset({E.CANCELLATION_TERMS_ACCEPTED}),
        reason_category=ReasonCategory.SUBSCRIPTION,
        stage=Stage.RFI,
        amount=usd("300.00"),
    )
    assert sets(d) == [{E.USAGE_AFTER_RENEWAL}]


def test_pre_chargeback_voi_reports_singles_before_pairs():
    # Pre-chargeback fraud, 1000 USD: margin = max(0, 1000p - 70) - 70.
    #   3DS p .70 -> 560; prior orders .60 -> 460; login .45 -> 310; AVS+CVC .30 -> 160.
    d = run(
        DEMO_POLICY,
        reason_category=ReasonCategory.FRAUD,
        stage=Stage.PRE_CHARGEBACK,
        amount=usd("1000.00"),
    )
    assert d.margin == D("-40")
    assert sets(d) == [
        {E.THREE_DS_AUTHENTICATED},
        {E.PRIOR_UNDISPUTED_ORDERS_MATCH},
        {E.ACCOUNT_LOGIN_MATCH},
        {E.AVS_MATCH, E.CVC_MATCH},
    ]
    # Supersets of flipping sets are not reported.
    assert {E.AVS_MATCH, E.CVC_MATCH, E.ACCOUNT_LOGIN_MATCH} not in sets(d)


def test_flip_into_the_marginal_band_shows_refer_as_final_action():
    d = run(make_policy(marginal_band=D("41")))
    assert d.action is Action.ACCEPT  # margin -60 is outside the band
    (entry,) = d.voi
    assert entry.math_action_if_present is Action.CHALLENGE
    assert entry.final_action_if_present is Action.REFER


def test_voi_reported_even_when_case_is_referred_for_amount():
    d = run(make_policy(referral_amount_threshold=D("100")))
    assert d.action is Action.REFER
    assert sets(d) == [{E.DELIVERY_CONFIRMED}]


def test_standalone_voi_matches_the_decision():
    dispute = make_dispute(
        reason_category=ReasonCategory.FRAUD, stage=Stage.PRE_CHARGEBACK, amount=usd("1000.00")
    )
    standalone = value_of_information(dispute, DEMO_POLICY, frozenset(), ownership_resolved=True)
    assert (
        standalone
        == run(
            DEMO_POLICY,
            reason_category=ReasonCategory.FRAUD,
            stage=Stage.PRE_CHARGEBACK,
            amount=usd("1000.00"),
        ).voi
    )


def test_voi_serialises_sets_sorted():
    d = run(
        DEMO_POLICY,
        reason_category=ReasonCategory.SUBSCRIPTION,
        stage=Stage.RFI,
        amount=usd("300.00"),
    )
    assert d.model_dump(mode="json")["voi"][0]["evidence"] == [
        "CANCELLATION_TERMS_ACCEPTED",
        "USAGE_AFTER_RENEWAL",
    ]


# --- exactness, by brute force ---------------------------------------------------------------


def _subsets(items):
    items = sorted(items)
    for mask in range(1, 1 << len(items)):
        yield frozenset(x for i, x in enumerate(items) if mask >> i & 1)


@settings(max_examples=150, deadline=None)
@given(
    reason=st.sampled_from(sorted(KNOWN_REASONS)),
    stage=st.sampled_from([Stage.RFI, Stage.PRE_CHARGEBACK, Stage.CHARGEBACK]),
    amount=st.decimals(min_value=D("1"), max_value=5000, places=2),
    present=st.frozensets(st.sampled_from(list(EvidenceType)), max_size=4),
)
def test_voi_is_exactly_the_minimal_flipping_sets(reason, stage, amount, present):
    dispute = make_dispute(
        reason=DisputeReason(type=REASON_TYPE_FOR[reason]), stage=stage, amount=usd(amount)
    )
    kw = {"ownership_resolved": True}
    base = evaluate(dispute, DEMO_POLICY, present, **kw).math_action
    reported = {v.evidence for v in value_of_information(dispute, DEMO_POLICY, present, **kw)}

    # Every set reported really flips.
    for s in reported:
        assert evaluate(dispute, DEMO_POLICY, present | s, **kw).math_action is not base

    # Brute force: every minimal flipping subset of the reason's prior evidence is reported.
    universe = set().union(*(e.evidence for e in DEMO_POLICY.priors if e.reason is reason))
    flipping = [
        s
        for s in _subsets(universe - present)
        if evaluate(dispute, DEMO_POLICY, present | s, **kw).math_action is not base
    ]
    minimal = {s for s in flipping if not any(t < s for t in flipping)}
    assert reported == minimal

    # The single-type case agrees with the old one-type-at-a-time search.
    singles = {
        frozenset({t})
        for t in EvidenceType
        if t not in present
        and evaluate(dispute, DEMO_POLICY, present | {t}, **kw).math_action is not base
    }
    assert {s for s in reported if len(s) == 1} == singles
