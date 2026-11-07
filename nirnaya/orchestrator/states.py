"""Case lifecycle states and the transitions allowed between them (architecture section 4)."""

from enum import StrEnum


class CaseState(StrEnum):
    INGESTED = "INGESTED"
    DEFERRED = "DEFERRED"
    ASSESSED = "ASSESSED"
    INVESTIGATING = "INVESTIGATING"
    DECIDED = "DECIDED"
    DRAFTING = "DRAFTING"
    VERIFYING = "VERIFYING"
    RENDERING = "RENDERING"
    PRE_WRITE_CHECK = "PRE_WRITE_CHECK"
    SUBMITTING = "SUBMITTING"
    AWAITING_OUTCOME = "AWAITING_OUTCOME"
    REFERRED = "REFERRED"
    CLOSED = "CLOSED"


S = CaseState

# Transitions a handler or command may make.
FLOW: dict[CaseState, frozenset[CaseState]] = {
    S.INGESTED: frozenset({S.DEFERRED, S.ASSESSED, S.CLOSED}),
    S.DEFERRED: frozenset(),
    S.ASSESSED: frozenset({S.INVESTIGATING, S.DECIDED}),
    S.INVESTIGATING: frozenset({S.INVESTIGATING, S.DECIDED}),
    S.DECIDED: frozenset({S.PRE_WRITE_CHECK, S.DRAFTING, S.REFERRED}),
    S.DRAFTING: frozenset({S.VERIFYING, S.REFERRED}),
    # VERIFYING -> DRAFTING: a blocked draft goes back once with the findings (architecture 8.6).
    S.VERIFYING: frozenset({S.RENDERING, S.REFERRED, S.DRAFTING}),
    S.RENDERING: frozenset({S.PRE_WRITE_CHECK, S.REFERRED}),
    S.PRE_WRITE_CHECK: frozenset({S.SUBMITTING, S.REFERRED}),
    S.SUBMITTING: frozenset({S.AWAITING_OUTCOME, S.REFERRED}),
    S.AWAITING_OUTCOME: frozenset(),
    # REFERRED -> INGESTED: a person sends back a case with no decision on the current facts.
    S.REFERRED: frozenset({S.INGESTED, S.PRE_WRITE_CHECK, S.DRAFTING, S.CLOSED}),
    S.CLOSED: frozenset(),
}

# Triggered by what Airwallex reports (ingestion) or by the deadline watchdog.
EXTERNAL_NEW_CYCLE = frozenset(s for s in CaseState if s is not S.CLOSED)  # -> INGESTED
EXTERNAL_CLOSE = frozenset(s for s in CaseState if s is not S.CLOSED)  # -> CLOSED
# The dispute's amount changed within a stage before any write: decide again (-> ASSESSED).
EXTERNAL_REASSESS = frozenset(
    {
        S.ASSESSED,
        S.INVESTIGATING,
        S.DECIDED,
        S.DRAFTING,
        S.VERIFYING,
        S.RENDERING,
        S.PRE_WRITE_CHECK,
        S.REFERRED,
    }
)
# A deferral predicts that Airwallex will act. If it has not by the deadline buffer, the
# watchdog refers the case (DEFERRAL_UNCONFIRMED) instead of letting the deadline pass silently.
DEFERRAL_CHECK = frozenset({S.DEFERRED})
WATCHDOG_REFERS = frozenset(
    s
    for s in CaseState
    if s not in {S.DEFERRED, S.AWAITING_OUTCOME, S.SUBMITTING, S.REFERRED, S.CLOSED}
)

# States the scheduler advances; the others wait for Airwallex or a person.
RUNNABLE = frozenset(
    {
        S.INGESTED,
        S.ASSESSED,
        S.INVESTIGATING,
        S.DECIDED,
        S.DRAFTING,
        S.VERIFYING,
        S.RENDERING,
        S.PRE_WRITE_CHECK,
        S.SUBMITTING,
    }
)


class IllegalTransition(RuntimeError):
    pass


def check(frm: CaseState, to: CaseState, *, external: bool = False) -> None:
    if to in FLOW[frm]:
        return
    if external and to is S.INGESTED and frm in EXTERNAL_NEW_CYCLE:
        return
    if external and to is S.CLOSED and frm in EXTERNAL_CLOSE:
        return
    if external and to is S.REFERRED and frm in WATCHDOG_REFERS | DEFERRAL_CHECK:
        return
    if external and to is S.ASSESSED and frm in EXTERNAL_REASSESS:
        return
    raise IllegalTransition(f"{frm} -> {to}")
