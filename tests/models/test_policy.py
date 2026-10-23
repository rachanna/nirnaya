from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.models import EvidenceType, MerchantPolicy, PriorEntry, ReasonCategory
from tests.factories import make_policy, policy_kwargs


def test_demo_policy_is_valid_and_labelled_as_assumption():
    assert "ASSUMPTION" in DEMO_POLICY.assumptions_note


def test_policy_round_trips_through_json():
    assert MerchantPolicy.model_validate_json(DEMO_POLICY.model_dump_json()) == DEMO_POLICY


@pytest.mark.parametrize("p", ["-0.01", "1.01"])
def test_probability_out_of_range_is_rejected(p):
    with pytest.raises(ValidationError):
        make_policy(escalation_probability=D(p))
    with pytest.raises(ValidationError):
        make_policy(unknown_reason_prior=D(p))
    with pytest.raises(ValidationError):
        PriorEntry(reason=ReasonCategory.FRAUD, p=D(p))


def test_probability_bounds_are_inclusive():
    zero_priors = tuple(e.model_copy(update={"p": D(0)}) for e in DEMO_POLICY.priors)
    make_policy(escalation_probability=D("1"), unknown_reason_prior=D("0"), priors=zero_priors)
    make_policy(escalation_probability=D("0"), unknown_reason_prior=D("1"))


# --- p + q <= 1: keeping A and losing at pre-arbitration are disjoint events --------------


def test_prior_plus_q_above_one_is_rejected():
    # The demo's highest prior is 0.85 (duplicate with a refund).
    with pytest.raises(ValidationError, match="disjoint"):
        make_policy(escalation_probability=D("0.16"))


def test_prior_plus_q_equal_to_one_is_accepted():
    make_policy(escalation_probability=D("0.15"))


def test_unknown_prior_plus_q_above_one_is_rejected():
    with pytest.raises(ValidationError, match="unknown_reason_prior"):
        make_policy(unknown_reason_prior=D("0.95"), escalation_probability=D("0.06"))


def test_unknown_prior_plus_q_equal_to_one_is_accepted():
    make_policy(unknown_reason_prior=D("0.85"), escalation_probability=D("0.15"))


def test_float_probability_is_rejected():
    with pytest.raises(ValidationError, match="never a float"):
        make_policy(escalation_probability=0.1)


@pytest.mark.parametrize(
    "field",
    [
        "fee_pre_resolution",
        "fee_chargeback",
        "fee_pre_arbitration",
        "handling_cost",
        "handling_cost_pre",
        "counted_chargeback_cost",
        "min_gain",
        "referral_amount_threshold",
        "marginal_band",
    ],
)
def test_negative_costs_are_rejected(field):
    with pytest.raises(ValidationError):
        make_policy(**{field: D("-1")})


def test_h_pre_defaults_to_zero():
    data = policy_kwargs()
    del data["handling_cost_pre"]
    assert MerchantPolicy(**data).handling_cost_pre == 0


def test_duplicate_prior_keys_are_rejected():
    dup = PriorEntry(reason=ReasonCategory.FRAUD, p=D("0.2"))
    with pytest.raises(ValidationError, match="duplicate"):
        make_policy(priors=(*DEMO_POLICY.priors, dup))


def test_missing_base_prior_is_rejected():
    priors = tuple(
        e
        for e in DEMO_POLICY.priors
        if not (e.reason is ReasonCategory.DUPLICATE and not e.evidence)
    )
    with pytest.raises(ValidationError, match="no base prior"):
        make_policy(priors=priors)


def test_unknown_reason_in_priors_table_is_rejected():
    entry = PriorEntry(
        reason=ReasonCategory.UNKNOWN,
        evidence=frozenset({EvidenceType.DELIVERY_CONFIRMED}),
        p=D("0.9"),
    )
    with pytest.raises(ValidationError, match="unknown_reason_prior"):
        make_policy(priors=(*DEMO_POLICY.priors, entry))


def test_unknown_policy_currency_is_rejected():
    with pytest.raises(ValidationError):
        make_policy(currency="ZZZ")
