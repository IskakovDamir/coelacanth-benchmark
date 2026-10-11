"""Verbatim quote check: the standard every quote in the benchmark and in our documents must pass.

A quote matches a cached text at one of two levels:
- "strict": after Unicode NFKC, quote and dash unification, removal of soft hyphens and zero-width characters,
  and whitespace collapsing;
- "hyphen": after additionally removing all whitespace and hyphens on both sides, which absorbs line-break
  hyphenation from PDF extraction ("domain-" + newline + "stripped").

    PYTHONPATH=src python -m coelacanth.quotes research/quotes/*.json

Registry files are JSON lists of {"source": "<stem in cache/fulltext>", "quote": "<text>"}.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Callable

from .env import REPO_ROOT

FULLTEXT_DIR = REPO_ROOT / "cache" / "fulltext"

# Code points as integers so this file stays plain ASCII.
_SINGLE_QUOTES = (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)
_DOUBLE_QUOTES = (0x201C, 0x201D, 0x201E, 0x2033)
_DASHES = (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)
_DROPPED = (0x00AD, 0x200B, 0xFEFF)
_TRANSLATE = {**{c: "'" for c in _SINGLE_QUOTES}, **{c: '"' for c in _DOUBLE_QUOTES},
              **{c: "-" for c in _DASHES}, **{c: None for c in _DROPPED}}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", text).strip()


def squash(text: str) -> str:
    return re.sub(r"[\s\-]+", "", normalize(text))


def match_level(quote: str, text: str) -> str | None:
    """'strict', 'hyphen', or None when the quote does not occur in the text."""
    if normalize(quote) in normalize(text):
        return "strict"
    if squash(quote) in squash(text):
        return "hyphen"
    return None


def read_cached(key: str, directory: Path = FULLTEXT_DIR) -> str | None:
    path = directory / f"{key}.txt"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None


class QuoteChecker:
    """Check many quotes against cached texts, normalizing each text once."""

    def __init__(self, texts: Callable[[str], str | None] = read_cached) -> None:
        self._texts = texts
        self._normalized: dict[str, tuple[str, str] | None] = {}

    def level(self, quote: str, key: str) -> str | None:
        if key not in self._normalized:
            text = self._texts(key)
            self._normalized[key] = None if text is None else (normalize(text), squash(text))
        pair = self._normalized[key]
        if pair is None:
            raise FileNotFoundError(f"no cached text for {key!r}")
        if normalize(quote) in pair[0]:
            return "strict"
        if squash(quote) in pair[1]:
            return "hyphen"
        return None


def check_registries(paths: list[str]) -> int:
    checker = QuoteChecker()
    total = failed = 0
    for path in paths:
        for entry in json.loads(Path(path).read_text(encoding="utf-8")):
            total += 1
            try:
                level = checker.level(entry["quote"], entry["source"])
            except FileNotFoundError:
                level = None
            if level is None:
                failed += 1
                print(f"FAIL {Path(path).name} [{entry['source']}] {entry['quote'][:120]}")
    print(f"{total} quotes checked, {total - failed} pass, {failed} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(check_registries(sys.argv[1:]))
