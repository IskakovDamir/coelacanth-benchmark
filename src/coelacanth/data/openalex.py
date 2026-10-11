"""OpenAlex client: cached in SQLite, rate-limited, with a daily budget guard. Standard library only.

Prices follow OpenAlex's usage pricing as read on 2026-09-24 (docs/api-keys.md): single-work lookups are free,
list and filter calls cost $0.0001, searches $0.001. The free allowance is $1 per day with a key and $0.10 without,
reset at midnight UTC. The key travels as a bearer token, never in URLs, cache keys or the ledger.

    PYTHONPATH=src python -m coelacanth.data.openalex work doi:10.1126/sciadv.adx3420
    PYTHONPATH=src python -m coelacanth.data.openalex search "trapezoidal rule glucose tolerance" --per-page 3
    PYTHONPATH=src python -m coelacanth.data.openalex ids W1 W2
    PYTHONPATH=src python -m coelacanth.data.openalex spent
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Iterable, Iterator, Mapping

from .. import __version__, env
from .cache import ResponseCache, utcnow
from .ratelimit import RateLimiter

BASE_URL = "https://api.openalex.org"
SOURCE = "openalex"
USER_AGENT = f"coelacanth/{__version__} (+https://github.com/IskakovDamir/coelacanth)"
PRICE_USD = {"entity": 0.0, "list": 0.0001, "search": 0.001}
FREE_DAILY_USD = {True: 1.00, False: 0.10}
IDS_PER_CALL = 50

Fetch = Callable[[str, Mapping[str, str], float], tuple[int, dict[str, str], bytes]]


class OpenAlexError(RuntimeError):
    """OpenAlex answered with an error that retrying will not fix."""


class BudgetExceeded(RuntimeError):
    """The call would exceed the daily budget; nothing was sent."""


SYSTEM_CA_BUNDLE = "/etc/ssl/cert.pem"
_ssl_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """Verifying TLS context. python.org builds of Python on macOS ship without CA certificates until their
    'Install Certificates' script runs; then fall back to the operating system's bundle. Verification stays on."""
    global _ssl_context
    if _ssl_context is None:
        context = ssl.create_default_context()
        if not context.get_ca_certs() and not ssl.get_default_verify_paths().cafile:
            try:
                context.load_verify_locations(SYSTEM_CA_BUNDLE)
            except (FileNotFoundError, ssl.SSLError):
                pass
        _ssl_context = context
    return _ssl_context


