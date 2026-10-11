"""Tests for measurement A's scoring (eval/protocol.md decisions 5 and 23, sections 7 and 8; docs/design/harness.md
section 8.8): the primary metric with Wilson intervals, the secondary retrieval metrics, the verdict metrics on a
hand-computed confusion matrix, failures, subsets, calibration, leakage, cost, McNemar and the command line. No
network and no model: synthetic resolved rows and a tiny items and splits fixture.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from coelacanth.harness import score as score_mod
from coelacanth.harness.score import (calibration_block, mcnemar_exact, mcnemar_paired, rate, read_run, score,
                                      table7)

SYSTEM = "single-agent"


def work(n, doi=None):
    return {"openalex": f"W{n}", "doi": doi, "arxiv": None, "pmid": None, "title": f"Work {n}"}


def gold(n, cross=True, doi=None):
    return {"work": work(n, doi), "gold": True, "cross_field": cross, "relation": "equivalent"}


def item(item_id, golds=(), verdict="nothing_close"):
    return {"id": item_id, "claim": {"text": "a claim", "date": "2019-01-01"}, "candidates": list(golds),
            "verdict": verdict}


def candidate(rank, wid=None, **fields):
    return {"rank_raw": rank, "openalex": wid, "doi": None, "arxiv": None, "pmid": None, "url": None, "same_work": [],
            "placeholder": False, "leak": None, "duplicate_of": None, "rank_clean": rank, **fields}


def verdict(six=None, class3=None, rule="precedence_over_candidates", mapped=True):
    return {"rule": rule, "native": None, "six": six, "class3": class3, "mapped": mapped, "six_before_cleaning": six}


def row(item_id, candidates=(), *, system=SYSTEM, condition="text", status="ok", verdict_block=None, probability=None,
        leakage=None, cost=None, run_id="run-1", config="c" * 64):
    failure = status != "ok"
    return {"format": "coelacanth-resolved/v1", "run_id": run_id, "system": system, "condition": condition,
            "item": item_id, "claim_date": "2019-01-01", "config_sha256": config, "status": status,
            "failure": failure, "candidates": list(candidates), "top10": [], "evidence": [],
            "leakage": leakage, "verdict": verdict_block or verdict(None, None, mapped=None),
            "probability": probability, "cost": cost}


def ranking_to(first_gold_rank, wid, filler="9", upto=None):
    """A candidate list whose candidate at `first_gold_rank` is work `wid`, with unique fillers elsewhere."""
    if first_gold_rank is None:
        return [candidate(r, f"W{filler}{wid}{r}") for r in range(1, (upto or 3) + 1)]
    return [candidate(r, wid if r == first_gold_rank else f"W{filler}{wid}{r}") for r in range(1, first_gold_rank + 1)]


def splits(**subsets):
    return {"format": "coelacanth-splits/v1", "subsets": subsets}


# Ten R items. Item 1 has two cross-field gold works and one same-field gold work, item 8 a cross-field and a
# same-field one, the rest a single cross-field one. The first cross-field gold work of item i sits at FIRST_RANK[i].
R_IDS = [f"cf-{n:04d}" for n in range(1, 11)]
FIRST_RANK = [1, 2, 4, 10, 1, 3, 11, None, 5, None]


def r_items():
    items = {}
    for n, iid in enumerate(R_IDS, 1):
        golds = [gold(n)]
        if n == 1:
            golds += [gold(11), gold(12, cross=False)]
        if n == 8:
            golds += [gold(88, cross=False)]
        items[iid] = item(iid, golds, verdict="equivalent")
    return items


def r_rows(**kwargs):
    rows = []
    for n, (iid, first) in enumerate(zip(R_IDS, FIRST_RANK), 1):
        cands = ranking_to(first, f"W{n}")
        if n == 8:  # the same-field gold work is found, the cross-field one is not
            cands = [candidate(1, "W9801"), candidate(2, "W9802"), candidate(3, "W88")]
        rows.append(row(iid, cands, **kwargs))
    return rows


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        self.out = score(r_rows(), r_items(), splits(R=R_IDS))
        self.sys = self.out["systems"][0]
        self.r = self.sys["subsets"]["R"]["retrieval"]

    def test_miss_rate_and_wilson_interval(self):
        miss = self.r["cf"]["miss_at_10"]
        self.assertEqual((miss["k"], miss["n"]), (3, 10))  # items 7 (rank 11), 8 and 10
        self.assertAlmostEqual(miss["rate"], 0.3)
        self.assertAlmostEqual(miss["wilson95"][0], 0.1078, places=4)  # Wilson 95% for 3 of 10
        self.assertAlmostEqual(miss["wilson95"][1], 0.6032, places=4)
        self.assertEqual(self.r["cf"]["success_at_10"]["k"], 7)

    def test_wilson_helper_edges(self):
        self.assertEqual(rate(0, 0)["wilson95"], None)
        self.assertAlmostEqual(rate(0, 10)["wilson95"][1], 0.2775, places=4)

    def test_mrr_at_10_uses_the_first_cross_field_gold(self):
        want = (1 + 1 / 2 + 1 / 4 + 1 / 10 + 1 + 1 / 3 + 0 + 0 + 1 / 5 + 0) / 10
        self.assertAlmostEqual(self.r["cf"]["mrr_at_10"], want)

    def test_success_at_1_and_cross_field_recall(self):
        self.assertEqual(self.r["cf"]["success_at_1"]["k"], 2)  # items 1 and 5
        # item 1 finds one of two cross-field gold works; items 2, 3, 4, 5, 6 and 9 find their only one
        self.assertAlmostEqual(self.r["cf"]["recall_at_10"], (0.5 + 6) / 10)

    def test_all_gold_metrics_count_same_field_gold(self):
        a = self.r["any"]
        self.assertEqual((a["success_at_10"]["k"], a["success_at_10"]["n"]), (8, 10))  # item 8 now succeeds
        self.assertAlmostEqual(a["recall_at_10"], (1 / 3 + 6 + 1 / 2) / 10)

    def test_gold_comes_from_the_items_file_not_the_row(self):
        rows = r_rows()
        rows[9]["candidates"] = [candidate(1, "W999", gold=True, cross_field=True, relation="equivalent")]
        out = score(rows, r_items(), splits(R=R_IDS))["systems"][0]["subsets"]["R"]
        self.assertEqual(out["retrieval"]["cf"]["miss_at_10"]["k"], 3)

    def test_placeholders_removed_and_duplicate_candidates_never_match_gold(self):
        items = {"cf-0001": item("cf-0001", [gold(1, doi="https://doi.org/10.1/X")], verdict="equivalent")}
        cands = [candidate(1, None, doi="10.1/x", placeholder=True, resolved_by="none"),
                 candidate(2, None, doi="10.1/x", rank_clean=None, duplicate_of=1),
                 candidate(3, None, doi="10.1/X", leak="excluded_work", rank_clean=None)]
        out = score([row("cf-0001", cands)], items, splits(R=["cf-0001"]))
        self.assertEqual(out["systems"][0]["per_item"]["cf-0001"]["cf_success_10"], False)

    def test_doi_in_another_form_and_same_work_match(self):
        items = {"cf-0001": item("cf-0001", [gold(1, doi="https://doi.org/10.1/X")], verdict="equivalent"),
                 "cf-0002": item("cf-0002", [gold(2)], verdict="equivalent")}
        rows = [row("cf-0001", [candidate(5, None, doi="10.1/x")]),
                row("cf-0002", [candidate(4, "W77", same_work=["W2", "W77"])])]
        out = score(rows, items, splits(R=["cf-0001", "cf-0002"]))["systems"][0]["per_item"]
        self.assertEqual((out["cf-0001"]["cf_success_10"], out["cf-0002"]["cf_success_10"]), (True, True))
        self.assertEqual(out["cf-0001"]["cf_rr_10"], 1 / 5)

    def test_top10_fallback_without_candidates(self):
        items = {"cf-0001": item("cf-0001", [gold(1)], verdict="equivalent")}
        r = row("cf-0001")
        r["candidates"], r["top10"] = None, [None, "W5", "W1"]
        out = score([r], items, splits(R=["cf-0001"]))["systems"][0]["per_item"]["cf-0001"]
        self.assertEqual(out["cf_rr_10"], 1 / 3)

    def test_items_without_cross_field_gold_are_counted_not_scored(self):
        items = {"cf-0001": item("cf-0001", [gold(1)], verdict="equivalent"),
                 "cf-0002": item("cf-0002", [gold(2, cross=False)], verdict="equivalent"),
                 "cf-0003": item("cf-0003")}
        rows = [row("cf-0001", [candidate(1, "W1")]), row("cf-0002", [candidate(1, "W2")]), row("cf-0003")]
        out = score(rows, items, splits(ALL=list(items)))["systems"][0]["subsets"]["ALL"]["retrieval"]
        self.assertEqual((out["cf"]["n_eval"], out["cf"]["n_without_cf_gold"]), (1, 2))
        self.assertEqual((out["any"]["n_eval"], out["any"]["n_without_gold"]), (2, 1))


class TestFailures(unittest.TestCase):
    def test_failures_are_counted_as_no_output_never_dropped(self):
        ids = ["cf-0001", "cf-0002", "cf-0003", "cf-0004"]
        items = {i: item(i, [gold(n)], verdict="equivalent") for n, i in enumerate(ids, 1)}
        rows = [row("cf-0001", [candidate(1, "W1")]),
                row("cf-0002", [candidate(1, "W999")]),
                row("cf-0003", [candidate(1, "W3")], status="capped"),  # its list is ignored: a final failure
                row("cf-0004", status="infra_error", leakage=None)]
        out = score(rows, items, splits(R=ids))["systems"][0]["subsets"]["R"]
        miss = out["retrieval"]["cf"]["miss_at_10"]
        self.assertEqual((miss["k"], miss["n"]), (3, 4))
        self.assertEqual(out["status_counts"], {"capped": 1, "infra_error": 1, "ok": 2})
        self.assertEqual((out["n_failures"], out["n_pending_rerun"]), (2, 1))
        self.assertEqual(out["leakage"]["n_leakage_unknown"], 4)

    def test_a_rerun_due_row_is_marked_in_the_table(self):
        items = {"cf-0001": item("cf-0001", [gold(1)], verdict="equivalent")}
        out = score([row("cf-0001", status="model_mismatch")], items, splits(R=["cf-0001"]))
        self.assertIn("1 due for re-run", table7(out))


# Eleven verdict items: R holds eight, N three. (true six, predicted six or outcome)
VERDICT_CASES = [
    ("a1", "equivalent", ("equivalent", "already_done")),
    ("a2", "specialization", ("specialization", "already_done")),
    ("a3", "equivalent", ("similar_mechanism", "related")),
    ("a4", "specialization", ("nothing_close", "nothing_close")),
    ("r1", "generalization", ("specialization", "already_done")),
    ("r2", "partial", ("partial", "related")),
    ("r3", "similar_mechanism", ("similar_mechanism", "related")),
    ("r4", "partial", "unmappable"),
    ("n1", "nothing_close", ("similar_mechanism", "related")),
    ("n2", "nothing_close", ("nothing_close", "nothing_close")),
    ("n3", "nothing_close", "failure"),
]


def verdict_fixture():
    items, rows = {}, []
    for name, true_six, pred in VERDICT_CASES:
        iid = f"cf-{name}"
        items[iid] = item(iid, verdict=true_six)
        if pred == "unmappable":
            rows.append(row(iid, verdict_block=verdict(None, None, "native_item_label", mapped=False)))
        elif pred == "failure":
            rows.append(row(iid, status="capped"))
        else:
            leak = {"hit_top10": True, "hit_evidence": False, "n_hits": 1} if name == "n2" else None
            rows.append(row(iid, verdict_block=verdict(*pred), leakage=leak))
    ids = [f"cf-{n}" for n, _, _ in VERDICT_CASES]
    return items, rows, splits(ALL=ids, R=ids[:8], N=ids[8:])


class TestVerdicts(unittest.TestCase):
    def setUp(self):
        items, rows, spl = verdict_fixture()
        self.out = score(rows, items, spl)["systems"][0]["subsets"]
        self.v = self.out["R"]["verdict"]

    def test_r_plus_n_is_the_verdict_set_of_an_r_subset(self):
        self.assertEqual(self.out["R"]["verdict_n"], 11)
        self.assertEqual(self.out["R"]["n_run"], 8)
        self.assertEqual(self.out["N"]["verdict_n"], 3)
        self.assertEqual(self.out["ALL"]["verdict"]["macro_f1"], self.v["macro_f1"])

    def test_macro_f1_on_a_hand_computed_confusion_matrix(self):
        # already_done: tp 2, fp 1, fn 2 (4/7); related: tp 2, fp 2, fn 2 (1/2); nothing_close: tp 1, fp 1, fn 2 (2/5)
        pc = self.v["per_class"]
        self.assertAlmostEqual(pc["already_done"]["f1"], 4 / 7)
        self.assertAlmostEqual(pc["related"]["f1"], 1 / 2)
        self.assertAlmostEqual(pc["nothing_close"]["f1"], 2 / 5)
        self.assertAlmostEqual(self.v["macro_f1"], 103 / 210)
        self.assertEqual(self.v["confusion_3"]["already_done"],
                         {"already_done": 2, "related": 1, "nothing_close": 1, "unmappable": 0, "failure": 0})
        self.assertEqual(self.v["confusion_3"]["related"],
                         {"already_done": 1, "related": 2, "nothing_close": 0, "unmappable": 1, "failure": 0})
        self.assertEqual(self.v["confusion_3"]["nothing_close"],
                         {"already_done": 0, "related": 1, "nothing_close": 1, "unmappable": 0, "failure": 1})

    def test_unmappable_and_failures_are_reported(self):
        self.assertEqual((self.v["n"], self.v["n_labelled"], self.v["n_unmappable"], self.v["n_failure"]),
                         (11, 9, 1, 1))

    def test_already_done_precision_and_false_already_done_rate(self):
        self.assertEqual((self.v["already_done_precision"]["k"], self.v["already_done_precision"]["n"]), (2, 3))
        weaker = self.v["false_already_done"]  # seven items weaker than specialization, one called already done (r1)
        self.assertEqual((weaker["k"], weaker["n"]), (1, 7))
        self.assertAlmostEqual(weaker["rate"], 1 / 7)

    def test_six_class_matrix_is_descriptive_and_keeps_every_item(self):
        six = self.v["six_class"]
        self.assertEqual(six["equivalent"]["equivalent"], 1)
        self.assertEqual(six["equivalent"]["similar_mechanism"], 1)
        self.assertEqual(six["partial"]["unmappable"], 1)
        self.assertEqual(six["nothing_close"]["failure"], 1)
        self.assertEqual(sum(sum(r.values()) for r in six.values()), 11)

    def test_item_label_sensitivity_drops_items_with_a_leakage_hit(self):
        s = self.out["R"]["verdict_sensitivity_no_leak"]
        self.assertEqual((s["n_dropped"], s["n"]), (1, 10))  # n2 had a top-10 hit and the system has an item label row
        self.assertIsNone(score(r_rows(), r_items(), splits(R=R_IDS))["systems"][0]["subsets"]["R"]
                          ["verdict_sensitivity_no_leak"])

    def test_three_class_only_system_has_no_six_class_matrix(self):
        items = {"cf-1": item("cf-1", verdict="specialization"), "cf-2": item("cf-2", verdict="nothing_close")}
        native = verdict(None, "already_done", "native_item_label")
        rows = [row("cf-1", verdict_block=native), row("cf-2", verdict_block=verdict(None, "nothing_close",
                                                                                   "native_item_label"))]
        v = score(rows, items, splits(ALL=["cf-1", "cf-2"]))["systems"][0]["subsets"]["ALL"]["verdict"]
        self.assertIsNone(v["six_class"])
        # related occurs in neither the adjudicated nor the predicted labels, so it is left out of the average
        self.assertIsNone(v["per_class"]["related"]["f1"])
        self.assertAlmostEqual(v["macro_f1"], 1.0)

    def test_rule_none_gives_no_verdict_and_is_not_unmappable(self):
        items = {"cf-1": item("cf-1", [gold(1)], verdict="equivalent")}
        rows = [row("cf-1", [candidate(1, "W1")], verdict_block=verdict(None, None, "none", mapped=None))]
        out = score(rows, items, splits(R=["cf-1"]))["systems"][0]["subsets"]["R"]
        self.assertIsNone(out["verdict"])
        self.assertEqual(out["retrieval"]["cf"]["success_at_10"]["k"], 1)


class TestSubsets(unittest.TestCase):
    def test_subsets_under_20_items_are_descriptive_only(self):
        out = score(r_rows(), r_items(), splits(R=R_IDS))["systems"][0]["subsets"]["R"]
        self.assertTrue(out["descriptive_only"])
        self.assertEqual(out["descriptive_reasons"], ["under_20_items"])

    def test_twenty_items_are_not_flagged_but_h_always_is(self):
        ids = [f"cf-{n:04d}" for n in range(1000, 1024)]
        items = {i: item(i, [gold(n)], verdict="equivalent") for n, i in enumerate(ids, 1)}
        rows = [row(i, [candidate(1, f"W{n}")]) for n, i in enumerate(ids, 1)]
        out = score(rows, items, splits(R=ids, H=ids[:21]))["systems"][0]["subsets"]
        self.assertFalse(out["R"]["descriptive_only"])
        self.assertEqual(out["R"]["n_run"], 24)
        self.assertTrue(out["H"]["descriptive_only"])  # decision 23: H is descriptive at any size
        self.assertEqual(out["H"]["descriptive_reasons"], ["h_family_or_recall_stratum"])

    def test_h_is_reported_first_with_k_of_n(self):
        spl = splits(ALL=R_IDS + ["cf-0011"], R=R_IDS, P40=R_IDS[:5], H=R_IDS[:4])
        out = score(r_rows(), r_items(), spl)
        self.assertEqual(out["subset_order"][:3], ["H", "R", "R_recalled"])
        self.assertEqual(list(out["systems"][0]["subsets"])[0], "H")
        self.assertEqual((out["subsets"]["H"]["of_R"]["k"], out["subsets"]["H"]["of_R"]["n"]), (4, 10))
        self.assertEqual(out["subsets"]["R_recalled"]["n"], 6)  # R minus H, derived
        lines = table7(out).splitlines()
        self.assertTrue(any(line.startswith("H: 4 of 10 items of R") for line in lines))
        data = [line for line in lines if line.startswith("| ") and "---" not in line][1:]
        self.assertTrue(data[0].split(" | ")[1] == "H")

    def test_subset_without_run_items_is_left_out_and_partial_runs_say_so(self):
        out = score(r_rows()[:6], r_items(), splits(R=R_IDS, K=["cf-9000"]))["systems"][0]["subsets"]
        self.assertNotIn("K", out)
        self.assertEqual((out["R"]["n_subset"], out["R"]["n_run"], out["R"]["n_not_run"]), (10, 6, 4))

    def test_systems_and_conditions_are_scored_apart(self):
        rows = r_rows() + r_rows(condition="skeleton", run_id="run-2") + r_rows(system="aaa-system", run_id="run-3")
        out = score(rows, r_items(), splits(R=R_IDS))
        self.assertEqual([(s["system"], s["condition"]) for s in out["systems"]],
                         [("aaa-system", "text"), (SYSTEM, "skeleton"), (SYSTEM, "text")])
        self.assertEqual(out["systems"][1]["label"], f"{SYSTEM} (skeleton)")

    def test_an_item_answered_twice_or_two_configs_are_refused(self):
        with self.assertRaisesRegex(ValueError, "answered cf-0001 twice"):
            score(r_rows() + r_rows(run_id="run-2"), r_items(), splits(R=R_IDS))
        rows = [r_rows()[0], row("cf-0002", run_id="run-2", config="d" * 64)]
        with self.assertRaisesRegex(ValueError, "different configs"):
            score(rows, r_items(), splits(R=R_IDS))

    def test_an_unknown_item_is_an_error(self):
        with self.assertRaises(KeyError):
            score([row("cf-9999")], r_items(), splits(R=R_IDS))


class TestCalibrationLeakageCost(unittest.TestCase):
    def points(self):
        items = {f"cf-{n}": item(f"cf-{n}", verdict="nothing_close") for n in range(1, 5)}
        wrong = verdict("equivalent", "already_done")
        right = verdict("nothing_close", "nothing_close")
        rows = [row("cf-1", verdict_block=right, probability=0.9), row("cf-2", verdict_block=right, probability=0.8),
                row("cf-3", verdict_block=wrong, probability=0.3), row("cf-4", verdict_block=wrong, probability=0.6)]
        return items, rows

    def test_brier_and_ece_on_a_hand_example(self):
        items, rows = self.points()
        cal = score(rows, items, splits(N=list(items)))["systems"][0]["subsets"]["N"]["calibration"]
        # outcomes (verdict correct) 1, 1, 0, 0 against 0.9, 0.8, 0.3, 0.6
        self.assertAlmostEqual(cal["brier"], (0.01 + 0.04 + 0.09 + 0.36) / 4)
        self.assertAlmostEqual(cal["ece"], (0.1 + 0.2 + 0.3 + 0.6) / 4)  # one item in each of four bins
        self.assertEqual((cal["n"], len(cal["bins"])), (4, 10))
        self.assertEqual(cal["bins"][9]["n"], 1)

    def test_already_done_target_and_no_probability(self):
        items, rows = self.points()
        recs = [score_mod.item_record(r, items[r["item"]]) for r in rows]
        cal = calibration_block(recs, "already_done")  # none of the four claims is already done
        self.assertAlmostEqual(cal["brier"], (0.81 + 0.64 + 0.09 + 0.36) / 4)
        for r in rows:
            r["probability"] = None
        self.assertIsNone(score(rows, items, splits(N=list(items)))["systems"][0]["subsets"]["N"]["calibration"])

    def test_probability_one_falls_in_the_last_bin_and_out_of_range_is_an_error(self):
        items, rows = self.points()
        rows[0]["probability"] = 1.0
        cal = score(rows, items, splits(N=list(items)))["systems"][0]["subsets"]["N"]["calibration"]
        self.assertEqual(cal["bins"][9]["n"], 1)
        rows[0]["probability"] = 90
        with self.assertRaisesRegex(ValueError, "outside"):
            score(rows, items, splits(N=list(items)))

    def test_leakage_rate_and_cost_per_item(self):
        items = {f"cf-{n}": item(f"cf-{n}") for n in range(1, 5)}
        rows = [row("cf-1", leakage={"hit_top10": True, "hit_evidence": False, "n_hits": 2},
                    cost={"usd": 0.1, "openalex_usd": 0.002}),
                row("cf-2", leakage={"hit_top10": False, "hit_evidence": True, "n_hits": 1}, cost={"usd": 0.2}),
                row("cf-3", leakage={"hit_top10": False, "hit_evidence": False, "n_hits": 0}, cost={"usd": 0.3}),
                row("cf-4", leakage={"hit_top10": False, "hit_evidence": False, "n_hits": 0})]
        out = score(rows, items, splits(N=list(items)))["systems"][0]["subsets"]["N"]
        self.assertEqual((out["leakage"]["rate"]["k"], out["leakage"]["rate"]["n"]), (2, 4))
        self.assertAlmostEqual(out["leakage"]["hits_per_item"], 3 / 4)
        cost = out["cost"]
        self.assertEqual(cost["n_costed"], 3)
        self.assertAlmostEqual(cost["usd_per_item"], 0.2)
        self.assertAlmostEqual(cost["usd_max_item"], 0.3)
        self.assertAlmostEqual(cost["usd_total"], 0.6)
        self.assertAlmostEqual(cost["openalex_usd_per_item"], 0.002)


class TestMcNemar(unittest.TestCase):
    def test_exact_p_on_a_hand_example(self):
        r = mcnemar_exact(5, 1)
        self.assertEqual((r["discordant"], r["p_value"]), (6, 0.21875))  # 2 * (1 + 6) / 64
        self.assertEqual(mcnemar_exact(1, 5)["p_value"], 0.21875)

    def test_no_discordant_items_and_ties_cap_at_one(self):
        self.assertEqual(mcnemar_exact(0, 0)["p_value"], 1.0)
        self.assertEqual(mcnemar_exact(3, 3)["p_value"], 1.0)
        self.assertAlmostEqual(mcnemar_exact(8, 0)["p_value"], 2 / 256)

    def test_paired_from_per_item_outcomes(self):
        a = {"x1": True, "x2": True, "x3": True, "x4": False, "x5": None, "x6": True}
        b = {"x1": True, "x2": False, "x3": False, "x4": True, "x6": None, "x7": True}
        r = mcnemar_paired(a, b)
        self.assertEqual((r["n"], r["both"], r["neither"], r["b"], r["c"]), (4, 1, 0, 2, 1))
        self.assertEqual(r["n_unpaired"], 3)  # x5, x6 undefined for one system, x7 run by one system only
        self.assertAlmostEqual(r["diff"], 0.25)
        self.assertEqual(mcnemar_paired(a, b, items=["x2", "x3"])["n"], 2)

    def test_paired_outcomes_between_two_scored_systems(self):
        rows = r_rows() + r_rows(system="other", run_id="run-2")
        for r in rows:
            if r["system"] == "other" and r["item"] in R_IDS[:3]:
                r["candidates"] = []  # the other system misses three items the first one finds
        out = score(rows, r_items(), splits(R=R_IDS))
        mine, other = out["systems"][1], out["systems"][0]
        self.assertEqual(other["system"], "other")
        res = score_mod.paired_outcomes(mine, other)
        self.assertEqual((res["b"], res["c"], res["n"]), (3, 0, 10))
        self.assertEqual(res["p_value"], 0.25)


class TestFilesAndCommandLine(unittest.TestCase):
    def make_run(self, base, name="run-1", kind="scored", cost=0.25, config="c" * 64):
        run_dir = Path(base) / name
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps({"run_id": name, "kind": kind}), encoding="utf-8")
        rows = r_rows(run_id=name, config=config)
        (run_dir / "resolved.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        (run_dir / "outputs.jsonl").write_text("".join(json.dumps({"item": r["item"], "cost": {"usd": cost,
                                                  "openalex_nominal": {"search": 2, "list": 0, "entity": 5}}}) + "\n"
                                                       for r in rows), encoding="utf-8")
        return run_dir

    def test_read_run_attaches_cost_and_refuses_pilots(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = read_run(self.make_run(tmp))
            self.assertEqual(rows[0]["cost"]["usd"], 0.25)
            self.assertAlmostEqual(rows[0]["cost"]["openalex_usd"], 0.002)
            with self.assertRaisesRegex(ValueError, "pilots"):
                read_run(self.make_run(tmp, "pilot-1", kind="pilot"))
            empty = Path(tmp) / "empty"
            empty.mkdir()
            with self.assertRaises(FileNotFoundError):
                read_run(empty)

    def test_ledger_is_the_fallback_for_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(tmp)
            (run_dir / "outputs.jsonl").unlink()
            (run_dir / "ledger.jsonl").write_text(json.dumps({"item": "cf-0001", "cost_usd": 0.5}) + "\n",
                                                  encoding="utf-8")
            rows = {r["item"]: r for r in read_run(run_dir)}
            self.assertEqual(rows["cf-0001"]["cost"]["usd"], 0.5)
            self.assertIsNone(rows["cf-0002"]["cost"])

    def test_cli_writes_scores_and_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(tmp)
            items_path, splits_path = Path(tmp) / "items.jsonl", Path(tmp) / "splits.json"
            items_path.write_text("".join(json.dumps(i) + "\n" for i in r_items().values()), encoding="utf-8")
            splits_path.write_text(json.dumps(splits(ALL=R_IDS, R=R_IDS, H=R_IDS[:4])), encoding="utf-8")
            out_dir = Path(tmp) / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = score_mod.main([str(run_dir), "--splits", str(splits_path), "--items", str(items_path),
                                       "--out", str(out_dir)])
            self.assertEqual(code, 0)
            scores = json.loads((out_dir / "scores.json").read_text(encoding="utf-8"))
            self.assertEqual(list(scores["systems"][0]["subsets"])[0], "H")
            self.assertEqual(scores["systems"][0]["subsets"]["R"]["cost"]["usd_per_item"], 0.25)
            self.assertEqual(len(scores["inputs"]["splits"]["sha256"]), 64)
            table = (out_dir / "table7.md").read_text(encoding="utf-8")
            self.assertIn("| system | subset | misses (k of n) |", table)
            self.assertIn("3 of 10", table)
            self.assertIn("0.250", table)
            # a .json path puts the table beside it
            with contextlib.redirect_stdout(io.StringIO()):
                code = score_mod.main([str(run_dir), "--splits", str(splits_path), "--items", str(items_path),
                                       "--out", str(Path(tmp) / "x" / "s.json")])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "x" / "s.md").exists())

    def test_no_dashes_in_the_table_or_the_module(self):
        text = Path(score_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)
        self.assertNotIn(chr(0x2014), table7(score(r_rows(), r_items(), splits(R=R_IDS))))


if __name__ == "__main__":
    unittest.main()
