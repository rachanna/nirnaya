import pytest

from nirnaya.agent.llm_drafter import LLMDrafter
from nirnaya.agent.llm_investigator import LLMInvestigator
from nirnaya.agent.llm_reader import LLMReader
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.env import load_dotenv
from nirnaya.llm.fake import ScriptedLLM
from nirnaya.llm.types import LLMConfigError
from nirnaya.orchestrator.settings import Settings
from nirnaya.runtime import build_agents, build_orchestrator, build_reader
from tests.harness import Harness


def test_scripted_is_the_default_and_spends_no_tokens():
    inv, dra = build_agents(Settings(), env={})
    assert isinstance(inv, ScriptedInvestigator) and isinstance(dra, ScriptedDrafter)


def test_llm_mode_uses_the_given_client_and_settings():
    llm = ScriptedLLM([])
    inv, dra = build_agents(Settings(max_calls_per_round=2), env={"NIRNAYA_AGENT": "llm"}, llm=llm)
    assert isinstance(inv, LLMInvestigator) and isinstance(dra, LLMDrafter)
    assert inv.llm is llm and dra.llm is llm and inv.max_calls == 2


def test_notes_are_read_only_in_llm_mode_with_the_same_client():
    assert build_reader(env={}) is None
    llm = ScriptedLLM([])
    reader = build_reader(env={"NIRNAYA_AGENT": "llm"}, llm=llm)
    assert isinstance(reader, LLMReader) and reader.llm is llm
    h = Harness()
    orch = build_orchestrator(
        h.store, h.aw, h.aw, h.world, h.settings, env={"NIRNAYA_AGENT": "llm"}, llm=llm
    )
    assert orch.reader.llm is llm and orch.investigator.llm is llm and orch.drafter.llm is llm
    scripted = build_orchestrator(h.store, h.aw, h.aw, h.world, h.settings, env={})
    assert scripted.reader is None


def test_llm_mode_without_a_key_fails_with_a_clear_message():
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        build_agents(Settings(), env={"NIRNAYA_AGENT": "llm"})
    env = {"NIRNAYA_AGENT": "llm", "NIRNAYA_LLM_PROVIDER": "openai", "NIRNAYA_MODEL": "m"}
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        build_agents(Settings(), env=env)


def test_unknown_agent_mode_is_rejected():
    with pytest.raises(LLMConfigError, match="scripted or llm"):
        build_agents(Settings(), env={"NIRNAYA_AGENT": "autopilot"})


def test_dotenv_loads_values_but_never_overrides_the_environment(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comment\n\nNIRNAYA_AGENT=llm\nexport NIRNAYA_MODEL='dev-model'\n"
        'OPENAI_API_KEY="sk-file"\nEMPTY=\nNOT A PAIR\n',
        encoding="utf-8",
    )
    env = {"OPENAI_API_KEY": "sk-real"}
    loaded = load_dotenv(f, env)
    assert loaded == ["NIRNAYA_AGENT", "NIRNAYA_MODEL"]
    assert env == {
        "OPENAI_API_KEY": "sk-real",
        "NIRNAYA_AGENT": "llm",
        "NIRNAYA_MODEL": "dev-model",
    }


def test_missing_dotenv_is_fine(tmp_path):
    assert load_dotenv(tmp_path / "absent.env", {}) == []
