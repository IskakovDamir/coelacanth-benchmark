"""Tests for the annotators' quiz (tools/quiz/make_quiz.py and tools/quiz/import_quiz.py), on synthetic data.

    PYTHONPATH=src python3 -m unittest tests.test_quiz -v
"""

import contextlib
import copy
import csv
import io
import itertools
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "quiz"))

import import_quiz  # noqa: E402
import make_quiz  # noqa: E402
from coelacanth.benchmark.items import RELATIONS  # noqa: E402

COMBOS = list(itertools.combinations(RELATIONS, 3))  # each already in precedence order
REASON = "Synthetic reason sentence that must never reach a page."


def packet(item_id, n_cands):
    return {
        "id": item_id,
        "prefill": {"by": "claude-opus-5-5", "date": "2026-10-06", "status": "draft", "fields": []},
        "claim": {"text": f"Claim {item_id} with a </script> tag and <b>markup</b> in it.", "skeleton": "Skeleton.",
                  "coined_names": [], "date": "2010-01-01", "field": "Medicine", "domain": "Health Sciences",
                  "computational": False,
                  "source_work": {"openalex": "W1", "doi": "https://doi.org/10.5555/x", "title": "Source",
                                  "first_author": "A. Author", "n_authors": 2, "year": 2010, "cache_key": "oa-W1"},
                  "chart": [{"id": "e1", "text": "first", "key": None}, {"id": "e2", "text": "second", "key": None}]},
        "candidates": [{"cid": f"c{k}", "work": {"openalex": f"W{k + 10}", "title": f"Candidate {k}",
                                                  "first_author": "B. Writer", "year": 1990, "cache_key": "x"},
                        "evidence": [{"element": "e1", "quote_candidate": "q cand", "quote_claim": "q claim",
                                      "location_candidate": "abstract", "location_claim": "abstract",
                                      "text_level": "abstract", "general_form": False}] if k % 2 else [],
                        "attribution_hint": "unknown", "note": "Text: abstract only cached."}
                       for k in range(1, n_cands + 1)],
        "notes": "draft"}


def batch(name, first, sizes):
    return {"format": "coelacanth-batch/v1", "batch": name, "created": "2026-10-06", "labels": list(RELATIONS),
            "items": [packet(f"cf-{first + i:04d}", n) for i, n in enumerate(sizes)]}


# 6 items with candidates, and 5 items of which two have none (b02-like). Not named b01: assign gives b01's first 25
# items to a1 and a2, which would leave a3 nothing in a batch this small.
B1 = batch("t01", 1, [3, 1, 2, 2, 1, 4])
B2 = batch("b02", 101, [2, 0, 1, 0, 3])


def proposals_for(batches, part="all"):
    items, k = [], 0
    for b in batches:
        for p in b["items"]:
            cands = []
            for c in p["candidates"]:
                options = list(COMBOS[k % len(COMBOS)])
                cands.append({"cid": c["cid"], "options": options, "model_top": options[k % 3],
                              "model_confidence": 1 + k % 3, "reason": REASON})
                k += 1
            if cands:
                items.append({"id": p["id"], "candidates": cands})
    return {"format": make_quiz.PROPOSALS_FORMAT, "made_by": "synthetic test data", "made_at": "2026-10-09",
            "part": part, "items": items}


class Workspace:
    """A temporary folder with batch files and proposals, and paths for the outputs."""

    def __init__(self, tmp, batches=(B1, B2), proposals=None):
        self.root = Path(tmp)
        (self.root / "batches").mkdir()
        self.batches = []
        for b in batches:
            path = self.root / "batches" / f"{b['batch']}.json"
            path.write_text(json.dumps(b), encoding="utf-8")
            self.batches.append(path)
        self.proposals = []
        for i, data in enumerate(proposals if proposals is not None else [proposals_for(batches)]):
            path = self.root / f"proposals-{i}.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.proposals.append(path)
        self.out = self.root / "send"
        self.assignment = self.root / "assignment.json"

    def make(self, annotators=("a1", "a2", "a3"), **kw):
        return make_quiz.make(self.batches, self.proposals, list(annotators), self.out, self.assignment, **kw)


