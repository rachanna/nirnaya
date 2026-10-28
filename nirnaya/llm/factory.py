"""Choose the model provider from configuration.

NIRNAYA_LLM_PROVIDER  anthropic (default) | openai
NIRNAYA_MODEL         model id; defaults to claude-sonnet-5-5 for anthropic, required for openai
ANTHROPIC_API_KEY / OPENAI_API_KEY   the provider's key (never logged)
OPENAI_BASE_URL       optional, for an OpenAI-compatible endpoint
LANGSMITH_TRACING     true to trace every call to LangSmith (nirnaya.llm.tracing)
"""

import os
from collections.abc import Mapping
from typing import Literal

from nirnaya.llm.anthropic_client import AnthropicClient
from nirnaya.llm.base import LLMClient
from nirnaya.llm.openai_client import OpenAIClient
from nirnaya.llm.tracing import maybe_traced
from nirnaya.llm.types import LLMConfigError
from nirnaya.models.base import DomainModel, NonBlankStr

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMSettings(DomainModel):
    provider: Literal["anthropic", "openai"]
    model: NonBlankStr
    base_url: str | None = None


def llm_settings_from_env(env: Mapping[str, str] = os.environ) -> LLMSettings:
    provider = env.get("NIRNAYA_LLM_PROVIDER", "anthropic").strip().lower()
    if provider not in KEY_VARS:
        raise LLMConfigError(f"NIRNAYA_LLM_PROVIDER must be anthropic or openai, not {provider!r}")
    model = (env.get("NIRNAYA_MODEL") or "").strip()
    if not model:
        if provider != "anthropic":
            raise LLMConfigError("NIRNAYA_MODEL is required when NIRNAYA_LLM_PROVIDER=openai")
        model = DEFAULT_ANTHROPIC_MODEL
    base_url = None
    if provider == "openai":
        base_url = (env.get("OPENAI_BASE_URL") or "").strip() or None
    return LLMSettings(provider=provider, model=model, base_url=base_url)


def make_llm(settings: LLMSettings, env: Mapping[str, str] = os.environ) -> LLMClient:
    key = (env.get(KEY_VARS[settings.provider]) or "").strip()
    if not key:
        raise LLMConfigError(f"{KEY_VARS[settings.provider]} is not set")
    client: LLMClient
    if settings.provider == "anthropic":
        client = AnthropicClient(settings.model, api_key=key)
    else:
        client = OpenAIClient(settings.model, api_key=key, base_url=settings.base_url)
    return maybe_traced(client, env)
