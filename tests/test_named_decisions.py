"""Tests for tools/checks/import_named_decisions.py on synthetic data."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "checks"))

import import_named_decisions as ind  # noqa: E402

MERGED = {"works": {
    "cf-1/W1": {"item": "cf-1", "annotator": "a1", "relation": "partial", "cross_field": "yes"},
    "cf-1/W2": {"item": "cf-1", "annotator": "a1", "relation": "different", "cross_field": "yes"},
    "cf-2/W3": {"item": "cf-2", "annotator": "a2", "relation": "similar_mechanism", "cross_field": "no"}}}


def good(**over):
    d = {"format": "coelacanth-named-decisions/v1", "decided_by": "damir", "page_hash": "h",
         "decisions": {"cf-1/W1": {"relation": "different", "cross_field": "yes", "reason": "r"},
                       "cf-2/W3": {"relation": "similar_mechanism", "cross_field": "no", "reason": "r"},
                       "cf-3/W9": {"relation": "partial", "cross_field": "yes", "reason": "r"}}}
    d.update(over)
    return d


class TestNamedDecisions(unittest.TestCase):
    def test_problems(self):
        ids = ["cf-1/W1", "cf-2/W3", "cf-3/W9"]
        self.assertEqual(ind.problems(good(), ids, "h"), [])
        bad = good(page_hash="x", decided_by="claude")
        bad["decisions"]["cf-1/W1"]["reason"] = " "
        del bad["decisions"]["cf-2/W3"]
        out = " | ".join(ind.problems(bad, ids, "h"))
        for part in ("decided_by 'claude'", "page hash differs", "cf-1/W1: no reason", "cf-2/W3: no decision"):
            self.assertIn(part, out)
        self.assertNotIn("no decision", " ".join(ind.problems(bad, ids, "h", partial=True)))

    def test_finalize_moves_items_out_of_h(self):
        f = ind.finalize(MERGED, good()["decisions"], ["cf-1", "cf-2", "cf-3", "cf-4"])
        self.assertEqual(f["works"]["cf-1/W1"]["by"], "damir")
        self.assertEqual(f["works"]["cf-1/W2"]["by"], "a1")
        self.assertFalse(f["works"]["cf-1/W1"]["cross_field_gold"])     # Damir lowered it to `different`
        self.assertTrue(f["works"]["cf-3/W9"]["cross_field_gold"])      # a work nobody had judged
        self.assertEqual(f["items_out_of_H"], ["cf-3"])
        self.assertEqual(f["H_after"], ["cf-1", "cf-2", "cf-4"])
        self.assertEqual(f["changed_from_student"], ["cf-1/W1"])


if __name__ == "__main__":
    unittest.main()
