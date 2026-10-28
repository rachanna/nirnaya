"""Anthropic Messages API adapter.

Current Claude models (Sonnet 5.5, Opus 5.5) reject a forced tool_choice with a 400. A forced
tool is therefore requested the documented way: tool_choice stays auto, the tool is marked
strict (its input always matches the schema), and the system prompt names it. Auto does not
guarantee a call, so a response without it is asked for once more; after that the caller sees
no tool call and fails closed.
"""

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


def strict_schema(schema: Any) -> Any:
    """A copy of a JSON schema with additionalProperties: false on every object (strict mode
    requires it). The provider-neutral ToolSpec is left untouched."""
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_schema(v) for k, v in schema.items()}
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
    return out


def _tool(spec: ToolSpec, *, strict: bool) -> dict[str, Any]:
    tool: dict[str, Any] = {"name": spec.name, "description": spec.description}
    if strict:
        tool["input_schema"] = strict_schema(spec.input_schema)
        tool["strict"] = True
    else:
        tool["input_schema"] = spec.input_schema
    return tool


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
        if force_tool:
            system = f"{system}\n\nAnswer by calling the {force_tool} tool exactly once."
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": _messages(messages),
        }
        if tools:
            kwargs["tools"] = [_tool(t, strict=t.name == force_tool) for t in tools]
        r = self._create(kwargs)
        usage = [r]
        if force_tool and not _calls(r, force_tool):
            r = self._create(kwargs)  # auto does not guarantee the call: ask once more
            usage.append(r)
        texts = [b.text for b in r.content if getattr(b, "type", None) == "text"]
        calls = tuple(
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in r.content
            if getattr(b, "type", None) == "tool_use"
        )
        return LLMResponse(
            provider=self.provider,
            model=getattr(r, "model", self.model),
            text="".join(texts) or None,
            tool_calls=calls,
            stop=_STOP.get(r.stop_reason, StopReason.OTHER),
            usage=Usage(
                input_tokens=sum(_tokens(x, "input_tokens") for x in usage),
                output_tokens=sum(_tokens(x, "output_tokens") for x in usage),
            ),
        )

    def _create(self, kwargs: dict[str, Any]) -> Any:
        try:
            return self._client.messages.create(**kwargs)
        except Exception as e:  # any SDK or transport failure: fail closed
            raise LLMError(f"anthropic call failed: {type(e).__name__}") from e


def _calls(r: Any, name: str) -> bool:
    return any(getattr(b, "type", None) == "tool_use" and b.name == name for b in r.content)


def _tokens(r: Any, field: str) -> int:
    return getattr(getattr(r, "usage", None), field, 0) or 0
