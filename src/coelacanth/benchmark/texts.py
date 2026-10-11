"""Cached texts for quote checks: a work's OpenAlex abstract and, when it is open access, its full text from arXiv or
Europe PMC, written to cache/fulltext/ (gitignored, never redistributed) under the keys SCHEMA.md's `cache_key`
names. HTTP responses go through the shared SQLite cache; OpenAlex work lookups are free, arXiv is fetched at most
once every three seconds across all processes (arXiv's terms of use; a lock file in the system temp folder holds the
time of the last request) and Europe PMC at most twice a second.

Keys: `oa-<W id>` (title and abstract), `arxiv-<id>` (PDF to text with pdftotext), `arxivabs-<id>` (title and
abstract from the arXiv API, for preprints OpenAlex no longer resolves), `pmc-<PMCID>` (Europe PMC open-access XML to
text). The work object carries the best key: full text when there is one, else the abstract.

    PYTHONPATH=src python -m coelacanth.benchmark.texts fetch W2049633694 10.1214/aos/1176346059 arXiv:1303.6700
    PYTHONPATH=src python -m coelacanth.benchmark.texts quote oa-W2049633694 "a broadly applicable algorithm"
    PYTHONPATH=src python -m coelacanth.benchmark.texts refs W2049633694     # its reference list, one line each
"""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping

from ..data.cache import ResponseCache
from ..data.openalex import USER_AGENT, OpenAlexClient, OpenAlexError, abstract_text, urllib_fetch
from ..data.ratelimit import RateLimiter
from ..quotes import FULLTEXT_DIR, QuoteChecker

WORK_SELECT = ["id", "doi", "title", "publication_date", "publication_year", "ids", "authorships", "locations",
               "primary_location", "best_oa_location", "primary_topic", "type", "abstract_inverted_index"]
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", re.I)
ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(.+)$", re.I)
ARXIV_INTERVAL = 3.0
ARXIV_LOCK = Path(tempfile.gettempdir()) / "coelacanth-arxiv.lock"


class SharedLimiter:
    """At most one call per `interval` seconds across processes, through an exclusive lock on a small file."""

    def __init__(self, path: Path, interval: float) -> None:
        self.path, self.interval = path, interval

    def acquire(self) -> None:
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                last = float(f.read().strip() or 0)
                wait = last + self.interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                f.seek(0)
                f.truncate()
                f.write(str(time.time()))
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


_arxiv_limiter = SharedLimiter(ARXIV_LOCK, ARXIV_INTERVAL)
_epmc_limiter = RateLimiter(2.0)


class TextError(RuntimeError):
    """A text could not be fetched or converted."""


def arxiv_id(work: Mapping) -> str | None:
    doi = (work.get("doi") or "").lower()
    m = ARXIV_DOI_RE.search(doi)
    if m:
        return m.group(1)
    for loc in work.get("locations") or []:
        for url in (loc.get("landing_page_url"), loc.get("pdf_url")):
            m = ARXIV_RE.search(url or "")
            if m:
                return m.group(1)
    return None


def pmcid(work: Mapping) -> str | None:
    value = ((work.get("ids") or {}).get("pmcid") or "").rstrip("/")
    m = re.search(r"(PMC)?(\d+)$", value, re.I)
    return f"PMC{m.group(2)}" if value and m else None


def work_object(work: Mapping, cache_key: str | None) -> dict:
    """SCHEMA.md's work object from an OpenAlex work."""
    authors = work.get("authorships") or []
    topic = work.get("primary_topic") or {}
    source = (work.get("primary_location") or {}).get("source") or {}
    pmid = ((work.get("ids") or {}).get("pmid") or "").rstrip("/").rsplit("/", 1)[-1] or None
    return {"openalex": (work.get("id") or "").rsplit("/", 1)[-1] or None, "doi": work.get("doi"),
            "arxiv": arxiv_id(work), "pmid": pmid, "title": work.get("title"),
            "first_author": ((authors[0].get("author") or {}).get("display_name") if authors else None),
            "n_authors": len(authors), "year": work.get("publication_year"),
            "publication_date": work.get("publication_date"), "venue": source.get("display_name"),
            "field": (topic.get("field") or {}).get("display_name"),
            "domain": (topic.get("domain") or {}).get("display_name"),
            "oa_url": (work.get("best_oa_location") or {}).get("landing_page_url"), "cache_key": cache_key}


