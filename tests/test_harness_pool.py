"""Tests for pooling rounds (docs/design/harness.md section 8.10). No OpenAlex call is made: a fake client returns
work records, and the abstract and arXiv caches are patched.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth.benchmark.items import FOUND_BY_RE, validate
from coelacanth.harness import pool
from coelacanth.llm import read_jsonl
from coelacanth.quotes import QuoteChecker

SINGLE = ("single-agent", "text", "single-agent.text.T1.20261020T000000Z", "a" * 64)
INC = ("inc-style", "skeleton", "inc-style.skeleton.T3.20261021T000000Z", "b" * 64)
ARXIV_WORK = {"openalex": None, "doi": "https://doi.org/10.48550/arxiv.2101.00001", "arxiv": "2101.00001",
              "pmid": None, "title": "A preprint", "first_author": "Ann Example", "n_authors": 1, "year": 1961,
              "publication_date": "1961-01-01", "venue": "arXiv", "field": None, "domain": None, "oa_url": None,
              "cache_key": "arxivabs-2101.00001"}


class FakeClient:
    cache = None

    def __init__(self):
        self.calls = []

    def work(self, wid, select=None):
        self.calls.append(wid)
        return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/10.1/{wid.lower()}",
                "title": f"Work number {wid[1:]}", "publication_date": "1960-01-01", "publication_year": 1960,
                "authorships": [{"author": {"display_name": "Ann Example"}}],
                "primary_location": {"source": {"display_name": "Journal"}},
                "primary_topic": {"field": {"display_name": "Mathematics"},
                                  "domain": {"display_name": "Physical Sciences"}}}


def kept(rank, wid=None, arxiv=None):
    return {"rank_raw": rank, "openalex": wid, "doi": None, "arxiv": arxiv,
            "resolved_by": "openalex_id" if wid else "arxiv", "date": "1960-01-01", "date_source": "openalex",
            "relation_native": "partial", "leak": None, "duplicate_of": None, "placeholder": False, "rank_clean": rank}


def removed(rank_raw, wid=None, **fields):
    return {**kept(rank_raw, wid), "rank_clean": None, **fields}


def item(item_id, candidates=()):
    return {"id": item_id, "version": "v0", "status": "pooled",
            "claim": {"text": "Integrate a sampled curve by adding the strips between neighbouring samples.",
                      "date": "1999-05-01", "source_work": {"openalex": "W1"}},
            "candidates": list(candidates), "verdict": None, "pool": None, "exclusions": ["W1"]}


ITEMS = [item("cf-0001", [{"work": {"doi": "10.1/W103"}, "found_by": ["annotator:a1"], "relation": None}]),
         item("cf-0002")]


class PoolTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config-hashes.jsonl").write_text(
            "".join(json.dumps({"system": s, "config_sha256": h}) + "\n" for s, _, _, h in (SINGLE, INC)),
            encoding="utf-8")
        cf2 = [kept(1, "https://openalex.org/W9"),
               removed(2, placeholder=True, resolved_by="none", rank_clean=2, date=None),
               kept(3, "W10"), removed(4, "W50", leak="on_or_after_claim_date"),
               removed(5, "W60", leak="excluded_work"), removed(6, "W9", duplicate_of=1),
               removed(7, resolved_by="none", leak="year_only_not_before", date=None),
               {**kept(8, arxiv="2101.00001"), "rank_clean": 4}]
        self.single = self.run_dir(SINGLE, {"cf-0001": [kept(r, f"W{100 + r}") for r in range(1, 13)],
                                            "cf-0002": cf2})
        self.inc = self.run_dir(INC, {"cf-0001": [kept(1, "W105")] + [kept(r, f"W{198 + r}") for r in range(2, 9)]})

    def tearDown(self):
        self.tmp.cleanup()

    def run_dir(self, run, rows, kind="scored", config_sha256=None, fields=None):
        """`fields` maps an item ID to extra resolved-row fields (status, failure, resolution_error)."""
        system, condition, run_id, sha = run
        path = self.root / "runs" / system / condition / run_id
        path.mkdir(parents=True)
        (path / "run.json").write_text(json.dumps({"run_id": run_id, "system": system, "condition": condition,
                                                   "kind": kind, "config_sha256": config_sha256 or sha}),
                                       encoding="utf-8")
        (path / "resolved.jsonl").write_text("".join(json.dumps(
            {"format": "coelacanth-resolved/v1", "run_id": run_id, "system": system, "condition": condition,
             "item": item_id, "claim_date": "1999-05-01", "config_sha256": sha, "status": "ok", "failure": False,
             "candidates": cands, **(fields or {}).get(item_id, {})}) + "\n" for item_id, cands in rows.items()),
            encoding="utf-8")
        return path

    def build(self, run_dirs=None, client=None, **kwargs):
        with mock.patch("coelacanth.benchmark.texts.cache_abstract",
                        side_effect=lambda work: "oa-" + work["id"].rsplit("/", 1)[1]), \
                mock.patch("coelacanth.benchmark.texts.arxiv_work", return_value=(ARXIV_WORK, "arxivabs-2101.00001")):
            return pool.build_pool(1, run_dirs or [self.single, self.inc], ITEMS, client=client or FakeClient(),
                                   root=self.root, **{"skeleton_depth": 5,
                                                      "closed_book_works": [("cf-0002", "W7"), ("cf-0001", "W101")],
                                                      **kwargs})

    def files(self):
        out = self.root / "pool" / "round1"
        return (read_jsonl(out / "pool.jsonl"), read_jsonl(out / "pool-meta.jsonl"),
                read_jsonl(out / "exclusions.jsonl"), json.loads((out / "pool-manifest.json").read_text()),
                json.loads((out / "exclusion-sample.json").read_text()))


class TestFoundBy(PoolTestCase):
    def test_found_by_matches_FOUND_BY_RE(self):
        self.assertEqual(pool.found_by("single-agent", "text", 2), "sys:single-agent@2")
        self.assertEqual(pool.found_by("inc-style", "skeleton", 7), "sys:inc-style.skeleton@7")
        for args in (("refpipe.v0", "text", 1), ("single agent", "text", 1), ("single-agent", "text", 0)):
            with self.assertRaises(ValueError):
                pool.found_by(*args)
        self.build()
        _, meta, _, _, _ = self.files()
        tags = [t for m in meta for t in m["found_by"]]
        self.assertTrue(tags and all(FOUND_BY_RE.fullmatch(t) for t in tags))
        self.assertIn(pool.CLOSED_BOOK_TAG, tags)


class TestBuildPool(PoolTestCase):
    def test_top10_of_each_run_and_condition(self):
        self.build()
        _, meta, _, manifest, _ = self.files()
        found = {m["openalex"]: m["found_by"] for m in meta if m["item"] == "cf-0001"}
        self.assertEqual(sorted(found, key=lambda w: int(w[1:])),
                         [f"W{n}" for n in range(101, 111)] + ["W200", "W201", "W202", "W203"])
        self.assertEqual(found["W105"], ["sys:inc-style.skeleton@1", "sys:single-agent@5"])
        self.assertEqual(found["W101"], ["sys:single-agent@1", "mining:closedbook"])
        self.assertEqual(found["W110"], ["sys:single-agent@10"])
        self.assertEqual((manifest["depth"], manifest["skeleton_depth"]), (10, 5))
        self.assertEqual([r["run_id"] for r in manifest["runs"]], [INC[2], SINGLE[2]])

    def test_existing_candidate_is_matched_not_sent_to_judges(self):
        self.build()
        pooled, meta, _, manifest, _ = self.files()
        w103 = next(m for m in meta if m["openalex"] == "W103")
        self.assertEqual(w103["matched_existing_candidate"], 0)
        self.assertNotIn("W103", [r["work"]["openalex"] for r in pooled])
        self.assertEqual(manifest["per_item"]["cf-0001"]["n_matched_existing"], 1)
        self.assertEqual(manifest["per_item"]["cf-0001"]["n_pooled"], 13)

    def test_judge_file_has_no_system_rank_or_rationale(self):
        self.build()
        pooled, _, _, _, _ = self.files()
        self.assertTrue(pooled)
        self.assertTrue(all(set(r) == {"item", "work"} for r in pooled))
        text = (self.root / "pool" / "round1" / "pool.jsonl").read_text(encoding="utf-8")
        for hidden in ("sys:", "single-agent", "inc-style", "skeleton", "found_by", "rank", "relation", "why",
                       "closedbook", "mining", "headline", "partial"):
            self.assertNotIn(hidden, text)
        self.assertEqual(pooled[0]["work"]["cache_key"], "oa-" + pooled[0]["work"]["openalex"])

    def test_sorted_by_openalex_id(self):
        self.build()
        pooled, _, _, _, _ = self.files()
        self.assertEqual([r["item"] for r in pooled], sorted(r["item"] for r in pooled))
        cf2 = [r["work"]["openalex"] or r["work"]["arxiv"] for r in pooled if r["item"] == "cf-0002"]
        self.assertEqual(cf2, ["W7", "W9", "W10", "2101.00001"])
        cf1 = [int(r["work"]["openalex"][1:]) for r in pooled if r["item"] == "cf-0001"]
        self.assertEqual(cf1, sorted(cf1))

    def test_placeholders_not_pooled_but_counted(self):
        result = self.build()
        _, meta, exclusions, manifest, sample = self.files()
        self.assertEqual(manifest["per_item"]["cf-0002"]["auto_removed"],
                         {"unresolved": 2, "excluded_work": 1, "leakage": 1, "duplicate": 1})
        self.assertEqual(sorted((e["rank_raw"], e["reason"]) for e in exclusions if e["item"] == "cf-0002"),
                         [(2, "unresolved"), (4, "leakage"), (5, "excluded_work"), (6, "duplicate"),
                          (7, "unresolved")])
        self.assertEqual({m["openalex"] for m in meta if m["item"] == "cf-0002"}, {"W7", "W9", "W10", None})
        self.assertEqual(manifest["per_item"]["cf-0002"]["n_pooled"], 4)
        self.assertEqual(sample, pool.exclusion_sample(result))
        self.assertEqual(len(sample), 5)

    def test_pilot_and_unlogged_runs_refused(self):
        pilot = self.run_dir(("single-agent", "text", "pilot-1", "a" * 64), {"cf-0001": [kept(1, "W1")]},
                             kind="pilot")
        unlogged = self.run_dir(("single-agent", "text", "run-x", "a" * 64), {"cf-0001": [kept(1, "W1")]},
                                config_sha256="c" * 64)
        for bad in (pilot, unlogged):
            with self.assertRaises(ValueError):
                self.build([self.single, bad])
        self.assertFalse((self.root / "pool").exists())

    def test_existing_round_is_never_rewritten(self):
        self.build()
        with self.assertRaises(FileExistsError):
            self.build()

    def test_merged_item_passes_validate_rule_10(self):
        self.build()
        pooled, meta, _, manifest, _ = self.files()
        tags = {(m["item"], m["openalex"] or m["arxiv"]): m["found_by"] for m in meta}
        merged = item("cf-0002")
        merged["candidates"] = [{"work": r["work"], "relation": None, "gold": False,
                                 "found_by": tags[("cf-0002", r["work"]["openalex"] or r["work"]["arxiv"])]}
                                for r in pooled if r["item"] == "cf-0002"]
        merged["pool"] = {"depth": manifest["depth"], "systems": [r["system"] for r in manifest["runs"]],
                          "n_pooled": len(merged["candidates"]), "n_unjudged": len(merged["candidates"])}
        quotes = QuoteChecker(lambda key: None)
        self.assertEqual(validate(merged, quotes), [])
        merged["status"] = "adjudicated"
        self.assertTrue(any(p.startswith("rule 10:") for p in validate(merged, quotes)))


class PreprintClient(FakeClient):
    """W500 is the OpenAlex record of the arXiv preprint 2101.00001 (an arXiv landing page among its locations)."""

    def work(self, wid, select=None):
        work = super().work(wid, select)
        if wid == "W500":
            work["locations"] = [{"landing_page_url": "https://arxiv.org/abs/2101.00001"}]
        return work


class TestRowStatus(PoolTestCase):
    """Only valid lists are pooled (protocol 3 and 12, decision 33; harness fix review, findings 15 and 23)."""

    def test_rows_due_for_a_rerun_are_refused(self):
        for n, fields in enumerate(({"status": "infra_error", "failure": True},
                                    {"status": "model_mismatch", "failure": True},
                                    {"status": "infra_error", "failure": True, "resolution_error": "OpenAlex: 503"})):
            bad = self.run_dir(("inc-style", "skeleton", f"inc-style.skeleton.T3.r{n}", "b" * 64),
                               {"cf-0002": [kept(1, "W300")]}, fields={"cf-0002": fields})
            with self.assertRaisesRegex(ValueError, "re-run"):
                self.build([self.single, bad])
        self.assertFalse((self.root / "pool").exists())

    def test_failed_rows_are_not_pooled(self):
        capped = self.run_dir(("inc-style", "skeleton", "inc-style.skeleton.T3.capped", "b" * 64),
                              {"cf-0002": [kept(1, "W300"), removed(2, "W301", leak="on_or_after_claim_date")]},
                              fields={"cf-0002": {"status": "capped", "failure": True}})
        self.build([self.single, self.inc, capped])
        pooled, meta, exclusions, manifest, _ = self.files()
        self.assertNotIn("W300", [m["openalex"] for m in meta])
        self.assertNotIn("W301", [e["openalex"] for e in exclusions])
        self.assertEqual(manifest["per_item"]["cf-0002"]["n_pooled"], 4)
        self.assertEqual({r["run_id"]: r["skipped_failures"] for r in manifest["runs"]},
                         {INC[2]: 0, "inc-style.skeleton.T3.capped": 1, SINGLE[2]: 0})

    def test_a_second_run_of_the_same_system_and_condition_on_an_item_is_refused(self):
        again = self.run_dir(("single-agent", "text", "single-agent.text.T1.again", "a" * 64),
                             {"cf-0001": [kept(1, "W900")]})
        with self.assertRaisesRegex(ValueError, "twice"):
            self.build([self.single, again])

    def test_one_work_under_w_id_and_arxiv_id_is_pooled_once(self):
        a = self.run_dir(("single-agent", "text", "single-agent.text.T1.w", "a" * 64), {"cf-0002": [kept(1, "W500")]})
        b = self.run_dir(("inc-style", "skeleton", "inc-style.skeleton.T3.x", "b" * 64),
                         {"cf-0002": [{**kept(1, arxiv="2101.00001"), "resolved_by": "arxiv"}]})
        self.build([a, b], client=PreprintClient(), closed_book_works=[])
        pooled, meta, _, manifest, _ = self.files()
        self.assertEqual([(m["openalex"], m["arxiv"], m["found_by"]) for m in meta],
                         [("W500", "2101.00001", ["sys:single-agent@1", "sys:inc-style.skeleton@1"])])
        self.assertEqual(len(pooled), 1)
        self.assertEqual((manifest["per_item"]["cf-0002"]["n_pooled"], manifest["per_item"]["cf-0002"]["n_merged"]),
                         (1, 1))


class TestExclusionSample(unittest.TestCase):
    def test_seeded_sample_of_20(self):
        rows = [{"item": f"cf-{i:04d}", "run_id": "r", "rank_raw": 1, "reason": "leakage"} for i in range(30)]
        first = pool.exclusion_sample({"exclusions": rows})
        self.assertEqual(len(first), 20)
        self.assertEqual(first, pool.exclusion_sample({"exclusions": list(reversed(rows))}))
        self.assertNotEqual(first, pool.exclusion_sample({"exclusions": rows}, seed=1))
        self.assertTrue(all(r in rows for r in first))


if __name__ == "__main__":
    unittest.main()
