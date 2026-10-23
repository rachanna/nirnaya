"""Deterministic investigator and drafter for Milestone 3 (no LLM).

They implement the same ports Claude will in Milestone 4, so the pipeline can be tested end
to end without a model.
"""

from decimal import Decimal

from nirnaya.agent.runs import DraftResult, InvestigationResult
from nirnaya.agent.targets import tools_for
from nirnaya.agent.tools import ToolGateway, ToolRefused
from nirnaya.evidence.catalogue import (
    DENIAL_FACTS,
    FIELD_RULES,
    REASON_SPECIFIC,
    record_establishes,
    render_claim_text,
)
from nirnaya.evidence.derive import case_records
from nirnaya.models.claim import (
    Claim,
    DateTimeValue,
    MoneyValue,
    NormalisedValue,
    TextValue,
)
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import (
    AmountBasis,
    EvidenceType,
    ReasonCategory,
    RecordAuthor,
    SourceSystem,
)
from nirnaya.models.evidence import EvidenceRecord, raw_text
from nirnaya.models.money import FxTable, MissingFxRateError, Money, convert
from nirnaya.verifier.normalise import parse_instant

S = SourceSystem

# How each citable field's value is normalised in a claim.
FIELD_KIND: dict[tuple[SourceSystem, str], str] = {
    (S.CARRIER, "status"): "ENUM",
    (S.CARRIER, "delivered_at"): "DATETIME",
    (S.CARRIER, "signed_by"): "NAME",
    (S.PAYMENT_ATTEMPT, "three_ds"): "ENUM",
    (S.PAYMENT_ATTEMPT, "avs"): "ENUM",
    (S.PAYMENT_ATTEMPT, "cvc"): "ENUM",
    (S.LOGIN, "purchase_device_login_at"): "DATETIME",
    (S.LOGIN, "active_after_renewal_at"): "DATETIME",
    (S.ORDER, "order_id"): "IDENTIFIER",
    (S.ORDER, "placed_at"): "DATETIME",
    (S.ORDER, "total_amount"): "MONEY",
    (S.ORDER, "customer_name"): "NAME",
    (S.ORDER, "prior_undisputed_orders_matching_device_ip"): "ENUM",
    (S.ORDER, "refunded_amount"): "MONEY",
    (S.POLICY, "cancellation_terms_accepted_at"): "DATETIME",
    (S.HELPDESK, "contacts_before_dispute"): "ENUM",
}


class ScriptedInvestigator:
    """Calls each needed tool once per lookup key, in a fixed order."""

    def investigate(
        self, dispute: Dispute, targets: list[EvidenceType], tools: ToolGateway
    ) -> InvestigationResult:
        used = 0
        called = tools.called()
        for tool in tools_for(targets):
            for key in tools.lookup_keys(tool):
                if f"{tool}:{key}" in called:
                    continue
                try:
                    tools.fetch(tool, key)
                except ToolRefused:
                    return InvestigationResult(calls=used)
                used += 1
        return InvestigationResult(calls=used)


def _normalise(
    kind: str, record: EvidenceRecord, field: str, dispute: Dispute, fx: FxTable | None
) -> NormalisedValue | None:
    value = record.fields[field]
    if kind == "DATETIME":
        try:
            return DateTimeValue(at=parse_instant(value))
        except ValueError:
            return None
    if kind == "MONEY":
        currency = record.fields.get("currency")
        if not isinstance(value, str) or not isinstance(currency, str):
            return None
        try:
            money, _ = convert(
                Money(amount=Decimal(value), currency=currency), dispute.amount.currency, fx
            )
        except (ValueError, MissingFxRateError, ArithmeticError):
            return None
        return MoneyValue(money=money, basis=AmountBasis.GROSS)
    text = raw_text(value)
    return None if not text else TextValue(kind=kind, text=text)


class ScriptedDrafter:
    """One claim per fact the case's records establish, from the first matching field rule.

    It never drafts a reason-specific claim under an UNKNOWN reason, and never denies a
    customer contact when a customer-authored record is on file.
    """

    def __init__(self, fx: FxTable | None = None) -> None:
        self._fx = fx

    def draft(self, dispute: Dispute, records: list[EvidenceRecord]) -> DraftResult:
        own = [r for r in case_records(records, dispute) if r.hash_ok()]
        customer_wrote = any(r.author is RecordAuthor.CUSTOMER for r in own)
        claims: list[Claim] = []
        for fact in sorted(EvidenceType):
            if dispute.reason_category is ReasonCategory.UNKNOWN and fact in REASON_SPECIFIC:
                continue
            if fact in DENIAL_FACTS and customer_wrote:
                continue
            claim = self._first_claim(fact, own, dispute)
            if claim is not None:
                claims.append(claim)
        return DraftResult(claims=tuple(claims))

    def _first_claim(
        self, fact: EvidenceType, records: list[EvidenceRecord], dispute: Dispute
    ) -> Claim | None:
        for rule in FIELD_RULES:
            if rule.establishes is not fact:
                continue
            for record in records:
                if fact not in record_establishes(record, rule.field):
                    continue
                kind = FIELD_KIND[(record.source_system, rule.field)]
                value = _normalise(kind, record, rule.field, dispute, self._fx)
                if value is None:
                    continue
                return Claim(
                    id=f"claim-{fact.lower().replace('_', '-')}",
                    case_id=dispute.id,
                    merchant_id=dispute.merchant_id,
                    text=render_claim_text(fact, value),
                    source_record_id=record.id,
                    source_field=rule.field,
                    establishes=fact,
                    extracted_value=raw_text(record.fields[rule.field]) or "",
                    normalised_value=value,
                )
        return None
