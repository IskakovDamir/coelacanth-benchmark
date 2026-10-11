"""Tests for adjudication: the disagreement report, the decisions file and applying it to adjudicated items that pass
items.validate. No network: works annotators add are resolved by a patched lookup or not at all (offline).

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import contextlib
import copy
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from coelacanth.benchmark import adjudicate, annotation
from coelacanth.benchmark.items import derive_verdict, validate
from coelacanth.quotes import QuoteChecker

TEXTS = {"arxiv-src": "We fit the model by least squares. We add a quadratic penalty on the coefficients. The penalty "
                      "weight is a multiple of the identity.",
         "arxiv-c1": "Minimise the residual norm. Add the squared norm of G x for a chosen matrix G. Smoothness "
                     "operators are common choices.",
         "arxiv-c2": "Gas molecules on plane surfaces of glass follow a saturating curve.",
         "arxiv-w9": "A mixing distribution is estimated by maximum likelihood on a grid of support points."}


def _ev(element, quote_candidate, quote_claim, general_form=False):
    return {"element": element, "quote_candidate": quote_candidate, "quote_claim": quote_claim,
            "location_candidate": "section 1", "location_claim": "abstract", "text_level": "full_text",
            "general_form": general_form}


C1_EVIDENCE = [_ev("e1", "Minimise the residual norm", "We fit the model by least squares"),
               _ev("e2", "Add the squared norm of G x", "We add a quadratic penalty on the coefficients"),
               _ev("e3", "for a chosen matrix G", "a multiple of the identity", general_form=True)]
C2_EVIDENCE = [_ev("e2", "follow a saturating curve", "We add a quadratic penalty on the coefficients")]


def make_packet(n):
    return {"id": f"cf-{n:04d}",
            "prefill": {"by": "claude-opus-5-5", "date": "2026-10-06", "status": "draft",
                        "fields": ["claim.text", "claim.skeleton", "claim.chart", "candidates[].evidence"]},
            "claim": {"text": "Estimate the coefficients by least squares with a quadratic penalty weighted by a "
                              "multiple of the identity matrix.",
                      "skeleton": "Fit a linear model with a squared-norm penalty whose weight matrix is scalar.",
                      "coined_names": [], "date": "1970-02-01", "field": "Statistics", "domain": "Physical Sciences",
                      "computational": True,
                      "source_work": {"doi": f"10.1/src{n}", "title": "Biased estimation for nonorthogonal problems",
                                      "first_author": "Arthur Hoerl", "year": 1970, "publication_date": "1970-02-01",
                                      "field": "Statistics", "cache_key": "arxiv-src"},
                      "chart": [{"id": "e1", "text": "least-squares fit", "key": None},
                                {"id": "e2", "text": "quadratic penalty on the coefficients", "key": None},
                                {"id": "e3", "text": "penalty weight is a multiple of the identity", "key": None}]},
            "candidates": [
                {"cid": "c1", "work": {"doi": "10.1/c1", "title": "Ill-posed problems", "first_author": "Andrey Tikhonov",
                                       "year": 1963, "publication_date": "1963-01-01", "field": "Mathematics",
                                       "venue": "Doklady", "cache_key": "arxiv-c1"},
                 "evidence": copy.deepcopy(C1_EVIDENCE), "attribution_hint": "unknown", "note": "facts"},
                {"cid": "c2", "work": {"doi": "10.1/c2", "title": "Adsorption of gases", "first_author": "Irving Langmuir",
                                       "year": 1918, "publication_date": "1918-09-01", "field": "Chemistry",
                                       "cache_key": "arxiv-c2"},
                 "evidence": copy.deepcopy(C2_EVIDENCE), "attribution_hint": "unknown", "note": "facts"}],
            "notes": "draft"}


def make_meta(ai=False):
    return {"seed": "test", "found_by": {"c1": ["mining:kulikowski"], "c2": ["mining:kulikowski"]},
            "exposure": {"kind": "review", "work": None, "date": None},
            "origin": {"kind": "ai", "system": "gen-x", "mode": "unknown"} if ai else
            {"kind": "human", "system": None, "mode": None},
            "negative": None,
            "provenance": {"source": "ai_origin" if ai else "kulikowski", "mined_by": "kulikowski_package",
                           "license": "CC-BY-4.0"},
            "exclusions": [], "prefill_assessment": "c1 states the general form (draft)."}


PACKETS = [make_packet(n) for n in (1, 2, 3, 4)]
BATCH = {"format": annotation.BATCH_FORMAT, "batch": "b00", "created": "2026-10-06",
         "labels": list(annotation.RELATIONS), "items": PACKETS}
META = {"format": annotation.META_FORMAT, "batch": "b00",
        "items": {"cf-0001": make_meta(), "cf-0002": make_meta(), "cf-0003": make_meta(ai=True),
                  "cf-0004": make_meta()}}

ELEMENTS = {"equivalent": {"covered_elements": ["e1", "e2", "e3"]},
            "specialization": {"covered_elements": ["e1", "e2", "e3"], "restricted_elements": ["e3"]},
            "partial": {"covered_elements": ["e1", "e2"]},
            "similar_mechanism": {"covered_elements": ["e1", "e3"], "analogous_elements": ["e2"]},
            "different": {}}


def cand(cid, relation, cross_field=True, confidence=2, **extra):
    out = {"cid": cid, "relation": relation, "covered_elements": [], "analogous_elements": [],
           "restricted_elements": [], "attribution": "unknown", "cross_field": cross_field,
           "confidence": confidence, "rejected_evidence": [], "note": ""}
    out.update(copy.deepcopy(ELEMENTS[relation]))
    out.update(extra)
    return out


def item(packet, c1, c2=None, keys=(True, True, True), status="done", notes=""):
    out = {"id": packet["id"], "minutes": 20,
           "claim": {"text": packet["claim"]["text"], "skeleton": packet["claim"]["skeleton"],
                     "chart": [{"id": e["id"], "text": e["text"], "key": k}
                               for e, k in zip(packet["claim"]["chart"], keys)]},
           "text_edited": False, "candidates": [c1, c2 or cand("c2", "different")], "status": status,
           "notes": notes, "prefill_status": "draft"}
    out["verdict"] = derive_verdict(out)
    if status != "done":
        out.update(minutes=0, verdict=None)
        for c in out["candidates"]:
            c.update(relation=None, covered_elements=[], analogous_elements=[], restricted_elements=[])
    return out


def labels(annotator, items):
    return {"format": annotation.LABELS_FORMAT, "batch": "b00", "annotator": annotator,
            "exported_at": "2026-10-12T10:00:00Z", "items": items}


def base_labels():
    a1 = [item(PACKETS[0], cand("c1", "specialization"), notes="read the full text"),
          item(PACKETS[1], cand("c1", "equivalent", confidence=3)),
          item(PACKETS[2], cand("c1", "different")),
          item(PACKETS[3], cand("c1", "partial"))]
    a2 = [item(PACKETS[0], cand("c1", "partial", note="no restriction stated")),
          item(PACKETS[1], cand("c1", "equivalent", confidence=2)),
          item(PACKETS[2], cand("c1", "different"), cand("c2", "different", rejected_evidence=[0])),
          item(PACKETS[3], cand("c1", "partial"), status="skipped")]
    return labels("a1", a1), labels("a2", a2)


class AdjudicationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.checker = QuoteChecker(lambda key: TEXTS.get(key))
        self.write_inputs(BATCH, *base_labels())

    def tearDown(self):
        self.tmp.cleanup()

    def write_inputs(self, batch, l1, l2, meta=META):
        self.batch_path = self.dir / "b00.json"
        self.batch_path.write_text(json.dumps(batch))
        (self.dir / "b00-meta.json").write_text(json.dumps(meta))
        self.labels = [self.dir / "b00-a1-labels.json", self.dir / "b00-a2-labels.json"]
        self.labels[0].write_text(json.dumps(l1))
        self.labels[1].write_text(json.dumps(l2))

    def run_cmd(self, *args):
        with unittest.mock.patch.object(adjudicate, "QuoteChecker", lambda: self.checker), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = adjudicate.main(list(map(str, args)))
        return code, printed.getvalue()

    def report(self, *extra):
        out_dir = self.dir / "adj"
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", out_dir, *extra)
        self.assertEqual(code, 0, printed)
        return out_dir, json.loads((out_dir / "adjudication.json").read_text())

    def decide(self, decisions, choices):
        """Fill the decisions: {key: (decision, reason[, override])}; returns the filled copy."""
        filled = copy.deepcopy(decisions)
        for it in filled["items"]:
            for entry in it["entries"]:
                if entry["key"] in choices:
                    choice = choices[entry["key"]]
                    entry["decision"], entry["reason"] = choice[0], choice[1]
                    if len(choice) > 2:
                        entry["override"] = choice[2]
        return filled

    def apply(self, decisions, offline=False, fetch=None):
        return adjudicate.apply(self.batch_path, self.labels, decisions, offline=offline, fetch=fetch,
                                checker=self.checker)


STANDARD = {"cf-0001/c1": ("a2", "the candidate states no restriction to a multiple of the identity"),
            "cf-0002/c1": ("confirmed", "quotes for e1 to e3 state the same estimator"),
            "cf-0003/ai-origin": ("confirmed", "neither reading found prior work; c1 is a different method")}


class TestReport(AdjudicationCase):
    def test_entries_and_files(self):
        out_dir, decisions = self.report()
        self.assertEqual(decisions["format"], adjudicate.DECISIONS_FORMAT)
        self.assertEqual(decisions["annotators"], ["a1", "a2"])
        self.assertEqual(decisions["single_reading"], {"cf-0004": "a1"})
        entries = {e["key"]: e for it in decisions["items"] for e in it["entries"]}
        self.assertEqual(sorted(entries), ["cf-0001/c1", "cf-0002/c1", "cf-0003/ai-origin"])
        self.assertEqual((entries["cf-0001/c1"]["kind"], entries["cf-0001/c1"]["allowed"]),
                         ("disagreement", ["a1", "a2", "exclude"]))
        self.assertEqual(entries["cf-0001/c1"]["options"]["a1"]["relation"], "specialization")
        self.assertEqual(entries["cf-0001/c1"]["options"]["a2"]["note"], "no restriction stated")
        self.assertEqual((entries["cf-0002/c1"]["kind"], entries["cf-0002/c1"]["allowed"]),
                         ("evidence_check", ["confirmed", "exclude"]))
        self.assertEqual(entries["cf-0003/ai-origin"]["kind"], "ai_origin_check")
        for entry in entries.values():
            self.assertEqual((entry["decision"], entry["reason"], entry["override"]), ("", "", {}))
        self.assertEqual(decisions["inputs"]["labels"][1]["sha256"], adjudicate.sha256(self.labels[1]))
        page = (out_dir / "report.html").read_text()
        for absent in ("<script", "src=", "<link", "@import", "url("):
            self.assertNotIn(absent, page)
        self.assertIn("default-src 'none'", page)
        self.assertIn("differs", page)
        self.assertIn("pre-filled #1, rejected", page)  # quote origins are shown, rejected ones too
        self.assertIn("Claude's draft assessment", page)
        markdown = (out_dir / "report.md").read_text()
        self.assertIn("## cf-0001 (1 to decide)", markdown)
        self.assertIn("relation **DIFFERS** | specialization | partial", markdown)
        self.assertIn("<details><summary>Candidate c2", markdown)  # agreements are collapsed
        self.assertIn("confidence **DIFFERS** | 3 | 2", markdown)  # minor differences are shown, inside the fold

    def test_an_existing_decisions_file_is_kept_unless_forced(self):
        out_dir, decisions = self.report()
        filled = self.decide(decisions, STANDARD)
        (out_dir / "adjudication.json").write_text(json.dumps(filled))
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", out_dir)
        self.assertEqual(code, 0)
        self.assertIn("kept the existing", printed)
        self.assertEqual(json.loads((out_dir / "adjudication.json").read_text()), filled)
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", out_dir, "--force")
        self.assertEqual(json.loads((out_dir / "adjudication.json").read_text()), decisions)

    def test_a_stale_kept_file_is_flagged_and_decisions_are_carried_over(self):
        out_dir, decisions = self.report()
        filled = self.decide(decisions, STANDARD)
        old_text = json.dumps(filled)
        (out_dir / "adjudication.json").write_text(old_text)
        # a2 re-exports after a form fix: a changed note on cf-0001/c1 changes that entry's options; cf-0002/c1 (an
        # evidence check on a1's agreed reading) and cf-0003 stay as they were
        l1, l2 = base_labels()
        l2["items"][0]["candidates"][0]["note"] = "re-exported after a form fix"
        self.labels[1].write_text(json.dumps(l2))
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", out_dir)
        self.assertEqual(code, 0)
        self.assertIn("warning: the kept adjudication.json was made from other inputs, so apply will refuse it",
                      printed)
        self.assertIn("decisions: the labels of a2 changed since the report; run the report again", printed)
        self.assertEqual((out_dir / "adjudication.json").read_text(), old_text)  # still kept as it was
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", out_dir, "--carry-over")
        self.assertEqual(code, 0, printed)
        self.assertIn("carried over 2 decisions from the old file", printed)
        self.assertIn("decide again (their readings changed): cf-0001/c1", printed)
        backups = list(out_dir.glob("adjudication.replaced-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), old_text)
        renewed = json.loads((out_dir / "adjudication.json").read_text())
        entries = {e["key"]: e for it in renewed["items"] for e in it["entries"]}
        self.assertEqual((entries["cf-0002/c1"]["decision"], entries["cf-0002/c1"]["reason"]), STANDARD["cf-0002/c1"])
        self.assertEqual(entries["cf-0003/ai-origin"]["decision"], "confirmed")
        self.assertEqual(entries["cf-0001/c1"]["decision"], "")
        self.assertEqual(renewed["inputs"]["labels"][1]["sha256"], adjudicate.sha256(self.labels[1]))
        items, problems, _ = self.apply(self.decide(renewed, {"cf-0001/c1": STANDARD["cf-0001/c1"]}))
        self.assertEqual(problems, [])
        self.assertEqual(len(items), 3)
        # a kept file that still matches its inputs raises no warning
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", out_dir)
        self.assertNotIn("warning:", printed)

    def test_force_and_carry_over_exclude_each_other(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", self.dir / "adj", "--force",
                         "--carry-over")

    def test_label_problems_stop_the_report(self):
        l1, l2 = base_labels()
        l2["items"][0]["verdict"] = "equivalent"
        self.write_inputs(BATCH, l1, l2)
        code, printed = self.run_cmd("report", self.batch_path, *self.labels, "--out-dir", self.dir / "adj")
        self.assertEqual(code, 1)
        self.assertIn("differs from the derived verdict", printed)
        self.assertFalse((self.dir / "adj").exists())

    def test_claim_and_key_flag_differences(self):
        l1, l2 = base_labels()
        l2["items"][0]["claim"]["chart"][2]["key"] = False
        l2["items"][0]["candidates"][0] = cand("c1", "equivalent")  # partial would need an uncovered key element
        l2["items"][0]["verdict"] = "equivalent"
        self.write_inputs(BATCH, l1, l2)
        _, decisions = self.report()
        entry = next(e for it in decisions["items"] for e in it["entries"] if e["key"] == "cf-0001/claim")
        self.assertEqual((entry["differs"], entry["allowed"]), (["key flags"], ["a1", "a2", "exclude"]))
        self.assertEqual(entry["options"]["a2"]["chart"][2], {"id": "e3", "text": "penalty weight is a multiple of "
                                                                                  "the identity", "key": False})


class TestApply(AdjudicationCase):
    def test_decided_items_pass_the_loader(self):
        _, decisions = self.report()
        items, problems, notes = self.apply(self.decide(decisions, STANDARD))
        self.assertEqual(problems, [])
        self.assertEqual([i["id"] for i in items], ["cf-0001", "cf-0002", "cf-0003"])
        self.assertIn("cf-0004: read by a1 only of these two; adjudicate it with its other reader's labels, or it "
                      "stays labelled (decision 4)", notes)
        first = items[0]
        self.assertEqual((first["status"], first["verdict"]), ("adjudicated", "partial"))
        c1 = first["candidates"][0]
        self.assertEqual((c1["relation"], c1["covered_elements"], c1["field_confirmed_by"]),
                         ("partial", ["e1", "e2"], "a2"))
        self.assertEqual(first["claim"]["checked_by"], "a1")  # pre-filled claim, kept from the first reading
        annotation_block = first["annotation"]
        self.assertEqual(annotation_block["adjudicated_by"], "damir")
        self.assertEqual([a["id"] for a in annotation_block["annotators"]], ["a1", "a2"])
        self.assertEqual(annotation_block["annotators"][0]["relations"], {"c1": "specialization", "c2": "different"})
        self.assertEqual(annotation_block["adjudication"],
                         [{"key": "cf-0001/c1", "kind": "disagreement", "what": "candidate", "decision": "a2",
                           "reason": STANDARD["cf-0001/c1"][1]}])
        self.assertIn("a1: read the full text", annotation_block["notes"])
        self.assertEqual(items[1]["verdict"], "equivalent")
        self.assertEqual(items[1]["candidates"][0]["confidence"], 3)  # agreed labels kept from the first reading
        for it in items:
            self.assertEqual(validate(it, self.checker), [])

    def test_command_writes_nothing_while_a_decision_is_missing(self):
        out_dir, decisions = self.report()
        partly = dict(STANDARD)
        del partly["cf-0002/c1"]
        (out_dir / "adjudication.json").write_text(json.dumps(self.decide(decisions, partly)))
        out = self.dir / "adjudicated.jsonl"
        code, printed = self.run_cmd("apply", self.batch_path, *self.labels, out_dir / "adjudication.json", out)
        self.assertEqual(code, 1)
        self.assertIn("cf-0002/c1: no decision (one of ['confirmed', 'exclude'])", printed)
        self.assertIn("cf-0002/c1: the reason must be one non-empty line", printed)
        self.assertFalse(out.exists())
        (out_dir / "adjudication.json").write_text(json.dumps(self.decide(decisions, STANDARD)))
        code, printed = self.run_cmd("apply", self.batch_path, *self.labels, out_dir / "adjudication.json", out)
        self.assertEqual(code, 0, printed)
        self.assertIn("wrote 3 items", printed)
        self.assertEqual(len(out.read_text().splitlines()), 3)

    def test_bad_decisions_are_refused(self):
        _, decisions = self.report()
        cases = [({"cf-0001/c1": ("a3", "x")}, "decision 'a3' is not one of"),
                 ({"cf-0001/c1": ("a2", "two\nlines")}, "the reason must be one non-empty line"),
                 ({"cf-0001/c1": ("a2", "ok", {"gold": True})}, "override field 'gold' is not one of"),
                 ({"cf-0001/c1": ("a2", "ok", {"confidence": 4})}, "override confidence must be 1, 2 or 3"),
                 ({"cf-0001/c1": ("exclude", "ok", {"relation": "partial"})}, "an override needs a decision"),
                 ({"cf-0003/ai-origin": ("confirmed", "ok", {"relation": "partial"})}, "is not one of []")]
        for choices, needle in cases:
            _, problems, _ = self.apply(self.decide(decisions, dict(STANDARD, **choices)))
            self.assertTrue(any(needle in p for p in problems), (needle, problems))
        stray = self.decide(decisions, STANDARD)
        stray["items"][0]["entries"].append(dict(stray["items"][0]["entries"][0], key="cf-0001/c9"))
        _, problems, _ = self.apply(stray)
        self.assertIn("cf-0001/c9: not an entry of these labels (did the labels change since the report?)", problems)

    def test_changed_labels_are_refused(self):
        _, decisions = self.report()
        l1, l2 = base_labels()
        l2["items"][1]["candidates"][0]["note"] = "edited after the report"
        self.labels[1].write_text(json.dumps(l2))
        _, problems, _ = self.apply(self.decide(decisions, STANDARD))
        self.assertIn("decisions: the labels of a2 changed since the report; run the report again", problems)

    def test_override_and_its_validation(self):
        _, decisions = self.report()
        ok = {"cf-0001/c1": ("a1", "e3 is not a restriction of the candidate",
                             {"relation": "partial", "covered_elements": ["e1", "e2"], "restricted_elements": []})}
        items, problems, _ = self.apply(self.decide(decisions, dict(STANDARD, **ok)))
        self.assertEqual(problems, [])
        c1 = items[0]["candidates"][0]
        self.assertEqual((c1["relation"], c1["gold"], c1["restricted_elements"]), ("partial", True, []))
        self.assertEqual(items[0]["annotation"]["adjudication"][0]["override"], ok["cf-0001/c1"][2])
        bad = {"cf-0001/c1": ("a2", "both cover everything", {"relation": "equivalent"})}
        items, problems, _ = self.apply(self.decide(decisions, dict(STANDARD, **bad)))
        self.assertEqual(items, [])
        self.assertIn("cf-0001: rule 3: candidate 0 (c1) (equivalent): key elements not covered: ['e3']", problems)

    def test_override_making_a_candidate_gold_is_confirmed_by_the_adjudicator(self):
        _, decisions = self.report()
        choices = {"cf-0003/ai-origin": ("confirmed", "ok")}
        l1, l2 = base_labels()
        # cf-0003 agrees on c1 different; the evidence check of cf-0002 turns it into similar_mechanism by override
        choices["cf-0002/c1"] = ("confirmed", "the fit and the weight are the same; the penalty matches by role only",
                                 {"relation": "similar_mechanism", "covered_elements": ["e1", "e3"],
                                  "analogous_elements": ["e2"], "cross_field": False})
        items, problems, _ = self.apply(self.decide(decisions, dict(STANDARD, **choices)))
        self.assertEqual(problems, [])
        c1 = items[1]["candidates"][0]
        self.assertEqual((c1["relation"], c1["cross_field"], c1["field_confirmed_by"]),
                         ("similar_mechanism", False, "damir"))
        self.assertEqual(items[1]["verdict"], "similar_mechanism")

    def test_exclude(self):
        _, decisions = self.report()
        items, problems, _ = self.apply(self.decide(decisions, dict(
            STANDARD, **{"cf-0001/c1": ("exclude", "the source states two different claims")})))
        self.assertEqual(problems, [])
        self.assertEqual((items[0]["status"], items[0]["exclusion_reason"]), ("excluded", "annotators_disagree"))
        self.assertEqual(validate(items[0], self.checker), [])
        items, _, _ = self.apply(self.decide(decisions, dict(
            STANDARD, **{"cf-0003/ai-origin": ("exclude", "the generated method is too vague to label")})))
        self.assertEqual((items[2]["status"], items[2]["exclusion_reason"]), ("excluded", "excluded_at_adjudication"))

    def test_a_chart_element_of_the_other_reading_is_reported(self):
        l1, l2 = base_labels()
        first = l2["items"][0]
        first["claim"]["chart"].append({"id": "e7", "text": "a smoothness alternative is named", "key": True})
        first["candidates"][0]["covered_elements"] = ["e1", "e2", "e7"]
        first["candidates"][0]["added_evidence"] = [_ev("e7", "Smoothness operators are common choices",
                                                        "The penalty weight is a multiple of the identity")]
        self.write_inputs(BATCH, l1, l2)
        _, decisions = self.report()
        choices = dict(STANDARD, **{"cf-0001/claim": ("a1", "the chart of a1 has the claim's grain")})
        _, problems, _ = self.apply(self.decide(decisions, choices))
        self.assertTrue(any("cf-0001/c1: the labels kept (a2's) refer to elements ['e7']" in p for p in problems),
                        problems)
        choices["cf-0001/claim"] = ("a2", "e7 is part of the claim")
        items, problems, _ = self.apply(self.decide(decisions, choices))
        self.assertEqual(problems, [])
        self.assertEqual([e["id"] for e in items[0]["claim"]["chart"]], ["e1", "e2", "e3", "e7"])


class TestCrossover(AdjudicationCase):
    def setUp(self):
        super().setUp()
        plan = {"batch": "b00", "crossover": {"X_prefilled_for_annotator_1": ["cf-0001"],
                                              "Y_prefilled_for_annotator_2": ["cf-0002"]}}
        batch = dict(BATCH, items=PACKETS[:2])
        meta = dict(META, items={k: v for k, v in META["items"].items() if k in ("cf-0001", "cf-0002")})
        a1 = [item(PACKETS[0], cand("c1", "partial")), item(PACKETS[1], cand("c1", "different"), status="skipped")]
        a1[1].update(prefill_status="none", claim={"text": "", "skeleton": "", "chart": [], "coined_names": []})
        bare = item(PACKETS[0], cand("c1", "partial", added_evidence=[
            _ev("e1", "Minimise the residual norm", "We fit the model by least squares"),
            _ev("e2", "Add the squared norm of G x", "We add a quadratic penalty on the coefficients")]))
        bare["claim"].update(text="Least squares with a quadratic penalty on the coefficients, weighted by a scalar "
                                  "multiple of the identity.", coined_names=[])
        bare["candidates"][1] = cand("c2", "different")
        bare.update(text_edited=True, prefill_status="none")
        a2 = [bare, item(PACKETS[1], cand("c1", "different"), status="skipped")]
        self.write_inputs(batch, labels("a1", a1), labels("a2", a2), meta)
        for name, data in annotation.crossover(batch, plan).items():
            (self.dir / f"b00-{name}.json").write_text(json.dumps(data))

    def test_a_bare_claim_needs_a_named_checker(self):
        _, decisions = self.report()
        [entry] = decisions["items"]
        self.assertEqual(entry["claim_checked_by"], "")
        keys = [e["key"] for e in entry["entries"]]
        self.assertEqual(keys, ["cf-0001/claim"])  # c1 agrees (partial), c2 agrees (different)
        choice = {"cf-0001/claim": ("a2", "the annotator's text keeps the source's terms")}
        _, problems, _ = self.apply(self.decide(decisions, choice))
        self.assertIn("cf-0001: the claim kept was written by annotator a2 (a bare packet or a contrast item); name the "
                      "person who checked it against the source in claim_checked_by", problems)
        filled = self.decide(decisions, choice)
        filled["items"][0]["claim_checked_by"] = "a2"
        _, problems, _ = self.apply(filled)
        self.assertIn("cf-0001: claim_checked_by must be a person other than the writer a2", problems)
        filled["items"][0]["claim_checked_by"] = "damir"
        items, problems, _ = self.apply(filled)
        self.assertEqual(problems, [])
        claim = items[0]["claim"]
        self.assertEqual((claim["written_by"], claim["checked_by"]), ("a2", "damir"))
        self.assertEqual(items[0]["annotation"]["prefill"]["status"], "none")
        self.assertEqual([a["prefill_status"] for a in items[0]["annotation"]["annotators"]], ["draft", "none"])
        self.assertEqual(validate(items[0], self.checker), [])

    def test_the_prefilled_claim_keeps_its_checker(self):
        _, decisions = self.report()
        items, problems, _ = self.apply(self.decide(decisions, {"cf-0001/claim": ("a1", "the checked draft is exact")}))
        self.assertEqual(problems, [])
        claim = items[0]["claim"]
        self.assertEqual((claim["written_by"], claim["checked_by"]), (annotation.PREFILL_AUTHOR, "a1"))


ADDED_W9 = {"doi": "10.1000/w9", "title": "Mixing distributions on a grid", "year": 1960, "first_author": "Ann Other",
            "field": "Statistics"}


def _added(cid, work, relation, **extra):
    out = cand(cid, relation, **extra)
    out["work"] = dict(work)
    out["evidence"] = [_ev("e1", "maximum likelihood on a grid", "We fit the model by least squares"),
                       _ev("e2", "A mixing distribution is estimated", "We add a quadratic penalty on the coefficients")]
    for key in ("rejected_evidence",):
        out.pop(key)
    return out


class TestAddedCandidates(AdjudicationCase):
    def setUp(self):
        super().setUp()
        l1, l2 = base_labels()
        w8 = dict(ADDED_W9, doi="10.1000/w8", title="Surface sites that fill up")
        w7 = dict(ADDED_W9, doi="10.1000/w7", title="An unrelated survey")
        similar = _added("a2", w8, "similar_mechanism")  # covers e1 and e3: a quote pair for each (rule 2)
        similar["evidence"].append(_ev("e3", "a grid of support points", "a multiple of the identity"))
        l1["items"][2]["added_candidates"] = [_added("a1", ADDED_W9, "partial"), similar]
        l2["items"][2]["added_candidates"] = [_added("a1", dict(ADDED_W9, title="Mixing distributions on a grid."),
                                                     "partial", confidence=1),
                                              _added("a2", w7, "different")]
        for lab in (l1, l2):
            lab["items"][2]["verdict"] = derive_verdict(
                {"candidates": lab["items"][2]["candidates"] + lab["items"][2]["added_candidates"]})
        self.write_inputs(BATCH, l1, l2)

    def test_matched_one_sided_gold_and_one_sided_other(self):
        _, decisions = self.report("--offline")
        self.assertFalse(decisions["resolved_works"])
        entries = {e["key"]: e for it in decisions["items"] for e in it["entries"]}
        self.assertNotIn("cf-0003/ai-origin", entries)  # the readings disagree there, so its entries decide it
        self.assertNotIn("cf-0003/doi:10.1000/w9", entries)  # both added it and agree on partial
        self.assertNotIn("cf-0003/doi:10.1000/w7", entries)  # only a2 added it, not gold: kept as labelled
        one_sided = entries["cf-0003/doi:10.1000/w8"]
        self.assertEqual((one_sided["allowed"], one_sided["what"]), (["a1", "omit", "exclude"], "added candidate"))
        choices = dict(STANDARD)
        del choices["cf-0003/ai-origin"]
        choices["cf-0003/doi:10.1000/w8"] = ("omit", "the sites are not the coefficients; no role correspondence")
        items, problems, notes = self.apply(self.decide(decisions, choices), offline=True)
        self.assertEqual(problems, [])
        self.assertIn("offline: quotes were not checked verbatim", notes)
        third = items[2]
        works = [c["work"].get("doi") for c in third["candidates"]]
        self.assertEqual(works[2:], ["10.1000/w9", "10.1000/w7"])
        self.assertEqual(third["candidates"][2]["found_by"], ["annotator:a1", "annotator:a2"])
        self.assertEqual(third["candidates"][3]["found_by"], ["annotator:a2"])
        self.assertEqual(third["verdict"], "partial")
        choices["cf-0003/doi:10.1000/w8"] = ("a1", "the filling of sites corresponds to the penalty by role")
        items, problems, _ = self.apply(self.decide(decisions, choices), offline=True)
        self.assertEqual(problems, [])
        self.assertEqual(len(items[2]["candidates"]), 5)

    def test_offline_and_online_runs_do_not_mix(self):
        _, decisions = self.report("--offline")
        choices = dict(STANDARD, **{"cf-0003/doi:10.1000/w8": ("omit", "no correspondence")})
        del choices["cf-0003/ai-origin"]

        def fetch(identifier):
            work = dict(ADDED_W9, doi=f"https://doi.org/{identifier}", openalex=None, arxiv=None, pmid=None,
                        publication_date="1960-01-01", cache_key="arxiv-w9",
                        title={"10.1000/w8": "Surface sites that fill up",
                               "10.1000/w7": "An unrelated survey"}.get(identifier, ADDED_W9["title"]))
            return {"work": work, "texts": {}, "problems": []}

        _, problems, _ = self.apply(self.decide(decisions, choices), fetch=fetch)
        self.assertIn("decisions: the report was made offline; run apply the same way, or the report again", problems)

    def test_online_report_and_apply_resolve_the_added_works_and_check_their_quotes(self):
        titles = {"10.1000/w8": "Surface sites that fill up", "10.1000/w7": "An unrelated survey"}

        def fetch(identifier):
            work = {"openalex": None, "doi": f"https://doi.org/{identifier}", "arxiv": None, "pmid": None,
                    "title": titles.get(identifier, ADDED_W9["title"]), "first_author": "Ann Other", "year": 1960,
                    "publication_date": "1960-01-01", "field": "Statistics", "cache_key": "arxiv-w9"}
            return {"work": work, "texts": {"arxiv-w9": {"level": "full_text"}}, "problems": []}

        with unittest.mock.patch.object(annotation, "fetch_work", fetch):
            _, decisions = self.report()
        self.assertTrue(decisions["resolved_works"])
        choices = dict(STANDARD, **{"cf-0003/doi:10.1000/w8": ("a1", "the filling of sites corresponds by role")})
        del choices["cf-0003/ai-origin"]
        items, problems, _ = self.apply(self.decide(decisions, choices), fetch=fetch)
        self.assertEqual(problems, [])
        w9 = items[2]["candidates"][2]
        self.assertEqual((w9["work"]["cache_key"], w9["found_by"]), ("arxiv-w9", ["annotator:a1", "annotator:a2"]))
        # a quote that is not in the resolved work's cached text stops the apply
        l1 = json.loads(self.labels[0].read_text())
        l1["items"][2]["added_candidates"][0]["evidence"][0]["quote_candidate"] = "not in the cached text"
        self.labels[0].write_text(json.dumps(l1))
        _, problems, _ = self.apply(self.decide(decisions, choices), fetch=fetch)
        self.assertTrue(any("a1: added: cf-0003: candidate a1 evidence 1 quote_candidate: not found verbatim" in p
                            for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
