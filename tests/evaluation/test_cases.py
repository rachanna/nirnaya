"""The evaluation cases: well formed, covering, and agreeing with the deterministic core.

Expectations come from eval/EXPERT_POLICY.md, worked by hand. A failure here means the
engine or verifier disagrees with the expert policy: investigate which one is wrong; do
not edit the expectation to match the code.
"""

from decimal import Decimal

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evaluation.cases import Route, load_cases, synthetic_world
from nirnaya.evidence.derive import (
    contradictory_record_ids,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.models import ReasonCategory, Stage
from nirnaya.models.enums import AUTOMATED_STAGES, KNOWN_REASONS
from nirnaya.verifier import verify

CASES = load_cases()
BY_ID = {c.id: c for c in CASES}


def ids(case):
    return case.id


# --- Shape and coverage -------------------------------------------------------------------


def test_there_are_about_thirty_or_more_cases():
    assert len(CASES) >= 30


def test_every_case_is_for_the_demo_merchant_and_policy():
    assert {c.dispute.merchant_id for c in CASES} == {DEMO_POLICY.merchant_id}


def test_kit4_cases_are_present():
    assert {"K1", "K1B", "K2", "K2C", "K3"} <= {c.id for c in CASES if "kit4" in c.tags}


@pytest.mark.parametrize("reason", sorted(KNOWN_REASONS))
def test_every_known_reason_at_chargeback_and_an_earlier_stage(reason):
    stages = {c.dispute.stage for c in CASES if c.dispute.reason_category is reason}
    assert Stage.CHARGEBACK in stages
    assert stages & {Stage.RFI, Stage.PRE_CHARGEBACK}


def test_unknown_reason_is_covered_at_rfi_and_chargeback():
    stages = {c.dispute.stage for c in CASES if c.dispute.reason_category is ReasonCategory.UNKNOWN}
    assert {Stage.RFI, Stage.CHARGEBACK} <= stages


def test_every_automated_stage_and_every_route_is_covered():
    assert {c.dispute.stage for c in CASES} >= AUTOMATED_STAGES
    assert {c.expected.route for c in CASES} == set(Route)


def test_adversarial_and_boundary_cases_are_present():
    tagged = lambda t: {c.id for c in CASES if t in c.tags}  # noqa: E731
    assert len(tagged("adversarial")) >= 6
    assert tagged("marginal") and tagged("boundary") and tagged("low_value")
    assert tagged("contradiction") and tagged("unanswered_contact") and tagged("prompt_injection")
    assert tagged("tenant_isolation") and tagged("deferred")


def test_offline_only_cases_are_exactly_the_pre_chargeback_ones_needing_a_response():
    for c in CASES:
        needs_response = (
            c.dispute.stage is Stage.PRE_CHARGEBACK and c.expected.route is not Route.DEFER
        )
        assert ("offline_only" in c.tags) is needs_response, c.id


def test_every_case_has_a_rationale_with_its_arithmetic_or_reason():
    for c in CASES:
        assert len(c.expected.rationale) > 30, c.id


# --- Agreement with the deterministic core ------------------------------------------------


def decide_case(case):
    records = case.evidence_records()
    return decide(
        case.dispute,
        DEMO_POLICY,
        evidence_present(records, case.dispute),
        ownership_resolved=case.ownership_resolved,
        contradictory_record_ids=contradictory_record_ids(records, case.dispute),
        unanswered_contact_ids=unanswered_customer_contacts(records, case.dispute),
    )


@pytest.mark.parametrize("case", [c for c in CASES if c.expected.engine], ids=ids)
def test_engine_agrees_with_the_expert_policy(case):
    exp = case.expected.engine
    d = decide_case(case)
    assert d.action is exp.action
    assert d.math_action is exp.math_action
    assert d.accept_reason is exp.accept_reason
    assert d.referral_reasons == exp.referral_reasons
    if exp.p is not None:
        assert d.p == exp.p
    if exp.margin is not None:
        assert d.margin == exp.margin


@pytest.mark.parametrize("case", [c for c in CASES if c.expected.verification], ids=ids)
def test_verifier_agrees_with_the_expert_policy(case):
    exp = case.expected.verification
    result = verify(case.dispute, case.claim_objects(), case.evidence_records())
    assert result.outcome is exp.outcome
    for claim_result in result.claims:
        expected_checks = set(exp.failed_checks.get(claim_result.claim.id, ()))
        assert {f.check for f in claim_result.failures} == expected_checks, claim_result.claim.id
    assert {f.check for f in result.case_failures} == set(exp.case_failures)
    if exp.counterevidence_ids is not None:
        assert sorted({ce.record_id for ce in result.counterevidence}) == sorted(
            exp.counterevidence_ids
        )


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c.expected.route is Route.CHALLENGE and c.expected.verification],
    ids=ids,
)
def test_challenges_are_still_supported_by_verified_facts_alone(case):
    # Architecture section 6: re-deciding on only the verified facts must still challenge.
    result = verify(case.dispute, case.claim_objects(), case.evidence_records())
    recheck = decide(
        case.dispute,
        DEMO_POLICY,
        result.established,
        ownership_resolved=case.ownership_resolved,
    )
    assert recheck.action is case.expected.engine.action


def test_routes_are_consistent_with_engine_and_verification():
    for c in CASES:
        exp = c.expected
        if exp.route is Route.DEFER:
            continue
        blocked = exp.verification is not None and exp.verification.outcome.value != "PASSED"
        if blocked and exp.engine.action.value == "CHALLENGE":
            # A challenge whose draft does not verify goes to a person.
            assert exp.route is Route.REFER, c.id
        else:
            assert exp.route.value == exp.engine.action.value, c.id


def test_margins_are_exact_decimals():
    for c in CASES:
        if c.expected.engine and c.expected.engine.margin is not None:
            assert isinstance(c.expected.engine.margin, Decimal)


# --- The merchant-system mocks can reach each case's evidence ------------------------------


WORLD = synthetic_world(CASES)


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_lookup_chain_reaches_exactly_the_cases_own_records(case):
    """dispute.merchant_order_id -> order -> tracking number and customer -> everything else."""
    merchant = case.dispute.merchant_id
    found = WORLD.find_orders(merchant, case.dispute.merchant_order_id)
    reached = {r.id for r in found}
    for order in found:
        reached |= {r.id for r in WORLD.tracking_events(merchant, order.fields["tracking_number"])}
        customer = order.fields["customer_id"]
        for lookup in (WORLD.login_summary, WORLD.customer_contacts, WORLD.policy_acceptances):
            reached |= {r.id for r in lookup(merchant, customer)}
    own = {r.id for r in case.world if r.merchant_id in (None, merchant)}
    assert reached == own


def test_world_records_are_all_synthetic_evidence():
    for c in CASES:
        assert all(r.synthetic and r.hash_ok() for r in c.evidence_records()), c.id
