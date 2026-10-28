"""The model-backed drafter: proposes structured claims through one forced tool call.

The schema limits what can be cited (this case's merchant and third-party records) and what
can be claimed (facts allowed for this reason). Code builds every Claim, assigns ids and
renders the text; an item code cannot build is dropped and recorded. Nothing here is trusted:
verify() decides what, if anything, may be submitted.
"""

import os
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from nirnaya import assets
from nirnaya.agent.context import facts_block, records_block
from nirnaya.agent.runs import AgentRun, DraftResult
from nirnaya.agent.scripted import FIELD_KIND
from nirnaya.evidence.catalogue import (
    FIELD_RULES,
    REASON_SPECIFIC,
    record_establishes,
    render_claim_text,
)
from nirnaya.evidence.derive import case_records
from nirnaya.llm.base import LLMClient
from nirnaya.llm.tracing import annotate
from nirnaya.llm.types import LLMError, Message, ToolSpec
from nirnaya.models.claim import Claim, DateTimeValue, DateValue, MoneyValue, TextValue
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import AmountBasis, EvidenceType, ReasonCategory, RecordAuthor
from nirnaya.models.evidence import EvidenceRecord
from nirnaya.models.money import Money

SUBMIT = "submit_claims"
VALUE_KINDS = ["MONEY", "DATETIME", "DATE", "NAME", "IDENTIFIER", "ENUM"]


def allowed_facts(dispute: Dispute) -> list[str]:
    facts = set(EvidenceType)
    if dispute.reason_category is ReasonCategory.UNKNOWN:
        facts -= set(REASON_SPECIFIC)
    return sorted(facts)


