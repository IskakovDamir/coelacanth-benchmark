"""Tests for resolution, dating, the leakage rule and cleaning after a run (docs/design/harness.md section 7).
No network: a routed fetch stands in for OpenAlex, the arXiv API is patched, and a fake label mapper implements
precedence over the cleaned list (labels.map_item comes with the rules freeze).

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from coelacanth.benchmark import texts
from coelacanth.benchmark.items import derive_verdict
from coelacanth.data.cache import ResponseCache
from coelacanth.data.openalex import OpenAlexClient
from coelacanth.data.ratelimit import RateLimiter
from coelacanth.harness import resolve as resolve_mod
from coelacanth.harness.resolve import (_Booked, clean, date_evidence, earliest_date, leak, resolve_candidate,
                                        resolve_run)

CLAIM = "2016-06-01"
THREE_CLASS = {"equivalent": "already_done", "specialization": "already_done", "generalization": "related",
               "partial": "related", "similar_mechanism": "related", "nothing_close": "nothing_close"}
CFG = {"system": "single-agent", "label_map": {"rule": "precedence_over_candidates"}}


class RoutedFetch:
    """Answer each request with the payload of the first route whose key occurs in the URL, else HTTP 404; record
    every URL."""

    def __init__(self, routes: dict) -> None:
        self.routes = list(routes.items())
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        for key, payload in self.routes:
            if key in url:
                return 200, {}, json.dumps(payload).encode()
        return 404, {}, b'{"error": "not found"}'


def oa_work(wid, date=None, *, year=None, doi=None, title="A title", author="Ann Smith", arxiv=None):
    """An OpenAlex work with the fields the resolver reads."""
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title,
            "publication_date": date, "publication_year": year or (int(date[:4]) if date else None),
            "ids": {"openalex": f"https://openalex.org/{wid}"},
            "authorships": [{"author": {"display_name": author}}],
            "locations": [{"landing_page_url": f"https://arxiv.org/abs/{arxiv}"}] if arxiv else []}


def precedence(cfg, native, cleaned, status):
    """Stands in for labels.map_item under the `precedence_over_candidates` rule."""
    if status != "ok":
        return {"rule": cfg["label_map"]["rule"], "native": native, "six": None, "class3": None, "mapped": False}
    six = derive_verdict({"candidates": cleaned})
    return {"rule": cfg["label_map"]["rule"], "native": native, "six": six, "class3": THREE_CLASS[six], "mapped": True}


def item(exclusions=(), source=None):
    return {"id": "cf-9001", "claim": {"text": "a claim", "date": CLAIM, "source_work": source or {}},
            "exclusions": list(exclusions)}


def row(candidates=(), evidence=(), status="ok"):
    return {"run_id": "single-agent.text.T1.x", "system": "single-agent", "condition": "text", "item": "cf-9001",
            "at": "2026-10-20T08:12:00+00:00", "config_sha256": "c0ffee", "status": status, "failure": status != "ok",
            "native": {"label": None, "probability": None, "candidates": list(candidates), "evidence": list(evidence)}}


def arxiv_v1(dates):
    """A stand-in for texts.arxiv_work: the arXiv API's v1 date per ID, TextError for any other ID."""
    def fake(aid, cache, directory=None):
        if aid not in dates:
            raise texts.TextError(f"arXiv {aid}: no entry")
        return {"openalex": None, "doi": f"https://doi.org/10.48550/arxiv.{aid}", "arxiv": aid, "pmid": None,
                "title": f"Preprint {aid}", "first_author": "Ann Smith", "n_authors": 1, "year": int(dates[aid][:4]),
                "publication_date": dates[aid], "venue": "arXiv", "field": None, "domain": None, "oa_url": None,
                "cache_key": None}, "arxivabs-" + aid
    return fake


class ResolveCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.day = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        self.cache = ResponseCache(Path(self.tmp.name) / "http.sqlite", now=lambda: self.day)
        self.limiter = RateLimiter(1000, clock=lambda: 0.0, sleep=lambda s: None)
        patcher = mock.patch("coelacanth.benchmark.texts.arxiv_work", side_effect=arxiv_v1({}))
        self.arxiv = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.cache.close()
        self.tmp.cleanup()

    def client(self, fetch) -> OpenAlexClient:
        return OpenAlexClient(api_key="", cache=self.cache, limiter=self.limiter, fetch=fetch, now=lambda: self.day)

    def clean(self, routes, candidates=(), evidence=(), exclusions=(), source=None, status="ok", raw=None):
        self.fetch = RoutedFetch({**routes, "search=": {"results": []}})
        r = row(candidates, evidence, status)
        if raw is not None:
            r["raw"] = raw
        return clean(r, item(exclusions, source), self.client(self.fetch), map_verdict=precedence, cfg=CFG)


