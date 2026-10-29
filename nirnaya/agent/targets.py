"""What the investigator may look for, decided by code (architecture section 8.2)."""

from collections.abc import Iterable

from nirnaya.agent.tools import Tool, allowed_keys
from nirnaya.evidence.catalogue import CONTRADICTION_RULES, REQUIRED_EVIDENCE
from nirnaya.evidence.derive import evidence_present
from nirnaya.models.decision import Decision, StrengthEntry
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action, EvidenceType, SourceSystem
from nirnaya.models.evidence import EvidenceRecord

E = EvidenceType

# The tool that can find each evidence type. Payment-attempt facts have none: they are final
# once ingested, so they are never investigation targets.
TOOL_FOR: dict[EvidenceType, Tool | None] = {
    E.ORDER_PLACED: Tool.ORDER,
    E.DISTINCT_TRANSACTIONS: Tool.ORDER,
    E.REFUND_ISSUED: Tool.ORDER,
    E.PRIOR_UNDISPUTED_ORDERS_MATCH: Tool.ORDER,
    E.DELIVERY_CONFIRMED: Tool.CARRIER,
    E.DELIVERY_SIGNATURE: Tool.CARRIER,
    E.ACCOUNT_LOGIN_MATCH: Tool.LOGIN,
    E.USAGE_AFTER_RENEWAL: Tool.LOGIN,
    E.CANCELLATION_TERMS_ACCEPTED: Tool.POLICY,
    E.NO_PRIOR_CUSTOMER_CONTACT: Tool.HELPDESK,
    E.THREE_DS_AUTHENTICATED: None,
    E.AVS_MATCH: None,
    E.CVC_MATCH: None,
}


def investigation_targets(
    decision: Decision, dispute: Dispute, present: frozenset[EvidenceType]
) -> list[EvidenceType]:
    """Evidence worth searching for: every set VoI says would flip the maths, plus, when the
    maths favours a challenge, whatever the reason's evidence requirements still lack, so that
    the challenge can be verified. Only types some tool can find. A flipping set containing a
    type no tool can find (a payment-attempt fact, final once ingested) is dropped whole: the
    agent never fetches half of a set it cannot complete."""
    wanted: set[EvidenceType] = set()
    for v in decision.voi:
        if all(TOOL_FOR[t] is not None for t in v.evidence):
            wanted |= v.evidence
    if decision.math_action is Action.CHALLENGE:
        for group in REQUIRED_EVIDENCE[dispute.reason_category]:
            if not group & present:
                wanted |= group
    return sorted(t for t in wanted - present if TOOL_FOR[t] is not None)


def support_targets(entries: Iterable[StrengthEntry]) -> list[EvidenceType]:
    """Evidence that would make a challenge stronger (engine.strengthening_sets), limited to
    whole sets some tool can complete: a set holding a payment-attempt fact is dropped, as in
    investigation_targets. Fetched by code once the case is worth fighting, never the agent."""
    wanted: set[EvidenceType] = set()
    for entry in entries:
        if all(TOOL_FOR[t] is not None for t in entry.evidence):
            wanted |= entry.evidence
    return sorted(wanted)


def tools_for(targets: list[EvidenceType]) -> list[Tool]:
    return sorted({tool for t in targets if (tool := TOOL_FOR[t]) is not None})


# The tool that reads each merchant system. Payment attempts are ingested, not looked up.
TOOL_OF_SOURCE: dict[SourceSystem, Tool] = {
    SourceSystem.ORDER: Tool.ORDER,
    SourceSystem.CARRIER: Tool.CARRIER,
    SourceSystem.LOGIN: Tool.LOGIN,
    SourceSystem.HELPDESK: Tool.HELPDESK,
    SourceSystem.POLICY: Tool.POLICY,
}


def counterevidence_lookups(
    dispute: Dispute,
    records: list[EvidenceRecord],
    called: Iterable[str],
    also: Iterable[EvidenceType] = (),
) -> list[str]:
    """Lookups ("tool:key") a challenge still needs before it is drafted: for every fact the
    case's records establish, every key of the tool that serves it (another parcel, another
    order) and of every system holding a contradiction rule against it. Targets say what is
    worth *finding*; this says what must be *checked*. Decided by code, whatever the agent did.
    `also`: facts the case relies on beyond its structured fields (a confirmed quote's fact).
    """
    done = set(called)
    tools: set[Tool] = set()
    for fact in evidence_present(records, dispute) | frozenset(also):
        if (tool := TOOL_FOR[fact]) is not None:
            tools.add(tool)
        for rule in CONTRADICTION_RULES:
            if rule.contradicts is fact and (tool := TOOL_OF_SOURCE.get(rule.source_system)):
                tools.add(tool)
    return sorted(
        lookup
        for tool in tools
        for key in allowed_keys(tool, dispute, records)
        if (lookup := f"{tool}:{key}") not in done
    )
