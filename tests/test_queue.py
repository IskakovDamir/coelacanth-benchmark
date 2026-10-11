"""Tests for the candidate queue: batch membership, exclusions and the counts on the repository's own files.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import unittest

from coelacanth.mining import queue


class TestPlaced(unittest.TestCase):
    def test_batch_membership_beats_status_and_exclusions_are_marked(self):
        row = queue._placed({"ref": "W1"}, ("b01", "cf-0001"), "ready")
        self.assertEqual((row["status"], row["batch"], row["item"]), ("in_batch", "b01", "cf-0001"))
        ref = next(iter(queue.EXCLUDED))
        row = queue._placed({"ref": ref}, None, "ready")
        self.assertEqual(row["status"], "excluded")
        self.assertIn("b01", row["excluded_because"])


class TestOnRepositoryData(unittest.TestCase):
    def test_every_row_has_an_id_and_counts_add_up(self):
        rows = queue.build()
        self.assertEqual(len({r["qid"] for r in rows}), len(rows))
        c = queue.counts(rows)
        self.assertLessEqual(c["controls_in_batches"] + c["attributed_controls_to_draw"],
                             max(queue.TARGET["attributed_controls"], c["controls_in_batches"]))
        self.assertIn("# Candidate queue toward benchmark v0", queue.markdown(rows))


if __name__ == "__main__":
    unittest.main()
