"""Tests for the quiz adjudication (tools/quiz/adjudicate_quiz.py), on synthetic data.

    PYTHONPATH=src python3 -m unittest tests.test_quiz_adjudicate -v
"""

import contextlib
import copy
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "quiz"))

import adjudicate_quiz as adj  # noqa: E402
import import_quiz  # noqa: E402
from coelacanth.benchmark.items import RELATIONS  # noqa: E402

PRIVATE_REASON = "Private model reason sentence that must never reach the adjudication page."

# item id -> (meta: role, negative, origin kind), candidates: cid -> attribution_hint
ITEMS = {
    "cf-0001": (("non_attributed", None, "human"), {"c1": "not_cited", "c2": "not_cited"}),
    "cf-0002": (("attributed_control", None, "human"), {"c1": "not_cited"}),
    "cf-0003": (("natural_distractor", {"kind": "natural_distractor"}, "human"), {"c1": "not_cited"}),
    "cf-0004": (("non_attributed", None, "human"), {"c1": "cited"}),
    "cf-0005": ((None, None, "ai"), {"c1": "unknown"}),
    "cf-0006": (("non_attributed", None, "human"), {"c1": "not_cited"}),
}

# (item, cid) -> reading of a1, reading of a2: (relation or "cannot" or None for text only, cross_field, comment)
READINGS = {
    ("cf-0001", "c1"): (("partial", True, ""), ("partial", True, "same")),
    ("cf-0001", "c2"): (("equivalent", False, "a1 thinks equivalent"), ("different", False, "a2 thinks different")),
    ("cf-0002", "c1"): (("similar_mechanism", True, ""), ("similar_mechanism", True, "")),
    ("cf-0003", "c1"): (("partial", True, ""), ("partial", False, "same field to me")),
    ("cf-0004", "c1"): (("partial", True, ""), ("partial", True, "")),
    ("cf-0005", "c1"): (("cannot", True, "unclear"), ("different", True, "")),
    ("cf-0006", "c1"): ((None, True, "my own words"), ("partial", True, "")),
}


def packet(item_id, hints):
    return {"id": item_id, "prefill": {"by": "claude-opus-5-5", "status": "draft"},
            "claim": {"text": f"Claim {item_id} with </script> and <b>markup</b>.", "skeleton": "Skeleton.",
                      "date": "2010-01-01", "field": "Medicine",
                      "source_work": {"openalex": "W1", "title": "Source", "first_author": "A. Author", "year": 2010},
                      "chart": [{"id": "e1", "text": "first element", "key": None},
                                {"id": "e2", "text": "second element", "key": None}]},
            "candidates": [{"cid": cid, "work": {"openalex": f"W{n + 10}", "doi": f"10.5555/{n}", "arxiv": "1234.5678",
                                                 "pmid": "999", "title": f"Candidate {item_id} {cid}",
                                                 "first_author": "B. Writer", "year": 1990, "venue": "Journal",
                                                 "field": "Physics"},
                            "evidence": [{"element": "e1", "quote_candidate": f"cand quote {item_id} {cid}",
                                          "quote_claim": "claim quote", "location_candidate": "abstract",
                                          "location_claim": "abstract", "text_level": "abstract",
                                          "general_form": False}],
                            "attribution_hint": hint, "note": "Text: abstract only cached."}
                           for n, (cid, hint) in enumerate(hints.items())],
            "notes": "draft"}


def meta(role, negative, origin):
    return {"seed": "x", "found_by": {}, "provenance": {"source": "kulikowski", "mined_by": "kulikowski_package"},
            "origin": {"kind": origin, "system": None, "mode": None}, "negative": negative, "exclusions": [],
            "seed_detail": {"role": role} if role else {}}


