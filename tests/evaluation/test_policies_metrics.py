"""Policies on the 42 cases and the five metrics computed from them."""

from dataclasses import replace
from datetime import timedelta

import pytest

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import Route, load_cases
from nirnaya.evaluation.metrics import (
    compliance,
    coverage,
    financial,
    integrity,
    lifecycle,
    priced,
)
from nirnaya.evaluation.policies import Act, CaseResult, accept_all, fight_all, nirnaya

CASES = load_cases()
BY_ID = {c.id: c for c in CASES}
EXPECTED = {Route.ACCEPT: Act.ACCEPT, Route.CHALLENGE: Act.CHALLENGE,
            Route.REFER: Act.REFER, Route.DEFER: Act.NONE}  # fmt: skip


@pytest.fixture(scope="module")
def runs():
    return {
        "nirnaya": {c.id: nirnaya(c) for c in CASES},
        "fight_all": {c.id: fight_all(c) for c in CASES},
        "accept_all": {c.id: accept_all(c) for c in CASES},
    }


def test_the_nirnaya_pipeline_lands_on_every_expert_route(runs):
    wrong = {cid: r.action for cid, r in runs["nirnaya"].items()
             if r.action is not EXPECTED[BY_ID[cid].expected.route]}  # fmt: skip
    assert wrong == {}


def test_baselines_act_on_every_case_airwallex_is_not_handling(runs):
    for case in CASES:
        deferred = case.expected.route is Route.DEFER
        assert (runs["fight_all"][case.id].action is Act.NONE) == deferred, case.id
        assert (runs["accept_all"][case.id].action is Act.NONE) == deferred, case.id
        if not deferred:
            assert runs["fight_all"][case.id].action is Act.CHALLENGE
            assert runs["accept_all"][case.id].action is Act.ACCEPT


def test_nirnaya_submits_no_unsupported_claims_and_catches_the_fabrications(runs):
    n = integrity(CASES, runs["nirnaya"])
    assert n.unsupported_submitted == 0
    assert n.fabrications_caught >= 1  # K2-C's signature, A3's overstated amount
    f = integrity(CASES, runs["fight_all"])
    assert f.unsupported_submitted >= 2
    assert f.fabrications_caught == 0


def test_fight_all_submits_the_known_fabrications_unverified(runs):
    for cid in ("K2C", "A3"):
        assert runs["fight_all"][cid].unsupported_submitted >= 1, cid


def test_nirnaya_has_no_policy_violations_and_the_baselines_do(runs):
    assert compliance(CASES, runs["nirnaya"]).violations == 0
    accept = compliance(CASES, runs["accept_all"])
    fight = compliance(CASES, runs["fight_all"])
    must_refer = {
        c.id
        for c in CASES
        if c.expected.route is Route.REFER and c.expected.engine.referral_reasons
    }
    assert set(accept.cases) >= must_refer - {"X2"}
    assert fight.violations > 0 and any("unknown" in k for k in fight.by_kind)


def test_nirnaya_lifecycle_is_clean_and_a_duplicate_write_is_caught(runs):
    clean = lifecycle(CASES, runs["nirnaya"])
    assert clean.ok and clean.writes > 0
    r = runs["nirnaya"]["N1"]
    (w,) = [w for w in r.writes if w.status in {"CONFIRMED", "DRY_RUN"}]
    dup = replace(r, writes=(w, w.model_copy(update={"created_at": w.created_at})))
    bad = lifecycle([BY_ID["N1"]], {"N1": dup})
    assert not bad.ok and any("used 2 times" in p for p in bad.problems)
    late = replace(r, writes=(w.model_copy(update={"created_at": BY_ID["N1"].dispute.due_at
                                                     - timedelta(hours=1)}),))  # fmt: skip
    assert any("deadline" in p for p in lifecycle([BY_ID["N1"]], {"N1": late}).problems)


def test_coverage(runs):
    cov = coverage(CASES, runs["nirnaya"])
    assert cov.actionable == cov.automated + cov.referred
    assert cov.referred == sum(1 for c in CASES if c.expected.route is Route.REFER)
    assert coverage(CASES, runs["accept_all"]).referred == 0


def test_financial_compares_on_the_same_priced_cases(runs):
    n = financial(CASES, runs["nirnaya"], DEMO_POLICY)
    a = financial(CASES, runs["accept_all"], DEMO_POLICY)
    f = financial(CASES, runs["fight_all"], DEMO_POLICY, keep_fighting=True)
    assert n.priced_cases == a.priced_cases == f.priced_cases == sum(1 for c in CASES if priced(c))
    assert n.expected_loss < a.expected_loss and n.expected_loss < f.expected_loss
    assert n.agreement == len(CASES)
    assert sum(n.by_part.values()) == n.expected_loss


def test_an_unknown_case_result_kind_is_none_for_deferred_cases():
    deferred = [c for c in CASES if c.expected.route is Route.DEFER]
    assert deferred and all(fight_all(c).action is Act.NONE for c in deferred)
    assert CaseResult("x", Act.NONE).writes == ()
