"""Tests for the text cache helpers: identifiers, work objects and XML to text. No network.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import tempfile
import unittest
from pathlib import Path

from coelacanth.benchmark import texts

WORK = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x", "title": "A Title",
        "publication_year": 1999, "publication_date": "1999-05-01",
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123", "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/456"},
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {"display_name": "B C"}}],
        "locations": [{"landing_page_url": "https://arxiv.org/abs/1303.6700v2"}],
        "primary_location": {"source": {"display_name": "Venue"}},
        "primary_topic": {"field": {"display_name": "Mathematics"}, "domain": {"display_name": "Physical Sciences"}},
        "abstract_inverted_index": {"Short": [0], "abstract.": [1]}}


class TestIdentifiers(unittest.TestCase):
    def test_arxiv_id_from_location_or_doi(self):
        self.assertEqual(texts.arxiv_id(WORK), "1303.6700")
        self.assertEqual(texts.arxiv_id({"doi": "https://doi.org/10.48550/arXiv.2101.00001"}), "2101.00001")
        self.assertIsNone(texts.arxiv_id({"doi": "https://doi.org/10.1/x"}))

    def test_pmcid(self):
        self.assertEqual(texts.pmcid(WORK), "PMC456")
        self.assertIsNone(texts.pmcid({"ids": {}}))

    def test_work_object_fields(self):
        obj = texts.work_object(WORK, "oa-W1")
        self.assertEqual((obj["openalex"], obj["pmid"], obj["first_author"], obj["n_authors"], obj["year"]),
                         ("W1", "123", "Ada Lovelace", 2, 1999))
        self.assertEqual((obj["field"], obj["venue"], obj["cache_key"]), ("Mathematics", "Venue", "oa-W1"))


class TestSharedLimiter(unittest.TestCase):
    def test_second_call_waits_for_the_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            limiter = texts.SharedLimiter(Path(tmp) / "lock", 0.2)
            import time
            limiter.acquire()
            start = time.time()
            limiter.acquire()
            self.assertGreaterEqual(time.time() - start, 0.15)


class TestAiOrigin(unittest.TestCase):
    def test_generated_method_is_its_own_source_work(self):
        method = {"id": "ai-x-1", "model": "claude-sonnet-5", "field": "economics", "date": "2026-09-24",
                  "name": "Coined-X: a long description", "problem": "P.", "method": "M.", "evaluation": "E.",
                  "why_new": "W."}
        with tempfile.TemporaryDirectory() as tmp:
            work = texts.ai_origin_work(method, Path(tmp))
            cached = (Path(tmp) / "aio-ai-x-1.txt").read_text()
        self.assertEqual((work["ai_origin"], work["title"], work["cache_key"]), ("ai-x-1", "Coined-X", "aio-ai-x-1"))
        self.assertIn("Method: M.", cached)


class TestTexts(unittest.TestCase):
    def test_abstract_is_cached_with_its_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = texts.cache_abstract(WORK, Path(tmp))
            self.assertEqual(key, "oa-W1")
            self.assertEqual((Path(tmp) / "oa-W1.txt").read_text(), "A Title\n\nShort abstract.\n")

    def test_xml_text_drops_tags_references_and_decodes_entities(self):
        xml = ("<article><title>On &amp; off</title><p>First <italic>para</italic>.</p>"
               "<ref-list><ref>Cited 1990</ref></ref-list><p>Second.</p></article>")
        out = texts.xml_text(xml)
        self.assertIn("On & off\n", out)
        self.assertIn("First para.\n", out)
        self.assertNotIn("Cited 1990", out)


if __name__ == "__main__":
    unittest.main()
