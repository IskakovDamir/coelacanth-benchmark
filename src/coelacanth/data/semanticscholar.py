"""Semantic Scholar Graph API client: cached in SQLite, limited to 1 request per second, key in the x-api-key header.

It refuses to run without S2_API_KEY (Damir: no keyless runs). The API is free; calls are booked in the shared
ledger at $0. Endpoints used, per the Graph API's OpenAPI spec read on 2026-09-24
(cache/raw/s2-graph-swagger-2026-09-24.json):
- GET /snippet/search: text snippets of about 500 words from titles, abstracts and body text (no captions or
  bibliography); `query` is plain text with no special syntax; `limit` is at most 1000; filters include `year`,
  `fieldsOfStudy`, `publicationDateOrYear`, `venue` and `minCitationCount`.
- POST /paper/batch: up to 500 IDs per call (for example CorpusId:123), with `fields`.
The API license forbids repackaging the API: responses stay in the gitignored cache, and only identifiers resolved
through OpenAlex are written to data/.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Mapping

from .. import env
from .cache import ResponseCache
from .openalex import USER_AGENT, ssl_context
from .ratelimit import RateLimiter

BASE_URL = "https://api.semanticscholar.org/graph/v1"
SOURCE = "semanticscholar"
BATCH_MAX = 500
SNIPPET_LIMIT_MAX = 1000

Request = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, dict[str, str], bytes]]


class MissingKey(RuntimeError):
    """No S2_API_KEY: the client does not run keyless."""


class S2Error(RuntimeError):
    """The API answered with an error that retrying will not fix."""


def urllib_request(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                   timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read()


class S2Client:
    def __init__(self, api_key: str | None = None, *, cache: ResponseCache | None = None,
                 limiter: RateLimiter | None = None, request: Request = urllib_request, timeout: float = 60.0,
                 max_retries: int = 5) -> None:
        self.api_key = api_key if api_key is not None else env.get("S2_API_KEY")
        if not self.api_key:
            raise MissingKey("S2_API_KEY is not set; the Semantic Scholar client does not run without a key")
        self.cache = cache or ResponseCache()
        self.limiter = limiter or RateLimiter(1.0)
        self._request = request
        self._timeout = timeout
        self._max_retries = max_retries

    def snippet_search(self, query: str, *, limit: int = SNIPPET_LIMIT_MAX, year: str | None = None,
                       fields_of_study: str | None = None, publication_date_or_year: str | None = None,
                       fields: Iterable[str] | None = None) -> dict:
        params = {"query": query, "limit": str(min(limit, SNIPPET_LIMIT_MAX))}
        if year:
            params["year"] = year
        if fields_of_study:
            params["fieldsOfStudy"] = fields_of_study
        if publication_date_or_year:
            params["publicationDateOrYear"] = publication_date_or_year
        if fields:
            params["fields"] = ",".join(fields)
        return self._call("GET", "/snippet/search", params)

    def paper_batch(self, ids: list[str], fields: Iterable[str]) -> list[dict | None]:
        """Papers for up to BATCH_MAX IDs per call, in input order (None where the API has no record)."""
        out: list[dict | None] = []
        for start in range(0, len(ids), BATCH_MAX):
            chunk = ids[start:start + BATCH_MAX]
            data = self._call("POST", "/paper/batch", {"fields": ",".join(fields)}, {"ids": chunk})
            out.extend(data if isinstance(data, list) else [])
        return out

    def _call(self, method: str, path: str, params: Mapping[str, str], body: dict | None = None):
        url = BASE_URL + path + "?" + urllib.parse.urlencode(sorted(params.items()))
        cache_key = url if body is None else url + " " + json.dumps(body, sort_keys=True)
        cached = self.cache.get(SOURCE, cache_key)
        if cached is not None:
            self.cache.record(SOURCE, method.lower(), cache_key, 0.0, cached=True, status=200)
            return json.loads(cached)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "x-api-key": self.api_key}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        for attempt in range(self._max_retries + 1):
            self.limiter.acquire()
            status, resp_headers, raw = self._request(method, url, headers, payload, self._timeout)
            self.cache.record(SOURCE, method.lower(), cache_key, 0.0, cached=False, status=status)
            if status == 200:
                self.cache.put(SOURCE, cache_key, status, raw)
                return json.loads(raw)
            if status != 429 and status < 500:
                raise S2Error(f"HTTP {status} for {url}: {raw[:300].decode('utf-8', 'replace')}")
            if attempt < self._max_retries:
                self.limiter.pause(min(60.0, 2.0 ** (attempt + 1)))
        raise S2Error(f"gave up after {self._max_retries + 1} attempts: {url}")
