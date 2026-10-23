"""Deterministic request ids.

Airwallex wants a UUID per operation, reused only to retry that same operation (kit 4 guide).
A UUIDv5 of the operation's identity gives exactly that with no stored randomness: a retry
after a crash computes the same id, and a different operation always gets a different one.
A new attempt after a rejected write is a different operation, so `attempt` is part of the key.
"""

import uuid

# Fixed namespace for Nirnaya write requests. Changing it would change every request id.
NAMESPACE = uuid.UUID("6f1c2b8e-3a4d-5e6f-9a0b-1c2d3e4f5a6b")


def request_id(
    *,
    merchant_id: str,
    case_id: str,
    cycle: int,
    stage: str,
    action: str,
    decision_id: str,
    attempt: int = 1,
) -> str:
    parts = [merchant_id, case_id, str(cycle), stage, action, decision_id]
    if attempt != 1:  # attempt 1 keeps the ids already issued before attempts were counted
        parts.append(f"attempt={attempt}")
    key = "|".join(parts)
    return str(uuid.uuid5(NAMESPACE, key))
