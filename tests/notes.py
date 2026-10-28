"""A case whose deciding fact is only in a carrier driver's free-text note (architecture 8.8).

Derived from N1: a not-received chargeback, the carrier record DELIVERED with no structured
signature. At 120 USD, p = 0.60 gives 72 - 80 = -8 (accept); with the signature, p = 0.80 gives
96 - 80 = +16 (challenge, outside the marginal band).
"""

import json
from pathlib import Path

from nirnaya.evaluation.cases import EvalCase

ROOT = Path(__file__).resolve().parents[1]
NOTE = "Handed to the customer in person at the door; signature captured on the handheld device."
QUOTE = "signature captured on the handheld device"


def noted_case(
    *, case_id: str = "NOTED", amount: str = "120.00", driver_notes: str = NOTE
) -> EvalCase:
    src = json.loads((ROOT / "eval" / "cases" / "N1.json").read_text(encoding="utf-8"))
    src["id"] = src["dispute"]["id"] = case_id
    src["tags"] = []
    src["dispute"]["amount"]["amount"] = amount
    for r in src["world"]:
        r["id"] = r["id"].replace("N1", case_id)
        if r["system"] == "CARRIER":
            r["fields"]["driver_notes"] = driver_notes
        if r["system"] == "ORDER":
            r["fields"]["total_amount"] = amount
    for c in src["claims"]:
        c["source_record_id"] = c["source_record_id"].replace("N1", case_id)
    src["expected"] = {
        "route": "REFER",
        "engine": None,
        "verification": None,
        "rationale": "test fixture: the deciding fact is only in a note",
    }
    return EvalCase.model_validate(src)
