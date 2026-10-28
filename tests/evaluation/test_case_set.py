"""The case-set manifest: every report says which version of the eval cases it scored."""

import shutil

import pytest

from nirnaya.evaluation import case_set
from nirnaya.evaluation.cases import CASES_DIR


def test_the_manifest_matches_the_shipped_cases():
    info = case_set.check()
    assert info["count"] == len(list(CASES_DIR.glob("*.json")))
    assert info["version"] and len(info["sha256"]) == 64


@pytest.fixture
def copy(tmp_path):
    cases = tmp_path / "cases"
    shutil.copytree(CASES_DIR, cases)
    manifest = tmp_path / "case_set.toml"
    assert case_set.bump("v1", cases, manifest) == "v1"
    return cases, manifest


def test_an_edited_case_fails_until_the_set_gets_a_new_version(copy):
    cases, manifest = copy
    path = cases / "N1.json"
    path.write_text(path.read_text(encoding="utf-8").replace("N1", "N1 "), encoding="utf-8")
    with pytest.raises(case_set.CaseSetChanged, match="new version"):
        case_set.check(cases, manifest)
    with pytest.raises(case_set.CaseSetChanged, match="already names"):
        case_set.bump("v1", cases, manifest)  # same version, different cases: refused
    assert case_set.bump("v2", cases, manifest) == "v2"
    assert case_set.check(cases, manifest)["version"] == "v2"


def test_an_added_case_changes_the_set(copy):
    cases, manifest = copy
    shutil.copy(cases / "N1.json", cases / "N1-copy.json")
    with pytest.raises(case_set.CaseSetChanged):
        case_set.check(cases, manifest)


def test_line_endings_do_not_change_the_set(copy):
    cases, manifest = copy
    path = cases / "N1.json"
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert case_set.check(cases, manifest)["version"] == "v1"
