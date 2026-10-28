"""The model-backed note reader (architecture 8.8): proposes exact quotes from merchant and
carrier notes through one tool call.

The schema limits what can be proposed (the records shown, their note fields, the facts asked
about). Nothing here is trusted: the machine keeps only quotes word for word in an allowed note
(agent.reading.screen), a person confirms them, and the verifier checks them again.
"""

import os
from collections.abc import Mapping
from typing import Any

from nirnaya import assets
from nirnaya.agent.context import facts_block, records_block
from nirnaya.agent.reading import note_fields
from nirnaya.agent.runs import AgentRun, ReadResult
from nirnaya.evidence.catalogue import TEXT_RULES, Quote
from nirnaya.llm.base import LLMClient
from nirnaya.llm.tracing import annotate
from nirnaya.llm.types import LLMError, Message, ToolSpec
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType
from nirnaya.models.evidence import EvidenceRecord

PROPOSE = "propose_quotes"


def propose_spec(
    record_ids: list[str], fields: list[str], facts: list[str], wording: dict[str, Any]
) -> ToolSpec:
    notes: dict[str, str] = wording.get("fields", {})
    item = {
        "type": "object",
        "properties": {
            "record_id": {"type": "string", "enum": record_ids},
            "field": {"type": "string", "enum": fields},
            "fact": {"type": "string", "enum": facts},
            "quote": {"type": "string", "description": notes.get("quote", "")},
        },
        "required": ["record_id", "field", "fact", "quote"],
    }
    return ToolSpec(
        name=PROPOSE,
        description=wording["description"],
        input_schema={
            "type": "object",
            "properties": {"quotes": {"type": "array", "items": item}},
            "required": ["quotes"],
        },
    )


def note_guide(
    records: list[EvidenceRecord], facts: frozenset[EvidenceType], meaning: Mapping[str, str]
) -> str:
    """Which note of which record may state which fact, and what the fact means."""
    lines = []
    for record in records:
        for name in note_fields(record, facts):
            for fact in sorted(TEXT_RULES[(record.source_system, name)] & facts):
                lines.append(f"- {record.id}.{name}: {fact} ({meaning.get(fact, fact)})")
    return "\n".join(lines)


def parse_quotes(items: list[Any]) -> tuple[list[Quote], list[dict[str, Any]]]:
    quotes: list[Quote] = []
    dropped: list[dict[str, Any]] = []
    for item in items:
        try:
            if not isinstance(item, dict):
                raise TypeError("not an object")
            quote = item["quote"]
            if not isinstance(quote, str) or not quote.strip():
                raise ValueError("empty quote")
            quotes.append(
                Quote(str(item["record_id"]), str(item["field"]), EvidenceType(item["fact"]), quote)
            )
        except (KeyError, TypeError, ValueError) as e:
            dropped.append({"item": item, "why": str(e).splitlines()[0][:200]})
    return quotes, dropped


class LLMReader:
    def __init__(
        self, llm: LLMClient, *, max_tokens: int = 2048, env: Mapping[str, str] | None = None
    ) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        env = os.environ if env is None else env
        self.system = assets.prompt("reader_system", env)
        self.user = assets.prompt("reader_user", env)
        self.tools_asset, tools = assets.prompt_table("reader_tools", env)
        self.wording: dict[str, Any] = tools["reader"]["propose_quotes"]
        self.meaning: dict[str, str] = tools["reader"].get("facts", {})

    def read(
        self, dispute: Dispute, facts: frozenset[EvidenceType], records: list[EvidenceRecord]
    ) -> ReadResult:
        fields = sorted({name for r in records for name in note_fields(r, facts)})
        if not fields:
            return ReadResult()
        spec = propose_spec(
            sorted(r.id for r in records), fields, sorted(f.value for f in facts), self.wording
        )
        prompt = assets.render(
            self.user,
            facts=facts_block(dispute),
            records=records_block(records, source="merchant-and-carrier-records"),
            guide=note_guide(records, facts, self.meaning),
        )
        run_base: dict[str, Any] = {
            "kind": "READ",
            "provider": self.llm.provider,
            "model": self.llm.model,
            "prompts": (self.system.ref, self.user.ref, self.tools_asset.ref),
        }
        try:
            with annotate(
                step="read",
                case_id=dispute.id,
                merchant_id=dispute.merchant_id,
                stage=str(dispute.stage),
                prompts=run_base["prompts"],
            ):
                resp = self.llm.complete(
                    system=self.system.text,
                    messages=[Message.user(prompt)],
                    tools=[spec],
                    force_tool=PROPOSE,
                    max_tokens=self.max_tokens,
                )
        except LLMError as e:
            return ReadResult(run=AgentRun(**run_base, turns=1, stop=f"LLM_ERROR: {e}"))
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        call = next((c for c in resp.tool_calls if c.name == PROPOSE), None)
        if call is None or not isinstance(call.arguments.get("quotes"), list):
            return ReadResult(
                run=AgentRun(**run_base, **usage, turns=1, stop=f"NO_SUBMISSION: {resp.stop}")
            )
        quotes, dropped = parse_quotes(call.arguments["quotes"])
        run = AgentRun(
            **run_base,
            **usage,
            turns=1,
            stop=str(resp.stop),
            dropped=tuple(dropped),
            transcript=({"proposed": len(call.arguments["quotes"])},),
        )
        return ReadResult(proposals=tuple(quotes), run=run)
