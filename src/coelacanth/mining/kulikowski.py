"""Kulikowski's curated cross-field twins (JCDL '26 package, tag v1.0.0) as benchmark candidates.

Reads the package clone in data/raw/kulikowski-spdf (see eval/results/kulikowski-baseline/), resolves the 109
papers to OpenAlex (free single-work lookups; PMC papers through NCBI's ID converter first) and writes
    data/candidates/kulikowski-papers.jsonl   one row per paper
    data/candidates/kulikowski-pairs.jsonl    one row per cross-field twin pair (same family, different field)
The derived data is CC-BY-4.0 (Kulikowski, LICENSE-DATA in the package); attribution is kept in every row.

    PYTHONPATH=src python -m coelacanth.mining.kulikowski
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import re
from pathlib import Path

from ..data.cache import ResponseCache
from ..data.openalex import OpenAlexClient, OpenAlexError
from ..env import REPO_ROOT
from . import CANDIDATES_DIR, fetch_free, write_jsonl

PACKAGE = REPO_ROOT / "data" / "raw" / "kulikowski-spdf"
ATTRIBUTION = ("Kulikowski, Same Problem, Different Field (JCDL '26), package v1.0.0, "
               "doi:10.48804/W3B9WC, CC-BY-4.0")
IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?format=json&tool=coelacanth&ids="
SELECT = ["id", "doi", "title", "publication_date", "type", "primary_topic", "authorships"]


def read_families(package: Path = PACKAGE) -> list[dict]:
    lines = [line for line in (package / "datasets" / "mode_a_seed_families.csv").read_text(encoding="utf-8")
             .splitlines() if line.strip() and not line.startswith("#")]
    rows = []
    for row in csv.DictReader(io.StringIO("\n".join(lines))):
        extra = row.pop(None, None)  # unquoted commas in the free-text note spill into extra fields
        if extra:
            row["note"] = ",".join([row.get("note") or ""] + extra)
        rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def external_id(url: str) -> tuple[str, str] | None:
    """('arxiv', '2012.06069'), ('pmcid', 'PMC7823953') or ('doi', '10.1073/...') from a package URL."""
    if m := re.search(r"arxiv\.org/(?:abs|pdf|html)/([^\s?#]+?)(?:v\d+)?/?$", url):
        return "arxiv", m.group(1)
    if m := re.search(r"(PMC\d+)", url):
        return "pmcid", m.group(1)
    if m := re.search(r"doi\.org/(10\.\S+)$", url):
        return "doi", m.group(1)
    return None


def pmc_to_doi_or_pmid(pmcids: list[str], cache: ResponseCache) -> dict[str, str]:
    """PMCID -> 'doi:...' or 'pmid:...' through NCBI's ID converter (one call, cached)."""
    if not pmcids:
        return {}
    body = fetch_free(IDCONV + ",".join(sorted(pmcids)), "ncbi-idconv", cache)
    out = {}
    for rec in json.loads(body).get("records", []):
        if rec.get("doi"):
            out[rec["pmcid"]] = "doi:" + rec["doi"]
        elif rec.get("pmid"):
            out[rec["pmcid"]] = "pmid:" + str(rec["pmid"])
    return out


def _brief(work: dict) -> dict:
    topic = work.get("primary_topic") or {}
    authors = work.get("authorships") or []
    first = (authors[0].get("author") or {}).get("display_name") if authors else None
    return {"openalex": (work.get("id") or "").rsplit("/", 1)[-1] or None, "doi": work.get("doi"),
            "openalex_title": work.get("title"), "publication_date": work.get("publication_date"),
            "type": work.get("type"), "first_author": first, "n_authors": len(authors),
            "openalex_field": (topic.get("field") or {}).get("display_name"),
            "openalex_domain": (topic.get("domain") or {}).get("display_name")}


def mine(client: OpenAlexClient, package: Path = PACKAGE) -> tuple[list[dict], list[dict]]:
    titles = {}
    for line in (package / "data" / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            titles[rec["id"]] = rec.get("title")
    rows = read_families(package)
    ids = {r["id"]: external_id(r["url"]) for r in rows}
    pmc = pmc_to_doi_or_pmid([v[1] for v in ids.values() if v and v[0] == "pmcid"], client.cache)
    papers = []
    for r in rows:
        ext = ids[r["id"]]
        lookup = None
        if ext and ext[0] == "arxiv":
            lookup = "arXiv:" + ext[1]
        elif ext and ext[0] == "doi":
            lookup = "doi:" + ext[1]
        elif ext and ext[0] == "pmcid":
            lookup = pmc.get(ext[1])
        paper = {"kid": r["id"], "family": r["family"], "field": r["field"], "role": r["role"] or "member",
                 "url": r["url"], "external_id": f"{ext[0]}:{ext[1]}" if ext else None,
                 "manifest_title": titles.get(r["id"]), "note": r.get("note"), "resolved": False}
        if lookup:
            try:
                paper.update(_brief(client.work(lookup, select=SELECT)), resolved=True)
            except OpenAlexError as err:
                paper["error"] = str(err)[:200]
        papers.append(paper)

    members = [p for p in papers if p["role"] == "member" and p["family"]]
    pairs = []
    for a, b in itertools.combinations(members, 2):
        if a["family"] != b["family"] or a["field"] == b["field"]:
            continue
        da, db = a.get("publication_date"), b.get("publication_date")
        if da and db and da != db:
            earlier, later = (a, b) if da < db else (b, a)
            order = "known"
        else:
            earlier, later, order = a, b, "unknown"
        pairs.append({"source": "kulikowski", "family": a["family"],
                      "later": later["kid"], "later_field": later["field"], "later_date": later.get("publication_date"),
                      "earlier": earlier["kid"], "earlier_field": earlier["field"],
                      "earlier_date": earlier.get("publication_date"), "order": order,
                      "cross_openalex_field": (later.get("openalex_field") != earlier.get("openalex_field")
                                               if later.get("openalex_field") and earlier.get("openalex_field")
                                               else None),
                      "attribution": ATTRIBUTION})
    for p in papers:
        p["attribution"] = ATTRIBUTION
    return papers, pairs


def main() -> int:
    client = OpenAlexClient()
    papers, pairs = mine(client)
    write_jsonl(CANDIDATES_DIR / "kulikowski-papers.jsonl", papers)
    write_jsonl(CANDIDATES_DIR / "kulikowski-pairs.jsonl", pairs)
    queries = {p["later"] for p in pairs} | {p["earlier"] for p in pairs}
    usable = {p["later"] for p in pairs if p["order"] == "known"}
    print(f"papers {len(papers)} (resolved {sum(p['resolved'] for p in papers)}), members "
          f"{sum(p['role'] == 'member' for p in papers)}, cross-field twin pairs {len(pairs)}, queries {len(queries)}, "
          f"papers with an earlier cross-field twin {len(usable)}, pairs with unknown order "
          f"{sum(p['order'] == 'unknown' for p in pairs)}, pairs also cross-field in OpenAlex "
          f"{sum(p['cross_openalex_field'] is True for p in pairs)}")
    print(f"OpenAlex spend today: ${client.spent_today():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
