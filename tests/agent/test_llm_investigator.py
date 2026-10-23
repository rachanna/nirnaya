from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.prompts import INVESTIGATOR_SYSTEM
from nirnaya.agent.tools import Tool
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.llm.types import Role, StopReason
from nirnaya.models import EvidenceType
from tests.agent.llm_helpers import all_text_sent, call, finish, investigation

E = EvidenceType
DELIVERY = [E.DELIVERY_CONFIRMED]


def refused_events(h, case):
    return [
        e["body"]
        for e in h.store.audit_events(case.dispute.merchant_id, case.dispute.id)
        if e["event"] == "TOOL_REFUSED"
    ]


def test_fetches_an_offered_tool_with_an_offered_key_then_finishes():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM([reply(None, call("get_carrier_tracking", key="TRK-N1")), finish()])
    result = LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
    assert result.calls == 1
    assert result.run.stop == "FINISH" and result.run.turns == 2
    assert [(c.tool, c.outcome) for c in result.run.tool_calls] == [
        ("get_carrier_tracking", "FETCHED"),
        ("finish", "FINISH"),
    ]
    assert "N1-carrier" in {r.id for r in h.store.evidence(case.dispute.merchant_id, "N1")}
    assert result.run.input_tokens == 20 and result.run.output_tokens == 10


def test_tools_offered_match_the_targets_and_keys_are_an_enum():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM([finish()])
    LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
    specs = {t.name: t for t in llm.calls[0]["tools"]}
    assert set(specs) == {"get_carrier_tracking", "finish"}
    key_schema = specs["get_carrier_tracking"].input_schema["properties"]["key"]
    assert key_schema["enum"] == tools.lookup_keys(Tool.CARRIER) == ["TRK-N1"]


def test_unknown_tool_invented_key_and_untargeted_tool_are_refused_and_audited():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM(
        [
            reply(
                None,
                call("get_bank_statements", 1, key="x"),
                call("get_carrier_tracking", 2, key="TRK-SOMEONE-ELSE"),
                call("get_login_history", 3, key="CUST-N1"),
            ),
            finish(),
        ]
    )
    result = LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
    assert result.calls == 0
    assert [c.outcome for c in result.run.tool_calls[:3]] == ["REFUSED"] * 3
    assert len(refused_events(h, case)) == 3
    ids = {r.id for r in h.store.evidence(case.dispute.merchant_id, "N1")}
    assert "N1-carrier" not in ids


def test_every_tool_call_gets_a_tool_result():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM(
        [
            reply(None, call("get_carrier_tracking", 1, key="TRK-N1"), call("nope", 2)),
            finish(),
        ]
    )
    LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
    second = llm.calls[1]["messages"]
    results = [m for m in second if m.role is Role.TOOL]
    assert [m.tool_call_id for m in results] == ["call_1", "call_2"]


def test_model_failure_stops_cleanly():
    h, case, tools = investigation("N1")
    result = LLMInvestigator(ScriptedLLM([])).investigate(case.dispute, DELIVERY, tools)
    assert result.calls == 0 and result.run.stop.startswith("LLM_ERROR")


def test_refusal_and_max_tokens_stop_the_round():
    for stop in (StopReason.REFUSAL, StopReason.MAX_TOKENS):
        h, case, tools = investigation("N1")
        llm = ScriptedLLM([reply("no", stop=stop)])
        result = LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
        assert result.run.stop == str(stop) and result.calls == 0


def test_a_reply_without_tool_calls_ends_the_round():
    h, case, tools = investigation("N1")
    result = LLMInvestigator(ScriptedLLM([reply("I think we are done.")])).investigate(
        case.dispute, DELIVERY, tools
    )
    assert result.run.stop == "NO_TOOL_CALL"


def test_the_turn_cap_holds():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM([reply(None, call("get_login_history", n, key="x")) for n in range(10)])
    result = LLMInvestigator(llm, max_turns=3).investigate(case.dispute, DELIVERY, tools)
    assert (result.run.turns, result.run.stop) == (3, "TURN_LIMIT")
    assert len(llm.calls) == 3


def test_the_per_round_lookup_cap_holds():
    h, case, tools = investigation("S5")
    targets = [E.CANCELLATION_TERMS_ACCEPTED, E.USAGE_AFTER_RENEWAL]
    llm = ScriptedLLM(
        [
            reply(
                None,
                call("get_login_history", 1, key="CUST-S5"),
                call("get_policy_acceptance", 2, key="CUST-S5"),
            )
        ]
    )
    result = LLMInvestigator(llm, max_calls_per_round=1).investigate(case.dispute, targets, tools)
    assert result.calls == 1
    assert [c.outcome for c in result.run.tool_calls] == ["FETCHED", "REFUSED"]
    assert result.run.stop == "ROUND_LIMIT"


def test_no_model_call_when_nothing_is_offered():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM([])
    result = LLMInvestigator(llm).investigate(case.dispute, [E.THREE_DS_AUTHENTICATED], tools)
    assert result.calls == 0 and result.run is None and llm.calls == []


# --- untrusted text --------------------------------------------------------------------------


def test_customer_written_text_is_never_shown_to_the_model():
    # A1: the customer's email tries to instruct the agent.
    h, case, tools = investigation("A1")
    llm = ScriptedLLM([reply(None, call("get_carrier_tracking", key="TRK-A1")), finish()])
    LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
    sent = all_text_sent(llm)
    assert "ignore previous instructions" not in sent.lower()
    assert "A1-email-1" in sent and "withheld" in sent


def test_merchant_record_fields_arrive_inside_data_blocks():
    h, case, tools = investigation("N1")
    llm = ScriptedLLM([reply(None, call("get_carrier_tracking", key="TRK-N1")), finish()])
    LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
    result_msg = next(m for m in llm.calls[1]["messages"] if m.role is Role.TOOL)
    assert result_msg.text.startswith('<untrusted_data source="get_carrier_tracking:TRK-N1">')
    assert "DELIVERED" in result_msg.text


def test_the_system_prompt_is_static_and_holds_no_case_data():
    seen = set()
    for cid in ("N1", "A1"):
        h, case, tools = investigation(cid)
        llm = ScriptedLLM([finish()])
        LLMInvestigator(llm).investigate(case.dispute, DELIVERY, tools)
        seen.add(llm.calls[0]["system"])
    assert seen == {INVESTIGATOR_SYSTEM}
    assert "N1" not in INVESTIGATOR_SYSTEM and "A1" not in INVESTIGATOR_SYSTEM
