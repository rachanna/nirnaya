"""What each policy does on each evaluation case (Milestone 6).

- nirnaya: the real pipeline (ingest, state machine, scripted investigator and drafter,
  verifier, renderer, write gateway) against the in-memory fake Airwallex. Nothing is copied
  from the expectations: the route and the submitted claims are read from what happened.
- fight_all: challenges every case it may act on, submitting the case's drafted claims as
  they are, unverified (including the adversarial drafts).
- accept_all: accepts every case it may act on.
A case Airwallex is already handling (deferral, a status or stage that cannot be acted on)
gets NONE from every policy.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from nirnaya.agent.runs import DraftResult
from nirnaya.agent.scripted import ScriptedDrafter, ScriptedInvestigator
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.evaluation.cases import EvalCase, synthetic_world
from nirnaya.evidence.catalogue import record_establishes
from nirnaya.evidence.derive import case_records
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.integrations.mocks.systems import SyntheticWorld
from nirnaya.models.claim import Claim
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import ACTIONABLE_STATUS, AUTOMATED_STAGES, ValidationStatus
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.orchestrator.deferral import deferral_reason
from nirnaya.orchestrator.machine import Orchestrator
from nirnaya.orchestrator.ports import Drafter, Investigator
from nirnaya.orchestrator.settings import Settings
from nirnaya.orchestrator.states import CaseState
from nirnaya.store import Store, connect
from nirnaya.store.repository import WriteRow
from nirnaya.verifier import CheckName, verify


class Act(StrEnum):
    ACCEPT = "ACCEPT"
    CHALLENGE = "CHALLENGE"
    REFER = "REFER"
    NONE = "NONE"  # Airwallex is handling it; nothing to do


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    action: Act
    # Claims sent with a challenge, and how many of them fail a blocking check.
    submitted: tuple[Claim, ...] = ()
    unsupported_submitted: int = 0
    # Of those, claims whose cited field does establish the fact and only the value was written
    # in the wrong form (e.g. "Order ORD-A3 exists." for ORD-A3). The rest overreach.
    unsupported_format: int = 0
    # Claims the policy itself stopped before anything was sent (Nirnaya's verifier).
    blocked_before_write: int = 0
    writes: tuple[WriteRow, ...] = field(default=())
    stage_of_cycle: dict[int, str] = field(default_factory=dict)


def actionable(dispute: Dispute) -> bool:
    """A policy may act: Airwallex expects a response and is not handling the case itself."""
    return (
        dispute.status is ACTIONABLE_STATUS
        and dispute.stage in AUTOMATED_STAGES
        and deferral_reason(dispute, None) is None
    )


def score_claims(case: EvalCase, claims: list[Claim]) -> int:
    """How many claims fail a blocking check against the case's records (scoring only)."""
    return score_claims_split(case, claims)[0]


# Failures that only concern how a value was written, not whether the record supports the fact.
FORMAT_ONLY = frozenset(
    {CheckName.EXTRACTED_VALUE_MISMATCH, CheckName.VALUE_MISMATCH, CheckName.NAME_OR_ID_MISMATCH}
)


def score_claims_split(case: EvalCase, claims: list[Claim]) -> tuple[int, int]:
    """(claims that fail a blocking check, of which format only). Format only: the cited field
    of the case's own record does establish the claimed fact, and every failure is about how
    the value was written. Wrong record, a field that does not establish the fact, more than
    the record shows, a wrong amount or date: substantive."""
    if not claims:
        return 0, 0
    records = {r.id: r for r in case_records(case.evidence_records(), case.dispute)}
    result = verify(case.dispute, claims, case.evidence_records())
    blocked = [c for c in result.claims if c.claim.validation_status is ValidationStatus.BLOCKED]
    fmt = 0
    for c in blocked:
        record = records.get(c.claim.source_record_id)
        checks = {f.check for f in c.failures}
        if (
            record is not None
            and c.claim.establishes in record_establishes(record, c.claim.source_field)
            and checks <= FORMAT_ONLY
        ):
            fmt += 1
    return len(blocked), fmt


