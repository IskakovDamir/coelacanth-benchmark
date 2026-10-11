"""Tests for the pre-fill anchor measure (eval/protocol.md decision 25 and section 11): words and the share of changed
words, chart changes, readings on pre-filled and bare packets, the crossover pairs, missing labels and the command.
Synthetic batches only; nothing is written to eval/results/.

    PYTHONPATH=src python -m unittest tests.test_anchor -v
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from coelacanth.benchmark import anchor, annotation
from coelacanth.benchmark.agreement import load_pooled
from coelacanth.benchmark.items import derive_verdict


class TestWords(unittest.TestCase):
    def test_words_drop_edge_punctuation_and_keep_case_and_symbols(self):
        self.assertEqual(anchor.words("The rate, (k) is N."), ["The", "rate", "k", "is", "N"])
        self.assertEqual(anchor.words("x \u2264 y, e.g. x_i"), ["x", "\u2264", "y", "e.g", "x_i"])
        # typographic quotes and a dash standing alone are punctuation; NFKC folds the ligature
        self.assertEqual(anchor.words("\u201cleast squares\u201d \u2014 \ufb01t"), ["least", "squares", "fit"])
        self.assertEqual(anchor.words(None), [])
        self.assertEqual(anchor.words("  "), [])

    def test_levenshtein(self):
        self.assertEqual(anchor.levenshtein("kitten", "sitting"), 3)
        self.assertEqual(anchor.levenshtein([], ["a", "b"]), 2)
        self.assertEqual(anchor.levenshtein(["a", "b", "c"], ["b", "c", "a"]), 2)  # a moved word: out, then in

    def test_unchanged_text_has_distance_zero(self):
        change = anchor.text_change("The estimator minimises the squared residuals.",
                                    "The estimator minimises the squared residuals.")
        self.assertEqual(change, {"draft_words": 6, "final_words": 6, "edit_distance": 0, "share": 0.0,
                                  "changed": False})

    def test_edits_by_word(self):
        cases = [("one two three four", "one two THREE four", 1, 0.25),      # substitution
                 ("one two three four", "one two three four five", 1, 0.2),  # insertion
                 ("one two three four", "one three four", 1, 0.25),          # deletion
                 ("N is fixed", "n is fixed", 1, 1 / 3),                     # case is kept
                 ("", "a text written anew", 4, 1.0),                        # nothing drafted
                 ("old words here", "entirely new text", 3, 1.0)]            # every word replaced
        for draft, final, distance, share in cases:
            with self.subTest(final=final):
                change = anchor.text_change(draft, final)
                self.assertEqual(change["edit_distance"], distance)
                self.assertAlmostEqual(change["share"], share)
                self.assertTrue(change["changed"])

    def test_punctuation_and_whitespace(self):
        punctuation = anchor.text_change("A rate is fixed.", "A rate is fixed")
        self.assertEqual((punctuation["share"], punctuation["changed"]), (0.0, True))
        spacing = anchor.text_change("A rate  is fixed.", "A rate is fixed. ")
        self.assertEqual((spacing["share"], spacing["changed"]), (0.0, False))
        self.assertEqual(anchor.text_change("", None)["share"], 0.0)

    def test_chart_change_by_element_id(self):
        draft = [{"id": "e1", "text": "Fit by least squares.", "key": None},
                 {"id": "e2", "text": "Add a quadratic penalty.", "key": None},
                 {"id": "e3", "text": "Choose the weight by cross-validation.", "key": None}]
        final = [{"id": "e1", "text": "Fit by least squares", "key": True},
                 {"id": "e2", "text": "Add a quadratic penalty on the coefficients.", "key": True},
                 {"id": "e7", "text": "The penalty is a multiple of the identity.", "key": False}]
        change = anchor.chart_change(draft, final)
        self.assertEqual(change["removed"], ["e3"])
        self.assertEqual(change["added"], ["e7"])
        self.assertEqual(list(change["edited"]), ["e1", "e2"])  # e1 lost its full stop: an edit of 0 words
        self.assertEqual(change["edited"]["e1"], 0.0)
        self.assertAlmostEqual(change["edited"]["e2"], 3 / 7)
        self.assertEqual((change["draft_elements"], change["final_elements"], change["unchanged"]), (3, 3, 0))
        # key flags alone are never a change: the draft has none
        same = anchor.chart_change(draft, [dict(e, key=True) for e in draft])
        self.assertEqual((same["removed"], same["added"], same["edited"], same["unchanged"]), ([], [], {}, 3))


# A batch of three items: cf-0001 pre-filled for both annotators, cf-0002 in group X (pre-filled for a1, bare for
# a2), cf-0003 in group Y (pre-filled for a2, bare for a1).

DRAFT_CHART = ["Fit by least squares.", "Add a quadratic penalty.", "Choose the weight by cross-validation."]
TEXTS = {1: "The ridge estimate minimises the residual sum of squares plus a penalty.",
         2: "Adding a quadratic penalty to the least squares criterion stabilises the estimate.",
         3: "The population grows at a rate proportional to its size until resources run out."}


def make_packet(n):
    work = {"doi": f"10.1/c{n}", "title": "A candidate", "first_author": "A. Writer", "year": 1990,
            "publication_date": "1990-01-01", "cache_key": None}
    return {"id": f"cf-{n:04d}",
            "prefill": {"by": "claude-opus-5-5", "date": "2026-10-06", "status": "draft",
                        "fields": ["claim.text", "claim.skeleton", "claim.chart"]},
            "claim": {"text": TEXTS[n], "skeleton": f"A neutral skeleton of claim {n}.", "coined_names": [],
                      "date": "2020-01-01", "field": "Statistics", "domain": "Physical Sciences",
                      "computational": True,
                      "source_work": {"doi": f"10.1/s{n}", "title": "A source", "first_author": "B. Author",
                                      "year": 2020, "publication_date": "2020-01-01", "cache_key": None},
                      "chart": [{"id": f"e{i}", "text": t, "key": None} for i, t in enumerate(DRAFT_CHART, 1)]},
            "candidates": [{"cid": "c1", "work": work, "evidence": [], "attribution_hint": "unknown",
                            "note": "facts"}],
            "notes": ""}


PACKETS = [make_packet(n) for n in (1, 2, 3)]
BATCH = {"format": annotation.BATCH_FORMAT, "batch": "b00", "created": "2026-10-06",
         "labels": list(annotation.RELATIONS), "items": PACKETS}
PLAN = {"batch": "b00", "crossover": {"X_prefilled_for_annotator_1": ["cf-0002"],
                                      "Y_prefilled_for_annotator_2": ["cf-0003"]}}
BARE_CHART = [("e1", "Penalised least squares."), ("e2", "The penalty is quadratic.")]


def reading(packet, relation, text=None, chart=None, status="done", minutes=20):
    """One labelled item: on a pre-filled packet the draft's texts unless given, on a bare packet the given ones."""
    bare = annotation.is_bare(packet)
    pclaim = packet["claim"]
    chart = chart if chart is not None else [(e["id"], e["text"]) for e in pclaim["chart"]]
    claim = {"text": text if text is not None else pclaim["text"],
             "skeleton": "My own skeleton." if bare else pclaim["skeleton"],
             "chart": [{"id": i, "text": t, "key": True} for i, t in chart]}
    if bare:
        claim["coined_names"] = []
    cand = {"cid": "c1", "relation": relation, "covered_elements": [], "analogous_elements": [],
            "restricted_elements": [], "attribution": "unknown", "cross_field": True, "confidence": 2,
            "rejected_evidence": [], "note": ""}
    if relation == "equivalent":
        cand["covered_elements"] = [i for i, _ in chart]
    item = {"id": packet["id"], "minutes": minutes, "claim": claim, "text_edited": claim["text"] != pclaim["text"],
            "candidates": [cand], "status": status, "notes": "", "prefill_status": "none" if bare else "draft"}
    item["verdict"] = derive_verdict(item)
    if status != "done":
        item.update(minutes=0, verdict=None)
        cand.update(relation=None, covered_elements=[])
    return item


