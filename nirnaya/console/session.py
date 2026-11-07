"""A demo session for the console: a store, the simulated Airwallex and the orchestrator.

The console shows the kit 4 cases (1, 1b, 2, 2-C, 3) end to end, then one case beyond the kit
whose deciding fact is only in a driver's note (architecture 8.8), and one where the agent
fixes a draft the verifier blocked (architecture 8.6). Offline, Airwallex is the
in-memory fake and merchant systems are SYNTHETIC; writes land only in the fake. A sandbox run
written by `python -m nirnaya.sandbox_demo` can be opened to view instead (no actions).
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from nirnaya.agent.llm_drafter import LLMDrafter
from nirnaya.agent.runs import DraftResult
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.agentic import AgenticCase, Cached, load_agentic_cases, load_llm_caches
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.evaluation.self_correction import Injected, Recording
from nirnaya.evaluation.self_correction import latest_cache as latest_redraft_cache
from nirnaya.integrations.mocks.airwallex_ai import simulated as simulated_ai
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.llm.base import LLMClient
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import Action, Stage
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.referrals import approve, close_manually, reject_findings
from nirnaya.orchestrator.settings import Settings
from nirnaya.runtime import build_orchestrator
from nirnaya.store import Store, connect

KIT = ("K1", "K2", "K3")
NOTE_CASE = "AG-F1"  # the agentic suite's signature-in-a-driver's-note case
REDRAFT_CASE = "N1"  # not received, worth challenging; its first draft is broken on purpose
# Demo B: fraud at pre-chargeback with nothing to fight with, settled early. Offline only:
# the sandbox auto-accepts pre-chargebacks within a second.
EARLY_CASE = "F5"


class _OneCaseBroken:
    """The session's drafter, except that one case's first draft is broken on purpose (the
    self-correction demo); every other case drafts as configured."""

    revises = True

    def __init__(self, drafter: object, case_id: str, injected: Injected) -> None:
        self.drafter, self.case_id, self.injected = drafter, case_id, injected

    def draft(
        self, dispute: Dispute, records: list[EvidenceRecord], feedback: dict | None = None
    ) -> DraftResult:
        if dispute.id == self.case_id:
            return self.injected.draft(dispute, records, feedback=feedback)
        if feedback is not None and getattr(self.drafter, "revises", False):
            return self.drafter.draft(dispute, records, feedback=feedback)  # type: ignore[attr-defined]
        return self.drafter.draft(dispute, records)  # type: ignore[attr-defined]


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
    # How the self-correction case was redrafted: live, or a replayed cached answer (labelled).
    redraft_source: str | None = None
    # How far the demo clock has moved. A dispute staged later keeps the time it had left.
    clock_moved: timedelta = timedelta(0)

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
    def from_sandbox_run(
        cls, directory: Path = SANDBOX_DIR, db_name: str = "sandbox.db"
    ) -> "DemoSession | None":
        """View the store the sandbox runner wrote (or a kept copy of it, `db_name`). None when
        there is no completed staging."""
        state_path = directory / "sandbox_state.json"
        db_path = directory / db_name
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

    def _stage(self, aw: FakeAirwallex, label: str) -> None:
        case = self.cases[label]
        if self.store.case(M, label) is None:
            rec = simulated_ai(label, case.dispute.stage)
            due_at = case.dispute.due_at + self.clock_moved
            aw.add_dispute(
                case.dispute.model_copy(update={"ai_recommendation": rec, "due_at": due_at}),
                attempt=case.payment_attempt,
                intent_owner="same" if case.ownership_resolved else None,
            )

    def run_kit(self) -> None:
        aw, orch = self._require()
        for label in KIT:
            self._stage(aw, label)
        orch.run_until_idle(self.now)
        self.log.append("Ran kit cases 1, 2 and 3.")

    def run_case(self, label: str) -> None:
        """One case on its own: a kit case staged at Airwallex and decided, or one of the
        cases beyond the kit (the early accept, the driver's note, the self-correction)."""
        if label == NOTE_CASE:
            return self.run_note_case()
        if label == REDRAFT_CASE:
            return self.run_redraft_case()
        if label not in (*KIT, EARLY_CASE):
            raise KeyError(label)
        aw, orch = self._require()
        self._stage(aw, label)
        orch.run_until_idle(self.now)
        self.log.append(
            f"Ran kit case {KIT.index(label) + 1}." if label in KIT else f"Ran {label}."
        )

    def decide(self, case_id: str, choice: str, approver: str) -> None:
        """A person's decision on a referred case: accept, fight, fight confirming the quotes
        the reader proposed (word for word, as shown), or handle it outside."""
        if choice == "confirm":
            case = self.store.case(M, case_id)
            if case is None:
                raise KeyError(case_id)
            proposed = tuple(
                f.finding_id
                for f in self.store.findings(M, case_id, case.cycle)
                if f.status == "PROPOSED"
            )
            if not proposed:
                raise ValueError("no proposed quote to confirm")
            self.approve(case_id, Action.CHALLENGE, approver, confirm=proposed)
        elif choice == "accept":
            self.approve(case_id, Action.ACCEPT, approver)
        elif choice == "fight":
            self.approve(case_id, Action.CHALLENGE, approver)
        elif choice == "manual":
            self.close(case_id, approver, "handled outside Nirnaya")
        else:
            raise ValueError(f"unknown choice {choice!r}")

    def reject_k1(self) -> None:
        """Kit beat 1b: the issuer rejects case 1's evidence; Airwallex escalates it."""
        aw, orch = self._require()
        aw.simulate_escalate("K1", due_at=self.now + timedelta(days=20))
        aw.set_ai_recommendation("K1", simulated_ai("K1", Stage.CHARGEBACK))
        self.now = self.now + timedelta(days=5)
        self.clock_moved += timedelta(days=5)
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

    def run_redraft_case(self, cache: dict | None = None) -> None:
        """Self-correction: N1's first draft adds "signed for by J. Smith", which the carrier
        record does not show (the kit 2-C fabrication), on purpose. The verifier blocks it; the
        agent gets the findings and redrafts. In llm mode the redraft is a live model call;
        otherwise Claude's redraft from the self-correction evaluation is replayed, labelled."""
        aw, orch = self._require()
        if getattr(orch.drafter, "revises", False) and not isinstance(orch.drafter, _OneCaseBroken):
            inner = orch.drafter
            llm = getattr(inner, "llm", None)
            who = "the configured drafter" if llm is None else f"{llm.provider}/{llm.model}"
            self.redraft_source = f"redrafted live by {who}"
        else:
            run = cache if cache is not None else latest_redraft_cache()
            answer = None if run is None else run.get("cases", {}).get(f"{REDRAFT_CASE}:overreach")
            if run is None or answer is None:
                raise RuntimeError(
                    "no cached redraft: run NIRNAYA_LIVE_LLM=1 uv run python -m "
                    "nirnaya.evaluation.self_correction --live, or start with NIRNAYA_AGENT=llm"
                )
            replay = Recording(None, run["provider"], run["model"], answer)
            inner = LLMDrafter(replay, env={})  # type: ignore[arg-type]
            self.redraft_source = (
                f"redrafted from a cached answer by {run['provider']}/{run['model']}, recorded "
                f"{run['run_at'][:10]} and replayed (no model call now)"
            )
        if not isinstance(orch.drafter, _OneCaseBroken):
            orch.drafter = _OneCaseBroken(orch.drafter, REDRAFT_CASE, Injected(inner, "overreach"))
        case = self.cases[REDRAFT_CASE]
        if self.store.case(M, case.id) is None:
            aw.add_dispute(case.dispute, attempt=case.payment_attempt, intent_owner="same")
        orch.run_until_idle(self.now)
        self.log.append(f"Ran {case.id} with a broken first draft; it was {self.redraft_source}.")

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
