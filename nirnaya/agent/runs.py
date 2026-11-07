"""What an investigator, drafter or reader returns. The machine persists runs with its step."""

from typing import Any, Literal

from nirnaya.evidence.catalogue import Quote
from nirnaya.models.base import DomainModel
from nirnaya.models.claim import Claim


class ToolCallRecord(DomainModel):
    tool: str
    key: str | None = None
    outcome: Literal["FETCHED", "REFUSED", "FAILED", "FINISH"]
    detail: str = ""
    records: tuple[str, ...] = ()


class AgentRun(DomainModel):
    """One model-backed investigation round, drafting call or reading of notes."""

    kind: Literal["INVESTIGATE", "DRAFT", "READ"]
    provider: str
    model: str
    turns: int
    stop: str
    input_tokens: int = 0
    output_tokens: int = 0
    # The prompt versions that produced this run: name@version#sha256 prefix (nirnaya/assets).
    prompts: tuple[str, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    # Items the model proposed that code could not use (a claim, a quote), with why.
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


class ReadResult(DomainModel):
    """Quotes a reader proposes from merchant and carrier notes. Never trusted: code keeps only
    those word for word in an allowed note (agent.reading.screen), and a person confirms them."""

    proposals: tuple[Quote, ...] = ()
    run: AgentRun | None = None