class TestLeakRule(ResolveCase):
    def test_on_claim_date_is_a_hit(self):
        self.assertEqual(leak({"date": CLAIM}, CLAIM, frozenset()), "on_or_after_claim_date")
        self.assertIsNone(leak({"date": "2016-05-31"}, CLAIM, frozenset()))
        out = self.clean({"/works/W1?": oa_work("W1", CLAIM)}, [{"openalex": "W1", "relation": "equivalent"}])
        self.assertEqual(out["candidates"][0]["leak"], "on_or_after_claim_date")
        self.assertEqual(out["top10"], [])
        self.assertEqual(out["verdict"]["six"], "nothing_close")

    def test_undated_is_a_hit(self):
        self.assertEqual(leak({"date": None}, CLAIM, frozenset()), "undated")
        out = self.clean({}, [{"title": "A memoir nobody indexed", "authors": "Doe", "year": None}])
        c = out["candidates"][0]
        self.assertEqual((c["resolved_by"], c["date_source"], c["leak"], c["placeholder"]),
                         ("none", "none", "undated", False))

    def test_year_only_same_year_is_a_hit(self):
        self.assertEqual(leak({"date": "2016"}, CLAIM, frozenset()), "year_only_not_before")
        self.assertIsNone(leak({"date": "2015"}, CLAIM, frozenset()))
        out = self.clean({"/works/W1?": oa_work("W1", None, year=2016)}, [{"openalex": "W1"}])
        c = out["candidates"][0]
        self.assertEqual((c["date"], c["date_source"], c["leak"]), ("2016", "year_only", "year_only_not_before"))

    def test_excluded_work_comes_first(self):
        keys = frozenset({"doi:10.5555/src"})
        self.assertEqual(leak({"doi": "https://doi.org/10.5555/SRC", "date": "1990-01-01"}, CLAIM, keys),
                         "excluded_work")


class TestDating(ResolveCase):
    def test_earlier_arxiv_v1_rescues_a_later_journal_date(self):
        self.arxiv.side_effect = arxiv_v1({"1505.01234": "2015-05-05"})
        journal = oa_work("W1", "2017-05-01", doi="10.5555/journal", arxiv="1505.01234")
        out = self.clean({"/works/doi:10.5555/journal?": journal}, [{"doi": "10.5555/JOURNAL", "relation": "partial"}])
        c = out["candidates"][0]
        self.assertEqual((c["resolved_by"], c["openalex"], c["arxiv"]), ("doi", "W1", "1505.01234"))
        self.assertEqual((c["date"], c["date_source"], c["leak"], c["rank_clean"]),
                         ("2015-05-05", "arxiv_v1", None, 1))
        self.assertEqual(out["top10"], ["W1"])
        self.assertEqual(earliest_date(journal, self.client(self.fetch)), ("2015-05-05", "arxiv_v1"))

    def test_earlier_record_with_same_title_and_author(self):
        later = oa_work("W1", "2017-05-01", title="On Filtering, Revisited", author="Ann Smith")
        same = oa_work("W7", "2014-02-02", title="On filtering revisited", author="A. Smith")
        other_author = oa_work("W8", "2001-01-01", title="On Filtering, Revisited", author="Bob Jones")
        fetch = RoutedFetch({"search=On+Filtering": {"results": [later, same, other_author]}})
        self.assertEqual(earliest_date(later, self.client(fetch)), ("2014-02-02", "earlier_record"))

    def test_dates_before_the_claim_need_no_lookup(self):
        out = self.clean({"/works/W2?": oa_work("W2", "1970-02-01", arxiv="hep-th/9901001")}, [{"openalex": "W2"}])
        self.assertEqual(out["candidates"][0]["date_source"], "openalex")
        self.assertFalse([u for u in self.fetch.calls if "search=" in u])
        self.arxiv.assert_not_called()

    def test_preprint_openalex_no_longer_resolves_is_dated_by_the_arxiv_api(self):
        self.arxiv.side_effect = arxiv_v1({"1303.6700": "2013-03-27"})
        out = self.clean({}, [{"url": "https://arxiv.org/abs/1303.6700v2", "relation": "equivalent"}])
        c = out["candidates"][0]
        self.assertEqual((c["resolved_by"], c["date"], c["date_source"], c["leak"]),
                         ("arxiv", "2013-03-27", "arxiv_v1", None))
        self.assertEqual(out["top10"], ["arXiv:1303.6700"])

    def test_live_page_is_an_evidence_hit(self):
        evidence = [{"kind": "fetched_page", "url": "https://en.wikipedia.org/wiki/Kalman_filter", "openalex": None},
                    {"kind": "fetched_page", "url": "https://doi.org/10.5555/OLD", "openalex": None},
                    {"kind": "search_results", "url": None, "openalex": None}]
        out = self.clean({"/works/doi:10.5555/old?": oa_work("W2", "1960-03-01", doi="10.5555/old")},
                         evidence=evidence)
        live, paper, listing = out["evidence"]
        self.assertEqual((live["date"], live["date_source"], live["leak"]),
                         ("2026-10-20", "version_read", "on_or_after_claim_date"))
        self.assertEqual((paper["openalex"], paper["date_source"], paper["leak"]), ("W2", "openalex", None))
        self.assertEqual((listing["date_source"], listing["leak"]), ("not_counted", None))
        self.assertEqual(out["leakage"], {"hit_top10": False, "hit_evidence": True, "n_hits": 1, "n_undated": 0,
                                          "n_excluded": 0})
        fetched = date_evidence({"kind": "fetched_page", "url": "https://example.org/a", "fetched_at": "2026-10-21"},
                                self.client(self.fetch))
        self.assertEqual((fetched["date"], fetched["date_source"]), ("2026-10-21", "version_read"))