def labels_file(annotator, items):
    return {"format": annotation.LABELS_FORMAT, "batch": "b00", "annotator": annotator,
            "exported_at": "2026-10-12T10:00:00Z", "items": items}


A1_TEXT_2 = TEXTS[2].replace("stabilises", "regularises")
A1_CHART_2 = [("e1", DRAFT_CHART[0]), ("e2", "Add a quadratic penalty on the coefficients."),
              ("e7", "The penalty is a multiple of the identity.")]
A1_BARE_TEXT_3 = "A population increases in proportion to its size while resources last."
A2_TEXT_1 = TEXTS[1].replace("residual sum of squares", "squared residuals")
A2_BARE_TEXT_2 = "Penalising the squared coefficients in a least squares fit makes the estimate stable."


def example_labels(a2_skips_bare=False):
    copies = annotation.crossover(BATCH, PLAN)
    p1, p2 = ({p["id"]: p for p in copies[a]["items"]} for a in ("a1", "a2"))
    a1 = [reading(p1["cf-0001"], "equivalent", minutes=30),
          reading(p1["cf-0002"], "equivalent", text=A1_TEXT_2, chart=A1_CHART_2, minutes=25),
          reading(p1["cf-0003"], "different", text=A1_BARE_TEXT_3, chart=BARE_CHART, minutes=50)]
    bare_2 = (reading(p2["cf-0002"], "different", text="", chart=[], status="skipped") if a2_skips_bare else
              reading(p2["cf-0002"], "different", text=A2_BARE_TEXT_2, chart=BARE_CHART, minutes=45))
    a2 = [reading(p2["cf-0001"], "equivalent", text=A2_TEXT_1, minutes=35), bare_2,
          reading(p2["cf-0003"], "different", minutes=15)]
    return copies, labels_file("a1", a1), labels_file("a2", a2)


class AnchorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.batch_path = self.dir / "b00.json"
        self.batch_path.write_text(json.dumps(BATCH))
        self.plan_path = self.dir / "b00-plan.json"
        self.plan_path.write_text(json.dumps(PLAN))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, a2_skips_bare=False):
        copies, l1, l2 = example_labels(a2_skips_bare)
        for name, data in copies.items():
            (self.dir / f"b00-{name}.json").write_text(json.dumps(data))
        paths = [self.dir / "b00-a1-labels.json", self.dir / "b00-a2-labels.json"]
        for path, labels in zip(paths, (l1, l2)):
            path.write_text(json.dumps(labels))
        return paths

    def measure(self, paths):
        groups, problems = load_pooled([self.batch_path], paths)
        self.assertEqual([p for p in problems if p != anchor.ONE_FILE_PROBLEM], [])
        self.assertEqual([p for g in groups for r in g.readings for p in r.problems], [])
        return anchor.measure(groups)

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = anchor.main([str(a) for a in args])
        return code, out.getvalue()


class TestReadings(AnchorCase):
    def test_readings_on_the_draft(self):
        report = self.measure(self.write())
        rows = {(r["item"], r["annotator"]): r for r in report["prefilled"]["readings"]}
        self.assertEqual(sorted(rows), [("cf-0001", "a1"), ("cf-0001", "a2"), ("cf-0002", "a1"), ("cf-0003", "a2")])
        # an unchanged pre-fill
        unchanged = rows[("cf-0001", "a1")]
        self.assertEqual((unchanged["text"]["share"], unchanged["text"]["changed"]), (0.0, False))
        self.assertEqual(unchanged["skeleton"]["share"], 0.0)
        self.assertEqual((unchanged["chart"]["removed"], unchanged["chart"]["added"], unchanged["chart"]["edited"],
                          unchanged["chart"]["unchanged"]), ([], [], {}, 3))
        self.assertEqual((unchanged["read_on"], unchanged["copy"], unchanged["verdict"]),
                         ("draft", "b00-a1.json", "equivalent"))
        # an edited text: "residual sum of squares" (4 words) became "squared residuals" (2) in 12 words
        edited = rows[("cf-0001", "a2")]["text"]
        self.assertEqual((edited["draft_words"], edited["final_words"], edited["edit_distance"]), (12, 10, 4))
        self.assertAlmostEqual(edited["share"], 4 / 12)
        # removed, added and edited elements, and one word substituted in the text
        x = rows[("cf-0002", "a1")]
        self.assertEqual((x["chart"]["removed"], x["chart"]["added"], list(x["chart"]["edited"])),
                         (["e3"], ["e7"], ["e2"]))
        self.assertAlmostEqual(x["chart"]["edited"]["e2"], 3 / 7)
        self.assertEqual(x["chart"]["unchanged"], 1)
        self.assertEqual(x["text"]["edit_distance"], 1)
        summary = report["prefilled"]["summary"]
        self.assertEqual(summary["readings"], 4)
        self.assertEqual((summary["text"]["n"], summary["text"]["zero"], summary["text"]["changed"]), (4, 2, 2))
        self.assertEqual(summary["chart"], {"draft_elements": 12, "removed": 1, "added": 1, "edited": 1,
                                            "readings_changed": 1})
        self.assertEqual(report["prefilled"]["by_batch"]["b00"]["readings"], 4)

    def test_a_bare_reading_is_compared_with_the_draft_it_never_saw(self):
        report = self.measure(self.write())
        rows = {(r["item"], r["annotator"]): r for r in report["bare"]["readings"]}
        self.assertEqual(sorted(rows), [("cf-0002", "a2"), ("cf-0003", "a1")])
        bare = rows[("cf-0003", "a1")]
        self.assertEqual((bare["read_on"], bare["chart"]), ("bare", None))
        expected = anchor.text_change(TEXTS[3], A1_BARE_TEXT_3)
        self.assertEqual(bare["text"], expected)
        self.assertGreater(bare["text"]["share"], 0.5)
        self.assertEqual(bare["skeleton"], anchor.text_change("A neutral skeleton of claim 3.", "My own skeleton."))
        self.assertEqual(report["bare"]["summary"]["readings"], 2)
        self.assertNotIn("chart", report["bare"]["summary"])

    def test_crossover_pairs(self):
        report = self.measure(self.write())
        cross = report["crossover"]
        self.assertEqual(report["plans"], {"b00": "b00-plan.json"})
        rows = {c["item"]: c for c in cross["items"]}
        self.assertEqual(sorted(rows), ["cf-0002", "cf-0003"])
        x, y = rows["cf-0002"], rows["cf-0003"]
        self.assertEqual((x["group"], x["prefilled"]["annotator"], x["bare"]["annotator"]), ("X", "a1", "a2"))
        self.assertEqual((x["prefilled"]["verdict"], x["bare"]["verdict"], x["verdict_agrees"]),
                         ("equivalent", "nothing_close", False))
        self.assertAlmostEqual(x["prefilled"]["text_share"], 1 / 12)
        self.assertEqual(x["bare"]["text_share"], anchor.text_change(TEXTS[2], A2_BARE_TEXT_2)["share"])
        self.assertEqual((x["prefilled"]["minutes"], x["bare"]["minutes"]), (25, 45))
        self.assertEqual((y["group"], y["prefilled"]["annotator"], y["bare"]["annotator"], y["verdict_agrees"]),
                         ("Y", "a2", "a1", True))
        self.assertEqual(y["prefilled"]["text_share"], 0.0)
        self.assertEqual((x["missing"], y["missing"]), (None, None))
        agreement = cross["verdict_agreement"]
        self.assertEqual((agreement["k"], agreement["n"], agreement["share"]), (1, 2, 0.5))
        low, high = agreement["wilson95"]
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)
        self.assertEqual(cross["missing"], [])
        self.assertEqual(cross["prefilled_text_share"]["n"], 2)
        # the reference: cf-0001, read by both on the same draft, outside the crossover
        shared = report["shared_draft"]
        self.assertEqual([p["item"] for p in shared["pairs"]], ["cf-0001"])
        self.assertEqual((shared["verdict_agreement"]["k"], shared["verdict_agreement"]["n"]), (1, 1))

    def test_without_a_plan_the_copies_show_the_crossover(self):
        paths = self.write()
        self.plan_path.unlink()
        report = self.measure(paths)
        self.assertEqual(report["plans"], {"b00": None})
        self.assertEqual([(c["item"], c["group"], c["verdict_agrees"]) for c in report["crossover"]["items"]],
                         [("cf-0002", None, False), ("cf-0003", None, True)])


