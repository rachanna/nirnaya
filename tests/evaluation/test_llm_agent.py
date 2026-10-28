"""The model-agent comparison, offline: a stand-in model that stops at once, drafts nothing."""

from nirnaya.evaluation.cases import load_cases
from nirnaya.evaluation.llm_agent import compare, main, run_case
from nirnaya.llm.types import LLMResponse, StopReason, ToolCall, Usage

CASES = {c.id: c for c in load_cases()}


class Quiet:
    """Finishes every investigation round and submits no claims."""

    provider = model = "fake"

    def complete(self, *, system, messages, tools=(), force_tool=None, max_tokens=2048):
        names = [t.name for t in tools]
        name, args = (
            ("finish", {"reason": "x"}) if "finish" in names else (names[0], {"claims": []})
        )
        return LLMResponse(
            provider="fake", model="fake", text=None,
            tool_calls=(ToolCall(id="c", name=name, arguments=args),),
            stop=StopReason.TOOL_USE, usage=Usage(input_tokens=3, output_tokens=2),
        )  # fmt: skip


def test_a_case_run_records_route_claims_tools_and_tokens():
    scripted = run_case(CASES["N1"], llm=None)
    assert (scripted["action"], scripted["tokens"]) == ("CHALLENGE", 0)
    # Code's own intake lookups (order, helpdesk) plus the agent's carrier lookup that flips N1.
    assert scripted["lookups"] == 3
    quiet = run_case(CASES["N1"], llm=Quiet())
    assert quiet["tokens"] > 0 and quiet["lookups"] == 2  # never looked further: no scan
    assert quiet["action"] != "CHALLENGE"


def test_compare_lists_every_difference():
    a = {"N1": {"action": "CHALLENGE", "unsupported": 0, "lookups": 1, "tokens": 0}}
    b = {"N1": {"action": "ACCEPT", "unsupported": 0, "lookups": 0, "tokens": 9}}
    out = compare(a, b)
    assert out["route_differs"] == ["N1"]
    assert out["scripted"]["lookups"] == 1 and out["llm"]["tokens"] == 9


def test_the_live_run_is_opt_in():
    lines = []
    assert main(env={}, out=lines.append) == 2
    assert "NIRNAYA_LIVE_LLM=1" in lines[0]
