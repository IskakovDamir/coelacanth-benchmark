"""Tests for the OpenAlex client, cache and rate limiter. No network: a scripted fetch stands in for HTTP.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coelacanth.data.cache import ResponseCache
from coelacanth.data.openalex import (BudgetExceeded, OpenAlexClient, OpenAlexError, abstract_text,
                                      normalize_work_id)
from coelacanth.data.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class FakeFetch:
    """Return scripted (status, headers, body) answers in order and record every request."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        status, hdrs, payload = self.answers.pop(0)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, hdrs, body


def ok(payload):
    return 200, {}, payload


class ClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.day = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        self.cache = ResponseCache(Path(self.tmp.name) / "http.sqlite", now=lambda: self.day)
        self.clock = FakeClock()
        self.limiter = RateLimiter(10, clock=self.clock.now, sleep=self.clock.sleep)

    def tearDown(self) -> None:
        self.cache.close()
        self.tmp.cleanup()

    def client(self, fetch, **kwargs) -> OpenAlexClient:
        kwargs.setdefault("api_key", "")  # keyless unless a test says otherwise; ignores any local .env
        return OpenAlexClient(cache=self.cache, limiter=self.limiter, fetch=fetch, now=lambda: self.day, **kwargs)


class TestLookupsAndCache(ClientTestCase):
    def test_work_lookup_is_cached_and_free(self):
        fetch = FakeFetch(ok({"id": "https://openalex.org/W1", "title": "T"}))
        client = self.client(fetch)
        self.assertEqual(client.work("https://doi.org/10.1126/SciAdv.ADX3420")["title"], "T")
        self.assertEqual(client.work("doi:10.1126/sciadv.adx3420")["title"], "T")
        self.assertEqual(len(fetch.calls), 1)
        self.assertIn("/works/doi:10.1126/sciadv.adx3420", fetch.calls[0][0])
        self.assertEqual(client.spent_today(), 0.0)

    def test_key_goes_in_header_not_in_url_cache_or_ledger(self):
        fetch = FakeFetch(ok({"results": [], "meta": {}}))
        client = self.client(fetch, api_key="SECRET-KEY")
        client.works(search="kriging")
        url, headers = fetch.calls[0]
        self.assertNotIn("SECRET-KEY", url)
        self.assertEqual(headers["Authorization"], "Bearer SECRET-KEY")
        keyless = self.client(FakeFetch())  # no scripted answers: any network call would fail
        self.assertEqual(keyless.works(search="kriging"), {"results": [], "meta": {}})
        self.assertNotIn(b"SECRET-KEY", Path(self.cache.path).read_bytes())

    def test_client_errors_raise_and_are_not_cached(self):
        error = (404, {}, b'{"error": "not found"}')
        fetch = FakeFetch(error, error)
        client = self.client(fetch)
        with self.assertRaises(OpenAlexError):
            client.work("W404")
        with self.assertRaises(OpenAlexError):
            client.work("W404")
        self.assertEqual(len(fetch.calls), 2)


class TestBudget(ClientTestCase):
    def test_search_cost_is_booked_and_guard_refuses_before_sending(self):
        fetch = FakeFetch(ok({"results": []}))
        client = self.client(fetch, daily_budget_usd=0.0015)
        client.works(search="a")
        self.assertAlmostEqual(client.spent_today(), 0.001)
        with self.assertRaises(BudgetExceeded):
            client.works(search="b")
        self.assertEqual(len(fetch.calls), 1)

    def test_search_filters_are_priced_as_searches(self):
        fetch = FakeFetch(ok({"results": []}))
        client = self.client(fetch, daily_budget_usd=0.0015)
        client.works(filter='title.search:"is equivalent to",type:letter')
        self.assertAlmostEqual(client.spent_today(), 0.001)
        with self.assertRaises(BudgetExceeded):
            client.works(filter='abstract.search:"rediscovered"')

    def test_ledger_books_the_cost_the_server_reports(self):
        fetch = FakeFetch((200, {"X-RateLimit-Cost-USD": "0.0005"}, {"results": []}))
        client = self.client(fetch)
        client.works(search="a")
        self.assertAlmostEqual(client.spent_today(), 0.0005)

    def test_budget_resets_at_midnight_utc(self):
        fetch = FakeFetch(ok({"results": []}), ok({"results": []}))
        client = self.client(fetch, daily_budget_usd=0.001)
        client.works(search="a")
        with self.assertRaises(BudgetExceeded):
            client.works(search="b")
        self.day = self.day + timedelta(days=1)
        client.works(search="b")
        self.assertAlmostEqual(client.spent_today(), 0.001)

    def test_keyless_default_budget_is_the_free_allowance(self):
        self.assertAlmostEqual(self.client(FakeFetch()).daily_budget_usd, 0.10)
        self.assertAlmostEqual(self.client(FakeFetch(), api_key="k").daily_budget_usd, 1.00)


