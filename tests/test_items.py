"""Tests for the benchmark item loader: one test class per SCHEMA.md validation rule.

The fixture is synthetic (made-up works and texts), so no real paper is quoted here.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from coelacanth.benchmark.items import (derive_verdict, guide_dois, load_items, skeleton_view, system_view,
                                        validate, validate_items)
from coelacanth.env import REPO_ROOT
from coelacanth.quotes import QuoteChecker

TEXTS = {
    "claim-src": "Glucose is measured at fixed times after the load. Consecutive measurements are joined by straight "
                 "lines, and the area of each strip between two times is added to give the response.",
    "cand-a": "The integral of a function known only at its ordinates is approximated by joining neighbouring "
              "ordinates with straight lines and summing the areas of the resulting trapezoids.",
    "cand-b": "For any fixed weights, the integral is approximated by a weighted sum of the ordinates, and the "
              "weights decide the accuracy.",
}


def ev(element, quote_candidate, quote_claim, general_form=False, text_level="full_text"):
    return {"element": element, "quote_candidate": quote_candidate, "quote_claim": quote_claim,
            "location_candidate": "p. 1", "location_claim": "p. 2", "text_level": text_level,
            "general_form": general_form}


E1 = ev("e1", "The integral of a function known only at its ordinates", "Glucose is measured at fixed times")
E2 = ev("e2", "joining neighbouring ordinates with straight lines", "Consecutive measurements are joined by straight lines")
E3 = ev("e3", "summing the areas of the resulting trapezoids", "the area of each strip between two times is added")


def make_item() -> dict:
    return {
        "id": "cf-0001",
        "version": "v0",
        "status": "adjudicated",
        "claim": {
            "text": "The response to the load is read from the measured glucose values: consecutive measurements are "
                    "joined by straight lines and the areas of the strips between measurement times are added.",
            "skeleton": "Approximate the integral of a sampled function by linear interpolation between neighbouring "
                        "samples and summation of the pieces.",
            "coined_names": ["Strip Model"],
            "written_by": "a1",
            "checked_by": "a2",
            "source_work": {"openalex": "W100", "title": "A synthetic model for metabolic response curves",
                            "first_author": "Okonkwo", "year": 2025, "cache_key": "claim-src"},
            "date": "2025-03-01",
            "field": "Medicine",
            "domain": "Health Sciences",
            "computational": True,
            "chart": [{"id": "e1", "text": "area under a sampled curve", "key": True},
                      {"id": "e2", "text": "straight lines between consecutive samples", "key": True},
                      {"id": "e3", "text": "sum of the strip areas", "key": True}],
        },
        "candidates": [
            {"work": {"openalex": "W200", "year": 1990, "publication_date": "1990-05-01", "cache_key": "cand-a"},
             "found_by": ["annotator:a1", "sys:refpipe-v0@2"], "relation": "equivalent",
             "covered_elements": ["e1", "e2", "e3"], "analogous_elements": [], "restricted_elements": [],
             "evidence": [E1, E2, E3], "attribution": "not_cited", "cross_field": True,
             "field_confirmed_by": "a2", "gold": True, "confidence": 3},
            {"work": {"openalex": "W300", "year": 1980, "cache_key": "cand-b"},
             "found_by": ["sys:inc-style@4"], "relation": "specialization",
             "covered_elements": ["e1", "e2", "e3"], "analogous_elements": [], "restricted_elements": ["e2"],
             "evidence": [ev("e1", "the integral is approximated", "Glucose is measured at fixed times"),
                          ev("e2", "the integral is approximated by a weighted sum of the ordinates",
                             "Consecutive measurements are joined by straight lines", general_form=True),
                          ev("e3", "a weighted sum of the ordinates", "the area of each strip between two times is added")],
             "attribution": "unknown", "cross_field": False, "field_confirmed_by": "a1", "gold": True,
             "confidence": 2},
            {"work": {"openalex": "W400", "year": 2001}, "found_by": ["sys:inc-style@12"], "relation": None,
             "covered_elements": [], "evidence": [], "attribution": "unknown", "gold": False, "confidence": None},
        ],
        "verdict": "equivalent",
        "pool": {"depth": 10, "systems": ["refpipe-v0", "inc-style"], "n_pooled": 3, "n_unjudged": 1},
        "exposure": {"kind": "letter", "work": None, "date": None},
        "origin": {"kind": "human", "system": None, "mode": None},
        "negative": None,
        "provenance": {"source": "declaration", "mined_by": "manual", "dataset": None, "license": "CC-BY-4.0",
                       "attribution": None},
        "annotation": {"annotators": [], "prefill": {"by": "claude", "fields": ["chart", "evidence"]}, "batch": 1,
                       "adjudicated_by": "a1+a2", "notes": ""},
        "exclusions": ["W100"],
    }


class ItemTestCase(unittest.TestCase):
    def setUp(self):
        self.checker = QuoteChecker(TEXTS.get)
        self.item = make_item()

    def problems(self, item=None, guide=frozenset()):
        return validate(item if item is not None else self.item, self.checker, guide)

    def assertRule(self, rule, item=None, guide=frozenset()):
        found = self.problems(item, guide)
        self.assertTrue(any(p.startswith(f"rule {rule}:") for p in found), f"no rule {rule} violation in {found}")


class TestFixtureAndStructure(ItemTestCase):
    def test_fixture_passes(self):
        self.assertEqual(self.problems(), [])

    def test_bad_id_short_chart_and_long_text(self):
        self.item["id"] = "cf-42"
        self.assertRule(0)
        item = make_item()
        item["claim"]["chart"] = item["claim"]["chart"][:1]
        self.assertRule(0, item)
        item = make_item()
        item["claim"]["text"] = "word " * 121
        self.assertRule(0, item)

    def test_labelled_item_needs_every_claim_field(self):
        del self.item["claim"]["skeleton"]
        self.assertRule(0)

    def test_ai_origin_source_needs_no_bibliographic_identifier_but_candidates_do(self):
        item = {"id": "cf-0002", "version": "v0", "status": "candidate",
                "claim": {"source_work": {"ai_origin": "ai-opus-1-1"}},
                "candidates": [{"work": {"ai_origin": "ai-opus-1-2"}, "found_by": ["sys:single@1"], "relation": None}]}
        found = self.problems(item)
        self.assertFalse(any("claim.source_work needs" in p for p in found))
        self.assertTrue(any("candidate 0: work needs" in p for p in found))

    def test_candidate_status_needs_little(self):
        item = {"id": "cf-0002", "version": "v0", "status": "candidate",
                "claim": {"source_work": {"doi": "10.1000/x"}},
                "candidates": [{"work": {"openalex": "W1"}, "found_by": ["mining:catalogue:gci"], "relation": None}]}
        self.assertEqual(self.problems(item), [])


class TestRule1LabelsAndVerdict(ItemTestCase):
    def test_unknown_label(self):
        self.item["candidates"][0]["relation"] = "same_as"
        self.assertRule(1)

    def test_stored_verdict_must_match_precedence(self):
        self.item["verdict"] = "specialization"
        self.assertRule(1)

    def test_gold_flag_follows_relation(self):
        self.item["candidates"][1]["gold"] = False
        self.assertRule(1)

    def test_precedence(self):
        item = {"candidates": [{"relation": r} for r in ("similar_mechanism", "specialization", "different", None)]}
        self.assertEqual(derive_verdict(item), "specialization")
        self.assertEqual(derive_verdict({"candidates": [{"relation": "different"}]}), "nothing_close")
        self.assertEqual(derive_verdict({"candidates": []}), "nothing_close")
        self.assertEqual(derive_verdict({"candidates": [{"relation": "partial"}, {"relation": "generalization"}]}),
                         "generalization")


class TestRule2Evidence(ItemTestCase):
    def test_covered_key_element_needs_evidence(self):
        self.item["candidates"][0]["evidence"] = [E1, E2]
        self.assertRule(2)

    def test_specialization_needs_restricted_elements(self):
        self.item["candidates"][1]["restricted_elements"] = []
        self.assertRule(2)

    def test_specialization_needs_general_form_quote(self):
        for entry in self.item["candidates"][1]["evidence"]:
            entry["general_form"] = False
        self.assertRule(2)

    def test_similar_mechanism_needs_evidence_per_analogous_element(self):
        cand = self.item["candidates"][1]
        cand.update(relation="similar_mechanism", covered_elements=[], restricted_elements=[],
                    analogous_elements=["e1", "e2"], evidence=[cand["evidence"][0]])
        self.item["verdict"] = "equivalent"
        self.assertRule(2)

    def test_similar_mechanism_needs_evidence_per_covered_key_element(self):
        # SCHEMA.md rule 2 (LABELING.md step 5): a covered key element needs a quote pair, as an analogous one does
        cand = self.item["candidates"][1]
        pairs = cand["evidence"]
        cand.update(relation="similar_mechanism", covered_elements=["e1"], restricted_elements=[],
                    analogous_elements=["e2", "e3"], evidence=pairs[1:])
        self.assertEqual(self.problems(),
                         ["rule 2: candidate 1 (similar_mechanism): covered key elements without evidence: ['e1']"])
        cand["evidence"] = pairs  # the pair for e1 added
        self.assertEqual(self.problems(), [])


class TestRule3Coverage(ItemTestCase):
    def as_partial(self, covered):
        cand = self.item["candidates"][1]
        cand.update(relation="partial", covered_elements=covered, restricted_elements=[],
                    evidence=[e for e in cand["evidence"] if e["element"] in covered])
        return self.item

    def test_partial_with_one_covered_key_element_is_rejected(self):
        self.assertRule(3, self.as_partial(["e1"]))

    def test_partial_with_every_key_element_covered_is_rejected(self):
        self.assertRule(3, self.as_partial(["e1", "e2", "e3"]))

    def test_partial_with_two_of_three_passes(self):
        self.assertEqual(self.problems(self.as_partial(["e1", "e3"])), [])

    def test_similar_mechanism_needs_a_counterpart_for_every_key_element(self):
        # LABELING.md clarification 1: key element e3 neither covered nor analogous makes the pair different
        cand = self.item["candidates"][1]
        cand.update(relation="similar_mechanism", covered_elements=[], restricted_elements=[],
                    analogous_elements=["e1", "e2"])
        self.assertEqual(self.problems(),
                         ["rule 3: candidate 1 (similar_mechanism): key elements without a counterpart: ['e3']"])
        cand["analogous_elements"] = ["e1", "e2", "e3"]  # the evidence already holds a quote pair for e3
        self.assertEqual(self.problems(), [])

    def test_similar_mechanism_needs_an_analogous_key_element(self):
        # LABELING.md clarification 1: every key element covered and only the non-key e3 analogous is equivalent
        self.item["claim"]["chart"][2]["key"] = False
        cand = self.item["candidates"][1]
        cand.update(relation="similar_mechanism", covered_elements=["e1", "e2"], restricted_elements=[],
                    analogous_elements=["e3"])
        self.assertEqual(self.problems(), ["rule 3: candidate 1 (similar_mechanism): no analogous key element"])
        cand.update(covered_elements=["e1"], analogous_elements=["e2", "e3"])  # e2, key, by role only
        self.assertEqual(self.problems(), [])
        cand.update(covered_elements=["e1", "e2"], analogous_elements=[])
        self.assertEqual(self.problems(), ["rule 3: candidate 1 (similar_mechanism): no analogous key element"])

    def test_equivalent_must_cover_every_key_element(self):
        self.item["candidates"][0]["covered_elements"] = ["e1", "e2"]
        self.assertRule(3)


class TestRule4Quotes(ItemTestCase):
    def test_quote_not_in_text(self):
        self.item["candidates"][0]["evidence"][0] = ev("e1", "a function known everywhere", E1["quote_claim"])
        self.assertRule(4)

    def test_quote_too_long(self):
        self.item["candidates"][0]["evidence"][0] = ev("e1", "word " * 41, E1["quote_claim"])
        self.assertRule(4)

    def test_missing_cached_text(self):
        self.item["candidates"][0]["work"]["cache_key"] = "not-cached"
        self.assertRule(4)

    def test_typographic_variants_still_match(self):
        soft_hyphen, no_break_space = chr(0x00AD), chr(0x00A0)
        quote = f"joining neigh{soft_hyphen}bouring{no_break_space}ordinates with  straight\nlines"
        self.item["candidates"][0]["evidence"][1] = ev("e2", quote, E2["quote_claim"])
        self.assertEqual(self.problems(), [])


class TestRule5ClaimText(ItemTestCase):
    def with_text(self, text):
        self.item["claim"]["text"] = text
        return self.item

    def test_surname(self):
        self.assertRule(5, self.with_text("As Okonkwo showed, consecutive measurements are joined by lines."))

    def test_candidate_surname_eponym(self):
        self.item["candidates"][0]["work"]["first_author"] = "Rudolf E. Kalman"
        self.assertRule(5, self.with_text("An ensemble Kalman filter joins consecutive measurements."))

    def test_three_title_words(self):
        self.assertRule(5, self.with_text("We give a model for metabolic response through strips between times."))

    def test_year_with_cue_or_known_year(self):
        self.assertRule(5, self.with_text("Since 1998 the strips between measurement times are added."))
        self.item = make_item()
        self.assertRule(5, self.with_text("Strips between times are added as 1990 suggested."))

    def test_plain_numbers_are_not_years(self):
        self.assertEqual(self.problems(self.with_text(
            "Strips between times are added; with 1800 samples the error falls below 0.1 percent.")), [])

    def test_coined_name(self):
        self.assertRule(5, self.with_text("The strip model joins consecutive measurements by straight lines."))

    def test_skeleton_must_differ_from_text(self):
        self.item["claim"]["skeleton"] = self.item["claim"]["text"].upper()
        self.assertRule(5)

    def test_checker_must_differ_from_writer(self):
        self.item["claim"]["checked_by"] = "a1"
        self.assertRule(5)

    def test_checker_required_from_adjudication_on(self):
        self.item["claim"]["checked_by"] = None
        self.item["status"] = "labelled"
        self.assertNotIn(5, {int(p.split(":")[0].split()[-1]) for p in self.problems(self.item) if p.startswith("rule")})
        self.item["status"] = "adjudicated"
        self.assertRule(5)


class TestRule6Dates(ItemTestCase):
    def test_gold_candidate_after_the_claim(self):
        self.item["candidates"][0]["work"]["publication_date"] = "2025-06-01"
        self.assertRule(6)

    def test_same_year_needs_a_full_date(self):
        self.item["candidates"][1]["work"]["year"] = 2025
        self.assertRule(6)

    def test_gold_candidate_without_date(self):
        del self.item["candidates"][1]["work"]["year"]
        self.assertRule(6)


class TestRule7Negatives(ItemTestCase):
    def test_unknown_kind(self):
        self.item["negative"] = {"kind": "attributed_renaming_control"}
        self.assertRule(7)

    def test_contrast_names_its_positive(self):
        self.item["negative"] = {"kind": "contrast"}
        self.assertRule(7)

    def test_built_negative_with_gold_keeps_its_kind(self):
        item = make_item()
        item["negative"] = {"kind": "natural_distractor"}
        self.assertEqual(self.problems(item), [])

    def test_clean_negative_passes(self):
        item = make_item()
        item["negative"] = {"kind": "contrast", "from_item": "cf-0003", "edited_element": "e2"}
        item["candidates"] = [dict(item["candidates"][0], relation="different", gold=False, covered_elements=[],
                                   evidence=[])]
        item["verdict"] = "nothing_close"
        self.assertEqual(self.problems(item), [])


class TestRule8GuideExamples(ItemTestCase):
    def test_guide_dois_come_from_labeling_md(self):
        dois = guide_dois((REPO_ROOT / "eval" / "benchmark" / "LABELING.md").read_text(encoding="utf-8"))
        self.assertIn("10.2337/diacare.17.2.152", dois)
        self.assertGreaterEqual(len(dois), 12)

    def test_item_on_a_guide_doi_must_be_excluded(self):
        self.item["claim"]["source_work"]["doi"] = "10.2337/diacare.17.2.152"
        self.assertRule(8, guide=frozenset({"10.2337/diacare.17.2.152"}))

    def test_guide_doi_in_url_form_is_caught(self):
        self.item["claim"]["source_work"]["doi"] = "https://doi.org/10.2337/DIACARE.17.2.152"
        self.assertRule(8, guide=frozenset({"10.2337/diacare.17.2.152"}))

    def test_guide_item_must_be_excluded(self):
        self.item["provenance"]["source"] = "guide"
        self.assertRule(8)


class TestRule9Origin(ItemTestCase):
    def test_ai_item_names_its_system(self):
        self.item["origin"] = {"kind": "ai", "system": None, "mode": "unknown"}
        self.assertRule(9)


class TestRule10Pool(ItemTestCase):
    def test_every_candidate_has_found_by(self):
        self.item["candidates"][0]["found_by"] = []
        self.assertRule(10)

    def test_found_by_form(self):
        self.item["candidates"][0]["found_by"] = ["sys:refpipe-v0"]
        self.assertRule(10)

    def test_unjudged_candidate_inside_the_depth(self):
        self.item["candidates"][2]["found_by"] = ["sys:inc-style@3"]
        self.assertRule(10)

    def test_unjudged_candidate_from_an_annotator(self):
        self.item["candidates"][2]["found_by"] = ["annotator:a2"]
        self.assertRule(10)

    def test_unjudged_candidates_are_fine_before_adjudication(self):
        self.item["status"] = "labelled"
        self.item["candidates"][2]["found_by"] = ["sys:inc-style@3"]
        self.assertEqual(self.problems(), [])


class TestRule11SystemView(ItemTestCase):
    def test_system_view_is_text_and_date_only(self):
        view = system_view(self.item)
        self.assertEqual(set(view), {"text", "date"})
        self.assertEqual(view["text"], self.item["claim"]["text"])
        flat = json.dumps(view)
        for hidden in (self.item["claim"]["skeleton"], "W100", "W200", "Okonkwo", "equivalent"):
            self.assertNotIn(hidden, flat)

    def test_skeleton_view_swaps_the_text(self):
        self.assertEqual(skeleton_view(self.item)["text"], self.item["claim"]["skeleton"])


class TestRule12CrossFieldConfirmed(ItemTestCase):
    def test_gold_candidate_needs_a_cross_field_flag(self):
        del self.item["candidates"][0]["cross_field"]
        self.assertRule(12)

    def test_flag_needs_a_confirming_annotator(self):
        self.item["candidates"][1]["field_confirmed_by"] = ""
        self.assertRule(12)

    def test_not_required_before_labelling(self):
        self.item["status"] = "pooled"
        self.item["verdict"] = None
        for cand in self.item["candidates"]:
            cand.pop("cross_field", None)
            cand.pop("field_confirmed_by", None)
        self.assertEqual([p for p in self.problems() if p.startswith("rule 12")], [])


class TestFiles(unittest.TestCase):
    def test_load_and_validate_a_file(self):
        items = [make_item(), make_item()]
        items[1]["id"] = "cf-0000"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            path.write_text("\n".join(json.dumps(i) for i in items) + "\n\n", encoding="utf-8")
            loaded = load_items(path)
            self.assertEqual(len(loaded), 2)
            report = validate_items(loaded, QuoteChecker(TEXTS.get))
            self.assertEqual(report, {"<file>": ["rule 0: items are not sorted by id"]})
            dup = copy.deepcopy(loaded)
            dup[1]["id"] = "cf-0001"
            self.assertIn("rule 0: item ids repeat", validate_items(dup, QuoteChecker(TEXTS.get))["<file>"])
            path.write_text('{"id": "cf-0001"}\n{broken\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, ":2:"):
                load_items(path)


if __name__ == "__main__":
    unittest.main()
