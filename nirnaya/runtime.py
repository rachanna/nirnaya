"""Wiring: choose the investigator, drafter and note reader, and build an orchestrator.

NIRNAYA_AGENT   scripted (default: no model, no tokens, notes not read)
                | llm (uses nirnaya.llm.make_llm; the model also reads notes, architecture 8.8)
"""

import os
from collections.abc import Mapping

from nirnaya.agent.llm_drafter import LLMDrafter
from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.llm_reader import LLMReader
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.agent.tools import MerchantSystems
from nirnaya.llm.base import LLMClient
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.tracing import tracing_settings
from nirnaya.llm.types import LLMConfigError
from nirnaya.models.money import FxTable
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.ports import (
    NULL_OBSERVER,
    DisputeActions,
    DisputeSource,
    Drafter,
    Investigator,
    Reader,
    StepObserver,
)
from nirnaya.orchestrator.settings import Settings
from nirnaya.store.repository import Store


def _mode(env: Mapping[str, str]) -> str:
    mode = env.get("NIRNAYA_AGENT", "scripted").strip().lower()
    if mode not in {"scripted", "llm"}:
        raise LLMConfigError(f"NIRNAYA_AGENT must be scripted or llm, not {mode!r}")
    return mode


def build_agents(
    settings: Settings, env: Mapping[str, str] = os.environ, llm: LLMClient | None = None
) -> tuple[Investigator, Drafter]:
    if _mode(env) == "scripted":
        return ScriptedInvestigator(), ScriptedDrafter()
    client = llm or make_llm(llm_settings_from_env(env), env)
    investigator = LLMInvestigator(
        client,
        max_calls_per_round=settings.max_calls_per_round,
        max_turns=settings.max_turns_per_round,
        env=env,
    )
    return investigator, LLMDrafter(client, env=env)


def build_reader(
    env: Mapping[str, str] = os.environ, llm: LLMClient | None = None
) -> Reader | None:
    """The note reader: the model in llm mode; none in scripted mode (notes are not read)."""
    if _mode(env) == "scripted":
        return None
    return LLMReader(llm or make_llm(llm_settings_from_env(env), env), env=env)


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
    if llm is None and _mode(env) == "llm":
        llm = make_llm(llm_settings_from_env(env), env)  # one client for every agent step
    investigator, drafter = build_agents(settings, env, llm)
    return Orchestrator(
        store,
        source,
        actions,
        systems,
        investigator,
        drafter,
        settings,
        fx=fx,
        synthetic=synthetic,
        observer=case_observer(env),
        reader=build_reader(env, llm),
    )


def case_observer(env: Mapping[str, str] = os.environ) -> StepObserver:
    """One LangSmith trace per case when LANGSMITH_TRACING=true; otherwise nothing."""
    tracing = tracing_settings(env)
    if tracing is None:
        return NULL_OBSERVER
    from nirnaya.integrations.langsmith.cases import CaseTracer

    return CaseTracer(tracing)
