"""Tests for the b02 plan on the repository's own files: size, unique IDs after b01, the crossover split."""

import unittest
from pathlib import Path

from coelacanth.mining import batchplan

DRAWS = Path(__file__).resolve().parents[1] / "data" / "candidates" / "draws-v0.json"


@unittest.skipUnless(DRAWS.exists(), "needs the data archive (data/candidates/draws-v0.json)")
class TestB02Plan(unittest.TestCase):
    def test_plan_is_deterministic_and_complete(self):
        plan = batchplan.plan()
        self.assertEqual(plan, batchplan.plan())
        ids = [m["id"] for m in plan["items"]]
        self.assertEqual(len(ids), 30)
        self.assertEqual(len(set(ids)), 30)
        self.assertEqual(min(ids), "cf-0037")
        self.assertNotIn("gci#18", {m["seed"] for m in plan["items"]})

    def test_crossover_groups_are_disjoint_members(self):
        plan = batchplan.plan()
        x, y = plan["crossover"]["X_prefilled_for_annotator_1"], plan["crossover"]["Y_prefilled_for_annotator_2"]
        self.assertEqual((len(x), len(y)), (6, 6))
        self.assertFalse(set(x) & set(y))
        self.assertTrue(set(x) | set(y) <= {m["id"] for m in plan["items"]})


if __name__ == "__main__":
    unittest.main()
