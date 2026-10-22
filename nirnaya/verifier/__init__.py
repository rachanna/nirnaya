"""Deterministic verifier: blocks any claim its cited record does not prove."""

from nirnaya.verifier.result import (
    CheckName,
    ClaimResult,
    Counterevidence,
    Failure,
    VerificationOutcome,
    VerificationResult,
)
from nirnaya.verifier.verify import verify

__all__ = [
    "CheckName",
    "ClaimResult",
    "Counterevidence",
    "Failure",
    "VerificationOutcome",
    "VerificationResult",
    "verify",
]
