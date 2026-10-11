"""Tests for contrast items (eval/protocol.md decision 14): the pivot draw, templates, checks of filled contrast
files, the contrast batch that tools/annotate.html loads, and finishing adjudicated contrast items.

    PYTHONPATH=src python -m unittest tests.test_contrast -v
"""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from coelacanth.benchmark import adjudicate, annotation, contrast, items
from coelacanth.quotes import QuoteChecker, read_cached

TEXTS = {
    "arxiv-1": "Population models. We estimate the mixing distribution of patient parameters by maximum likelihood "
               "over a fixed grid of support points. Each dose is then chosen from the estimated distribution.",
    "oa-W2": "Mixing distributions\n\nPatient parameters are drawn from an unknown mixing distribution. The mixing "
             "distribution is estimated by maximum likelihood over a fixed grid of support points. Doses are chosen "
             "from the estimated distribution.",
    "oa-W3": "Hospital costs\n\nA survey of hospital costs in three regions.",
}
EV = {"text_level": "abstract", "general_form": False, "location_candidate": "abstract", "location_claim": "abstract"}
PIVOT = {
    "id": "cf-0011", "version": "v0", "status": "adjudicated",
    "claim": {
        "text": "Estimate the mixing distribution of patient parameters by maximum likelihood over a fixed grid of "
                "support points, then choose each dose from the estimated distribution.",
        "skeleton": "Fit an unknown mixing distribution on a grid by maximum likelihood and pick a control input "
                    "from it.",
        "coined_names": ["NPAG"], "written_by": "prefill:claude", "checked_by": "a1",
        "source_work": {"arxiv": "1", "title": "Two methods for population models",
                        "first_author": "Tatiana Tatarinova",
                        "publication_date": "2013-03-26", "year": 2013, "cache_key": "arxiv-1"},
        "date": "2013-03-26", "field": "Pharmacology", "domain": "Life Sciences", "computational": True,
        "chart": [{"id": "e1", "text": "patient parameters drawn from an unknown mixing distribution", "key": True},
                  {"id": "e2", "text": "maximum likelihood over a fixed grid of support points", "key": True},
                  {"id": "e3", "text": "each dose chosen from the estimated distribution", "key": True},
                  {"id": "e4", "text": "a simulation study", "key": False}]},
    "candidates": [
        {"work": {"openalex": "W2", "title": "Mixing distributions", "first_author": "A. P. Dempster",
                  "publication_date": "1977-09-01", "year": 1977, "field": "Statistics", "cache_key": "oa-W2"},
         "found_by": ["mining:kulikowski"], "relation": "equivalent", "covered_elements": ["e1", "e2", "e3"],
         "analogous_elements": [], "restricted_elements": [],
         "evidence": [dict(EV, element="e1", quote_candidate="drawn from an unknown mixing distribution",
                           quote_claim="the mixing distribution of patient parameters"),
                      dict(EV, element="e2", quote_candidate="by maximum likelihood over a fixed grid",
                           quote_claim="by maximum likelihood over a fixed grid"),
                      dict(EV, element="e3", quote_candidate="Doses are chosen from the estimated distribution",
                           quote_claim="chosen from the estimated distribution")],
         "attribution": "not_cited", "cross_field": True, "field_confirmed_by": "a1", "gold": True, "confidence": 2},
        {"work": {"openalex": "W3", "title": "Hospital costs", "first_author": "B. Smith",
                  "publication_date": "1990-01-01", "year": 1990, "field": "Economics", "cache_key": "oa-W3"},
         "found_by": ["annotator:a2"], "relation": "different", "covered_elements": [], "analogous_elements": [],
         "restricted_elements": [], "evidence": [], "attribution": "unknown", "cross_field": True,
         "field_confirmed_by": None, "gold": False, "confidence": 3}],
    "verdict": "equivalent", "pool": None,
    "exposure": {"kind": "benchmark_import", "work": None, "date": None},
    "origin": {"kind": "human", "system": None, "mode": None}, "negative": None,
    "provenance": {"source": "kulikowski", "mined_by": "kulikowski_package", "dataset": "K", "license": "CC-BY-4.0",
                   "attribution": "Kulikowski package, CC-BY-4.0"},
    "annotation": {"annotators": [], "prefill": None, "batch": "b01", "adjudicated_by": "damir", "notes": None},
    "exclusions": ["arxiv:1"]}
