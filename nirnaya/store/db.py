import sqlite3
from collections.abc import Callable
from pathlib import Path

from nirnaya.store.schema import SCHEMA, SCHEMA_VERSION


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _v1_to_v2(conn: sqlite3.Connection) -> None:
    # v2: a referral records why (the failing claims, records or guard check).
    if "detail_json" not in _columns(conn, "referrals"):
        conn.execute("ALTER TABLE referrals ADD COLUMN detail_json TEXT")


def _v2_to_v3(conn: sqlite3.Connection) -> None:
    # v3: quotes proposed from notes, awaiting a person (the findings table). SCHEMA creates it
    # on open; this step only records that the version changed.
    conn.execute("SELECT 1 FROM findings LIMIT 1")


# MIGRATIONS[n] brings a version-n database to version n + 1. Each step must be safe to re-run.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _v1_to_v2, 2: _v2_to_v3}


def _set_version(conn: sqlite3.Connection) -> None:
    """Stamp a new database, or migrate an old one one version at a time.

    Each step runs in its own write-locked transaction and re-reads the version inside it, so a
    crash leaves the database at a consistent version and a concurrent opener waits, then sees
    the version the first one wrote. An up-to-date database is only read, never locked.
    """
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is not None and row["version"] == SCHEMA_VERSION:
        return
    while True:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
                conn.execute("COMMIT")
                return
            version = row["version"]
            if version == SCHEMA_VERSION:
                conn.execute("COMMIT")
                return
            if version > SCHEMA_VERSION or version not in MIGRATIONS:
                raise RuntimeError(f"database schema {version} != expected {SCHEMA_VERSION}")
            MIGRATIONS[version](conn)
            conn.execute("UPDATE schema_version SET version = ?", (version + 1,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def connect(path: str | Path = ":memory:", *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (and if new, create) a Nirnaya database. Transactions are managed explicitly.

    `check_same_thread=False` is only for a caller that uses the connection from one thread at a
    time but not always the same one (a Streamlit session, which reruns on worker threads).
    """
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _set_version(conn)
    return conn
