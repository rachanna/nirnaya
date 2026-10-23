"""Opt-in: the real configured model on a few cases. Not part of the normal run.

    NIRNAYA_LIVE_LLM=1 uv run pytest -m live -s

A real model is non-deterministic, so this reports agreement rather than gating on it, but
two things must always hold: nothing unverified is written, and the audit chain is intact.
"""

import os

import pytest

from nirnaya.config.env import load_dotenv
from nirnaya.llm.factory import llm_settings_from_env, make_llm
from nirnaya.llm.types import LLMConfigError
from nirnaya.orchestrator.states import CaseState
from nirnaya.runtime import build_orchestrator
from nirnaya.store import verify_chain
from nirnaya.verifier import VerificationOutcome
from tests.harness import CASES, Harness

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def llm():
    load_dotenv()
    if os.environ.get("NIRNAYA_LIVE_LLM") != "1":
        pytest.skip("set NIRNAYA_LIVE_LLM=1 to run live model tests")
    try:
        return make_llm(llm_settings_from_env())
    except LLMConfigError as e:
        pytest.skip(str(e))


@pytest.mark.parametrize("case_id", ["K1", "N1", "S5", "K3", "A1"])
def test_live_model_on_a_case(llm, case_id):
    h = Harness()
    case = CASES[case_id]
    h.stage(case)
    orch = build_orchestrator(
        h.store, h.aw, h.aw, h.world, h.settings, env={"NIRNAYA_AGENT": "llm"}, llm=llm
    )
    orch.run_until_idle(h.now(case))
    m, cid = case.dispute.merchant_id, case.dispute.id
    state = h.store.case(m, cid).state
    writes = [(w.action, w.status) for w in h.store.writes(m, cid)]
    runs = h.store.agent_runs(m, cid, 1)
    print(
        f"\n{case_id}: state={state} writes={writes} expected={case.expected.route}"
        f" runs={[(r['kind'], r['stop'], r['input_tokens'] + r['output_tokens']) for r in runs]}"
    )

    assert verify_chain(h.store.conn) is None
    for w in h.store.writes(m, cid):
        if w.action == "CHALLENGE":  # every challenge went out on a PASSED verification
            draft = h.store.latest_draft(m, cid, w.cycle)
            assert (
                h.store.verification(m, cid, w.cycle, draft).outcome is VerificationOutcome.PASSED
            )
    assert state in {s.value for s in CaseState}
