"""Record every model call as it happens, so the web demo can show the live call flow.

RecordingLLM wraps the LLMClient the agents use and otherwise behaves exactly like it. A call is
listed the moment it starts (status "running") and filled in when the model answers. What it
records is what the model saw and said: the step and case (from tracing.annotate), the prompt
versions, the tools offered, the new messages of this turn, the reply, the tool calls, tokens and
time. It decides nothing; the agents, engine, verifier and gateway are unchanged.
"""

import threading
import time
from collections.abc import Sequence
from typing import Any

from nirnaya.llm.base import LLMClient
from nirnaya.llm.tracing import current
from nirnaya.llm.types import LLMError, LLMResponse, Message, Role, ToolSpec

TEXT_LIMIT = 4000  # characters of one message or tool-argument dump kept for display


def _clip(text: str | None) -> str:
    text = text or ""
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + " … (cut for display)"


def _new_turn(messages: Sequence[Message]) -> list[dict[str, str]]:
    """The messages after the model's last turn: what is new to it on this call."""
    last = max((i for i, m in enumerate(messages) if m.role is Role.ASSISTANT), default=-1)
    return [
        {"role": str(m.role), "text": _clip(m.text)}
        for m in messages[last + 1 :]
        if m.role is not Role.ASSISTANT
    ]


class RecordingLLM:
    def __init__(self, inner: LLMClient, clock: Any = time.perf_counter) -> None:  # noqa: ANN401
        self.inner = inner
        self._clock = clock
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

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
        started = self._clock()
        with self._lock:
            entry: dict[str, Any] = {
                "n": sum(c["case_id"] == context.get("case_id") for c in self._calls) + 1,
                "case_id": context.get("case_id"),
                "step": str(context.get("step", "call")),
                "turn": context.get("turn"),
                "prompts": [str(p).split("#")[0] for p in context.get("prompts", ())],
                "tools": [t.name for t in tools],
                "forced": force_tool,
                "sent": _new_turn(messages),
                "status": "running",
            }
            self._calls.append(entry)
        try:
            response = self.inner.complete(
                system=system,
                messages=messages,
                tools=tools,
                force_tool=force_tool,
                max_tokens=max_tokens,
            )
        except LLMError as e:
            with self._lock:
                entry.update(status="failed", error=str(e), ms=self._ms(started))
            raise
        with self._lock:
            entry.update(
                status="done",
                ms=self._ms(started),
                stop=str(response.stop),
                reply=_clip(response.text) or None,
                tool_calls=[
                    {"name": c.name, "arguments": c.arguments} for c in response.tool_calls
                ],
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        return response

    def _ms(self, started: float) -> int:
        return round((self._clock() - started) * 1000)

    def calls(self, case_id: str) -> list[dict[str, Any]]:
        """This case's calls so far, oldest first (copies; safe while a run is going)."""
        with self._lock:
            return [dict(c) for c in self._calls if c["case_id"] == case_id]
