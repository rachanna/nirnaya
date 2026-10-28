"""The tool gateway: the only way an investigator reaches merchant systems.

It injects merchant_id and case_id (no tool takes them as arguments), accepts only lookup
keys that already appear in the case's own records, snapshots every result as hashed
evidence by code, stores it, and enforces the per-cycle tool budget.
"""

from datetime import datetime
from enum import StrEnum

from nirnaya.integrations.mocks.systems import (
    CarrierTracking,
    Helpdesk,
    LoginHistory,
    OrderSystem,
    PolicyStore,
)
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import SourceSystem
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.store.repository import Store


class Tool(StrEnum):
    ORDER = "get_order"
    CARRIER = "get_carrier_tracking"
    LOGIN = "get_login_history"
    HELPDESK = "get_helpdesk_contacts"
    POLICY = "get_policy_acceptance"


def allowed_keys(tool: Tool, dispute: Dispute, records: list[EvidenceRecord]) -> list[str]:
    """The keys a case allows for a tool: the dispute's order id, or values found in the
    case's own order records. Shared by the tool gateway, the counterevidence sweep and the
    write gateway's check, so all three agree on what "every key" means."""
    if tool is Tool.ORDER:
        order_id = dispute.merchant_order_id
        return [order_id] if order_id else []
    orders = [r for r in records if r.source_system is SourceSystem.ORDER]
    field = "tracking_number" if tool is Tool.CARRIER else "customer_id"
    return sorted({v for r in orders if isinstance(v := r.fields.get(field), str) and v})


class ToolRefused(RuntimeError):
    """The call was not allowed: unknown key, or the budget is spent."""


class MerchantSystems(OrderSystem, CarrierTracking, LoginHistory, Helpdesk, PolicyStore):
    """Everything the gateway needs; SyntheticWorld provides it."""


class ToolGateway:
    def __init__(
        self,
        store: Store,
        systems: MerchantSystems,
        dispute: Dispute,
        cycle: int,
        *,
        now: datetime,
        max_calls: int,
    ) -> None:
        self._store = store
        self._systems = systems
        self._dispute = dispute
        self._merchant = dispute.merchant_id
        self._cycle = cycle
        self._now = now
        self._max_calls = max_calls

    @property
    def calls_used(self) -> int:
        return self._store.cycle(self._merchant, self._dispute.id, self._cycle).tool_calls

    def called(self) -> set[str]:
        return self._store.tools_called(self._merchant, self._dispute.id, self._cycle)

    def case_evidence(self) -> list[EvidenceRecord]:
        """The case's records captured so far (for showing the model what is on file)."""
        return self._store.evidence(self._merchant, self._dispute.id)

    def lookup_keys(self, tool: Tool) -> list[str]:
        """The keys this case allows for a tool: the dispute's order id, or values found in
        the case's own order records. The investigator cannot invent a key."""
        return allowed_keys(tool, self._dispute, self.case_evidence())

    def refuse(self, tool: str, key: object, why: str) -> None:
        """Audit a call the investigator asked for and was not allowed to make."""
        with self._store.transaction():
            self._store.audit(
                self._merchant,
                self._dispute.id,
                "TOOL_REFUSED",
                {"tool": str(tool), "key": str(key), "why": why},
                self._now,
            )

    def fetch(self, tool: Tool, key: str) -> list[EvidenceRecord]:
        if key not in self.lookup_keys(tool):
            raise ToolRefused(f"{tool} key {key!r} does not come from this case's records")
        if self.calls_used >= self._max_calls:
            raise ToolRefused("tool budget for this cycle is spent")
        lookup = {
            Tool.ORDER: self._systems.find_orders,
            Tool.CARRIER: self._systems.tracking_events,
            Tool.LOGIN: self._systems.login_summary,
            Tool.HELPDESK: self._systems.customer_contacts,
            Tool.POLICY: self._systems.policy_acceptances,
        }[tool]
        found = lookup(self._merchant, key)
        records = [s.snapshot(case_id=self._dispute.id, captured_at=self._now) for s in found]
        with self._store.transaction():
            records = [
                self._store.put_evidence_or_revise(self._merchant, record, self._now)
                for record in records
            ]
            self._store.record_tool_called(
                self._merchant, self._dispute.id, self._cycle, f"{tool}:{key}"
            )
            self._store.audit(
                self._merchant,
                self._dispute.id,
                "TOOL_CALL",
                {"tool": tool, "key": key, "records": [r.id for r in records]},
                self._now,
            )
        return records
