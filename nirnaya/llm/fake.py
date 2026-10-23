"""A scripted LLM for tests and offline runs: replays canned responses, records every call."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from nirnaya.llm.types import LLMError, LLMResponse, Message, StopReason, ToolCall, ToolSpec, Usage


def reply(
    text: str | None = None, *tool_calls: ToolCall, stop: StopReason | None = None
) -> LLMResponse:
    """Build a canned response."""
    return LLMResponse(
        provider="fake",
        model="fake",
        text=text,
        tool_calls=tool_calls,
        stop=stop or (StopReason.TOOL_USE if tool_calls else StopReason.END),
        usage=Usage(input_tokens=10, output_tokens=5),
    )


@dataclass
class ScriptedLLM:
    responses: list[LLMResponse]
    provider: str = "fake"
    model: str = "fake"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        force_tool: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "force_tool": force_tool,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise LLMError("scripted LLM has no more responses")
        return self.responses.pop(0)
