"""Mirror every locked prompt version to LangSmith's prompt hub, read-only.

Each prompt becomes a private LangSmith prompt `nirnaya-<name>`, and each version one commit,
pushed in version order, tagged with the version and described with name@version#hash. A
version already mirrored (its ref is in a commit description) is skipped, so re-running is
safe. $placeholders are shown as mustache {{placeholders}}; the text is otherwise unchanged.

The repo file stays the source of truth: nothing is ever pulled back.
"""

from collections.abc import Callable
from itertools import groupby
from pathlib import Path
from string import Template
from typing import Any

from nirnaya import assets

DESCRIPTION = (
    "Mirror of nirnaya/assets/prompts/{name}. Read-only: the repo file is the source of "
    "truth; change it there by publishing a new version."
)


def identifier(name: str) -> str:
    return f"nirnaya-{name.replace('_', '-')}"


def to_mustache(text: str) -> str:
    def swap(m: Any) -> str:  # noqa: ANN401 - re.Match
        if m.group("escaped") is not None:
            return "$"
        name = m.group("named") or m.group("braced")
        return "{{" + name + "}}" if name else m.group(0)

    return Template.pattern.sub(swap, text)


def template(asset: assets.Asset) -> Any:  # noqa: ANN401 - a LangChain PromptTemplate
    from langchain_core.prompts import PromptTemplate

    names = sorted(set(Template(asset.text).get_identifiers()))
    return PromptTemplate(
        template=to_mustache(asset.text), input_variables=names, template_format="mustache"
    )


def _order(asset: assets.Asset) -> tuple[str, int, str]:
    digits = asset.version.lstrip("v")
    return asset.name, int(digits) if digits.isdigit() else 0, asset.version


def published(root: Path = assets.ROOT) -> list[assets.Asset]:
    """Every locked prompt file, by name and then version number."""
    return sorted((a for a in assets.published(root) if a.path.startswith("prompts/")), key=_order)


def mirror(
    client: Any,  # noqa: ANN401 - langsmith.Client
    root: Path = assets.ROOT,
    out: Callable[[str], None] = lambda _: None,
) -> list[tuple[str, str]]:
    from langsmith.utils import LangSmithNotFoundError

    results: list[tuple[str, str]] = []
    for name, versions in groupby(published(root), key=lambda a: a.name):
        ident = identifier(name)
        try:
            existing = [c.description or "" for c in client.list_prompt_commits(ident)]
        except LangSmithNotFoundError:
            existing = []
        for asset in versions:
            if any(asset.ref in d for d in existing):
                results.append((asset.ref, "unchanged"))
                continue
            url = client.push_prompt(
                ident,
                object=template(asset),
                is_public=False,
                description=DESCRIPTION.format(name=name),
                tags=["nirnaya", "mirror"],
                commit_tags=[asset.version],
                commit_description=f"{asset.ref}: mirror of nirnaya/assets/{asset.path}",
            )
            results.append((asset.ref, "pushed"))
            out(f"pushed {asset.ref} -> {url}")
    return results
