"""Tests for the agreement measure: Krippendorff's alpha against Krippendorff's own worked examples, Cohen's kappa,
the bootstrap, the four distinctions computed from labels files, and the command.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from coelacanth.benchmark import agreement, annotation
from coelacanth.benchmark.items import derive_verdict

N = None


class TestKrippendorffExamples(unittest.TestCase):
    """Krippendorff K (2011), "Computing Krippendorff's Alpha-Reliability", cached as
    cache/fulltext/method-krippendorff-2011.txt: the published values of its examples A, B and C."""

    def test_a_binary_two_observers(self):
        meg = [0, 1, 0, 0, 0, 0, 0, 0, 1, 0]
        owen = [1, 1, 1, 0, 0, 1, 0, 0, 0, 0]
        units = list(zip(meg, owen))
        o = agreement.coincidences(units)
        self.assertEqual((o[(0, 0)], o[(0, 1)], o[(1, 0)], o[(1, 1)]), (10, 4, 4, 2))
        self.assertEqual(round(agreement.krippendorff_alpha(units), 3), 0.095)

    def test_b_nominal_two_observers(self):
        ben = "a a b b d c c c e d d a".split()
        gerry = "b a b b b c c c e d d d".split()
        self.assertEqual(round(agreement.krippendorff_alpha(list(zip(ben, gerry))), 3), 0.692)

    def test_c_nominal_four_observers_with_missing_values(self):
        a = [1, 2, 3, 3, 2, 1, 4, 1, 2, N, N, N]
        b = [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, N, 3]
        c = [N, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, N]
        d = [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, N]
        units = list(zip(a, b, c, d))
        o = agreement.coincidences(units)
        # the coincidence matrix printed in the example, and n = 40 pairable values (unit 12's lone value is unpaired)
        self.assertEqual([o[(1, k)] for k in (1, 2, 3, 4, 5)], [7, Fraction(4, 3), Fraction(1, 3), Fraction(1, 3), 0])
        self.assertEqual([o[(2, 2)], o[(3, 3)], o[(4, 4)], o[(5, 5)]], [10, 8, 4, 3])
        self.assertEqual(sum(o.values()), 40)
        self.assertEqual(round(agreement.krippendorff_alpha(units), 3), 0.743)

    def test_scotts_pi_relation_for_two_coders(self):
        # Krippendorff (2004, cache/fulltext/method-krippendorff-2004.txt): for two coders and binary data, alpha
        # exceeds Scott's pi by (1 - pi) / n. Example A: p = 0.7, (b + c) = 0.4, pi = 1 - 0.4 / (2 * 0.7 * 0.3).
        pi = 1 - 0.4 / (2 * 0.7 * 0.3)
        alpha = agreement.krippendorff_alpha(list(zip([0, 1, 0, 0, 0, 0, 0, 0, 1, 0], [1, 1, 1, 0, 0, 1, 0, 0, 0, 0])))
        self.assertAlmostEqual(alpha, pi + (1 - pi) / 20)

    def test_undefined_without_variation_or_pairs(self):
        self.assertIsNone(agreement.krippendorff_alpha([("x", "x"), ("x", "x")]))
        self.assertIsNone(agreement.krippendorff_alpha([("x", None), (None, "y")]))
        self.assertEqual(agreement.krippendorff_alpha([("x", "x"), ("y", "y")]), 1.0)


class TestKappaAndBootstrap(unittest.TestCase):
    def test_cohen_kappa_by_krippendorffs_dichotomous_form(self):
        # Krippendorff (2004), Figure 2: kappa = 1 - (b + c) / (pA qB + pB qA); example A gives 1 - 0.4 / 0.44.
        pairs = list(zip([0, 1, 0, 0, 0, 0, 0, 0, 1, 0], [1, 1, 1, 0, 0, 1, 0, 0, 0, 0]))
        self.assertAlmostEqual(agreement.cohen_kappa(pairs), 1 - 0.4 / 0.44)
        self.assertIsNone(agreement.cohen_kappa([("x", "x")] * 3))
        self.assertIsNone(agreement.cohen_kappa([(None, "x")]))
        self.assertEqual(agreement.cohen_kappa([("x", "x"), ("y", "y")]), 1.0)

    def test_quantile_interpolates(self):
        self.assertEqual(agreement.quantile([0.0, 1.0], 0.25), 0.25)
        self.assertEqual(agreement.quantile([1.0, 2.0, 3.0], 0.5), 2.0)

    def test_bootstrap_is_seeded_and_resamples_items(self):
        per_item = [agreement.coincidences([("a", "a")]), agreement.coincidences([("b", "b")]),
                    agreement.coincidences([("a", "b")])]
        draws = agreement.resample_indices(3, 200, seed=7)
        self.assertEqual(draws, agreement.resample_indices(3, 200, seed=7))
        self.assertNotEqual(draws, agreement.resample_indices(3, 200, seed=8))
        self.assertTrue(all(len(d) == 3 for d in draws))
        values = agreement.bootstrap(per_item, draws)
        self.assertEqual(len(values), 200)
        # a resample of the first item three times has no variation: alpha undefined
        undefined = sum(1 for d, v in zip(draws, values) if v is None)
        self.assertEqual(undefined, sum(1 for d in draws if len(set(d)) == 1 and d[0] in (0, 1)))


# Labels files built on minimal packets: c1 carries the relation under test, c2 is always different.

ELEMENTS = {"equivalent": {"covered_elements": ["e1", "e2", "e3"]},
            "specialization": {"covered_elements": ["e1", "e2", "e3"], "restricted_elements": ["e3"]},
            "generalization": {"covered_elements": ["e1", "e2"]},
            "partial": {"covered_elements": ["e1", "e2"]},
            "similar_mechanism": {"covered_elements": ["e2", "e3"], "analogous_elements": ["e1"]},
            "different": {}}


def make_packet(n):
    work = {"doi": f"10.1/c{n}", "title": "A candidate", "first_author": "A. Writer", "year": 1990,
            "publication_date": "1990-01-01", "cache_key": None}
    return {"id": f"cf-{n:04d}",
            "prefill": {"by": "claude-opus-5-5", "date": "2026-10-06", "status": "draft", "fields": ["claim.text"]},
            "claim": {"text": f"Claim number {n} in the source field's words.", "skeleton": f"Neutral claim {n}.",
                      "coined_names": [], "date": "2020-01-01", "field": "Statistics", "domain": "Physical Sciences",
                      "computational": True,
                      "source_work": {"doi": f"10.1/s{n}", "title": "A source", "first_author": "B. Author",
                                      "year": 2020, "publication_date": "2020-01-01", "cache_key": None},
                      "chart": [{"id": f"e{i}", "text": f"element {i}", "key": None} for i in (1, 2, 3)]},
            "candidates": [{"cid": f"c{j}", "work": dict(work), "evidence": [], "attribution_hint": "unknown",
                            "note": "facts"} for j in (1, 2)],
            "notes": ""}


def label_candidate(cid, relation, cross_field=True):
    cand = {"cid": cid, "relation": relation, "covered_elements": [], "analogous_elements": [],
            "restricted_elements": [], "attribution": "unknown", "cross_field": cross_field, "confidence": 2,
            "rejected_evidence": [], "note": ""}
    cand.update(copy.deepcopy(ELEMENTS[relation]))
    return cand


def label_item(packet, relation, cross_field=True, c2_cross_field=False, status="done"):
    item = {"id": packet["id"], "minutes": 12,
            "claim": {"text": packet["claim"]["text"], "skeleton": packet["claim"]["skeleton"],
                      "chart": [{"id": e["id"], "text": e["text"], "key": True} for e in packet["claim"]["chart"]]},
            "text_edited": False,
            "candidates": [label_candidate("c1", relation, cross_field),
                           label_candidate("c2", "different", c2_cross_field)],
            "status": status, "notes": "", "prefill_status": "draft"}
    item["verdict"] = derive_verdict(item)
    if status != "done":
        item.update(minutes=0, verdict=None)
        for cand in item["candidates"]:
            cand.update(relation=None, covered_elements=[], analogous_elements=[], restricted_elements=[])
    return item


def labels_file(annotator, items, batch="b00"):
    return {"format": annotation.LABELS_FORMAT, "batch": batch, "annotator": annotator,
            "exported_at": "2026-10-12T10:00:00Z", "items": items}


# Example B of Krippendorff (2011) as item verdicts: Ben is a1, Gerry is a2; e (nothing close) means c1 is different.
VERDICT_OF = {"a": "equivalent", "b": "specialization", "c": "partial", "d": "similar_mechanism", "e": "different"}
BEN = "a a b b d c c c e d d a".split()
GERRY = "b a b b b c c c e d d d".split()
PACKETS = [make_packet(n) for n in range(1, 14)]
BATCH = {"format": annotation.BATCH_FORMAT, "batch": "b00", "created": "2026-10-06",
         "labels": list(annotation.RELATIONS), "items": PACKETS}


def example_labels():
    a1 = [label_item(p, VERDICT_OF[v], c2_cross_field=False) for p, v in zip(PACKETS, BEN)]
    # a2 calls the gold candidate of the first two items same-field; c2's flags differ but c2 is never gold
    a2 = [label_item(p, VERDICT_OF[v], cross_field=n >= 2, c2_cross_field=True)
          for n, (p, v) in enumerate(zip(PACKETS, GERRY))]
    a1.append(label_item(PACKETS[12], "partial"))
    a2.append(label_item(PACKETS[12], "partial", status="skipped"))
    return labels_file("a1", a1), labels_file("a2", a2)


class TestDistinctions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.batch_path = self.dir / "b00.json"
        self.batch_path.write_text(json.dumps(BATCH))
        l1, l2 = example_labels()
        self.paths = [self.dir / "b00-a1-labels.json", self.dir / "b00-a2-labels.json"]
        for path, labels in zip(self.paths, (l1, l2)):
            path.write_text(json.dumps(labels))

    def tearDown(self):
        self.tmp.cleanup()

    def measure(self, **kwargs):
        batch, readings, problems = agreement.load_readings(self.batch_path, self.paths)
        self.assertEqual(problems, [])
        self.assertEqual([p for r in readings for p in r.problems], [])
        return agreement.measure(batch, readings, resamples=300, **kwargs)

    def test_the_example_labels_pass_check_labels(self):
        for path in self.paths:
            self.assertEqual(annotation.labels_problems(BATCH, json.loads(path.read_text())), [])

    def test_verdict_reproduces_krippendorffs_example_b(self):
        report = self.measure()
        self.assertEqual(report["items"]["used"], [p["id"] for p in PACKETS[:12]])
        self.assertEqual(report["items"]["excluded"], {"cf-0013": "done by a1 only"})
        verdict = report["distinctions"]["verdict"]
        self.assertEqual(round(verdict["alpha"], 3), 0.692)
        self.assertEqual(verdict["n_units"], 12)
        self.assertEqual(verdict["pairs"][0]["raw_agreement"], 0.75)
        self.assertEqual(verdict["result"], "pass")

    def test_already_done_by_hand(self):
        d = self.measure()["distinctions"]["already_done"]
        # units: 4 both already done, 6 both not, unit 5 (not, done), unit 12 (done, not); n = 24, n_done = 10:
        # alpha = 1 - 23 * 4 / (24^2 - 10^2 - 14^2) = 1 - 92 / 280
        self.assertAlmostEqual(d["alpha"], 1 - 92 / 280)
        # kappa = (10/12 - 74/144) / (1 - 74/144) = 46 / 70
        self.assertAlmostEqual(d["pairs"][0]["kappa"], 46 / 70)
        self.assertEqual(d["pairs"][0]["confusion"]["matrix"], [[4, 1], [1, 6]])
        self.assertEqual(d["pairs"][0]["confusion"]["categories"], ["already_done", "not_already_done"])
        self.assertEqual(d["result"], "fail")

    def test_gold_is_per_packet_candidate(self):
        d = self.measure()["distinctions"]["gold"]
        self.assertEqual(d["n_units"], 24)
        self.assertEqual(d["alpha"], 1.0)
        self.assertEqual(d["ci95"], [1.0, 1.0])
        self.assertEqual(d["p_below"], {"0.667": 0.0, "0.800": 0.0})

    def test_cross_field_counts_only_candidates_both_rated_gold(self):
        d = self.measure()["distinctions"]["cross_field"]
        # 11 candidates both rated gold, two of them flagged differently: n = 22, o_TT = 18, n_T = 20, n_F = 2,
        # alpha = 1 - 21 * 4 / (22^2 - 20^2 - 2^2) = 1 - 84 / 80; the never-gold c2's differing flags do not count
        self.assertEqual(d["n_units"], 11)
        self.assertAlmostEqual(d["alpha"], 1 - 84 / 80)
        self.assertEqual((d["threshold"], d["result"]), (None, "descriptive"))

    def test_bootstrap_interval_and_probabilities_are_reproducible(self):
        first = self.measure(seed=5)["distinctions"]["verdict"]
        again = self.measure(seed=5)["distinctions"]["verdict"]
        self.assertEqual(first["ci95"], again["ci95"])
        self.assertEqual(first["p_below"], again["p_below"])
        lo, hi = first["ci95"]
        self.assertTrue(lo <= first["alpha"] <= hi)
        self.assertTrue(0 < first["p_below"]["0.667"] <= first["p_below"]["0.800"] <= 1)
        self.assertEqual(first["bootstrap"]["defined"] + first["bootstrap"]["undefined"], 300)

    def test_decision_follows_the_gated_distinctions(self):
        self.assertEqual(self.measure()["decision"], "fail")  # already done falls short of 0.80
        report = self.measure(item_ids=["cf-0003", "cf-0006", "cf-0007", "cf-0008", "cf-0009"])
        self.assertEqual(report["items"]["used"], ["cf-0003", "cf-0006", "cf-0007", "cf-0008", "cf-0009"])
        self.assertEqual({k: d["alpha"] for k, d in report["distinctions"].items()},
                         {"verdict": 1.0, "already_done": 1.0, "gold": 1.0, "cross_field": None})
        self.assertEqual(report["decision"], "proceed")

    def test_command_writes_the_report_and_prints_the_table(self):
        out = self.dir / "agreement.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = agreement.main([str(self.batch_path), *map(str, self.paths), "--out", str(out),
                                   "--resamples", "100"])
        self.assertEqual(code, 0)
        report = json.loads(out.read_text())
        self.assertEqual(report["format"], agreement.REPORT_FORMAT)
        self.assertEqual([x["annotator"] for x in report["inputs"]["labels"]], ["a1", "a2"])
        self.assertEqual(report["inputs"]["labels"][0]["sha256"], agreement.sha256(self.paths[0]))
        self.assertEqual(report["bootstrap"], {"resamples": 100, "seed": agreement.SEED, "unit": "item",
                                               "interval": "percentile, 95%"})
        text = printed.getvalue()
        for needle in ("six-way verdict", "already done", "cross-field on gold", "12 items done by at least two annotators of a1, a2 (1 left out)",
                       "revise LABELING.md once"):
            self.assertIn(needle, text)

    def test_command_stops_on_label_problems_unless_told_otherwise(self):
        labels = json.loads(self.paths[1].read_text())
        labels["items"][0]["verdict"] = "partial"
        self.paths[1].write_text(json.dumps(labels))
        out = self.dir / "agreement.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(agreement.main([str(self.batch_path), *map(str, self.paths), "--out", str(out)]), 1)
        self.assertIn("differs from the derived verdict", printed.getvalue())
        self.assertFalse(out.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agreement.main([str(self.batch_path), *map(str, self.paths), "--out", str(out),
                                             "--resamples", "50", "--ignore-problems"]), 0)
        self.assertEqual(len(json.loads(out.read_text())["label_problems"]), 1)

    def test_the_same_annotator_twice_is_refused(self):
        _, _, problems = agreement.load_readings(self.batch_path, [self.paths[0], self.paths[0]])
        self.assertTrue(any("more than one labels file" in p for p in problems))

    def test_the_command_reports_the_shared_prefill(self):
        out = self.dir / "agreement.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            agreement.main([str(self.batch_path), *map(str, self.paths), "--out", str(out), "--resamples", "20"])
        self.assertEqual(len(json.loads(out.read_text())["prefill"]["shared_prefill"]), 12)
        self.assertIn("pre-fill: 12 items read on the same pre-filled draft by every reader (report as agreement "
                      "under a shared pre-fill, decision 25)", printed.getvalue())


class TestPooledBatches(unittest.TestCase):
    """The final reliability (protocol section 11) pools every item with two readings across batches: example B of
    Krippendorff (2011) split into two batches of six items gives the same alpha as the twelve items in one batch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        l1, l2 = example_labels()
        self.batch_paths, self.paths = [], []
        for name, packets in (("b00", PACKETS[:6]), ("b09", PACKETS[6:12])):
            path = self.dir / f"{name}.json"
            path.write_text(json.dumps(dict(BATCH, batch=name, items=packets)))
            self.batch_paths.append(path)
            ids = {p["id"] for p in packets}
            for labels in (l1, l2):
                part = labels_file(labels["annotator"], [i for i in labels["items"] if i["id"] in ids], batch=name)
                self.paths.append(self.dir / f"{name}-{labels['annotator']}-labels.json")
                self.paths[-1].write_text(json.dumps(part))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pooled_alpha_equals_the_alpha_of_all_items(self):
        groups, problems = agreement.load_pooled(self.batch_paths, self.paths)
        self.assertEqual(problems + [p for g in groups for r in g.readings for p in r.problems], [])
        self.assertEqual([[r.annotator for r in g.readings] for g in groups], [["a1", "a2"], ["a1", "a2"]])
        report = agreement.measure_pooled([(g.batch, g.readings) for g in groups], resamples=100)
        self.assertEqual(report["coders"], ["a1", "a2"])
        self.assertEqual(report["batches"], ["b00", "b09"])
        self.assertEqual(report["items"]["used"], [p["id"] for p in PACKETS[:12]])
        self.assertEqual(round(report["distinctions"]["verdict"]["alpha"], 3), 0.692)
        self.assertAlmostEqual(report["distinctions"]["already_done"]["alpha"], 1 - 92 / 280)

    def test_command_pools_and_names_both_batches(self):
        out = self.dir / "pooled.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = agreement.main([str(self.batch_paths[0]), *map(str, self.paths), "--batch",
                                   str(self.batch_paths[1]), "--out", str(out), "--resamples", "50"])
        self.assertEqual(code, 0, printed.getvalue())
        report = json.loads(out.read_text())
        self.assertEqual(report["batch"], "b00+b09")
        self.assertEqual([b["batch"] for b in report["inputs"]["batches"]], ["b00", "b09"])
        self.assertEqual(sorted({x["batch"] for x in report["inputs"]["labels"]}), ["b00", "b09"])
        text = printed.getvalue()
        self.assertIn("agreement on b00+b09: 12 items", text)
        self.assertIn("pooled (section 11)", text)
        self.assertNotIn("revise LABELING.md", text)

    def test_pooled_inputs_are_checked(self):
        stray = self.dir / "b07-a1-labels.json"
        stray.write_text(json.dumps(labels_file("a1", [], batch="b07")))
        _, problems = agreement.load_pooled(self.batch_paths, self.paths + [stray])
        self.assertTrue(any("holds labels for batch 'b07', which is not among the batches given" in p
                            for p in problems), problems)
        overlap = self.dir / "b08.json"
        overlap.write_text(json.dumps(dict(BATCH, batch="b08", items=PACKETS[5:7])))
        _, problems = agreement.load_pooled(self.batch_paths + [overlap], self.paths)
        self.assertTrue(any("item cf-0006 is in batch 'b00' and in batch 'b08'" in p for p in problems), problems)
        _, problems = agreement.load_pooled(self.batch_paths + [self.batch_paths[0]], self.paths)
        self.assertTrue(any("batch 'b00' is given twice" in p for p in problems), problems)


