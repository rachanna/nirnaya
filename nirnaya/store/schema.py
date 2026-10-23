"""SQLite schema. Every table carries merchant_id; every query filters on it."""

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS merchants (
    merchant_id            TEXT PRIMARY KEY,
    airwallex_account_id   TEXT NOT NULL,
    delegated_authority    INTEGER NOT NULL,
    auto_accept_threshold  TEXT,
    active_policy_version  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    merchant_id  TEXT NOT NULL REFERENCES merchants(merchant_id),
    version      TEXT NOT NULL,
    policy_json  TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    PRIMARY KEY (merchant_id, version)
);

CREATE TABLE IF NOT EXISTS cases (
    case_id      TEXT PRIMARY KEY,
    merchant_id  TEXT NOT NULL REFERENCES merchants(merchant_id),
    state        TEXT NOT NULL,
    cycle        INTEGER NOT NULL,
    due_at       TEXT NOT NULL,
    urgent       INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cases_by_merchant ON cases (merchant_id, state, due_at);

CREATE TABLE IF NOT EXISTS cycles (
    merchant_id         TEXT NOT NULL,
    case_id             TEXT NOT NULL REFERENCES cases(case_id),
    cycle               INTEGER NOT NULL,
    stage               TEXT NOT NULL,
    policy_version      TEXT NOT NULL,
    ownership_resolved  INTEGER,
    tool_calls          INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT NOT NULL,
    PRIMARY KEY (case_id, cycle)
);

CREATE TABLE IF NOT EXISTS dispute_snapshots (
    snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id   TEXT NOT NULL,
    case_id       TEXT NOT NULL,
    dispute_json  TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_records (
    merchant_id  TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    record_id    TEXT NOT NULL,
    record_json  TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    PRIMARY KEY (case_id, record_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id    TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL,
    case_id        TEXT NOT NULL,
    cycle          INTEGER NOT NULL,
    seq            INTEGER NOT NULL,
    action         TEXT NOT NULL,
    decision_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (case_id, cycle, seq)
);

CREATE TABLE IF NOT EXISTS claims (
    merchant_id  TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    cycle        INTEGER NOT NULL,
    draft        INTEGER NOT NULL,
    claim_id     TEXT NOT NULL,
    claim_json   TEXT NOT NULL,
    PRIMARY KEY (case_id, cycle, draft, claim_id)
);

CREATE TABLE IF NOT EXISTS verifications (
    merchant_id  TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    cycle        INTEGER NOT NULL,
    draft        INTEGER NOT NULL,
    outcome      TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    PRIMARY KEY (case_id, cycle, draft)
);

CREATE TABLE IF NOT EXISTS renders (
    merchant_id  TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    cycle        INTEGER NOT NULL,
    draft        INTEGER NOT NULL,
    filename     TEXT NOT NULL,
    content      BLOB NOT NULL,
    sha256       TEXT NOT NULL,
    file_id      TEXT,
    PRIMARY KEY (case_id, cycle, draft)
);

CREATE TABLE IF NOT EXISTS referrals (
    referral_id    TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL,
    case_id        TEXT NOT NULL,
    cycle          INTEGER NOT NULL,
    reasons_json   TEXT NOT NULL,
    urgent         INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    resolution     TEXT,
    approval_json  TEXT,
    resolved_at    TEXT
);

CREATE TABLE IF NOT EXISTS writes (
    request_id     TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL,
    case_id        TEXT NOT NULL,
    cycle          INTEGER NOT NULL,
    action         TEXT NOT NULL,
    request_json   TEXT NOT NULL,
    status         TEXT NOT NULL,
    dry_run        INTEGER NOT NULL,
    response_json  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id  TEXT NOT NULL,
    case_id      TEXT,
    event        TEXT NOT NULL,
    body_json    TEXT NOT NULL,
    at           TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    hash         TEXT NOT NULL
);
"""
