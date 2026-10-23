import pytest

from nirnaya.agent.scripted import FIELD_KIND, ScriptedDrafter, ScriptedInvestigator
from nirnaya.agent.targets import TOOL_FOR, investigation_targets
from nirnaya.agent.tools import Tool, ToolGateway, ToolRefused
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evaluation.cases import load_cases
from nirnaya.evidence.catalogue import FIELD_RULES, REASON_SPECIFIC
from nirnaya.evidence.derive import evidence_present
from nirnaya.integrations.mocks import SourceRecord, SyntheticWorld
from nirnaya.models import (
    DisputeReason,
    EvidenceType,
    ReasonCategory,
    RecordAuthor,
    SourceSystem,
)
from nirnaya.store import Store, connect
from nirnaya.verifier import verify
from tests.factories import CASE, MERCHANT, OTHER_MERCHANT, T0, make_dispute, usd

E = EvidenceType
S = SourceSystem


def src(id, system, merchant=MERCHANT, author=RecordAuthor.MERCHANT, **fields):
    return SourceRecord(id=id, merchant_id=merchant, system=system, author=author, fields=fields)


WORLD = SyntheticWorld(
    [
        src("order-1", S.ORDER, order_id="ORD-1", customer_id="CUST-1", tracking_number="TRK-1"),
        src(
            "carrier-1",
            S.CARRIER,
            author=RecordAuthor.THIRD_PARTY,
            tracking_number="TRK-1",
            status="DELIVERED",
            delivered_at="2026-02-14T15:30:00-05:00",
        ),
        src(
            "foreign-carrier",
            S.CARRIER,
            merchant=OTHER_MERCHANT,
            author=RecordAuthor.THIRD_PARTY,
            tracking_number="TRK-1",
            status="LOST",
        ),
        src(
            "login-1", S.LOGIN, customer_id="CUST-1", active_after_renewal_at="2026-02-02T08:00:00Z"
        ),
        src(
            "policy-1",
            S.POLICY,
            customer_id="CUST-1",
            cancellation_terms_accepted_at="2025-11-03T10:00:00Z",
        ),
    ]
)


@pytest.fixture
def store():
    s = Store(connect())
    s.register_merchant(MERCHANT, airwallex_account_id="a", policy=DEMO_POLICY,
                        delegated_authority=True)  # fmt: skip
    with s.transaction():
        s.create_case(MERCHANT, CASE, "INVESTIGATING", T0, T0)
        s.start_cycle(MERCHANT, CASE, 1, stage="CHARGEBACK", policy_version="v", at=T0)
    return s


def gateway(store, max_calls=8, **dispute_overrides):
    dispute = make_dispute(merchant_order_id="ORD-1", **dispute_overrides)
    return ToolGateway(store, WORLD, dispute, 1, now=T0, max_calls=max_calls), dispute


# --- tool gateway ----------------------------------------------------------------------------


def test_keys_come_only_from_the_dispute_and_the_case_records(store):
    tools, _ = gateway(store)
    assert tools.lookup_keys(Tool.ORDER) == ["ORD-1"]
    assert tools.lookup_keys(Tool.CARRIER) == []  # no order fetched yet
    tools.fetch(Tool.ORDER, "ORD-1")
    assert tools.lookup_keys(Tool.CARRIER) == ["TRK-1"]
    assert tools.lookup_keys(Tool.LOGIN) == ["CUST-1"]


def test_invented_keys_are_refused(store):
    tools, _ = gateway(store)
    with pytest.raises(ToolRefused, match="does not come from"):
        tools.fetch(Tool.CARRIER, "TRK-1")  # not yet in any case record
    with pytest.raises(ToolRefused):
        tools.fetch(Tool.ORDER, "ORD-SOMEONE-ELSE")


def test_merchant_is_injected_so_other_tenants_records_never_arrive(store):
    tools, _ = gateway(store)
    tools.fetch(Tool.ORDER, "ORD-1")
    assert [r.id for r in tools.fetch(Tool.CARRIER, "TRK-1")] == ["carrier-1"]


def test_results_are_stored_as_hashed_evidence_and_audited(store):
    tools, _ = gateway(store)
    tools.fetch(Tool.ORDER, "ORD-1")
    (rec,) = store.evidence(MERCHANT, CASE)
    assert rec.hash_ok() and rec.synthetic and rec.captured_at == T0
    assert tools.called() == {"get_order:ORD-1"}
    assert tools.calls_used == 1
    assert store.audit_events(MERCHANT, CASE)[-1]["event"] == "TOOL_CALL"


