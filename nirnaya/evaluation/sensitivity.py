"""How much the decisions depend on the assumed numbers.

For every case with a maths action, the stage formula is re-evaluated with one assumption
changed at a time (priors shifted, q, the pre-arbitration fee, R, handling cost, m), and the
number of maths actions that flip is counted. Also lists the cases whose hand-derived prior is
within 0.05 of the break-even prior. The fees and q are unverified assumptions
(TODO(verify-airwallex-docs) in the policy file), so this is what a merchant must calibrate.
Uses the engine's own stage formula (engine.core._economics), so nothing is re-derived here.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine.core import _economics
from nirnaya.evaluation.cases import EvalCase
from nirnaya.models.enums import AUTOMATED_STAGES, Action, Stage
from nirnaya.models.policy import MerchantPolicy

D = Decimal
ZERO = Decimal(0)


def _action(stage: Stage, p: Decimal, amount: Decimal, policy: MerchantPolicy) -> Action:
    return Action.CHALLENGE if _economics(stage, p, amount, policy).margin > 0 else Action.ACCEPT


def _break_even(stage: Stage, amount: Decimal, policy: MerchantPolicy) -> Decimal | None:
    """The prior above which the maths says challenge (None if it never does)."""
    if _action(stage, D(1), amount, policy) is Action.ACCEPT:
        return None
    lo, hi = D(0), D(1)
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if _action(stage, mid, amount, policy) is Action.CHALLENGE else (mid, hi)
    return hi.quantize(D("0.001"))


def sensitivity(cases: list[EvalCase], policy: MerchantPolicy = DEMO_POLICY) -> dict[str, Any]:
    priced = [
        c
        for c in cases
        if c.expected.engine is not None
        and c.expected.engine.p is not None
        and c.expected.engine.math_action is not None
        and c.dispute.stage in AUTOMATED_STAGES
        and c.dispute.amount.currency == policy.currency
    ]

    def flips(pol: MerchantPolicy, dp: Decimal = ZERO) -> list[str]:
        out = []
        for c in priced:
            e = c.expected.engine
            p = min(max(e.p + dp, D(0)), D(1))
            if _action(c.dispute.stage, p, c.dispute.amount.amount, pol) is not e.math_action:
                out.append(c.id)
        return out

    variants: list[tuple[str, Callable[[], list[str]]]] = [
        ("as configured", lambda: flips(policy)),
        *[
            (f"every prior {d}", lambda d=d: flips(policy, D(d)))
            for d in ("-0.10", "-0.05", "+0.05", "+0.10")
        ],
    ]
    for name, field, value in [
        ("q = 0.05", "escalation_probability", "0.05"),
        ("q = 0.25", "escalation_probability", "0.25"),
        ("F_pa = 0", "fee_pre_arbitration", "0"),
        ("F_pa = 1000", "fee_pre_arbitration", "1000"),
        ("R = 0", "counted_chargeback_cost", "0"),
        ("R = 100", "counted_chargeback_cost", "100"),
        ("h = 50", "handling_cost", "50"),
        ("m = 0", "min_gain", "0"),
    ]:
        changed = policy.model_copy(update={field: D(value)})
        variants.append((name, lambda changed=changed: flips(changed)))

    rows = []
    for name, run in variants:
        flipped = run()
        rows.append({"assumption": name, "flips": len(flipped), "cases": flipped})

    near = []
    for c in priced:
        pstar = _break_even(c.dispute.stage, c.dispute.amount.amount, policy)
        if pstar is not None and abs(c.expected.engine.p - pstar) <= D("0.05"):
            near.append(
                {
                    "case": c.id,
                    "p": str(c.expected.engine.p),
                    "break_even": str(pstar),
                    "gap": str((c.expected.engine.p - pstar).quantize(D("0.001"))),
                }
            )
    return {"cases": len(priced), "rows": rows, "near_break_even": near}
