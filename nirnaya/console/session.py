"""A demo session for the console: a store, the simulated Airwallex and the orchestrator.

The console shows the kit 4 cases (1, 1b, 2, 2-C, 3) end to end, then one case beyond the kit
whose deciding fact is only in a driver's note (architecture 8.8). Offline, Airwallex is the
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
from nirnaya.evaluation.agentic import AgenticCase, Cached, load_agentic_cases, load_llm_caches
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.integrations.mocks.airwallex_ai import simulated as simulated_ai
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.llm.base import LLMClient
from nirnaya.models.enums import Action, Stage
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, close_manually, reject_findings
from nirnaya.orchestrator.settings import Settings
from nirnaya.runtime import build_orchestrator
from nirnaya.store import Store, connect

KIT = ("K1", "K2", "K3")
NOTE_CASE = "AG-F1"  # the agentic suite's signature-in-a-driver's-note case
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
    note_case: AgenticCase | None = None
    # How the note was read: a live model call or a replayed cached answer (labelled).
    note_source: str | None = None

    @property
    def can_act(self) -> bool:
        return self.orch is not None

    @classmethod
    def offline(
        cls, env: Mapping[str, str] | None = None, llm: LLMClient | None = None
    ) -> "DemoSession":
        """A fresh in-memory session. The agent (scripted or LLM) follows the environment."""
        cases = {c.id: c for c in load_cases()}
        note = next(c for c in load_agentic_cases() if c.id == NOTE_CASE)
        store = Store(connect(check_same_thread=False))  # Streamlit reruns on worker threads
        store.register_merchant(
            M, airwallex_account_id="acct-demo", policy=DEMO_POLICY, delegated_authority=True
        )
        aw = FakeAirwallex()
        orch = build_orchestrator(
            store,
            aw,
            aw,
            synthetic_world([*cases.values(), note.case]),
            Settings(dry_run=False),  # the fake is the only Airwallex here
            env=os.environ if env is None else env,
            llm=llm,
        )
        now = cases["K1"].dispute.opened_at + timedelta(hours=1)
        return cls(store, cases, {k: k for k in KIT}, now, aw=aw, orch=orch, note_case=note)

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
        store = Store(connect(db_path, check_same_thread=False))
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
                rec = simulated_ai(label, case.dispute.stage)
                aw.add_dispute(
                    case.dispute.model_copy(update={"ai_recommendation": rec}),
                    attempt=case.payment_attempt,
                    intent_owner="same" if case.ownership_resolved else None,
                )
        orch.run_until_idle(self.now)
        self.log.append("Ran kit cases 1, 2 and 3.")

    def reject_k1(self) -> None:
        """Kit beat 1b: the issuer rejects case 1's evidence; Airwallex escalates it."""
        aw, orch = self._require()
        aw.simulate_escalate("K1", due_at=self.now + timedelta(days=20))
        aw.set_ai_recommendation("K1", simulated_ai("K1", Stage.CHARGEBACK))
        self.now = self.now + timedelta(days=5)
        orch.run_until_idle(self.now)
        self.rejected = True
        self.log.append("The issuer rejected K1's evidence; K1 escalated to chargeback.")

    def run_note_case(self, caches: dict | None = None) -> None:
        """Beyond the kit: a not-received chargeback whose signature is only in the driver's
        note. In llm mode the model reads it live; otherwise a cached Claude answer from the
        agentic suite is replayed (no model call), and labelled as such."""
        aw, orch = self._require()
        if self.note_case is None:
            raise RuntimeError("no note case in this session")
        if orch.reader is not None:
            llm = getattr(orch.reader, "llm", None)
            who = "the configured reader" if llm is None else f"{llm.provider}/{llm.model}"
            self.note_source = f"read live by {who}"
        else:
            runs = caches if caches is not None else load_llm_caches()
            answered = [r for r in runs.values() if NOTE_CASE in r.get("cases", {})]
            if not answered:
                raise RuntimeError(
                    "no cached reading: run NIRNAYA_LIVE_LLM=1 uv run python -m "
                    "nirnaya.evaluation.agentic --live, or start the console with NIRNAYA_AGENT=llm"
                )
            run = answered[-1]
            orch.reader = Cached(run["cases"], run["provider"], run["model"])
            self.note_source = (
                f"read from a cached answer by {run['provider']}/{run['model']}, recorded "
                f"{run['run_at'][:10]} and replayed (no model call now)"
            )
        case = self.note_case.case
        if self.store.case(M, case.id) is None:
            aw.add_dispute(case.dispute, attempt=case.payment_attempt, intent_owner="same")
        orch.run_until_idle(self.now)
        self.log.append(f"Ran {case.id}: the driver's note was {self.note_source}.")

    def approve(
        self, case_id: str, action: Action, approver: str, confirm: tuple[str, ...] = ()
    ) -> None:
        """Approve; `confirm` names the proposed quotes the approver confirms (a challenge)."""
        _, orch = self._require()
        approve(
            self.store,
            M,
            case_id,
            action,
            approver=approver,
            now=self.now,
            confirm_findings=confirm,
        )
        orch.run_until_idle(self.now)
        quoted = f", confirming {len(confirm)} quote(s)" if confirm else ""
        self.log.append(f"{approver} approved {action} on {case_id}{quoted}.")

    def reject_quotes(self, case_id: str, finding_ids: tuple[str, ...], approver: str) -> None:
        self._require()
        reject_findings(self.store, M, case_id, finding_ids, approver=approver, now=self.now)
        self.log.append(f"{approver} rejected {len(finding_ids)} quote(s) on {case_id}.")

    def close(self, case_id: str, approver: str, note: str) -> None:
        self._require()
        close_manually(self.store, M, case_id, approver=approver, note=note, now=self.now)
        self.log.append(f"{approver} handled {case_id} outside Nirnaya.")
