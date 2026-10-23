"""Deterministic verifier: blocks any claim its cited record does not prove."""

from nirnaya.verifier.result import (
    ApproverNote,
    CheckName,
    ClaimResult,
    Counterevidence,
    Failure,
    VerificationOutcome,
    VerificationResult,
)
from nirnaya.verifier.verify import verify

__all__ = [
    "ApproverNote",
    "CheckName",
    "ClaimResult",
    "Counterevidence",
    "Failure",
    "VerificationOutcome",
    "VerificationResult",
    "verify",
]
