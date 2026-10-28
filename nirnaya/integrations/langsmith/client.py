"""LangSmith connection settings, from env. A key is enough; tracing need not be on."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClientSettings:
    api_key: str
    endpoint: str | None


def client_settings(env: Mapping[str, str] = os.environ) -> ClientSettings | None:
    key = (env.get("LANGSMITH_API_KEY") or "").strip()
    if not key:
        return None
    return ClientSettings(key, (env.get("LANGSMITH_ENDPOINT") or "").strip() or None)


def make_client(settings: ClientSettings) -> Any:  # noqa: ANN401 - langsmith.Client
    from langsmith import Client

    return Client(api_key=settings.api_key, api_url=settings.endpoint)
