"""Anthropic Messages API adapter."""

from collections.abc import Sequence
from typing import Any

from nirnaya.llm.types import (
    LLMError,
    LLMResponse,
    Message,
    Role,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

_STOP = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}


def _messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Tool results become tool_result blocks in a user turn; consecutive results share one turn."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role is Role.USER:
            out.append({"role": "user", "content": m.text})
        elif m.role is Role.ASSISTANT:
            blocks: list[dict[str, Any]] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.text}
            previous = out[-1] if out else None
            if previous and previous["role"] == "user" and isinstance(previous["content"], list):
                previous["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self, model: str, *, api_key: str | None = None, client: Any = None, timeout: float = 60.0
    ) -> None:
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._client = client

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        force_tool: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": _messages(messages),
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        if force_tool:
            kwargs["tool_choice"] = {"type": "tool", "name": force_tool}
        try:
            r = self._client.messages.create(**kwargs)
        except Exception as e:  # any SDK or transport failure: fail closed
            raise LLMError(f"anthropic call failed: {type(e).__name__}") from e
        texts = [b.text for b in r.content if getattr(b, "type", None) == "text"]
        calls = tuple(
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in r.content
            if getattr(b, "type", None) == "tool_use"
        )
        usage = getattr(r, "usage", None)
        return LLMResponse(
            provider=self.provider,
            model=getattr(r, "model", self.model),
            text="".join(texts) or None,
            tool_calls=calls,
            stop=_STOP.get(r.stop_reason, StopReason.OTHER),
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            ),
        )
