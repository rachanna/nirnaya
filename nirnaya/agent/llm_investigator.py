"""The model-backed investigator: one round of permitted lookups per call.

The model chooses among the tools code offers for the current targets, with each tool's key
restricted to an enum of keys the case allows; the gateway checks again. Any problem ends the
round: the engine then decides on what was found.
"""

import os
from collections.abc import Mapping
from typing import Any

from nirnaya import assets
from nirnaya.agent.context import facts_block, records_block
from nirnaya.agent.runs import AgentRun, InvestigationResult, ToolCallRecord
from nirnaya.agent.targets import tools_for
from nirnaya.agent.tools import Tool, ToolGateway, ToolRefused
from nirnaya.llm.base import LLMClient
from nirnaya.llm.types import LLMError, Message, StopReason, ToolSpec
from nirnaya.models.dispute import Dispute
from nirnaya.models.enums import EvidenceType

FINISH = "finish"


def _spec(tool: Tool, keys: list[str], description: str) -> ToolSpec:
    return ToolSpec(
        name=tool.value,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"key": {"type": "string", "enum": keys}},
            "required": ["key"],
            "additionalProperties": False,
        },
    )


def _finish_spec(description: str) -> ToolSpec:
    return ToolSpec(
        name=FINISH,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    )


class LLMInvestigator:
    def __init__(
        self,
        llm: LLMClient,
        *,
        max_calls_per_round: int = 3,
        max_turns: int = 6,
        max_tokens: int = 1024,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.llm = llm
        self.max_calls = max_calls_per_round
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        env = os.environ if env is None else env
        self.system = assets.prompt("investigator_system", env)
        self.user = assets.prompt("investigator_user", env)
        self.tools_asset, tools = assets.prompt_table("tools", env)
        self.descriptions: dict[str, str] = tools["investigator"]

    def offered(self, targets: list[EvidenceType], tools: ToolGateway) -> dict[str, list[str]]:
        """Tool name -> keys not yet called, for the tools the current targets need."""
        called = tools.called()
        offer = {}
        for tool in tools_for(targets):
            keys = [k for k in tools.lookup_keys(tool) if f"{tool}:{k}" not in called]
            if keys:
                offer[tool.value] = keys
        return offer

    def investigate(
        self, dispute: Dispute, targets: list[EvidenceType], tools: ToolGateway
    ) -> InvestigationResult:
        offer = self.offered(targets, tools)
        if not offer:
            return InvestigationResult(calls=0)
        specs = [
            _spec(Tool(name), keys, self.descriptions[name]) for name, keys in offer.items()
        ] + [_finish_spec(self.descriptions[FINISH])]
        records = tools.case_evidence()
        messages = [
            Message.user(
                assets.render(
                    self.user,
                    facts=facts_block(dispute),
                    targets=", ".join(sorted(targets)),
                    records=records_block(records, source="case-records"),
                )
            )
        ]
        calls: list[ToolCallRecord] = []
        transcript: list[dict[str, Any]] = []
        used = turns = tokens_in = tokens_out = 0
        stop = "TURN_LIMIT"
        while turns < self.max_turns:
            turns += 1
            try:
                resp = self.llm.complete(
                    system=self.system.text,
                    messages=messages,
                    tools=specs,
                    max_tokens=self.max_tokens,
                )
            except LLMError as e:
                stop = f"LLM_ERROR: {e}"
                break
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            transcript.append(
                {
                    "turn": turns,
                    "stop": resp.stop,
                    "text": resp.text,
                    "tool_calls": [c.model_dump() for c in resp.tool_calls],
                }
            )
            if resp.stop in {StopReason.REFUSAL, StopReason.MAX_TOKENS}:
                stop = str(resp.stop)
                break
            if not resp.tool_calls:
                stop = "NO_TOOL_CALL"
                break
            messages.append(resp.as_message())
            finished = False
            for call in resp.tool_calls:
                record, reply, did_fetch = self._handle(
                    call.name, call.arguments, offer, tools, used
                )
                calls.append(record)
                used += did_fetch
                finished |= record.outcome == "FINISH"
                messages.append(Message.tool_result(call.id, reply))
            if finished:
                stop = "FINISH"
                break
            if used >= self.max_calls:
                stop = "ROUND_LIMIT"
                break
        run = AgentRun(
            kind="INVESTIGATE",
            provider=self.llm.provider,
            model=self.llm.model,
            turns=turns,
            stop=stop,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            prompts=(self.system.ref, self.user.ref, self.tools_asset.ref),
            tool_calls=tuple(calls),
            transcript=tuple(transcript),
        )
        return InvestigationResult(calls=used, run=run)

    def _handle(
        self,
        name: str,
        arguments: dict[str, Any],
        offer: dict[str, list[str]],
        tools: ToolGateway,
        used: int,
    ) -> tuple[ToolCallRecord, str, int]:
        if name == FINISH:
            return (
                ToolCallRecord(
                    tool=FINISH, outcome="FINISH", detail=str(arguments.get("reason", ""))[:200]
                ),
                "ok",
                0,
            )
        key = arguments.get("key")
        why = None
        if name not in offer:
            why = "this tool is not offered for this case right now"
        elif not isinstance(key, str) or key not in offer[name]:
            why = "that key is not one of the keys offered"
        elif used >= self.max_calls:
            why = "the lookup limit for this round is reached"
        if why is None:
            try:
                fetched = tools.fetch(Tool(name), key)
            except ToolRefused as e:
                why = str(e)
            else:
                record = ToolCallRecord(
                    tool=name, key=key, outcome="FETCHED", records=tuple(r.id for r in fetched)
                )
                return record, records_block(fetched, source=f"{name}:{key}"), 1
        tools.refuse(name, key, why)
        return (
            ToolCallRecord(tool=name, key=str(key), outcome="REFUSED", detail=why),
            f"Refused: {why}.",
            0,
        )
