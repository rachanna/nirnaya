"""Provider-neutral conversation types. Adapters translate these to and from each SDK."""

from enum import StrEnum
from typing import Any, Self

from pydantic import model_validator

from nirnaya.models.base import DomainModel, NonBlankStr


class LLMError(RuntimeError):
    """The model call failed or returned something unusable. Callers fail closed."""


class LLMConfigError(LLMError):
    """The provider, model or credentials are not configured."""


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # a tool result, answering one tool call


class ToolSpec(DomainModel):
    name: NonBlankStr
    description: NonBlankStr
    input_schema: dict[str, Any]


class ToolCall(DomainModel):
    id: NonBlankStr
    name: NonBlankStr
    arguments: dict[str, Any]


class Message(DomainModel):
    role: Role
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.role is Role.TOOL and (not self.tool_call_id or self.text is None):
            raise ValueError("a tool result needs tool_call_id and text")
        if self.role is Role.USER and (not self.text or self.tool_calls):
            raise ValueError("a user message is text only")
        if self.role is Role.ASSISTANT and not (self.text or self.tool_calls):
            raise ValueError("an assistant message needs text or tool calls")
        return self

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role=Role.USER, text=text)

    @classmethod
    def tool_result(cls, call_id: str, text: str) -> "Message":
        return cls(role=Role.TOOL, tool_call_id=call_id, text=text)


class StopReason(StrEnum):
    END = "END"
    TOOL_USE = "TOOL_USE"
    MAX_TOKENS = "MAX_TOKENS"
    REFUSAL = "REFUSAL"
    OTHER = "OTHER"


class Usage(DomainModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(DomainModel):
    provider: str
    model: str
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    stop: StopReason
    usage: Usage

    def as_message(self) -> Message:
        """The assistant turn, to send back on the next call."""
        return Message(role=Role.ASSISTANT, text=self.text or None, tool_calls=self.tool_calls)