def citable(dispute: Dispute, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """This case's intact merchant and third-party records. Customer records establish nothing."""
    return [
        r
        for r in case_records(records, dispute)
        if r.hash_ok() and r.author is not RecordAuthor.CUSTOMER
    ]


Supported = list[tuple[EvidenceRecord, str, EvidenceType]]


def supported(dispute: Dispute, own: list[EvidenceRecord]) -> Supported:
    """(record, field, fact) for every field of this case's records that establishes a fact.

    Only these are offered to the model, so it cannot pick a fact no record supports (a live
    model once claimed "separate purchase" from a bare order id, which blocked its whole draft).
    The verifier still checks everything the model submits.
    """
    allowed = set(allowed_facts(dispute))
    return [
        (record, name, fact)
        for record in own
        for name in record.fields
        for fact in sorted(record_establishes(record, name))
        if fact in allowed
    ]


def field_guide(pairs: Supported) -> str:
    """Exactly which field of which record establishes which fact, and its value form."""
    lines = []
    for record, name, fact in pairs:
        kind = FIELD_KIND.get((record.source_system, name), "IDENTIFIER")
        rule = next(
            r
            for r in FIELD_RULES
            if r.source_system is record.source_system and r.field == name and r.establishes is fact
        )
        lines.append(f"- {record.id}.{name} ({kind}) establishes {fact}: {rule.description}")
    return "\n".join(lines)


def submit_spec(record_ids: list[str], facts: list[str], wording: dict[str, Any]) -> ToolSpec:
    """The submit tool. Its structure is code; its wording (description and field notes) comes
    from the versioned tools prompt."""
    notes: dict[str, str] = wording.get("fields", {})

    def string(name: str) -> dict[str, Any]:
        if name in notes:
            return {"type": "string", "description": notes[name]}
        return {"type": "string"}

    item = {
        "type": "object",
        "properties": {
            "source_record_id": {"type": "string", "enum": record_ids},
            "source_field": string("source_field"),
            "establishes": {"type": "string", "enum": facts},
            "extracted_value": string("extracted_value"),
            "value_kind": {"type": "string", "enum": VALUE_KINDS},
            "text": string("text"),
            "amount": string("amount"),
            "currency": string("currency"),
            "basis": {"type": "string", "enum": [b.value for b in AmountBasis]},
            "at": string("at"),
            "date": string("date"),
            "tz": string("tz"),
        },
        "required": [
            "source_record_id",
            "source_field",
            "establishes",
            "extracted_value",
            "value_kind",
        ],
    }
    return ToolSpec(
        name=SUBMIT,
        description=wording["description"],
        input_schema={
            "type": "object",
            "properties": {"claims": {"type": "array", "items": item}},
            "required": ["claims"],
        },
    )


def _value(item: dict[str, Any]) -> Any:
    kind = item.get("value_kind")
    if kind == "MONEY":
        money = Money(amount=str(item.get("amount", "")), currency=str(item.get("currency", "")))
        return MoneyValue(money=money, basis=item.get("basis") or AmountBasis.GROSS)
    if kind == "DATETIME":
        return DateTimeValue(at=item.get("at"))
    if kind == "DATE":
        return DateValue(date=item.get("date"), tz=item.get("tz"))
    if kind in {"NAME", "IDENTIFIER", "ENUM"}:
        return TextValue(kind=kind, text=item.get("text"))
    raise ValueError(f"unknown value_kind {kind!r}")


def build_claims(
    dispute: Dispute, items: list[Any], citable_ids: set[str]
) -> tuple[list[Claim], list[dict[str, Any]]]:
    """Build Claims from a model's submitted items. Code assigns ids and renders the text; an
    item that cannot be built is dropped with why. Nothing built here is trusted."""
    claims: list[Claim] = []
    dropped: list[dict[str, Any]] = []
    for n, item in enumerate(items, start=1):
        try:
            if not isinstance(item, dict) or item.get("source_record_id") not in citable_ids:
                raise ValueError("cites a record that is not citable")
            value = _value(item)
            fact = EvidenceType(item["establishes"])
            claims.append(
                Claim(
                    id=f"claim-{n}",
                    case_id=dispute.id,
                    merchant_id=dispute.merchant_id,
                    text=render_claim_text(fact, value),
                    source_record_id=item["source_record_id"],
                    source_field=str(item["source_field"]),
                    establishes=fact,
                    extracted_value=str(item["extracted_value"]),
                    normalised_value=value,
                )
            )
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            dropped.append({"item": item, "why": str(e).splitlines()[0][:200]})
    return claims, dropped


class LLMDrafter:
    def __init__(
        self, llm: LLMClient, *, max_tokens: int = 4096, env: Mapping[str, str] | None = None
    ) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        env = os.environ if env is None else env
        self.system = assets.prompt("drafter_system", env)
        self.user = assets.prompt("drafter_user", env)
        self.tools_asset, tools = assets.prompt_table("tools", env)
        self.wording: dict[str, Any] = tools["drafter"]["submit_claims"]

    def draft(self, dispute: Dispute, records: list[EvidenceRecord]) -> DraftResult:
        own = citable(dispute, records)
        if not own:
            return DraftResult(claims=())
        pairs = supported(dispute, own)
        if not pairs:
            return DraftResult(claims=())  # nothing these records can establish
        facts = sorted({fact.value for _, _, fact in pairs})
        spec = submit_spec(sorted({record.id for record, _, _ in pairs}), facts, self.wording)
        prompt = assets.render(
            self.user,
            facts=facts_block(dispute),
            records=records_block(own, source="citable-records"),
            field_guide=field_guide(pairs),
        )
        run_base = {
            "kind": "DRAFT",
            "provider": self.llm.provider,
            "model": self.llm.model,
            "prompts": (self.system.ref, self.user.ref, self.tools_asset.ref),
        }
        try:
            with annotate(
                step="draft",
                case_id=dispute.id,
                merchant_id=dispute.merchant_id,
                stage=str(dispute.stage),
                prompts=run_base["prompts"],
            ):
                resp = self.llm.complete(
                    system=self.system.text,
                    messages=[Message.user(prompt)],
                    tools=[spec],
                    force_tool=SUBMIT,
                    max_tokens=self.max_tokens,
                )
        except LLMError as e:
            return DraftResult(claims=(), run=AgentRun(**run_base, turns=1, stop=f"LLM_ERROR: {e}"))
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        call = next((c for c in resp.tool_calls if c.name == SUBMIT), None)
        if call is None or not isinstance(call.arguments.get("claims"), list):
            return DraftResult(
                claims=(),
                run=AgentRun(**run_base, **usage, turns=1, stop=f"NO_SUBMISSION: {resp.stop}"),
            )
        claims, dropped = build_claims(dispute, call.arguments["claims"], {r.id for r in own})
        run = AgentRun(
            **run_base,
            **usage,
            turns=1,
            stop=str(resp.stop),
            dropped=tuple(dropped),
            transcript=({"submitted": len(call.arguments["claims"])},),
        )
        return DraftResult(claims=tuple(claims), run=run)