class TestMissingLabels(AnchorCase):
    def test_a_skipped_bare_reading_leaves_the_item_without_a_pair(self):
        report = self.measure(self.write(a2_skips_bare=True))
        rows = {c["item"]: c for c in report["crossover"]["items"]}
        self.assertEqual(rows["cf-0002"]["missing"], "the bare reading (a2) is missing or skipped")
        self.assertIsNone(rows["cf-0002"]["bare"])
        self.assertEqual(rows["cf-0002"]["prefilled"]["annotator"], "a1")
        self.assertIsNone(rows["cf-0002"]["verdict_agrees"])
        self.assertEqual(report["crossover"]["missing"], ["cf-0002"])
        self.assertEqual((report["crossover"]["verdict_agreement"]["k"], report["crossover"]["verdict_agreement"]["n"]),
                         (1, 1))
        self.assertEqual(len(report["bare"]["readings"]), 1)

    def test_one_labels_file(self):
        paths = self.write()
        report = self.measure(paths[:1])
        rows = {c["item"]: c for c in report["crossover"]["items"]}
        self.assertEqual(rows["cf-0002"]["missing"], "no labels file was made on a copy where it is bare")
        self.assertEqual(rows["cf-0003"]["missing"], "no labels file was made on a copy where it is pre-filled")
        self.assertEqual(report["crossover"]["verdict_agreement"], {"k": 0, "n": 0, "share": None, "wilson95": None})
        self.assertEqual(report["prefilled"]["summary"]["readings"], 2)
        self.assertEqual(report["shared_draft"]["pairs"], [])

    def test_no_done_reading_at_all(self):
        self.write()
        copies = annotation.crossover(BATCH, PLAN)
        skipped = []
        for name in ("a1", "a2"):
            packets = {p["id"]: p for p in copies[name]["items"]}
            items = [reading(packets[i], "different", text="" if annotation.is_bare(packets[i]) else None,
                             chart=[] if annotation.is_bare(packets[i]) else None, status="skipped")
                     for i in ("cf-0001", "cf-0002", "cf-0003")]
            path = self.dir / f"skipped-{name}.json"
            path.write_text(json.dumps(labels_file(name, items)))
            skipped.append(path)
        report = self.measure(skipped)
        self.assertEqual(report["prefilled"]["summary"]["readings"], 0)
        self.assertEqual(report["prefilled"]["summary"]["text"]["mean"], None)
        self.assertEqual([c["missing"] for c in report["crossover"]["items"]],
                         ["the pre-filled reading (a1) is missing or skipped; the bare reading (a2) is missing or "
                          "skipped",
                          "the pre-filled reading (a2) is missing or skipped; the bare reading (a1) is missing or "
                          "skipped"])


