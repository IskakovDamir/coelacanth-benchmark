"""Full-text phrase mining through Semantic Scholar snippet search (Damir, 2026-09-24): sentences in which a paper
says that a result was previously derived, is equivalent to a well-known one, was first proposed elsewhere, was
rediscovered, or is known in another field under another name.

Snippet search ranks by relevance and has no phrase syntax, so every phrase is sent as plain text and the returned
snippets are kept only when a regular expression finds the phrase in them. To get past the 1000-result limit, each
query runs per year slice, and a slice that fills the limit is split by field of study. Snippet texts, scores and
corpus IDs stay in the gitignored cache (the S2 license forbids repackaging the API); the hits are resolved to DOIs
with the paper batch endpoint and then to OpenAlex, and only OpenAlex metadata plus the matched phrase names are
written to data/candidates/s2-snippet-hits.jsonl.

Needs S2_API_KEY in .env; without it the script prints the plan and stops.

    PYTHONPATH=src python -m coelacanth.mining.s2_snippets --plan
    PYTHONPATH=src python -m coelacanth.mining.s2_snippets
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict

from ..data.openalex import OpenAlexClient, OpenAlexError
from ..data.semanticscholar import SNIPPET_LIMIT_MAX, MissingKey, S2Client
from ..env import get
from . import CANDIDATES_DIR, write_jsonl

PHRASES = {
    "previously_derived_by": {
        "queries": ["was previously derived by"],
        "pattern": r"\bwas previously derived by\b"},
    "equivalent_to_the_well_known": {
        "queries": ["is equivalent to the well-known"],
        "pattern": r"\bis equivalent to the well[- ]known\b"},
    "first_proposed_in": {
        "queries": ["was first proposed in"],
        "pattern": r"\bwas first proposed in\b"},
    "rediscovered": {
        "queries": ["rediscovered", "was independently rediscovered", "has been rediscovered"],
        "pattern": r"\brediscovered\b"},
    "known_in_field_as": {
        "queries": ["is known in the literature as", "is known in statistics as", "is known in physics as",
                    "is known in economics as", "is known in biology as", "is known in engineering as",
                    "is known in ecology as", "is known in chemistry as", "is known in epidemiology as",
                    "is known in the machine learning literature as"],
        "pattern": r"\bis known in (?:the )?[A-Za-z][\w,-]*(?: [\w,-]+){0,5} as\b"},
}
YEAR_SLICES = ["-1969", "1970-1979", "1980-1989", "1990-1999", "2000-2004", "2005-2009", "2010-2014", "2015-2017",
               "2018-2019", "2020-2021", "2022-2023", "2024-2026"]
FIELDS_OF_STUDY = ["Computer Science", "Medicine", "Chemistry", "Biology", "Materials Science", "Physics", "Geology",
                   "Psychology", "Art", "History", "Geography", "Sociology", "Business", "Political Science",
                   "Economics", "Philosophy", "Mathematics", "Engineering", "Environmental Science",
                   "Agricultural and Food Sciences", "Education", "Law", "Linguistics"]
SNIPPET_FIELDS = ["snippet.text", "snippet.snippetKind", "snippet.section"]


def plan() -> list[tuple[str, str, str]]:
    """(phrase name, query, year slice) for the first pass; full slices are split by field of study at run time."""
    return [(name, query, years) for name, spec in PHRASES.items() for query in spec["queries"] for years in YEAR_SLICES]


def matching(data: dict, pattern: str) -> list[dict]:
    """Snippet matches whose text contains the phrase pattern (case-insensitive)."""
    rx = re.compile(pattern, re.IGNORECASE)
    return [m for m in data.get("data") or [] if rx.search(((m.get("snippet") or {}).get("text") or ""))]


def search_slice(client: S2Client, query: str, years: str) -> list[dict]:
    """All snippets for one query and year slice; a slice that fills the limit is split by field of study."""
    data = client.snippet_search(query, year=years, fields=SNIPPET_FIELDS)
    results = list(data.get("data") or [])
    if len(results) >= SNIPPET_LIMIT_MAX:
        results = []
        for field in FIELDS_OF_STUDY:
            results += client.snippet_search(query, year=years, fields_of_study=field,
                                             fields=SNIPPET_FIELDS).get("data") or []
    return results


def mine(client: S2Client, openalex: OpenAlexClient) -> tuple[list[dict], dict]:
    hits: dict[str, dict] = {}
    stats = defaultdict(lambda: {"snippets": 0, "matching": 0})
    for name, query, years in plan():
        found = search_slice(client, query, years)
        kept = matching({"data": found}, PHRASES[name]["pattern"])
        stats[name]["snippets"] += len(found)
        stats[name]["matching"] += len(kept)
        for m in kept:
            corpus_id = str((m.get("paper") or {}).get("corpusId") or "")
            if not corpus_id:
                continue
            hit = hits.setdefault(corpus_id, {"phrases": set(), "n_snippets": 0, "kinds": set()})
            hit["phrases"].add(name)
            hit["n_snippets"] += 1
            hit["kinds"].add((m.get("snippet") or {}).get("snippetKind"))
    ids = sorted(hits)
    papers = client.paper_batch([f"CorpusId:{i}" for i in ids], fields=["externalIds", "year"])
    rows = []
    for corpus_id, paper in zip(ids, papers):
        doi = ((paper or {}).get("externalIds") or {}).get("DOI")
        row = {"source": "s2_snippets", "phrases": sorted(hits[corpus_id]["phrases"]),
               "n_snippets": hits[corpus_id]["n_snippets"],
               "snippet_kinds": sorted(k for k in hits[corpus_id]["kinds"] if k), "doi": doi, "openalex": None,
               "title": None, "publication_date": None, "field": None}
        if doi:
            try:
                work = openalex.work("doi:" + doi, select=["id", "title", "publication_date", "primary_topic"])
                row.update(openalex=(work.get("id") or "").rsplit("/", 1)[-1], title=work.get("title"),
                           publication_date=work.get("publication_date"),
                           field=(((work.get("primary_topic") or {}).get("field")) or {}).get("display_name"))
            except (OpenAlexError, ValueError):
                pass
        rows.append(row)
    return rows, dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.mining.s2_snippets")
    parser.add_argument("--plan", action="store_true", help="print the planned queries and stop")
    args = parser.parse_args(argv)
    steps = plan()
    if args.plan or not get("S2_API_KEY"):
        print(f"{len(steps)} first-pass requests ({len(PHRASES)} phrases, {sum(len(s['queries']) for s in PHRASES.values())} "
              f"queries, {len(YEAR_SLICES)} year slices); a slice that fills the {SNIPPET_LIMIT_MAX}-result limit "
              f"adds {len(FIELDS_OF_STUDY)} requests; at 1 request per second the first pass takes about "
              f"{len(steps) / 60:.0f} minutes")
        if not get("S2_API_KEY"):
            print("S2_API_KEY is not set: not running (no keyless runs)")
        return 0
    try:
        client = S2Client()
    except MissingKey as err:
        raise SystemExit(str(err))
    rows, stats = mine(client, OpenAlexClient())
    out = CANDIDATES_DIR / "s2-snippet-hits.jsonl"
    write_jsonl(out, rows)
    print(json.dumps({"papers": len(rows), "resolved_to_openalex": sum(bool(r["openalex"]) for r in rows),
                      "per_phrase": stats}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
