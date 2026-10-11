"""Gradient Coupling Inquiry catalogue (L. Holden, github.com/deciduus/same-object, CC-BY-4.0) as candidates.

Parses the 18-instance table in vault/computed/C21-rediscovery-clustering.md (kinds: R = independent
rediscovery, B = cross-domain bridge, B(neg) = failed unification), follows each instance's vault notes, extracts the
DOIs they cite and resolves those in OpenAlex (free lookups). Everything is read at one pinned commit. Writes
    data/candidates/gci-catalogue.jsonl   one row per instance, with the catalogue's own text (CC-BY-4.0)

    PYTHONPATH=src python -m coelacanth.mining.gci
"""

from __future__ import annotations

import json
import re

from ..data.cache import ResponseCache
from ..data.openalex import OpenAlexClient, OpenAlexError
from . import CANDIDATES_DIR, fetch_free, write_jsonl

REPO = "deciduus/same-object"
TABLE_NOTE = "vault/computed/C21-rediscovery-clustering.md"
ATTRIBUTION = "Holden, Gradient Coupling Inquiry, github.com/deciduus/same-object, doi:10.5281/zenodo.22334047, CC-BY-4.0"
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\])|,;\"'`*<>?#]+)")
DOI_TAIL_RE = re.compile(r"(/citations|/full|/abstract|\.full\.pdf|\.full|\.pdf|v\d+(\.full)?)+$", re.IGNORECASE)


def clean_doi(raw_doi: str) -> str:
    """Strip link suffixes that notes append to DOIs ('/citations', 'v1.full', '.full.pdf') and trailing dots."""
    return DOI_TAIL_RE.sub("", raw_doi.rstrip(".")).rstrip(".").lower()
SELECT = ["id", "doi", "title", "publication_date", "primary_topic", "authorships"]


def head_commit(cache: ResponseCache) -> str:
    """The commit read first; cached, so later runs read the same commit."""
    body = fetch_free(f"https://api.github.com/repos/{REPO}/commits/HEAD", "github", cache,
                      headers={"Accept": "application/vnd.github+json"})
    return json.loads(body)["sha"]


def raw(path: str, sha: str, cache: ResponseCache) -> str:
    return fetch_free(f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}", "github", cache).decode("utf-8")


def tree(sha: str, cache: ResponseCache) -> list[str]:
    body = fetch_free(f"https://api.github.com/repos/{REPO}/git/trees/{sha}?recursive=1", "github", cache,
                      headers={"Accept": "application/vnd.github+json"})
    return [t["path"] for t in json.loads(body)["tree"] if t["type"] == "blob" and t["path"].endswith(".md")]


def parse_instances(markdown: str) -> list[dict]:
    """Rows of the '| # | Kind | Instance | Vault note(s) | Primary object-type | Dual |' table."""
    rows = []
    for line in markdown.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0].isdigit():
            rows.append({"number": int(cells[0]), "kind": cells[1], "instance": cells[2], "vault_notes": cells[3],
                         "object_type": cells[4], "dual": None if cells[5] in ("", "—", "-") else cells[5]})
    return rows


def note_paths(ref_cell: str, paths: list[str]) -> list[str]:
    """Resolve 'C5, G28, Q2' or '[[kedem-caplan]], G1' to vault files by stem or by 'C5-' style prefix."""
    stems = {p.rsplit("/", 1)[-1][:-3]: p for p in paths if p.startswith("vault/")}
    found = []
    for token in re.findall(r"\[\[([^\]]+)\]\]|([A-Za-z][\w\-]*)", ref_cell):
        name = (token[0] or token[1]).strip()
        if name in stems:
            found.append(stems[name])
        elif re.fullmatch(r"[A-Z]\d+", name):
            found += [p for s, p in stems.items() if s.startswith(name + "-")]
    return sorted(set(found))


def mine(client: OpenAlexClient) -> tuple[str, list[dict]]:
    cache = client.cache
    sha = head_commit(cache)
    paths = tree(sha, cache)
    rows = parse_instances(raw(TABLE_NOTE, sha, cache))
    for row in rows:
        row["note_paths"] = note_paths(row["vault_notes"], paths)
        dois = set()
        for path in row["note_paths"]:
            dois |= {clean_doi(d) for d in DOI_RE.findall(raw(path, sha, cache))}
        dois.discard("10.5281/zenodo.22334047")  # the catalogue's own DOI
        works = []
        for doi in sorted(dois):
            try:
                work = client.work("doi:" + doi, select=SELECT)
            except (OpenAlexError, ValueError):
                works.append({"doi": doi, "resolved": False})
                continue
            topic = work.get("primary_topic") or {}
            authors = work.get("authorships") or []
            works.append({"doi": doi, "resolved": True, "openalex": (work.get("id") or "").rsplit("/", 1)[-1],
                          "title": work.get("title"), "publication_date": work.get("publication_date"),
                          "first_author": (authors[0].get("author") or {}).get("display_name") if authors else None,
                          "field": (topic.get("field") or {}).get("display_name")})
        row.update(source="gci", independent_rediscovery=row["kind"].startswith("R"), cited_works=works,
                   commit=sha, attribution=ATTRIBUTION)
    return sha, rows


def main() -> int:
    client = OpenAlexClient()
    sha, rows = mine(client)
    write_jsonl(CANDIDATES_DIR / "gci-catalogue.jsonl", rows)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"commit {sha[:12]}: {len(rows)} instances {kinds}; notes followed "
          f"{sum(len(r['note_paths']) for r in rows)}; DOIs cited {sum(len(r['cited_works']) for r in rows)} "
          f"(resolved {sum(w['resolved'] for r in rows for w in r['cited_works'])})")
    print(f"OpenAlex spend today: ${client.spent_today():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
