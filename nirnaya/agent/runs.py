"""What an investigator or drafter returns. The machine persists runs with its step."""

from typing import Any, Literal

from nirnaya.models.base import DomainModel
from nirnaya.models.claim import Claim


class ToolCallRecord(DomainModel):
    tool: str
    key: str | None = None
    outcome: Literal["FETCHED", "REFUSED", "FINISH"]
    detail: str = ""
    records: tuple[str, ...] = ()


class AgentRun(DomainModel):
    """One model-backed investigation round or drafting call."""

    kind: Literal["INVESTIGATE", "DRAFT"]
    provider: str
    model: str
    turns: int
    stop: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: tuple[ToolCallRecord, ...] = ()
    # Draft items the model proposed that code could not turn into a claim.
    dropped: tuple[dict[str, Any], ...] = ()
    transcript: tuple[dict[str, Any], ...] = ()

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class InvestigationResult(DomainModel):
    calls: int
    run: AgentRun | None = None


class DraftResult(DomainModel):
    claims: tuple[Claim, ...]
    run: AgentRun | None = None