FILL = {
    "edited_element": "e2",
    "new_element_text": "a normal distribution fitted by the method of moments",
    "claim_text": "Estimate the mixing distribution of patient parameters as a normal distribution fitted by the "
                  "method of moments, then choose each dose from the estimated distribution.",
    "skeleton": "Fit an unknown mixing distribution as a normal law by moments and pick a control input from it.",
    "what_changed": "e2 now fits a normal law by moments instead of maximum likelihood over a grid, so c1 lacks e2.",
    "minutes": 25}


def filled(**changes):
    out = contrast.template(PIVOT, "cf-0070", "a1", "a2", created="2026-10-12")
    out.update(FILL)
    out.update(changes)
    return out


class TestPivots(unittest.TestCase):
    def test_eligibility(self):
        self.assertEqual(contrast.pivot_problems(PIVOT), [])
        cases = {"status": ("labelled", "not 'adjudicated'"), "negative": ({"kind": "contrast", "from_item": "x"},
                                                                           "built as a negative"),
                 "origin": ({"kind": "ai", "system": "m"}, "not of human origin")}
        for field, (value, message) in cases.items():
            item = dict(copy.deepcopy(PIVOT), **{field: value})
            self.assertTrue(any(message in p for p in contrast.pivot_problems(item)), field)
        cited = copy.deepcopy(PIVOT)
        cited["candidates"][0]["attribution"] = "cited"
        self.assertTrue(any("attributed import" in p for p in contrast.pivot_problems(cited)))
        nothing = copy.deepcopy(PIVOT)
        nothing["candidates"] = nothing["candidates"][1:]
        self.assertTrue(any("no gold candidate" in p for p in contrast.pivot_problems(nothing)))

    def test_seeded_stratified_order(self):
        pool = []
        for n in range(12):
            item = copy.deepcopy(PIVOT)
            item["id"] = f"cf-{n + 1:04d}"
            item["provenance"]["source"] = ("declaration", "kulikowski", "catalogue")[n % 3]
            item["claim"]["computational"] = n % 2 == 0
            pool.append(item)
        pool.append(dict(copy.deepcopy(PIVOT), id="cf-0099", status="labelled"))
        first = contrast.pivot_order(pool)
        self.assertEqual(first, contrast.pivot_order(list(reversed(pool))))
        self.assertEqual(sorted(first["order"]), sorted(i["id"] for i in pool[:12]))
        self.assertIn("cf-0099", first["ineligible"])
        strata = {i: s for s, ids in first["strata"].items() for i in ids}
        self.assertEqual(len({strata[i] for i in first["order"][:6]}), 6)  # one per stratum before a second
        self.assertNotEqual(first["order"], contrast.pivot_order(pool, seed=1)["order"])


class TestTemplate(unittest.TestCase):
    def test_template_starts_from_the_pivot(self):
        t = contrast.template(PIVOT, "cf-0070", "a1", "a2", created="2026-10-12")
        self.assertEqual((t["claim_text"], t["skeleton"]), (PIVOT["claim"]["text"], PIVOT["claim"]["skeleton"]))
        self.assertEqual((t["edited_element"], t["new_element_text"], t["what_changed"], t["minutes"]),
                         ("", "", "", None))
        self.assertEqual([c["cid"] for c in t["pivot"]["candidates"]], ["c1", "c2"])
        self.assertEqual(t["pivot"]["candidates"][0]["covered_elements"], ["e1", "e2", "e3"])
        self.assertEqual(set(t), set(contrast.FIXED_FIELDS + contrast.FILL_FIELDS))

    def test_template_refuses_ineligible_pivots_and_one_person(self):
        with self.assertRaises(ValueError):
            contrast.template(dict(PIVOT, status="labelled"), "cf-0070", "a1", "a2")
        with self.assertRaises(ValueError):
            contrast.template(PIVOT, "cf-0070", "a1", "a1")

    def test_ids_are_new_and_above_every_id_in_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            batches, folder = Path(tmp) / "batches", Path(tmp) / "contrast"
            (batches / "assigned").mkdir(parents=True)
            folder.mkdir()
            (batches / "b02.json").write_text(json.dumps({"format": annotation.BATCH_FORMAT, "batch": "b02",
                                                          "items": [{"id": "cf-0067"}]}))
            (folder / "cf-0068.json").write_text(json.dumps({"id": "cf-0068"}))
            used = contrast.used_ids([PIVOT], batches, folder)
            self.assertEqual(used, {"cf-0011", "cf-0067", "cf-0068"})
            self.assertEqual(contrast.id_problems("cf-0069", used), [])
            self.assertIn("already used", contrast.id_problems("cf-0068", used)[0])
            self.assertIn("next free id is cf-0069", contrast.id_problems("cf-0048", used)[0])
            self.assertIn("must look like", contrast.id_problems("cf-69", used)[0])


