"""Provider-neutral LLM interface with Anthropic and OpenAI adapters, chosen by configuration."""

from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import LLMSettings, llm_settings_from_env, make_llm
from nirnaya.llm.types import (
    LLMConfigError,
    LLMError,
    LLMResponse,
    Message,
    Role,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)
from nirnaya.llm.untrusted import untrusted

__all__ = [
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMResponse",
    "LLMSettings",
    "Message",
    "Role",
    "StopReason",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "llm_settings_from_env",
    "make_llm",
    "untrusted",
]
