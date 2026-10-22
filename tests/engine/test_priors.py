from decimal import Decimal as D

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine.priors import lookup_prior
from nirnaya.models import EvidenceType, PriorEntry, ReasonCategory
from tests.factories import make_policy

E = EvidenceType
R = ReasonCategory


def test_base_entry_when_no_evidence():
    assert lookup_prior(DEMO_POLICY, R.NON_RECEIPT, frozenset()) == (D("0.10"), frozenset())


def test_exact_entry():
    ev = frozenset({E.DELIVERY_CONFIRMED})
    assert lookup_prior(DEMO_POLICY, R.NON_RECEIPT, ev) == (D("0.60"), ev)


def test_max_over_configured_subsets():
    present = frozenset({E.AVS_MATCH, E.CVC_MATCH, E.ACCOUNT_LOGIN_MATCH, E.THREE_DS_AUTHENTICATED})
    p, key = lookup_prior(DEMO_POLICY, R.FRAUD, present)
    assert p == D("0.70")
    assert key == {E.THREE_DS_AUTHENTICATED}


def test_entry_needing_absent_evidence_is_not_used():
    # {DELIVERY_CONFIRMED, DELIVERY_SIGNATURE} needs both; signature alone falls back to base.
    p, key = lookup_prior(DEMO_POLICY, R.NON_RECEIPT, frozenset({E.DELIVERY_SIGNATURE}))
    assert (p, key) == (D("0.10"), frozenset())


def test_adding_evidence_never_lowers_p():
    # A superset entry with a LOWER p must not override a subset entry with a higher p.
    priors = (
        *DEMO_POLICY.priors,
        PriorEntry(
            reason=R.NON_RECEIPT,
            evidence=frozenset({E.DELIVERY_CONFIRMED, E.ORDER_PLACED}),
            p=D("0.20"),
        ),
    )
    policy = make_policy(priors=priors)
    p, key = lookup_prior(policy, R.NON_RECEIPT, frozenset({E.DELIVERY_CONFIRMED, E.ORDER_PLACED}))
    assert (p, key) == (D("0.60"), {E.DELIVERY_CONFIRMED})


def test_irrelevant_evidence_does_not_change_p():
    base = lookup_prior(DEMO_POLICY, R.NON_RECEIPT, frozenset({E.DELIVERY_CONFIRMED}))
    noisy = lookup_prior(
        DEMO_POLICY, R.NON_RECEIPT, frozenset({E.DELIVERY_CONFIRMED, E.AVS_MATCH, E.CVC_MATCH})
    )
    assert base == noisy


def test_other_reasons_entries_are_not_used():
    p, _ = lookup_prior(DEMO_POLICY, R.DUPLICATE, frozenset({E.DELIVERY_CONFIRMED}))
    assert p == D("0.10")


def test_equal_p_tie_breaks_deterministically_to_the_larger_set():
    priors = (
        *DEMO_POLICY.priors,
        PriorEntry(reason=R.DUPLICATE, evidence=frozenset({E.ORDER_PLACED}), p=D("0.65")),
    )
    policy = make_policy(priors=priors)
    present = frozenset({E.DISTINCT_TRANSACTIONS, E.ORDER_PLACED})
    p, key = lookup_prior(policy, R.DUPLICATE, present)
    assert p == D("0.65")
    # Same size, same p: the tie-break is on sorted names, never on set iteration order.
    assert key == {E.ORDER_PLACED}
    assert all(lookup_prior(policy, R.DUPLICATE, present) == (p, key) for _ in range(20))


def test_unknown_reason_uses_conservative_prior_and_ignores_evidence():
    everything = frozenset(EvidenceType)
    assert lookup_prior(DEMO_POLICY, R.UNKNOWN, everything) == (
        DEMO_POLICY.unknown_reason_prior,
        None,
    )
