"""Before any challenge is drafted, code fetches every record that could contradict it.

What gets fetched used to be driven only by evidence that is *missing* (value of information,
unmet requirements). So a model could stop after the helpful record and leave the harmful one
unfetched (two shipments, one returned), and a source nobody needed for the maths (a login
record reporting the account compromised) was never read. The sweep closes both, by code:
whichever agent ran, every key of every tool that serves a fact the case relies on, and every
source with a contradiction rule against one, is looked at before drafting, or the case goes
to a person. Found from an independent review (NIR-001, NIR-002).
"""

import json
from pathlib import Path

from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.agent.targets import counterevidence_lookups
from nirnaya.evaluation.cases import EvalCase, synthetic_world
from nirnaya.integrations.mocks import SourceRecord, SyntheticWorld
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.llm.types import ToolCall
from nirnaya.models import RecordAuthor, SourceSystem
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness

ROOT = Path(__file__).resolve().parents[2]
N1 = CASES["N1"]
M = N1.dispute.merchant_id


def shipment_sources() -> list[SourceRecord]:
    """N1's order shipped in two parcels: TRK-N1 delivered, TRK-N1B returned to sender."""
    extra = [
        SourceRecord(
            id="N1-order-split", merchant_id=M, system=SourceSystem.ORDER,
            author=RecordAuthor.MERCHANT,
            fields={"order_id": "ORD-N1", "customer_id": "CUST-N1", "customer_name": "J. Smith",
                    "placed_at": "2026-02-10T17:00:00+08:00", "total_amount": "200.00",
                    "currency": "USD", "tracking_number": "TRK-N1B",
                    "fulfilment_status": "SHIPPED"},
        ),
        SourceRecord(
            id="N1-carrier-b", merchant_id=M, system=SourceSystem.CARRIER,
            author=RecordAuthor.THIRD_PARTY,
            fields={"tracking_number": "TRK-N1B", "status": "RETURNED_TO_SENDER"},
        ),
    ]  # fmt: skip
    return N1.source_records() + extra


def two_shipments() -> SyntheticWorld:
    return SyntheticWorld(shipment_sources())


def selective_model() -> LLMInvestigator:
    """A model that fetches only the delivered parcel, then stops."""
    finish = [reply(None, ToolCall(id=f"f{i}", name="finish", arguments={"reason": "done"}))
              for i in range(10)]  # fmt: skip
    llm = ScriptedLLM(
        [reply(None, ToolCall(id="c1", name="get_carrier_tracking", arguments={"key": "TRK-N1"}))]
        + finish
    )
    return LLMInvestigator(llm, env={})


def run(case, world, investigator, settings=None):
    h = Harness()
    if settings is not None:
        h.settings = settings
    h.stage(case)
    orch = Orchestrator(h.store, h.aw, h.aw, world, investigator, ScriptedDrafter(), h.settings)
    orch.run_until_idle(h.now(case))
    return h


def events(h, case_id, name):
    return [e["body"] for e in h.store.audit_events(M, case_id) if e["event"] == name]


def test_a_model_cannot_hide_the_returned_parcel():
    h = run(N1, two_shipments(), selective_model())
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    assert not [w for w in h.store.writes(M, "N1") if w.action == "CHALLENGE"]
    assert "N1-carrier-b" in {r.id for r in h.store.evidence(M, "N1")}
    (sweep,) = events(h, "N1", "COUNTEREVIDENCE_SWEEP")
    assert "get_carrier_tracking:TRK-N1B" in sweep["fetched"]


def compromised_account_case() -> EvalCase:
    """A fraud chargeback challenged on code-fetched order history, while the login system,
    which nothing needed for the maths, reports the account compromised."""
    src = json.loads((ROOT / "eval" / "cases" / "F8.json").read_text(encoding="utf-8"))
    src["id"] = src["dispute"]["id"] = "COMPROMISED"
    src["tags"] = []
    for r in src["world"]:
        r["id"] = r["id"].replace("F8", "COMPROMISED")
        if r["system"] == "ORDER":
            r["fields"]["prior_undisputed_orders_matching_device_ip"] = 3
    src["expected"]["engine"] = None
    src["expected"]["route"] = "REFER"
    src["expected"]["rationale"] = "test fixture: the login record contradicts the order history"
    return EvalCase.model_validate(src)


def test_a_source_nobody_needed_for_the_maths_is_still_checked():
    case = compromised_account_case()
    h = run(case, synthetic_world([case]), ScriptedInvestigator())
    cid = case.dispute.id
    assert h.store.case(M, cid).state == CaseState.REFERRED
    assert not [w for w in h.store.writes(M, cid) if w.action == "CHALLENGE"]
    systems = {r.source_system for r in h.store.evidence(M, cid)}
    assert SourceSystem.LOGIN in systems  # fetched by the sweep, not by the agent


def test_without_budget_for_the_sweep_a_person_decides():
    tight = Settings(dry_run=True, max_tool_calls=3)  # intake (2) + the agent's lookup (1)
    h = run(N1, two_shipments(), selective_model(), settings=tight)
    assert h.store.case(M, "N1").state == CaseState.REFERRED
    referral = h.store.open_referral_for(M, "N1")
    assert referral.reasons == ["COUNTEREVIDENCE_INCOMPLETE"]
    assert "get_carrier_tracking:TRK-N1B" in referral.detail["unfetched"]
    assert h.store.latest_draft(M, "N1", 1) == 0  # nothing was drafted


def test_the_gateway_refuses_a_challenge_whose_counterevidence_was_not_checked():
    from nirnaya.guardrails.gateway import GuardFailure, WriteGateway
    from nirnaya.models import Action

    h = Harness()
    h.prepare(N1, swept=False)  # decided, drafted, verified and rendered, but never swept
    gateway = WriteGateway(h.store, h.aw, h.aw, h.settings)
    world_keys = counterevidence_lookups(
        N1.dispute, h.store.evidence(M, "N1"), h.store.tools_called(M, "N1", 1)
    )
    assert world_keys  # prepare() bypassed the lookups
    result = gateway.submit(M, "N1", 1, Action.CHALLENGE, now=h.now(N1))
    assert not result.ok and result.failure is GuardFailure.COUNTEREVIDENCE_INCOMPLETE


def test_the_lookups_cover_the_same_tool_and_every_contradiction_source():
    records = [s.snapshot(case_id="N1", captured_at=Harness.now(N1)) for s in shipment_sources()]
    own = [r for r in records if r.id in {"N1-order", "N1-order-split", "N1-carrier"}]
    needed = counterevidence_lookups(N1.dispute, own, called=set())
    # DELIVERY_CONFIRMED is established: every carrier key (both parcels), plus the order and
    # helpdesk systems, which hold contradiction rules against it.
    assert "get_carrier_tracking:TRK-N1" in needed and "get_carrier_tracking:TRK-N1B" in needed
    assert "get_order:ORD-N1" in needed and "get_helpdesk_contacts:CUST-N1" in needed
    assert needed == sorted(needed)
    assert counterevidence_lookups(N1.dispute, own, called=set(needed)) == []
