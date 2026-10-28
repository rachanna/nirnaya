"""LangSmith tracing of model calls: opt-in, provider-neutral, carrying case and prompt context.
Offline: the traced call is replaced by a recorder, so nothing is sent."""

import pytest

from nirnaya.llm import Message, Role, StopReason, ToolCall, ToolSpec
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.fake import ScriptedLLM, reply
from nirnaya.llm.tracing import (
    TracedLLM,
    TracingSettings,
    annotate,
    current,
    trace_inputs,
    trace_outputs,
    tracing_settings,
)

ON = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "lsv2-test"}
TOOL = ToolSpec(name="get_order", description="Look up.", input_schema={"type": "object"})


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LANGSMITH_TRACING": "true"},  # no key
        {"LANGSMITH_TRACING": "false", "LANGSMITH_API_KEY": "k"},
        {"LANGSMITH_API_KEY": "k"},  # a key alone does not turn tracing on
    ],
)
def test_tracing_is_off_unless_asked_for_with_a_key(env):
    assert tracing_settings(env) is None


def test_tracing_settings_read_project_and_endpoint():
    s = tracing_settings(
        {**ON, "LANGSMITH_ENDPOINT": "https://eu.example", "LANGSMITH_PROJECT": "p"}
    )
    assert s == TracingSettings(api_key="lsv2-test", endpoint="https://eu.example", project="p")
    assert tracing_settings(ON).project == "nirnaya"  # the default project


def test_make_llm_wraps_only_when_tracing_is_on():
    env = {"ANTHROPIC_API_KEY": "sk-test"}
    assert not isinstance(make_llm(llm_settings_from_env(env), env), TracedLLM)
    traced = make_llm(llm_settings_from_env({**env, **ON}), {**env, **ON})
    assert isinstance(traced, TracedLLM)
    assert (traced.provider, traced.model) == ("anthropic", "claude-sonnet-5-5")


def test_inputs_are_logged_as_chat_messages_with_tools():
    msgs = [
        Message.user("Case facts."),
        Message(
            role=Role.ASSISTANT,
            text="Looking.",
            tool_calls=(ToolCall(id="c1", name="get_order", arguments={"key": "O-1"}),),
        ),
        Message.tool_result("c1", "{}"),
    ]
    out = trace_inputs({"system": "Rules.", "messages": msgs, "tools": [TOOL], "force_tool": None})
    system, user, assistant, tool = out["messages"]
    assert system == {"role": "system", "content": "Rules."}
    assert user == {"role": "user", "content": "Case facts."}
    assert assistant["tool_calls"][0]["function"] == {
        "name": "get_order",
        "arguments": '{"key": "O-1"}',
    }
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": "{}"}
    assert out["tools"][0]["name"] == "get_order"


def test_outputs_carry_the_reply_tool_calls_and_token_usage():
    r = reply("ok", ToolCall(id="c1", name="finish", arguments={"reason": "done"}))
    out = trace_outputs(r)
    message = out["choices"][0]["message"]
    assert message["content"] == "ok" and message["tool_calls"][0]["function"]["name"] == "finish"
    assert out["usage_metadata"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert out["stop"] == str(StopReason.TOOL_USE)


def test_a_traced_call_passes_through_and_carries_the_annotated_context():
    inner = ScriptedLLM([reply("done")])
    traced = TracedLLM(inner, TracingSettings(api_key="k", endpoint=None, project="nirnaya"))
    seen = {}

    def recorder(**kwargs):
        seen.update(kwargs["langsmith_extra"])
        kwargs.pop("langsmith_extra")
        return traced._call(**kwargs)

    traced._traced = recorder
    prompts = ("drafter_system@v1#abc", "drafter_user@v1#def")
    with annotate(step="draft", case_id="N1", merchant_id="m", prompts=prompts):
        r = traced.complete(system="s", messages=[Message.user("u")], max_tokens=10)
    assert r.text == "done" and inner.calls[0]["max_tokens"] == 10
    assert seen["name"] == "draft"
    assert seen["metadata"]["case_id"] == "N1"
    assert seen["metadata"]["ls_provider"] == "fake" and seen["metadata"]["ls_model_name"] == "fake"
    assert "drafter_system@v1" in seen["tags"] and "nirnaya" in seen["tags"]
    assert current() == {}  # the context ends with the block


def test_annotations_nest_and_unwind():
    with annotate(case_id="N1"):
        with annotate(step="investigate"):
            assert current() == {"case_id": "N1", "step": "investigate"}
        assert current() == {"case_id": "N1"}
    assert current() == {}


class ContextRecorder:
    """A model that records the trace context it was called in, then replays canned replies."""

    provider = "fake"
    model = "fake"

    def __init__(self, *responses):
        self.responses, self.contexts = list(responses), []

    def complete(self, **kwargs):
        self.contexts.append(current())
        return self.responses.pop(0)


def test_the_drafter_and_investigator_label_their_calls():
    from nirnaya.agent.llm_drafter import LLMDrafter
    from nirnaya.agent.llm_investigator import LLMInvestigator
    from tests.agent.llm_helpers import finish, investigation
    from tests.harness import CASES

    h, case, tools = investigation("N1")
    llm = ContextRecorder(finish())
    inv = LLMInvestigator(llm)
    from nirnaya.models import EvidenceType

    inv.investigate(case.dispute, [EvidenceType.DELIVERY_CONFIRMED], tools)
    (ctx,) = llm.contexts
    assert (ctx["step"], ctx["case_id"]) == ("investigate", "N1")
    assert ctx["prompts"] == (inv.system.ref, inv.user.ref, inv.tools_asset.ref)

    llm = ContextRecorder(
        reply(None, ToolCall(id="t", name="submit_claims", arguments={"claims": []}))
    )
    drafter = LLMDrafter(llm)
    drafter.draft(CASES["N1"].dispute, CASES["N1"].evidence_records())
    (ctx,) = llm.contexts
    assert (ctx["step"], ctx["case_id"], ctx["merchant_id"]) == (
        "draft",
        "N1",
        CASES["N1"].dispute.merchant_id,
    )
    assert ctx["prompts"][0] == drafter.system.ref


def test_the_baseline_labels_its_calls_with_variant_and_prompts():
    from nirnaya.evaluation.cases import load_cases
    from nirnaya.evaluation.llm_baseline import ask, load_prompts

    case = next(c for c in load_cases() if c.id == "K2")
    prompts = load_prompts("informed", {})
    llm = ContextRecorder(
        reply(None, ToolCall(id="t", name="decide", arguments={"action": "ACCEPT"}))
    )
    ask(llm, case, prompts)
    (ctx,) = llm.contexts
    assert (ctx["step"], ctx["case_id"], ctx["variant"]) == ("baseline", "K2", "informed")
    assert ctx["prompts"] == prompts.refs