def page_data(html):
    m = re.search(r'<script type="application/json" id="quiz-data">\n(.*?)\n</script>', html, re.S)
    return json.loads(m.group(1))


class TestMakeQuiz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_pages_and_assignment(self):
        ws = Workspace(self.tmp.name)
        assignment = ws.make()
        self.assertEqual(assignment["counts"]["pairs"], 13 + 6)
        self.assertEqual(assignment["counts"]["items"], 9)
        self.assertEqual(assignment["batches"][1]["dropped_without_candidates"], ["cf-0102", "cf-0104"])
        expected = {(i["id"], c["cid"]): o for i in proposals_for([B1, B2])["items"] for c in i["candidates"]
                    for o in [c["options"]]}
        readers = {}
        for a in ("a1", "a2", "a3"):
            html = (ws.out / f"quiz-{a}.html").read_text(encoding="utf-8")
            for word in ("model_top", "model_confidence", '"reason"', REASON):
                self.assertNotIn(word, html)
            data = page_data(html)
            self.assertEqual(data["annotator"], a)
            self.assertEqual(data["hash"], make_quiz.data_hash(data))
            self.assertEqual(data["hash"], assignment["per_annotator"][a]["hash"])
            self.assertIn("</script>", data["items"][0]["claim"]["text"])  # escaped in the page, intact in the data
            for item in data["items"]:
                self.assertNotIn(item["id"], ("cf-0102", "cf-0104"))
                readers.setdefault(item["id"], []).append(a)
                for c in item["candidates"]:
                    self.assertEqual(c["options"], expected[(item["id"], c["cid"])])
                    self.assertEqual(c["options"], sorted(c["options"], key=RELATIONS.index))
                    self.assertEqual(set(c), {"cid", "work", "note", "evidence", "options"})
                    self.assertNotIn("cache_key", c["work"])
        self.assertEqual(len(readers), 9)
        for item_id, who in readers.items():
            self.assertEqual(len(who), 2, item_id)
            self.assertEqual(len(set(who)), 2, item_id)
        for key, q in assignment["questions"].items():
            item_id = key.split("/")[0]
            self.assertEqual(sorted(q["annotators"]), sorted(readers[item_id]))
        # nothing next to the batches: no assigned/ folder
        self.assertEqual(sorted(p.name for p in (ws.root / "batches").iterdir()), ["b02.json", "t01.json"])

    def test_shared_set_to_first_two(self):
        ws = Workspace(self.tmp.name)
        assignment = ws.make(shared=2)
        self.assertEqual(assignment["batches"][0]["shared"], {"annotators": ["a1", "a2"], "items": ["cf-0001", "cf-0002"]})
        self.assertEqual(assignment["questions"]["cf-0001/c1"]["annotators"], ["a1", "a2"])

    def test_deterministic(self):
        ws = Workspace(self.tmp.name)
        ws.make()
        first = {p.name: p.read_bytes() for p in ws.out.iterdir()}
        first_assignment = ws.assignment.read_bytes()
        ws.make()
        self.assertEqual(first, {p.name: p.read_bytes() for p in ws.out.iterdir()})
        self.assertEqual(first_assignment, ws.assignment.read_bytes())

    def test_shared_override_balances(self):
        ws = Workspace(self.tmp.name)
        assignment = ws.make(shared=0)
        loads = [info["n_items"] for info in assignment["per_annotator"].values()]
        self.assertLessEqual(max(loads) - min(loads), 2)

    def test_two_annotators_get_everything(self):
        ws = Workspace(self.tmp.name)
        assignment = ws.make(annotators=("a1", "a2"))
        self.assertEqual(assignment["per_annotator"]["a1"]["n_questions"], 19)
        self.assertEqual(assignment["per_annotator"]["a2"]["n_questions"], 19)

    def bad(self, mutate, expect):
        props = proposals_for([B1, B2])
        mutate(props)
        ws = Workspace(self.tmp.name, proposals=props if isinstance(props, list) else [props])
        with self.assertRaises(make_quiz.QuizError) as ctx:
            ws.make()
        self.assertTrue(any(expect in p for p in ctx.exception.problems), ctx.exception.problems)
        self.assertFalse(ws.out.exists())
        self.assertFalse(ws.assignment.exists())

    def test_missing_pair(self):
        self.bad(lambda p: p["items"][0]["candidates"].pop(), "no proposal")

    def test_duplicate_pair(self):
        self.bad(lambda p: p["items"].append(copy.deepcopy(p["items"][0])), "proposed twice")

    def test_unknown_pair(self):
        self.bad(lambda p: p["items"].append({"id": "cf-0999", "candidates": [dict(p["items"][0]["candidates"][0])]}),
                 "not a pair of the batches")

    def test_wrong_option_count(self):
        def f(p):
            p["items"][0]["candidates"][0]["options"] = ["equivalent", "partial"]
        self.bad(f, "exactly three")

    def test_unsorted_options(self):
        def f(p):
            c = p["items"][0]["candidates"][0]
            c["options"] = ["partial", "equivalent", "different"]
            c["model_top"] = "partial"
        self.bad(f, "not sorted by precedence")

    def test_repeated_option(self):
        def f(p):
            c = p["items"][0]["candidates"][0]
            c["options"] = ["equivalent", "equivalent", "different"]
            c["model_top"] = "different"
        self.bad(f, "repeat")

    def test_model_top_outside(self):
        def f(p):
            c = p["items"][0]["candidates"][0]
            c["model_top"] = next(r for r in RELATIONS if r not in c["options"])
        self.bad(f, "model_top")

    def test_bad_confidence_and_format(self):
        def f(p):
            p["items"][0]["candidates"][0]["model_confidence"] = 5
        self.bad(f, "model_confidence")

        def g(p):
            p["format"] = "other"
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bad(g, "format")

    def test_split_proposals_merge(self):
        full = proposals_for([B1, B2])
        a = dict(full, part="first", items=full["items"][:3])
        b = dict(full, part="second", items=full["items"][3:])
        ws = Workspace(self.tmp.name, proposals=[a, b])
        assignment = ws.make()
        self.assertEqual(assignment["counts"]["pairs"], 19)
        self.assertEqual([f["part"] for f in assignment["proposals"]], ["first", "second"])

    def test_cli_failure_writes_nothing(self):
        props = proposals_for([B1, B2])
        props["items"].pop()
        ws = Workspace(self.tmp.name, proposals=[props])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = make_quiz.main(["--annotators", "a1", "a2", "a3", "--out", str(ws.out), "--batches",
                                   *map(str, ws.batches), "--proposals", *map(str, ws.proposals), "--assignment",
                                   str(ws.assignment)])
        self.assertEqual(code, 1)
        self.assertIn("nothing written", out.getvalue())
        self.assertFalse(ws.out.exists())

    def test_template_has_previews_and_no_private_fields(self):
        text = make_quiz.TEMPLATE.read_text(encoding="utf-8")
        for mode in ("start", "question", "other", "results"):
            self.assertIn(f'"{mode}"', text)
        for word in ("model_top", "model_confidence", '"reason"'):
            self.assertNotIn(word, text)
        self.assertEqual(len(make_quiz.DATA_RE.findall(text)), 1)
        self.assertNotIn("http://", text.replace("http-equiv", ""))  # no external requests


