"""SYNTHETIC merchant systems: order, carrier tracking, login history, helpdesk, policy store.

Each system is a typed, read-only, merchant-scoped lookup behind a Protocol, so a real
adapter can replace it later. Lookups chain the way real systems do: the dispute's
merchant_order_id finds the order; the order's tracking_number finds carrier events; its
customer_id finds login, helpdesk and policy records.

Records come back as SourceRecord (raw system output). Snapshotting a SourceRecord into a
hashed EvidenceRecord is a separate, explicit step (SourceRecord.snapshot).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Protocol

from nirnaya.models.base import DomainModel, NonBlankStr
from nirnaya.models.enums import RecordAuthor, SourceSystem
from nirnaya.models.evidence import EvidenceRecord, FieldValue

# The field each system is looked up by.
KEY_FIELD: Mapping[SourceSystem, str] = {
    SourceSystem.ORDER: "order_id",
    SourceSystem.CARRIER: "tracking_number",
    SourceSystem.LOGIN: "customer_id",
    SourceSystem.HELPDESK: "customer_id",
    SourceSystem.POLICY: "customer_id",
}


class SourceRecord(DomainModel):
    """One record as a merchant system returns it, before it becomes evidence."""

    id: NonBlankStr
    merchant_id: NonBlankStr
    system: SourceSystem
    author: RecordAuthor
    fields: dict[str, FieldValue]

    def snapshot(self, *, case_id: str, captured_at: datetime) -> EvidenceRecord:
        """Capture this record as hashed, SYNTHETIC evidence for one case."""
        return EvidenceRecord.create(
            id=self.id,
            merchant_id=self.merchant_id,
            case_id=case_id,
            source_system=self.system,
            author=self.author,
            synthetic=True,
            fields=self.fields,
            captured_at=captured_at,
        )


class OrderSystem(Protocol):
    def find_orders(self, merchant_id: str, order_id: str) -> list[SourceRecord]: ...


class CarrierTracking(Protocol):
    def tracking_events(self, merchant_id: str, tracking_number: str) -> list[SourceRecord]: ...


class LoginHistory(Protocol):
    def login_summary(self, merchant_id: str, customer_id: str) -> list[SourceRecord]: ...


class Helpdesk(Protocol):
    def customer_contacts(self, merchant_id: str, customer_id: str) -> list[SourceRecord]: ...


class PolicyStore(Protocol):
    def policy_acceptances(self, merchant_id: str, customer_id: str) -> list[SourceRecord]: ...


class SyntheticWorld:
    """In-memory implementation of all five systems. SYNTHETIC data only."""

    def __init__(self, records: Iterable[SourceRecord]) -> None:
        self._index: dict[tuple[str, SourceSystem, str], list[SourceRecord]] = defaultdict(list)
        seen: set[str] = set()
        for record in records:
            if record.system not in KEY_FIELD:
                raise ValueError(f"{record.system} is not a merchant system")
            key = record.fields.get(KEY_FIELD[record.system])
            if not isinstance(key, str) or not key:
                raise ValueError(f"record {record.id!r} has no {KEY_FIELD[record.system]}")
            if record.id in seen:
                raise ValueError(f"duplicate record id {record.id!r}")
            seen.add(record.id)
            self._index[(record.merchant_id, record.system, key)].append(record)
        for bucket in self._index.values():
            bucket.sort(key=lambda r: r.id)

    def _lookup(self, merchant_id: str, system: SourceSystem, key: str) -> list[SourceRecord]:
        return list(self._index.get((merchant_id, system, key), ()))

    def find_orders(self, merchant_id: str, order_id: str) -> list[SourceRecord]:
        return self._lookup(merchant_id, SourceSystem.ORDER, order_id)

    def tracking_events(self, merchant_id: str, tracking_number: str) -> list[SourceRecord]:
        return self._lookup(merchant_id, SourceSystem.CARRIER, tracking_number)

    def login_summary(self, merchant_id: str, customer_id: str) -> list[SourceRecord]:
        return self._lookup(merchant_id, SourceSystem.LOGIN, customer_id)

    def customer_contacts(self, merchant_id: str, customer_id: str) -> list[SourceRecord]:
        return self._lookup(merchant_id, SourceSystem.HELPDESK, customer_id)

    def policy_acceptances(self, merchant_id: str, customer_id: str) -> list[SourceRecord]:
        return self._lookup(merchant_id, SourceSystem.POLICY, customer_id)
