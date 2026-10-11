"""Tests for tools/freeze/freeze_items.py on small synthetic data (no repository files are read, no network).

    PYTHONPATH=src python -m unittest tests.test_freeze_items -v
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "freeze"))

import freeze_items as fi  # noqa: E402
from coelacanth.harness import run  # noqa: E402

MODELS = ["claude-sonnet-5-5", "claude-haiku-4-5-20251001"]


def packet(pid, cands, date="2015-05-05", text=None):
    return {"id": pid, "prefill": {"by": "claude-opus-5-5", "status": "draft"},
            "claim": {"text": text or f"draft text of {pid}", "skeleton": f"neutral skeleton of {pid}",
                      "coined_names": [], "date": date, "field": "F", "domain": "D", "computational": True,
                      "source_work": {"openalex": "W" + pid[-4:], "title": "a source", "first_author": "Aaa",
                                      "year": 2015, "publication_date": date, "cache_key": "oa-s",
                                      "method_text": "SECRET METHOD TEXT"},
                      "chart": [{"id": "e1", "text": "x", "key": None}, {"id": "e2", "text": "y", "key": None}]},
            "candidates": [{"cid": cid, "work": {"openalex": f"W9{pid[-2:]}{n}", "title": "cand", "first_author": "Bbb",
                                                 "year": 1990, "publication_date": "1990-01-01"},
                            "evidence": [], "attribution_hint": hint} for n, (cid, hint) in enumerate(cands)]}


def meta(role=None, source="kulikowski", origin="human", negative=None):
    return {"found_by": {"c1": ["mining:x"], "c2": ["mining:x"]}, "exclusions": ["W0"],
            "provenance": {"source": source, "mined_by": "manual"}, "exposure": {"kind": "catalogue"},
            "origin": {"kind": origin, "system": "gen" if origin == "ai" else None, "mode": None},
            "negative": negative, "seed_detail": {"role": role}}


def pair(item, cid, relation, cross, gold=None, excluded=False):
    gold = relation in fi.PRECEDENCE if gold is None else gold
    return {"item": item, "candidate": cid, "batch": "b01", "relation": relation, "cross_field": cross,
            "excluded": excluded, "gold": gold and not excluded,
            "decided_by": {"relation": "agreed: a1,a2", "cross_field": "adjudicated: damir"}}


def synthetic_inputs(named_final=None, closed_book="default", k=("k-1", "k-2")):
    packets = [
        packet("cf-0001", [("c1", "not_cited"), ("c2", "not_cited")], date="2010-01-01"),  # R, in H
        packet("cf-0002", [("c1", "not_cited")], date="2011-01-01"),                        # R, recalled
        packet("cf-0003", [("c1", "not_cited")]),                                           # gold but cited: not R
        packet("cf-0004", [("c1", "not_cited")]),                                           # attributed control
        packet("cf-0005", [("c1", "not_cited"), ("c2", "not_cited")]),                      # natural distractor
        packet("cf-0006", [("c1", "not_cited")]),                                           # positive, nothing close
        packet("cf-0007", [("c1", "not_cited")]),                                           # only candidate excluded
        packet("cf-0008", []),                                                              # AI origin
        packet("cf-0009", [("c1", "not_cited")]),                                           # built negative with gold
    ]
    metas = {"cf-0001": meta("non_attributed"), "cf-0002": meta("non_attributed"), "cf-0003": meta("non_attributed"),
             "cf-0004": meta("attributed_control"),
             "cf-0005": meta("natural_distractor", negative={"kind": "natural_distractor"}),
             "cf-0006": meta("non_attributed"), "cf-0007": meta(None, source="declaration"),
             "cf-0008": meta("ai_origin", source="ai_origin", origin="ai"),
             "cf-0009": meta("natural_distractor", negative={"kind": "natural_distractor"})}
    pairs = [pair("cf-0001", "c1", "partial", True), pair("cf-0001", "c2", "different", True),
             pair("cf-0002", "c1", "partial", True), pair("cf-0003", "c1", "partial", True),
             pair("cf-0004", "c1", "partial", True), pair("cf-0005", "c1", "different", True),
             pair("cf-0005", "c2", "different", False), pair("cf-0006", "c1", "different", False),
             pair("cf-0007", "c1", None, False, excluded=True), pair("cf-0009", "c1", "partial", False)]
    attribution = {"R": ["cf-0001", "cf-0002"], "R_final": True, "pending": [],
                   "pairs": {"cf-0003/c1": {"value": "cited", "decided_by": "agreed: a2,a3"}}}
    texts = {"cf-0001": {"text": "checked text one", "checked_by": "annotator:a1", "decided_by": "damir",
                         "changed": True}}
    full = {f"{m}:{p}": n for m in MODELS for p, n in fi.EXPECTED_SAMPLES.items()}
    if closed_book == "default":
        closed_book = {
            "cf-0001": {"cross_low": [], "cross_high": [], "any_low": [], "any_high": [], "valid": dict(full),
                        "models_without_valid": []},
            "cf-0002": {"cross_low": ["claude-sonnet-5-5:frozen"], "cross_high": ["claude-sonnet-5-5:frozen"], "any_low": ["claude-sonnet-5-5:frozen"],
                        "any_high": ["claude-sonnet-5-5:frozen"], "valid": dict(full), "models_without_valid": []}}
    return fi.Inputs(batches={"b01": {"batch": "b01", "items": packets}}, metas={"b01": {"items": metas}},
                     labels={"pairs": pairs}, texts=texts, attribution=attribution, named_final=named_final,
                     closed_book=closed_book, models=MODELS, k_queries=list(k) if k is not None else None,
                     hashes={"x": "0"})


def named_final(moved=("cf-0001",), h_before=("cf-0001",)):
    return {"format": "coelacanth-named-final/v1", "H_before": list(h_before), "items_out_of_H": list(moved),
            "by_item": {i: {"works": 2, "gold": 1, "cross_field_gold": 1} for i in moved}}


def final_inputs():
    return synthetic_inputs(named_final=named_final(moved=(), h_before=("cf-0001",)))


class TestItems(unittest.TestCase):
    def test_pick_claim_text_prefers_the_checked_text(self):
        p = packet("cf-0001", [])
        self.assertEqual(fi.pick_claim_text(p, {"cf-0001": {"text": "t", "checked_by": "annotator:a1",
                                                            "decided_by": "damir", "changed": True}}),
                         ("t", "annotator:a1", "damir", True))
        self.assertEqual(fi.pick_claim_text(p, {}), ("draft text of cf-0001", None, None, False))

    def test_build_items_statuses_and_labels(self):
        items = {i["id"]: i for i in fi.build_items(synthetic_inputs())[0]}
        self.assertEqual([items[i]["status"] for i in ("cf-0001", "cf-0007", "cf-0008")],
                         ["adjudicated", "excluded", "candidate"])
        self.assertEqual(items["cf-0007"]["exclusion_reason"], "no_full_text")
        self.assertEqual(items["cf-0001"]["claim"]["text"], "checked text one")
        self.assertEqual(items["cf-0001"]["claim"]["checked_by"], "annotator:a1")
        self.assertEqual(items["cf-0002"]["claim"]["text"], "draft text of cf-0002")
        self.assertEqual(items["cf-0001"]["verdict"], "partial")
        self.assertEqual(items["cf-0006"]["verdict"], "nothing_close")
        self.assertIsNone(items["cf-0008"]["verdict"])  # an AI-origin item has no judged candidate yet
        self.assertEqual(items["cf-0003"]["candidates"][0]["attribution"], "cited")  # the people's answer, not the hint
        self.assertEqual(items["cf-0001"]["candidates"][0]["field_confirmed_by"], "damir")
        self.assertIsNone(items["cf-0001"]["candidates"][1]["field_confirmed_by"])  # not gold
        self.assertTrue(items["cf-0007"]["annotation"]["excluded_pairs"])
        self.assertNotIn("method_text", items["cf-0001"]["claim"]["source_work"])

    def test_items_are_sorted_and_serialised_the_same_way_twice(self):
        a, b = fi.assemble(synthetic_inputs(), frozen_at="t"), fi.assemble(synthetic_inputs(), frozen_at="t")
        self.assertEqual(a["items_text"], b["items_text"])
        ids = [json.loads(line)["id"] for line in a["items_text"].splitlines()]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(a["splits"]["items_sha256"], hashlib.sha256(a["items_text"].encode()).hexdigest())

    def test_a_pair_missing_from_labels_is_a_problem(self):
        inp = synthetic_inputs()
        inp.labels["pairs"] = [p for p in inp.labels["pairs"] if (p["item"], p["candidate"]) != ("cf-0002", "c1")]
        self.assertTrue(any("cf-0002/c1: no pair" in m for m in fi.assemble(inp)["splits"]["missing"]))


class TestSubsets(unittest.TestCase):
    def test_subset_definitions(self):
        sub = fi.assemble(final_inputs(), frozen_at="t")["splits"]["subsets"]
        self.assertEqual(sub["ALL"], ["cf-0001", "cf-0002", "cf-0003", "cf-0004", "cf-0005", "cf-0006", "cf-0009"])
        self.assertEqual(sub["R"], ["cf-0001", "cf-0002"])
        self.assertEqual(sub["C"], ["cf-0004"])
        self.assertEqual(sub["A"], ["cf-0008"])
        self.assertEqual(sub["N"], ["cf-0005", "cf-0006"])  # verdict nothing_close, whatever the construction
        self.assertEqual(sub["N_built"], ["cf-0005"])  # cf-0009 has gold and is out of N
        self.assertNotIn("cf-0009", sub["N"])
        self.assertEqual(sub["K"], ["k-1", "k-2"])
        self.assertEqual(sub["K_items"], sorted(sub["K_items"]))
        self.assertIn("cf-0001", sub["K_items"])
        self.assertNotIn("cf-0007", sub["ALL"])  # excluded
        self.assertEqual(sub["H"], ["cf-0001"])
        self.assertEqual(sub["R_recalled"], ["cf-0002"])
        self.assertEqual(sub["P40"], sub["R"])  # R is far below 45: all of R
        self.assertEqual(sub["P20"], sub["R_half"])
        self.assertEqual(len(sub["R_half"]), 1)

    def test_n_definition_built(self):
        sub = fi.assemble(final_inputs(), n_definition="built")["splits"]["subsets"]
        self.assertEqual(sub["N"], ["cf-0005"])

    def test_h_excludes_items_named_final_moves_out(self):
        res = fi.assemble(synthetic_inputs(named_final=named_final(moved=("cf-0001",))), frozen_at="t")
        sub = res["splits"]["subsets"]
        self.assertEqual(sub["H"], [])
        self.assertEqual(sub["H_any"], [])
        self.assertEqual(sub["H_half"], [])
        self.assertEqual(sub["R_recalled"], ["cf-0001", "cf-0002"])
        self.assertEqual(res["splits"]["R_recalled"], sub["R_recalled"])
        self.assertEqual(res["report"]["closed_book"]["moved_out_of_H_by_named_works"], ["cf-0001"])
        self.assertNotIn("provisional", res["splits"])

    def test_a_named_work_that_is_gold_but_not_cross_field_leaves_h_any_only(self):
        nf = {"H_before": ["cf-0001"], "items_out_of_H": [],
              "by_item": {"cf-0001": {"works": 1, "gold": 1, "cross_field_gold": 0}}}
        cb = fi.closed_book_sets(["cf-0001", "cf-0002"], synthetic_inputs().closed_book)
        h, h_any, moved, problems = fi.apply_named_final(cb, nf)
        self.assertEqual((h, h_any, moved, problems), (["cf-0001"], [], [], []))

    def test_named_final_h_before_must_match_the_current_probes(self):
        res = fi.assemble(synthetic_inputs(named_final=named_final(moved=(), h_before=("cf-0001", "cf-0002"))))
        self.assertTrue(res["splits"]["provisional"])
        self.assertTrue(any("H_before" in m for m in res["splits"]["missing"]))

    def test_closed_book_sets(self):
        full = {f"{m}:{p}": n for m in MODELS for p, n in fi.EXPECTED_SAMPLES.items()}

        def row(**over):
            base = {"cross_low": [], "cross_high": [], "any_low": [], "any_high": [], "valid": dict(full),
                    "models_without_valid": []}
            base.update(over)
            return base
        state = {"in-h": row(), "named": row(cross_low=["claude-sonnet-5-5:frozen"], cross_high=["claude-sonnet-5-5:frozen"]),
                 "borderline": row(cross_high=["claude-haiku-4-5-20251001:cross_field"]),
                 "declined": row(models_without_valid=["claude-haiku-4-5-20251001"], cross_high=["claude-sonnet-5-5:frozen"], cross_low=["claude-sonnet-5-5:frozen"]),
                 "short": row(valid={**full, "claude-sonnet-5-5:cross_field": 1}), "any-only": row(any_high=["claude-sonnet-5-5:frozen"])}
        out = fi.closed_book_sets(sorted(state) + ["absent"], state)
        self.assertEqual(out["H"], ["any-only", "in-h", "short"])
        self.assertEqual(out["H_any"], ["in-h", "short"])
        self.assertEqual(out["recalled"], ["borderline", "named"])
        self.assertEqual(out["declines"], ["declined"])
        self.assertEqual(out["uncertain"], ["any-only", "borderline"])
        self.assertEqual(out["incomplete"], {"short": ["claude-sonnet-5-5:cross_field"]})
        self.assertEqual(out["no_record"], ["absent"])

    def test_h_post_uses_the_latest_cutoff_among_the_models(self):
        items = [{"id": f"cf-{n:04d}", "status": "adjudicated", "verdict": "partial", "origin": {"kind": "human"},
                  "provenance": {"source": "s", "role": None}, "negative": None, "claim": {"date": d}}
                 for n, d in ((1, "2025-10-01"), (2, "2026-09-01"), (3, "2020-01-01"))]
        sub, problems = fi.derive_subsets(items, r_ids=[i["id"] for i in items], h=[i["id"] for i in items],
                                          h_any=[], r_recalled=[], k_queries=[],
                                          models_for_h=["claude-sonnet-5-5", "claude-haiku-4-5-20251001"], seed=1)
        self.assertEqual(problems, [])
        self.assertEqual(sub["H_post"], ["cf-0002"])  # after 2026-08-01, the later of the two cutoffs

    def test_r_recomputed_from_the_items_must_match_attribution(self):
        inp = synthetic_inputs()
        inp.attribution["R"] = ["cf-0001", "cf-0002", "cf-0003"]
        res = fi.assemble(inp)
        self.assertTrue(any(m.startswith("R: attribution.json lists") for m in res["splits"]["missing"]))

    def test_attribution_not_final_is_missing(self):
        inp = synthetic_inputs(named_final=named_final())
        inp.attribution["R_final"] = False
        self.assertTrue(any("R_final" in m for m in fi.assemble(inp)["splits"]["missing"]))


class TestDraws(unittest.TestCase):
    @staticmethod
    def big_items(n=60):
        return [{"id": f"cf-{k:04d}", "status": "adjudicated", "verdict": "partial", "origin": {"kind": "human"},
                 "provenance": {"source": ("kulikowski", "declaration", "catalogue")[k % 3], "role": None},
                 "negative": None, "claim": {"date": "2015-01-01"}} for k in range(1, n + 1)]

    def draw(self, seed, recalled=()):
        items = self.big_items()
        ids = [i["id"] for i in items]
        return fi.derive_subsets(items, r_ids=ids, h=ids[:5], h_any=[], r_recalled=list(recalled), k_queries=[],
                                 models_for_h=["claude-sonnet-5-5"], seed=seed)[0]

    def test_reproducible_under_the_seed_and_different_for_another(self):
        a, b, c = self.draw(7), self.draw(7), self.draw(8)
        self.assertEqual(a, b)
        self.assertNotEqual(a["P40"], c["P40"])
        self.assertEqual(len(a["P40"]), 40)
        self.assertEqual(len(set(a["P40"])), 40)

    def test_p40_is_stratified_by_source_and_recall(self):
        recalled = [f"cf-{k:04d}" for k in range(1, 61) if k % 2 == 0]
        sub = self.draw(7, recalled)
        by_cell = {}
        for i in sub["P40"]:
            k = int(i[3:])
            by_cell.setdefault((k % 3, k % 2 == 0), []).append(i)
        self.assertEqual(sorted(len(v) for v in by_cell.values()), [6, 6, 7, 7, 7, 7])  # 60 items in 6 cells of 10
        self.assertEqual(sorted(sub["P40"]), sub["P40"])

    def test_halves_are_nested_and_ceil_sized(self):
        sub = self.draw(7)
        self.assertEqual(len(sub["P20"]), 20)
        self.assertTrue(set(sub["P20"]) <= set(sub["P40"]))
        self.assertEqual(len(sub["R_half"]), 30)
        self.assertEqual(len(sub["H_half"]), 3)
        self.assertTrue(set(sub["H_half"]) <= set(sub["H"]))
        self.assertEqual(fi.half_size(5), 3)
        self.assertEqual(fi.half_size(10), 5)

    def test_stratified_sample_never_exceeds_the_set(self):
        self.assertEqual(fi.stratified_sample(["a", "b"], {"a": 1, "b": 2}, 5, 1, "x"), ["a", "b"])
        self.assertEqual(fi.stratified_sample([], {}, 3, 1, "x"), [])


class TestViewsAndHarness(unittest.TestCase):
    def test_views_hash_matches_the_harness_function(self):
        res = fi.assemble(final_inputs(), frozen_at="t")
        items = {i["id"]: i for i in map(json.loads, res["items_text"].splitlines())}
        views = res["splits"]["views_sha256"]
        self.assertNotIn("cf-0007", views)  # excluded
        self.assertIn("cf-0008", views)  # AI-origin items are runnable (subset A)
        for item_id, digest in views.items():
            claim = items[item_id]["claim"]
            expected = run.view_sha256({"text": claim["text"], "skeleton": claim["skeleton"], "date": claim["date"]})
            self.assertEqual(digest, expected)
            self.assertEqual(digest, run.frozen_view_sha256(items[item_id]))

    def test_harness_view_check_accepts_the_written_items_and_catches_a_change(self):
        res = fi.assemble(final_inputs(), frozen_at="t")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            path.write_text(res["items_text"], encoding="utf-8")
            ids = sorted(res["splits"]["views_sha256"])
            self.assertEqual(run._view_problems(ids, res["splits"], path), [])
            changed = res["items_text"].replace("draft text of cf-0002", "another text")
            path.write_text(changed, encoding="utf-8")
            self.assertTrue(any("cf-0002" in p for p in run._view_problems(ids, res["splits"], path)))

    def test_splits_file_has_the_fields_the_harness_reads(self):
        splits = fi.assemble(final_inputs(), frozen_at="t")["splits"]
        for key in ("format", "frozen_at", "items_path", "items_sha256", "seed", "refpipe_readiness", "models_for_H",
                    "views_sha256", "subsets", "R_recalled"):
            self.assertIn(key, splits)
        self.assertEqual(splits["refpipe_readiness"], "not_ready")
        self.assertEqual(splits["models_for_H"], MODELS)
        self.assertEqual(splits["items_path"], "eval/benchmark/v0/items.jsonl")
        for name in ("ALL", "H", "H_any", "H_post", "H_half", "P40", "P20", "C", "A", "N", "K", "R"):
            self.assertIn(name, splits["subsets"])


class TestProvisionalAndWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.splits_dir = self.repo / fi.SPLITS_DIR_REL
        self.addCleanup(self.tmp.cleanup)

    def test_provisional_without_named_final(self):
        res = fi.assemble(synthetic_inputs(), frozen_at="t")
        self.assertIs(res["splits"]["provisional"], True)
        self.assertTrue(any("named-final.json" in m for m in res["splits"]["missing"]))
        self.assertIs(res["report"]["provisional"], True)

    def test_provisional_output_is_refused_for_eval_results_splits(self):
        res = fi.assemble(synthetic_inputs(), frozen_at="t")
        with self.assertRaises(fi.FreezeRefused):
            fi.write_outputs(res, self.splits_dir, write=False, repo=self.repo)
        with self.assertRaises(fi.FreezeRefused):
            fi.write_outputs(res, self.splits_dir / "sub", write=False, repo=self.repo)
        with self.assertRaises(fi.FreezeRefused):
            fi.write_outputs(res, None, write=True, repo=self.repo)
        self.assertFalse(self.splits_dir.exists())
        self.assertFalse((self.repo / fi.ITEMS_REL).exists())

    def test_provisional_output_goes_to_a_scratch_directory_under_another_name(self):
        res = fi.assemble(synthetic_inputs(), frozen_at="t")
        scratch = Path(self.tmp.name) / "scratch"
        written = {p.name for p in fi.write_outputs(res, scratch, write=False, repo=self.repo)}
        self.assertEqual(written, {"items.jsonl", fi.PROVISIONAL_NAME, fi.REPORT_NAME})
        self.assertFalse((scratch / fi.SPLITS_NAME).exists())
        self.assertFalse((scratch / fi.SUMS_NAME).exists())
        self.assertFalse(self.splits_dir.exists())

    def test_final_output_into_splits_needs_the_write_flag(self):
        res = fi.assemble(final_inputs(), frozen_at="t")
        self.assertNotIn("provisional", res["splits"])
        with self.assertRaises(fi.FreezeRefused):
            fi.write_outputs(res, self.splits_dir, write=False, repo=self.repo)

    def test_write_creates_splits_items_and_sha256sums_once(self):
        res = fi.assemble(final_inputs(), frozen_at="t")
        written = fi.write_outputs(res, None, write=True, repo=self.repo)
        self.assertEqual({p.name for p in written}, {"items.jsonl", fi.SPLITS_NAME, fi.REPORT_NAME, fi.SUMS_NAME})
        splits_path = self.splits_dir / fi.SPLITS_NAME
        sums = (self.splits_dir / fi.SUMS_NAME).read_text(encoding="utf-8")
        self.assertIn(hashlib.sha256(splits_path.read_bytes()).hexdigest(), sums)  # what run.preflight checks
        self.assertEqual(hashlib.sha256((self.repo / fi.ITEMS_REL).read_bytes()).hexdigest(),
                         json.loads(splits_path.read_text(encoding="utf-8"))["items_sha256"])
        with self.assertRaises(fi.FreezeRefused):  # the freeze happens once
            fi.write_outputs(res, None, write=True, repo=self.repo)

    def test_main_refuses_write_while_provisional(self):
        with mock.patch.object(fi, "load_inputs", return_value=synthetic_inputs()):
            self.assertEqual(fi.main(["--write"], repo=self.repo), 2)
            self.assertEqual(fi.main(["--out", str(self.splits_dir)], repo=self.repo), 2)
            scratch = Path(self.tmp.name) / "scratch"
            self.assertEqual(fi.main(["--out", str(scratch), "--seed", "3"], repo=self.repo), 0)
        self.assertFalse(self.splits_dir.exists())
        self.assertTrue((scratch / fi.PROVISIONAL_NAME).exists())
        self.assertEqual(json.loads((scratch / fi.PROVISIONAL_NAME).read_text(encoding="utf-8"))["seed"], 3)

    def test_main_writes_when_nothing_is_missing(self):
        with mock.patch.object(fi, "load_inputs", return_value=final_inputs()):
            self.assertEqual(fi.main(["--write"], repo=self.repo), 0)
        self.assertTrue((self.splits_dir / fi.SPLITS_NAME).exists())
        self.assertTrue((self.repo / fi.ITEMS_REL).exists())

    def test_missing_closed_pool_or_closed_book_is_provisional(self):
        self.assertTrue(fi.assemble(synthetic_inputs(named_final=named_final(), k=None))["splits"]["provisional"])
        res = fi.assemble(synthetic_inputs(named_final=named_final(), closed_book=None))
        self.assertTrue(any("closed-book" in m for m in res["splits"]["missing"]))


if __name__ == "__main__":
    unittest.main()
