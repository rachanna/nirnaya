"""I7, the mechanisable half: every Airwallex endpoint the code calls is cited in the
architecture's endpoint table (docs/architecture.md section 9.2), which names its source.

Enum values (stages, statuses, reasons) are still checked by review, against the same table.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A path, including {placeholder} or f-string {expression} segments.
PATH = re.compile(r"/api/v1/(?:[A-Za-z0-9_/]|\{[^}]*\})+")


def endpoints(text: str) -> set[str]:
    return {re.sub(r"\{[^}]*\}", "{}", m.group(0)).rstrip("/") for m in PATH.finditer(text)}


def test_every_endpoint_in_the_airwallex_client_is_cited_in_the_architecture():
    code = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "nirnaya" / "integrations" / "airwallex").glob("*.py"))
    )
    doc = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    used = endpoints(code)
    assert used, "no endpoints found: the pattern is broken"
    missing = sorted(used - endpoints(doc))
    assert not missing, f"cite these in architecture section 9.2 with their source: {missing}"


def test_the_pattern_normalises_placeholders_and_expressions():
    assert endpoints("f\"/api/v1/pa/payment_intents/{quote(x, safe='')}/confirm\"") == {
        "/api/v1/pa/payment_intents/{}/confirm"
    }
    assert endpoints("`POST /api/v1/pa/payment_disputes/{dispute_id}/accept`") == {
        "/api/v1/pa/payment_disputes/{}/accept"
    }
