from collections.abc import Sequence
from typing import Protocol

from nirnaya.llm.types import LLMResponse, Message, ToolSpec


class LLMClient(Protocol):
    """One model call. `system` must be static instructions only: case data and any
    customer or support text go in messages, wrapped with `untrusted()`, never in system."""

    provider: str
    model: str

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        force_tool: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...
