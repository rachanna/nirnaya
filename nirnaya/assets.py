"""Versioned assets: prompts and configuration, one immutable file per version.

    nirnaya/assets/
      prompts/<name>/<version>.md|.toml   what a model is told
      policies/<merchant>/<version>.toml  merchant policy and priors
      fx/<snapshot>.toml, evaluation/...  other configuration
      active.toml                         which version of each is in use
      assets.lock                         SHA-256 of every published file

A published file never changes: a new text is a new version. Loading checks the file against
the lock and fails closed on a mismatch or an unlocked file, as evidence hashes do. Hashes are
taken over the text with normalised line endings, so a recorded hash is the hash of exactly
what was used, whatever the checkout's line endings.

Prompt versions come from active.toml, overridable for an experiment with
NIRNAYA_PROMPTS=name:version,name:version. Configuration (policy, FX, economics) has no
override: a change there alters decisions, so it goes through a committed pin.

Templates use $placeholders, filled only by code; render() requires exactly the template's
placeholders. Case data is never put into a system prompt.

    uv run python -m nirnaya.assets lock     # add new files to the lock
"""

import hashlib
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

ROOT = Path(__file__).resolve().parent / "assets"
LOCK = "assets.lock"
ACTIVE = "active.toml"
_UNLOCKED = {LOCK, ACTIVE}


class AssetError(ValueError):
    """An asset is missing, malformed or wrongly selected."""


class AssetIntegrityError(AssetError):
    """A published asset is not in the lock or no longer matches it."""


@dataclass(frozen=True)
class Asset:
    path: str  # relative to the asset root, POSIX separators
    text: str
    sha256: str

    @property
    def name(self) -> str:
        return Path(self.path).parent.name

    @property
    def version(self) -> str:
        return Path(self.path).stem

    @property
    def ref(self) -> str:
        """Short provenance: name@version#first 12 hex of the hash."""
        return f"{self.name}@{self.version}#{self.sha256[:12]}"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")  # universal newlines: \r\n reads as \n


def _locked(root: Path) -> dict[str, str]:
    path = root / LOCK
    if not path.is_file():
        return {}
    return dict(tomllib.loads(_read(path)).get("files", {}))


def load(rel: str, *, root: Path = ROOT) -> Asset:
    path = root / rel
    if not path.is_file():
        raise AssetError(f"no file {rel}")
    text = _read(path)
    sha = digest(text)
    expected = _locked(root).get(rel)
    if expected is None:
        raise AssetIntegrityError(f"{rel} is not in the lock; run: python -m nirnaya.assets lock")
    if expected != sha:
        raise AssetIntegrityError(f"{rel} has changed since it was locked")
    return Asset(rel, text, sha)


