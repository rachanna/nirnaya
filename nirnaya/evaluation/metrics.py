"""The five evaluation metrics, reported separately per policy (spec section 9).

1. Financial decision quality: expected net loss (economics.py) and agreement with the
   expert route.
2. Evidence integrity: unsupported claims submitted; fabrications caught before any write.
3. Merchant-policy compliance: automatic actions where the policy requires a person.
4. API lifecycle correctness: only for policies that use the API (Nirnaya), from its ledger.
5. Safe automation coverage: share of actionable cases automated versus referred.
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from nirnaya.evaluation.cases import EvalCase, Route
from nirnaya.evaluation.economics import Cost, price
from nirnaya.evaluation.policies import Act, CaseResult
from nirnaya.models.enums import Action, ReasonCategory, Stage
from nirnaya.models.policy import MerchantPolicy
from nirnaya.orchestrator.settings import Settings

_ACTION = {Act.ACCEPT: Action.ACCEPT, Act.CHALLENGE: Action.CHALLENGE, Act.REFER: Action.REFER}


def priced(case: EvalCase) -> bool:
    """Cases every policy must decide, with the expert numbers needed to price them."""
    e = case.expected.engine
    return (
        case.expected.route is not Route.DEFER
        and e is not None
        and e.p is not None
        and e.math_action in {Action.ACCEPT, Action.CHALLENGE}
    )


def case_cost(
    case: EvalCase, result: CaseResult, policy: MerchantPolicy, *, keep_fighting: bool = False
) -> Cost | None:
    if not priced(case) or result.action is Act.NONE:
        return None
    e = case.expected.engine
    assert e is not None and e.p is not None
    return price(
        _ACTION[result.action],
        case.dispute.stage,
        e.p,
        case.dispute.amount.amount,
        policy,
        keep_fighting=keep_fighting,
        reviewer_action=e.math_action,
    )


@dataclass(frozen=True)
class Financial:
    expected_loss: Decimal
    by_part: dict[str, Decimal]
    priced_cases: int
    agreement: int
    decided: int


def financial(
    cases: list[EvalCase],
    results: dict[str, CaseResult],
    policy: MerchantPolicy,
    *,
    keep_fighting: bool = False,
) -> Financial:
    total = Cost()
    priced_n = 0
    for case in cases:
        cost = case_cost(case, results[case.id], policy, keep_fighting=keep_fighting)
        if cost is not None:
            total = total + cost
            priced_n += 1
    expected = {Route.ACCEPT: Act.ACCEPT, Route.CHALLENGE: Act.CHALLENGE,
                Route.REFER: Act.REFER, Route.DEFER: Act.NONE}  # fmt: skip
    agree = sum(1 for c in cases if results[c.id].action is expected[c.expected.route])
    return Financial(
        expected_loss=total.total,
        by_part={
            "amount_lost": total.amount_lost,
            "handling": total.handling,
            "fees": total.fees,
            "counted_chargeback": total.counted_chargeback,
            "review": total.review,
        },
        priced_cases=priced_n,
        agreement=agree,
        decided=len(cases),
    )


@dataclass(frozen=True)
class Integrity:
    challenges: int
    claims_submitted: int
    unsupported_submitted: int
    unsupported_format: int  # of which: a true fact whose value was written in the wrong form
    challenges_without_evidence: int
    fabrications_caught: int


def integrity(cases: list[EvalCase], results: dict[str, CaseResult]) -> Integrity:
    rs = [results[c.id] for c in cases]
    challenges = [r for r in rs if r.action is Act.CHALLENGE]
    return Integrity(
        challenges=len(challenges),
        claims_submitted=sum(len(r.submitted) for r in challenges),
        unsupported_submitted=sum(r.unsupported_submitted for r in challenges),
        unsupported_format=sum(r.unsupported_format for r in challenges),
        challenges_without_evidence=sum(1 for r in challenges if not r.submitted),
        fabrications_caught=sum(r.blocked_before_write for r in rs),
    )


@dataclass(frozen=True)
class Compliance:
    violations: int
    by_kind: dict[str, int]
    cases: tuple[str, ...]


def compliance(cases: list[EvalCase], results: dict[str, CaseResult]) -> Compliance:
    """An automatic accept or challenge where the merchant's policy requires a person, or an
    automatic challenge of an unknown reason."""
    kinds: Counter[str] = Counter()
    bad: list[str] = []
    for case in cases:
        act = results[case.id].action
        if act not in {Act.ACCEPT, Act.CHALLENGE}:
            continue
        e = case.expected.engine
        reasons = [r.value for r in (e.referral_reasons if e else ())]
        hit = False
        for reason in reasons:
            kinds[f"acted despite {reason}"] += 1
            hit = True
        if act is Act.CHALLENGE and case.dispute.reason_category is ReasonCategory.UNKNOWN:
            kinds["challenged an unknown reason"] += 1
            hit = True
        if hit:
            bad.append(case.id)
    return Compliance(violations=len(bad), by_kind=dict(kinds), cases=tuple(bad))


@dataclass(frozen=True)
class Lifecycle:
    writes: int
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems


# The Challenge API lists RFI and CHARGEBACK; the disputes guide also allows challenging a
# pre-chargeback (it escalates). Until that is settled (architecture open decision 18) a
# pre-chargeback challenge is not counted as a lifecycle error.
_CHALLENGE_STAGES = {Stage.RFI.value, Stage.PRE_CHARGEBACK.value, Stage.CHARGEBACK.value}


def lifecycle(cases: list[EvalCase], results: dict[str, CaseResult]) -> Lifecycle:
    """From a policy's own ledger: the right action for the stage, deadlines met, no duplicates."""
    buffers = Settings().deadline_buffer
    problems: list[str] = []
    ids: Counter[str] = Counter()
    count = 0
    for case in cases:
        r = results[case.id]
        confirmed: Counter[tuple[int, str]] = Counter()
        for w in r.writes:
            count += 1
            ids[w.request_id] += 1
            stage = r.stage_of_cycle.get(w.cycle, "")
            if w.status in {"CONFIRMED", "DRY_RUN"}:
                confirmed[(w.cycle, w.action)] += 1
            if w.action == "CHALLENGE" and stage not in _CHALLENGE_STAGES:
                problems.append(f"{case.id}: challenge at {stage}")
            if w.action == "ACCEPT" and stage == Stage.RFI.value:
                problems.append(f"{case.id}: accept at RFI without approval")
            if stage and w.created_at >= case.dispute.due_at - buffers[Stage(stage)]:
                problems.append(f"{case.id}: write inside the deadline buffer")
        problems += [
            f"{case.id}: {n} {action} writes in cycle {cycle}"
            for (cycle, action), n in confirmed.items()
            if n > 1
        ]
    problems += [f"request_id {rid} used {n} times" for rid, n in ids.items() if n > 1]
    return Lifecycle(writes=count, problems=tuple(problems))


@dataclass(frozen=True)
class Coverage:
    actionable: int
    automated: int
    referred: int

    @property
    def automated_share(self) -> Decimal:
        return Decimal(self.automated) / Decimal(self.actionable) if self.actionable else Decimal(0)


def coverage(cases: list[EvalCase], results: dict[str, CaseResult]) -> Coverage:
    acts = [results[c.id].action for c in cases if results[c.id].action is not Act.NONE]
    return Coverage(
        actionable=len(acts),
        automated=sum(1 for a in acts if a in {Act.ACCEPT, Act.CHALLENGE}),
        referred=sum(1 for a in acts if a is Act.REFER),
    )
