"""Set up a case for the LLM investigator or drafter, and script model replies."""

from nirnaya.agent.tools import Tool, ToolGateway
from nirnaya.ingest import ingest
from nirnaya.llm.fake import reply
from nirnaya.llm.types import ToolCall
from tests.harness import CASES, Harness


def investigation(case_id: str, max_calls: int = 8):
    """A case ingested with the baseline lookups done (order and helpdesk), as at INGESTED."""
    h = Harness()
    case = CASES[case_id]
    h.stage(case)
    ingest(h.store, h.aw, case.dispute.merchant_id, h.now(case))
    tools = ToolGateway(h.store, h.world, case.dispute, 1, now=h.now(case), max_calls=max_calls)
    for tool in (Tool.ORDER, Tool.HELPDESK):
        for key in tools.lookup_keys(tool):
            tools.fetch(tool, key)
    return h, case, tools


def call(name: str, n: int = 1, **arguments) -> ToolCall:
    return ToolCall(id=f"call_{n}", name=name, arguments=arguments)


def finish(n: int = 99):
    return reply(None, call("finish", n, reason="nothing more is worth fetching"))


def all_text_sent(llm) -> str:
    """Everything the model was shown, across every call: system prompts and messages."""
    parts = []
    for c in llm.calls:
        parts.append(c["system"])
        parts += [m.text or "" for m in c["messages"]]
    return "\n".join(parts)
