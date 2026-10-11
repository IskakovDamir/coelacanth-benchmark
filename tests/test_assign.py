"""Tests for the assignment of batches to annotators (coelacanth.benchmark.assign).

    PYTHONPATH=src python -m unittest tests.test_assign -v
"""

import contextlib
import copy
import csv
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from itertools import combinations, product
from pathlib import Path

from coelacanth.benchmark import annotation, assign
from coelacanth.quotes import QuoteChecker

REPO = Path(__file__).resolve().parents[1]
TEXTS = {"arxiv-1": "Population models. We estimate the mixing distribution by nonparametric maximum likelihood on a grid.",
         "oa-W2": "A Title\n\nA broadly applicable algorithm for computing maximum likelihood estimates from incomplete data."}
PACKET = {
    "id": "cf-0001",
    "prefill": {"by": "claude-opus-5-5", "date": "2026-10-06", "status": "draft",
                "fields": ["claim.text", "claim.skeleton", "claim.chart", "candidates[].evidence"]},
    "claim": {"text": "Estimate the population distribution of model parameters without a parametric form.",
              "skeleton": "Fit an unknown mixing distribution by maximum likelihood over a grid of support points.",
              "coined_names": ["NPAG"], "date": "2013-03-26", "field": "Pharmacology", "domain": "Life Sciences",
              "computational": True,
              "source_work": {"arxiv": "1", "title": "Two methods", "first_author": "Tatiana Tatarinova",
                              "publication_date": "2013-03-26", "year": 2013, "cache_key": "arxiv-1"},
              "chart": [{"id": "e1", "text": "unknown mixing distribution", "key": None},
                        {"id": "e2", "text": "nonparametric maximum likelihood on a grid", "key": None}]},
    "candidates": [{"cid": "c1", "work": {"openalex": "W2", "title": "EM", "first_author": "A. P. Dempster",
                                          "publication_date": "1977-09-01", "year": 1977, "cache_key": "oa-W2"},
                    "evidence": [{"element": "e2", "quote_candidate": "computing maximum likelihood estimates",
                                  "quote_claim": "nonparametric maximum likelihood on a grid",
                                  "location_candidate": "abstract", "location_claim": "abstract",
                                  "text_level": "abstract", "general_form": False}],
                    "attribution_hint": "unknown", "note": "draft"}],
    "notes": "draft"}
META = {"seed": "p01", "found_by": {"c1": ["mining:kulikowski"]}, "exposure": {"kind": "benchmark_import"},
        "origin": {"kind": "human", "system": None, "mode": None}, "negative": None,
        "provenance": {"source": "kulikowski", "mined_by": "kulikowski_package", "license": "CC-BY-4.0"},
        "exclusions": []}
RELATIONS = list(annotation.RELATIONS)
NAMES = ["a1", "a2", "a3", "a4", "a5", "a6"]


def batch(name, ids):
    """A batch of the given name whose packets are PACKET under each id, in the given order."""
    return {"format": annotation.BATCH_FORMAT, "batch": name, "created": "2026-10-07", "labels": RELATIONS,
            "items": [dict(copy.deepcopy(PACKET), id=i) for i in ids]}


def ids(first, n):
    return [f"cf-{k:04d}" for k in range(first, first + n)]


# b01's shape (30 items, the first 25 shared) and b02's (30 items, 12 in the crossover), in made-up packets
B01 = batch("b01", ids(1, 30))
B02 = batch("b02", ids(37, 30))
X, Y = ids(37, 6), ids(43, 6)
PLAN = {"batch": "b02", "crossover": {"X_prefilled_for_annotator_1": X, "Y_prefilled_for_annotator_2": Y}}


def loads(assignment):
    return [assignment["loads"][a]["items"] for a in assignment["annotators"]]


