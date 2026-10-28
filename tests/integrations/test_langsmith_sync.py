"""LangSmith sync, offline: the prompt mirror and the experiment upload, with a fake client."""

import json

import pytest

from nirnaya import assets
from nirnaya.integrations.langsmith import experiments, prompts
from nirnaya.integrations.langsmith.client import client_settings


class FakeClient:
    def __init__(self, commits=None, datasets=None):
        self.commits = commits or {}  # identifier -> [description]
        self.datasets = datasets or {}  # name -> metadata
        self.pushed, self.examples = [], []

    def list_prompt_commits(self, identifier, **_):
        from langsmith.utils import LangSmithNotFoundError

        if identifier not in self.commits:
            raise LangSmithNotFoundError("no prompt")
        from types import SimpleNamespace

        return iter(SimpleNamespace(description=d) for d in self.commits[identifier])

    def push_prompt(self, identifier, **kwargs):
        self.pushed.append((identifier, kwargs))
        self.commits.setdefault(identifier, []).append(kwargs["commit_description"])
        return f"https://smith.example/prompts/{identifier}"

    def has_dataset(self, *, dataset_name):
        return dataset_name in self.datasets

    def read_dataset(self, *, dataset_name):
        from types import SimpleNamespace

        return SimpleNamespace(metadata=self.datasets[dataset_name])

    def create_dataset(self, name, **kwargs):
        self.datasets[name] = kwargs["metadata"]

    def create_examples(self, *, dataset_name, examples):
        self.examples.extend(examples)


def test_settings_need_a_key_but_not_tracing():
    assert client_settings({}) is None
    s = client_settings({"LANGSMITH_API_KEY": "k", "LANGSMITH_ENDPOINT": "https://x"})
    assert (s.api_key, s.endpoint) == ("k", "https://x")


# --- prompt mirror ------------------------------------------------------------------------------


def test_placeholders_become_mustache_and_dollars_unescape():
    assert prompts.to_mustache("Hi $name, ${x} costs $$5") == "Hi {{name}}, {{x}} costs $5"


def test_every_locked_prompt_version_is_mirrored_once_in_version_order():
    client = FakeClient()
    first = prompts.mirror(client)
    shipped = [a for a in prompts.published() if a.path.startswith("prompts/")]
    assert [ref for ref, status in first] == [a.ref for a in shipped]
    assert all(status == "pushed" for _, status in first)
    ident, kw = client.pushed[0]
    assert ident.startswith("nirnaya-") and "_" not in ident
    assert kw["is_public"] is False and kw["commit_tags"] == [shipped[0].version]
    assert shipped[0].ref in kw["commit_description"]
    informed = [
        k["commit_tags"] for i, k in client.pushed if i == "nirnaya-baseline-informed-system"
    ]
    assert informed == [["v1"], ["v2"], ["v3"]]  # history in version order

    again = prompts.mirror(client)  # nothing new: nothing pushed
    assert all(status == "unchanged" for _, status in again)
    assert len(client.pushed) == len(shipped)


def test_a_mirrored_template_renders_like_the_repo_file():
    a = assets.prompt("drafter_user", {})
    t = prompts.template(a)
    values = {"facts": "F", "records": "R", "field_guide": "G"}
    assert t.format(**values) == assets.render(a, **values)


# --- experiments ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report():
    from nirnaya.evaluation.report import build, load_caches

    return build(caches=load_caches())


def test_the_dataset_is_the_case_set_version_with_expected_routes(report):
    client = FakeClient()
    name = experiments.ensure_dataset(client, report)
    assert name == f"nirnaya-eval-cases-{report['versions']['case_set']['version']}"
    assert len(client.examples) == report["cases"]
    k1 = next(e for e in client.examples if e["inputs"]["case_id"] == "K1")
    assert k1["reference_outputs"] == {"expected": "CHALLENGE"}
    assert experiments.ensure_dataset(client, report) == name  # reused
    assert len(client.examples) == report["cases"]


def test_a_dataset_for_different_cases_under_the_same_name_is_refused(report):
    name = f"nirnaya-eval-cases-{report['versions']['case_set']['version']}"
    client = FakeClient(datasets={name: {"sha256": "something-else"}})
    with pytest.raises(experiments.SyncError, match="different cases"):
        experiments.ensure_dataset(client, report)


def test_each_policy_becomes_one_experiment_and_is_not_uploaded_twice(report, tmp_path):
    calls = []

    def fake_evaluate(target, **kwargs):
        calls.append((target, kwargs))

    record = tmp_path / "langsmith.json"
    client = FakeClient()
    done = experiments.upload(
        client, report, record=record, evaluate=fake_evaluate, out=lambda _: None
    )
    assert [k for k in done] == list(report["policies"])
    target, kw = calls[0]  # nirnaya
    assert kw["experiment_prefix"] == "nirnaya" and kw["data"].startswith("nirnaya-eval-cases-")
    assert target({"case_id": "K1"})["action"] == "CHALLENGE"  # stored results, no recompute
    assert kw["metadata"]["case_set"] == report["versions"]["case_set"]["version"]

    agrees, unsupported, cost = kw["evaluators"]
    assert agrees({"action": "CHALLENGE"}, {"expected": "CHALLENGE"})["score"] == 1
    assert agrees({"action": "NONE"}, {"expected": "DEFER"})["score"] == 1
    assert unsupported({"unsupported": 2}, {})["score"] == 2
    assert cost({"cost": "150.00"}, {})["score"] == 150.0
    (summary,) = kw["summary_evaluators"]
    keys = {r["key"]: r["score"] for r in summary([], [])["results"]}
    assert keys["expected_net_loss_usd"] == float(
        report["policies"]["nirnaya"]["financial"]["expected_loss"]
    )

    llm = next(kw for _, kw in calls if kw["experiment_prefix"].startswith("llm_only_"))
    assert llm["metadata"]["model"] and llm["metadata"]["prompts"]

    n = len(calls)
    experiments.upload(client, report, record=record, evaluate=fake_evaluate, out=lambda _: None)
    assert len(calls) == n  # same results: skipped
    assert json.loads(record.read_text(encoding="utf-8"))["nirnaya"]["fingerprint"]