class TestChanges(unittest.TestCase):
    """--changes: a re-export after a form fix may stand for the first reading only if no decision-critical field
    changed (decision 18 counts independent first readings)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.batch_path = self.dir / "b00.json"
        self.batch_path.write_text(json.dumps(BATCH))
        self.first, _ = example_labels()
        self.first_path = self.dir / "b00-a1-labels.json"
        self.first_path.write_text(json.dumps(self.first))

    def tearDown(self):
        self.tmp.cleanup()

    def run_changes(self, later):
        later_path = self.dir / "b00-a1-labels-v2.json"
        later_path.write_text(json.dumps(later))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = agreement.main([str(self.batch_path), str(self.first_path), str(later_path), "--changes"])
        return code, printed.getvalue()

    def test_form_fixes_are_not_changes(self):
        later = copy.deepcopy(self.first)
        later["items"][0]["candidates"][0].update(confidence=1, note="fixed a quote", attribution="cited")
        later["items"][1]["minutes"] = 40
        code, text = self.run_changes(later)
        self.assertEqual(code, 0, text)
        self.assertIn("no decision-critical field changed", text)
        self.assertIn(f"first: {self.first_path} sha256 {agreement.sha256(self.first_path)}", text)

    def test_critical_changes_are_listed(self):
        later = copy.deepcopy(self.first)
        first_item, second_item = later["items"][0], later["items"][1]
        first_item["candidates"][0] = label_candidate("c1", "different")  # cf-0001: equivalent -> different
        first_item["verdict"] = derive_verdict(first_item)
        second_item["candidates"][0]["cross_field"] = False  # cf-0002: gold, the flag changes
        later["items"][12]["status"] = "skipped"  # cf-0013 no longer done
        code, text = self.run_changes(later)
        self.assertEqual(code, 1, text)
        self.assertIn("cf-0001: item verdict equivalent -> nothing_close", text)
        self.assertIn("cf-0001/c1: gold (equivalent, cross-field) -> not gold", text)
        self.assertIn("cf-0002/c1: gold (equivalent, cross-field) -> gold (equivalent, same field)", text)
        self.assertIn("cf-0013: marked done only in the first file", text)
        self.assertIn("4 decision-critical changes", text)

    def test_added_candidates_are_matched_by_identifier(self):
        packets = PACKETS[:1]
        work = {"doi": "10.1234/added", "title": "Found work", "first_author": "C. Finder", "year": 1980,
                "field": "Physics"}
        added = dict(label_candidate("a1", "partial"), work=work, evidence=[])
        first = labels_file("a1", [dict(label_item(packets[0], "different"), added_candidates=[added])])
        later = copy.deepcopy(first)
        later["items"][0]["added_candidates"][0]["cid"] = "a2"  # renumbered, the same work
        self.assertEqual(agreement.critical_changes(packets, first, later), [])
        later["items"][0]["added_candidates"] = []
        self.assertEqual(agreement.critical_changes(packets, first, later),
                         ["cf-0001: item verdict partial -> nothing_close",
                          "cf-0001/doi:10.1234/added: gold (partial, cross-field) -> not in the later file"])

    def test_two_annotators_are_refused(self):
        _, other = example_labels()
        code, text = self.run_changes(other)
        self.assertEqual(code, 2)
        self.assertIn("--changes compares two exports of one annotator", text)


class TestCrossoverCopies(unittest.TestCase):
    def test_labels_are_checked_against_the_copy_they_were_made_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            batch = dict(BATCH, items=PACKETS[:2])
            plan = {"batch": "b00", "crossover": {"X_prefilled_for_annotator_1": ["cf-0001"],
                                                  "Y_prefilled_for_annotator_2": ["cf-0002"]}}
            copies = annotation.crossover(batch, plan)
            (folder / "b00.json").write_text(json.dumps(batch))
            for name, data in copies.items():
                (folder / f"b00-{name}.json").write_text(json.dumps(data))
            a1 = [label_item(PACKETS[0], "partial"), label_item(PACKETS[1], "different", status="skipped")]
            a1[1]["prefill_status"] = "none"
            a1[1]["claim"] = {"text": "", "skeleton": "", "chart": [], "coined_names": []}
            bare = label_item(PACKETS[0], "partial")
            bare["claim"]["text"] = "The annotator's own claim text."
            bare["claim"]["coined_names"] = []
            bare.update(text_edited=True, prefill_status="none")
            a2 = [bare, label_item(PACKETS[1], "different")]
            paths = [folder / "l1.json", folder / "l2.json"]
            paths[0].write_text(json.dumps(labels_file("a1", a1)))
            paths[1].write_text(json.dumps(labels_file("a2", a2)))
            _, readings, problems = agreement.load_readings(folder / "b00.json", paths)
            self.assertEqual(problems, [])
            self.assertEqual([r.batch_path.name for r in readings], ["b00-a1.json", "b00-a2.json"])
            self.assertEqual([p for r in readings for p in r.problems], [])
            # against the pre-filled batch the bare reading is refused
            self.assertTrue(any("prefill_status 'none'" in p
                                for p in annotation.labels_problems(batch, labels_file("a2", a2))))
            report = agreement.measure(batch, readings, resamples=20)
            self.assertEqual(report["items"]["used"], ["cf-0001"])
            # decision 25: cf-0001 was read once on the draft and once bare
            group = agreement.Group(folder / "b00.json", batch, readings)
            self.assertEqual(agreement.prefill_mix([group], report["items"]["used"]),
                             {"shared_prefill": [], "one_bare": ["cf-0001"], "all_bare": []})

    def test_copies_in_assigned_are_found_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            batch = dict(BATCH, items=PACKETS[:3])
            (folder / "b00.json").write_text(json.dumps(batch))
            (folder / "assigned").mkdir()
            paths = []
            for name, packets in (("a1", PACKETS[:2]), ("a2", PACKETS[1:3])):
                (folder / "assigned" / f"b00-{name}.json").write_text(json.dumps(dict(batch, annotator=name,
                                                                                      items=packets)))
                paths.append(folder / f"{name}.json")
                paths[-1].write_text(json.dumps(labels_file(name, [label_item(p, "partial") for p in packets])))
            _, readings, problems = agreement.load_readings(folder / "b00.json", paths)
            self.assertEqual(problems, [])
            self.assertEqual([(r.batch_path.parent.name, r.batch_path.name, r.problems) for r in readings],
                             [("assigned", "b00-a1.json", []), ("assigned", "b00-a2.json", [])])
            report = agreement.measure(batch, readings, resamples=20)
            self.assertEqual(report["items"]["used"], ["cf-0002"])
            self.assertEqual(report["items"]["excluded"], {"cf-0001": "done by a1 only", "cf-0003": "done by a2 only"})
            # labels that fit no copy are reported against the first copy tried
            (folder / "assigned" / "b00-a2.json").unlink()
            _, readings, _ = agreement.load_readings(folder / "b00.json", paths)
            self.assertTrue(any("(checked against b00.json): item cf-0001 of the batch is missing" in p
                                for p in readings[1].problems), readings[1].problems)


class TestMoreThanTwoAnnotators(unittest.TestCase):
    """Krippendorff (2011) example C through labels files: four annotators, each item read by those who valued it
    (the others skipped it), so alpha runs with missing values; the lone reading of unit 12 cannot be paired."""

    VALUES = {1: "equivalent", 2: "specialization", 3: "partial", 4: "similar_mechanism", 5: "different"}
    DATA = {"a1": [1, 2, 3, 3, 2, 1, 4, 1, 2, N, N, N],
            "a2": [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, N, 3],
            "a3": [N, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, N],
            "a4": [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, N]}

    def test_alpha_with_missing_readings_and_require_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            batch = dict(BATCH, items=PACKETS[:12])
            (folder / "b00.json").write_text(json.dumps(batch))
            paths = []
            for name, values in self.DATA.items():
                items = [label_item(p, self.VALUES[v]) if v is not None else label_item(p, "different", status="skipped")
                         for p, v in zip(PACKETS, values)]
                paths.append(folder / f"{name}.json")
                paths[-1].write_text(json.dumps(labels_file(name, items)))
            batch, readings, problems = agreement.load_readings(folder / "b00.json", paths)
            self.assertEqual(problems + [p for r in readings for p in r.problems], [])
            report = agreement.measure(batch, readings, resamples=50)
            self.assertEqual(report["items"]["excluded"], {"cf-0012": "done by a2 only"})
            verdict = report["distinctions"]["verdict"]
            self.assertEqual(round(verdict["alpha"], 3), 0.743)
            self.assertEqual((verdict["n_units"], verdict["n_values"]), (11, 40))
            self.assertEqual(len(verdict["pairs"]), 6)
            strict = agreement.measure(batch, readings, resamples=50, require_all=True)
            self.assertEqual(strict["items"]["used"], ["cf-0002", "cf-0003", "cf-0004", "cf-0005", "cf-0006",
                                                       "cf-0007", "cf-0008", "cf-0009"])


if __name__ == "__main__":
    unittest.main()
