"""A demo session for the console: a store, the simulated Airwallex and the orchestrator.

The console shows the kit 4 cases (1, 1b, 2, 2-C, 3) end to end. Offline, Airwallex is the
in-memory fake and merchant systems are SYNTHETIC; writes land only in the fake. A sandbox run
written by `python -m nirnaya.sandbox_demo` can be opened to view instead (no actions).
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.llm.base import LLMClient
from nirnaya.models.enums import Action
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, close_manually
from nirnaya.orchestrator.settings import Settings
from nirnaya.runtime import build_orchestrator
from nirnaya.store import Store, connect

KIT = ("K1", "K2", "K3")
M = DEMO_POLICY.merchant_id
SANDBOX_DIR = Path(".nirnaya")


@dataclass
class DemoSession:
    store: Store
    cases: dict[str, EvalCase]
    # Kit label (K1...) -> the dispute id in this store (the same offline; Airwallex's in the
    # sandbox).
    kit_ids: dict[str, str]
    now: datetime
    aw: FakeAirwallex | None = None
    orch: Orchestrator | None = None
    source: str = "offline"
    rejected: bool = False
    log: list[str] = field(default_factory=list)

    @property
    def can_act(self) -> bool:
        return self.orch is not None

    @classmethod
    def offline(
        cls, env: Mapping[str, str] | None = None, llm: LLMClient | None = None
    ) -> "DemoSession":
        """A fresh in-memory session. The agent (scripted or LLM) follows the environment."""
        cases = {c.id: c for c in load_cases()}
        store = Store(connect())
        store.register_merchant(
            M, airwallex_account_id="acct-demo", policy=DEMO_POLICY, delegated_authority=True
        )
        aw = FakeAirwallex()
        orch = build_orchestrator(
            store,
            aw,
            aw,
            synthetic_world(list(cases.values())),
            Settings(dry_run=False),  # the fake is the only Airwallex here
            env=os.environ if env is None else env,
            llm=llm,
        )
        now = cases["K1"].dispute.opened_at + timedelta(hours=1)
        return cls(store, cases, {k: k for k in KIT}, now, aw=aw, orch=orch)

    @classmethod
    def from_sandbox_run(cls, directory: Path = SANDBOX_DIR) -> "DemoSession | None":
        """View the store the sandbox runner wrote. None when there is no completed staging."""
        state_path = directory / "sandbox_state.json"
        db_path = directory / "sandbox.db"
        if not state_path.is_file() or not db_path.is_file():
            return None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        kit_ids = {k: v["dispute"] for k, v in state.get("cases", {}).items() if "dispute" in v}
        if not kit_ids:
            return None
        cases = {c.id: c for c in load_cases()}
        store = Store(connect(db_path))
        now = max((c.updated_at for c in store.cases(M)), default=datetime.now().astimezone())
        return cls(store, cases, kit_ids, now, source="sandbox")

    # --- actions (offline only) ---------------------------------------------------------------

    def _require(self) -> tuple[FakeAirwallex, Orchestrator]:
        if self.aw is None or self.orch is None:
            raise RuntimeError("this session only views a sandbox run")
        return self.aw, self.orch

    def run_kit(self) -> None:
        aw, orch = self._require()
        for label in KIT:
            case = self.cases[label]
            if self.store.case(M, label) is None:
                aw.add_dispute(
                    case.dispute,
                    attempt=case.payment_attempt,
                    intent_owner="same" if case.ownership_resolved else None,
                )
        orch.run_until_idle(self.now)
        self.log.append("Ran kit cases 1, 2 and 3.")

    def reject_k1(self) -> None:
        """Kit beat 1b: the issuer rejects case 1's evidence; Airwallex escalates it."""
        aw, orch = self._require()
        aw.simulate_escalate("K1", due_at=self.now + timedelta(days=20))
        self.now = self.now + timedelta(days=5)
        orch.run_until_idle(self.now)
        self.rejected = True
        self.log.append("The issuer rejected K1's evidence; K1 escalated to chargeback.")

    def approve(self, case_id: str, action: Action, approver: str) -> None:
        _, orch = self._require()
        approve(self.store, M, case_id, action, approver=approver, now=self.now)
        orch.run_until_idle(self.now)
        self.log.append(f"{approver} approved {action} on {case_id}.")

    def close(self, case_id: str, approver: str, note: str) -> None:
        self._require()
        close_manually(self.store, M, case_id, approver=approver, note=note, now=self.now)
        self.log.append(f"{approver} handled {case_id} outside Nirnaya.")
