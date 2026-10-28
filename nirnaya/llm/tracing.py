"""LangSmith tracing of model calls. Optional and off by default.

    LANGSMITH_TRACING=true      turn it on (a key alone does not)
    LANGSMITH_API_KEY           required when on (never logged)
    LANGSMITH_ENDPOINT          optional, for a regional or self-hosted LangSmith
    LANGSMITH_PROJECT           optional, default "nirnaya"

make_llm() wraps the provider client in TracedLLM when tracing is on, so agent code keeps
depending only on LLMClient. Each call is logged as an LLM run: the system prompt, messages and
tools sent, the reply and tool calls, token usage, and the context the caller set with
annotate(): the step, the case, and the prompt versions (name@version#hash) that produced it.

What is sent: everything the model sees. In this project that is SYNTHETIC case data or sandbox
test data; customer-written text reaches a trace only where it reaches the model (the LLM-only
baseline). Tracing records; it decides nothing.
"""

import atexit
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from nirnaya.llm.base import LLMClient
from nirnaya.llm.types import LLMResponse, Message, Role, ToolCall, ToolSpec

DEFAULT_PROJECT = "nirnaya"
_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("nirnaya_trace_context", default=None)


@dataclass(frozen=True)
class TracingSettings:
    api_key: str
    endpoint: str | None
    project: str


def tracing_settings(env: Mapping[str, str] = os.environ) -> TracingSettings | None:
    if env.get("LANGSMITH_TRACING", "").strip().lower() not in {"true", "1"}:
        return None
    key = (env.get("LANGSMITH_API_KEY") or "").strip()
    if not key:
        return None
    return TracingSettings(
        api_key=key,
        endpoint=(env.get("LANGSMITH_ENDPOINT") or "").strip() or None,
        project=(env.get("LANGSMITH_PROJECT") or "").strip() or DEFAULT_PROJECT,
    )


# --- context: what the caller is doing, attached to every model call inside the block -----------


def current() -> dict[str, Any]:
    return dict(_CONTEXT.get() or {})


@contextmanager
def annotate(**context: Any) -> Iterator[None]:
    """Attach context (step, case_id, prompts, ...) to model calls made inside the block.
    Costs nothing when tracing is off."""
    token = _CONTEXT.set({**current(), **context})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


# --- what a run records ---------------------------------------------------------------------------


def _tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments, default=str)},
    }


def _message(m: Message) -> dict[str, Any]:
    if m.role is Role.USER:
        return {"role": "user", "content": m.text}
    if m.role is Role.ASSISTANT:
        out: dict[str, Any] = {"role": "assistant", "content": m.text or ""}
        if m.tool_calls:
            out["tool_calls"] = [_tool_call(c) for c in m.tool_calls]
        return out
    return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text}


def trace_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The call's arguments as chat messages (OpenAI-style, which LangSmith renders)."""
    tools: Sequence[ToolSpec] = inputs.get("tools") or ()
    return {
        "messages": [{"role": "system", "content": inputs.get("system", "")}]
        + [_message(m) for m in inputs.get("messages") or ()],
        "tools": [t.model_dump(mode="json") for t in tools],
        "force_tool": inputs.get("force_tool"),
        "max_tokens": inputs.get("max_tokens"),
    }


def trace_outputs(response: LLMResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.text or ""}
    if response.tool_calls:
        message["tool_calls"] = [_tool_call(c) for c in response.tool_calls]
    usage = response.usage
    return {
        "choices": [{"message": message}],
        "stop": str(response.stop),
        "usage_metadata": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
        },
    }


# --- the wrapper ---------------------------------------------------------------------------------


class TracedLLM:
    """An LLMClient that logs each call to LangSmith and otherwise behaves like the client it
    wraps. A tracing failure never fails the call: the SDK sends in the background."""

    def __init__(self, inner: LLMClient, settings: TracingSettings) -> None:
        from langsmith import Client, traceable

        self.inner = inner
        self.settings = settings
        self._client = Client(api_key=settings.api_key, api_url=settings.endpoint)
        self._traced = traceable(
            run_type="llm",
            client=self._client,
            project_name=settings.project,
            enabled=True,
            process_inputs=trace_inputs,
            process_outputs=trace_outputs,
        )(self._call)
        atexit.register(self.flush)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def _call(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        force_tool: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        return self.inner.complete(
            system=system,
            messages=messages,
            tools=tools,
            force_tool=force_tool,
            max_tokens=max_tokens,
        )

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        force_tool: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        context = current()
        prompts = [str(p) for p in context.get("prompts", ())]
        metadata = {
            **context,
            "prompts": prompts,
            "ls_provider": self.provider,
            "ls_model_name": self.model,
        }
        tags = ["nirnaya", str(context.get("step", "llm"))] + [p.split("#")[0] for p in prompts]
        return self._traced(
            system=system,
            messages=messages,
            tools=tools,
            force_tool=force_tool,
            max_tokens=max_tokens,
            langsmith_extra={
                "name": str(context.get("step", f"{self.provider}.complete")),
                "metadata": metadata,
                "tags": tags,
            },
        )

    def flush(self) -> None:
        with suppress(Exception):  # tracing must never break a run at exit
            self._client.flush()


def maybe_traced(client: LLMClient, env: Mapping[str, str] = os.environ) -> LLMClient:
    settings = tracing_settings(env)
    return client if settings is None else TracedLLM(client, settings)
