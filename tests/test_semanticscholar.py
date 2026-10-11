"""Tests for the Semantic Scholar client and the snippet miner. No network: fakes stand in for HTTP and clients.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from coelacanth.data.cache import ResponseCache
from coelacanth.data.ratelimit import RateLimiter
from coelacanth.data.semanticscholar import MissingKey, S2Client, S2Error
from coelacanth.mining import s2_snippets


class FakeRequest:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        status, payload = self.answers.pop(0)
        return status, {}, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = ResponseCache(Path(self.tmp.name) / "http.sqlite")
        self.limiter = RateLimiter(1000, sleep=lambda s: None)

    def tearDown(self):
        self.cache.close()
        self.tmp.cleanup()

    def client(self, request, key="KEY"):
        return S2Client(key, cache=self.cache, limiter=self.limiter, request=request)


class TestClient(ClientCase):
    def test_refuses_to_run_without_a_key(self):
        with self.assertRaises(MissingKey):
            S2Client("", cache=self.cache)

    def test_key_in_header_and_responses_cached(self):
        fake = FakeRequest((200, {"data": [], "retrievalVersion": "v"}))
        client = self.client(fake)
        client.snippet_search("was first proposed in", year="1990-1999")
        client.snippet_search("was first proposed in", year="1990-1999")
        self.assertEqual(len(fake.calls), 1)
        method, url, headers, _ = fake.calls[0]
        self.assertEqual((method, headers["x-api-key"]), ("GET", "KEY"))
        self.assertIn("/snippet/search?", url)
        self.assertIn("limit=1000", url)
        self.assertNotIn("KEY", url)

    def test_retries_on_429_and_raises_on_400(self):
        fake = FakeRequest((429, b"slow"), (200, {"data": []}))
        self.assertEqual(self.client(fake).snippet_search("x"), {"data": []})
        with self.assertRaises(S2Error):
            self.client(FakeRequest((400, b"bad"))).snippet_search("y")

    def test_paper_batch_posts_ids_in_chunks(self):
        ids = [f"CorpusId:{i}" for i in range(600)]
        fake = FakeRequest((200, [{"paperId": str(i)} for i in range(500)]), (200, [{"paperId": "x"}] * 100))
        papers = self.client(fake).paper_batch(ids, fields=["externalIds"])
        self.assertEqual(len(papers), 600)
        self.assertEqual([c[0] for c in fake.calls], ["POST", "POST"])
        self.assertEqual(len(json.loads(fake.calls[0][3])["ids"]), 500)


def snippet(corpus_id, text, kind="body"):
    return {"snippet": {"text": text, "snippetKind": kind}, "score": 0.5, "paper": {"corpusId": corpus_id}}


class FakeS2:
    def __init__(self, full_slice=None):
        self.full_slice = full_slice
        self.calls = []

    def snippet_search(self, query, *, year=None, fields_of_study=None, fields=None, **_):
        self.calls.append((query, year, fields_of_study))
        if (query, year) == self.full_slice and fields_of_study is None:
            return {"data": [snippet("9", "filler")] * 1000}
        if query == "was first proposed in" and year == "1990-1999":
            return {"data": [snippet("1", "This estimator was first proposed in the physics literature."),
                             snippet("2", "The method was proposed in 1993.")]}
        return {"data": []}

    def paper_batch(self, ids, fields):
        return [{"externalIds": {"DOI": "10.1000/one"}} for _ in ids]


class FakeOpenAlex:
    def work(self, work_id, select=None):
        return {"id": "https://openalex.org/W1", "title": "One", "publication_date": "1995-01-01",
                "primary_topic": {"field": {"display_name": "Physics and Astronomy"}}}


class TestMiner(unittest.TestCase):
    def test_keeps_only_snippets_with_the_phrase_and_writes_no_text(self):
        rows, stats = s2_snippets.mine(FakeS2(), FakeOpenAlex())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phrases"], ["first_proposed_in"])
        self.assertEqual((rows[0]["openalex"], rows[0]["doi"]), ("W1", "10.1000/one"))
        self.assertNotIn("physics literature", json.dumps(rows))
        self.assertEqual(stats["first_proposed_in"], {"snippets": 2, "matching": 1})

    def test_full_slice_is_split_by_field_of_study(self):
        fake = FakeS2(full_slice=("rediscovered", "2010-2014"))
        s2_snippets.search_slice(fake, "rediscovered", "2010-2014")
        self.assertEqual(sum(1 for c in fake.calls if c[2] is not None), len(s2_snippets.FIELDS_OF_STUDY))

    def test_known_in_field_as_pattern(self):
        rx = s2_snippets.PHRASES["known_in_field_as"]["pattern"]
        found = s2_snippets.matching({"data": [snippet("1", "This quantity is known in ecology as the Hill number."),
                                               snippet("2", "It is known in advance.")]}, rx)
        self.assertEqual([m["paper"]["corpusId"] for m in found], ["1"])


if __name__ == "__main__":
    unittest.main()
