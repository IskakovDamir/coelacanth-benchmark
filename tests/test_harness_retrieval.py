"""Tests for the harness's retrieval side: exclusion matching, the bound OpenAlex search, and the framework the
re-implemented checkers run in (item budget, one tool-free step, merging sub-calls, JSON parsing).
No network and no model: a scripted fetch stands in for OpenAlex and a fake for the CLI.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import re
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coelacanth.data.cache import ResponseCache
from coelacanth.data.openalex import OpenAlexClient, normalize_work_id
from coelacanth.data.ratelimit import RateLimiter
from coelacanth.env import REPO_ROOT
from coelacanth.harness.reimpl import ItemBudget, llm_step, merge, parse_json
from coelacanth.harness.retrieval import bound_search, canonical_id, exclusion_keys, work_keys
from coelacanth.pricing import cli_cost

B01_META = REPO_ROOT / "eval" / "benchmark" / "v0" / "batches" / "b01-meta.json"


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


def oa_work(wid, date=None, *, year=None, doi=None, title="A title", author="Ann Smith", arxiv=None, abstract=None):
    """An OpenAlex work with the fields the harness reads."""
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title,
            "publication_date": date, "publication_year": year or (int(date[:4]) if date else None),
            "ids": {"openalex": f"https://openalex.org/{wid}"},
            "authorships": [{"author": {"display_name": author}}],
            "locations": [{"landing_page_url": f"https://arxiv.org/abs/{arxiv}"}] if arxiv else [],
            "abstract_inverted_index": ({w: [i] for i, w in enumerate(abstract.split())} if abstract else None)}


class ClientCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        day = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        self.cache = ResponseCache(Path(self.tmp.name) / "http.sqlite", now=lambda: day)
        self.limiter = RateLimiter(1000, clock=lambda: 0.0, sleep=lambda s: None)
        self.day = day

    def tearDown(self) -> None:
        self.cache.close()
        self.tmp.cleanup()

    def client(self, fetch) -> OpenAlexClient:
        return OpenAlexClient(api_key="", cache=self.cache, limiter=self.limiter, fetch=fetch, now=lambda: self.day)


def _form(value: str) -> str:
    if re.fullmatch(r"W\d+", value):
        return "W ID"
    if value.lower().startswith("https://doi.org/"):
        return "DOI URL"
    if value.startswith("10."):
        return "bare DOI"
    return "handle URL" if "hdl.handle.net" in value else "other URL"


def _respelled(value: str) -> str:
    """The same identifier written another way, as a system might give it."""
    form = _form(value)
    if form == "W ID":
        return "https://openalex.org/" + value.lower()
    if form == "DOI URL":
        return "DOI: " + value[len("https://doi.org/"):].upper()
    if form == "bare DOI":
        return "http://dx.doi.org/" + value.upper()
    return value.replace("https://", "http://").replace("www.", "") + "/"


@unittest.skipUnless(B01_META.exists(), "needs the data archive (eval/benchmark/v0/batches)")
class TestExclusionForms(unittest.TestCase):
    def setUp(self) -> None:
        meta = json.loads(B01_META.read_text(encoding="utf-8"))
        self.exclusions = [e for item in meta["items"].values() for e in item["exclusions"]]
        self.keys = exclusion_keys(self.exclusions)

    def test_b01_exclusion_forms_are_all_matched(self):
        forms = Counter(_form(e) for e in self.exclusions)
        self.assertEqual(set(forms), {"W ID", "DOI URL", "bare DOI", "handle URL", "other URL"})
        self.assertTrue(any(_form(e) == "DOI URL" and e != e.lower() for e in self.exclusions))  # mixed case
        with self.assertRaises(ValueError):  # why matching needs its own canonical form (spec F8)
            normalize_work_id(next(e for e in self.exclusions if _form(e) == "handle URL"))
        for e in self.exclusions:
            self.assertIn(canonical_id(_respelled(e)), self.keys, e)
            field = {"W ID": "openalex", "bare DOI": "doi"}.get(_form(e), "url")
            self.assertTrue(work_keys({field: _respelled(e)}) & self.keys, e)

    def test_arxiv_doi_url_and_id_share_one_key(self):
        self.assertIn("https://doi.org/10.48550/arXiv.2609.07595", self.exclusions)
        for spelling in ("arXiv:2609.07595v2", "https://arxiv.org/abs/2609.07595",
                         "https://arxiv.org/pdf/2609.07595v1.pdf", "10.48550/ARXIV.2609.07595", "2609.07595"):
            self.assertEqual(canonical_id(spelling), "arxiv:2609.07595", spelling)
        self.assertEqual(canonical_id("https://doi.org/10.48550/arXiv.cond-mat/9804229"), "arxiv:cond-mat/9804229")
        self.assertIn("arxiv:cond-mat/9804229", self.keys)

    def test_other_identifiers_and_unmatched_works(self):
        self.assertEqual(canonical_id("https://pubmed.ncbi.nlm.nih.gov/9312132/"), "pmid:9312132")
        self.assertEqual(canonical_id("PMID: 9312132"), "pmid:9312132")
        self.assertEqual(canonical_id("not an identifier at all"), "url:not an identifier at all")
        self.assertFalse(work_keys({"doi": "10.9999/unrelated", "openalex": "W1", "url": "https://example.org/x"})
                         & self.keys)


class TestExclusionLookups(ClientCase):
    def test_w_id_adds_its_doi_and_doi_its_w_id(self):
        fetch = RoutedFetch({"/works/W1?": oa_work("W1", "2001-01-01", doi="10.5555/a"),
                             "/works/doi:10.5555/b?": oa_work("W2", "2002-01-01", doi="10.5555/b", arxiv="0801.0001")})
        keys = exclusion_keys(["W1", "https://doi.org/10.5555/B", "https://hdl.handle.net/1/2", "W404"],
                              self.client(fetch))
        self.assertTrue({"openalex:W1", "doi:10.5555/a", "openalex:W2", "doi:10.5555/b", "arxiv:0801.0001",
                         "url:hdl.handle.net/1/2", "openalex:W404"} <= keys)
        self.assertEqual(len(fetch.calls), 3)  # the handle URL is never sent to OpenAlex


class TestBoundSearch(ClientCase):
    def test_filter_is_the_day_before(self):
        for claim, before in (("2019-03-14", "2019-03-13"), ("2020-03-01", "2020-02-29"), ("2019-01-01", "2018-12-31")):
            fetch = RoutedFetch({"search=": {"results": []}})
            search = bound_search(self.client(fetch), claim_date=claim, exclusions=[], counts={})
            search(f"kalman {claim}")
            self.assertIn(f"filter=to_publication_date:{before}", fetch.calls[-1])

    def test_year_only_date_in_the_claim_year_dropped(self):
        claim = "2019-03-14"
        results = [oa_work("W1", "2019-01-01"), oa_work("W2", "2018-01-01"), oa_work("W3", "2019-02-01")]
        fetch = RoutedFetch({"search=": {"results": results}})
        kept = bound_search(self.client(fetch), claim_date=claim, exclusions=[], counts={})("anything")
        self.assertEqual([w["openalex"] for w in kept], ["W2", "W3"])

    def test_same_day_result_dropped(self):
        claim = "2019-03-14"
        results = [oa_work("W1", claim), oa_work("W2", "2019-03-13", abstract="an earlier filter"),
                   oa_work("W3", None), oa_work("W4", "2019-07-01"), oa_work("W5", "1990-01-01", doi="10.5555/x5")]
        fetch = RoutedFetch({"search=": {"results": results}})
        counts, rows = {}, []
        search = bound_search(self.client(fetch), claim_date=claim, exclusions=["https://doi.org/10.5555/X5"],
                              counts=counts, log=rows.append)
        kept = search("recursive state estimation")
        self.assertEqual([w["openalex"] for w in kept], ["W2"])
        self.assertEqual(kept[0]["abstract"], "an earlier filter")
        search("recursive state estimation")  # answered by the cache, still counted at list price
        self.assertEqual(counts, {"search": 2, "usd": 0.002})
        self.assertEqual(len([u for u in fetch.calls if "search=" in u]), 1)
        self.assertEqual([r["n_kept"] for r in rows], [1, 1])
        shown = json.dumps(kept) + repr(search) + json.dumps(rows)
        for hidden in (claim, "10.5555/x5", "recursive"):
            self.assertNotIn(hidden, shown)


def fake_cli(costs):
    """CLI outputs whose Sonnet 5.5 input tokens cost the given dollars, in order ($2 per million tokens)."""
    outs = iter(costs)

    def call(model, message, **kwargs):
        cost = next(outs)
        return {"is_error": False, "result": f'```json\n{{"step_cost": {cost}}}\n```', "num_turns": 1,
                "subtype": "success", "modelUsage": {model: {"inputTokens": int(cost * 500_000)}}}
    return call


class TestReimplFramework(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        prompt = Path(self.tmp.name) / "step.txt"
        prompt.write_text("Claim: $text\nWrite $n queries.", encoding="utf-8")
        self.cfg = {"system": "inc-style", "model": "claude-sonnet-5-5", "effort": "high", "timeout_s": 60,
                    "steps": [{"name": "inc.queries", "prompt": {"path": str(prompt), "sha256": "x"}},
                              {"name": "inc.rerank", "prompt": {"path": str(prompt), "sha256": "x"}, "cap_usd": 0.25}]}
        self.rows: list[dict] = []
        self.ctx = SimpleNamespace(cwd=self.tmp.name, log_call=self.rows.append)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reimpl_steps_share_one_item_cap(self):
        budget = ItemBudget(0.5)
        fields = {"text": "a field-native claim", "n": 3}
        with mock.patch("coelacanth.llm.cli_call", side_effect=fake_cli([0.3, 0.3])) as cli:
            first, out1 = llm_step(self.cfg, "inc.queries", fields, budget, self.ctx)
            second, out2 = llm_step(self.cfg, "inc.rerank", fields, budget, self.ctx)
            third, out3 = llm_step(self.cfg, "inc.queries", fields, budget, self.ctx)
        self.assertEqual(cli.call_count, 2)
        self.assertEqual([c.kwargs["max_budget_usd"] for c in cli.call_args_list], [0.5, 0.2])
        self.assertTrue(all(c.kwargs["tools"] == () for c in cli.call_args_list))
        self.assertEqual(cli.call_args_list[0].args[1], "Claim: a field-native claim\nWrite 3 queries.")
        self.assertEqual(first, {"step_cost": 0.3})
        self.assertTrue(budget.exhausted)
        self.assertEqual(budget.remaining, 0.0)
        self.assertIsNone(third)
        self.assertTrue(out3["is_error"] and out3["budget_exhausted"])
        self.assertEqual([(r["step"], r["kind"], r["cost_usd"]) for r in self.rows],
                         [("inc.queries", "llm", 0.3), ("inc.rerank", "llm", 0.3)])
        self.assertAlmostEqual(self.rows[0]["budget_left_usd"], 0.2)
        merged = merge([out1, out2, out3])
        self.assertTrue(merged["is_error"] and merged["budget_exhausted"])
        self.assertEqual(merged["llm_calls"], 2)
        self.assertAlmostEqual(cli_cost(merged), 0.6)

    def test_item_budget(self):
        budget = ItemBudget(0.4)
        self.assertEqual(budget.call_cap(), 0.4)
        self.assertEqual(budget.call_cap(0.1), 0.1)
        budget.charge(0.4069)  # the CLI's own stop overshoots its cap (6 Oct ledger)
        self.assertTrue(budget.exhausted)
        self.assertEqual(budget.remaining, 0.0)
        self.assertEqual(budget.call_cap(0.1), 0.0)
        self.assertTrue(ItemBudget(0.0).exhausted)  # a placeholder cap never calls

    def test_timed_out_step_is_charged_its_cap(self):
        budget = ItemBudget(0.5)
        with mock.patch("coelacanth.llm.cli_call", return_value={"is_error": True, "result": "timed out",
                                                                 "timed_out": True}):
            parsed, out = llm_step(self.cfg, "inc.rerank", {"text": "t", "n": 1}, budget, self.ctx)
        self.assertIsNone(parsed)
        self.assertEqual(out["booked_cost"], {"usd": 0.25, "source": "upper_bound"})
        self.assertAlmostEqual(budget.spent, 0.25)
        self.assertEqual(self.rows[0]["cost_source"], "upper_bound")
        self.assertEqual(merge([out])["booked_cost"], {"usd": 0.25, "source": "upper_bound"})

    def test_missing_template_field_raises_before_any_call(self):
        with mock.patch("coelacanth.llm.cli_call") as cli, self.assertRaises(KeyError):
            llm_step(self.cfg, "inc.queries", {"text": "t"}, ItemBudget(1.0), self.ctx)
        cli.assert_not_called()
        with self.assertRaises(KeyError):
            llm_step(self.cfg, "no.such.step", {}, ItemBudget(1.0), self.ctx)


class TestMerge(unittest.TestCase):
    def test_model_usage_summed_per_model_and_field(self):
        a = {"is_error": False, "result": "first", "num_turns": 1, "subtype": "success", "total_cost_usd": 0.01,
             "wall_seconds": 2.0, "usage": {"input_tokens": 10, "output_tokens": 5},
             "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 1000, "outputTokens": 200, "cacheReadInputTokens": 50,
                                                  "costUSD": 0.004, "contextWindow": 1000000,
                                                  "canonicalModel": "claude-sonnet-5-5"}}}
        b = {"is_error": True, "result": "second", "num_turns": 2, "subtype": "error_max_budget_usd",
             "total_cost_usd": 0.02, "wall_seconds": 3.5, "usage": {"input_tokens": 20},
             "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 3000, "outputTokens": 100, "contextWindow": 1000000},
                            "claude-haiku-4-5-20251001": {"inputTokens": 7}}}
        c = {"is_error": False, "result": "third", "num_turns": 1, "subtype": "success",
             "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 1}}}
        m = merge([a, b, c])
        sonnet = m["modelUsage"]["claude-sonnet-5-5"]
        self.assertEqual((sonnet["inputTokens"], sonnet["outputTokens"], sonnet["cacheReadInputTokens"]),
                         (4001, 300, 50))
        self.assertAlmostEqual(sonnet["costUSD"], 0.004)
        self.assertEqual(sonnet["contextWindow"], 1000000)
        self.assertEqual(sonnet["canonicalModel"], "claude-sonnet-5-5")
        self.assertEqual(m["modelUsage"]["claude-haiku-4-5-20251001"], {"inputTokens": 7})
        self.assertTrue(m["is_error"])
        self.assertEqual((m["result"], m["subtype"], m["num_turns"]), ("third", "error_max_budget_usd", 4))
        self.assertEqual(m["usage"], {"input_tokens": 30, "output_tokens": 5})
        self.assertAlmostEqual(m["total_cost_usd"], 0.03)
        self.assertAlmostEqual(m["wall_seconds"], 5.5)
        self.assertIsNone(m["booked_cost"])
        self.assertEqual(m["llm_calls"], 3)
        self.assertAlmostEqual(cli_cost(m), cli_cost(a) + cli_cost(b) + cli_cost(c))

    def test_all_steps_ok_is_ok(self):
        m = merge([{"is_error": False, "result": "x", "subtype": "success"}])
        self.assertFalse(m["is_error"])
        self.assertEqual(m["subtype"], "success")


class TestParseJson(unittest.TestCase):
    def test_last_fenced_block_wins_over_later_prose(self):
        text = 'Plan:\n```json\n[1]\n```\nThen:\n```json\n{"label": "novel"}\n```\nAs noted in [3].'
        self.assertEqual(parse_json(text), {"label": "novel"})

    def test_bare_json_whole_object_not_its_inner_parts(self):
        self.assertEqual(parse_json('Answer: {"a": [1, {"b": 2}]} done'), {"a": [1, {"b": 2}]})
        self.assertEqual(parse_json('first {"a": 1} then [2, 3]'), [2, 3])

    def test_unparsable_fenced_block_falls_back_to_bare_text(self):
        self.assertEqual(parse_json('```json\n{not json}\n```\n{"ok": true}'), {"ok": True})

    def test_none_when_no_object_or_list(self):
        for text in (None, "", "no json here", '{"broken": ', "42", '"a string"'):
            self.assertIsNone(parse_json(text), text)


if __name__ == "__main__":
    unittest.main()
