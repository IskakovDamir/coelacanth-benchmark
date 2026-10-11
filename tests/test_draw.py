"""Tests for the seeded v0 draws: deterministic, stratified, and blind to anything a system found."""

import unittest

from coelacanth.mining import draw

METHODS = [{"id": f"m{f}{g}{i}", "field": f"f{f}", "model": f"g{g}"} for f in range(3) for g in range(2) for i in range(3)]


class TestAiOrigin(unittest.TestCase):
    def test_same_seed_same_order(self):
        self.assertEqual(draw.ai_origin_order(METHODS), draw.ai_origin_order(list(reversed(METHODS))))

    def test_one_per_cell_before_a_second(self):
        picked = draw.ai_origin_pick(draw.ai_origin_order(METHODS), target=7)
        cells = [(m[1], m[2]) for m in picked]
        self.assertEqual(len(set(cells[:6])), 6)

    def test_unreadable_fields_are_skipped(self):
        picked = draw.ai_origin_pick(draw.ai_origin_order(METHODS), readable_fields={"f0"}, target=10)
        self.assertEqual(len(picked), 6)
        self.assertTrue(all(m[1] == "0" for m in picked))


class TestControlsAndNegatives(unittest.TestCase):
    def test_at_most_two_controls_per_family(self):
        queue = [{"source": "kulikowski", "role": "attributed_control", "ref": f"k{i}", "family": "a" if i < 5 else "b",
                  "batch": "b01" if i == 0 else None, "status": "in_batch" if i == 0 else "ready"} for i in range(8)]
        picked = draw.control_pick(queue, target=4)["picked"]
        families = [("a" if int(r[1:]) < 5 else "b") for r in picked]
        self.assertEqual(len(picked), 3)
        self.assertLessEqual(families.count("a"), 1)

    def test_named_methods_are_not_negatives(self):
        manifest = [{"id": "d1", "role": "distractor", "method_named": "yes"},
                    {"id": "d2", "role": "distractor", "method_named": "no"},
                    {"id": "d3", "role": "distractor", "method_named": ""},
                    {"id": "m1", "role": "member", "method_named": "no"}]
        out = draw.negative_pick(manifest, [{"number": 18, "kind": "B(neg)"}], target=10)
        self.assertEqual(out["picked"][0], "gci#18")
        self.assertEqual(sorted(out["picked"][1:]), ["d2", "d3"])


if __name__ == "__main__":
    unittest.main()
