"""Plain-language wording for the console, for people outside payments.

Wording only: every verdict, number and reason comes from the engine, verifier or store, and
is translated here, never recomputed.
"""

from collections.abc import Sequence
from typing import Protocol

VERDICTS = {
    "CHALLENGE": ("Fight it", "green"),
    "ACCEPT": ("Accept it", "orange"),
    "REFER": ("A person decides", "blue"),
}

STAGES = {
    "RFI": "information request",
    "PRE_CHARGEBACK": "pre-chargeback",
    "CHARGEBACK": "chargeback",
    "PRE_ARBITRATION": "pre-arbitration",
    "ARBITRATION": "arbitration",
}

# Why a case goes to a person. Engine reasons first, then the machine's own.
REASON_TEXT = {
    "STAGE_NOT_AUTOMATED": "At this stage Airwallex or a person decides, not Nirnaya.",
    "MISSING_FX_RATE": "There is no exchange rate for this currency, so the cost is unknown.",
    "STATUS_NOT_ACTIONABLE": "The dispute isn't waiting for a response.",
    "AMOUNT_ABOVE_THRESHOLD": "The amount is above the merchant's limit for automatic decisions.",
    "MARGINAL": "Fighting and accepting are worth about the same, so a person chooses.",
    "CONTRADICTORY_RECORDS": "The merchant's own records disagree with each other.",
    "OWNERSHIP_UNRESOLVED": "It isn't clear the payment belongs to this merchant.",
    "UNANSWERED_CUSTOMER_CONTACT": (
        "The customer contacted support before the dispute and never got a reply."
    ),
    "UNKNOWN_REASON_CHALLENGE": "The dispute reason is unknown, so Nirnaya never fights it alone.",
    "RFI_ACCEPT_NEEDS_APPROVAL": (
        "Accepting at this stage refunds the full amount, so a person approves it."
    ),
    "PROPOSED_EVIDENCE": "The agent found evidence in a written note. A person must confirm it.",
    "REDRAFT_AFTER_OVERREACH": (
        "The first draft claimed more than the records show. A person checks the fix."
    ),
    "VERIFICATION_BLOCKED": "A drafted claim failed its check against the records.",
    "VERIFICATION_REFER": "The records behind the response disagree with each other.",
    "DRAFT_EMPTY": "No claim could be drafted from the records.",
    "COUNTEREVIDENCE_INCOMPLETE": "Not every record that could contradict it was checked.",
    "DEADLINE_NEAR": "The deadline is close, so a person takes over.",
    "STEP_FAILED": "A step kept failing, so a person takes over.",
    "DEFERRAL_UNCONFIRMED": "Airwallex was expected to act on its own and hasn't.",
}

# Airwallex's dispute reason types, in words.
REASON_TYPES = {
    "FRAUDULENT": "fraud (the cardholder says they didn't make it)",
    "UNRECOGNIZED_CHARGE": "the cardholder doesn't recognise the charge",
    "PRODUCT_NOT_RECEIVED": "the item never arrived",
    "CREDIT_NOT_PROCESSED": "a refund that was never processed",
    "DUPLICATE_CHARGE": "charged twice",
    "SUBSCRIPTION_CANCELLED": "a cancelled subscription",
}

# What a quoted note would show, for the facts a note may state (architecture 8.8).
FACTS = {
    "DELIVERY_SIGNATURE": "the customer signed for the delivery",
    "DELIVERY_CONFIRMED": "the order was delivered",
    "DISTINCT_TRANSACTIONS": "it was a separate purchase, not a double charge",
}

LOOKUPS = {
    "get_order": "Order",
    "get_carrier_tracking": "Delivery tracking",
    "get_login_history": "Login history for",
    "get_helpdesk_contacts": "Support messages from",
    "get_policy_acceptance": "Accepted terms for",
}


class _Decision(Protocol):
    action: str
    accept_reason: str | None
    margin: str | None
    referral_reasons: tuple[str, ...]


def verdict(action: str) -> tuple[str, str]:
    """The verdict in words and its badge colour."""
    return VERDICTS.get(action, (action.title(), "gray"))


def reason_type_text(reason_type: str | None) -> str:
    if not reason_type:
        return "not given"
    return REASON_TYPES.get(reason_type, reason_type.lower().replace("_", " "))


def fact_text(fact: str) -> str:
    return FACTS.get(fact, fact.lower().replace("_", " "))


def stage_name(stage: str) -> str:
    return STAGES.get(stage, stage.lower().replace("_", " "))


def status_text(stage_and_status: str) -> str:
    """Airwallex's "RFI CHALLENGED" as "information request, challenged"."""
    stage, _, status = stage_and_status.partition(" ")
    return f"{stage_name(stage)}, {status.lower().replace('_', ' ')}" if status else stage


def reason_sentences(codes: Sequence[str]) -> list[str]:
    """Each referral reason as a sentence; a code with no sentence stays visible as is."""
    return [
        REASON_TEXT.get(c, REASON_TEXT.get(c.removeprefix("GUARD_"), c)) if c else c for c in codes
    ]


def decision_sentence(view: _Decision, stage: str | None = None) -> str:
    """One sentence on why, using the engine's own number. At pre-chargeback an accept settles
    before the dispute becomes a chargeback, so it says so."""
    if view.action == "REFER":
        return " ".join(reason_sentences(view.referral_reasons)) or "A person decides."
    if view.action == "CHALLENGE":
        return (
            f"Fighting is worth about {view.margin} USD more than accepting, after fees and the "
            "risk of losing."
        )
    if view.accept_reason == "LOW_VALUE_TRANSACTION":
        return "Fighting would cost more than it could win back."
    if view.margin is not None and stage == "PRE_CHARGEBACK":
        return (
            f"Settling now, before it becomes a chargeback, is about {view.margin.lstrip('-')} "
            "USD better than fighting."
        )
    if view.margin is not None:
        return f"Accepting is about {view.margin.lstrip('-')} USD better than fighting."
    return "Accepting is the better choice."


def lookup_sentence(tool: str, key: str, records: Sequence[str]) -> str:
    what = LOOKUPS.get(tool, tool)
    found = "nothing found" if not records else "found" if len(records) == 1 else (
        f"{len(records)} found"
    )  # fmt: skip
    return f"{what} {key}: {found}"


def write_sentence(action: str, status: str, *, dry_run: bool, customer_info: bool = False) -> str:
    name = action.title()
    if dry_run:
        return f"{name} prepared (dry run: not sent)"
    if status == "CONFIRMED":
        extra = " (with the customer's device and IP)" if customer_info else ""
        return f"{name} sent; Airwallex confirmed it{extra}"
    if status in {"PENDING", "UNKNOWN"}:
        return f"{name} sent; waiting for Airwallex to confirm"
    return f"{name} failed; a person was told"
