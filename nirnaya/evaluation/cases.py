"""Evaluation cases: SYNTHETIC disputes, merchant-system data and expert-policy expectations.

Expected results are derived by hand from eval/EXPERT_POLICY.md, never by running the
engine, so the evaluation can catch an engine mistake.
"""

from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import model_validator

from nirnaya.evidence.catalogue import render_claim_text
from nirnaya.integrations.mocks import KEY_FIELD, SourceRecord, SyntheticWorld
from nirnaya.models.base import Dec, DomainModel, NonBlankStr
from nirnaya.models.claim import Claim, NormalisedValue
from nirnaya.models.decision import ReferralReason
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import (
    AcceptReason,
    Action,
    EvidenceType,
    RecordAuthor,
    SourceSystem,
    Stage,
)
from nirnaya.models.evidence import EvidenceRecord, FieldValue
from nirnaya.models.payment import PaymentAttempt
from nirnaya.verifier.result import CheckName, VerificationOutcome

CASES_DIR = Path(__file__).resolve().parents[2] / "eval" / "cases"


class Route(StrEnum):
    """Where the case ends up in the full pipeline."""

    ACCEPT = "ACCEPT"
    CHALLENGE = "CHALLENGE"
    REFER = "REFER"
    # Airwallex acts on its own (auto-defend, auto-accept, pre-arbitration review); record only.
    DEFER = "DEFER"


class WorldRecord(DomainModel):
    id: NonBlankStr
    system: SourceSystem
    author: RecordAuthor
    # None means the case's own merchant. Set only for tenant-isolation cases.
    merchant_id: NonBlankStr | None = None
    fields: dict[str, FieldValue]


class ClaimDraft(DomainModel):
    """A claim as a drafter would propose it; text is rendered by code."""

    id: NonBlankStr
    source_record_id: NonBlankStr
    source_field: NonBlankStr
    establishes: EvidenceType
    extracted_value: str
    normalised_value: NormalisedValue


class ExpectedEngine(DomainModel):
    action: Action
    math_action: Action | None
    accept_reason: AcceptReason | None = None
    referral_reasons: tuple[ReferralReason, ...] = ()
    p: Dec | None = None
    margin: Dec | None = None


class ExpectedVerification(DomainModel):
    outcome: VerificationOutcome
    # claim id -> the exact set of failed checks; claims not listed must have none.
    failed_checks: dict[str, tuple[CheckName, ...]] = {}
    case_failures: tuple[CheckName, ...] = ()
    counterevidence_ids: tuple[str, ...] | None = None


class Expected(DomainModel):
    route: Route
    # None when the engine is not run (Airwallex acts before Nirnaya would decide).
    engine: ExpectedEngine | None
    verification: ExpectedVerification | None = None
    rationale: NonBlankStr


class SandboxStaging(DomainModel):
    """How to stage this case with the Airwallex simulation API (Milestone 5)."""

    create_stage: Stage
    reason_code: str | None = None
    notes: str = ""


class EvalCase(DomainModel):
    id: NonBlankStr
    title: NonBlankStr
    tags: tuple[str, ...]
    dispute: Dispute
    payment_attempt: PaymentAttempt | None = None
    ownership_resolved: bool = True
    world: tuple[WorldRecord, ...] = ()
    claims: tuple[ClaimDraft, ...] = ()
    sandbox: SandboxStaging | None = None
    expected: Expected

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [r.id for r in self.world]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate world record id")
        for r in self.world:
            if r.system is SourceSystem.PAYMENT_ATTEMPT:
                raise ValueError("payment attempts go in payment_attempt, not world")
            if not isinstance(r.fields.get(KEY_FIELD[r.system]), str):
                raise ValueError(f"world record {r.id!r} lacks {KEY_FIELD[r.system]}")
        citable = set(ids) | ({self.payment_attempt_record_id} if self.payment_attempt else set())
        for c in self.claims:
            if c.source_record_id not in citable:
                raise ValueError(f"claim {c.id!r} cites unknown record {c.source_record_id!r}")
        if self.expected.verification is not None and not self.claims:
            raise ValueError("an expected verification needs claims")
        if self.expected.verification is not None:
            unknown = set(self.expected.verification.failed_checks) - {c.id for c in self.claims}
            if unknown:
                raise ValueError(f"failed_checks name unknown claims {sorted(unknown)}")
        return self

    @property
    def payment_attempt_record_id(self) -> str:
        assert self.payment_attempt is not None
        return f"payment_attempt:{self.payment_attempt.id}"

    def source_records(self) -> list[SourceRecord]:
        return [
            SourceRecord(
                id=r.id,
                merchant_id=r.merchant_id or self.dispute.merchant_id,
                system=r.system,
                author=r.author,
                fields=r.fields,
            )
            for r in self.world
        ]

    def evidence_records(self) -> list[EvidenceRecord]:
        """Every record of this case as captured evidence ("fully investigated")."""
        captured_at = self.dispute.opened_at + timedelta(hours=1)
        records = [
            s.snapshot(case_id=self.dispute.id, captured_at=captured_at)
            for s in self.source_records()
        ]
        if self.payment_attempt is not None:
            records.append(
                self.payment_attempt.to_evidence_record(
                    case_id=self.dispute.id, captured_at=captured_at, synthetic=True
                )
            )
        return records

    def claim_objects(self) -> list[Claim]:
        return [
            Claim(
                id=d.id,
                case_id=self.dispute.id,
                merchant_id=self.dispute.merchant_id,
                text=render_claim_text(d.establishes, d.normalised_value),
                source_record_id=d.source_record_id,
                source_field=d.source_field,
                establishes=d.establishes,
                extracted_value=d.extracted_value,
                normalised_value=d.normalised_value,
            )
            for d in self.claims
        ]


def load_cases(directory: Path = CASES_DIR) -> list[EvalCase]:
    """All cases, sorted by id. Each file is named after its case id."""
    cases = []
    for path in sorted(directory.glob("*.json")):
        case = EvalCase.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != case.id:
            raise ValueError(f"{path.name} holds case {case.id!r}")
        cases.append(case)
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases


def synthetic_world(cases: list[EvalCase]) -> SyntheticWorld:
    """One SYNTHETIC world holding every case's merchant-system records."""
    return SyntheticWorld(r for case in cases for r in case.source_records())
