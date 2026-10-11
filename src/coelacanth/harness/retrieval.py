"""Exclusion matching and the bound OpenAlex search (docs/design/harness.md sections 4 and 7; protocol section 4).

Exclusions come in five forms (b01-meta.json: W IDs, DOI URLs in mixed case, bare DOIs, a handle.net URL and another
web URL), and `openalex.normalize_work_id` raises on the last two, so works are matched by their own keys:
`openalex:W1`, `doi:<lowercased DOI>`, `arxiv:<ID without version>` (arXiv IDs, arXiv URLs and 10.48550 DOIs alike),
`pmid:N`, and `url:<lowercased URL without scheme, www. or trailing slash>` for anything else.

`bound_search` is the only retrieval the systems we run get: an OpenAlex search filtered to the day before the claim
date, with excluded works and anything dated on or after the claim date dropped in code. The caller gets the works
that pass, never the exclusions or the claim date.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import date, timedelta
from typing import Callable, Iterable, Mapping

from ..benchmark import texts
from ..data.openalex import PRICE_USD, OpenAlexClient, OpenAlexError, abstract_text

Search = Callable[..., list[dict]]

OPENALEX_RE = re.compile(r"(?:(?:https?://)?(?:api\.)?openalex\.org/(?:works/)?)?(w\d+)", re.I)
DOI_URL_RE = re.compile(r"(?:https?://)?(?:dx\.|www\.)?doi\.org/", re.I)
DOI_PREFIX_RE = re.compile(r"doi:\s*", re.I)
ARXIV_ID_RE = re.compile(r"(?:arxiv:\s*)?(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", re.I)
PUBMED_RE = re.compile(r"(?:pmid:\s*|(?:https?://)?(?:www\.)?"
                       r"(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/)?(\d+)/?", re.I)
LOOKUP = {"openalex": "{}", "doi": "doi:{}", "arxiv": "arXiv:{}", "pmid": "pmid:{}"}


def canonical_id(value: str) -> str:
    """The key a work identifier is matched by. Never raises: an unrecognised value becomes a `url:` key."""
    v = str(value).strip()
    m = OPENALEX_RE.fullmatch(v)
    if m:
        return "openalex:" + m.group(1).upper()
    doi = None
    if DOI_URL_RE.match(v):
        doi = urllib.parse.unquote(DOI_URL_RE.sub("", v, count=1))
    elif DOI_PREFIX_RE.match(v):
        doi = DOI_PREFIX_RE.sub("", v, count=1)
    elif v.startswith("10."):
        doi = v
    if doi is not None:
        doi = doi.strip().lower()
        m = texts.ARXIV_DOI_RE.match(doi)
        return "arxiv:" + re.sub(r"v\d+$", "", m.group(1)) if m else "doi:" + doi
    m = ARXIV_ID_RE.fullmatch(v) or texts.ARXIV_RE.search(v.rstrip("/"))
    if m:
        return "arxiv:" + m.group(1).lower()
    m = PUBMED_RE.fullmatch(v)
    if m:
        return "pmid:" + m.group(1)
    key = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", v.lower())
    return "url:" + re.sub(r"^www\.", "", key).split("#", 1)[0].rstrip("/")


def work_keys(work: Mapping) -> set[str]:
    """Every key a work is known by. Reads an OpenAlex work (`id`, `doi`, `ids`, arXiv and landing-page locations) as
    well as a SCHEMA work object, a system's candidate or a resolved record (`openalex`, `doi`, `arxiv`, `pmid`,
    `url`)."""
    ids = work.get("ids") or {}
    values = [work.get(f) for f in ("openalex", "doi", "arxiv", "pmid", "url")]
    values += [ids.get("openalex"), ids.get("doi"), ids.get("pmid")]
    if str(work.get("id") or "").startswith("https://openalex.org/"):
        values.append(work["id"])
    aid = texts.arxiv_id(work)
    if aid:
        values.append("arXiv:" + aid)
    values += [loc.get("landing_page_url") for loc in work.get("locations") or []]
    return {canonical_id(v) for v in values if isinstance(v, (str, int)) and str(v).strip()}


def exclusion_keys(exclusions: Iterable[str], client: OpenAlexClient | None = None) -> frozenset[str]:
    """All keys an excluded work may be matched by. With a client, each W ID, DOI, arXiv ID or PMID is looked up
    (free, cached entity calls) and the work's other identifiers are added, so a W ID also matches its DOI and a DOI
    its W ID. A lookup OpenAlex cannot answer adds nothing."""
    keys: set[str] = set()
    for value in exclusions:
        if not str(value or "").strip():
            continue
        key = canonical_id(value)
        keys.add(key)
        kind, _, ident = key.partition(":")
        if client is None or kind not in LOOKUP:
            continue
        try:
            keys |= work_keys(client.work(LOOKUP[kind].format(ident), select=texts.WORK_SELECT))
        except (OpenAlexError, ValueError):
            continue
    return frozenset(keys)


def day_before(claim_date: str) -> str:
    return (date.fromisoformat(claim_date) - timedelta(days=1)).isoformat()


def bound_search(client: OpenAlexClient, *, claim_date: str, exclusions: Iterable[str], counts: dict,
                 log: Callable[[dict], None] | None = None) -> Search:
    """An OpenAlex search bound to one item: `to_publication_date` is the day before `claim_date`, and excluded,
    undated or later works are dropped before the caller sees the results (OpenAlex dates are not always earliest
    versions; `resolve` re-dates afterwards). Each search adds 1 to `counts["search"]` and its list price to
    `counts["usd"]`, whether or not the cache answered it (an upper bound). `log`, when given, gets one calls.jsonl row
    per search, without the query text."""
    keys = exclusion_keys(exclusions, client)
    to_date = day_before(claim_date)

    def search(query: str, per_page: int = 25, field_id: str | None = None) -> list[dict]:
        """Works for `query` published before the claim date; `field_id` narrows to one OpenAlex field."""
        flt = f"to_publication_date:{to_date}" + (f",primary_topic.field.id:{field_id}" if field_id else "")
        data = client.works(search=query, filter=flt, select=texts.WORK_SELECT, per_page=per_page)
        counts["search"] = counts.get("search", 0) + 1
        counts["usd"] = round(counts.get("usd", 0.0) + PRICE_USD["search"], 6)
        results = data.get("results") or []
        kept = []
        for work in results:
            published = work.get("publication_date")
            year_only = bool(published) and published.endswith("-01-01")  # OpenAlex stores a known year as 1 January
            if (not published or published >= claim_date or work_keys(work) & keys
                    or (year_only and published[:4] >= claim_date[:4])):
                continue
            obj = texts.work_object(work, None)
            obj.pop("cache_key")
            kept.append({**obj, "abstract": abstract_text(work)})
        if log:
            log({"step": "openalex.search", "kind": "openalex_search", "model": None,
                 "cost_usd": PRICE_USD["search"], "cost_source": "list_price", "tokens_all": None,
                 "budget_left_usd": None, "is_error": False, "n_results": len(results), "n_kept": len(kept)})
        return kept

    return search
