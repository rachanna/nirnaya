"""Builds a store, a fake Airwallex and a synthetic world around evaluation cases."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from nirnaya.agent.scripted import ScriptedDrafter
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evaluation.cases import EvalCase, load_cases, synthetic_world
from nirnaya.evidence.derive import (
    contradictory_record_ids,
    evidence_present,
    unanswered_customer_contacts,
)
from nirnaya.ingest import ingest
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.models import Claim
from nirnaya.orchestrator.settings import Settings
from nirnaya.render.evidence_doc import render_evidence
from nirnaya.store import Store, connect
from nirnaya.verifier import VerificationOutcome, verify

CASES = {c.id: c for c in load_cases()}


@dataclass
class Harness:
    dry_run: bool = True
    delegated: bool = True
    auto_accept_pre_chargeback: bool = False
    store: Store = field(init=False)
    aw: FakeAirwallex = field(init=False)
    settings: Settings = field(init=False)

    def __post_init__(self) -> None:
        self.store = Store(connect())
        self.aw = FakeAirwallex(auto_accept_pre_chargeback=self.auto_accept_pre_chargeback)
        self.settings = Settings(dry_run=self.dry_run)
        self.world = synthetic_world(list(CASES.values()))
        self.store.register_merchant(
            DEMO_POLICY.merchant_id,
            airwallex_account_id="acct-demo",
            policy=DEMO_POLICY,
            delegated_authority=self.delegated,
        )

    @staticmethod
    def now(case: EvalCase) -> datetime:
        return case.dispute.opened_at + timedelta(hours=1)

    def stage(self, case: EvalCase) -> None:
        """Put the dispute (and its payment attempt) in the fake Airwallex."""
        self.aw.add_dispute(
            case.dispute,
            attempt=case.payment_attempt,
            intent_owner="same" if case.ownership_resolved else None,
        )

    def prepare(
        self, case: EvalCase, *, claims: list[Claim] | None = None, render: bool = True
    ) -> None:
        """Bring a case to the point of writing, without the state machine: ingested, all
        evidence captured, decided, and (for a challenge) drafted, verified and rendered."""
        m, cid, now = case.dispute.merchant_id, case.dispute.id, self.now(case)
        self.stage(case)
        ingest(self.store, self.aw, m, now)
        records = case.evidence_records()
        with self.store.transaction():
            self.store.set_ownership(m, cid, 1, case.ownership_resolved)
            for r in records:
                self.store.put_evidence(m, r)
            decision = decide(
                case.dispute,
                DEMO_POLICY,
                evidence_present(records, case.dispute),
                ownership_resolved=case.ownership_resolved,
                contradictory_record_ids=contradictory_record_ids(records, case.dispute),
                unanswered_contact_ids=unanswered_customer_contacts(records, case.dispute),
            )
            self.store.add_decision(m, cid, 1, decision, now)
        if claims is None and decision.action.value != "CHALLENGE":
            return
        drafted = claims if claims is not None else ScriptedDrafter().draft(case.dispute, records)
        with self.store.transaction():
            draft = self.store.put_claims(m, cid, 1, drafted)
            result = verify(
                case.dispute, drafted, records, expected_hashes=self.store.capture_hashes(m, cid)
            )
            self.store.put_verification(m, cid, 1, draft, result)
            if render and result.outcome is VerificationOutcome.PASSED:
                name, content = render_evidence(case.dispute, result, records, cycle=1, draft=draft)
                self.store.put_render(m, cid, 1, draft, name, content)