def results_rows(assignment, annotator, choose=None):
    """Rows as the page exports them, every assigned question answered with letter A unless `choose` says otherwise."""
    info = assignment["per_annotator"][annotator]
    rows = []
    for i, key in enumerate(info["questions"]):
        item, cid = key.split("/")
        options = assignment["questions"][key]["options"]
        letter, relation, text = (choose(i, key, options) if choose else ("A", options[0], ""))
        rows.append({"annotator": annotator, "quiz_hash": info["hash"], "item": item, "candidate": cid,
                     "letter": letter, "relation": relation, "other_text": text,
                     "cross_field": "yes" if i % 2 else "no", "comment": "", "seconds": 30 + i})
    return rows


def to_csv(rows, delimiter=","):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=import_quiz.COLUMNS, delimiter=delimiter, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


class TestImportQuiz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.assignment = self.ws.make()
        self.answers = self.ws.root / "answers"

    def write(self, name, data: bytes):
        path = self.ws.root / name
        path.write_bytes(data)
        return path

    def test_csv_json_tsv(self):
        rows = results_rows(self.assignment, "a1")
        csv_path = self.write("quiz-a1-results.csv", to_csv(rows))
        rec = import_quiz.import_file(csv_path, self.assignment, self.answers)
        self.assertEqual(len(rec["answers"]), self.assignment["per_annotator"]["a1"]["n_questions"])
        self.assertTrue((self.answers / "a1.json").exists())
        payload = {"format": import_quiz.RESULTS_FORMAT, "annotator": "a2",
                   "quiz_hash": self.assignment["per_annotator"]["a2"]["hash"],
                   "answers": [{k: v for k, v in r.items() if k not in ("annotator", "quiz_hash")}
                               for r in results_rows(self.assignment, "a2")]}
        rec2 = import_quiz.import_file(self.write("a2.json", json.dumps(payload).encode()), self.assignment, self.answers)
        self.assertEqual(rec2["annotator"], "a2")
        rec3 = import_quiz.import_file(self.write("a3.tsv", to_csv(results_rows(self.assignment, "a3"), "\t")),
                                       self.assignment, self.answers)
        self.assertEqual(rec3["annotator"], "a3")
        self.assertTrue(all(a["relation"] in RELATIONS for a in rec3["answers"]))

    def problems(self, rows, name="x.csv"):
        with self.assertRaises(import_quiz.ImportProblems) as ctx:
            import_quiz.import_file(self.write(name, to_csv(rows)), self.assignment, self.answers)
        self.assertFalse(self.answers.exists())
        return " | ".join(ctx.exception.problems)

    def test_validation_errors(self):
        rows = results_rows(self.assignment, "a1")
        bad = copy.deepcopy(rows)
        for r in bad:
            r["annotator"] = "zz"
        self.assertIn("unknown annotator", self.problems(bad))
        bad = copy.deepcopy(rows)
        for r in bad:
            r["quiz_hash"] = "0" * 64
        self.assertIn("quiz hash", self.problems(bad))
        self.assertIn("unanswered", self.problems(rows[:-1]))
        self.assertIn("answered twice", self.problems(rows + rows[:1]))
        bad = copy.deepcopy(rows)
        bad[0]["relation"] = next(r for r in RELATIONS if r != bad[0]["relation"])
        self.assertIn("letter A is", self.problems(bad))
        bad = copy.deepcopy(rows)
        bad[0]["letter"] = "other"  # its relation is option A: not an "other" answer
        self.assertIn("not Другое", self.problems(bad))
        bad = copy.deepcopy(rows)
        bad[0].update(letter="other", relation="cannot_decide", other_text="")
        self.assertIn("cannot decide without text", self.problems(bad))
        bad = copy.deepcopy(rows)
        bad[0].update(letter="other", relation="", other_text="")
        self.assertIn("needs text", self.problems(bad))
        bad = copy.deepcopy(rows)
        bad[0]["cross_field"] = ""
        self.assertIn("cross_field", self.problems(bad))
        bad = copy.deepcopy(rows)
        bad[0]["letter"] = "D"
        self.assertIn("letter must be", self.problems(bad))
        other_q = next(k for k in self.assignment["questions"] if k not in self.assignment["per_annotator"]["a1"]["questions"])
        bad = copy.deepcopy(rows)
        bad[0]["item"], bad[0]["candidate"] = other_q.split("/")
        self.assertIn("not a question of a1", self.problems(bad))

    def test_other_answers_accepted(self):
        def choose(i, key, options):
            rest = [r for r in RELATIONS if r not in options]
            return [("other", rest[0], ""), ("other", "cannot_decide", "between two"), ("other", "", "text only"),
                    ("B", "", "")][i % 4]
        rec = import_quiz.import_file(self.write("a1.csv", to_csv(results_rows(self.assignment, "a1", choose))),
                                      self.assignment, self.answers)
        kinds = [(a["relation"] is not None, a["cannot_decide"]) for a in rec["answers"][:4]]
        self.assertEqual(kinds, [(True, False), (False, True), (False, False), (True, False)])
        self.assertEqual(rec["answers"][3]["relation"],
                         self.assignment["questions"][self.assignment["per_annotator"]["a1"]["questions"][3]]["options"][1])

    def test_keeps_first_reading(self):
        path = self.write("a1.csv", to_csv(results_rows(self.assignment, "a1")))
        import_quiz.import_file(path, self.assignment, self.answers)
        with self.assertRaises(import_quiz.ImportProblems):
            import_quiz.import_file(path, self.assignment, self.answers)
        import_quiz.import_file(path, self.assignment, self.answers, replace=True)

    def test_cli_end_to_end(self):
        files = [self.write(f"{a}.csv", to_csv(results_rows(self.assignment, a))) for a in ("a1", "a2", "a3")]
        agreement_path = self.ws.root / "agreement.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = import_quiz.main([*map(str, files), "--assignment", str(self.ws.assignment), "--proposals",
                                     *map(str, self.ws.proposals), "--answers-dir", str(self.answers),
                                     "--agreement", str(agreement_path)])
        self.assertEqual(code, 0)
        report = json.loads(agreement_path.read_text(encoding="utf-8"))
        self.assertEqual(report["pairs"]["two_or_more_readings"], 19)
        # everyone chose option A on every pair: the relation agrees perfectly
        self.assertEqual(report["distinctions"]["relation"]["raw_agreement"], 1.0)
        self.assertEqual(report["anchoring"]["overall"]["letters"]["A"], 1.0)


