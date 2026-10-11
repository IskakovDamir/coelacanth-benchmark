"""Tests for the pooled prior-art search: what systems see, the output contract's parsing, and pooling. No model is
called.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth import priorart

METHOD = {"id": "ai-x-1", "model": "m", "field": "economics", "date": "2026-09-24", "name": "CoinedName-X",
          "problem": "A problem.", "method": "A method.", "evaluation": "An evaluation.",
          "why_new": "Unlike Smith 1990, this is new."}


class TestPrompts(unittest.TestCase):
    def test_method_text_hides_the_coined_name_and_the_novelty_argument(self):
        text = priorart.method_text(METHOD)
        self.assertIn("A method.", text)
        self.assertNotIn("CoinedName-X", text)
        self.assertNotIn("Smith 1990", text)

    def test_every_system_gets_the_date_bound_contract(self):
        for system in priorart.SYSTEMS:
            message = priorart.user_message(system, METHOD)
            self.assertIn("published before 2026-09-24", message)
            self.assertIn("at most 10", message)
        self.assertIn("steps 1 to 3", priorart.user_message("hunch", METHOD))
        self.assertIn(f"at most {priorart.MAX_SEARCHES} web searches", priorart.user_message("single", METHOD))


class TestParse(unittest.TestCase):
    def test_last_json_list_wins_and_entries_are_cleaned(self):
        answer = ('first ```json\n[{"title": "Old"}]\n```\nthen\n```json\n'
                  '[{"title": "A", "year": "1977", "relation": "partial"}, {"title": ""}, '
                  '{"title": "B", "relation": "maybe"}]\n```')
        out = priorart.parse_candidates(answer)
        self.assertEqual([c["title"] for c in out], ["A", "B"])
        self.assertEqual((out[0]["year"], out[0]["relation"], out[1]["relation"]), (1977, "partial", None))

    def test_no_block_is_none_and_an_empty_list_is_kept(self):
        self.assertIsNone(priorart.parse_candidates("no json here"))
        self.assertEqual(priorart.parse_candidates("```json\n[]\n```"), [])

    def test_at_most_ten(self):
        answer = "```json\n" + json.dumps([{"title": f"T{i}"} for i in range(15)]) + "\n```"
        self.assertEqual(len(priorart.parse_candidates(answer)), 10)


class TestPool(unittest.TestCase):
    def test_same_work_from_two_systems_is_pooled_once_with_both_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            rows = [{"method": "m1", "system": "single", "is_error": False,
                     "candidates": [{"rank": 1, "title": "Work A", "doi": "https://doi.org/10.1/A", "relation": "partial"}]},
                    {"method": "m1", "system": "hunch", "is_error": False,
                     "candidates": [{"rank": 3, "title": "work a", "doi": "10.1/a", "relation": "equivalent"},
                                    {"rank": 4, "title": "Work B", "doi": None, "relation": "different"}]}]
            (out / "answers.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            with mock.patch.object(priorart, "OUT_DIR", out):
                counts = priorart.pool()
                pooled = json.loads((out / "pooled.json").read_text())
        self.assertEqual(counts, {"m1": 2})
        self.assertEqual(pooled["m1"][0]["found_by"], ["sys:single@1", "sys:hunch@3"])
        self.assertEqual(pooled["m1"][0]["relations"], {"single": "partial", "hunch": "equivalent"})


if __name__ == "__main__":
    unittest.main()
