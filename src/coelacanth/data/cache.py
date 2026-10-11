"""SQLite store for HTTP responses plus a cost ledger, shared by all data clients (cache/ is gitignored)."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..env import REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "cache" / "http.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER NOT NULL,
    body BLOB NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    cost_usd REAL NOT NULL,
    cached INTEGER NOT NULL,
    status INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_source_at ON ledger (source, at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


class ResponseCache:
    """Responses keyed by source and a secret-free URL; every cache hit and network call goes into the ledger."""

    def __init__(self, path: Path | str = DEFAULT_PATH, now: Callable[[], datetime] = utcnow) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    @staticmethod
    def key(source: str, url: str) -> str:
        return hashlib.sha256(f"{source} {url}".encode("utf-8")).hexdigest()

    def get(self, source: str, url: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM responses WHERE key = ?",
                                   (self.key(source, url),)).fetchone()
        return row[0] if row else None

    def put(self, source: str, url: str, status: int, body: bytes) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?)",
                             (self.key(source, url), source, url, status, body, _stamp(self._now())))

    def record(self, source: str, kind: str, url: str, cost_usd: float, cached: bool, status: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO ledger (at, source, kind, url, cost_usd, cached, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_stamp(self._now()), source, kind, url, cost_usd, int(cached), status))

    def spent(self, source: str, since: datetime) -> float:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE source = ? AND at >= ?",
                                   (source, _stamp(since))).fetchone()
        return float(row[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()
