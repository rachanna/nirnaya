"""Once a case is worth fighting, code fetches the evidence that makes the fight stronger.

Lookups used to be driven only by what could flip the decision (value of information). Kit
case 1 is already a fight on the order record (device and IP match three prior undisputed
orders: p 0.60, margin 745), so the signed delivery the kit brief names was never fetched and
the response never cited it. Now, before the decision is recorded, code makes the supporting
lookups (engine.strengthening_sets, tools that can complete them) and decides again: p 0.75,
margin 970, the hand-built package of tests/test_kit4_scenarios.py. Optional: skipped when it
and the sweep it would add do not fit the budget, never a reason to refer. The agent still
investigates only what could flip the decision.
"""

from decimal import Decimal as D

from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.integrations.mocks import SyntheticWorld
from nirnaya.models import EvidenceType
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import CaseState
from tests.harness import CASES, Harness

M = CASES["K1"].dispute.merchant_id
CARRIER = "get_carrier_tracking:TRK-K1"


def run(label, world=None, settings=None):
    h = Harness()
    if settings is not None:
        h.settings = settings
    case = CASES[label]
    h.stage(case)
    orch = Orchestrator(
        h.store,
        h.aw,
        h.aw,
        world or h.world,
        ScriptedInvestigator(),
        ScriptedDrafter(),
        h.settings,
    )
    orch.run_until_idle(h.now(case))
    return h


def events(h, case_id, name):
    return [e["body"] for e in h.store.audit_events(M, case_id) if e["event"] == name]


def decided(h, case_id):
    return [b for b in events(h, case_id, "STATE") if b.get("to") == "DECIDED"]


def test_case_1_fetches_the_signed_delivery_before_deciding():
    h = run("K1")
    (body,) = decided(h, "K1")
    assert body["support"] == [CARRIER] and body["p_before"] == "0.60"
    lookups = [f"{b['tool']}:{b['key']}" for b in events(h, "K1", "TOOL_CALL")]
    assert lookups.index(CARRIER) < len(lookups)  # fetched, by code
    _, decision = h.store.latest_decision(M, "K1", 1)
    assert (decision.p, decision.margin) == (D("0.75"), D("970.0000"))  # the kit test's package


def test_the_response_cites_the_signature_and_passes():
    h = run("K1")
    draft = h.store.latest_draft(M, "K1", 1)
    result = h.store.verification(M, "K1", 1, draft)
    assert result.outcome.value == "PASSED"
    cited = {c.claim.establishes for c in result.claims}
    assert EvidenceType.DELIVERY_SIGNATURE in cited
    assert EvidenceType.PRIOR_UNDISPUTED_ORDERS_MATCH in cited
    assert [w.action for w in h.store.writes(M, "K1")] == ["CHALLENGE"]


def test_no_room_in_the_budget_means_no_supporting_lookup_and_still_a_fight():
    # Intake (order, helpdesk) and the sweep (login) use 3; the carrier would make 4.
    h = run("K1", settings=Settings(dry_run=True, max_tool_calls=3))
    (body,) = decided(h, "K1")
    assert "support" not in body
    _, decision = h.store.latest_decision(M, "K1", 1)
    assert decision.margin == D("745.0000")
    assert h.store.case(M, "K1").state != CaseState.REFERRED
    assert [w.action for w in h.store.writes(M, "K1")] == ["CHALLENGE"]


def test_accepts_and_referrals_make_no_supporting_lookup():
    for label in ("K2", "K3"):
        h = run(label)
        assert all("support" not in b for b in decided(h, label))
        assert not any(b["tool"] == "get_carrier_tracking" for b in events(h, label, "TOOL_CALL"))


def test_a_supporting_record_that_contradicts_the_case_goes_to_a_person():
    # The carrier names a signer but also reports no signature captured: records disagree.
    sources = [
        s.model_copy(update={"fields": {**s.fields, "signature_status": "NOT_SIGNED"}})
        if s.id == "K1-carrier"
        else s
        for s in CASES["K1"].source_records()
    ]
    h = run("K1", world=SyntheticWorld(sources))
    assert h.store.case(M, "K1").state == CaseState.REFERRED
    assert not h.store.writes(M, "K1")


def test_the_chargeback_round_is_strengthened_too():
    h = run("K1B")
    (body,) = decided(h, "K1B")
    assert any(s.startswith("get_carrier_tracking:") for s in body["support"])
    _, decision = h.store.latest_decision(M, "K1B", 1)
    assert decision.p == D("0.75")
