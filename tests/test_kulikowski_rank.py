"""Tests for the Kulikowski closed-pool export (eval/results/measurement-a/scripts/kulikowski_rank.py; protocol 5).
The script's helpers use only the standard library, so they run here on a synthetic pool; the committed export is
then re-scored from its own files. The package clone in data/raw/ is never read.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import importlib.util
import json
import random
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "eval" / "results" / "measurement-a" / "scripts" / "kulikowski_rank.py"
EXPORT = REPO / "eval" / "results" / "measurement-a" / "closed-pool" / "kulikowski-fp"


def load_script():
    spec = importlib.util.spec_from_file_location("kulikowski_rank", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


K = load_script()

# a1 and a2 are twins in different fields (the two queries). m1 and m2 share a family but also a field, and x1 has
# no family: these three are pool-only papers. Same-field cells hold 0.95 so that a leak into a ranking shows.
IDS = ["a1", "a2", "m1", "m2", "x1"]
FIELD = {"a1": "F1", "a2": "F2", "m1": "F3", "m2": "F3", "x1": "F1"}
FAM = {"a1": "fa", "a2": "fa", "m1": "fm", "m2": "fm", "x1": ""}
DIRECTED = {"a1": {"a2": 0.5, "m1": 0.7, "m2": 0.1},
            "a2": {"a1": 0.9, "m1": 0.2, "m2": 0.3, "x1": 0.4},
            "m1": {"a1": 0.3, "a2": 0.6, "x1": 0.8},
            "m2": {"a1": 0.1, "a2": 0.2, "x1": 0.4},
            "x1": {"a2": 0.0, "m1": 0.6, "m2": 0.2}}
S = [[1.0 if q == k else DIRECTED[q].get(k, 0.95) for k in IDS] for q in IDS]


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestRankings(unittest.TestCase):
    def test_every_paper_gets_a_row_and_metrics_stay_on_the_queries(self):
        rows, p1, mrr = K.rankings(IDS, FIELD, FAM, S, {"a1": "W1"})
        self.assertEqual([r["query"] for r in rows], IDS)
        self.assertEqual({r["query"]: r["query_role"] for r in rows},
                         {"a1": "query", "a2": "query", "m1": "pool_only", "m2": "pool_only", "x1": "pool_only"})
        self.assertEqual((p1, mrr), (0.5, 0.75))  # a1 finds a2 at rank 2, a2 finds a1 at rank 1
        x1 = rows[IDS.index("x1")]
        self.assertEqual([(e["id"], e["rank"]) for e in x1["ranking"]], [("m1", 1), ("m2", 2), ("a2", 3)])
        self.assertEqual(rows[0]["query_openalex"], "W1")
        for row in rows:  # cross-field candidates only, never the paper itself
            self.assertTrue(all(FIELD[e["id"]] != row["field"] for e in row["ranking"]))


class TestPairs(unittest.TestCase):
    def test_every_cross_field_pair_is_scored_by_the_mean_of_both_directions(self):
        rows, _, _ = K.rankings(IDS, FIELD, FAM, S, {})
        pairs = K.pair_rows(rows, IDS, FIELD, FAM)
        got = {(p["a"], p["b"]): (p["score_ab"], p["score_ba"], p["score"], p["twin"]) for p in pairs}
        self.assertEqual(got, {("a1", "a2"): (0.5, 0.9, 0.7, True), ("a1", "m1"): (0.7, 0.3, 0.5, False),
                               ("a1", "m2"): (0.1, 0.1, 0.1, False), ("a2", "m1"): (0.2, 0.6, 0.4, False),
                               ("a2", "m2"): (0.3, 0.2, 0.25, False), ("a2", "x1"): (0.4, 0.0, 0.2, False),
                               ("m1", "x1"): (0.8, 0.6, 0.7, False), ("m2", "x1"): (0.4, 0.2, 0.3, False)})

    def test_pairs_among_pool_only_papers_count_against_ap(self):
        rows, _, _ = K.rankings(IDS, FIELD, FAM, S, {})
        pairs = K.pair_rows(rows, IDS, FIELD, FAM)
        ap = K.average_precision([p["twin"] for p in pairs], [p["score"] for p in pairs])
        self.assertEqual(ap, 0.5)  # m1 / x1 ties the twin at 0.7; without the pool-only rows AP would be 1.0

    def test_a_pair_with_one_directed_score_is_refused(self):
        rows, _, _ = K.rankings(IDS, FIELD, FAM, S, {})
        with self.assertRaisesRegex(ValueError, "has 1 directed scores, expected 2"):
            K.pair_rows([r for r in rows if r["query"] != "x1"], IDS, FIELD, FAM)

    def test_rank_only_entries_score_one_over_rank(self):
        self.assertEqual(K.directed_score({"id": "b", "rank": 4}), 0.25)
        self.assertEqual(K.directed_score({"id": "b", "score": None, "rank": 2}), 0.5)
        self.assertEqual(K.directed_score({"id": "b", "score": 0.0, "rank": 3}), 0.0)


class TestAveragePrecision(unittest.TestCase):
    def test_ties_enter_together(self):
        self.assertAlmostEqual(K.average_precision([True, False, True, False], [0.9, 0.8, 0.8, 0.1]), 0.5 + 0.5 * 2 / 3)
        self.assertEqual(K.average_precision([False, True], [0.5, 0.5]), 0.5)

    def test_matches_sklearn_when_installed(self):
        try:
            from sklearn.metrics import average_precision_score
        except ImportError:
            self.skipTest("scikit-learn is not installed; the script checks this itself when it runs")
        rng = random.Random("kulikowski-ap")
        for _ in range(50):
            n = rng.randint(2, 60)
            labels = [rng.random() < 0.2 for _ in range(n)]
            labels[0] = True
            scores = [round(rng.random(), 1) for _ in range(n)]
            self.assertAlmostEqual(K.average_precision(labels, scores), average_precision_score(labels, scores),
                                   places=12)

    def test_no_positive_pair_refused(self):
        with self.assertRaises(ValueError):
            K.average_precision([False, False], [0.2, 0.1])


class TestCommittedExport(unittest.TestCase):
    """Re-scores the committed export from its own files, as the harness would score any closed-pool system."""

    def check(self, setting):
        rankings = read_jsonl(EXPORT / setting / "rankings.jsonl")
        pairs = read_jsonl(EXPORT / setting / "pairs.jsonl")
        summary = json.loads((EXPORT / setting / "summary.json").read_text(encoding="utf-8"))
        roles = [r["query_role"] for r in rankings]
        self.assertEqual((len(rankings), roles.count("query"), roles.count("pool_only")), (109, 92, 17))
        ids = [r["query"] for r in rankings]
        field = {r["query"]: r["field"] for r in rankings}
        fam = {r["query"]: r["family"] for r in rankings}
        rebuilt = K.pair_rows(rankings, ids, field, fam)
        self.assertEqual([{k: p[k] for k in ("a", "b", "score_ab", "score_ba", "score", "twin")} for p in pairs],
                         rebuilt)
        self.assertEqual((len(pairs), sum(p["twin"] for p in pairs)), (5812, 210))
        first = [next(e["rank"] for e in r["ranking"] if fam[e["id"]] == r["family"])
                 for r in rankings if r["query_role"] == "query"]
        p1, mrr = sum(n == 1 for n in first) / len(first), sum(1 / n for n in first) / len(first)
        ap = K.average_precision([p["twin"] for p in pairs], [p["score"] for p in pairs])
        self.assertEqual((round(p1, 3), round(mrr, 3)), K.EXPECTED[setting])
        self.assertEqual((summary["p_at_1"], summary["mrr"], summary["ap"]),
                         (round(p1, 6), round(mrr, 6), round(ap, 6)))
        self.assertEqual((summary["papers"], summary["queries"], summary["pairs"], summary["twins"]),
                         (109, 92, 5812, 210))
        return ap

    def test_headline_reproduces_the_package_ap(self):
        self.assertEqual(round(self.check("headline"), 3), 0.557)

    def test_unigram(self):
        self.check("unigram")


if __name__ == "__main__":
    unittest.main()
