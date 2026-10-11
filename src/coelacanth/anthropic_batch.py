"""Anthropic Message Batches API client, standard library only.

Endpoints and headers per the official batch-processing guide read on 2026-09-24
(https://platform.claude.com/docs/en/build-with-claude/batch-processing.md, cached in cache/raw/anthropic/):
POST /v1/messages/batches with {"requests": [{"custom_id", "params"}]}; GET /v1/messages/batches/{id} until
`processing_status` is "ended"; then the JSONL at `results_url`, one {"custom_id", "result": {"type", ...}} per request
(`succeeded` with a `message`, or `errored`, `canceled`, `expired`, which are not billed). Headers: x-api-key and
anthropic-version 2023-06-01. A batch holds at most 100,000 requests or 256 MB; most finish within an hour, all within
24 hours. Batch prices are half the list prices (coelacanth.pricing). The key comes from ANTHROPIC_API_KEY
(environment or .env) and is only ever sent in the header.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Iterator, Mapping

from . import env
from .data.semanticscholar import urllib_request
from .llm import LOW_CREDIT, key_problem

API_URL = "https://api.anthropic.com/v1/messages/batches"
API_VERSION = "2023-06-01"
MAX_REQUESTS = 100_000

Request = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, dict[str, str], bytes]]


class BatchError(RuntimeError):
    """The Batch API answered with an error."""


class LowCreditBalance(BatchError):
    """The API refused the request because the account's credit balance is too low."""


class BatchClient:
    def __init__(self, api_key: str | None = None, *, request: Request = urllib_request, timeout: float = 120.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        env.load_dotenv()
        self.api_key = api_key if api_key is not None else env.get("ANTHROPIC_API_KEY")
        problem = key_problem(self.api_key)
        if problem:
            raise BatchError(problem)
        self._request = request
        self._timeout = timeout
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}

    def _call(self, method: str, url: str, body: dict | None = None) -> bytes:
        """One API call with retries on 429 and 5xx; a GET is also retried after a network error (a DNS failure
        while polling), a POST never is, since the request may have reached the server and would create a second
        batch."""
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(6):
            try:
                status, _, raw = self._request(method, url, self._headers(), payload, self._timeout)
            except OSError as err:  # URLError, timeouts and connection resets are OSErrors
                if method != "GET" or attempt == 5:
                    raise BatchError(f"network error for {method} {url}: {err}") from err
                self._sleep(min(60.0, 2.0 ** (attempt + 2)))
                continue
            if status == 200:
                return raw
            text = raw[:400].decode("utf-8", "replace")
            if LOW_CREDIT.search(text):
                raise LowCreditBalance(f"HTTP {status}: the API reports a low credit balance: {text}")
            if status not in (429, 500, 502, 503, 529) or attempt == 5:
                raise BatchError(f"HTTP {status} for {method} {url}: {text}")
            self._sleep(min(60.0, 2.0 ** (attempt + 1)))
        raise BatchError("unreachable")

    def create(self, requests: list[dict]) -> dict:
        if not 0 < len(requests) <= MAX_REQUESTS:
            raise ValueError(f"a batch needs 1 to {MAX_REQUESTS} requests, got {len(requests)}")
        return json.loads(self._call("POST", API_URL, {"requests": requests}))

    def get(self, batch_id: str) -> dict:
        return json.loads(self._call("GET", f"{API_URL}/{batch_id}"))

    def wait(self, batch_id: str, *, poll_seconds: float = 30.0, max_hours: float = 24.5,
             on_poll: Callable[[dict], None] | None = None) -> dict:
        """Poll until the batch has ended (or give up after max_hours) and return its final state."""
        deadline = time.monotonic() + max_hours * 3600
        while True:
            batch = self.get(batch_id)
            if on_poll:
                on_poll(batch)
            if batch.get("processing_status") == "ended":
                return batch
            if time.monotonic() > deadline:
                raise BatchError(f"batch {batch_id} still {batch.get('processing_status')} after {max_hours} hours")
            self._sleep(poll_seconds)

    def results(self, batch: Mapping) -> Iterator[dict]:
        """The ended batch's results, one dict per request, from its results_url."""
        url = batch.get("results_url")
        if not url:
            raise BatchError(f"batch {batch.get('id')} has no results_url (status {batch.get('processing_status')})")
        for line in self._call("GET", url).decode("utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def message_text(result: Mapping) -> str | None:
    """The text of a succeeded result's message (text blocks joined), or None for any other result type."""
    res = result.get("result") or {}
    if res.get("type") != "succeeded":
        return None
    blocks = (res.get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
