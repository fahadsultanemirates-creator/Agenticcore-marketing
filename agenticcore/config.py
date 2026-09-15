"""Loads ``.env`` into the process environment.

Every client in this package reads its credentials straight from
``os.environ``. The README tells you to put them in a ``.env`` file, so
something has to bridge the two — that's this module. It's hand-rolled
rather than ``python-dotenv`` to keep the runtime dependencies to the two
the framework actually needs (``anthropic`` and ``requests``).

Real environment variables always win, so exporting a key in your shell or
setting it in a CI secret still overrides whatever the file says.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def load_env(path: str | Path = ".env", override: bool = False) -> dict[str, str]:
    """Parse ``path`` and merge it into ``os.environ``; returns what it set.

    A missing file is not an error — the framework is expected to run from
    real environment variables alone (CI, containers, a systemd unit), and
    the offline dry-run paths need no credentials at all.
    """

    env_path = Path(path)
    if not env_path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        parsed = _parse_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if not value:
            continue  # a blank placeholder from .env.example is not a setting
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def _parse_line(line: str) -> Optional[tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()

    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]  # quoted values keep any '#' and whitespace inside
    else:
        value = value.partition(" #")[0].strip()
    return key, value