class TestRule(unittest.TestCase):
    def cases(self):
        """(batch, plan, annotators) for every batch shape and two to six annotators."""
        for n in range(2, 7):
            yield B01, None, NAMES[:n]
            yield B02, PLAN, NAMES[:n]
            for size in range(max(1, (n + 1) // 2), 14):
                yield batch("b00", ids(100, size)), None, NAMES[:n]

    def test_every_item_goes_to_two_different_annotators_and_each_copy_holds_exactly_theirs(self):
        for data, plan, names in self.cases():
            a = assign.assign(data, names, plan)
            self.assertEqual([i["id"] for i in a["items"]], [p["id"] for p in data["items"]])
            for item in a["items"]:
                self.assertEqual(len(item["annotators"]), 2, item)
                self.assertEqual(len(set(item["annotators"])), 2, item)
                self.assertTrue(set(item["annotators"]) <= set(names))
                self.assertEqual(item["annotators"], sorted(item["annotators"], key=names.index))
            copies = assign.personal_copies(data, a)
            self.assertEqual(sorted(copies), sorted(names))
            for name, c in copies.items():
                self.assertEqual(c["annotator"], name)
                mine = [i["id"] for i in a["items"] if name in i["annotators"]]
                self.assertEqual([p["id"] for p in c["items"]], mine)
                self.assertEqual(assign.copy_problems(c, a, name), [])
            held = sorted(p["id"] for c in copies.values() for p in c["items"])
            self.assertEqual(held, sorted(2 * [p["id"] for p in data["items"]]))
            self.assertEqual(sum(loads(a)), 2 * len(data["items"]))

    def test_with_two_annotators_each_copy_is_the_whole_batch_naming_its_annotator(self):
        copies = assign.personal_copies(B01, assign.assign(B01, ["a1", "a2"]))
        for name, c in copies.items():
            self.assertEqual(list(c)[:3], ["format", "batch", "annotator"])
            self.assertEqual({k: v for k, v in c.items() if k != "annotator"}, B01)

    def test_with_two_annotators_and_a_plan_the_copies_are_the_crossover_copies(self):
        mine = assign.personal_copies(B02, assign.assign(B02, ["a1", "a2"], PLAN))
        theirs = annotation.crossover(B02, PLAN, ("a1", "a2"))
        for name in ("a1", "a2"):
            self.assertEqual(json.dumps(mine[name]), json.dumps(theirs[name]))

    def test_the_first_25_items_of_b01_go_to_the_first_two_annotators(self):
        shuffled = dict(B01, items=list(reversed(B01["items"])))  # by ID, not by position in the file
        for n in range(2, 7):
            for names in (NAMES[:n], [f"s{k}" for k in range(n)]):
                a = assign.assign(shuffled, names)
                by_id = {i["id"]: i for i in a["items"]}
                for item_id in ids(1, 25):
                    self.assertEqual(by_id[item_id]["annotators"], names[:2])
                    self.assertEqual(by_id[item_id]["reason"], assign.REASONS["shared"])
                self.assertEqual(a["shared"], {"annotators": names[:2], "items": ids(1, 25)})
                self.assertEqual({by_id[i]["reason"] for i in ids(26, 5)}, {assign.REASONS["rotation"]})
        other = assign.assign(batch("b00", ids(1, 30)), NAMES[:4])
        self.assertEqual(other["shared"]["items"], [])

    def test_crossover_groups_go_to_a1_and_a2_and_are_bare_for_the_right_one(self):
        for n in range(2, 7):
            a = assign.assign(B02, NAMES[:n], PLAN)
            by_id = {i["id"]: i for i in a["items"]}
            for item_id in X:
                self.assertEqual((by_id[item_id]["annotators"], by_id[item_id]["bare_for"]), (["a1", "a2"], ["a2"]))
            for item_id in Y:
                self.assertEqual((by_id[item_id]["annotators"], by_id[item_id]["bare_for"]), (["a1", "a2"], ["a1"]))
            for item_id in set(by_id) - set(X + Y):
                self.assertEqual(by_id[item_id]["bare_for"], [])
            copies = assign.personal_copies(B02, a)
            bare = {name: [p["id"] for p in c["items"] if annotation.is_bare(p)] for name, c in copies.items()}
            self.assertEqual(bare["a1"], Y)
            self.assertEqual(bare["a2"], X)
            for name in NAMES[2:n]:
                self.assertEqual(bare[name], [])
                self.assertFalse({p["id"] for p in copies[name]["items"]} & set(X + Y))
            for c in copies.values():
                for packet in c["items"]:
                    original = next(p for p in B02["items"] if p["id"] == packet["id"])
                    self.assertEqual(packet, annotation.strip_prefill(original) if annotation.is_bare(packet)
                                     else original)
            self.assertEqual(a["loads"]["a1"]["bare"], 6)
            self.assertEqual(a["loads"]["a2"]["bare"], 6)

    def test_loads_are_within_one_item_and_pairs_rotate(self):
        for n in range(2, 7):
            for size in range(max(1, (n + 1) // 2), 61):
                a = assign.assign(batch("b00", ids(100, size)), NAMES[:n])
                self.assertLessEqual(max(loads(a)) - min(loads(a)), 1, (n, size, loads(a)))
                pairs = [a["pairs"].get(f"{p} {q}", 0) for p, q in combinations(NAMES[:n], 2)]
                # within one item up to five annotators (the docstring's promise); six can reach two
                self.assertLessEqual(max(pairs) - min(pairs), 1 if n <= 5 else 2, (n, size, a["pairs"]))
        # the first items already rotate: three annotators, three items, three different pairs
        self.assertEqual(assign.assign(batch("b00", ids(1, 3)), NAMES[:3])["pairs"], {"a1 a2": 1, "a1 a3": 1, "a2 a3": 1})
        # the items fixed to a1 and a2 decide the rest: b01 and b02 with three and four annotators
        self.assertEqual(loads(assign.assign(B01, NAMES[:3])), [28, 27, 5])
        self.assertEqual(loads(assign.assign(B02, NAMES[:3], PLAN)), [21, 21, 18])
        self.assertEqual(loads(assign.assign(B02, NAMES[:4], PLAN)), [15, 15, 15, 15])
        self.assertEqual(loads(assign.assign(B02, NAMES[:2], PLAN)), [30, 30])

    def test_with_items_fixed_to_a1_and_a2_the_largest_load_is_as_small_as_it_can_be(self):
        for n, shared, free in product((3, 4), range(0, 4), range(1, 5)):
            best_max, best_min = None, 0  # over every way of giving the free items to pairs
            for choice in product(list(combinations(range(n), 2)), repeat=free):
                load = [shared, shared] + [0] * (n - 2)
                for p, q in choice:
                    load[p] += 1
                    load[q] += 1
                best_max = max(load) if best_max is None else min(best_max, max(load))
                best_min = max(best_min, min(load))
            try:
                a = assign.assign(batch("b00", ids(1, shared + free)), NAMES[:n], shared=shared)
            except ValueError as err:
                self.assertIn("would get no item", str(err))
                self.assertEqual(best_min, 0, (n, shared, free))  # no way gives everyone an item
                continue
            self.assertEqual((max(loads(a)), min(loads(a))), (best_max, best_min), (n, shared, free, loads(a)))

    def test_the_same_inputs_give_the_same_assignment(self):
        for data, plan, names in self.cases():
            self.assertEqual(assign.assign(data, names, plan), assign.assign(copy.deepcopy(data), list(names), plan))

    def test_errors(self):
        for args, needle in [((B01, ["a1"]), "at least two annotators"),
                             ((B01, ["a1", "a1"]), "repeat"),
                             ((B01, ["a1", "a 2"]), "letters, digits"),
                             ((dict(B01, annotator="a1"), ["a1", "a2"]), "already the copy"),
                             ((B01, ["a1", "a2"], PLAN), "the plan is for batch"),
                             ((B02, ["a1", "a2"], {"batch": "b02", "crossover": {}}), "no crossover groups"),
                             ((batch("b00", ids(1, 2)), NAMES[:6]), "would get no item"),
                             ((dict(B01, items=B01["items"] + B01["items"][:1]), ["a1", "a2"]), "unique ids")]:
            with self.assertRaises(ValueError) as caught:
                assign.assign(*args)
            self.assertIn(needle, str(caught.exception))

    def test_tracker_has_one_row_per_annotator_and_item(self):
        a = assign.assign(B02, NAMES[:3], PLAN)
        rows = list(csv.DictReader(io.StringIO(assign.tracker_csv(a))))
        self.assertEqual(list(rows[0]), list(assign.TRACKER_COLUMNS))
        self.assertEqual(len(rows), 60)
        self.assertEqual({r["status"] for r in rows}, {"assigned"})
        self.assertEqual({(r["annotator"], r["item"]) for r in rows},
                         {(name, i["id"]) for i in a["items"] for name in i["annotators"]})

    def test_hours_follow_the_brief(self):
        self.assertEqual(assign.estimated_hours(80), {"central": 46.9, "low": 24.1, "high": 89.9})
        self.assertEqual(assign.estimated_hours(30)["central"], 17.6)


class TestCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for key, text in TEXTS.items():
            (self.dir / f"{key}.txt").write_text(text)
        self.checker = QuoteChecker(lambda key: TEXTS.get(key))
        self.batch = batch("b02", ids(37, 30))
        self.batch_path = self.dir / "b02.json"
        self.batch_path.write_text(json.dumps(self.batch))
        self.meta = {"format": annotation.META_FORMAT, "batch": "b02",
                     "items": {p["id"]: copy.deepcopy(META) for p in self.batch["items"]}}
        (self.dir / "b02-meta.json").write_text(json.dumps(self.meta))
        self.plan_path = self.dir / "b02-plan.json"
        self.plan_path.write_text(json.dumps(PLAN))

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                unittest.mock.patch.object(annotation, "FULLTEXT_DIR", self.dir), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = assign.main([str(self.batch_path), *args])
        out = printed.getvalue()
        return code, (json.loads(out) if out.startswith("{") else out)

    def files(self, out_dir):
        return {p.name: p.read_bytes() for p in sorted(Path(out_dir).iterdir())}

    def test_command_writes_the_copies_the_assignment_and_the_tracker_and_checks_the_copies(self):
        code, report = self.run_main("--annotators", "a1", "a2", "a3", "--plan", str(self.plan_path))
        self.assertEqual(code, 0, report)
        out = self.dir / "assigned"
        self.assertEqual(sorted(self.files(out)), ["b02-a1.json", "b02-a2.json", "b02-a3.json",
                                                   "b02-assignment.json", "b02-tracker.csv"])
        self.assertEqual({k: (v["items"], v["bare"], v["problems"]) for k, v in report["copies"].items()},
                         {"b02-a1.json": (21, 6, {}), "b02-a2.json": (21, 6, {}), "b02-a3.json": (18, 0, {})})
        self.assertEqual(report["meta"], str(self.dir / "b02-meta.json"))
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                unittest.mock.patch.object(annotation, "FULLTEXT_DIR", self.dir):
            for name in ("a1", "a2", "a3"):
                self.assertEqual(annotation.check_batch(out / f"b02-{name}.json"), {})
        written = json.loads((out / "b02-assignment.json").read_text())
        self.assertEqual(written, assign.assign(self.batch, ["a1", "a2", "a3"], PLAN))
        self.assertEqual((out / "b02-tracker.csv").read_text(), assign.tracker_csv(written))
        # labels of another annotator are refused against a copy, as with the crossover copies
        a3 = json.loads((out / "b02-a3.json").read_text())
        labels = {"format": annotation.LABELS_FORMAT, "batch": "b02", "annotator": "a1", "items": []}
        self.assertTrue(any("copy for annotator a3" in x for x in annotation.labels_problems(a3, labels)))

    def test_meta_problems_of_the_items_are_reported_although_the_copies_have_no_meta_file_next_to_them(self):
        self.meta["items"]["cf-0040"]["found_by"] = {}
        del self.meta["items"]["cf-0041"]
        (self.dir / "b02-meta.json").write_text(json.dumps(self.meta))
        code, report = self.run_main("--annotators", "a1", "a2")
        self.assertEqual(code, 1)
        problems = report["copies"]["b02-a1.json"]["problems"]
        self.assertEqual(problems["cf-0040"], ["meta: found_by is empty for c1"])
        self.assertEqual(problems["cf-0041"], ["meta: no entry in b02-meta.json"])

    def test_the_plan_next_to_the_batch_is_used_without_plan(self):
        code, report = self.run_main("--annotators", "a1", "a2")
        self.assertEqual(code, 0, report)
        self.assertTrue(report["plan"].endswith("(found next to the batch)"))
        self.assertEqual(report["copies"]["b02-a1.json"]["bare_items"], Y)
        self.plan_path.unlink()
        code, report = self.run_main("--annotators", "a1", "a2", "--out-dir", str(self.dir / "other"))
        self.assertEqual((code, report["plan"], report["copies"]["b02-a1.json"]["bare"]), (0, None, 0))

    def test_the_same_inputs_write_the_same_files(self):
        self.run_main("--annotators", "a1", "a2", "a3", "--out-dir", str(self.dir / "one"))
        self.run_main("--annotators", "a1", "a2", "a3", "--out-dir", str(self.dir / "two"))
        self.assertEqual(self.files(self.dir / "one"), self.files(self.dir / "two"))

    def test_a_tracker_with_entries_is_kept_and_one_for_another_assignment_stops_the_run(self):
        self.run_main("--annotators", "a1", "a2", "a3")
        out = self.dir / "assigned"
        tracker = out / "b02-tracker.csv"
        edited = tracker.read_text().replace("a1,cf-0037,assigned,,,,", "a1,cf-0037,returned,41,2026-10-08,,")
        tracker.write_text(edited)
        code, report = self.run_main("--annotators", "a1", "a2", "a3")
        self.assertEqual(code, 0)
        self.assertIn("kept", report["tracker"])
        self.assertEqual(tracker.read_text(), edited)
        before = self.files(out)
        code, printed = self.run_main("--annotators", "a1", "a2")
        self.assertEqual(code, 1)
        self.assertIn("nothing written", printed)
        self.assertIn("another assignment", printed)
        self.assertEqual(self.files(out), before)

    def test_decision_19_damir_as_second_reader_and_check_labels_with_the_meta_beside_the_copy(self):
        # eval/protocol.md decision 19: with one annotator, Damir gives the second reading from his own copy
        code, report = self.run_main("--annotators", "a1", "damir")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["copies"]["b02-damir.json"]["bare_items"], X)
        self.assertEqual(report["copies"]["b02-a1.json"]["bare_items"], Y)
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        copy_path = self.dir / "assigned" / "b02-damir.json"
        script = self.dir / "export.js"
        script.write_text(NODE_EXPORT)
        done = subprocess.run([node, str(script), str(REPO / "tools" / "annotate.html"), str(copy_path)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        labels_path = self.dir / "b02-damir-labels.json"
        labels_path.write_text(done.stdout)
        self.assertEqual(json.loads(done.stdout)["annotator"], "damir")
        # a copy in assigned/ finds the batch's meta one folder up ...
        problems = annotation.check_labels(copy_path, labels_path, offline=True)
        self.assertNotIn("meta: b02-meta.json not found next to the batch", problems)
        # ... and passes with the copy and the batch's meta copied into one scratch folder
        # (docs/annotators/dlya-damira.md, section 2)
        scratch = self.dir / "scratch"
        scratch.mkdir()
        shutil.copy(copy_path, scratch)
        shutil.copy(self.dir / "b02-meta.json", scratch)
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker):
            self.assertEqual(annotation.check_labels(scratch / "b02-damir.json", labels_path, offline=True), [])

    def test_annotate_html_accepts_the_copies_and_exports_labels_its_rules_pass(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        code, report = self.run_main("--annotators", "a1", "a2", "a3")
        self.assertEqual(code, 0, report)
        paths = [str(self.dir / "assigned" / name) for name in report["copies"]]
        script = self.dir / "check.js"
        script.write_text(NODE_CHECK)
        done = subprocess.run([node, str(script), str(REPO / "tools" / "annotate.html"), *paths],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        for path in paths:
            self.assertEqual(result[path], {"shape": [], "labels": [], "annotator": Path(path).stem.split("-")[1],
                                            "bare": report["copies"][Path(path).name]["bare"]})


# Loads each copy into the page's pure core: the batch checks the page runs on loading a file, and a labels file of
# untouched items exported by the page's own functions, checked by the page's rules.
NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.slice(html.indexOf("<script>") + "<script>".length, html.lastIndexOf("</script>"));
const core = script.slice(script.indexOf("// CORE-BEGIN"), script.indexOf("// CORE-END"));
const Core = vm.runInNewContext(`(function () { "use strict";\n${core}\nreturn Core; })()`, {});
const out = {};
for (const file of process.argv.slice(3)) {
  const batch = JSON.parse(fs.readFileSync(file, "utf8"));
  const labels = {format: Core.LABELS_FORMAT, batch: batch.batch, annotator: batch.annotator,
                  exported_at: "2026-10-07T00:00:00Z",
                  items: batch.items.map(p => Core.exportItem(p, Core.freshItemState(p)))};
  out[file] = {shape: Array.from(Core.batchShapeProblems(batch)), labels: Array.from(Core.labelsProblems(batch, labels)),
               annotator: batch.annotator, bare: batch.items.filter(Core.isBare).length};
}
console.log(JSON.stringify(out));
"""

# The labels file the page exports for a copy whose items are all untouched (every item skipped).
NODE_EXPORT = r"""
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.slice(html.indexOf("<script>") + "<script>".length, html.lastIndexOf("</script>"));
const core = script.slice(script.indexOf("// CORE-BEGIN"), script.indexOf("// CORE-END"));
const Core = vm.runInNewContext(`(function () { "use strict";\n${core}\nreturn Core; })()`, {});
const batch = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify({format: Core.LABELS_FORMAT, batch: batch.batch, annotator: batch.annotator,
                            exported_at: "2026-10-09T00:00:00Z",
                            items: batch.items.map(p => Core.exportItem(p, Core.freshItemState(p)))}));
"""


if __name__ == "__main__":
    unittest.main()
