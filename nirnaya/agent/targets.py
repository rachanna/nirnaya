"""What the investigator may look for, decided by code (architecture section 8.2)."""

from nirnaya.agent.tools import Tool
from nirnaya.evidence.catalogue import REQUIRED_EVIDENCE
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action, EvidenceType

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


def tools_for(targets: list[EvidenceType]) -> list[Tool]:
    return sorted({tool for t in targets if (tool := TOOL_FOR[t]) is not None})