class TestCheck(unittest.TestCase):
    def problems(self, c, pivot=PIVOT):
        return contrast.check_contrast(c, pivot)[0]

    def test_good_contrast_passes_and_notes_show_the_changed_words(self):
        problems, notes = contrast.check_contrast(filled(), PIVOT)
        self.assertEqual(problems, [])
        self.assertIn('"by maximum likelihood over a fixed grid of support points," -> "as a normal distribution '
                      'fitted by the method of moments,"', notes[0])
        self.assertTrue(notes[0].startswith("claim text, 1 changed run of words"))

    def test_nothing_edited(self):
        out = self.problems(filled(claim_text=PIVOT["claim"]["text"], skeleton=PIVOT["claim"]["skeleton"],
                                   new_element_text=PIVOT["claim"]["chart"][1]["text"]))
        self.assertTrue(any("claim_text is still the pivot's" in p for p in out))
        self.assertTrue(any("skeleton is still the pivot's" in p for p in out))
        self.assertTrue(any("nothing was edited" in p for p in out))

    def test_the_edited_element_is_key_and_had_by_a_gold_candidate(self):
        self.assertTrue(any("not a key element" in p for p in self.problems(filled(edited_element="e4"))))
        pivot = copy.deepcopy(PIVOT)
        pivot["candidates"][0]["covered_elements"] = ["e1", "e3"]
        out = contrast.check_contrast(dict(filled(), pivot=contrast.pivot_snapshot(pivot)), pivot)[0]
        self.assertTrue(any("no gold candidate of the pivot has e2" in p for p in out))
        self.assertTrue(any("must be the id of one element" in p for p in self.problems(filled(edited_element="e9"))))

    def test_claim_rules(self):
        text = FILL["claim_text"] + " Unlike Dempster in 1977, NPAG does this."
        out = self.problems(filled(claim_text=text))
        for message in ("surname 'Dempster'", "contains a year", "coined name 'NPAG'"):
            self.assertTrue(any(message in p for p in out), message)
        out = self.problems(filled(claim_text=" ".join(["dose"] * 121), skeleton=" ".join(["dose"] * 121)))
        self.assertTrue(any("121 words" in p for p in out))
        self.assertTrue(any("skeleton repeats claim.text" in p for p in out))

    def test_file_shape_people_minutes_and_what_changed(self):
        out = self.problems(filled(minutes="25", what_changed="changed e2", writer="a2", note="x"))
        for message in ("minutes must be a number above 0", "what_changed is empty or too short",
                        "checker must be a different annotator", "unknown fields"):
            self.assertTrue(any(message in p for p in out), message)
        broken = filled()
        del broken["skeleton"]
        self.assertTrue(any("fields missing: ['skeleton']" in p for p in self.problems(broken)))

    def test_pivot_section_must_not_change(self):
        c = filled()
        c["pivot"]["chart"][1]["text"] = "something else"
        self.assertTrue(any("pivot section differs" in p for p in self.problems(c)))
        self.assertTrue(any("is not in the items file" in p for p in self.problems(filled(), None)))

    def test_meta_carries_rule_7_record(self):
        meta = contrast.contrast_meta(filled(), PIVOT)
        self.assertEqual(meta["negative"], {"kind": "contrast", "from_item": "cf-0011", "edited_element": "e2"})
        self.assertEqual((meta["provenance"]["source"], meta["provenance"]["mined_by"]), ("negative", "manual"))
        self.assertEqual(meta["found_by"], {"c1": ["mining:kulikowski"], "c2": ["annotator:a2"]})
        self.assertEqual(annotation.meta_problems(meta, ["c1", "c2"]), [])


class PacketTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.batches, self.texts = root / "batches", root / "fulltext"
        self.texts.mkdir()
        for key, text in TEXTS.items():
            (self.texts / f"{key}.txt").write_text(text, encoding="utf-8")
        self.checker = QuoteChecker(lambda key: read_cached(key, self.texts))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, contrasts, batch="c01"):
        return contrast.write_packets(contrasts, [PIVOT], batch, self.batches, self.texts, created="2026-10-12",
                                      checker=self.checker)

    def read(self, name):
        return json.loads((self.batches / name).read_text(encoding="utf-8"))


class TestPacket(PacketTestCase):
    def test_contrast_batch_and_copies(self):
        report = self.write([filled()])
        self.assertEqual(report["copies"]["c01-a1.json"], {"writes": ["cf-0070"], "checks blind": [], "problems": {}})
        self.assertEqual(report["copies"]["c01-a2.json"], {"writes": [], "checks blind": ["cf-0070"], "problems": {}})
        batch, meta = self.read("c01.json"), self.read("c01-meta.json")
        copy_a2 = self.read("assigned/c01-a2.json")
        self.assertEqual((copy_a2["annotator"], copy_a2["items"]), ("a2", batch["items"]))
        packet = batch["items"][0]
        self.assertEqual(packet["prefill"]["by"], "annotator:a1")
        self.assertEqual([(e["id"], e["key"]) for e in packet["claim"]["chart"]],
                         [("e1", None), ("e2", None), ("e3", None), ("e4", None)])
        self.assertEqual(packet["claim"]["chart"][1]["text"], FILL["new_element_text"])
        self.assertEqual(packet["claim"]["source_work"]["cache_key"], "ctr-cf-0070")
        self.assertEqual(packet["claim"]["source_work"]["arxiv"], "1")
        self.assertEqual((self.texts / "ctr-cf-0070.txt").read_text(encoding="utf-8"), FILL["claim_text"])
        self.assertTrue(all(c["evidence"] == [] and c["attribution_hint"] == "unknown" for c in packet["candidates"]))
        self.assertEqual(meta["items"]["cf-0070"]["negative"]["edited_element"], "e2")
        self.assertNotIn("what_changed", json.dumps(batch))  # the writer's intent stays in the hidden meta
        self.assertIn(FILL["what_changed"], meta["items"]["cf-0070"]["prefill_assessment"])
        self.assertEqual(annotation.packet_problems(packet, meta["items"]["cf-0070"], self.checker, self.texts), [])

    def test_nothing_written_on_a_problem_or_an_existing_batch(self):
        with self.assertRaises(ValueError):
            self.write([filled(minutes=None)])
        self.assertFalse(self.batches.exists())
        self.write([filled()])
        with self.assertRaises(ValueError):
            self.write([filled()])
        with self.assertRaises(ValueError):  # the id is now used by batch c01
            self.write([filled()], batch="c02")

    def test_cached_claim_texts_can_be_written_again(self):
        (path,) = contrast.write_texts([filled()], self.texts)
        self.assertEqual((path.name, path.read_text(encoding="utf-8")), ("ctr-cf-0070.txt", FILL["claim_text"]))
        with self.assertRaises(ValueError):
            contrast.write_texts([{"id": "x", "claim_text": "y"}], self.texts)

    def test_a_third_annotator_gets_only_their_items(self):
        second = filled(id="cf-0071", writer="a2", checker="a3")
        report = self.write([filled(), second])
        self.assertEqual(sorted(report["copies"]), ["c01-a1.json", "c01-a2.json", "c01-a3.json"])
        self.assertEqual([p["id"] for p in self.read("assigned/c01-a3.json")["items"]], ["cf-0071"])
        self.assertEqual([p["id"] for p in self.read("assigned/c01-a2.json")["items"]], ["cf-0070", "cf-0071"])


