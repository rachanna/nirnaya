"""Expected cost of an action on a dispute, for comparing policies (Milestone 6).

Every policy is priced with the same expert assumptions: the case's hand-derived win prior p
(eval/EXPERT_POLICY.md) and the merchant policy's fees and escalation probability q. Costs run
from the current stage onward; costs already sunk are the same for every policy and are left
out. The numbers are expected values under assumed priors on SYNTHETIC cases, not real issuer
outcomes.

The formulas mirror the engine's (nirnaya/engine/formulas.py): for every stage,
cost(accept) - cost(challenge with the expert continuation) equals the engine's margin + m.

| Stage          | Accept     | Challenge                                       |
|----------------|------------|-------------------------------------------------|
| CHARGEBACK     | A          | (1 - p) * A + h + q * F_pa                      |
| PRE_CHARGEBACK | A + F_pre  | h_pre + F_cb + R + C_cb                         |
| RFI            | A + F_rfi  | h_rfi + F_cb + R + C_cb                         |

C_cb is the cost at the chargeback stage the challenge escalates to: the cheaper of accepting
and challenging there (what an expert does), or always challenging (a fight-everything policy).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from nirnaya.models.enums import Action, Stage
from nirnaya.models.policy import MerchantPolicy

# ASSUMPTION (eval/EXPERT_POLICY.md): what a person's review of a referred case costs.
REVIEW_COST = Decimal("25")

_ZERO = Decimal(0)


@dataclass(frozen=True)
class Cost:
    """An expected cost, itemised so a report can show where every dollar goes."""

    amount_lost: Decimal = _ZERO  # the disputed amount, times the chance of losing it
    handling: Decimal = _ZERO  # h, h_pre, h_rfi
    fees: Decimal = _ZERO  # F_pre, F_rfi, F_cb, and q * F_pa
    counted_chargeback: Decimal = _ZERO  # R
    review: Decimal = _ZERO  # a person's review of a referral
    parts: tuple[str, ...] = field(default=())

    @property
    def total(self) -> Decimal:
        return self.amount_lost + self.handling + self.fees + self.counted_chargeback + self.review

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            self.amount_lost + other.amount_lost,
            self.handling + other.handling,
            self.fees + other.fees,
            self.counted_chargeback + other.counted_chargeback,
            self.review + other.review,
            self.parts + other.parts,
        )


def accept_cost(stage: Stage, A: Decimal, policy: MerchantPolicy) -> Cost:
    if stage is Stage.CHARGEBACK:
        return Cost(amount_lost=A, parts=(f"accept: lose {A}",))
    fee = policy.fee_pre_resolution if stage is Stage.PRE_CHARGEBACK else policy.fee_rfi_accept
    if stage not in {Stage.PRE_CHARGEBACK, Stage.RFI}:
        raise ValueError(f"no pricing for stage {stage}")
    return Cost(amount_lost=A, fees=fee, parts=(f"accept: lose {A}, fee {fee}",))


def _chargeback_challenge(p: Decimal, A: Decimal, policy: MerchantPolicy) -> Cost:
    lost = (1 - p) * A
    pa = policy.escalation_probability * policy.fee_pre_arbitration
    return Cost(
        amount_lost=lost,
        handling=policy.handling_cost,
        fees=pa,
        parts=(f"challenge at chargeback: lose (1-p)*A = {lost}, h, q*F_pa = {pa}",),
    )


def _chargeback_expert(p: Decimal, A: Decimal, policy: MerchantPolicy) -> Cost:
    fight = _chargeback_challenge(p, A, policy)
    accept = accept_cost(Stage.CHARGEBACK, A, policy)
    return fight if fight.total < accept.total else accept


def challenge_cost(
    stage: Stage, p: Decimal, A: Decimal, policy: MerchantPolicy, *, keep_fighting: bool = False
) -> Cost:
    """Challenge now. At RFI or pre-chargeback this escalates to a chargeback, where the policy
    then accepts or challenges: the expert does the cheaper; `keep_fighting` always challenges."""
    if stage is Stage.CHARGEBACK:
        return _chargeback_challenge(p, A, policy)
    if stage is Stage.PRE_CHARGEBACK:
        handling = policy.handling_cost_pre
    elif stage is Stage.RFI:
        handling = policy.handling_cost_rfi
    else:
        raise ValueError(f"no pricing for stage {stage}")
    escalation = Cost(
        handling=handling,
        fees=policy.fee_chargeback,
        counted_chargeback=policy.counted_chargeback_cost,
        parts=(f"challenge at {stage}: escalates, F_cb + R",),
    )
    then = (
        _chargeback_challenge(p, A, policy) if keep_fighting else _chargeback_expert(p, A, policy)
    )
    return escalation + then


def expert_action(stage: Stage, p: Decimal, A: Decimal, policy: MerchantPolicy) -> Action:
    """The cheaper action before the minimum-gain hurdle m (a tie accepts)."""
    fight, accept = challenge_cost(stage, p, A, policy), accept_cost(stage, A, policy)
    return Action.CHALLENGE if fight.total < accept.total else Action.ACCEPT


def price(
    action: Action,
    stage: Stage,
    p: Decimal,
    A: Decimal,
    policy: MerchantPolicy,
    *,
    keep_fighting: bool = False,
    reviewer_action: Action | None = None,
) -> Cost:
    """The expected cost of `action`. A referral costs the review plus the reviewer's action."""
    if action is Action.ACCEPT:
        return accept_cost(stage, A, policy)
    if action is Action.CHALLENGE:
        return challenge_cost(stage, p, A, policy, keep_fighting=keep_fighting)
    if action is Action.REFER:
        if reviewer_action is None or reviewer_action is Action.REFER:
            raise ValueError("a referral is priced with the reviewer's accept or challenge")
        review = Cost(review=REVIEW_COST, parts=(f"referral review {REVIEW_COST}",))
        return review + price(reviewer_action, stage, p, A, policy)
    raise ValueError(f"cannot price {action}")
