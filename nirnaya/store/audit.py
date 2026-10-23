"""Append-only, hash-chained audit log: hash = sha256(prev_hash || canonical event)."""

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

GENESIS = "0" * 64


def _canonical(
    merchant_id: str, case_id: str | None, event: str, body_json: str, at: str, prev_hash: str
) -> bytes:
    payload = {
        "merchant_id": merchant_id,
        "case_id": case_id,
        "event": event,
        "body": json.loads(body_json),
        "at": at,
        "prev_hash": prev_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def append(
    conn: sqlite3.Connection,
    *,
    merchant_id: str,
    case_id: str | None,
    event: str,
    body: dict[str, Any],
    at: datetime,
) -> str:
    """Append one event; returns its hash. Call inside the caller's transaction."""
    last = conn.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = last["hash"] if last else GENESIS
    body_json = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    at_s = at.isoformat()
    digest = hashlib.sha256(
        prev_hash.encode() + _canonical(merchant_id, case_id, event, body_json, at_s, prev_hash)
    ).hexdigest()
    conn.execute(
        "INSERT INTO audit_events (merchant_id, case_id, event, body_json, at, prev_hash, hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (merchant_id, case_id, event, body_json, at_s, prev_hash, digest),
    )
    return digest


def verify_chain(conn: sqlite3.Connection) -> int | None:
    """None if the whole chain is intact, otherwise the seq of the first broken event."""
    prev_hash = GENESIS
    for row in conn.execute("SELECT * FROM audit_events ORDER BY seq"):
        expected = hashlib.sha256(
            prev_hash.encode()
            + _canonical(
                row["merchant_id"],
                row["case_id"],
                row["event"],
                row["body_json"],
                row["at"],
                prev_hash,
            )
        ).hexdigest()
        if row["prev_hash"] != prev_hash or row["hash"] != expected:
            return row["seq"]
        prev_hash = row["hash"]
    return None
