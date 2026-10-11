"""Tests for tools/checks/import_named.py on synthetic returned files."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "checks"))

import import_named  # noqa: E402

ASSIGNMENT = {"a1": {"ids": ["cf-1/W1", "cf-1/W2", "cf-2/W3"], "data_hash": "h1", "sha256": "x"}}


def file(**over):
    data = {"format": "coelacanth-named-works/v1", "annotator": "a1", "answered_by": "someone", "page_hash": "h1",
            "answers": {"cf-1/W1": {"relation": "partial", "cross_field": "yes", "note": "n1"},
                        "cf-1/W2": {"relation": "different", "cross_field": "yes"},
                        "cf-2/W3": {"relation": "similar_mechanism", "cross_field": "no"}}}
    data.update(over)
    return data


class TestImportNamed(unittest.TestCase):
    def test_valid_file_has_no_problems(self):
        self.assertEqual(import_named.problems(file(), ASSIGNMENT), [])

    def test_problems_are_named(self):
        bad = file(page_hash="other", answered_by=" ", format="v0")
        bad["answers"]["cf-9/W9"] = {"relation": "partial", "cross_field": "yes"}
        bad["answers"]["cf-1/W2"] = {"relation": "maybe", "cross_field": "yes"}
        del bad["answers"]["cf-2/W3"]
        out = " | ".join(import_named.problems(bad, ASSIGNMENT))
        for part in ("format 'v0'", "page hash differs", "answered_by is empty", "cf-9/W9: not on this page",
                     "cf-1/W2: no valid relation", "cf-2/W3: no valid relation"):
            self.assertIn(part, out)
        self.assertEqual(import_named.problems(file(annotator="a9"), ASSIGNMENT),
                         ["no page for annotator 'a9'"])

    def test_merge_marks_gold_and_cross_field_gold(self):
        m = import_named.merge([file()])
        w = m["works"]
        self.assertEqual((w["cf-1/W1"]["gold"], w["cf-1/W1"]["cross_field_gold"]), (True, True))
        self.assertEqual((w["cf-1/W2"]["gold"], w["cf-1/W2"]["cross_field_gold"]), (False, False))
        self.assertEqual((w["cf-2/W3"]["gold"], w["cf-2/W3"]["cross_field_gold"]), (True, False))
        self.assertEqual(m["for_damir"], ["cf-1/W1", "cf-2/W3"])
        self.assertEqual(m["by_item"]["cf-1"], {"works": 2, "gold": 1, "cross_field_gold": 1})
        self.assertEqual(w["cf-1/W1"]["answered_by_in_file"], "someone")


if __name__ == "__main__":
    unittest.main()
