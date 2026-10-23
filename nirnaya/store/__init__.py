"""SQLite store: tenant-scoped repository and hash-chained audit log."""

from nirnaya.store.audit import verify_chain
from nirnaya.store.db import connect
from nirnaya.store.repository import CaseRow, Merchant, Store, StoreError

__all__ = ["CaseRow", "Merchant", "Store", "StoreError", "connect", "verify_chain"]
