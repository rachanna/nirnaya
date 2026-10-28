"""The evaluation case set's version: a manifest (eval/case_set.toml) with the number of cases
and a SHA-256 over all of them, so every report states which cases it scored.

    uv run python -m nirnaya.evaluation.case_set bump v2

Any change to the cases (an edit, an addition, a removal) fails check() until the set is given
a new version; a version that already names other cases is never reused.
"""

import sys
import tomllib
from pathlib import Path
from typing import Any

from nirnaya.assets import digest
from nirnaya.evaluation.cases import CASES_DIR

MANIFEST = CASES_DIR.parent / "case_set.toml"


class CaseSetChanged(ValueError):
    """The cases no longer match the manifest's version."""


def current(directory: Path = CASES_DIR) -> dict[str, Any]:
    """Count and hash of the case files: each file's name and the hash of its text (newlines
    normalised), in name order."""
    files = sorted(directory.glob("*.json"))
    listing = "".join(f"{p.name}\n{digest(p.read_text(encoding='utf-8'))}\n" for p in files)
    return {"count": len(files), "sha256": digest(listing)}


def _manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def check(directory: Path = CASES_DIR, manifest: Path = MANIFEST) -> dict[str, Any]:
    """The manifest, verified against the cases; raises if they differ."""
    pinned, now = _manifest(manifest), current(directory)
    if not pinned or (pinned.get("count"), pinned.get("sha256")) != (now["count"], now["sha256"]):
        raise CaseSetChanged(
            f"the eval cases differ from {manifest.name} version {pinned.get('version')!r}; "
            "give the set a new version: python -m nirnaya.evaluation.case_set bump <version>"
        )
    return {"version": pinned["version"], **now}


def bump(version: str, directory: Path = CASES_DIR, manifest: Path = MANIFEST) -> str:
    pinned, now = _manifest(manifest), current(directory)
    if pinned.get("version") == version and pinned.get("sha256") != now["sha256"]:
        raise CaseSetChanged(f"version {version!r} already names a different set of cases")
    manifest.write_text(
        "# The evaluation case set: count and SHA-256 over every case file (name + text hash,\n"
        "# newlines normalised, in name order). Written by: python -m nirnaya.evaluation.case_set\n"
        "# bump <version>. A changed set gets a new version.\n"
        "\n"
        f'version = "{version}"\n'
        f"count = {now['count']}\n"
        f'sha256 = "{now["sha256"]}"\n',
        encoding="utf-8",
        newline="\n",
    )
    return version


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "bump":
        print("usage: python -m nirnaya.evaluation.case_set bump <version>")
        return 2
    try:
        print(f"case set is now version {bump(args[1])}")
    except CaseSetChanged as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