def _no_floats(value: Any, where: str) -> None:
    if isinstance(value, float):
        raise AssetError(f"{where}: a float is not allowed; write the number as a string")
    if isinstance(value, dict):
        for k, v in value.items():
            _no_floats(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _no_floats(v, f"{where}[{i}]")


def load_toml(rel: str, *, root: Path = ROOT) -> tuple[Asset, dict[str, Any]]:
    asset = load(rel, root=root)
    try:
        data = tomllib.loads(asset.text)
    except tomllib.TOMLDecodeError as e:
        raise AssetError(f"{rel}: {e}") from e
    _no_floats(data, rel)
    return asset, data


def active(root: Path = ROOT) -> dict[str, Any]:
    path = root / ACTIVE
    if not path.is_file():
        raise AssetError(f"no {ACTIVE} under {root}")
    return tomllib.loads(_read(path))


def prompt_versions(env: Mapping[str, str], root: Path = ROOT) -> dict[str, str]:
    """name -> version: the pins in active.toml, with NIRNAYA_PROMPTS applied."""
    versions = dict(active(root).get("prompts", {}))
    for entry in filter(None, (e.strip() for e in env.get("NIRNAYA_PROMPTS", "").split(","))):
        name, _, version = entry.partition(":")
        name, version = name.strip(), version.strip()
        if not name or not version:
            raise AssetError(f"NIRNAYA_PROMPTS entry {entry!r} is not name:version")
        if name not in versions:
            raise AssetError(f"NIRNAYA_PROMPTS names an unknown prompt {name!r}")
        versions[name] = version
    return versions


def _prompt_path(name: str, env: Mapping[str, str], root: Path) -> str:
    version = prompt_versions(env, root).get(name)
    if version is None:
        raise AssetError(f"unknown prompt {name!r}: not pinned in {ACTIVE}")
    found = sorted((root / "prompts" / name).glob(f"{version}.*"))
    if len(found) != 1:
        raise AssetError(f"no file for prompt {name} version {version}")
    return found[0].relative_to(root).as_posix()


def prompt(name: str, env: Mapping[str, str], *, root: Path = ROOT) -> Asset:
    return load(_prompt_path(name, env, root), root=root)


def prompt_table(
    name: str, env: Mapping[str, str], *, root: Path = ROOT
) -> tuple[Asset, dict[str, Any]]:
    """A prompt kept as TOML (for example tool descriptions)."""
    return load_toml(_prompt_path(name, env, root), root=root)


def config(key: str, *, root: Path = ROOT) -> tuple[Asset, dict[str, Any]]:
    rel = active(root).get("config", {}).get(key)
    if rel is None:
        raise AssetError(f"no config pin {key!r} in {ACTIVE}")
    return load_toml(rel, root=root)


def render(asset: Asset, **values: str) -> str:
    """Fill a template's $placeholders. Values are inserted as-is, never re-parsed."""
    template = Template(asset.text)
    if not template.is_valid():
        raise AssetError(f"{asset.path}: invalid placeholder")
    wanted = set(template.get_identifiers())
    missing, unexpected = wanted - values.keys(), values.keys() - wanted
    if missing:
        raise AssetError(f"{asset.path}: missing values for {sorted(missing)}")
    if unexpected:
        raise AssetError(f"{asset.path}: unexpected values {sorted(unexpected)}")
    return template.substitute(values)


def render_from(asset: Asset, pool: Mapping[str, str]) -> str:
    """Fill a template from a pool of values that serves every version of a prompt: each
    version takes the values it names. A value the template needs and the pool lacks fails."""
    wanted = set(Template(asset.text).get_identifiers())
    return render(asset, **{k: v for k, v in pool.items() if k in wanted})


def published(root: Path = ROOT) -> list[Asset]:
    """Every locked asset, verified, in path order."""
    return [load(rel, root=root) for rel in sorted(_locked(root))]


def _published(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): digest(_read(p))
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and p.name not in _UNLOCKED
        and not any(part.startswith((".", "__")) for part in p.relative_to(root).parts)
    }


def lock(root: Path = ROOT, *, write: bool = True) -> list[str]:
    """Add every new file to the lock and return them. An existing entry is never rewritten:
    a changed file raises, because a published version must not change."""
    locked = _locked(root)
    files = _published(root)
    changed = sorted(rel for rel, sha in files.items() if rel in locked and locked[rel] != sha)
    if changed:
        raise AssetIntegrityError(
            f"published files changed: {changed}; restore them and publish a new version instead"
        )
    new = sorted(rel for rel in files if rel not in locked)
    if new and write:
        merged = {**locked, **{rel: files[rel] for rel in new}}
        lines = [
            "# SHA-256 of every published asset (text, normalised line endings).",
            "# Written by: python -m nirnaya.assets lock. Never edit an entry: a changed",
            "# text is a new version file.",
            "",
            "[files]",
            *(f'"{rel}" = "{merged[rel]}"' for rel in sorted(merged)),
            "",
        ]
        (root / LOCK).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return new


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["lock"]:
        print("usage: python -m nirnaya.assets lock")
        return 2
    try:
        new = lock()
    except AssetIntegrityError as e:
        print(e)
        return 1
    print("\n".join(f"locked {rel}" for rel in new) or "nothing new to lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
