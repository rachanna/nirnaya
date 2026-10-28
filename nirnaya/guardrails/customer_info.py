"""customer_info for a chargeback challenge: the customer's device ID and IP address.

Airwallex's challenge request takes an optional `customer_info` object, "applicable when
Payment Dispute stage is CHARGEBACK" (API reference, checked 2026-10-28), with `device_id`
("Customer device unique identifier") and `ip` ("Customer ip"), among others. Nirnaya sends
only those two, and only when a record establishes them:

- the stage is CHARGEBACK;
- the record is the merchant's own ORDER record, intact, and cited by the verified claims (so
  the gateway has already checked it belongs to this case and merchant and is unchanged);
- ip_address parses as an IP address; device_id is non-blank text of at most 128 characters.

A value that fails is left out, never repaired. Deterministic; decided in code, not by a model.
"""

import ipaddress
from collections.abc import Iterable
from typing import Any

from nirnaya.models.base import DomainModel, NonBlankStr
from nirnaya.models.enums import RecordAuthor, SourceSystem, Stage
from nirnaya.models.evidence import EvidenceRecord

DEVICE_FIELD, IP_FIELD = "device_id", "ip_address"
MAX_DEVICE_ID = 128


class CustomerInfo(DomainModel):
    device_id: str | None = None
    ip: str | None = None
    # Where the values came from, for the ledger, the audit log and the approver.
    source_record_id: NonBlankStr
    source_sha256: NonBlankStr

    def wire(self) -> dict[str, str]:
        """The request's customer_info: only the values present."""
        return {k: v for k, v in {"device_id": self.device_id, "ip": self.ip}.items() if v}


def _device(value: Any) -> str | None:  # noqa: ANN401
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_DEVICE_ID:
        return None
    return value


def _ip(value: Any) -> str | None:  # noqa: ANN401
    if not isinstance(value, str):
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def customer_info(
    stage: Stage, records: Iterable[EvidenceRecord], *, cited: set[str]
) -> CustomerInfo | None:
    if stage is not Stage.CHARGEBACK:
        return None
    for record in records:
        if (
            record.id not in cited
            or record.source_system is not SourceSystem.ORDER
            or record.author is not RecordAuthor.MERCHANT
            or not record.hash_ok()
        ):
            continue
        device = _device(record.fields.get(DEVICE_FIELD))
        ip = _ip(record.fields.get(IP_FIELD))
        if device or ip:
            return CustomerInfo(
                device_id=device,
                ip=ip,
                source_record_id=record.id,
                source_sha256=record.sha256,
            )
    return None
