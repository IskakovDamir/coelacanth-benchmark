"""Tests for the closed-book runner's prompt and answer matching. No model is called.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from coelacanth import closedbook
from coelacanth.closedbook import match_answer, match_gold, project, score, user_message

KALMAN = {"first_author": "Rudolf E. Kalman", "year": 1960,
          "title": "A New Approach to Linear Filtering and Prediction Problems"}
DEGROOT = {"first_author": "DeGroot", "year": 1970, "title": "Optimal Statistical Decisions"}
OHAGAN = {"first_author": "Anthony O’Hagan", "year": 1978, "title": "Curve Fitting and Optimal Design for Prediction"}


class TestMatchGold(unittest.TestCase):
    def test_full_title_is_strict(self):
        self.assertEqual(match_gold('"A new approach to linear filtering and prediction problems" (J. Basic Eng.)',
                                    KALMAN), "strict")

    def test_surname_year_and_part_of_title_is_strict(self):
        self.assertEqual(match_gold("R. E. Kalman (1960), linear filtering, control theory", KALMAN), "strict")

    def test_surname_and_year_alone_is_loose(self):
        self.assertEqual(match_gold("Kalman, 1961, Transactions of the ASME", KALMAN), "loose")

    def test_wrong_year_and_no_title_is_no_match(self):
        self.assertIsNone(match_gold("Kalman and Bucy, 1975, continuous-time filtering", KALMAN))

    def test_spaced_surname_and_accents_and_apostrophes(self):
        self.assertEqual(match_gold("M. H. De Groot, Optimal Statistical Decisions, 1970", DEGROOT), "strict")
        self.assertEqual(match_gold("O'Hagan A. (1978) curve fitting and optimal design for prediction", OHAGAN),
                         "strict")


class TestMatchAnswer(unittest.TestCase):
    def test_best_level_over_lines_and_adjacent_pairs(self):
        answer = "1. A new approach to linear filtering\n   R. E. Kalman, 1960, control engineering\n2. unrelated"
        self.assertEqual(match_answer(answer, [KALMAN])["levels"], ["strict"])

    def test_unknown_and_concepts(self):
        self.assertTrue(match_answer("Unknown.", [KALMAN])["unknown"])
        found = match_answer("This is optimal stopping theory, e.g. the secretary problem.", [DEGROOT],
                             ["optimal stopping", "sequential analysis"])
        self.assertEqual(found["concepts"], ["optimal stopping"])
        self.assertEqual(found["levels"], [None])


class TestPrompt(unittest.TestCase):
    def test_message_carries_claim_date_and_protocol_prompt(self):
        message = user_message("claim text", "2020-01-02")
        self.assertIn("Date: 2020-01-02", message)
        self.assertIn("Claim: claim text", message)
        self.assertIn('answer "unknown"', message)


class TestScore(unittest.TestCase):
    def test_hit_rates_failed_shares_and_projection(self):
        items = [
            {"pid": "p1", "source": "kulikowski", "ref": "k1", "date": "2020-01-01", "gold_concepts": [],
             "gold": [dict(KALMAN, kind="origin", cross_field=True)]},
            {"pid": "p2", "source": "gci", "ref": "g1", "date": "1984-01-01", "gold_concepts": [],
             "gold": [dict(DEGROOT, kind="catalogue", cross_field=True),
                      {"first_author": "Eric L. Charnov", "year": 1976, "title": "Optimal foraging, the marginal value theorem",
                       "kind": "same_field_prior", "cross_field": False}]},
        ]
        answers = [
            {"pid": "p1", "model": "m1", "sample": 1, "answer": "Kalman, R. E. (1960) A new approach to linear filtering"},
            {"pid": "p1", "model": "m2", "sample": 1, "answer": "unknown"},
            {"pid": "p2", "model": "m1", "sample": 1, "answer": "Charnov (1976) Optimal foraging, the marginal value theorem"},
            {"pid": "p2", "model": "m2", "sample": 1, "answer": "unknown"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "items.jsonl").write_text("\n".join(json.dumps(i) for i in items) + "\n")
            (run / "answers.jsonl").write_text("\n".join(json.dumps(a) for a in answers) + "\n")
            result = score(run)
        s = result["summary"]
        self.assertEqual(s["per_model"]["m1"]["items_hit_cross_strict"], 1)
        self.assertEqual(s["per_model"]["m1"]["items_hit_any_strict"], 2)
        self.assertEqual(s["per_model"]["m2"]["unknown_samples"], 2)
        self.assertEqual(s["failed"]["cross_strict"]["items"], 1)
        self.assertEqual(s["failed"]["any_strict"]["items"], 0)
        self.assertEqual(s["by_source"]["non_kulikowski"]["failed_cross_strict"], 1)
        proj = project(s, 70, {"kulikowski": 20, "non_kulikowski": 50})
        self.assertEqual(proj["pooled"]["strict"]["H"], 35.0)
        self.assertEqual(proj["by_mix"]["strict"]["H"], 50.0)


class TestPrompts(unittest.TestCase):
    def test_variants_share_the_format(self):
        frozen = closedbook.user_message("a claim", "2001-01-01")
        cross = closedbook.user_message("a claim", "2001-01-01", "cross_field")
        self.assertIn(closedbook.PROMPT, frozen)
        self.assertIn("in a different field", cross)
        self.assertTrue(frozen.endswith(closedbook.FORMAT) and cross.endswith(closedbook.FORMAT))

    def test_v1_models(self):
        self.assertEqual(sorted(closedbook.MODELS), ["claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-5-5"])


class TestDoneKeys(unittest.TestCase):
    def test_a_decline_is_retried_once_and_other_errors_always(self):
        row = {"pid": "cf-1", "model": "m", "prompt": "frozen"}
        answers = [{**row, "sample": 1, "is_error": False},
                   {**row, "sample": 2, "is_error": True, "declined": True},
                   {**row, "sample": 3, "is_error": True, "declined": True},
                   {**row, "sample": 3, "is_error": True, "declined": True},
                   {**row, "sample": 4, "is_error": True, "declined": False},
                   {**row, "sample": 4, "is_error": True, "declined": False}]
        done = closedbook.done_keys(answers)
        self.assertIn(("cf-1", "m", 1, "frozen"), done)
        self.assertNotIn(("cf-1", "m", 2, "frozen"), done)  # declined once: one retry left
        self.assertIn(("cf-1", "m", 3, "frozen"), done)  # declined twice: no third call
        self.assertNotIn(("cf-1", "m", 4, "frozen"), done)  # infrastructure errors are re-run


if __name__ == "__main__":
    unittest.main()