class TestEndToEnd(PacketTestCase):
    """The labels made in the tool's format check and merge with the existing code; claim-side quotes come from the
    claim text; merge records the writer named in prefill.by; finish checks the adjudicated item against its contrast
    file and items.validate."""

    def labels(self, packet, annotator):
        chart = [dict(e, key=e["id"] != "e4") for e in packet["claim"]["chart"]]
        quote = dict(EV, element="e1", quote_candidate="drawn from an unknown mixing distribution",
                     quote_claim="the mixing distribution of patient parameters")
        quote3 = dict(EV, element="e3", quote_candidate="Doses are chosen from the estimated distribution",
                      quote_claim="choose each dose from the estimated distribution")
        base = {"analogous_elements": [], "restricted_elements": [], "rejected_evidence": [], "note": ""}
        cands = [dict(base, cid="c1", relation="partial", covered_elements=["e1", "e3"], attribution="not_cited",
                      cross_field=True, confidence=2, added_evidence=[quote, quote3]),
                 dict(base, cid="c2", relation="different", covered_elements=[], attribution="unknown",
                      cross_field=True, confidence=3, added_evidence=[])]
        item = {"id": packet["id"], "minutes": 30, "text_edited": False, "prefill_status": "draft", "status": "done",
                "claim": {"text": packet["claim"]["text"], "skeleton": packet["claim"]["skeleton"], "chart": chart},
                "candidates": cands, "added_candidates": [], "verdict": "partial", "notes": ""}
        return {"format": annotation.LABELS_FORMAT, "batch": "c01", "annotator": annotator,
                "exported_at": "2026-10-13T10:00:00Z", "items": [item]}

    def adjudicated(self):
        self.write([filled()])
        copy_a2, meta = self.read("assigned/c01-a2.json"), self.read("c01-meta.json")
        labels = self.labels(copy_a2["items"][0], "a2")
        self.assertEqual(annotation.labels_problems(copy_a2, labels), [])
        item = annotation.merge(copy_a2, meta, labels)[0]
        # the writer from prefill.by, the checker's reading as the claim check (SCHEMA.md rule 5)
        self.assertEqual((item["claim"]["written_by"], item["claim"]["checked_by"]), ("a1", "a2"))
        self.assertEqual(items.validate(item, self.checker), [])
        item["status"] = "adjudicated"
        item["annotation"]["annotators"].insert(0, {"id": "a1", "relations": {"c1": "partial", "c2": "different"},
                                                    "verdict": "partial", "minutes": 15})
        item["annotation"]["adjudicated_by"] = "damir"
        return item

    def test_the_writers_own_reading_leaves_the_claim_check_to_the_checker(self):
        self.write([filled()])
        copy_a1, meta = self.read("assigned/c01-a1.json"), self.read("c01-meta.json")
        labels = self.labels(copy_a1["items"][0], "a1")
        self.assertEqual(annotation.labels_problems(copy_a1, labels), [])
        item = annotation.merge(copy_a1, meta, labels)[0]
        self.assertEqual((item["claim"]["written_by"], item["claim"]["checked_by"]), ("a1", None))
        self.assertTrue(annotation.awaiting_claim_check(item))
        self.assertEqual(items.validate(item, self.checker), [])

    def test_adjudication_asks_for_the_claim_check_and_finish_accepts_the_result(self):
        # the writer's reading (a1) carries no claim check, so the decisions file asks for one even when the two
        # readings agree; the checker named there is recorded, and finish accepts the adjudicated item
        self.write([filled()])
        paths = []
        for who in ("a1", "a2"):
            copy_ = self.read(f"assigned/c01-{who}.json")
            path = self.batches / f"labels-{who}.json"
            path.write_text(json.dumps(self.labels(copy_["items"][0], who)), encoding="utf-8")
            paths.append(path)
        batch_path = self.batches / "c01.json"
        batch, readings, _, views, single, problems, _ = adjudicate.prepare(batch_path, paths, True, None, self.checker)
        self.assertEqual(problems, [])
        self.assertTrue(adjudicate.needs_claim_check(views[0]))
        decisions = adjudicate.decisions_template(batch, readings, views, single,
                                                  adjudicate.inputs_record(batch_path, readings), True, "damir")
        self.assertEqual([(e["id"], e["claim_checked_by"]) for e in decisions["items"]], [("cf-0070", "")])
        self.assertEqual(decisions["agreed_items"], [])
        problems = adjudicate.apply(batch_path, paths, decisions, offline=True, checker=self.checker)[1]
        self.assertTrue(any("name the person who checked it" in p for p in problems), problems)
        decisions["items"][0]["claim_checked_by"] = "a1"
        problems = adjudicate.apply(batch_path, paths, decisions, offline=True, checker=self.checker)[1]
        self.assertTrue(any("other than the writer a1" in p for p in problems), problems)
        decisions["items"][0]["claim_checked_by"] = "a2"
        done, problems, _ = adjudicate.apply(batch_path, paths, decisions, offline=True, checker=self.checker)
        self.assertEqual(problems, [])
        self.assertEqual((done[0]["claim"]["written_by"], done[0]["claim"]["checked_by"]), ("a1", "a2"))
        self.assertEqual(contrast.finish(done, {"cf-0070": filled()}, self.checker)[1], [])

    def test_finish_passes_a_matching_item_unchanged(self):
        item = self.adjudicated()
        out, problems = contrast.finish([item], {"cf-0070": filled()}, self.checker)
        self.assertEqual(problems, [])
        self.assertEqual(out, [item])
        self.assertEqual((out[0]["claim"]["written_by"], out[0]["claim"]["checked_by"]), ("a1", "a2"))
        self.assertEqual(out[0]["verdict"], "partial")
        self.assertEqual(out[0]["negative"], {"kind": "contrast", "from_item": "cf-0011", "edited_element": "e2"})

    def test_finish_refuses_edited_claims_and_missing_readings(self):
        item = self.adjudicated()
        item["claim"]["text"] += " Doses are rounded."
        item["claim"]["chart"][0]["text"] = "parameters"
        item["annotation"]["annotators"] = item["annotation"]["annotators"][1:]
        item["negative"]["edited_element"] = "e3"
        item["claim"]["written_by"], item["claim"]["checked_by"] = annotation.PREFILL_AUTHOR, "damir"
        problems = contrast.finish([item], {"cf-0070": filled()}, self.checker)[1]
        for message in ("claim text or skeleton differs", "the chart differs", "the writer 'a1' has no reading",
                        "the contrast file says", "claim.written_by is 'prefill:claude', the contrast file's writer is "
                        "'a1'", "claim.checked_by is 'damir', the contrast file's checker is 'a2'"):
            self.assertTrue(any(message in p for p in problems), message)
        self.assertTrue(any("no contrast file" in p for p in contrast.finish([item], {}, self.checker)[1]))
        plain = dict(copy.deepcopy(PIVOT))
        self.assertTrue(any("not a contrast item" in p for p in contrast.finish([plain], {}, self.checker)[1]))


class TestCommandLine(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = contrast.main(argv)
        return code, out.getvalue()

    def test_check_reports_each_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            items_path, good, bad = Path(tmp) / "items.jsonl", Path(tmp) / "good.json", Path(tmp) / "bad.json"
            items_path.write_text(json.dumps(PIVOT) + "\n", encoding="utf-8")
            good.write_text(json.dumps(filled()), encoding="utf-8")
            bad.write_text('{"claim_text": "a "quoted" word"}', encoding="utf-8")
            code, text = self.run_main(["check", str(items_path), str(good)])
            self.assertEqual((code, json.loads(text)[str(good)]["ok"]), (0, True))
            code, text = self.run_main(["check", str(items_path), str(good), str(bad)])
            self.assertEqual(code, 1)
            self.assertIn("not valid JSON", json.loads(text)[str(bad)]["problems"][0])
            code, text = self.run_main(["pivots", str(items_path)])
            self.assertEqual((code, json.loads(text)["order"]), (0, ["cf-0011"]))


if __name__ == "__main__":
    unittest.main()
