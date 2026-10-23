"""Tenant-scoped repository. Every method takes merchant_id and filters on it.

Domain objects are stored as their Pydantic JSON and re-validated when read back.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from nirnaya.models.base import DomainModel, UtcDatetime
from nirnaya.models.claim import Claim
from nirnaya.models.decision import Decision
from nirnaya.models.dispute import Dispute
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.models.policy import MerchantPolicy
from nirnaya.store import audit
from nirnaya.verifier.result import VerificationResult


class Merchant(DomainModel):
    merchant_id: str
    airwallex_account_id: str
    delegated_authority: bool
    auto_accept_threshold: Decimal | None
    active_policy_version: str


class CaseRow(DomainModel):
    case_id: str
    merchant_id: str
    state: str
    cycle: int
    due_at: UtcDatetime
    urgent: bool
    updated_at: UtcDatetime


class CycleRow(DomainModel):
    case_id: str
    merchant_id: str
    cycle: int
    stage: str
    policy_version: str
    ownership_resolved: bool | None
    tool_calls: int
    started_at: UtcDatetime


class WriteRow(DomainModel):
    request_id: str
    merchant_id: str
    case_id: str
    cycle: int
    action: str
    request: dict[str, Any]
    status: str
    dry_run: bool
    response: dict[str, Any] | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ReferralRow(DomainModel):
    referral_id: str
    merchant_id: str
    case_id: str
    cycle: int
    reasons: list[str]
    urgent: bool
    created_at: UtcDatetime
    resolution: str | None
    approval: dict[str, Any] | None
    resolved_at: UtcDatetime | None


class StoreError(RuntimeError):
    pass


def _iso(at: datetime) -> str:
    return at.isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._depth = 0

    # --- transactions and audit ---------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        """One atomic unit. Nested use joins the outer transaction."""
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute("ROLLBACK")
            raise
        self._depth -= 1
        if self._depth == 0:
            self.conn.execute("COMMIT")

    def audit(
        self, merchant_id: str, case_id: str | None, event: str, body: dict[str, Any], at: datetime
    ) -> None:
        audit.append(
            self.conn, merchant_id=merchant_id, case_id=case_id, event=event, body=body, at=at
        )

    def audit_events(self, merchant_id: str, case_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT seq, event, body_json, at FROM audit_events"
            " WHERE merchant_id = ? AND case_id = ? ORDER BY seq",
            (merchant_id, case_id),
        )
        return [
            {
                "seq": r["seq"],
                "event": r["event"],
                "body": json.loads(r["body_json"]),
                "at": r["at"],
            }
            for r in rows
        ]

    # --- merchants and policies ---------------------------------------------------------

    def register_merchant(
        self,
        merchant_id: str,
        *,
        airwallex_account_id: str,
        policy: MerchantPolicy,
        delegated_authority: bool,
        auto_accept_threshold: Decimal | None = None,
    ) -> None:
        if policy.merchant_id != merchant_id:
            raise StoreError("policy belongs to another merchant")
        policy_json = policy.model_dump_json()
        with self.transaction():
            self.conn.execute(
                "INSERT OR REPLACE INTO merchants VALUES (?, ?, ?, ?, ?)",
                (
                    merchant_id,
                    airwallex_account_id,
                    int(delegated_authority),
                    None if auto_accept_threshold is None else str(auto_accept_threshold),
                    policy.version,
                ),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO policies VALUES (?, ?, ?, ?)",
                (merchant_id, policy.version, policy_json, _sha(policy_json)),
            )

    def merchant(self, merchant_id: str) -> Merchant | None:
        row = self.conn.execute(
            "SELECT * FROM merchants WHERE merchant_id = ?", (merchant_id,)
        ).fetchone()
        if row is None:
            return None
        return Merchant(
            merchant_id=row["merchant_id"],
            airwallex_account_id=row["airwallex_account_id"],
            delegated_authority=bool(row["delegated_authority"]),
            auto_accept_threshold=None
            if row["auto_accept_threshold"] is None
            else Decimal(row["auto_accept_threshold"]),
            active_policy_version=row["active_policy_version"],
        )

    def merchant_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT merchant_id FROM merchants ORDER BY 1")]

    def policy(self, merchant_id: str, version: str) -> MerchantPolicy:
        row = self.conn.execute(
            "SELECT policy_json FROM policies WHERE merchant_id = ? AND version = ?",
            (merchant_id, version),
        ).fetchone()
        if row is None:
            raise StoreError(f"no policy {version!r} for {merchant_id!r}")
        return MerchantPolicy.model_validate_json(row["policy_json"])

    # --- cases and cycles -----------------------------------------------------------------

    def _case(self, row: sqlite3.Row | None) -> CaseRow | None:
        if row is None:
            return None
        return CaseRow(
            case_id=row["case_id"],
            merchant_id=row["merchant_id"],
            state=row["state"],
            cycle=row["cycle"],
            due_at=row["due_at"],
            urgent=bool(row["urgent"]),
            updated_at=row["updated_at"],
        )

    def case(self, merchant_id: str, case_id: str) -> CaseRow | None:
        return self._case(
            self.conn.execute(
                "SELECT * FROM cases WHERE merchant_id = ? AND case_id = ?", (merchant_id, case_id)
            ).fetchone()
        )

    def case_owner(self, case_id: str) -> str | None:
        """Which merchant owns a dispute id, so ingestion can refuse a cross-tenant id."""
        row = self.conn.execute(
            "SELECT merchant_id FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return None if row is None else row["merchant_id"]

    def cases(self, merchant_id: str, states: set[str] | None = None) -> list[CaseRow]:
        rows = self.conn.execute(
            "SELECT * FROM cases WHERE merchant_id = ? ORDER BY due_at, case_id", (merchant_id,)
        )
        found = [self._case(r) for r in rows]
        return [c for c in found if c is not None and (states is None or c.state in states)]

    def create_case(
        self, merchant_id: str, case_id: str, state: str, due_at: datetime, at: datetime
    ) -> None:
        self.conn.execute(
            "INSERT INTO cases (case_id, merchant_id, state, cycle, due_at, urgent, updated_at)"
            " VALUES (?, ?, ?, 1, ?, 0, ?)",
            (case_id, merchant_id, state, _iso(due_at), _iso(at)),
        )

    def update_case(self, merchant_id: str, case_id: str, *, at: datetime, **fields: Any) -> None:
        allowed = {"state", "cycle", "due_at", "urgent"}
        if not fields or set(fields) - allowed:
            raise StoreError(f"bad case update {sorted(fields)}")
        values = {
            k: (_iso(v) if isinstance(v, datetime) else int(v) if isinstance(v, bool) else v)
            for k, v in fields.items()
        }
        sets = ", ".join(f"{k} = ?" for k in values)
        cur = self.conn.execute(
            f"UPDATE cases SET {sets}, updated_at = ? WHERE merchant_id = ? AND case_id = ?",
            (*values.values(), _iso(at), merchant_id, case_id),
        )
        if cur.rowcount != 1:
            raise StoreError(f"no case {case_id!r} for {merchant_id!r}")

    def start_cycle(
        self,
        merchant_id: str,
        case_id: str,
        cycle: int,
        *,
        stage: str,
        policy_version: str,
        at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO cycles (merchant_id, case_id, cycle, stage, policy_version, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (merchant_id, case_id, cycle, stage, policy_version, _iso(at)),
        )

    def cycle(self, merchant_id: str, case_id: str, cycle: int) -> CycleRow:
        row = self.conn.execute(
            "SELECT * FROM cycles WHERE merchant_id = ? AND case_id = ? AND cycle = ?",
            (merchant_id, case_id, cycle),
        ).fetchone()
        if row is None:
            raise StoreError(f"no cycle {cycle} for case {case_id!r}")
        return CycleRow(
            case_id=row["case_id"],
            merchant_id=row["merchant_id"],
            cycle=row["cycle"],
            stage=row["stage"],
            policy_version=row["policy_version"],
            ownership_resolved=None
            if row["ownership_resolved"] is None
            else bool(row["ownership_resolved"]),
            tool_calls=row["tool_calls"],
            started_at=row["started_at"],
        )

    def set_ownership(self, merchant_id: str, case_id: str, cycle: int, resolved: bool) -> None:
        self.conn.execute(
            "UPDATE cycles SET ownership_resolved = ? WHERE merchant_id = ? AND case_id = ?"
            " AND cycle = ?",
            (int(resolved), merchant_id, case_id, cycle),
        )

    def tools_called(self, merchant_id: str, case_id: str, cycle: int) -> set[str]:
        row = self.conn.execute(
            "SELECT tools_called FROM cycles WHERE merchant_id = ? AND case_id = ? AND cycle = ?",
            (merchant_id, case_id, cycle),
        ).fetchone()
        return set() if row is None else set(json.loads(row["tools_called"]))

    def record_tool_called(self, merchant_id: str, case_id: str, cycle: int, call: str) -> None:
        called = sorted(self.tools_called(merchant_id, case_id, cycle) | {call})
        self.conn.execute(
            "UPDATE cycles SET tools_called = ?, tool_calls = tool_calls + 1"
            " WHERE merchant_id = ? AND case_id = ? AND cycle = ?",
            (json.dumps(called), merchant_id, case_id, cycle),
        )

    def add_tool_calls(self, merchant_id: str, case_id: str, cycle: int, n: int) -> None:
        self.conn.execute(
            "UPDATE cycles SET tool_calls = tool_calls + ? WHERE merchant_id = ? AND case_id = ?"
            " AND cycle = ?",
            (n, merchant_id, case_id, cycle),
        )

    # --- dispute snapshots ----------------------------------------------------------------

    def add_snapshot(self, merchant_id: str, dispute: Dispute, fetched_at: datetime) -> str:
        if dispute.merchant_id != merchant_id:
            raise StoreError("dispute belongs to another merchant")
        body = dispute.model_dump_json()
        digest = _sha(body)
        self.conn.execute(
            "INSERT INTO dispute_snapshots (merchant_id, case_id, dispute_json, sha256, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (merchant_id, dispute.id, body, digest, _iso(fetched_at)),
        )
        return digest

    def latest_dispute(self, merchant_id: str, case_id: str) -> Dispute | None:
        row = self.conn.execute(
            "SELECT dispute_json FROM dispute_snapshots WHERE merchant_id = ? AND case_id = ?"
            " ORDER BY snapshot_id DESC LIMIT 1",
            (merchant_id, case_id),
        ).fetchone()
        return None if row is None else Dispute.model_validate_json(row["dispute_json"])

    # --- evidence -------------------------------------------------------------------------

    def put_evidence(self, merchant_id: str, record: EvidenceRecord) -> bool:
        """Store a record once. Re-storing the same content is a no-op; different content under
        the same id is refused (records are immutable). Returns True if newly stored."""
        if record.merchant_id != merchant_id:
            raise StoreError("evidence belongs to another merchant")
        existing = self.conn.execute(
            "SELECT sha256, record_json FROM evidence_records WHERE case_id = ? AND record_id = ?",
            (record.case_id, record.id),
        ).fetchone()
        if existing is not None:
            if existing["sha256"] == record.sha256:
                return False
            # Re-fetched later with identical content: keep the original capture.
            kept = EvidenceRecord.model_validate_json(existing["record_json"])
            content = {"captured_at", "sha256"}
            if kept.model_dump(exclude=content) == record.model_dump(exclude=content):
                return False
            raise StoreError(f"record {record.id!r} changed after capture")
        self.conn.execute(
            "INSERT INTO evidence_records VALUES (?, ?, ?, ?, ?, ?)",
            (
                merchant_id,
                record.case_id,
                record.id,
                record.model_dump_json(),
                record.sha256,
                _iso(record.captured_at),
            ),
        )
        return True

    def evidence(self, merchant_id: str, case_id: str) -> list[EvidenceRecord]:
        rows = self.conn.execute(
            "SELECT record_json FROM evidence_records WHERE merchant_id = ? AND case_id = ?"
            " ORDER BY record_id",
            (merchant_id, case_id),
        )
        return [EvidenceRecord.model_validate_json(r["record_json"]) for r in rows]

    def capture_hashes(self, merchant_id: str, case_id: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT record_id, sha256 FROM evidence_records WHERE merchant_id = ? AND case_id = ?",
            (merchant_id, case_id),
        )
        return {r["record_id"]: r["sha256"] for r in rows}

    # --- decisions, claims, verifications, renders ---------------------------------------

    def add_decision(
        self, merchant_id: str, case_id: str, cycle: int, decision: Decision, at: datetime
    ) -> str:
        seq = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM decisions WHERE case_id = ? AND cycle = ?",
            (case_id, cycle),
        ).fetchone()[0]
        decision_id = f"{case_id}:{cycle}:{seq}"
        self.conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                merchant_id,
                case_id,
                cycle,
                seq,
                decision.action.value,
                decision.model_dump_json(),
                _iso(at),
            ),
        )
        return decision_id

    def latest_decision(
        self, merchant_id: str, case_id: str, cycle: int
    ) -> tuple[str, Decision] | None:
        row = self.conn.execute(
            "SELECT decision_id, decision_json FROM decisions WHERE merchant_id = ? AND case_id = ?"
            " AND cycle = ? ORDER BY seq DESC LIMIT 1",
            (merchant_id, case_id, cycle),
        ).fetchone()
        if row is None:
            return None
        return row["decision_id"], Decision.model_validate_json(row["decision_json"])

    def latest_draft(self, merchant_id: str, case_id: str, cycle: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(draft), 0) FROM claims WHERE merchant_id = ? AND case_id = ?"
            " AND cycle = ?",
            (merchant_id, case_id, cycle),
        ).fetchone()
        return row[0]

    def put_claims(self, merchant_id: str, case_id: str, cycle: int, claims: list[Claim]) -> int:
        draft = self.latest_draft(merchant_id, case_id, cycle) + 1
        for claim in claims:
            if claim.merchant_id != merchant_id or claim.case_id != case_id:
                raise StoreError("claim belongs to another case or merchant")
            self.conn.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?)",
                (merchant_id, case_id, cycle, draft, claim.id, claim.model_dump_json()),
            )
        return draft

    def claims(self, merchant_id: str, case_id: str, cycle: int, draft: int) -> list[Claim]:
        rows = self.conn.execute(
            "SELECT claim_json FROM claims WHERE merchant_id = ? AND case_id = ? AND cycle = ?"
            " AND draft = ? ORDER BY rowid",
            (merchant_id, case_id, cycle, draft),
        )
        return [Claim.model_validate_json(r["claim_json"]) for r in rows]

    def put_verification(
        self, merchant_id: str, case_id: str, cycle: int, draft: int, result: VerificationResult
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verifications VALUES (?, ?, ?, ?, ?, ?)",
            (merchant_id, case_id, cycle, draft, result.outcome.value, result.model_dump_json()),
        )

    def verification(
        self, merchant_id: str, case_id: str, cycle: int, draft: int
    ) -> VerificationResult | None:
        row = self.conn.execute(
            "SELECT result_json FROM verifications WHERE merchant_id = ? AND case_id = ?"
            " AND cycle = ? AND draft = ?",
            (merchant_id, case_id, cycle, draft),
        ).fetchone()
        return None if row is None else VerificationResult.model_validate_json(row["result_json"])

    def put_render(
        self, merchant_id: str, case_id: str, cycle: int, draft: int, filename: str, content: bytes
    ) -> str:
        digest = hashlib.sha256(content).hexdigest()
        self.conn.execute(
            "INSERT OR REPLACE INTO renders VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (merchant_id, case_id, cycle, draft, filename, content, digest),
        )
        return digest

    def render(
        self, merchant_id: str, case_id: str, cycle: int, draft: int
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT filename, content, sha256, file_id FROM renders WHERE merchant_id = ?"
            " AND case_id = ? AND cycle = ? AND draft = ?",
            (merchant_id, case_id, cycle, draft),
        ).fetchone()
        return None if row is None else dict(row)

    def set_render_file_id(
        self, merchant_id: str, case_id: str, cycle: int, draft: int, file_id: str
    ) -> None:
        self.conn.execute(
            "UPDATE renders SET file_id = ? WHERE merchant_id = ? AND case_id = ? AND cycle = ?"
            " AND draft = ?",
            (file_id, merchant_id, case_id, cycle, draft),
        )

    # --- referrals ------------------------------------------------------------------------

    def open_referral(
        self,
        merchant_id: str,
        case_id: str,
        cycle: int,
        reasons: list[str],
        urgent: bool,
        at: datetime,
    ) -> str:
        n = self.conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE case_id = ?", (case_id,)
        ).fetchone()[0]
        referral_id = f"{case_id}:ref:{n + 1}"
        self.conn.execute(
            "INSERT INTO referrals (referral_id, merchant_id, case_id, cycle, reasons_json, urgent,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (referral_id, merchant_id, case_id, cycle, json.dumps(reasons), int(urgent), _iso(at)),
        )
        return referral_id

    def _referral(self, row: sqlite3.Row) -> ReferralRow:
        return ReferralRow(
            referral_id=row["referral_id"],
            merchant_id=row["merchant_id"],
            case_id=row["case_id"],
            cycle=row["cycle"],
            reasons=json.loads(row["reasons_json"]),
            urgent=bool(row["urgent"]),
            created_at=row["created_at"],
            resolution=row["resolution"],
            approval=None if row["approval_json"] is None else json.loads(row["approval_json"]),
            resolved_at=row["resolved_at"],
        )

    def open_referral_for(self, merchant_id: str, case_id: str) -> ReferralRow | None:
        row = self.conn.execute(
            "SELECT * FROM referrals WHERE merchant_id = ? AND case_id = ? AND resolution IS NULL"
            " ORDER BY created_at DESC, referral_id DESC LIMIT 1",
            (merchant_id, case_id),
        ).fetchone()
        return None if row is None else self._referral(row)

    def latest_approval(self, merchant_id: str, case_id: str, cycle: int) -> ReferralRow | None:
        row = self.conn.execute(
            "SELECT * FROM referrals WHERE merchant_id = ? AND case_id = ? AND cycle = ?"
            " AND approval_json IS NOT NULL ORDER BY resolved_at DESC, referral_id DESC LIMIT 1",
            (merchant_id, case_id, cycle),
        ).fetchone()
        return None if row is None else self._referral(row)

    def resolve_referral(
        self,
        merchant_id: str,
        referral_id: str,
        *,
        resolution: str,
        approval: dict[str, Any] | None,
        at: datetime,
    ) -> None:
        cur = self.conn.execute(
            "UPDATE referrals SET resolution = ?, approval_json = ?, resolved_at = ?"
            " WHERE merchant_id = ? AND referral_id = ? AND resolution IS NULL",
            (
                resolution,
                None if approval is None else json.dumps(approval, sort_keys=True),
                _iso(at),
                merchant_id,
                referral_id,
            ),
        )
        if cur.rowcount != 1:
            raise StoreError(f"no open referral {referral_id!r}")

    # --- write ledger ---------------------------------------------------------------------

    def _write(self, row: sqlite3.Row) -> WriteRow:
        return WriteRow(
            request_id=row["request_id"],
            merchant_id=row["merchant_id"],
            case_id=row["case_id"],
            cycle=row["cycle"],
            action=row["action"],
            request=json.loads(row["request_json"]),
            status=row["status"],
            dry_run=bool(row["dry_run"]),
            response=None if row["response_json"] is None else json.loads(row["response_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def write(self, merchant_id: str, request_id: str) -> WriteRow | None:
        row = self.conn.execute(
            "SELECT * FROM writes WHERE merchant_id = ? AND request_id = ?",
            (merchant_id, request_id),
        ).fetchone()
        return None if row is None else self._write(row)

    def writes(self, merchant_id: str, case_id: str) -> list[WriteRow]:
        rows = self.conn.execute(
            "SELECT * FROM writes WHERE merchant_id = ? AND case_id = ? ORDER BY created_at, rowid",
            (merchant_id, case_id),
        )
        return [self._write(r) for r in rows]

    def insert_write(
        self,
        merchant_id: str,
        case_id: str,
        cycle: int,
        *,
        request_id: str,
        action: str,
        request: dict[str, Any],
        status: str,
        dry_run: bool,
        at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO writes VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                request_id,
                merchant_id,
                case_id,
                cycle,
                action,
                json.dumps(request, sort_keys=True, default=str),
                status,
                int(dry_run),
                _iso(at),
                _iso(at),
            ),
        )

    def update_write(
        self,
        merchant_id: str,
        request_id: str,
        *,
        status: str,
        response: dict[str, Any] | None,
        at: datetime,
    ) -> None:
        body = None if response is None else json.dumps(response, sort_keys=True, default=str)
        cur = self.conn.execute(
            "UPDATE writes SET status = ?, response_json = ?, updated_at = ?"
            " WHERE merchant_id = ? AND request_id = ?",
            (status, body, _iso(at), merchant_id, request_id),
        )
        if cur.rowcount != 1:
            raise StoreError(f"no write {request_id!r}")
