"""The check to run before presenting: everything the demo needs, and nothing it can break."""

import json

import pytest

from nirnaya.llm.fake import reply
from nirnaya.llm.types import LLMError
from nirnaya.preflight import FAIL, OK, WARN, main, run_checks

KEY = {"NIRNAYA_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"}


class Fine:
    provider = "fake"
    model = "fake"

    def complete(self, **_):
        return reply("OK")


class Down:
    provider = "fake"
    model = "fake"

    def complete(self, **_):
        raise LLMError("call failed: AuthenticationError")


@pytest.fixture
def paths(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    report = tmp_path / "report.json"
    financial = {"agreement": 42, "cases": 42}
    report.write_text(json.dumps({"policies": {"nirnaya": {"financial": financial}}}), "utf-8")
    return {"web_dist": dist, "results": report, "sandbox_dir": tmp_path / "none"}


def check(results, name):
    return next(c for c in results if c.name == name)


def run(paths, env=None, **kw):
    return run_checks(
        {**KEY, **(env or {})}, port_open=kw.pop("port_open", lambda _: False), **paths, **kw
    )


def test_a_ready_machine_has_no_failures(paths):
    results = run(paths, llm=Fine())
    assert [c.name for c in results if c.status == FAIL] == []
    assert check(results, "Kit cases").status == OK
    assert check(results, "Replayed note and redraft").status == OK
    assert check(results, "Recording").status == OK


def test_a_page_that_was_not_built_says_how_to_build_it(paths):
    (paths["web_dist"] / "index.html").unlink()
    found = check(run(paths, llm=Fine()), "Web page")
    assert found.status == FAIL and "npm run build" in found.detail


def test_missing_or_changed_results_fail(paths):
    paths["results"].write_text(
        json.dumps({"policies": {"nirnaya": {"financial": {"agreement": 41, "cases": 42}}}}),
        "utf-8",
    )
    found = check(run(paths, llm=Fine()), "Results")
    assert found.status == FAIL and "41/42" in found.detail
    paths["results"].unlink()
    assert check(run(paths, llm=Fine()), "Results").status == FAIL


def test_real_writes_switched_on_fail(paths):
    found = check(run(paths, {"DRY_RUN": "false"}, llm=Fine()), "Dry run")
    assert found.status == FAIL
    assert check(run(paths, llm=Fine()), "Dry run").status == OK


def test_no_model_key_only_warns_because_the_console_needs_none(paths):
    results = run_checks({}, port_open=lambda _: False, **paths)
    assert check(results, "Model key").status == WARN


def test_the_live_check_makes_one_call_and_fails_on_a_bad_key(paths):
    assert check(run(paths, live=True, llm=Fine()), "Model key").status == OK
    found = check(run(paths, live=True, llm=Down()), "Model key")
    assert found.status == FAIL and "AuthenticationError" in found.detail


def test_asking_for_the_live_check_with_no_key_fails(paths):
    results = run_checks({}, live=True, port_open=lambda _: False, **paths)
    assert check(results, "Model key").status == FAIL


def test_a_port_in_use_warns(paths):
    results = run(paths, llm=Fine(), port_open=lambda port: port == 8501)
    assert check(results, "Port 8501").status == WARN
    assert check(results, "Port 8000").status == OK


def test_no_sandbox_run_only_warns(paths):
    assert check(run(paths, llm=Fine()), "Sandbox run").status == WARN


def test_a_check_that_crashes_is_reported_not_raised(paths, monkeypatch):
    from nirnaya import preflight

    def boom(*_a, **_k):
        raise RuntimeError("exploded")

    monkeypatch.setattr(preflight.DemoSession, "offline", boom)
    found = check(run(paths, llm=Fine()), "Kit cases")
    assert found.status == FAIL and "exploded" in found.detail


def test_the_exit_code_follows_the_failures(paths):
    lines: list[str] = []
    args = dict(env=KEY, out=lines.append, port_open=lambda _: False, llm=Fine(), **paths)
    assert main([], **args) == 0
    assert any("Ready" in line for line in lines)

    (paths["web_dist"] / "index.html").unlink()
    lines.clear()
    assert main([], **args) == 1
    assert any("Not ready" in line for line in lines)