def urllib_fetch(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read()


def normalize_work_id(value: str) -> str:
    """Path segment for /works/ from an OpenAlex ID or URL, a DOI in any usual form, pmid:N or arXiv:ID."""
    v = value.strip()
    low = v.lower()
    if low.startswith("https://openalex.org/"):
        v = v.rsplit("/", 1)[1]
        low = v.lower()
    if re.fullmatch(r"w\d+", low):
        return low.upper()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if low.startswith(prefix):
            return "doi:" + low[len(prefix):]
    if low.startswith("10."):
        return "doi:" + low
    if low.startswith("pmid:") and low[5:].strip().isdigit():
        return "pmid:" + low[5:].strip()
    if low.startswith("arxiv:"):
        return "doi:10.48550/arxiv." + re.sub(r"v\d+$", "", low[6:].strip())
    raise ValueError(f"unrecognised work identifier: {value!r}")


def abstract_text(work: Mapping) -> str | None:
    """Rebuild the abstract from OpenAlex's inverted index."""
    index = work.get("abstract_inverted_index")
    if not index:
        return None
    words = {pos: word for word, positions in index.items() for pos in positions}
    return " ".join(words[i] for i in sorted(words))


def _charged(headers: Mapping[str, str], fallback: float) -> float:
    """The cost OpenAlex reports for the call (X-RateLimit-Cost-USD), else the price table's value."""
    for name, value in headers.items():
        if name.lower() == "x-ratelimit-cost-usd":
            try:
                return float(value)
            except ValueError:
                break
    return fallback


def _retry_after(headers: Mapping[str, str]) -> float | None:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except ValueError:
                return None
    return None


class OpenAlexClient:
    def __init__(self, api_key: str | None = None, *, cache: ResponseCache | None = None,
                 limiter: RateLimiter | None = None, daily_budget_usd: float | None = None,
                 fetch: Fetch = urllib_fetch, now: Callable[[], datetime] = utcnow,
                 rate_per_second: float = 10.0, timeout: float = 30.0, max_retries: int = 5) -> None:
        self.api_key = api_key if api_key is not None else env.get("OPENALEX_API_KEY")
        self.cache = cache or ResponseCache()
        self.limiter = limiter or RateLimiter(rate_per_second)
        self.daily_budget_usd = (FREE_DAILY_USD[bool(self.api_key)] if daily_budget_usd is None
                                 else daily_budget_usd)
        self.last_rate_limit: dict[str, str] = {}
        self._fetch = fetch
        self._now = now
        self._timeout = timeout
        self._max_retries = max_retries

    def work(self, work_id: str, select: Iterable[str] | None = None) -> dict:
        params = {"select": ",".join(select)} if select else {}
        return self._get("/works/" + normalize_work_id(work_id), params, "entity")

    def works(self, *, search: str | None = None, filter: str | Mapping[str, str] | None = None,
              select: Iterable[str] | None = None, sort: str | None = None, per_page: int = 25,
              page: int | None = None, cursor: str | None = None) -> dict:
        params = {"per-page": str(per_page)}
        if search:
            params["search"] = search
        if filter:
            params["filter"] = filter if isinstance(filter, str) else ",".join(f"{k}:{v}" for k, v in filter.items())
        if select:
            params["select"] = ",".join(select)
        if sort:
            params["sort"] = sort
        if cursor:
            params["cursor"] = cursor
        elif page:
            params["page"] = str(page)
        searching = bool(search) or ".search" in params.get("filter", "")  # title.search:, abstract.search: ...
        return self._get("/works", params, "search" if searching else "list")

    def iter_works(self, *, max_results: int | None = None, per_page: int = 200, **query) -> Iterator[dict]:
        """Follow cursor pagination; stop after max_results works."""
        cursor, seen = "*", 0
        while cursor:
            data = self.works(per_page=per_page, cursor=cursor, **query)
            results = data.get("results") or []
            for item in results:
                yield item
                seen += 1
                if max_results is not None and seen >= max_results:
                    return
            if not results:
                return
            cursor = (data.get("meta") or {}).get("next_cursor")

    def works_by_ids(self, ids: Iterable[str], select: Iterable[str] | None = None) -> list[dict]:
        """Fetch many works by OpenAlex W ID, IDS_PER_CALL per list call."""
        wids = [normalize_work_id(i) for i in ids]
        if any(not w.startswith("W") for w in wids):
            raise ValueError("works_by_ids takes OpenAlex W IDs; use work() for DOIs")
        out: list[dict] = []
        for start in range(0, len(wids), IDS_PER_CALL):
            chunk = wids[start:start + IDS_PER_CALL]
            data = self.works(filter="openalex:" + "|".join(chunk), select=select, per_page=len(chunk))
            out.extend(data.get("results") or [])
        return out

    def spent_today(self) -> float:
        midnight = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.cache.spent(SOURCE, midnight)

    def _url(self, path: str, params: Mapping[str, str]) -> str:
        query = urllib.parse.urlencode(sorted(params.items()), safe=":|,*")
        return BASE_URL + urllib.parse.quote(path, safe="/:.") + (f"?{query}" if query else "")

    def _get(self, path: str, params: Mapping[str, str], kind: str) -> dict:
        url = self._url(path, params)
        cached = self.cache.get(SOURCE, url)
        if cached is not None:
            self.cache.record(SOURCE, kind, url, 0.0, cached=True, status=200)
            return json.loads(cached)
        price = PRICE_USD[kind]
        spent = self.spent_today()
        if spent + price > self.daily_budget_usd + 1e-12:
            raise BudgetExceeded(f"OpenAlex: ${spent:.4f} spent today; this {kind} call costs ${price:.4f}; "
                                 f"daily budget ${self.daily_budget_usd:.2f}")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self._max_retries + 1):
            self.limiter.acquire()
            try:
                status, resp_headers, body = self._fetch(url, headers, self._timeout)
            except (TimeoutError, OSError) as err:  # a read timeout or a dropped connection: retry like a 5xx
                if attempt < self._max_retries:
                    self.limiter.pause(min(60.0, 2.0 ** attempt))
                    continue
                raise OpenAlexError(f"gave up after {self._max_retries + 1} attempts: {url}: {err}") from err
            self.last_rate_limit = {k: v for k, v in resp_headers.items() if k.lower().startswith("x-ratelimit")}
            if status == 200:
                self.cache.put(SOURCE, url, status, body)
                self.cache.record(SOURCE, kind, url, _charged(resp_headers, price), cached=False, status=status)
                return json.loads(body)
            self.cache.record(SOURCE, kind, url, 0.0, cached=False, status=status)
            if status != 429 and status < 500:
                raise OpenAlexError(f"HTTP {status} for {url}: {body[:300].decode('utf-8', 'replace')}")
            if attempt < self._max_retries:
                self.limiter.pause(_retry_after(resp_headers) or min(60.0, 2.0 ** attempt))
        raise OpenAlexError(f"gave up after {self._max_retries + 1} attempts: {url}")


def _brief(work: Mapping) -> dict:
    topic = work.get("primary_topic") or {}
    return {"id": work.get("id"), "doi": work.get("doi"), "title": work.get("title") or work.get("display_name"),
            "date": work.get("publication_date"), "field": (topic.get("field") or {}).get("display_name"),
            "domain": (topic.get("domain") or {}).get("display_name")}


BRIEF_FIELDS = ["id", "doi", "title", "display_name", "publication_date", "primary_topic"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.data.openalex")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("work").add_argument("id")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--per-page", type=int, default=5)
    search.add_argument("--filter")
    sub.add_parser("ids").add_argument("ids", nargs="+")
    sub.add_parser("spent")
    args = parser.parse_args(argv)

    client = OpenAlexClient()
    if args.cmd == "work":
        print(json.dumps(_brief(client.work(args.id, select=BRIEF_FIELDS)), ensure_ascii=False))
    elif args.cmd == "search":
        data = client.works(search=args.query, filter=args.filter, select=BRIEF_FIELDS, per_page=args.per_page)
        print(f"# {data.get('meta', {}).get('count')} results")
        for work in data.get("results", []):
            print(json.dumps(_brief(work), ensure_ascii=False))
    elif args.cmd == "ids":
        for work in client.works_by_ids(args.ids, select=BRIEF_FIELDS):
            print(json.dumps(_brief(work), ensure_ascii=False))
    print(f"# OpenAlex spend today: ${client.spent_today():.4f} of ${client.daily_budget_usd:.2f} "
          f"({'with key' if client.api_key else 'no key'}); rate-limit headers: {client.last_rate_limit}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