class TestCleaning(ResolveCase):
    OLD = {"/works/W2?": oa_work("W2", "1970-01-01"), "/works/W3?": oa_work("W3", "1980-01-01"),
           "/works/W1?": oa_work("W1", "2017-01-01")}

    def test_hits_removed_and_ranks_reassigned(self):
        out = self.clean(self.OLD, [{"openalex": "W1", "relation": "equivalent"},
                                    {"openalex": "W2", "relation": "similar_mechanism"},
                                    {"openalex": "W3", "relation": "partial"}])
        self.assertEqual([c["rank_raw"] for c in out["candidates"]], [1, 2, 3])
        self.assertEqual([c["rank_clean"] for c in out["candidates"]], [None, 1, 2])
        self.assertEqual(out["top10"], ["W2", "W3"])
        self.assertEqual((out["verdict"]["six"], out["verdict"]["six_before_cleaning"]), ("partial", "equivalent"))

    def test_leakage_counts_per_row(self):
        candidates = [{"openalex": "W9", "relation": "equivalent"}, {"title": "Untraceable", "year": None},
                      {"openalex": "W2"}]
        out = self.clean({**self.OLD, "/works/W9?": oa_work("W9", "1999-01-01")}, candidates,
                         evidence=[{"kind": "fetched_page", "url": "https://example.org/blog"}], exclusions=["W9"])
        self.assertEqual([c["leak"] for c in out["candidates"]], ["excluded_work", "undated", None])
        self.assertEqual(out["leakage"], {"hit_top10": True, "hit_evidence": True, "n_hits": 3, "n_undated": 1,
                                          "n_excluded": 1})
        late = [{"openalex": "W2"}] * 10 + [{"openalex": "W1"}]
        out = self.clean(self.OLD, late)
        self.assertEqual(out["leakage"], {"hit_top10": False, "hit_evidence": False, "n_hits": 1, "n_undated": 0,
                                          "n_excluded": 0})

    def test_duplicate_versions_collapse(self):
        journal = oa_work("W2", "1970-01-01", doi="10.5555/old", arxiv="1505.01234")
        preprint = oa_work("W5", "1969-06-01", doi="10.48550/arxiv.1505.01234")
        routes = {"/works/doi:10.5555/old?": journal, "/works/W2?": journal,
                  "/works/doi:10.48550/arxiv.1505.01234?": preprint, "/works/W3?": oa_work("W3", "1980-01-01")}
        out = self.clean(routes, [{"doi": "https://doi.org/10.5555/OLD", "relation": "partial"},
                                  {"openalex": "W2", "relation": "equivalent"},
                                  {"url": "https://arxiv.org/abs/1505.01234", "relation": "equivalent"},
                                  {"openalex": "W3", "relation": "similar_mechanism"}])
        self.assertEqual([c["duplicate_of"] for c in out["candidates"]], [None, 1, 1, None])
        self.assertEqual([c["rank_clean"] for c in out["candidates"]], [1, None, None, 2])
        self.assertEqual(out["top10"], ["W2", "W3"])
        self.assertEqual(out["verdict"]["six"], "partial")

    def test_unresolved_with_earlier_year_is_placeholder(self):
        near_miss = oa_work("W6", "1904-01-01", title="Lectures on heat", author="Carl Neumann")
        twins = [oa_work("W7", "1950-01-01", title="Twin title of record", author="Eve Ray"),
                 oa_work("W8", "1951-01-01", title="Twin title of record", author="Eve Ray")]
        out = self.clean({**self.OLD, "search=An+obscure+memoir": {"results": [near_miss]},
                          "search=Twin+title": {"results": twins}},
                         [{"title": "An obscure memoir on diffusion", "authors": "Doe", "year": 1903,
                           "relation": "equivalent"},
                          {"title": "A recent preprint", "authors": "Roe", "year": 2016},
                          {"openalex": "W2", "relation": "partial"},
                          {"title": "Twin title of record", "authors": ["Eve Ray"], "year": 1950}])
        memoir, recent, old, twin = out["candidates"]
        self.assertEqual((memoir["resolved_by"], memoir["date"], memoir["date_source"], memoir["leak"]),
                         ("none", "1903", "year_only", None))
        self.assertTrue(memoir["placeholder"])
        self.assertEqual(recent["leak"], "year_only_not_before")
        self.assertFalse(recent["placeholder"])
        self.assertTrue(twin["ambiguous"] and twin["placeholder"])
        self.assertEqual(twin["resolved_by"], "none")
        self.assertEqual(out["top10"], [None, "W2", None])
        self.assertIn("filter=publication_year:1902-1904", next(u for u in self.fetch.calls if "obscure" in u))

    def test_source_work_labelled_equivalent_gives_no_already_done(self):
        source = oa_work("W9", CLAIM, doi="10.5555/src")
        routes = {**self.OLD, "/works/W9?": source, "/works/doi:10.5555/src?": source}
        candidates = [{"openalex": "W9", "relation": "equivalent"},
                      {"doi": "10.5555/SRC", "relation": "specialization"},
                      {"openalex": "W2", "relation": "similar_mechanism"}]
        for exclusions, src in ((["https://doi.org/10.5555/SRC"], None), ([], {"openalex": "W9"})):
            out = self.clean(routes, candidates, exclusions=exclusions, source=src)
            self.assertEqual([c["leak"] for c in out["candidates"]], ["excluded_work", "excluded_work", None])
            self.assertEqual(out["verdict"]["six_before_cleaning"], "equivalent")
            self.assertEqual(out["verdict"]["six"], "similar_mechanism")
            self.assertNotEqual(out["verdict"]["class3"], "already_done")

    def test_real_label_rules_after_cleaning(self):
        """Integration with labels.map_item (no injected mapper): the excluded source work cannot give already done."""
        source = oa_work("W9", CLAIM, doi="10.5555/src")
        self.fetch = RoutedFetch({**self.OLD, "/works/W9?": source, "search=": {"results": []}})
        cfg = {**CFG, "label_map": {"rule": "precedence_over_candidates"}}
        out = clean(row([{"openalex": "W9", "relation": "equivalent"}, {"openalex": "W2", "relation": "partial"}],
                        (), "ok"), item([], {"openalex": "W9"}), self.client(self.fetch), cfg=cfg)
        self.assertEqual(out["verdict"]["six"], "partial")
        self.assertEqual(out["verdict"]["class3"], "related")
        self.assertEqual(out["verdict"]["six_before_cleaning"], "equivalent")

    def test_failed_row_keeps_its_record_with_null_labels(self):
        out = self.clean({}, [], status="capped")
        self.assertEqual((out["status"], out["failure"], out["candidates"], out["top10"]), ("capped", True, [], []))
        self.assertIsNone(out["verdict"]["class3"])
        self.assertIsNone(out["verdict"]["six_before_cleaning"])


