"""Dev runner: the kit 4 cases through the full pipeline, offline Airwallex, chosen agent.

    uv run python -m nirnaya.demo            # uses .env; NIRNAYA_AGENT=scripted by default
    NIRNAYA_AGENT=llm uv run python -m nirnaya.demo

Airwallex is the in-memory fake and merchant systems are SYNTHETIC, so writes are real only
inside the fake. The model is the only thing that may use the network (and only with
NIRNAYA_AGENT=llm).
"""

import os
import sys
from datetime import timedelta

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.config.env import load_dotenv
from nirnaya.evaluation.cases import load_cases, synthetic_world
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.llm.types import LLMConfigError
from nirnaya.orchestrator.settings import Settings
from nirnaya.runtime import build_orchestrator
from nirnaya.store import Store, connect, verify_chain
from nirnaya.trace import case_trace

CASE_IDS = ("K1", "K2", "K3", "S5", "N1")


def main(case_ids: tuple[str, ...] = CASE_IDS) -> int:
    load_dotenv()
    cases = {c.id: c for c in load_cases()}
    store = Store(connect())
    store.register_merchant(
        DEMO_POLICY.merchant_id,
        airwallex_account_id="acct-demo",
        policy=DEMO_POLICY,
        delegated_authority=True,
    )
    aw = FakeAirwallex()
    for cid in case_ids:
        c = cases[cid]
        aw.add_dispute(
            c.dispute,
            attempt=c.payment_attempt,
            intent_owner="same" if c.ownership_resolved else None,
        )
    settings = Settings(dry_run=False)  # the fake is the only Airwallex here
    try:
        orch = build_orchestrator(store, aw, aw, synthetic_world(list(cases.values())), settings)
    except LLMConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    agent = os.environ.get("NIRNAYA_AGENT", "scripted")
    print(
        f"Agent: {agent}  Provider: {os.environ.get('NIRNAYA_LLM_PROVIDER', 'anthropic')}"
        f"  Model: {os.environ.get('NIRNAYA_MODEL', '(default)')}"
    )

    now = cases[case_ids[0]].dispute.opened_at + timedelta(hours=1)
    orch.run_until_idle(now)
    if "K1" in case_ids:  # kit step: the issuer rejects case 1's evidence
        aw.simulate_escalate("K1", due_at=now + timedelta(days=20))
        orch.run_until_idle(now + timedelta(days=5))

    m = DEMO_POLICY.merchant_id
    for cid in case_ids:
        case = store.case(m, cid)
        print(
            f"\n== {cid}: {cases[cid].title}\n   ends {case.state} (cycle {case.cycle});"
            f" expected route {cases[cid].expected.route}"
        )
        for line in case_trace(store, m, cid):
            print(line)
    print("\nAudit chain intact:", verify_chain(store.conn) is None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
