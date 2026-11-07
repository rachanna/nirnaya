"""FC-18 (docs/failure-cases.md): a signature can't stand on a parcel the carrier says never came.

On a fraud case a signature can flip the decision on its own, so a quote from a driver's note was
offered to a person and sent as a challenge, on a parcel the carrier reports lost. The person now
sees the conflict, and confirming the quote does not send it.
"""

import json
from pathlib import Path

import pytest

from nirnaya.agent.runs import AgentRun, ReadResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.console.views import _if_confirmed
from nirnaya.evaluation.cases import EvalCase, synthetic_world
from nirnaya.evidence.catalogue import Quote, contradiction_rules_for
from nirnaya.models import Action
from nirnaya.models.enums import EvidenceType as E
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, reject_findings
from nirnaya.orchestrator.states import CaseState
from tests.factories import carrier_record
from tests.harness import Harness

ROOT = Path(__file__).resolve().parents[2]
NOTE = "Handed to the customer at the door; signature captured on the device."
QUOTE = "signature captured on the device"
AP = "ops@example.test"
NOT_DELIVERED = ["LOST", "RETURNED_TO_SENDER", "DELIVERY_EXCEPTION"]


def fraud_case(status: str) -> EvalCase:
    """K1's fraud case at a size where only a signature would make it worth fighting."""
    src = json.loads((ROOT / "eval" / "cases" / "K1.json").read_text(encoding="utf-8"))
    src["dispute"]["stage"] = "CHARGEBACK"
    src["dispute"]["amount"]["amount"] = "120.00"
    for r in src["world"]:
        if r["system"] == "CARRIER":
            r["fields"]["status"] = status
            r["fields"].pop("signed_by", None)
            r["fields"]["driver_notes"] = NOTE
        if r["system"] == "ORDER":
            r["fields"]["total_amount"] = "120.00"
    src["claims"] = []
    src["expected"] = {
        "route": "REFER",
        "engine": None,
        "verification": None,
        "rationale": "test fixture: the signature is only in a note",
    }
    return EvalCase.model_validate(src)


class Reader:
    def read(self, dispute, facts, records):
        run = AgentRun(kind="READ", provider="f", model="f", turns=1, stop="TOOL_USE")
        quote = Quote("K1-carrier", "driver_notes", E.DELIVERY_SIGNATURE, QUOTE)
        return ReadResult(proposals=(quote,), run=run)


def started(status):
    case = fraud_case(status)
    h = Harness()
    h.stage(case)
    orch = Orchestrator(
        h.store, h.aw, h.aw, synthetic_world([case]), ScriptedInvestigator(), ScriptedDrafter(),
        h.settings, reader=Reader(),
    )  # fmt: skip
    now = h.now(case)
    orch.run_until_idle(now)
    return h, orch, now, case.dispute.merchant_id, case.dispute.id


def finding_ids(h, m, cid):
    return [f.finding_id for f in h.store.findings(m, cid, 1) if f.status == "PROPOSED"]


@pytest.mark.parametrize("status", NOT_DELIVERED)
def test_a_parcel_that_never_arrived_contradicts_a_signature(status):
    record = carrier_record(status=status)
    assert [r.description for r in contradiction_rules_for(record, E.DELIVERY_SIGNATURE)] == [
        "carrier reports the parcel was not delivered"
    ]


def test_a_delivered_parcel_does_not():
    assert contradiction_rules_for(carrier_record(), E.DELIVERY_SIGNATURE) == []


@pytest.mark.parametrize("status", NOT_DELIVERED)
def test_the_person_is_shown_the_conflict_before_confirming(status):
    h, _, _, m, cid = started(status)
    referral = h.store.open_referral_for(m, cid)
    assert "PROPOSED_EVIDENCE" in referral.reasons
    assert referral.detail["action_if_confirmed"] != "CHALLENGE"
    conflicts = referral.detail["contradictions_if_confirmed"]
    assert [c["record_id"] for c in conflicts] == ["K1-carrier"]
    assert "conflicts with K1-carrier" in _if_confirmed(referral.detail)


@pytest.mark.parametrize("status", NOT_DELIVERED)
def test_confirming_the_quote_sends_nothing_and_a_person_still_decides(status):
    h, orch, now, m, cid = started(status)
    ids = finding_ids(h, m, cid)
    approve(h.store, m, cid, Action.CHALLENGE, approver=AP, now=now, confirm_findings=ids)
    orch.run_until_idle(now)
    assert h.store.writes(m, cid) == []
    assert h.store.case(m, cid).state == CaseState.REFERRED


def test_rejecting_the_quote_leaves_the_accept():
    h, orch, now, m, cid = started("LOST")
    reject_findings(h.store, m, cid, finding_ids(h, m, cid), approver=AP, now=now)
    approve(h.store, m, cid, Action.ACCEPT, approver=AP, now=now)
    orch.run_until_idle(now)
    assert [w.action for w in h.store.writes(m, cid)] == ["ACCEPT"]


def test_a_delivered_parcel_still_goes_through():
    h, orch, now, m, cid = started("DELIVERED")
    referral = h.store.open_referral_for(m, cid)
    assert referral.detail["action_if_confirmed"] == "CHALLENGE"
    assert referral.detail.get("contradictions_if_confirmed", []) == []
    ids = finding_ids(h, m, cid)
    approve(h.store, m, cid, Action.CHALLENGE, approver=AP, now=now, confirm_findings=ids)
    orch.run_until_idle(now)
    assert [w.action for w in h.store.writes(m, cid)] == ["CHALLENGE"]
