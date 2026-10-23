import sqlite3
from pathlib import Path

from nirnaya.store.schema import SCHEMA, SCHEMA_VERSION


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open (and if new, create) a Nirnaya database. Transactions are managed explicitly."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] != SCHEMA_VERSION:
        raise RuntimeError(f"database schema {row['version']} != expected {SCHEMA_VERSION}")
    return conn