class TestCommand(AnchorCase):
    def test_writes_the_report_and_the_table_where_asked(self):
        paths = self.write()
        out = self.dir / "out" / "b00-anchor.json"
        code, text = self.run_main(self.batch_path, *paths, "--out", out)
        self.assertEqual(code, 0, text)
        report = json.loads(out.read_text())
        self.assertEqual(report["format"], anchor.REPORT_FORMAT)
        self.assertEqual(report["batch"], "b00")
        self.assertEqual([entry["batch_copy"].rsplit("/", 1)[-1] for entry in report["inputs"]["labels"]],
                         ["b00-a1.json", "b00-a2.json"])
        self.assertEqual(report["crossover"]["verdict_agreement"]["k"], 1)
        table = out.with_suffix(".txt").read_text()
        self.assertIn("readings on a pre-filled draft: 4 (b00 4)", table)
        self.assertIn("verdict agreement, pre-filled against bare reading: 1 of 2", table)
        self.assertIn("cf-0002   X   a1 equivalent", table)
        self.assertIn(table.strip(), text)

    def test_one_labels_file_is_enough(self):
        paths = self.write()
        code, text = self.run_main(self.batch_path, paths[0], "--out", self.dir / "one.json")
        self.assertEqual(code, 0, text)
        self.assertNotIn(anchor.ONE_FILE_PROBLEM, text)
        self.assertIn("no labels file was made on a copy where it is bare", text)

    def test_label_problems_stop_the_command_unless_ignored(self):
        paths = self.write()
        broken = json.loads(paths[0].read_text())
        broken["items"][0]["text_edited"] = True  # the text equals the draft
        paths[0].write_text(json.dumps(broken))
        out = self.dir / "broken.json"
        code, text = self.run_main(self.batch_path, *paths, "--out", out)
        self.assertEqual(code, 1)
        self.assertIn("text_edited is true", text)
        self.assertIn("nothing measured", text)
        self.assertFalse(out.exists())
        code, _ = self.run_main(self.batch_path, *paths, "--out", out, "--ignore-problems")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.read_text())["label_problems"])

    def test_labels_for_another_batch_are_refused(self):
        paths = self.write()
        other = json.loads(paths[1].read_text())
        other["batch"] = "b99"
        paths[1].write_text(json.dumps(other))
        code, text = self.run_main(self.batch_path, *paths, "--out", self.dir / "x.json")
        self.assertEqual(code, 1)
        self.assertIn("which is not among the batches given", text)


if __name__ == "__main__":
    unittest.main()