def fight_all(case: EvalCase) -> CaseResult:
    if not actionable(case.dispute):
        return CaseResult(case.id, Act.NONE)
    claims = case.claim_objects()
    unsupported, fmt = score_claims_split(case, claims)
    return CaseResult(
        case.id,
        Act.CHALLENGE,
        tuple(claims),
        unsupported_submitted=unsupported,
        unsupported_format=fmt,
    )


def accept_all(case: EvalCase) -> CaseResult:
    return CaseResult(case.id, Act.ACCEPT if actionable(case.dispute) else Act.NONE)


class _FixtureDrafter:
    """For cases tagged `fabrication`: drafts exactly the fixture claims, as a careless model
    would, so the run shows the verifier stopping them."""

    def __init__(self, case: EvalCase) -> None:
        self._claims = tuple(case.claim_objects())

    def draft(self, dispute: Dispute, records: list[EvidenceRecord]) -> DraftResult:
        return DraftResult(claims=self._claims)


def nirnaya(
    case: EvalCase,
    world: SyntheticWorld | None = None,
    *,
    agents: tuple[Investigator, Drafter] | None = None,
    store: Store | None = None,
) -> CaseResult:
    """Run the case through the full pipeline and read the outcome from the store.

    By default the agent is the scripted one (with the careless fixture drafter on the
    fabrication cases). `agents` swaps in another investigator and drafter, and `store` lets
    the caller inspect the run afterwards (nirnaya.evaluation.llm_agent).
    """
    store = store if store is not None else Store(connect())
    store.register_merchant(
        DEMO_POLICY.merchant_id,
        airwallex_account_id="acct-eval",
        policy=DEMO_POLICY,
        delegated_authority=True,
    )
    aw = FakeAirwallex(auto_accept_pre_chargeback="sandbox_behaviour" in case.tags)
    aw.add_dispute(
        case.dispute,
        attempt=case.payment_attempt,
        intent_owner="same" if case.ownership_resolved else None,
    )
    if agents is None:
        drafter = _FixtureDrafter(case) if "fabrication" in case.tags else ScriptedDrafter()
        agents = (ScriptedInvestigator(), drafter)
    orch = Orchestrator(
        store,
        aw,
        aw,
        world or synthetic_world([case]),
        *agents,
        # Dry run, as the e2e tests: the evaluation measures decisions, and the fake refuses a
        # pre-chargeback challenge (architecture open decision 18) that the sandbox would never
        # reach anyway (pre-chargebacks auto-accept there).
        Settings(dry_run=True),
    )
    orch.run_until_idle(case.dispute.opened_at + timedelta(hours=1))

    m, cid = case.dispute.merchant_id, case.dispute.id
    writes = tuple(store.writes(m, cid))
    done = [w for w in writes if w.status in {"CONFIRMED", "DRY_RUN"}]
    if done:
        action = Act(done[-1].action)
    else:
        state = store.case(m, cid).state
        action = Act.REFER if state == CaseState.REFERRED else Act.NONE

    submitted: list[Claim] = []
    blocked = 0
    stages: dict[int, str] = {}
    case_row = store.case(m, cid)
    for cycle in range(1, case_row.cycle + 1):
        stages[cycle] = store.cycle(m, cid, cycle).stage
        draft = store.latest_draft(m, cid, cycle)
        verification = store.verification(m, cid, cycle, draft) if draft else None
        if verification is None:
            continue
        blocked += len(verification.blocked)
        if any(w.cycle == cycle and w.action == "CHALLENGE" for w in done):
            submitted += [c.claim for c in verification.claims]
    unsupported = sum(1 for c in submitted if c.validation_status is not ValidationStatus.VERIFIED)
    return CaseResult(
        cid,
        action,
        tuple(submitted),
        unsupported_submitted=unsupported,
        blocked_before_write=blocked,
        writes=writes,
        stage_of_cycle=stages,
    )
