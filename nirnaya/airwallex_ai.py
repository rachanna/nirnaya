"""Airwallex's AI recommendation beside Nirnaya's decision, in words.

Comparison only: the engine never reads the recommendation, so agreeing or disagreeing with it
changes nothing. When they differ, the reason Nirnaya gives is its own decision record.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from nirnaya.models.dispute import AiRecommendation
from nirnaya.models.enums import Action

SIMULATED_NOTE = (
    "SIMULATED: shows how Nirnaya displays Airwallex's recommendation; not Airwallex output"
)


@dataclass(frozen=True)
class AiComparison:
    airwallex: str  # what Airwallex's AI says, or why it says nothing
    nirnaya: str
    agree: bool | None  # None: no recommendation to compare with
    evidence_to_submit: tuple[str, ...]
    simulated: bool
    text: str


def _nirnaya(action: Action, referral_reasons: Sequence[str]) -> str:
    if action is Action.REFER:
        why = ", ".join(referral_reasons) or "policy"
        return f"REFER to a person ({why})"
    return str(action)


def comparison(
    rec: AiRecommendation | None, action: Action, referral_reasons: Sequence[str] = ()
) -> AiComparison:
    ours = _nirnaya(action, referral_reasons)
    simulated = bool(rec and rec.simulated)
    if rec is None:
        airwallex, agree = "no recommendation sent", None
    elif rec.status != "AVAILABLE":
        airwallex, agree = f"not available ({rec.unavailable_reason or rec.status})", None
    elif rec.action is None:
        airwallex, agree = f"unrecognised recommendation {rec.action_raw!r}", None
    else:
        airwallex = rec.action_raw or rec.action.title()
        agree = rec.action is action
    if agree is None:
        verdict = f"Airwallex AI: {airwallex}. Nirnaya: {ours}."
    elif agree:
        verdict = f"Airwallex AI recommends {airwallex}; Nirnaya agrees: {ours}."
    else:
        verdict = f"Airwallex AI recommends {airwallex}; Nirnaya decides {ours}."
    if simulated:
        verdict = f"{verdict} [{SIMULATED_NOTE}]"
    return AiComparison(
        airwallex=airwallex,
        nirnaya=ours,
        agree=agree,
        evidence_to_submit=() if rec is None else rec.evidence_to_submit,
        simulated=simulated,
        text=verdict,
    )