class TestRetriesAndPaging(ClientTestCase):
    def test_retry_after_on_429_then_success(self):
        fetch = FakeFetch((429, {"Retry-After": "2"}, b"slow down"), ok({"results": []}))
        client = self.client(fetch)
        client.works(filter="publication_year:2026")
        self.assertEqual(len(fetch.calls), 2)
        self.assertEqual(self.clock.sleeps, [2.0])
        self.assertAlmostEqual(client.spent_today(), 0.0001)

    def test_gives_up_after_max_retries(self):
        busy = (503, {}, b"busy")
        fetch = FakeFetch(busy, busy, busy)
        client = self.client(fetch, max_retries=2)
        with self.assertRaises(OpenAlexError):
            client.works(filter="publication_year:2026")
        self.assertEqual(len(fetch.calls), 3)
        self.assertEqual(client.spent_today(), 0.0)

    def test_network_timeout_is_retried_then_given_up(self):
        answers = [TimeoutError("read timed out"), ok({"results": []})]

        def flaky(url, headers, timeout):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            status, hdrs, payload = answer
            return status, hdrs, json.dumps(payload).encode()
        self.client(flaky).works(filter="publication_year:2026")
        self.assertEqual(self.clock.sleeps, [1.0])

        def dead(url, headers, timeout):
            raise TimeoutError("read timed out")
        with self.assertRaises(OpenAlexError):
            self.client(dead, max_retries=1).works(filter="publication_year:2025")

    def test_iter_works_follows_cursor_and_stops_at_max_results(self):
        page1 = {"results": [{"id": "W1"}, {"id": "W2"}], "meta": {"next_cursor": "c2"}}
        page2 = {"results": [{"id": "W3"}, {"id": "W4"}], "meta": {"next_cursor": "c3"}}
        fetch = FakeFetch(ok(page1), ok(page2))
        client = self.client(fetch)
        ids = [w["id"] for w in client.iter_works(filter="publication_year:2026", per_page=2, max_results=3)]
        self.assertEqual(ids, ["W1", "W2", "W3"])
        self.assertIn("cursor=*", fetch.calls[0][0])
        self.assertIn("cursor=c2", fetch.calls[1][0])
        self.assertAlmostEqual(client.spent_today(), 0.0002)

    def test_works_by_ids_batches_and_uses_the_openalex_filter(self):
        ids = [f"W{i}" for i in range(1, 121)]
        pages = [ok({"results": [{"id": w} for w in ids[s:s + 50]]}) for s in (0, 50, 100)]
        fetch = FakeFetch(*pages)
        out = self.client(fetch).works_by_ids(ids)
        self.assertEqual(len(out), 120)
        self.assertEqual(len(fetch.calls), 3)
        self.assertIn("filter=openalex:W1|W2|", fetch.calls[0][0])


class TestHelpers(unittest.TestCase):
    def test_normalize_work_id(self):
        cases = {
            "W123": "W123",
            "https://openalex.org/w123": "W123",
            "10.1063/1.1699114": "doi:10.1063/1.1699114",
            "https://doi.org/10.1063/1.1699114": "doi:10.1063/1.1699114",
            "DOI:10.1063/1.1699114": "doi:10.1063/1.1699114",
            "arXiv:2609.07595v2": "doi:10.48550/arxiv.2609.07595",
            "pmid:8137688": "pmid:8137688",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_work_id(raw), expected, raw)
        with self.assertRaises(ValueError):
            normalize_work_id("Tai 1994")

    def test_abstract_text(self):
        work = {"abstract_inverted_index": {"rule": [2], "The": [0], "trapezoidal": [1]}}
        self.assertEqual(abstract_text(work), "The trapezoidal rule")
        self.assertIsNone(abstract_text({}))

    def test_rate_limiter_spaces_calls(self):
        clock = FakeClock()
        limiter = RateLimiter(4, clock=clock.now, sleep=clock.sleep)
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(clock.sleeps, [0.25, 0.25])


if __name__ == "__main__":
    unittest.main()