def answer(item, cid, reading):
    rel, cross, comment = reading
    row = {"item": item, "candidate": cid, "letter": "A", "relation": rel, "cannot_decide": False, "other_text": "",
           "cross_field": cross, "comment": comment, "seconds": 10.0}
    if rel == "cannot":
        row.update(letter="other", relation=None, cannot_decide=True, other_text="cannot tell")
    elif rel is None:
        row.update(letter="other", other_text="an answer in words")
    return row


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        bdir = root / "batches"
        bdir.mkdir()
        self.batch = bdir / "t01.json"
        self.batch.write_text(json.dumps({"format": "coelacanth-batch/v1", "batch": "t01", "labels": list(RELATIONS),
                                          "items": [packet(i, h) for i, (_, h) in ITEMS.items()]}), encoding="utf-8")
        (bdir / "t01-meta.json").write_text(json.dumps({"format": "coelacanth-batch-meta/v1", "batch": "t01",
                                                        "items": {i: meta(*m) for i, (m, _) in ITEMS.items()}}),
                                            encoding="utf-8")
        qdir = root / "quiz"
        qdir.mkdir()
        self.assignment = qdir / "assignment.json"
        questions = {f"{i}/{c}": {"batch": "t01", "annotators": ["a1", "a2"], "options": ["partial", "similar_mechanism",
                                                                                          "different"]}
                     for (i, c) in READINGS}
        self.assignment.write_text(json.dumps({
            "format": "coelacanth-quiz-assignment/v1", "annotators": ["a1", "a2"], "questions": questions,
            "batch_files": [{"file": "eval/benchmark/v0/batches/t01.json",
                             "sha256": hashlib.sha256(self.batch.read_bytes()).hexdigest()}]}), encoding="utf-8")
        self.proposals = qdir / "proposals-t01.json"
        self.proposals.write_text(json.dumps({"format": "coelacanth-quiz-proposals/v1", "made_by": "model: test",
                                              "items": [{"id": i, "candidates": [
                                                  {"cid": c, "options": ["partial", "similar_mechanism", "different"],
                                                   "model_top": "partial", "model_confidence": 2,
                                                   "reason": PRIVATE_REASON + f" {i} {c}"} for c in h]}
                                                  for i, (_, h) in ITEMS.items()]}), encoding="utf-8")
        self.answers = qdir / "answers"
        self.answers.mkdir()
        for n, a in enumerate(("a1", "a2")):
            self.write_answers(a, [answer(i, c, r[n]) for (i, c), r in READINGS.items()])
        self.out = qdir / "adjudication"
        self.labels = qdir / "labels.json"

    def write_answers(self, annotator, rows):
        (self.answers / f"{annotator}.json").write_text(json.dumps(
            {"format": import_quiz.ANSWERS_FORMAT, "annotator": annotator, "quiz_hash": "h", "answers": rows}),
            encoding="utf-8")

    def report(self):
        return adj.report(self.assignment, self.answers, self.out, [self.batch], [self.proposals])

    def apply(self, decisions_path):
        return adj.apply(decisions_path, self.assignment, self.answers, self.labels, [self.batch])

    def decide(self, choices=None, path=None):
        """Fill the template: choices maps key -> (decision, reason); the rest gets the first allowed value."""
        d = json.loads((self.out / "decisions.json").read_text(encoding="utf-8"))
        for e in d["entries"]:
            e["decision"], e["reason"] = (choices or {}).get(e["key"], (e["allowed"][0], "test reason"))
        path = path or (self.root / "filled.json")
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return path


class AdjudicateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Fixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------ report

    def test_template_lists_only_disagreements(self):
        t = self.f.report()
        keys = [e["key"] for e in t["entries"]]
        self.assertEqual(keys, ["cf-0001/c2/relation", "cf-0003/c1/cross_field", "cf-0005/c1/relation",
                                "cf-0006/c1/relation"])
        by = {e["key"]: e for e in t["entries"]}
        self.assertEqual(by["cf-0001/c2/relation"]["readings"],
                         [{"annotator": "a1", "value": "equivalent", "comment": "a1 thinks equivalent"},
                          {"annotator": "a2", "value": "different", "comment": "a2 thinks different"}])
        self.assertEqual(by["cf-0001/c2/relation"]["allowed"], list(RELATIONS) + ["exclude"])
        self.assertEqual(by["cf-0003/c1/cross_field"]["allowed"], ["yes", "no"])
        self.assertEqual([r["value"] for r in by["cf-0003/c1/cross_field"]["readings"]], ["yes", "no"])
        self.assertEqual(by["cf-0005/c1/relation"]["readings"][0]["value"], "cannot_decide")
        self.assertEqual(by["cf-0006/c1/relation"]["readings"][0]["value"], None)
        self.assertEqual(by["cf-0006/c1/relation"]["readings"][0]["other_text"], "an answer in words")
        for e in t["entries"]:
            self.assertEqual((e["decision"], e["reason"]), ("", ""))
        self.assertEqual(t["adjudicator"], "damir")
        self.assertEqual(t["inputs"]["assignment"]["sha256"],
                         hashlib.sha256(self.f.assignment.read_bytes()).hexdigest())
        self.assertEqual({Path(r["file"]).name: r["sha256"] for r in t["inputs"]["answers"]},
                         {f"{a}.json": hashlib.sha256((self.f.answers / f"{a}.json").read_bytes()).hexdigest()
                          for a in ("a1", "a2")})

    def test_both_cannot_decide_is_a_disagreement(self):
        rows = [answer(i, c, ("cannot", True, "") if (i, c) == ("cf-0002", "c1") else r[0])
                for (i, c), r in READINGS.items()]
        self.f.write_answers("a1", rows)
        self.f.write_answers("a2", [dict(r) for r in rows])
        keys = [e["key"] for e in self.f.report()["entries"]]
        self.assertEqual(keys, ["cf-0002/c1/relation", "cf-0005/c1/relation", "cf-0006/c1/relation"])

    def test_report_is_deterministic(self):
        self.f.report()
        first = {p.name: p.read_bytes() for p in self.f.out.iterdir()}
        self.f.report()
        self.assertEqual(first, {p.name: p.read_bytes() for p in self.f.out.iterdir()})
        self.assertEqual(set(first), {"decisions.json", "disagreements.html", "disagreements.md"})

    def test_page_is_self_contained_and_private_free(self):
        t = self.f.report()
        page = (self.f.out / "disagreements.html").read_text(encoding="utf-8")
        self.assertNotIn("model_top", page)
        self.assertNotIn("model_confidence", page)
        self.assertNotIn(PRIVATE_REASON, page)
        self.assertIsNone(re.search(r'<(?:script|link|img|iframe)[^>]+(?:src|href)=["\']?https?:', page))
        self.assertNotIn("@import", page)
        self.assertIn("Content-Security-Policy", page)
        self.assertIn('lang="ru"', page)
        self.assertIn("prefers-color-scheme: dark", page)
        data = json.loads(re.search(r'<script type="application/json" id="adj-data">\n(.*?)\n</script>', page,
                                    re.S).group(1))
        self.assertEqual(data["decisions"], t)
        self.assertEqual([i["id"] for i in data["items"]], ["cf-0001", "cf-0003", "cf-0005", "cf-0006"])
        item = data["items"][0]
        self.assertEqual([c["cid"] for c in item["candidates"]], ["c2"])  # only candidates with a disagreement
        cand = item["candidates"][0]
        self.assertEqual(cand["work"]["doi"], "10.5555/1")
        self.assertEqual(cand["evidence"][0]["quote_candidate"], "cand quote cf-0001 c2")
        self.assertEqual(item["claim"]["chart"], [{"id": "e1", "text": "first element"},
                                                  {"id": "e2", "text": "second element"}])
        self.assertEqual(item["prefill_by"], "claude-opus-5-5")
        self.assertNotIn("attribution_hint", json.dumps(data))
        self.assertEqual(page.count("</script>"), 2)  # the claim's </script> is escaped inside the data

    def test_page_refuses_a_leaked_private_reason(self):
        rows = [answer(i, c, r[0]) for (i, c), r in READINGS.items()]
        rows[1]["comment"] = PRIVATE_REASON + " cf-0001 c2"
        self.f.write_answers("a1", rows)
        with self.assertRaises(adj.AdjudicationProblems) as err:
            self.f.report()
        self.assertIn("private proposal content", str(err.exception))
        data = {"decisions": {"entries": []}, "items": [{"model_top": "partial"}]}
        with self.assertRaises(adj.AdjudicationProblems):
            adj.render_page(adj.TEMPLATE.read_text(encoding="utf-8"), data)

    def test_summary_counts(self):
        self.f.report()
        md = (self.f.out / "disagreements.md").read_text(encoding="utf-8")
        self.assertIn("Disagreements: 4 (3 on the relation, 1 on cross-field), on 4 pairs.", md)
        self.assertIn("| a1 a2 | 7 | 3 | 1 |", md)
        self.assertIn("- `cf-0001/c2/relation`: a1 equivalent, a2 different", md)
        self.assertIn("- `cf-0006/c1/relation`: a1 (text only), a2 partial", md)

    def test_changed_batch_is_refused(self):
        self.f.batch.write_text(self.f.batch.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaises(adj.AdjudicationProblems) as err:
            self.f.report()
        self.assertIn("SHA-256 differs", str(err.exception))

    # ------------------------------------------------------------ apply

    def assert_refused(self, path, fragment):
        with self.assertRaises(adj.AdjudicationProblems) as err:
            self.f.apply(path)
        self.assertTrue(any(fragment in p for p in err.exception.problems), err.exception.problems)
        self.assertFalse(self.f.labels.exists())

    def test_apply_refuses_undecided_and_unknown_and_empty_reason(self):
        self.f.report()
        self.assert_refused(self.f.out / "decisions.json", "not decided")
        self.assert_refused(self.f.decide({"cf-0001/c2/relation": ("maybe", "r")}), "is not one of")
        self.assert_refused(self.f.decide({"cf-0003/c1/cross_field": ("exclude", "r")}), "is not one of")
        self.assert_refused(self.f.decide({"cf-0001/c2/relation": ("partial", "  ")}), "reason is empty")

    def test_apply_refuses_changed_inputs(self):
        self.f.report()
        path = self.f.decide()
        rows = [answer(i, c, r[1]) for (i, c), r in READINGS.items()]
        rows[0]["comment"] = "edited later"
        self.f.write_answers("a2", rows)
        self.assert_refused(path, "answers file a2.json changed")
        self.f.report()
        path = self.f.decide()
        self.f.assignment.write_text(self.f.assignment.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assert_refused(path, "the assignment changed")

    def test_apply_refuses_edited_or_missing_entries(self):
        self.f.report()
        d = json.loads(self.f.decide().read_text(encoding="utf-8"))
        tampered = copy.deepcopy(d)
        tampered["entries"][0]["readings"][0]["value"] = "partial"
        p = self.f.root / "t.json"
        p.write_text(json.dumps(tampered), encoding="utf-8")
        self.assert_refused(p, "fields changed from the template")
        short = copy.deepcopy(d)
        short["entries"].pop()
        p.write_text(json.dumps(short), encoding="utf-8")
        self.assert_refused(p, "missing from the decisions")
        other = copy.deepcopy(d)
        other["adjudicator"] = "someone"
        p.write_text(json.dumps(other), encoding="utf-8")
        self.assert_refused(p, "adjudicator must be")

    def test_apply_writes_labels(self):
        self.f.report()
        labels = self.f.apply(self.f.decide({
            "cf-0001/c2/relation": ("partial", "two key elements covered"),
            "cf-0003/c1/cross_field": ("yes", "different discipline"),
            "cf-0005/c1/relation": ("similar_mechanism", "same form"),
            "cf-0006/c1/relation": ("exclude", "candidate not earlier than the claim")}))
        self.assertEqual(json.loads(self.f.labels.read_text(encoding="utf-8")), labels)
        pairs = {(p["item"], p["candidate"]): p for p in labels["pairs"]}
        self.assertEqual(len(pairs), 7)
        p = pairs[("cf-0001", "c1")]
        self.assertEqual((p["relation"], p["cross_field"], p["gold"]), ("partial", True, True))
        self.assertEqual(p["decided_by"], {"relation": "agreed: a1,a2", "cross_field": "agreed: a1,a2"})
        p = pairs[("cf-0001", "c2")]
        self.assertEqual((p["relation"], p["cross_field"]), ("partial", False))
        self.assertEqual(p["decided_by"], {"relation": "adjudicated: damir", "cross_field": "agreed: a1,a2"})
        self.assertEqual(p["adjudication_reason"], {"relation": "two key elements covered"})
        self.assertEqual(pairs[("cf-0003", "c1")]["decided_by"]["cross_field"], "adjudicated: damir")
        self.assertTrue(pairs[("cf-0003", "c1")]["cross_field"])
        p = pairs[("cf-0006", "c1")]
        self.assertTrue(p["excluded"])
        self.assertIsNone(p["relation"])
        self.assertFalse(p["gold"])
        self.assertEqual(labels["excluded_pairs"], ["cf-0006/c1"])

        items = labels["items"]
        self.assertTrue(items["cf-0001"]["in_R_provisional"])
        self.assertEqual(items["cf-0001"]["gold_candidates"], ["c1", "c2"])
        self.assertEqual(items["cf-0001"]["cross_field_gold_candidates"], ["c1"])
        self.assertEqual(items["cf-0001"]["candidates"]["c1"]["attribution_hint"],
                         {"value": "not_cited", "status": adj.HINT_STATUS})
        self.assertIn("not confirmed by people", adj.HINT_STATUS)
        self.assertEqual(items["cf-0001"]["source"], "kulikowski")
        self.assertEqual(items["cf-0001"]["origin_kind"], "human")
        self.assertEqual(items["cf-0002"]["not_in_R_because"], ["attributed control (C)"])
        self.assertIn("built negative (natural_distractor)", items["cf-0003"]["not_in_R_because"])
        self.assertEqual(items["cf-0004"]["not_in_R_because"],
                         ["every cross-field gold candidate has attribution_hint 'cited'"])
        self.assertEqual(items["cf-0005"]["not_in_R_because"], ["origin ai"])
        self.assertEqual(items["cf-0006"]["not_in_R_because"], ["no cross-field gold candidate"])
        self.assertEqual(labels["in_R_provisional"], ["cf-0001"])
        self.assertEqual(labels["counts"]["built_negatives_with_gold"], ["cf-0003"])
        self.assertEqual(labels["counts"]["attributed_controls_with_cross_field_gold"], ["cf-0002"])
        self.assertEqual(labels["counts"]["relation_decided_by"], {"agreed": 4, "adjudicated": 3})
        notes = " ".join(labels["notes"])
        self.assertIn("Attribution was not asked in the quiz", notes)
        self.assertIn("closed-book", notes)
        self.assertEqual(labels["decisions_template_hash"],
                         json.loads((self.f.out / "decisions.json").read_text(encoding="utf-8"))["template_hash"])

    def test_cli(self):
        common = ["--assignment", str(self.f.assignment), "--answers-dir", str(self.f.answers),
                  "--batches", str(self.f.batch)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(adj.main(["report", "--out", str(self.f.out), "--proposals", str(self.f.proposals)]
                                      + common), 0)
            self.assertEqual(adj.main(["apply", str(self.f.out / "decisions.json"), "--labels", str(self.f.labels)]
                                      + common), 1)
            self.assertFalse(self.f.labels.exists())
            self.assertEqual(adj.main(["apply", str(self.f.decide()), "--labels", str(self.f.labels)] + common), 0)
        self.assertIn("disagreements: 4 (3 relation, 1 cross-field)", out.getvalue())
        self.assertIn("in R (provisional) 2", out.getvalue())  # the default fill makes cf-0006 gold too
        self.assertTrue(self.f.labels.exists())


if __name__ == "__main__":
    unittest.main()