class TestAgreement(unittest.TestCase):
    """Agreement on a hand-computed case: alpha = 1 - (n - 1)(n - matches) / (n^2 - sum n_c^2)."""

    def setUp(self):
        self.assignment = {"annotators": ["a1", "a2"], "questions": {
            f"cf-000{i}/c1": {"options": ["equivalent", "partial", "different"], "annotators": ["a1", "a2"]}
            for i in range(1, 5)}}

    def answers(self, annotator, relations, cross):
        return {"annotator": annotator, "answers": [
            {"item": f"cf-000{i}", "candidate": "c1", "letter": "A", "relation": r, "cannot_decide": r is None,
             "other_text": "", "cross_field": c, "comment": "", "seconds": 1}
            for i, (r, c) in enumerate(zip(relations, cross), 1)]}

    def test_known_alpha(self):
        # relation: (eq, eq), (eq, partial), (partial, partial), (partial, partial): n = 8, n_eq = 3, n_partial = 5,
        # matches = 6, alpha = 1 - 7 * 2 / (64 - 9 - 25) = 8 / 15
        answers = {"a1": self.answers("a1", ["equivalent", "equivalent", "partial", "partial"], [True, True, False, False]),
                   "a2": self.answers("a2", ["equivalent", "partial", "partial", "partial"], [True, False, False, False])}
        proposals = {(f"cf-000{i}", "c1"): {"options": ["equivalent", "partial", "different"], "model_top": "partial"}
                     for i in range(1, 5)}
        report = import_quiz.agreement(self.assignment, answers, proposals)
        self.assertAlmostEqual(report["distinctions"]["relation"]["alpha"], 8 / 15)
        self.assertAlmostEqual(report["distinctions"]["cross_field"]["alpha"], 8 / 15)
        self.assertEqual(report["distinctions"]["relation"]["raw_agreement"], 0.75)
        # already done: (yes, yes), (yes, no), (no, no), (no, no): the same table
        self.assertAlmostEqual(report["distinctions"]["already_done"]["alpha"], 8 / 15)
        # gold: everything is gold: no variation, alpha undefined
        self.assertIsNone(report["distinctions"]["gold"]["alpha"])
        self.assertEqual(report["anchoring"]["overall"]["chose_model_top"], 5)
        self.assertEqual(report["anchoring"]["overall"]["with_relation"], 8)
        self.assertEqual(report["pairs_of_annotators"]["a1 a2"]["relation"]["pairs"], 4)

    def test_cannot_decide_is_missing(self):
        answers = {"a1": self.answers("a1", [None, "equivalent", "partial", "partial"], [True] * 4),
                   "a2": self.answers("a2", ["equivalent", "equivalent", "partial", "different"], [True] * 4)}
        report = import_quiz.agreement(self.assignment, answers, None)
        self.assertEqual(report["distinctions"]["relation"]["pairable_units"], 3)
        self.assertEqual(report["cannot_decide"]["a1"], 1)
        self.assertIsNone(report["anchoring"]["overall"])


if __name__ == "__main__":
    unittest.main()
