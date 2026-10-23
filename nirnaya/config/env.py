"""A small .env loader with no dependency. Real environment variables always win."""

import os
from collections.abc import MutableMapping
from pathlib import Path


def load_dotenv(
    path: str | Path = ".env", environ: MutableMapping[str, str] = os.environ
) -> list[str]:
    """Set KEY=VALUE pairs from `path` that are not already set. Returns the keys it set.

    Blank lines and # comments are ignored; values may be wrapped in single or double quotes.
    Empty values are skipped so a blank template line never masks a default.
    """
    file = Path(path)
    if not file.is_file():
        return []
    loaded = []
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not key or not value or key in environ:
            continue
        environ[key] = value
        loaded.append(key)
    return loaded
