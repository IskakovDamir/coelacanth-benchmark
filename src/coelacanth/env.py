"""Settings from the environment, with an optional .env file at the repo root (gitignored, never committed)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_loaded = False


def load_dotenv(path: Path | None = None) -> None:
    """Read KEY=VALUE lines into os.environ without overriding variables that are already set."""
    global _loaded
    env_path = path or REPO_ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
    _loaded = True


def get(name: str, default: str | None = None) -> str | None:
    """Return a setting; an empty value counts as unset."""
    if not _loaded:
        load_dotenv()
    return os.environ.get(name) or default