def _write(key: str, text: str, directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.txt").write_text(text, encoding="utf-8")
    return key


def _get(cache: ResponseCache, source: str, url: str, limiter: RateLimiter | SharedLimiter) -> bytes:
    body = cache.get(source, url)
    if body is not None:
        cache.record(source, "text", url, 0.0, cached=True, status=200)
        return body
    limiter.acquire()
    status, _, body = urllib_fetch(url, {"User-Agent": USER_AGENT}, 120.0)
    cache.record(source, "text", url, 0.0, cached=False, status=status)
    if status != 200:
        raise TextError(f"HTTP {status} for {url}")
    cache.put(source, url, status, body)
    return body


def cache_abstract(work: Mapping, directory: Path = FULLTEXT_DIR) -> str | None:
    abstract = abstract_text(work)
    if not abstract:
        return None
    wid = (work.get("id") or "").rsplit("/", 1)[-1]
    return _write(f"oa-{wid}", f"{work.get('title') or ''}\n\n{abstract}\n", directory)


def cache_arxiv(aid: str, cache: ResponseCache, directory: Path = FULLTEXT_DIR) -> str:
    if not shutil.which("pdftotext"):
        raise TextError("pdftotext is not installed")
    body = _get(cache, "arxiv", f"https://arxiv.org/pdf/{aid}", _arxiv_limiter)
    if not body.startswith(b"%PDF"):
        raise TextError(f"arXiv {aid}: the response is not a PDF")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "paper.pdf"
        pdf.write_bytes(body)
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, timeout=120)
    if out.returncode != 0 or not out.stdout.strip():
        raise TextError(f"arXiv {aid}: pdftotext failed")
    return _write("arxiv-" + aid.replace("/", "_"), out.stdout.decode("utf-8", "replace"), directory)


def xml_text(xml: str) -> str:
    """Plain text from JATS XML: block ends become line breaks, tags are dropped, entities are decoded."""
    xml = re.sub(r"<(ref-list|table-wrap|fig|xref)[\s>].*?</\1>", " ", xml, flags=re.S)
    xml = re.sub(r"</(p|title|sec|abstract|article-title|caption|label)>", "\n", xml)
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip() + "\n"


def cache_pmc(pmc: str, cache: ResponseCache, directory: Path = FULLTEXT_DIR) -> str:
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmc}/fullTextXML"
    body = _get(cache, "europepmc", url, _epmc_limiter)
    return _write(f"pmc-{pmc}", xml_text(body.decode("utf-8", "replace")), directory)


ATOM = {"a": "http://www.w3.org/2005/Atom"}


def arxiv_work(aid: str, cache: ResponseCache, directory: Path = FULLTEXT_DIR) -> tuple[dict, str]:
    """Metadata from the arXiv API for a preprint OpenAlex does not resolve; caches its title and abstract."""
    body = _get(cache, "arxiv", f"https://export.arxiv.org/api/query?id_list={aid}", _arxiv_limiter)
    entry = ET.fromstring(body).find("a:entry", ATOM)
    if entry is None or entry.find("a:title", ATOM) is None:
        raise TextError(f"arXiv {aid}: no entry")
    title = " ".join((entry.findtext("a:title", "", ATOM)).split())
    summary = " ".join((entry.findtext("a:summary", "", ATOM)).split())
    authors = [a.findtext("a:name", "", ATOM) for a in entry.findall("a:author", ATOM)]
    published = (entry.findtext("a:published", "", ATOM) or "")[:10] or None
    key = _write("arxivabs-" + aid.replace("/", "_"), f"{title}\n\n{summary}\n", directory)
    work = {"openalex": None, "doi": f"https://doi.org/10.48550/arxiv.{aid}", "arxiv": aid, "pmid": None,
            "title": title, "first_author": authors[0] if authors else None, "n_authors": len(authors),
            "year": int(published[:4]) if published else None, "publication_date": published, "venue": "arXiv",
            "field": None, "domain": None, "oa_url": f"https://arxiv.org/abs/{aid}", "cache_key": key}
    return work, key


