"""Tests for annotation packets: the draft-only rules, quote checks against cached texts, rule 5 and rule 6, meta.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from coelacanth.benchmark import annotation, items
from coelacanth.quotes import QuoteChecker

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
                                  "text_level": "abstract", "general_form": False},
                                 {"element": "e1", "quote_candidate": "maximum likelihood estimates from incomplete data",
                                  "quote_claim": "We estimate the mixing distribution",
                                  "location_candidate": "abstract", "location_claim": "abstract",
                                  "text_level": "abstract", "general_form": False}],
                    "attribution_hint": "unknown", "note": "draft"}],
    "notes": "draft"}
META = {"seed": "p01", "found_by": {"c1": ["mining:kulikowski"]}, "exposure": {"kind": "benchmark_import"},
        "origin": {"kind": "human", "system": None, "mode": None}, "negative": None,
        "provenance": {"source": "kulikowski", "mined_by": "kulikowski_package", "license": "CC-BY-4.0"},
        "exclusions": []}


class PacketTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for key, text in TEXTS.items():
            (self.dir / f"{key}.txt").write_text(text)
        self.checker = QuoteChecker(lambda key: TEXTS.get(key))

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, packet, meta=META):
        return annotation.packet_problems(packet, meta, self.checker, text_dir=self.dir)


class TestPacket(PacketTestCase):
    def test_good_packet_passes(self):
        self.assertEqual(self.problems(PACKET), [])

    def test_an_annotators_prefill_names_them(self):
        p = copy.deepcopy(PACKET)
        p["prefill"]["by"] = "annotator:a1"
        self.assertEqual(self.problems(p), [])
        p["prefill"]["by"] = "annotator:"
        self.assertEqual(self.problems(p),
                         ["prefill.by 'annotator:': an annotator's pre-fill names them as 'annotator:<id>'"])

    def test_key_flags_and_labels_are_left_to_annotators(self):
        p = copy.deepcopy(PACKET)
        p["claim"]["chart"][0]["key"] = True
        p["candidates"][0]["relation"] = "partial"
        out = self.problems(p)
        self.assertTrue(any("key flags must be null" in x for x in out))
        self.assertTrue(any("humans choose every label" in x for x in out))

    def test_quotes_must_be_verbatim_and_abstract_level_when_an_abstract_is_quoted(self):
        p = copy.deepcopy(PACKET)
        p["candidates"][0]["evidence"][0]["quote_candidate"] = "computing exact estimates"
        p["candidates"][0]["evidence"][0]["text_level"] = "full_text"
        out = self.problems(p)
        self.assertTrue(any("not found verbatim in oa-W2" in x for x in out))
        self.assertTrue(any("text_level must be 'abstract'" in x for x in out))

    def test_rule_5_and_rule_6(self):
        p = copy.deepcopy(PACKET)
        p["claim"]["text"] += " Unlike Dempster, we use NPAG."
        p["candidates"][0]["work"]["publication_date"] = "2014-01-01"
        out = self.problems(p)
        self.assertTrue(any("surname 'Dempster'" in x for x in out))
        self.assertTrue(any("coined name 'NPAG'" in x for x in out))
        self.assertTrue(any("not before the claim date" in x for x in out))

    def test_notes_must_not_say_how_a_candidate_relates_to_the_claim(self):
        p = copy.deepcopy(PACKET)
        p["candidates"][0]["note"] = "Covers e1 and e2 by role only."
        self.assertTrue(any("candidate c1 note: says how a candidate relates" in x for x in self.problems(p)))
        p["candidates"][0]["note"] = "Statistics paper, 1977; abstract only."
        self.assertEqual(self.problems(p), [])
        p["notes"] = "c1 matches e2 at the level of roles."
        self.assertTrue(any(x.startswith("notes: says how") for x in self.problems(p)))

    def test_neutral_note_states_facts_only(self):
        note = annotation.neutral_note(PACKET["candidates"][0])
        self.assertEqual(note, "Field not in OpenAlex; 1977. Text: abstract only cached (oa-W2). 2 pre-filled quote pairs.")
        self.assertIsNone(annotation.EVALUATIVE.search(note))

    def test_meta_needs_found_by_for_every_candidate(self):
        meta = copy.deepcopy(META)
        meta["found_by"] = {}
        self.assertTrue(any("found_by is empty for c1" in x for x in self.problems(PACKET, meta)))

    def test_meta_negative_kinds(self):
        meta = copy.deepcopy(META)
        meta["negative"] = {"kind": "attributed_renaming_control"}
        self.assertTrue(any("negative.kind" in x for x in self.problems(PACKET, meta)))
        meta["negative"] = {"kind": "contrast"}
        self.assertTrue(any("from_item" in x for x in self.problems(PACKET, meta)))
        meta["negative"] = {"kind": "natural_distractor"}
        self.assertFalse(any("negative" in x for x in self.problems(PACKET, meta)))


class TestAiOriginText(PacketTestCase):
    def ai_packet(self):
        packet = copy.deepcopy(PACKET)
        packet["claim"]["source_work"] = {"ai_origin": "ai-x-1", "title": "A generated method", "year": 2026,
                                          "publication_date": "2026-09-24", "cache_key": "aio-ai-x-1"}
        (self.dir / "aio-ai-x-1.txt").write_text("Method name\n\nProblem: a problem. Method: a method.")
        return packet

    def test_method_text_travels_in_the_packet_and_is_checked(self):
        packet = self.ai_packet()
        self.assertTrue(any("method_text" in p for p in self.problems(packet)))
        annotation.embed_method_text(packet, self.dir)
        self.assertFalse(any("method_text" in p for p in self.problems(packet)))
        packet["claim"]["source_work"]["method_text"] = "edited"
        self.assertTrue(any("differs from the cached text" in p for p in self.problems(packet)))

    def test_bare_copy_keeps_the_method_text(self):
        packet = annotation.embed_method_text(self.ai_packet(), self.dir)
        bare = annotation.strip_prefill(packet)
        self.assertEqual(bare["claim"]["source_work"]["method_text"], packet["claim"]["source_work"]["method_text"])

    def test_assigned_copy_finds_the_shared_meta(self):
        (self.dir / "assigned").mkdir()
        (self.dir / "b09-meta.json").write_text("{}")
        self.assertEqual(annotation._meta_path(self.dir / "assigned" / "b09-a1.json", {"batch": "b09"}),
                         self.dir / "b09-meta.json")


class TestAssemble(PacketTestCase):
    def test_assemble_writes_the_batch_and_the_hidden_meta(self):
        drafts = self.dir / "drafts"
        (drafts / "meta").mkdir(parents=True)
        (drafts / "cf-0001.json").write_text(json.dumps(PACKET))
        (drafts / "meta" / "cf-0001.json").write_text(json.dumps(META))
        out = self.dir / "batches"
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                unittest.mock.patch.object(annotation, "FULLTEXT_DIR", self.dir):
            report = annotation.assemble("b00", drafts, out, created="2026-10-06")
        batch = json.loads((out / "b00.json").read_text())
        meta = json.loads((out / "b00-meta.json").read_text())
        self.assertEqual(report, {})
        self.assertEqual([p["id"] for p in batch["items"]], ["cf-0001"])
        self.assertNotIn("found_by", json.dumps(batch))
        self.assertEqual(meta["items"]["cf-0001"]["found_by"], {"c1": ["mining:kulikowski"]})


BATCH = {"format": annotation.BATCH_FORMAT, "batch": "b00", "created": "2026-10-06",
         "labels": ["equivalent", "specialization", "generalization", "partial", "similar_mechanism", "different"],
         "items": [PACKET]}
BATCH_META = {"format": annotation.META_FORMAT, "batch": "b00", "items": {"cf-0001": META}}
LABELS = {
    "format": annotation.LABELS_FORMAT, "batch": "b00", "annotator": "a1", "exported_at": "2026-10-06T10:00:00Z",
    "items": [{"id": "cf-0001", "minutes": 21.5,
               "claim": {"text": PACKET["claim"]["text"], "skeleton": PACKET["claim"]["skeleton"],
                         "chart": [{"id": "e1", "text": "unknown mixing distribution", "key": True},
                                   {"id": "e2", "text": "nonparametric maximum likelihood on a grid", "key": True}]},
               "text_edited": False,
               "candidates": [{"cid": "c1", "relation": "similar_mechanism", "covered_elements": ["e1"],
                               "analogous_elements": ["e2"], "restricted_elements": [], "attribution": "unknown",
                               "cross_field": True, "confidence": 2, "rejected_evidence": [], "note": "EM, not NPML"}],
               "verdict": "similar_mechanism", "status": "done", "notes": "read the abstract only",
               "prefill_status": "draft"}]}


def _candidate(labels):
    return labels["items"][0]["candidates"][0]


class TestLabels(PacketTestCase):
    def labels_with(self, **changes):
        labels = copy.deepcopy(LABELS)
        _candidate(labels).update(changes)
        return labels

    def test_good_labels_pass(self):
        self.assertEqual(annotation.labels_problems(BATCH, LABELS), [])

    def test_file_level_checks(self):
        labels = copy.deepcopy(LABELS)
        labels.update(format="labels/v0", batch="b01", annotator="")
        labels["items"].append(dict(labels["items"][0], id="cf-0099"))
        out = annotation.labels_problems(BATCH, labels)
        for needle in ("format must be", "not 'b00'", "annotator must be", "'cf-0099' is not in batch"):
            self.assertTrue(any(needle in x for x in out), needle)
        labels = copy.deepcopy(LABELS)
        labels["items"] = []
        self.assertTrue(any("cf-0001 of the batch is missing" in x for x in annotation.labels_problems(BATCH, labels)))

    def test_claim_checks(self):
        labels = copy.deepcopy(LABELS)
        item = labels["items"][0]
        item["minutes"] = 0
        item["claim"]["chart"][1]["id"] = "e3"
        item["claim"]["chart"][0]["key"] = False
        item["claim"]["text"] = "Edited text."
        out = annotation.labels_problems(BATCH, labels)
        for needle in ("minutes must be a number above 0", "claim.chart ids", "text_edited is false"):
            self.assertTrue(any(needle in x for x in out), needle)
        labels = copy.deepcopy(LABELS)
        for element in labels["items"][0]["claim"]["chart"]:
            element["key"] = False
        self.assertTrue(any("no key element" in x for x in annotation.labels_problems(BATCH, labels)))

    def test_relation_rules(self):
        cases = [
            ({"relation": None}, "no relation"),
            ({"relation": "same"}, "not one of the six labels"),
            ({"relation": "partial", "covered_elements": ["e1"]}, "(partial): needs at least two"),
            ({"relation": "equivalent", "covered_elements": ["e1"]}, "(equivalent): key elements not covered"),
            ({"relation": "specialization", "covered_elements": ["e1", "e2"]}, "no restricted element"),
            ({"analogous_elements": []}, "no analogous key element"),
            ({"covered_elements": ["e9"]}, "['e9'] are not in the chart"),
            ({"covered_elements": ["e2"]}, "both covered and analogous"),
            ({"confidence": 3}, "abstract-only evidence allows confidence at most 2"),
            ({"confidence": None}, "confidence must be 1, 2 or 3"),
            ({"cross_field": None}, "cross_field must be true or false"),
            ({"attribution": "yes"}, "attribution must be one of"),
            ({"rejected_evidence": [5]}, "rejected_evidence must list indices"),
        ]
        for changes, needle in cases:
            labels = self.labels_with(**changes)
            labels["items"][0]["verdict"] = annotation.derive_verdict(labels["items"][0]) \
                if changes.get("relation") != "same" else "similar_mechanism"
            out = annotation.labels_problems(BATCH, labels)
            self.assertTrue(any(needle in x for x in out), (needle, out))

    def test_similar_mechanism_needs_a_counterpart_for_every_key_element(self):
        # LABELING.md clarification 1: with key element e1 neither covered nor analogous, the pair is different
        labels = self.labels_with(covered_elements=[])
        self.assertEqual(annotation.labels_problems(BATCH, labels),
                         ["cf-0001: candidate c1 (similar_mechanism): key elements without a counterpart: ['e1'] "
                          "(LABELING.md clarification 1: then the pair is different)"])
        _candidate(labels)["analogous_elements"] = ["e1", "e2"]
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])

    def test_similar_mechanism_needs_an_analogous_key_element(self):
        # LABELING.md clarification 1: with every key element covered and only the non-key e2 analogous, the pair is
        # equivalent, not similar_mechanism
        labels = copy.deepcopy(LABELS)
        labels["items"][0]["claim"]["chart"][1]["key"] = False
        self.assertEqual(annotation.labels_problems(BATCH, labels),
                         ["cf-0001: candidate c1 (similar_mechanism): no analogous key element"])
        labels["items"][0]["claim"]["chart"][1]["key"] = True  # e2, key, corresponds by role only
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])

    def test_similar_mechanism_needs_a_quote_per_covered_key_element(self):
        # SCHEMA.md rule 2 (LABELING.md step 5): with the only pair for the covered key element e1 rejected, the merged
        # item fails the loader, and check-labels reports that once
        batch_path, labels_path = self.dir / "b00.json", self.dir / "b00-a1-labels.json"
        batch_path.write_text(json.dumps(BATCH))
        (self.dir / "b00-meta.json").write_text(json.dumps(BATCH_META))
        problem = "rule 2: candidate 0 (similar_mechanism): covered key elements without evidence: ['e1']"
        labels = self.labels_with(rejected_evidence=[1])
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])
        [item] = annotation.merge(BATCH, BATCH_META, labels)
        self.assertEqual(annotation.validate(item, self.checker), [problem])
        labels_path.write_text(json.dumps(labels))
        self.assertEqual(annotation.check_labels(batch_path, labels_path, self.checker), [f"cf-0001: {problem}"])
        labels_path.write_text(json.dumps(LABELS))  # the pair kept
        self.assertEqual(annotation.check_labels(batch_path, labels_path, self.checker), [])

    def test_rejecting_the_abstract_evidence_lifts_the_confidence_cap_and_verdict_must_match(self):
        self.assertEqual(annotation.labels_problems(BATCH, self.labels_with(confidence=3, rejected_evidence=[0, 1])),
                         [])
        labels = copy.deepcopy(LABELS)
        labels["items"][0]["verdict"] = "equivalent"
        self.assertTrue(any("differs from the derived verdict 'similar_mechanism'" in x
                            for x in annotation.labels_problems(BATCH, labels)))

    def test_skipped_items_may_be_incomplete_and_are_left_out_of_the_merge(self):
        labels = self.labels_with(relation=None, attribution=None, cross_field=None, confidence=None)
        item = labels["items"][0]
        item.update(status="skipped", minutes=0, verdict="nothing_close")
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])
        self.assertEqual(annotation.merge(BATCH, BATCH_META, labels), [])

    def test_merged_item_passes_the_loader(self):
        [item] = annotation.merge(BATCH, BATCH_META, LABELS)
        self.assertEqual(annotation.validate(item, self.checker), [])
        self.assertEqual((item["status"], item["version"], item["verdict"]), ("labelled", "v0", "similar_mechanism"))
        self.assertEqual((item["claim"]["written_by"], item["claim"]["checked_by"]), ("prefill:claude", "a1"))
        self.assertEqual([e["key"] for e in item["claim"]["chart"]], [True, True])
        self.assertEqual(item["claim"]["coined_names"], ["NPAG"])
        cand = item["candidates"][0]
        self.assertEqual(cand["found_by"], ["mining:kulikowski"])
        self.assertEqual((cand["gold"], cand["field_confirmed_by"], cand["cross_field"]), (True, "a1", True))
        self.assertEqual(len(cand["evidence"]), 2)
        self.assertEqual(item["annotation"]["annotators"],
                         [{"id": "a1", "relations": {"c1": "similar_mechanism"}, "verdict": "similar_mechanism",
                           "minutes": 21.5}])
        self.assertEqual(item["annotation"]["prefill"], PACKET["prefill"])
        self.assertEqual(item["annotation"]["batch"], "b00")
        self.assertEqual(item["annotation"]["notes"], "read the abstract only\nc1: EM, not NPML")
        self.assertEqual((item["pool"], item["provenance"]["source"]), (None, "kulikowski"))

    def test_merge_drops_rejected_evidence_and_the_loader_then_wants_a_quote(self):
        [item] = annotation.merge(BATCH, BATCH_META, self.labels_with(rejected_evidence=[0]))
        self.assertEqual([e["element"] for e in item["candidates"][0]["evidence"]], ["e1"])
        self.assertTrue(any("analogous elements without evidence" in x
                            for x in annotation.validate(item, self.checker)))

    def test_different_is_not_gold(self):
        labels = self.labels_with(relation="different", analogous_elements=[], confidence=2)
        labels["items"][0]["verdict"] = "nothing_close"
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])
        [item] = annotation.merge(BATCH, BATCH_META, labels)
        self.assertEqual((item["candidates"][0]["gold"], item["candidates"][0]["field_confirmed_by"]), (False, None))
        self.assertEqual(annotation.validate(item, self.checker), [])

    def test_check_labels_command(self):
        batch_path, labels_path = self.dir / "b00.json", self.dir / "b00-a1-labels.json"
        batch_path.write_text(json.dumps(BATCH))
        (self.dir / "b00-meta.json").write_text(json.dumps(BATCH_META))
        labels_path.write_text(json.dumps(LABELS))
        out_path = self.dir / "merged.jsonl"
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(annotation.main(["check-labels", str(batch_path), str(labels_path)]), 0)
            self.assertEqual(annotation.main(["merge", str(batch_path), str(labels_path), str(out_path)]), 0)
            labels_path.write_text(json.dumps(self.labels_with(relation="equivalent")))
            self.assertEqual(annotation.main(["check-labels", str(batch_path), str(labels_path)]), 1)
        self.assertIn("1 done items, 0 problems", printed.getvalue())
        self.assertIn("(equivalent): key elements not covered", printed.getvalue())
        self.assertEqual([json.loads(line)["id"] for line in out_path.read_text().splitlines()], ["cf-0001"])



# Labels format v2: chart edits, added evidence, added candidates, bare packets and the crossover. No network: the
# work lookup (annotation.fetch_work) is patched.

TEXTS_V2 = dict(TEXTS, **{"arxiv-9": "Mixture Models. The nonparametric maximum likelihood estimate of a mixing "
                                     "distribution is supported on finitely many points.",
                          "oa-W5": "Another Title\n\nA short abstract about support points."})
ADDED_WORK = {"openalex": "W9", "doi": "https://doi.org/10.1214/mix.1995", "arxiv": None, "pmid": None,
              "title": "Mixture Models: Theory, Geometry and Applications", "first_author": "Bruce G. Lindsay",
              "n_authors": 1, "year": 1995, "publication_date": "1995-01-01", "venue": "IMS", "field": "Mathematics",
              "domain": "Physical Sciences", "oa_url": None, "cache_key": "arxiv-9"}


def _ev(element, quote_candidate, quote_claim, level="full_text", general_form=False):
    return {"element": element, "quote_candidate": quote_candidate, "quote_claim": quote_claim,
            "location_candidate": "abstract", "location_claim": "section 2", "text_level": level,
            "general_form": general_form}


LABELS_V2 = copy.deepcopy(LABELS)
LABELS_V2["format"] = annotation.LABELS_FORMAT
_item = LABELS_V2["items"][0]
_item["claim"]["chart"].append({"id": "e7", "text": "the estimate sits on finitely many support points", "key": True})
_item["candidates"][0]["added_evidence"] = [_ev("e7", "maximum likelihood estimates", "on a grid", "abstract")]
_item["candidates"][0]["analogous_elements"] = ["e2", "e7"]  # every key element has a counterpart (clarification 1)
_item["added_candidates"] = [{
    "cid": "a1",
    "work": {"doi": "10.1214/mix.1995", "title": "Mixture models: theory, geometry and applications", "year": 1995,
             "first_author": "Bruce G. Lindsay", "field": "Statistics"},
    "evidence": [_ev("e1", "nonparametric maximum likelihood estimate of a mixing distribution",
                     "We estimate the mixing distribution"),
                 _ev("e2", "maximum likelihood estimate", "nonparametric maximum likelihood on a grid")],
    "relation": "partial", "covered_elements": ["e1", "e2"], "analogous_elements": [], "restricted_elements": [],
    "attribution": "unknown", "cross_field": True, "confidence": 3, "note": "found by a search in statistics"}]
_item["verdict"] = "partial"
BATCH_V2_META = {"format": annotation.META_FORMAT, "batch": "b00", "items": {"cf-0001": META}}


def fake_fetch(identifier):
    if identifier == "10.1214/mix.1995":
        return {"work": dict(ADDED_WORK), "texts": {"arxiv-9": {"level": "full_text", "words": 15}}, "problems": []}
    raise RuntimeError(f"unknown identifier {identifier}")


class V2TestCase(PacketTestCase):
    def setUp(self):
        super().setUp()
        for key, text in TEXTS_V2.items():
            (self.dir / f"{key}.txt").write_text(text)
        self.checker = QuoteChecker(lambda key: TEXTS_V2.get(key))

    def labels_v2(self):
        return copy.deepcopy(LABELS_V2)

    def item_problems(self, labels, batch=BATCH):
        return annotation.labels_problems(batch, labels)


class TestBarePacket(V2TestCase):
    def test_strip_prefill_keeps_the_facts_only_and_passes_the_packet_checks(self):
        bare = annotation.strip_prefill(PACKET)
        self.assertEqual(self.problems(bare), [])
        self.assertTrue(annotation.is_bare(bare))
        self.assertEqual(bare["prefill"], {"status": "none", "by": None, "date": None, "fields": []})
        claim = bare["claim"]
        self.assertEqual((claim["text"], claim["skeleton"], claim["chart"], claim["coined_names"]), ("", "", [], []))
        for field in ("date", "field", "domain", "computational", "source_work"):
            self.assertEqual(claim[field], PACKET["claim"][field])
        cand = bare["candidates"][0]
        self.assertEqual((cand["cid"], cand["work"], cand["evidence"], cand["attribution_hint"]),
                         ("c1", PACKET["candidates"][0]["work"], [], "unknown"))
        self.assertTrue(cand["note"].endswith("No pre-filled quote pairs."))
        self.assertEqual(bare["notes"], annotation.BARE_NOTES)
        self.assertEqual(PACKET["claim"]["text"][:8], "Estimate")  # the original is untouched

    def test_a_bare_packet_with_any_prefilled_text_or_evidence_is_rejected(self):
        cases = [(lambda p: p["claim"].update(text="Estimate it."), "claim.text is pre-filled"),
                 (lambda p: p["claim"].update(skeleton="Fit it."), "claim.skeleton is pre-filled"),
                 (lambda p: p["claim"].update(chart=copy.deepcopy(PACKET["claim"]["chart"])),
                  "claim.chart is pre-filled"),
                 (lambda p: p["claim"].update(coined_names=["NPAG"]), "claim.coined_names is pre-filled"),
                 (lambda p: p["candidates"][0].update(evidence=copy.deepcopy(PACKET["candidates"][0]["evidence"])),
                  "carries pre-filled evidence"),
                 (lambda p: p["candidates"][0].update(attribution_hint="cited"), "attribution_hint must be 'unknown'"),
                 (lambda p: p["candidates"][0].update(note="Statistics paper."), "generated neutral note"),
                 (lambda p: p.update(notes="Read section 2 first."), "standard instruction"),
                 (lambda p: p["prefill"].update(by="claude-opus-5-5"), "a bare packet's prefill must be")]
        for change, needle in cases:
            bare = annotation.strip_prefill(PACKET)
            change(bare)
            out = self.problems(bare)
            self.assertTrue(any(needle in x for x in out), (needle, out))


THREE = [PACKET, dict(copy.deepcopy(PACKET), id="cf-0002"), dict(copy.deepcopy(PACKET), id="cf-0003")]
BATCH_3 = dict(BATCH, items=THREE)
META_3 = {"format": annotation.META_FORMAT, "batch": "b00", "items": {p["id"]: META for p in THREE}}
PLAN = {"batch": "b00", "crossover": {"X_prefilled_for_annotator_1": ["cf-0001"],
                                      "Y_prefilled_for_annotator_2": ["cf-0002"]}}


class TestCrossover(V2TestCase):
    def test_each_copy_is_bare_where_the_plan_says_and_otherwise_the_batch(self):
        copies = annotation.crossover(BATCH_3, PLAN)
        self.assertEqual(sorted(copies), ["a1", "a2"])
        bare = {a: [p["id"] for p in c["items"] if annotation.is_bare(p)] for a, c in copies.items()}
        self.assertEqual(bare, {"a1": ["cf-0002"], "a2": ["cf-0001"]})
        for annotator, data in copies.items():
            self.assertEqual(data["annotator"], annotator)
            self.assertEqual(list(data)[:3], ["format", "batch", "annotator"])
            same = [p for p in data["items"] if not annotation.is_bare(p)]
            self.assertEqual(same, [p for p in THREE if p["id"] not in bare[annotator]])

    def test_plan_errors(self):
        def groups(x, y):
            return {"batch": "b00", "crossover": {"X_prefilled_for_annotator_1": x, "Y_prefilled_for_annotator_2": y}}
        for plan, needle in [(dict(PLAN, batch="b09"), "the plan is for batch"),
                             ({"batch": "b00", "crossover": {}}, "no crossover groups"),
                             (groups(["cf-0009"], ["cf-0002"]), "not in batch"),
                             (groups(["cf-0002"], ["cf-0002"]), "both groups")]:
            with self.assertRaises(ValueError) as caught:
                annotation.crossover(BATCH_3, plan)
            self.assertIn(needle, str(caught.exception))

    def test_crossover_command_writes_both_copies_and_check_batch_passes_on_them(self):
        batch_path = self.dir / "b00.json"
        batch_path.write_text(json.dumps(BATCH_3))
        (self.dir / "b00-meta.json").write_text(json.dumps(META_3))
        (self.dir / "plan.json").write_text(json.dumps(PLAN))
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                unittest.mock.patch.object(annotation, "FULLTEXT_DIR", self.dir), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = annotation.main(["crossover", str(batch_path), str(self.dir / "plan.json")])
            self.assertEqual(annotation.check_batch(self.dir / "b00-a2.json"), {})
        self.assertEqual(code, 0, printed.getvalue())
        report = json.loads(printed.getvalue())
        self.assertEqual(report["b00-a1.json"], {"bare": ["cf-0002"], "problems": {}})
        self.assertEqual(report["b00-a2.json"], {"bare": ["cf-0001"], "problems": {}})

    def test_labels_of_the_other_annotator_are_refused_against_a_copy(self):
        copies = annotation.crossover(BATCH_3, PLAN)
        labels = dict(copy.deepcopy(LABELS), annotator="a2")
        self.assertTrue(any("copy for annotator a1" in x for x in annotation.labels_problems(copies["a1"], labels)))


class TestLabelsV2(V2TestCase):
    def test_v1_file_still_checks_and_merges(self):
        labels = dict(copy.deepcopy(LABELS), format=annotation.LABELS_FORMAT_V1)
        del labels["items"][0]["prefill_status"]  # v1 had no such field
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])
        self.assertEqual(annotation.merge(BATCH, BATCH_META, labels), annotation.merge(BATCH, BATCH_META, LABELS))

    def test_v1_file_cannot_add_anything(self):
        labels = self.labels_v2()
        labels["format"] = annotation.LABELS_FORMAT_V1
        out = annotation.labels_problems(BATCH, labels)
        for needle in ("added_evidence needs labels format", "added_candidates needs labels format", "claim.chart ids"):
            self.assertTrue(any(needle in x for x in out), (needle, out))

    def test_good_v2_labels_pass(self):
        self.assertEqual(self.item_problems(self.labels_v2()), [])

    def test_chart_edits(self):
        labels = self.labels_v2()
        labels["items"][0]["claim"]["chart"][2]["id"] = "e3"
        self.assertTrue(any("claim.chart ids ['e3'] are neither packet elements nor new ids (added elements take e7"
                            in x for x in self.item_problems(labels)))
        labels = self.labels_v2()
        chart = labels["items"][0]["claim"]["chart"]
        chart += [dict(chart[2], id=f"e{n}") for n in range(8, 12)] + [dict(chart[2])]
        out = self.item_problems(labels)
        self.assertTrue(any("claim.chart ids repeat: ['e7']" in x for x in out), out)
        self.assertTrue(any("needs two to six elements, has 8" in x for x in out), out)

    def test_dropping_an_element_needs_its_prefilled_evidence_rejected(self):
        labels = self.labels_v2()
        item = labels["items"][0]
        item["claim"]["chart"] = [e for e in item["claim"]["chart"] if e["id"] != "e2"]
        item["claim"]["chart"].append({"id": "e8", "text": "a grid of candidate points", "key": False})
        cand = item["candidates"][0]
        cand.update(analogous_elements=["e1", "e7"], covered_elements=[])
        a1 = item["added_candidates"][0]
        a1.update(covered_elements=["e1", "e7"],
                  evidence=[a1["evidence"][0], _ev("e7", "supported on finitely many points", "on a grid")])
        item["added_candidates"][0]["relation"] = "equivalent"
        item["verdict"] = "equivalent"
        out = self.item_problems(labels)
        self.assertEqual(out, ["cf-0001: candidate c1: pre-filled evidence #1 is for e2, which is not in the chart "
                               "(reject it)"])
        cand["rejected_evidence"] = [0]
        self.assertEqual(self.item_problems(labels), [])

    def test_added_evidence_rules(self):
        cases = [({"element": "e5"}, "added evidence 1: element 'e5' is not in the chart"),
                 ({"element": None}, "added evidence 1: no element"),
                 ({"quote_candidate": " "}, "quote_candidate is empty"),
                 ({"quote_claim": "word " * 41}, "quote_claim has 41 words; the limit is 40"),
                 ({"location_claim": ""}, "location_claim is missing"),
                 ({"text_level": "full_text"}, "text_level must be 'abstract' when a quote comes from an abstract"),
                 ({"text_level": None}, "text_level must be one of ('full_text', 'abstract')"),
                 ({"general_form": None}, "general_form must be true or false")]
        for changes, needle in cases:
            labels = self.labels_v2()
            labels["items"][0]["candidates"][0]["added_evidence"][0].update(changes)
            out = self.item_problems(labels)
            self.assertTrue(any(needle in x for x in out), (needle, out))

    def test_added_abstract_evidence_caps_confidence(self):
        labels = self.labels_v2()
        _candidate(labels)["confidence"] = 3
        _candidate(labels)["rejected_evidence"] = [0]
        self.assertTrue(any("candidate c1: abstract-only evidence allows confidence at most 2" in x
                            for x in self.item_problems(labels)))

    def test_added_candidate_rules(self):
        def added(labels):
            return labels["items"][0]["added_candidates"][0]
        cases = [(lambda a: a.update(cid="x1"), "added candidate cids must be unique and look like a1"),
                 (lambda a: a["work"].update(doi="mix.1995"), "doi 'mix.1995' does not look like a DOI"),
                 (lambda a: a["work"].pop("doi"), "work needs one of the identifiers"),
                 (lambda a: a["work"].update(title=""), "candidate a1: work has no title"),
                 (lambda a: a["work"].update(first_author=None), "work has no first author"),
                 (lambda a: a["work"].update(field=" "), "work has no field"),
                 (lambda a: a["work"].update(year="1995"), "work year must be a whole number"),
                 (lambda a: a["work"].update(year=2014), "year 2014 is after the claim date 2013-03-26 (rule 6)"),
                 (lambda a: a["work"].update(doi=None, openalex="https://openalex.org/w2"),
                  "the same work as candidate c1"),
                 (lambda a: a["work"].update(doi=None, arxiv="arXiv:1v2"), "does not look like an arXiv ID"),
                 (lambda a: a["work"].update(doi=None, pmid="pmid:2"), None),
                 (lambda a: a.update(relation=None), "candidate a1: no relation"),
                 (lambda a: a.update(relation="equivalent"), "candidate a1 (equivalent): key elements not covered"),
                 (lambda a: a.update(covered_elements=["e1", "e9"]), "a1: covered_elements ['e9'] are not in"),
                 (lambda a: a.update(cross_field=None), "candidate a1: cross_field must be true or false"),
                 (lambda a: a["evidence"][0].update(element="e4"), "candidate a1 evidence 1: element 'e4' is not in"),
                 (lambda a: a["evidence"][0].update(text_level="abstract"), "a1: abstract-only evidence allows")]
        for change, needle in cases:
            labels = self.labels_v2()
            change(added(labels))
            labels["items"][0]["verdict"] = annotation.derive_verdict(
                {"candidates": labels["items"][0]["candidates"] + labels["items"][0]["added_candidates"]})
            out = self.item_problems(labels)
            if needle is None:
                self.assertEqual(out, [])
            else:
                self.assertTrue(any(needle in x for x in out), (needle, out))
        labels = self.labels_v2()
        labels["items"][0]["added_candidates"].append(dict(copy.deepcopy(added(labels)), cid="a2"))
        self.assertTrue(any("candidate a2: the same work as candidate a1" in x for x in self.item_problems(labels)))

    def test_verdict_counts_added_candidates(self):
        labels = self.labels_v2()
        labels["items"][0]["verdict"] = "similar_mechanism"
        self.assertTrue(any("differs from the derived verdict 'partial'" in x for x in self.item_problems(labels)))

    def test_skipped_item_may_hold_an_unfinished_added_candidate(self):
        labels = self.labels_v2()
        item = labels["items"][0]
        item.update(status="skipped", verdict=None)
        item["added_candidates"].append({"cid": "a2", "work": {"doi": "", "title": "", "year": None,
                                                               "first_author": "", "field": ""},
                                         "evidence": [{"element": "", "quote_candidate": "", "quote_claim": "",
                                                       "location_candidate": "", "location_claim": "",
                                                       "text_level": None, "general_form": False}],
                                         "relation": None, "covered_elements": [], "analogous_elements": [],
                                         "restricted_elements": [], "attribution": None, "cross_field": None,
                                         "confidence": None, "note": ""})
        item["verdict"] = annotation.derive_verdict({"candidates": item["candidates"] + item["added_candidates"]})
        self.assertEqual(self.item_problems(labels), [])

    def test_coined_names_are_typed_on_a_bare_packet_only(self):
        labels = self.labels_v2()
        labels["items"][0]["claim"]["coined_names"] = []
        self.assertTrue(any("only a bare packet's coined names are typed" in x for x in self.item_problems(labels)))
        labels["items"][0]["claim"]["coined_names"] = ["NPAG"]
        self.assertEqual(self.item_problems(labels), [])


def _bare_labels():
    labels = copy.deepcopy(LABELS_V2)
    item = labels["items"][0]
    item["claim"].update(text="Estimate how model parameters are distributed in a population without assuming a "
                              "parametric shape.", coined_names=["NPAG"],
                         chart=[{"id": "e1", "text": "unknown mixing distribution", "key": True},
                                {"id": "e2", "text": "maximum likelihood over a grid", "key": True},
                                {"id": "e3", "text": "finitely many support points", "key": True}])
    item["text_edited"] = True
    item["prefill_status"] = "none"
    item["candidates"][0]["added_evidence"] = [  # a bare packet pre-fills no pair: every marked element gets one
        _ev("e1", "maximum likelihood estimates from incomplete data", "We estimate the mixing distribution", "abstract"),
        _ev("e2", "computing maximum likelihood estimates", "nonparametric maximum likelihood on a grid", "abstract"),
        _ev("e3", "maximum likelihood estimates", "on a grid", "abstract")]
    item["candidates"][0]["analogous_elements"] = ["e2", "e3"]
    item["added_candidates"][0]["covered_elements"] = ["e1", "e2"]
    return labels


BARE_BATCH = dict(BATCH, items=[annotation.strip_prefill(PACKET)])


class TestMergeV2(V2TestCase):
    def test_merged_item_carries_added_candidates_evidence_and_chart_edits(self):
        works = {("cf-0001", "a1"): dict(ADDED_WORK)}
        [item] = annotation.merge(BATCH, BATCH_V2_META, LABELS_V2, works)
        self.assertEqual(annotation.validate(item, self.checker), [])
        self.assertEqual([e["id"] for e in item["claim"]["chart"]], ["e1", "e2", "e7"])
        c1, a1 = item["candidates"]
        self.assertEqual(len(c1["evidence"]), 3)
        self.assertEqual(c1["evidence"][2]["quote_candidate"], "maximum likelihood estimates")
        self.assertEqual((a1["work"], a1["found_by"], a1["relation"], a1["gold"], a1["field_confirmed_by"]),
                         (ADDED_WORK, ["annotator:a1"], "partial", True, "a1"))
        self.assertEqual(len(a1["evidence"]), 2)
        self.assertEqual(item["verdict"], "partial")
        # added candidates are keyed by their work, the same in both annotators' readings; a1 is this annotator's own
        self.assertEqual(item["annotation"]["annotators"][0]["relations"],
                         {"c1": "similar_mechanism", "openalex:W9": "partial"})
        self.assertIn("openalex:W9: found by a search in statistics", item["annotation"]["notes"])
        [offline] = annotation.merge(BATCH, BATCH_V2_META, LABELS_V2)  # unresolved: the typed DOI names it
        self.assertEqual(list(offline["annotation"]["annotators"][0]["relations"]), ["c1", "doi:10.1214/mix.1995"])

    def test_rejected_prefilled_evidence_is_left_out_and_added_evidence_kept(self):
        labels = self.labels_v2()
        _candidate(labels)["rejected_evidence"] = [0]
        [item] = annotation.merge(BATCH, BATCH_V2_META, labels, {("cf-0001", "a1"): dict(ADDED_WORK)})
        self.assertEqual([e["element"] for e in item["candidates"][0]["evidence"]], ["e1", "e7"])

    def test_bare_claim_is_the_annotators_and_awaits_a_second_check(self):
        labels = _bare_labels()
        self.assertEqual(annotation.labels_problems(BARE_BATCH, labels), [])
        [item] = annotation.merge(BARE_BATCH, BATCH_V2_META, labels, {("cf-0001", "a1"): dict(ADDED_WORK)})
        claim = item["claim"]
        self.assertEqual((claim["written_by"], claim["checked_by"], claim["coined_names"]), ("a1", None, ["NPAG"]))
        self.assertEqual(item["annotation"]["prefill"]["status"], "none")
        self.assertEqual(annotation.validate(item, self.checker), [])
        item["status"] = "adjudicated"
        self.assertEqual(annotation.validate(item, self.checker),
                         ["rule 0: claim.checked_by is missing", f"rule 5: {annotation.CLAIM_CHECK_MISSING}"])
        claim["checked_by"] = "a2"
        self.assertEqual(annotation.validate(item, self.checker), [])

    def test_an_annotators_prefill_is_recorded_as_written_by_them(self):
        # a contrast item (coelacanth.benchmark.contrast): prefill.by names the annotator who wrote the claim; another
        # annotator's reading checks it, while the writer's own reading leaves the check to a second person
        batch = copy.deepcopy(BATCH)
        batch["items"][0]["prefill"]["by"] = "annotator:a2"
        self.assertEqual(annotation.claim_writer(batch["items"][0]), "a2")
        works = {("cf-0001", "a1"): dict(ADDED_WORK)}
        [item] = annotation.merge(batch, BATCH_V2_META, LABELS_V2, works)
        self.assertEqual((item["claim"]["written_by"], item["claim"]["checked_by"]), ("a2", "a1"))
        self.assertFalse(annotation.awaiting_claim_check(item))
        self.assertEqual(annotation.validate(item, self.checker), [])
        [item] = annotation.merge(batch, BATCH_V2_META, dict(self.labels_v2(), annotator="a2"), works)
        self.assertEqual((item["claim"]["written_by"], item["claim"]["checked_by"]), ("a2", None))
        self.assertTrue(annotation.awaiting_claim_check(item))
        self.assertEqual(annotation.validate(item, self.checker), [])
        # a model's pre-fill and a bare packet as before
        self.assertEqual(annotation.claim_writer(PACKET), annotation.PREFILL_AUTHOR)
        self.assertIsNone(annotation.claim_writer(annotation.strip_prefill(PACKET)))

    def test_bare_labels_need_coined_names(self):
        labels = _bare_labels()
        del labels["items"][0]["claim"]["coined_names"]
        self.assertTrue(any("claim.coined_names is missing" in x
                            for x in annotation.labels_problems(BARE_BATCH, labels)))
        labels["items"][0]["claim"]["coined_names"] = ["NPAG", ""]
        self.assertTrue(any("must be a list of names" in x for x in annotation.labels_problems(BARE_BATCH, labels)))


class TestResolveAndQuotes(V2TestCase):
    def test_resolve_added_returns_the_work_and_checks_title_date_and_duplicates(self):
        works, problems = annotation.resolve_added(BATCH, LABELS_V2, fake_fetch)
        self.assertEqual((works, problems), ({("cf-0001", "a1"): ADDED_WORK}, []))
        cases = [({"publication_date": "2013-03-26", "year": 2013},
                  "dated 2013-03-26 (OpenAlex), not before the claim date"),
                 ({"publication_date": None, "year": 2013}, "year 2013 (OpenAlex) is not clearly before the claim date"),
                 ({"publication_date": None, "year": None}, "resolves to a work without a date"),
                 ({"title": "Spin glasses and neural networks"}, "not to the typed title"),
                 ({"openalex": "W2"}, "resolves to the same work as candidate c1")]
        for changes, needle in cases:
            def fetch(identifier, changes=changes):
                result = fake_fetch(identifier)
                result["work"].update(changes)
                return result
            _, problems = annotation.resolve_added(BATCH, LABELS_V2, fetch)
            self.assertTrue(any(needle in x for x in problems), (needle, problems))

    def test_an_identifier_that_does_not_resolve_is_reported(self):
        labels = self.labels_v2()
        labels["items"][0]["added_candidates"][0]["work"]["doi"] = "10.9999/none"
        works, problems = annotation.resolve_added(BATCH, labels, fake_fetch)
        self.assertEqual(works, {})
        self.assertEqual(problems, ["cf-0001: candidate a1: 10.9999/none could not be resolved (RuntimeError: unknown "
                                    "identifier 10.9999/none)"])

    def test_added_quotes_are_checked_verbatim_against_the_right_texts(self):
        works = {("cf-0001", "a1"): dict(ADDED_WORK)}
        self.assertEqual(annotation.added_quote_problems(BATCH, LABELS_V2, works, self.checker), [])
        labels = self.labels_v2()
        item = labels["items"][0]
        item["candidates"][0]["added_evidence"][0]["quote_candidate"] = "We estimate the mixing distribution"
        item["added_candidates"][0]["evidence"][1]["quote_claim"] = "maximum likelihood estimates from incomplete data"
        out = annotation.added_quote_problems(BATCH, labels, works, self.checker)
        self.assertEqual(len(out), 2, out)
        self.assertIn("candidate c1 added evidence 1 quote_candidate: not found verbatim in oa-W2", out[0])
        self.assertIn("candidate a1 evidence 2 quote_claim: not found verbatim in arxiv-1", out[1])
        abstract_only = {("cf-0001", "a1"): dict(ADDED_WORK, cache_key="oa-W5")}
        out = annotation.added_quote_problems(BATCH, LABELS_V2, abstract_only, self.checker)
        self.assertTrue(any("text_level must be 'abstract': only the abstract of this work is cached (oa-W5)" in x
                            for x in out), out)
        self.assertTrue(any("quote_candidate: not found verbatim in oa-W5" in x for x in out), out)

    def write_batch(self, labels, batch=BATCH):
        batch_path, labels_path = self.dir / "b00.json", self.dir / "b00-a1-labels.json"
        batch_path.write_text(json.dumps(batch))
        (self.dir / "b00-meta.json").write_text(json.dumps(BATCH_V2_META))
        labels_path.write_text(json.dumps(labels))
        return batch_path, labels_path

    def test_check_labels_online_offline_and_the_bare_note(self):
        batch_path, labels_path = self.write_batch(LABELS_V2)
        self.assertEqual(annotation.check_labels(batch_path, labels_path, self.checker, fetch=fake_fetch), [])

        def no_network(identifier):
            raise AssertionError("offline must not resolve")
        notes = []
        self.assertEqual(annotation.check_labels(batch_path, labels_path, QuoteChecker(lambda key: None),
                                                 offline=True, fetch=no_network, notes=notes), [])
        self.assertEqual(notes, ["offline: 1 added works not resolved or dated, and no quote checked verbatim "
                                 "(3 added quote pairs)"])
        batch_path, labels_path = self.write_batch(_bare_labels(), BARE_BATCH)
        notes = []
        self.assertEqual(annotation.check_labels(batch_path, labels_path, self.checker, fetch=fake_fetch, notes=notes),
                         [])
        self.assertEqual(len(notes), 1)
        self.assertIn("cf-0001: claim written by a1 on a bare packet; a second person checks it", notes[0])

    def test_check_labels_notes_the_writers_own_reading_of_an_annotators_prefill(self):
        batch = copy.deepcopy(BATCH)
        batch["items"][0]["prefill"]["by"] = "annotator:a1"
        notes = []
        self.assertEqual(annotation.check_labels(*self.write_batch(LABELS_V2, batch), self.checker, fetch=fake_fetch,
                                                 notes=notes), [])
        self.assertEqual(notes, ["cf-0001: claim written by a1 as this item's pre-fill, and labelled by them too; a "
                                 "second person checks it against the source (claim.checked_by) before adjudication"])

    def test_check_labels_and_merge_commands(self):
        batch_path, labels_path = self.write_batch(LABELS_V2)
        out_path = self.dir / "merged.jsonl"
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                unittest.mock.patch.object(annotation, "fetch_work", fake_fetch), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(annotation.main(["check-labels", str(batch_path), str(labels_path)]), 0)
            self.assertEqual(annotation.main(["check-labels", "--offline", str(batch_path), str(labels_path)]), 0)
            self.assertEqual(annotation.main(["merge", str(batch_path), str(labels_path), str(out_path)]), 0)
            labels = self.labels_v2()
            labels["items"][0]["added_candidates"][0]["work"]["doi"] = "10.9999/none"
            labels_path.write_text(json.dumps(labels))
            self.assertEqual(annotation.main(["merge", str(batch_path), str(labels_path), str(out_path)]), 1)
        text = printed.getvalue()
        self.assertIn("1 done items, 0 problems", text)
        self.assertIn("note: offline: 1 added works not resolved", text)
        self.assertIn("added: cf-0001: candidate a1: 10.9999/none could not be resolved", text)
        self.assertIn("nothing written: 1 problems, the same as check-labels reports", text)
        [item] = [json.loads(line) for line in out_path.read_text().splitlines()]
        self.assertEqual((item["candidates"][1]["work"]["openalex"], item["candidates"][1]["found_by"]),
                         ("W9", ["annotator:a1"]))


# After the adversarial review of format v2: the copy the labels were made on, bare packets with unknown fields,
# merge as a full gate with a pending file, one problem per bad quote, works dated by arXiv version 1.

class TestPrefillStatus(V2TestCase):
    def test_labels_made_on_the_prefilled_copy_never_pass_on_the_bare_copy(self):
        contaminated = self.labels_v2()  # what the tool exported after restoring pre-filled work into a bare item
        item = contaminated["items"][0]
        item.update(text_edited=True)
        item["claim"]["coined_names"] = ["NPAG"]
        self.assertEqual(annotation.labels_problems(BARE_BATCH, contaminated),
                         ["cf-0001: prefill_status 'draft' but the packet is bare (labels made on another copy of "
                          "the batch)"])
        out = annotation.labels_problems(BATCH, _bare_labels())
        self.assertIn("cf-0001: prefill_status 'none' but the packet is pre-filled (labels made on another copy of "
                      "the batch)", out)

    def test_v2_labels_name_their_copy_and_v1_labels_need_not(self):
        labels = self.labels_v2()
        del labels["items"][0]["prefill_status"]
        self.assertEqual(annotation.labels_problems(BATCH, labels),
                         ["cf-0001: prefill_status must be 'draft' or 'none' (the copy the labels were made on), got "
                          "None"])
        labels = copy.deepcopy(LABELS)
        labels["format"] = annotation.LABELS_FORMAT_V1
        del labels["items"][0]["prefill_status"]
        self.assertEqual(annotation.labels_problems(BATCH, labels), [])


class TestBarePacketFields(V2TestCase):
    def test_a_bare_packet_may_hold_only_the_fields_strip_prefill_keeps(self):
        cases = [(lambda p: p["candidates"][0].update(quotes=["computing maximum likelihood estimates"]),
                  "bare packet: candidate c1 has fields strip_prefill does not keep: ['quotes']"),
                 (lambda p: p["claim"].update(chart_draft=[{"id": "e1", "text": "x"}]),
                  "bare packet: claim has fields strip_prefill does not keep: ['chart_draft']"),
                 (lambda p: p.update(prefill_notes="Read section 2."),
                  "bare packet has fields strip_prefill does not keep: ['prefill_notes']"),
                 (lambda p: p["candidates"][0]["work"].update(summary="EM for mixtures"),
                  "bare packet: candidate c1 work has fields strip_prefill does not keep: ['summary']"),
                 (lambda p: p["claim"]["source_work"].update(abstract="We estimate"),
                  "bare packet: claim.source_work has fields strip_prefill does not keep: ['abstract']")]
        for change, needle in cases:
            bare = annotation.strip_prefill(PACKET)
            change(bare)
            self.assertIn(needle, self.problems(bare))

    def test_strip_prefill_drops_unknown_work_fields(self):
        packet = copy.deepcopy(PACKET)
        packet["candidates"][0]["work"]["summary"] = "EM for mixtures"
        bare = annotation.strip_prefill(packet)
        self.assertNotIn("summary", bare["candidates"][0]["work"])
        self.assertEqual(self.problems(bare), [])

    def test_a_candidate_without_cached_text_says_its_quotes_cannot_be_checked(self):
        cand = copy.deepcopy(PACKET["candidates"][0])
        cand["work"]["cache_key"] = None
        self.assertIn("Text: none cached, so quotes from it cannot be checked", annotation.neutral_note(cand))


def _abstract_only_fetch(identifier):
    result = fake_fetch(identifier)
    result["work"]["cache_key"] = "oa-W5"
    return result


class TestMergeGate(V2TestCase):
    def write(self, labels, batch=BATCH, meta=BATCH_V2_META):
        batch_path, labels_path = self.dir / "b00.json", self.dir / "b00-a1-labels.json"
        batch_path.write_text(json.dumps(batch))
        (self.dir / "b00-meta.json").write_text(json.dumps(meta))
        labels_path.write_text(json.dumps(labels))
        return batch_path, labels_path

    def run_main(self, args, fetch=fake_fetch):
        with unittest.mock.patch.object(annotation, "QuoteChecker", lambda: self.checker), \
                unittest.mock.patch.object(annotation, "fetch_work", fetch), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = annotation.main(args)
        return code, printed.getvalue()

    def test_merge_refuses_what_check_labels_rejects_an_abstract_only_added_work(self):
        labels = self.labels_v2()
        a1 = labels["items"][0]["added_candidates"][0]
        a1["evidence"] = [_ev("e1", "A short abstract", "We estimate the mixing distribution"),
                          _ev("e2", "about support points", "nonparametric maximum likelihood on a grid")]
        batch_path, labels_path = self.write(labels)
        out_path = self.dir / "merged.jsonl"
        code, printed = self.run_main(["check-labels", str(batch_path), str(labels_path)], _abstract_only_fetch)
        self.assertEqual(code, 1)
        self.assertEqual(printed.count("text_level must be 'abstract': only the abstract of this work is cached"), 2)
        code, printed = self.run_main(["merge", str(batch_path), str(labels_path), str(out_path)],
                                      _abstract_only_fetch)
        self.assertEqual(code, 1)
        self.assertIn("nothing written: 2 problems", printed)
        self.assertFalse(out_path.exists())

    def test_merge_writes_bare_items_with_a_note_on_the_claim_check(self):
        bare = dict(annotation.strip_prefill(PACKET), id="cf-0002")
        batch = dict(BATCH, items=[PACKET, bare])
        meta = dict(BATCH_V2_META, items={"cf-0001": META, "cf-0002": META})
        labels = self.labels_v2()
        bare_item = dict(_bare_labels()["items"][0], id="cf-0002")
        labels["items"].append(bare_item)
        batch_path, labels_path = self.write(labels, batch, meta)
        out_path = self.dir / "merged.jsonl"
        code, printed = self.run_main(["merge", str(batch_path), str(labels_path), str(out_path)])
        self.assertEqual(code, 0, printed)
        merged = [json.loads(line) for line in out_path.read_text().splitlines()]
        self.assertEqual([i["id"] for i in merged], ["cf-0001", "cf-0002"])
        self.assertEqual(items.validate_items(merged, self.checker), {})
        self.assertFalse((self.dir / "merged.pending.jsonl").exists())
        self.assertIn("wrote 2 items to", printed)
        self.assertIn("note: cf-0002: claim written by a1 on a bare packet", printed)

    def test_one_problem_per_bad_added_quote(self):
        labels = self.labels_v2()
        labels["items"][0]["added_candidates"][0]["evidence"][0]["quote_candidate"] = "an invented sentence"
        batch_path, labels_path = self.write(labels)
        out = annotation.check_labels(batch_path, labels_path, self.checker, fetch=fake_fetch)
        self.assertEqual(out, ["added: cf-0001: candidate a1 evidence 1 quote_candidate: not found verbatim in arxiv-9: "
                               "'an invented sentence'"])
        # a source without cached text, as cf-0016 in b01: each added claim quote is reported once
        no_text = copy.deepcopy(BARE_BATCH)
        no_text["items"][0]["claim"]["source_work"]["cache_key"] = None
        batch_path, labels_path = self.write(_bare_labels(), no_text)
        out = annotation.check_labels(batch_path, labels_path, self.checker, fetch=fake_fetch)
        self.assertEqual(len(out), 5, out)
        self.assertTrue(all("quote_claim: no cache_key to check the quote against" in x for x in out), out)


class TestArxivDate(V2TestCase):
    def late_fetch(self, **changes):
        def fetch(identifier):
            result = fake_fetch(identifier)
            result["work"].update(dict({"arxiv": "1301.00001", "publication_date": "2014-02-01", "year": 2014},
                                       **changes))
            return result
        return fetch

    def test_a_work_is_dated_by_its_arxiv_version_1_when_openalex_has_the_later_journal_date(self):
        notes = []
        works, problems = annotation.resolve_added(BATCH, LABELS_V2, self.late_fetch(), notes,
                                                   arxiv_date=lambda aid: "2013-01-04")
        self.assertEqual(problems, [])
        work = works[("cf-0001", "a1")]
        self.assertEqual((work["publication_date"], work["year"]), ("2013-01-04", 2013))
        self.assertEqual(notes, ["cf-0001: candidate a1: dated by its arXiv version 1, 2013-01-04, not by the "
                                 "OpenAlex date 2014-02-01"])
        [item] = annotation.merge(BATCH, BATCH_V2_META, LABELS_V2, works)
        self.assertEqual(annotation.validate(item, self.checker), [])

    def test_the_date_used_is_in_the_problem(self):
        cases = [(lambda aid: "2014-03-01", "dated 2014-02-01 (OpenAlex; arXiv version 1 is 2014-03-01), not before"),
                 (lambda aid: "2013-06-01", "dated 2013-06-01 (arXiv version 1), not before the claim date"),
                 (lambda aid: None, "dated 2014-02-01 (OpenAlex), not before")]
        for arxiv_date, needle in cases:
            _, problems = annotation.resolve_added(BATCH, LABELS_V2, self.late_fetch(), arxiv_date=arxiv_date)
            self.assertTrue(any(needle in x for x in problems), (needle, problems))

        def offline(aid):
            raise OSError("no network")
        _, problems = annotation.resolve_added(BATCH, LABELS_V2, self.late_fetch(), arxiv_date=offline)
        self.assertTrue(any("(OpenAlex; arXiv version 1 not read: OSError: no network)" in x for x in problems),
                        problems)
        _, problems = annotation.resolve_added(BATCH, LABELS_V2, self.late_fetch(publication_date=None),
                                               arxiv_date=lambda aid: "2015-01-01")
        self.assertTrue(any("year 2014 (OpenAlex; arXiv version 1 is 2015-01-01) is not clearly before" in x
                            for x in problems), problems)

    def test_arxiv_is_read_only_when_the_openalex_date_fails(self):
        def never(aid):
            raise AssertionError("not needed")
        works, problems = annotation.resolve_added(BATCH, LABELS_V2, self.late_fetch(publication_date="1995-01-01",
                                                                                     year=1995), arxiv_date=never)
        self.assertEqual(problems, [])
        _, problems = annotation.resolve_added(BATCH, LABELS_V2, fake_fetch, arxiv_date=never)
        self.assertEqual(problems, [])  # no arXiv ID


import contextlib  # noqa: E402
import io  # noqa: E402
import unittest.mock  # noqa: E402


class TestKeyLevel(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(annotation.key_level("oa-W1"), "abstract")
        self.assertEqual(annotation.key_level("arxivabs-1"), "abstract")
        for key in ("arxiv-1", "pmc-PMC1", "aio-x", "pdf-W2139708568"):
            self.assertEqual(annotation.key_level(key), "full_text", key)
        self.assertIsNone(annotation.key_level("ctr-cf-0001"))


if __name__ == "__main__":
    unittest.main()
