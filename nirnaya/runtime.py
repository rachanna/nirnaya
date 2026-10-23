"""Wiring: choose the investigator and drafter, and build an orchestrator.

NIRNAYA_AGENT   scripted (default: no model, no tokens) | llm (uses nirnaya.llm.make_llm)
"""

import os
from collections.abc import Mapping

from nirnaya.agent.llm_drafter import LLMDrafter
from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.agent.tools import MerchantSystems
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError
from nirnaya.models.money import FxTable
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.ports import DisputeActions, DisputeSource, Drafter, Investigator
from nirnaya.orchestrator.settings import Settings
from nirnaya.store.repository import Store


def build_agents(
    settings: Settings, env: Mapping[str, str] = os.environ, llm: LLMClient | None = None
) -> tuple[Investigator, Drafter]:
    mode = env.get("NIRNAYA_AGENT", "scripted").strip().lower()
    if mode == "scripted":
        return ScriptedInvestigator(), ScriptedDrafter()
    if mode != "llm":
        raise LLMConfigError(f"NIRNAYA_AGENT must be scripted or llm, not {mode!r}")
    client = llm or make_llm(llm_settings_from_env(env), env)
    investigator = LLMInvestigator(
        client,
        max_calls_per_round=settings.max_calls_per_round,
        max_turns=settings.max_turns_per_round,
    )
    return investigator, LLMDrafter(client)


def build_orchestrator(
    store: Store,
    source: DisputeSource,
    actions: DisputeActions,
    systems: MerchantSystems,
    settings: Settings,
    *,
    env: Mapping[str, str] = os.environ,
    llm: LLMClient | None = None,
    fx: FxTable | None = None,
    synthetic: bool = True,
) -> Orchestrator:
    investigator, drafter = build_agents(settings, env, llm)
    return Orchestrator(
        store, source, actions, systems, investigator, drafter, settings, fx=fx, synthetic=synthetic
    )