def fetch(identifier: str, client: OpenAlexClient | None = None, fulltext: bool = True,
          directory: Path = FULLTEXT_DIR) -> dict:
    """Resolve a work (OpenAlex ID, DOI, pmid:N or arXiv:ID), cache its texts, and return the work object plus
    the cached keys with their lengths and any fetch problems. An arXiv:ID that OpenAlex does not resolve is read
    from the arXiv API instead."""
    client = client or OpenAlexClient()
    try:
        work = client.work(identifier, select=WORK_SELECT)
    except OpenAlexError:
        if not identifier.lower().startswith("arxiv:"):
            raise
        aid = re.sub(r"v\d+$", "", identifier[6:].strip())
        obj, abstract_key = arxiv_work(aid, client.cache, directory)
        keys, problems = {abstract_key: "abstract"}, ["openalex: not resolved; metadata from the arXiv API"]
        if fulltext:
            try:
                full = cache_arxiv(aid, client.cache, directory)
                keys[full] = "full_text"
                obj["cache_key"] = full
            except (TextError, OSError, subprocess.SubprocessError) as err:
                problems.append(f"arxiv: {err}")
        lengths = {k: len((directory / f"{k}.txt").read_text(encoding="utf-8").split()) for k in keys}
        return {"work": obj, "texts": {k: {"level": v, "words": lengths[k]} for k, v in keys.items()},
                "problems": problems}
    keys, problems = {}, []
    abstract_key = cache_abstract(work, directory)
    if abstract_key:
        keys[abstract_key] = "abstract"
    full_key = None
    if fulltext:
        for kind, ident, fn in (("arxiv", arxiv_id(work), cache_arxiv), ("pmc", pmcid(work), cache_pmc)):
            if not ident or full_key:
                continue
            try:
                full_key = fn(ident, client.cache, directory)
                keys[full_key] = "full_text"
            except (TextError, OSError, subprocess.SubprocessError) as err:
                problems.append(f"{kind}: {err}")
    obj = work_object(work, full_key or abstract_key)
    lengths = {k: len((directory / f"{k}.txt").read_text(encoding="utf-8").split()) for k in keys}
    return {"work": obj, "texts": {k: {"level": v, "words": lengths[k]} for k, v in keys.items()},
            "problems": problems}


def ai_origin_work(method: Mapping, directory: Path = FULLTEXT_DIR) -> dict:
    """The work object of a generated AI-origin method, with its full record cached as `aio-<id>` for quote checks.
    Its title is the coined name before the colon of the method's name ("Branch-DFE: in situ ..." gives
    "Branch-DFE"): the rest describes the method and is not a published title a system could search for."""
    text = (f"{method['name']}\n\nField: {method['field']}\n\nProblem: {method['problem']}\n\n"
            f"Method: {method['method']}\n\nEvaluation: {method.get('evaluation') or ''}\n\n"
            f"Why new: {method.get('why_new') or ''}\n")
    key = _write(f"aio-{method['id']}", text, directory)
    return {"openalex": None, "doi": None, "arxiv": None, "pmid": None, "ai_origin": method["id"],
            "title": method["name"].split(":", 1)[0].strip(), "first_author": method["model"], "n_authors": 1,
            "year": int(method["date"][:4]), "publication_date": method["date"], "venue": None, "field": method["field"],
            "domain": None, "oa_url": None, "cache_key": key}


def references(identifier: str, client: OpenAlexClient | None = None) -> list[dict]:
    """The works a work cites, per OpenAlex: id, year, first author, title (list calls of 50 IDs, $0.0001 each)."""
    client = client or OpenAlexClient()
    refs = client.work(identifier, select=["referenced_works"]).get("referenced_works") or []
    works = client.works_by_ids([r.rsplit("/", 1)[-1] for r in refs],
                                select=["id", "publication_year", "authorships", "title", "doi"])
    return [{"openalex": (w.get("id") or "").rsplit("/", 1)[-1], "year": w.get("publication_year"),
             "first_author": ((w.get("authorships") or [{}])[0].get("author") or {}).get("display_name"),
             "title": w.get("title"), "doi": w.get("doi")} for w in works]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.texts")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="cache texts for works and print their work objects")
    f.add_argument("ids", nargs="+")
    f.add_argument("--no-fulltext", action="store_true")
    q = sub.add_parser("quote", help="check one quote against one cached text")
    q.add_argument("key")
    q.add_argument("quote")
    r = sub.add_parser("refs", help="list the works a work cites, per OpenAlex")
    r.add_argument("id")
    a = sub.add_parser("ai-origin", help="cache a generated method's text and print its work object")
    a.add_argument("id")
    args = parser.parse_args(argv)
    if args.command == "ai-origin":
        from ..llm import read_jsonl
        methods = {m["id"]: m for m in read_jsonl(FULLTEXT_DIR.parents[1] / "data" / "candidates" / "ai-origin"
                                                  / "methods.jsonl")}
        print(json.dumps({"work": ai_origin_work(methods[args.id])}, ensure_ascii=False))
        return 0
    if args.command == "refs":
        for ref in sorted(references(args.id), key=lambda x: (x["year"] or 0)):
            print(json.dumps(ref, ensure_ascii=False))
        return 0
    if args.command == "quote":
        try:
            level = QuoteChecker().level(args.quote, args.key)
        except FileNotFoundError:
            print(json.dumps({"key": args.key, "level": None, "problem": "no cached text"}))
            return 2
        print(json.dumps({"key": args.key, "level": level}))
        return 0 if level else 1
    client = OpenAlexClient()
    for ident in args.ids:
        try:
            print(json.dumps(fetch(ident, client, fulltext=not args.no_fulltext), ensure_ascii=False))
        except Exception as err:  # report and go on with the next identifier
            print(json.dumps({"id": ident, "error": f"{type(err).__name__}: {err}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
