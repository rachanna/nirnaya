"""The sandbox simulator interface over FakeAirwallex, for tests and the offline runner. SYNTHETIC.

ASSUMPTION: the reason types below are what this fake assigns to the Visa codes the kit uses.
The real sandbox decides for itself (architecture open decision 16); the live runner reports
what it assigned.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from nirnaya.integrations.airwallex.simulation import (
    PAID,
    InFavorOf,
    StagedIntent,
    StagedPayment,
)
from nirnaya.integrations.mocks.airwallex_fake import FakeAirwallex
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import (
    AvsResult,
    CardBrand,
    CvcResult,
    DisputeStatus,
    Stage,
    ThreeDSResult,
)
from nirnaya.models.money import Money
from nirnaya.models.payment import PaymentAttempt
from nirnaya.models.reasons import DisputeReason
from nirnaya.orchestrator.ports import AirwallexError

FAKE_REASON_TYPES = {
    "10.4": "FRAUDULENT",
    "13.1": "PRODUCT_NOT_RECEIVED",
    "13.6": "CREDIT_NOT_PROCESSED",
}


@dataclass
class _Intent:
    merchant_id: str
    amount: Money
    merchant_order_id: str
    attempt_id: str
    status: str = "REQUIRES_PAYMENT_METHOD"


@dataclass
class FakeSimulator:
    aw: FakeAirwallex
    now: Callable[[], datetime]
    _intents: dict[str, _Intent] = field(default_factory=dict)
    _by_key: dict[str, str] = field(default_factory=dict)

    def create_intent(
        self, merchant_id: str, *, key: str, amount: Money, merchant_order_id: str
    ) -> StagedIntent:
        if key in self._by_key:  # same key, same operation: idempotent like a request_id
            intent_id = self._by_key[key]
        else:
            intent_id = f"int_fake_{len(self._intents) + 1:03d}"
            self._intents[intent_id] = _Intent(
                merchant_id, amount, merchant_order_id, f"att_fake_{len(self._intents) + 1:03d}"
            )
            self._by_key[key] = intent_id
        intent = self._intents[intent_id]
        return StagedIntent(intent_id, f"secret_{intent_id}", intent.status)

    def confirm_with_test_card(
        self, merchant_id: str, *, key: str, payment_intent_id: str
    ) -> StagedPayment:
        self.pay(payment_intent_id)
        intent = self._intents[payment_intent_id]
        return StagedPayment(payment_intent_id, intent.attempt_id, intent.status)

    def pay(self, payment_intent_id: str) -> None:
        """What the shopper does on the hosted checkout page (test helper)."""
        self._intents[payment_intent_id].status = PAID

    def intent_status(self, merchant_id: str, payment_intent_id: str) -> str:
        intent = self._intents.get(payment_intent_id)
        if intent is None or intent.merchant_id != merchant_id:
            raise AirwallexError("not_found", f"payment intent {payment_intent_id}")
        return intent.status

    def create_dispute(
        self,
        merchant_id: str,
        *,
        payment_intent_id: str,
        reason_code: str,
        stage: Stage,
        due_at: datetime,
    ) -> str:
        intent = self._intents.get(payment_intent_id)
        if intent is None or intent.merchant_id != merchant_id:
            raise AirwallexError("not_found", f"payment intent {payment_intent_id}")
        if intent.status != PAID:
            raise AirwallexError("validation_error", "the payment intent has not succeeded")
        dispute_id = f"dst_fake_{len(self.aw.list_disputes(merchant_id)) + 1:03d}"
        opened = self.now()
        attempt = PaymentAttempt(
            id=intent.attempt_id,
            merchant_id=merchant_id,
            payment_intent_id=payment_intent_id,
            amount=intent.amount,
            three_ds=ThreeDSResult.NOT_PERFORMED,
            avs=AvsResult.UNKNOWN,
            cvc=CvcResult.UNKNOWN,
            created_at=opened,
        )
        self.aw.add_dispute(
            Dispute(
                id=dispute_id,
                merchant_id=merchant_id,
                payment_intent_id=payment_intent_id,
                payment_attempt_id=intent.attempt_id,
                merchant_order_id=intent.merchant_order_id,
                stage=stage,
                status=DisputeStatus.REQUIRES_RESPONSE,
                reason=DisputeReason(
                    type=FAKE_REASON_TYPES.get(reason_code, "UNKNOWN"), original_code=reason_code
                ),
                card_brand=CardBrand.VISA,
                amount=intent.amount,
                opened_at=opened,
                due_at=due_at,
            ),
            attempt=attempt,
        )
        return dispute_id

    def escalate(self, merchant_id: str, dispute_id: str, *, due_at: datetime) -> None:
        self.aw.retrieve_dispute(merchant_id, dispute_id)  # tenant check
        self.aw.simulate_escalate(dispute_id, due_at=due_at)

    def resolve(self, merchant_id: str, dispute_id: str, *, in_favor_of: InFavorOf) -> None:
        self.aw.retrieve_dispute(merchant_id, dispute_id)
        self.aw.simulate_resolve(dispute_id, in_favor_of=in_favor_of)
