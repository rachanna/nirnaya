"""OpenAI Chat Completions adapter. Also works with OpenAI-compatible endpoints (base_url)."""

import json
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
    "stop": StopReason.END,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


def _messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role is Role.USER:
            out.append({"role": "user", "content": m.text})
        elif m.role is Role.ASSISTANT:
            msg: dict[str, Any] = {"role": "assistant", "content": m.text}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        else:
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text})
    return out


class OpenAIClient:
    provider = "openai"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
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
            "messages": _messages(system, messages),
            "max_completion_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        if force_tool:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        try:
            r = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # any SDK or transport failure: fail closed
            raise LLMError(f"openai call failed: {type(e).__name__}") from e
        if not r.choices:
            raise LLMError("openai returned no choices")
        choice = r.choices[0]
        msg = choice.message
        calls = []
        for c in msg.tool_calls or ():
            try:
                arguments = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError as e:
                raise LLMError(f"tool call {c.function.name!r} has invalid JSON arguments") from e
            if not isinstance(arguments, dict):
                raise LLMError(f"tool call {c.function.name!r} arguments are not an object")
            calls.append(ToolCall(id=c.id, name=c.function.name, arguments=arguments))
        stop = (
            StopReason.REFUSAL
            if getattr(msg, "refusal", None)
            else _STOP.get(choice.finish_reason, StopReason.OTHER)
        )
        usage = getattr(r, "usage", None)
        return LLMResponse(
            provider=self.provider,
            model=getattr(r, "model", self.model) or self.model,
            text=msg.content or None,
            tool_calls=tuple(calls),
            stop=stop,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
        )
