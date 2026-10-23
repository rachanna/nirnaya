"""Architecture rules (docs/architecture.md section 3.3), checked by AST scan."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRITE_CALLS = {"accept", "challenge", "upload_file"}
# The gateway is the only caller; the fake implements the calls rather than calling them.
ALLOWED = {
    ROOT / "nirnaya" / "guardrails" / "gateway.py",
    ROOT / "nirnaya" / "integrations" / "mocks" / "airwallex_fake.py",
}


def source_files() -> list[Path]:
    return sorted((ROOT / "nirnaya").rglob("*.py"))


@pytest.mark.parametrize("path", source_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_only_the_write_gateway_calls_airwallex_writes(path):
    if path in ALLOWED:
        return
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in WRITE_CALLS, f"{path}: calls .{node.func.attr}()"


def test_the_ui_has_no_business_logic_imports():
    app = ROOT / "app"
    if not app.exists():
        pytest.skip("app/ arrives in Milestone 7")
    for path in app.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "nirnaya.engine" not in text and "nirnaya.verifier" not in text, path
