from decimal import ROUND_DOWN
from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.models import Action, EvidenceType, ReasonCategory, ReferralReason, Stage
from tests.engine.helpers import run
from tests.factories import make_dispute, make_policy, usd

UNKNOWN = ReasonCategory.UNKNOWN


def generous_policy(**overrides):
    """Costs so low that the maths would challenge almost anything."""
    data = {
        "unknown_reason_prior": D("0.9"),
        "handling_cost": D("0"),
        "fee_pre_arbitration": D("0"),
        "fee_chargeback": D("0"),
        "counted_chargeback_cost": D("0"),
        "min_gain": D("0"),
        "marginal_band": D("0"),
    }
    data.update(overrides)
    return make_policy(**data)


def test_unknown_reason_maths_challenge_is_referred():
    d = run(generous_policy(), reason_category=UNKNOWN)
    assert d.math_action is Action.CHALLENGE
    assert d.action is Action.REFER
    assert d.referral_reasons == (ReferralReason.UNKNOWN_REASON_CHALLENGE,)


def test_unknown_reason_maths_accept_is_automated():
    d = run(DEMO_POLICY, reason_category=UNKNOWN)
    assert d.math_action is Action.ACCEPT
    assert d.action is Action.ACCEPT


def test_unknown_reason_ignores_evidence():
    none = run(DEMO_POLICY, reason_category=UNKNOWN)
    all_ = run(DEMO_POLICY, frozenset(EvidenceType), reason_category=UNKNOWN)
    assert none.p == all_.p == DEMO_POLICY.unknown_reason_prior
    assert none.margin == all_.margin
    assert all_.prior_key is None


def test_unknown_reason_pre_chargeback_never_challenges():
    d = run(generous_policy(fee_pre_resolution=D("100")), reason_category=UNKNOWN,
            stage=Stage.PRE_CHARGEBACK)  # fmt: skip
    assert d.math_action is Action.CHALLENGE
    assert d.action is Action.REFER


money = st.decimals(min_value=0, max_value=5000, places=2)
prob = st.decimals(min_value=0, max_value=1, places=3)


@settings(max_examples=300, deadline=None)
@given(
    p=prob,
    q_share=prob,
    amount=st.decimals(min_value=D("0.01"), max_value=100000, places=2),
    f_pre=money,
    f_cb=money,
    f_pa=money,
    h=money,
    h_pre=money,
    r=money,
    m=money,
    band=money,
    threshold=st.decimals(min_value=0, max_value=200000, places=2),
    stage=st.sampled_from([Stage.PRE_CHARGEBACK, Stage.CHARGEBACK]),
    evidence=st.frozensets(st.sampled_from(list(EvidenceType))),
    ownership=st.booleans(),
)
def test_unknown_reason_never_auto_challenges(
    p,
    q_share,
    amount,
    f_pre,
    f_cb,
    f_pa,
    h,
    h_pre,
    r,
    m,
    band,
    threshold,
    stage,
    evidence,
    ownership,
):
    # q is drawn as a share of the room left by the largest p, so p + q <= 1 always holds.
    largest_p = max([p, *(e.p for e in DEMO_POLICY.priors)])
    q = ((1 - largest_p) * q_share).quantize(D("0.001"), rounding=ROUND_DOWN)
    policy = make_policy(
        unknown_reason_prior=p,
        escalation_probability=q,
        fee_pre_resolution=f_pre,
        fee_chargeback=f_cb,
        fee_pre_arbitration=f_pa,
        handling_cost=h,
        handling_cost_pre=h_pre,
        counted_chargeback_cost=r,
        min_gain=m,
        marginal_band=band,
        referral_amount_threshold=threshold,
    )
    d = decide(
        make_dispute(reason_category=UNKNOWN, stage=stage, amount=usd(amount)),
        policy,
        evidence,
        ownership_resolved=ownership,
    )
    assert d.action is not Action.CHALLENGE
    assert all(v.final_action_if_present is not Action.CHALLENGE for v in d.voi)