class TestResolution(ResolveCase):
    def test_exact_title_wins_over_the_same_authors_longer_title(self):
        same = oa_work("W1", "1983-05-13", title="Optimization by Simulated Annealing", author="Scott Kirkpatrick")
        longer = oa_work("W2", "1984-01-01", title="Optimization by simulated annealing: Quantitative studies",
                         author="Scott Kirkpatrick")
        client = self.client(RoutedFetch({"search=": {"results": [longer, same]}}))
        res = resolve_candidate({"title": "Optimization by Simulated Annealing", "authors": "Kirkpatrick",
                                 "year": 1983}, client)
        self.assertEqual((res["resolved_by"], res["work"]["id"]), ("title_search", "https://openalex.org/W1"))

    def test_two_equally_good_titles_stay_ambiguous(self):
        a = oa_work("W1", "1983-01-01", title="Spectra of point processes", author="Alan Hawkes")
        b = oa_work("W2", "1983-06-01", title="Spectra of point processes", author="Alan Hawkes")
        client = self.client(RoutedFetch({"search=": {"results": [a, b]}}))
        res = resolve_candidate({"title": "Spectra of point processes", "authors": "Hawkes", "year": 1983}, client)
        self.assertTrue(res["ambiguous"])
        self.assertIsNone(res["work"])

    def test_routes_in_order(self):
        fetch = RoutedFetch({"/works/W1?": oa_work("W1", "1970-01-01"),
                             "/works/doi:10.5555/a?": oa_work("W2", "1971-01-01"),
                             "/works/pmid:9312132?": oa_work("W3", "1997-10-01")})
        client = self.client(fetch)
        self.assertEqual(resolve_candidate({"openalex": "W1", "doi": "10.5555/a"}, client)["resolved_by"],
                         "openalex_id")
        self.assertEqual(resolve_candidate({"openalex": "W404", "doi": "10.5555/A"}, client)["resolved_by"], "doi")
        res = resolve_candidate({"url": "https://pubmed.ncbi.nlm.nih.gov/9312132/"}, client)
        self.assertEqual((res["resolved_by"], res["work"]["id"]), ("pmid", "https://openalex.org/W3"))
        self.assertEqual(resolve_candidate({"title": ""}, client), {"resolved_by": "none", "work": None,
                                                                    "ambiguous": False})

    def test_resolve_run_writes_records_and_books_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            infra = {**row(status="infra_error"), "native": None}
            ok = row([{"openalex": "W2", "relation": "partial"}, {"title": "An obscure memoir", "year": 1903}])
            (run_dir / "outputs.jsonl").write_text(json.dumps(infra) + "\n" + json.dumps(ok) + "\n", encoding="utf-8")
            fetch = RoutedFetch({"/works/W2?": oa_work("W2", "1970-01-01"), "search=": {"results": []}})
            summary = resolve_run(run_dir, client=self.client(fetch), items=[item(["W9"])],
                                  configs={"single-agent": CFG}, map_verdict=precedence)
            resolved = [json.loads(x) for x in (run_dir / "resolved.jsonl").read_text(encoding="utf-8").splitlines()]
            calls = [json.loads(x) for x in (run_dir / "resolve-calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(resolved), 1)  # the last row per item
        self.assertEqual((resolved[0]["status"], resolved[0]["top10"]), ("ok", ["W2", None]))
        self.assertEqual(resolved[0]["resolver"], {"title_searches": 1, "usd_nominal": 0.001})
        self.assertEqual(summary["placeholders"], 1)
        self.assertEqual(summary["title_searches"], 1)
        self.assertEqual({(c["kind"], c["cost_usd"]) for c in calls},
                         {("openalex_entity", 0.0), ("openalex_search", 0.001)})
        self.assertTrue(all(c["run_id"] == "single-agent.text.T1.x" and c["item"] == "cf-9001" for c in calls))
        self.assertEqual(sum(c["kind"] == "openalex_search" for c in calls), 1)

    def test_booked_client_counts_at_list_price_even_from_cache(self):
        fetch = RoutedFetch({"search=": {"results": []}})
        rows = []
        booked = _Booked(self.client(fetch), log=rows.append, tags={"item": "cf-9001"})
        booked.works(search="same query")
        booked.works(search="same query")
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual((booked.searches, round(booked.usd, 6)), (2, 0.002))
        self.assertEqual([r["item"] for r in rows], ["cf-9001", "cf-9001"])


class TestExcludedAndVersions(ResolveCase):
    """Exclusions, earlier records, cited pages and January-first dates (harness fix review, findings 19 to 25)."""

    SOURCE = {"openalex": "W11", "title": "Fast sparse filtering of signals", "first_author": "Ann Smith",
              "year": 2016}

    def test_unresolved_citation_of_the_source_work_is_excluded(self):
        source = oa_work("W11", CLAIM, title="Fast sparse filtering of signals")
        journal = oa_work("W12", "2016-09-01", title="Fast sparse filtering of signals")
        routes = {**TestCleaning.OLD, "/works/W11?": source, "/works/W12?": journal}
        for year, search in ((2014, []), (2015, [source, journal])):
            out = self.clean({**routes, "search=Fast+sparse": {"results": search}},
                             [{"title": "Fast sparse filtering of signals", "authors": "Ann Smith", "year": year,
                               "relation": "equivalent"}, {"openalex": "W2", "relation": "partial"}],
                             exclusions=["W12"], source=self.SOURCE)
            cited = out["candidates"][0]
            self.assertEqual((cited["resolved_by"], cited["leak"], cited["placeholder"]),
                             ("none", "excluded_work", False), year)
            self.assertEqual(out["top10"], ["W2"])
            self.assertEqual((out["verdict"]["six"], out["verdict"]["class3"]), ("partial", "related"))
            self.assertEqual(out["leakage"]["n_excluded"], 1)

    def test_earlier_record_needs_the_same_first_author(self):
        homonym = oa_work("W33", "2018-05-01", title="Introduction", author="Ann Smith")
        other = oa_work("W34", "1990-03-01", title="Introduction", author="Mary Smith")
        anonymous = {**oa_work("W35", "2019-02-01", title="Kalman filtering"), "authorships": []}
        kalman = oa_work("W36", "1961-03-01", title="Kalman Filtering", author="Rudolf Kalman")
        out = self.clean({"/works/W33?": homonym, "/works/W35?": anonymous,
                          "search=Introduction": {"results": [homonym, other]},
                          "search=Kalman": {"results": [anonymous, kalman]}},
                         [{"openalex": "W33", "relation": "equivalent"},
                          {"openalex": "W35", "relation": "specialization"}])
        self.assertEqual([(c["date"], c["date_source"], c["leak"]) for c in out["candidates"]],
                         [("2018-05-01", "openalex", "on_or_after_claim_date"),
                          ("2019-02-01", "openalex", "on_or_after_claim_date")])
        self.assertEqual((out["top10"], out["verdict"]["class3"]), ([], "nothing_close"))
        self.assertFalse([u for u in self.fetch.calls if "search=Kalman" in u])  # no first author: no title search

    def test_cited_web_page_is_dated_by_the_version_read(self):
        page = {"title": "Kalman filter", "authors": None, "year": 1960, "relation": "equivalent",
                "url": "https://en.wikipedia.org/wiki/Kalman_filter"}
        out = self.clean({}, [page], evidence=[{"kind": "fetched_page", "url": page["url"]}])
        c = out["candidates"][0]
        self.assertEqual((c["resolved_by"], c["date"], c["date_source"], c["leak"], c["placeholder"]),
                         ("none", "2026-10-20", "version_read", "on_or_after_claim_date", False))
        self.assertTrue(out["leakage"]["hit_top10"])
        self.assertEqual((out["top10"], out["verdict"]["six"]), ([], "nothing_close"))

    def test_versions_linked_by_dating_collapse(self):
        reprint = oa_work("W41", "2018-03-01", title="Lectures on Heat Flow", author="John Smith")
        original = oa_work("W42", "1950-03-01", title="Lectures on heat flow", author="J. Smith")
        routes = {"/works/W41?": reprint, "/works/W42?": original,
                  "search=Lectures": {"results": [reprint, original]}}
        pair = [{"openalex": "W41", "relation": "partial"}, {"openalex": "W42", "relation": "partial"}]
        out = self.clean(routes, pair)
        self.assertEqual([(c["date"], c["duplicate_of"]) for c in out["candidates"]],
                         [("1950-03-01", None), ("1950-03-01", 1)])
        self.assertEqual([c["same_work"] for c in out["candidates"]], [["W42"], []])
        self.assertEqual(out["top10"], ["W41"])
        out = self.clean(routes, pair[::-1])
        self.assertEqual([c["duplicate_of"] for c in out["candidates"]], [None, 1])
        self.assertEqual(out["top10"], ["W42"])
        journal = oa_work("W48", "2017-05-01", doi="10.5555/journal2", title="Sparse coding of images")
        preprint = oa_work("W49", "2015-05-05", doi="10.48550/arxiv.1505.01234", title="Sparse Coding of Images",
                           author="A. Smith")
        out = self.clean({"/works/doi:10.5555/journal2?": journal, "/works/doi:10.48550/arxiv.1505.01234?": preprint,
                          "search=Sparse+coding": {"results": [journal, preprint]}},
                         [{"doi": "10.5555/JOURNAL2", "relation": "partial"},
                          {"url": "https://doi.org/10.48550/arXiv.1505.01234", "relation": "partial"}])
        self.assertEqual([(c["date_source"], c["duplicate_of"]) for c in out["candidates"]],
                         [("earlier_record", None), ("openalex", 1)])
        self.assertEqual(out["top10"], ["W48"])

    def test_january_first_is_year_only(self):
        self.arxiv.side_effect = arxiv_v1({"1603.00001": "2016-03-01"})
        out = self.clean({"/works/W20?": oa_work("W20", "2016-01-01"),
                          "/works/W21?": oa_work("W21", "2016-01-01", arxiv="1603.00001"),
                          "/works/W22?": oa_work("W22", "1990-01-01")},
                         [{"openalex": "W20", "relation": "equivalent"}, {"openalex": "W21", "relation": "partial"},
                          {"openalex": "W22", "relation": "partial"}])
        self.assertEqual([(c["date"], c["date_source"], c["leak"]) for c in out["candidates"]],
                         [("2016", "year_only", "year_only_not_before"), ("2016-03-01", "arxiv_v1", None),
                          ("1990", "year_only", None)])
        self.assertEqual((out["top10"], out["verdict"]["six"]), (["W21", "W22"], "partial"))

    def test_exclusion_given_as_a_landing_page_url(self):
        handle = "https://hdl.handle.net/1843/36698"
        thesis = {**oa_work("W60", "1990-02-01", doi="10.5555/thesis"), "locations": [{"landing_page_url": handle}]}
        cited = [{"openalex": "W60", "relation": "equivalent"}, {"doi": "10.5555/THESIS", "relation": "equivalent"},
                 {"openalex": "W2", "relation": "partial"}]
        out = self.clean({"/works/W60?": thesis, "/works/doi:10.5555/thesis?": thesis, **TestCleaning.OLD}, cited,
                         evidence=[{"kind": "fetched_page", "url": "https://openalex.org/W60"}], exclusions=[handle])
        self.assertEqual([c["leak"] for c in out["candidates"]], ["excluded_work", "excluded_work", None])
        self.assertEqual(out["evidence"][0]["leak"], "excluded_work")
        self.assertEqual(out["leakage"]["n_excluded"], 3)
        self.assertEqual(out["verdict"]["six"], "partial")


class Outage:
    """HTTP 503 for every URL containing one of `down` (every URL when `down` is empty), else the routed answer."""

    def __init__(self, routes: dict, down=()) -> None:
        self.routed, self.down = RoutedFetch(routes), tuple(down)
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if not self.down or any(k in url for k in self.down):
            return 503, {}, b"service unavailable"
        return self.routed(url, headers, timeout)


class TestResolutionOutage(ResolveCase):
    """An OpenAlex or arXiv API outage is an infrastructure error of the resolution, never a placeholder or a leak
    (finding 4)."""

    def outage_client(self, fetch) -> OpenAlexClient:
        return OpenAlexClient(api_key="", cache=self.cache, limiter=self.limiter, fetch=fetch, now=lambda: self.day,
                              max_retries=1)

    def assert_resolution_error(self, out):
        self.assertEqual((out["status"], out["failure"]), ("infra_error", True))
        self.assertTrue(out["resolution_error"])
        self.assertEqual((out["candidates"], out["top10"], out["leakage"]), (None, None, None))
        self.assertIsNone(out["verdict"]["class3"])

    def test_openalex_outage_is_no_placeholder_or_leak(self):
        r = row([{"doi": "10.1000/real", "year": 1963, "relation": "equivalent"}, {"title": "Undated memoir"}])
        out = clean(r, item(), self.outage_client(Outage({})), map_verdict=precedence, cfg=CFG)
        self.assert_resolution_error(out)
        self.assertIn("OpenAlex", out["resolution_error"])

    def test_exclusion_lookup_outage_is_a_resolution_error(self):
        fetch = Outage({"/works/W2?": oa_work("W2", "1970-02-01")}, down=["/works/W9"])
        out = clean(row([{"openalex": "W2", "relation": "partial"}]), item(["W9"]), self.outage_client(fetch),
                    map_verdict=precedence, cfg=CFG)
        self.assert_resolution_error(out)

    def test_arxiv_api_outage_is_a_resolution_error(self):
        self.arxiv.side_effect = OSError("connection reset by peer")
        out = self.clean({}, [{"url": "https://arxiv.org/abs/1303.6700", "relation": "equivalent"}])
        self.assert_resolution_error(out)
        self.assertIn("arXiv", out["resolution_error"])

    def test_resolve_run_marks_the_row_and_a_rerun_resolves_it(self):
        routes = {"/works/W2?": oa_work("W2", "1970-02-01")}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "outputs.jsonl").write_text(json.dumps(row([{"openalex": "W2", "relation": "partial"}])) + "\n",
                                                   encoding="utf-8")
            kwargs = {"items": [item(["W9"])], "configs": {"single-agent": CFG}, "map_verdict": precedence}
            first = resolve_run(run_dir, client=self.outage_client(Outage(routes)), **kwargs)
            down = [json.loads(x) for x in (run_dir / "resolved.jsonl").read_text(encoding="utf-8").splitlines()]
            second = resolve_run(run_dir, client=self.client(RoutedFetch(routes)), **kwargs)
            up = [json.loads(x) for x in (run_dir / "resolved.jsonl").read_text(encoding="utf-8").splitlines()]
            calls = [json.loads(x) for x in (run_dir / "resolve-calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((first["rows"], first["resolution_errors"], second["resolution_errors"]), (1, 1, 0))
        self.assertEqual((down[0]["status"], down[0]["candidates"]), ("infra_error", None))
        self.assertEqual((up[0]["status"], up[0]["resolution_error"], up[0]["top10"]), ("ok", None, ["W2"]))
        self.assertEqual([(c["is_error"], c["error"]) for c in calls if c["is_error"]],
                         [(True, "unavailable"), (True, "not_found")])


def search_trace(path: Path, searches, *, structured: bool = True) -> dict:
    """A stream-json trace in Claude Code's format (as in cache/runs/cli-probes/): one WebSearch tool_use per entry of
    `searches` (a list of URL lists), each answered by a tool_result whose text lists the links as JSON and then the
    model's summary; a WebFetch whose page text also mentions a link. Returns the outputs row's `raw` block."""
    events = [{"type": "system", "subtype": "init"}]
    for n, urls in enumerate(searches):
        tid = f"toolu_{n}"
        events.append({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Searching; compare https://doi.org/10.5555/model-text"},
            {"type": "tool_use", "id": tid, "name": "WebSearch", "input": {"query": f"query {n}"}}]}})
        links = [{"title": f"Result {i}", "url": u} for i, u in enumerate(urls)]
        text = (f'Web search results for query: "query {n}"\n\nLinks: {json.dumps(links)}\n\n'
                "Based on the search results, see https://doi.org/10.5555/summary-text for details.")
        event = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text}]}}
        if structured:
            event["tool_use_result"] = {"query": f"query {n}", "searchCount": 1,
                                        "results": [{"tool_use_id": f"srvtoolu_{n}", "content": links}, "summary"]}
        events.append(event)
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_f", "name": "WebFetch", "input": {"url": "https://example.org/page"}}]}})
    events.append({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_f",
         "content": 'Links: [{"url": "https://doi.org/10.5555/page"}]'}]}})
    events.append({"type": "result", "subtype": "success", "is_error": False, "result": "[]"})
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return {"trace_path": str(path), "trace_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class TestExposure(ResolveCase):
    """Decision 28: search-result entries that resolve to dated works give the share dated on or after claim.date,
    reported as exposure and never as leakage (findings 14 and 26)."""

    LISTED = ["https://doi.org/10.5555/later", "https://en.wikipedia.org/wiki/Kalman_filter",
              "https://doi.org/10.5555/earlier", "https://arxiv.org/pdf/1303.6700", "https://openalex.org/W32",
              "https://doi.org/10.5555/missing"]

    def test_search_result_urls_reads_the_listed_links_only(self):
        for structured in (True, False):
            path = Path(self.tmp.name) / f"trace-{structured}.jsonl"
            search_trace(path, [self.LISTED[:3], self.LISTED[2:]], structured=structured)
            self.assertEqual(resolve_mod.search_result_urls(path), self.LISTED, structured)
        self.assertEqual(resolve_mod.search_result_urls(Path(self.tmp.name) / "missing.jsonl"), [])

    def test_exposure_counts_dated_scholarly_results_with_free_lookups(self):
        self.arxiv.side_effect = arxiv_v1({"1303.6700": "2013-03-27"})
        raw = search_trace(Path(self.tmp.name) / "trace.jsonl", [self.LISTED[:3], self.LISTED[3:]])
        later = oa_work("W30", "2017-03-01", doi="10.5555/later")
        out = self.clean({"/works/doi:10.5555/later?": later,
                          "/works/doi:10.5555/earlier?": oa_work("W31", "1990-03-01", doi="10.5555/earlier"),
                          "/works/W32?": oa_work("W32", CLAIM), "/works/W2?": oa_work("W2", "1970-02-01"),
                          "search=A+title": {"results": [later, oa_work("W99", "1950-03-01")]}},
                         [{"openalex": "W2", "relation": "partial"}], raw=raw)
        self.assertEqual(out["exposure"], {"search_results": 6, "resolved": 4, "on_or_after_claim": 2})
        self.assertEqual(out["leakage"], {"hit_top10": False, "hit_evidence": False, "n_hits": 0, "n_undated": 0,
                                          "n_excluded": 0})
        self.assertEqual(out["evidence"], [])
        self.assertFalse([u for u in self.fetch.calls if "search=" in u])  # free routes only
        self.assertEqual(out["resolver"]["title_searches"], 0)

    def test_no_trace_gives_no_exposure(self):
        self.assertIsNone(self.clean({}, [])["exposure"])
        raw = search_trace(Path(self.tmp.name) / "trace.jsonl", [])
        self.assertEqual(self.clean({}, [], raw=raw)["exposure"],
                         {"search_results": 0, "resolved": 0, "on_or_after_claim": 0})
        self.assertIsNone(self.clean({}, [], raw={**raw, "trace_sha256": "0" * 64})["exposure"])


if __name__ == "__main__":
    unittest.main()
