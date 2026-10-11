"""Candidate mining: sources of possible benchmark items, written to data/candidates/*.jsonl.

Only metadata we may redistribute goes into those files (identifiers, titles, dates, fields, and CC-BY catalogue
text with attribution); abstracts and full texts stay in the gitignored cache.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable, Mapping

from ..data.cache import ResponseCache
from ..data.openalex import USER_AGENT, urllib_fetch
from ..env import REPO_ROOT

CANDIDATES_DIR = REPO_ROOT / "data" / "candidates"


def write_jsonl(path: Path, rows: Iterable[Mapping]) -> int:
    """Write rows as JSON lines (UTF-8, keys in insertion order); returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def fetch_free(url: str, source: str, cache: ResponseCache, *, min_interval: float = 0.4,
               headers: Mapping[str, str] | None = None) -> bytes:
    """GET a free, keyless resource (NCBI ID converter, GitHub raw files) through the response cache.

    Cache hits are booked at $0 like network calls; nothing identifying the user is sent.
    """
    cached = cache.get(source, url)
    if cached is not None:
        cache.record(source, "free", url, 0.0, cached=True, status=200)
        return cached
    time.sleep(min_interval)
    status, _, body = urllib_fetch(url, {"User-Agent": USER_AGENT, **(headers or {})}, 30.0)
    cache.record(source, "free", url, 0.0, cached=False, status=status)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    cache.put(source, url, status, body)
    return body
