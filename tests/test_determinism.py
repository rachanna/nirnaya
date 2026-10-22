"""The deterministic core has no I/O, no clock, no randomness and no LLM, and its output
does not depend on hash seeding."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ["models", "evidence", "engine", "verifier"]
FORBIDDEN_MODULES = {
    "anthropic",
    "asyncio",
    "http",
    "httpx",
    "os",
    "random",
    "requests",
    "secrets",
    "socket",
    "subprocess",
    "threading",
    "time",
    "urllib",
    "uuid",
}
FORBIDDEN_CALLS = {"now", "today", "utcnow", "time", "time_ns", "urandom", "getenv"}


def core_files() -> list[Path]:
    return sorted(p for pkg in CORE for p in (ROOT / "nirnaya" / pkg).rglob("*.py"))


@pytest.mark.parametrize("path", core_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_core_module_has_no_forbidden_imports_or_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            assert name.split(".")[0] not in FORBIDDEN_MODULES, f"{path}: imports {name}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_CALLS, f"{path}: calls .{node.func.attr}()"


def test_scan_covers_every_core_package():
    assert {p.parent.name for p in core_files()} >= set(CORE)


SCRIPT = """
from tests.factories import carrier_record, make_claim, make_dispute
from nirnaya.config.demo import DEMO_POLICY
from nirnaya.engine import decide
from nirnaya.evidence.derive import contradictory_record_ids, evidence_present
from nirnaya.models import EvidenceType, ReasonCategory, Stage
from nirnaya.verifier import verify

records = [carrier_record(), carrier_record("carrier-2", status="LOST")]
outputs = []
for reason in ReasonCategory:
    for stage in (Stage.PRE_CHARGEBACK, Stage.CHARGEBACK):
        d = make_dispute(reason_category=reason, stage=stage)
        outputs.append(decide(d, DEMO_POLICY, frozenset(EvidenceType), ownership_resolved=True)
                       .model_dump_json())
        outputs.append(decide(d, DEMO_POLICY, evidence_present(records, d),
                              ownership_resolved=True,
                              contradictory_record_ids=contradictory_record_ids(records, d))
                       .model_dump_json())
        outputs.append(verify(d, [make_claim(), make_claim(id="x", text="bad")], records)
                       .model_dump_json())
print("\\n".join(outputs))
"""


def run_with_seed(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_output_is_identical_across_hash_seeds():
    # frozenset iteration order changes with the seed; serialised output must not.
    baseline = run_with_seed("0")
    assert baseline.strip()
    for seed in ("1", "42", "31337"):
        assert run_with_seed(seed) == baseline