def test_budget_is_enforced(store):
    tools, _ = gateway(store, max_calls=1)
    tools.fetch(Tool.ORDER, "ORD-1")
    with pytest.raises(ToolRefused, match="budget"):
        tools.fetch(Tool.CARRIER, "TRK-1")


# --- targets ---------------------------------------------------------------------------------


def test_every_evidence_type_has_a_tool_entry():
    assert set(TOOL_FOR) == set(EvidenceType)


def test_required_evidence_is_targeted_when_the_maths_favours_a_challenge():
    dispute = make_dispute(reason=DisputeReason(type="CANCELLED_PRODUCT"), amount=usd("300.00"))
    present = frozenset({E.CANCELLATION_TERMS_ACCEPTED, E.ORDER_PLACED})
    d = decide(dispute, DEMO_POLICY, present, ownership_resolved=True)
    assert d.math_action.value == "CHALLENGE"
    targets = investigation_targets(d, dispute, present)
    assert {E.USAGE_AFTER_RENEWAL, E.NO_PRIOR_CUSTOMER_CONTACT} <= set(targets)


def test_payment_attempt_targets_are_dropped():
    dispute = make_dispute(reason=DisputeReason(type="FRAUDULENT"), amount=usd("1000.00"))
    d = decide(dispute, DEMO_POLICY, frozenset({E.CVC_MATCH}), ownership_resolved=True)
    targets = investigation_targets(d, dispute, frozenset({E.CVC_MATCH}))
    assert E.PRIOR_UNDISPUTED_ORDERS_MATCH in targets
    assert not {E.THREE_DS_AUTHENTICATED, E.AVS_MATCH} & set(targets)


def test_present_evidence_is_never_a_target():
    dispute = make_dispute()
    d = decide(dispute, DEMO_POLICY, frozenset({E.DELIVERY_CONFIRMED}), ownership_resolved=True)
    assert E.DELIVERY_CONFIRMED not in investigation_targets(
        d, dispute, frozenset({E.DELIVERY_CONFIRMED})
    )


# --- scripted investigator -------------------------------------------------------------------


def test_scripted_investigator_fetches_each_needed_tool_once(store):
    tools, dispute = gateway(store)
    tools.fetch(Tool.ORDER, "ORD-1")
    inv = ScriptedInvestigator()
    targets = [E.DELIVERY_CONFIRMED, E.USAGE_AFTER_RENEWAL, E.CANCELLATION_TERMS_ACCEPTED]
    assert inv.investigate(dispute, targets, tools) == 3
    assert inv.investigate(dispute, targets, tools) == 0  # nothing left to fetch
    assert tools.called() == {
        "get_order:ORD-1",
        "get_carrier_tracking:TRK-1",
        "get_login_history:CUST-1",
        "get_policy_acceptance:CUST-1",
    }


def test_scripted_investigator_stops_at_the_budget(store):
    tools, dispute = gateway(store, max_calls=2)
    tools.fetch(Tool.ORDER, "ORD-1")
    used = ScriptedInvestigator().investigate(
        dispute, [E.DELIVERY_CONFIRMED, E.USAGE_AFTER_RENEWAL], tools
    )
    assert used == 1


# --- scripted drafter ------------------------------------------------------------------------


def test_every_citable_field_has_a_value_kind():
    assert {(r.source_system, r.field) for r in FIELD_RULES} <= set(FIELD_KIND)


CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_scripted_drafts_never_fail_verification(case):
    records = case.evidence_records()
    claims = ScriptedDrafter().draft(case.dispute, records)
    result = verify(case.dispute, claims, records)
    assert all(not c.failures for c in result.claims), [
        (c.claim.id, [f.check for f in c.failures]) for c in result.claims if c.failures
    ]
    present = evidence_present(records, case.dispute)
    assert {c.establishes for c in claims} <= present


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.dispute.reason_category is ReasonCategory.UNKNOWN],
    ids=lambda c: c.id,
)  # fmt: skip
def test_no_reason_specific_drafts_under_unknown(case):
    claims = ScriptedDrafter().draft(case.dispute, case.evidence_records())
    assert not {c.establishes for c in claims} & set(REASON_SPECIFIC)
