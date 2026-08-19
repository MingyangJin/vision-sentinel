"""Minimal .env loading.

Camera credentials live in .env (gitignored), never in source or argv.
"""

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    """Read KEY=VALUE lines into os.environ without overriding existing vars."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
