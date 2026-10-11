"""Tests for the AI-origin prompt and parsing. No model is called.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import unittest

from coelacanth.mining.ai_origin import DATE, PER_FIELD, good_outputs, parse_methods, prompt


class TestAIOrigin(unittest.TestCase):
    def test_prompt_is_dated_and_names_the_field(self):
        text = prompt("epidemiology")
        self.assertIn(f"Today is {DATE}.", text)
        self.assertIn(f"Propose {PER_FIELD} new methods for open problems in epidemiology", text)
        self.assertIn('"why_new"', text)

    def test_parse_keeps_complete_entries(self):
        answer = ('Here you go:\n[{"name": "A", "problem": "p", "method": "m", "evaluation": "e", "why_new": "w"},'
                  ' {"name": "B", "problem": "p"}, "junk"]')
        self.assertEqual(parse_methods(answer), [{"name": "A", "problem": "p", "method": "m", "evaluation": "e",
                                                  "why_new": "w"}])
        self.assertEqual(parse_methods("no json"), [])
        self.assertEqual(parse_methods(None), [])


class TestGoodOutputs(unittest.TestCase):
    def test_answers_from_another_model_or_with_errors_are_not_used(self):
        good = '[{"name": "A", "problem": "p", "method": "m", "evaluation": "e", "why_new": "w"}]'
        outputs = [
            {"model": "claude-opus-5-5", "field": "f1", "at": "t1", "answer": good},  # models_used from the ledger
            {"model": "claude-opus-5-5", "field": "f2", "at": "t2", "answer": good},  # served by Opus 5
            {"model": "claude-sonnet-5", "field": "f1", "at": "t3", "answer": good, "models_used": ["claude-sonnet-5"]},
            {"model": "claude-sonnet-5", "field": "f2", "at": "t4", "answer": good, "is_error": True,
             "models_used": ["claude-sonnet-5"]},
        ]
        ledger = [{"model": "claude-opus-5-5", "field": "f1", "at": "t1", "models_used": ["claude-opus-5-5"]},
                  {"model": "claude-opus-5-5", "field": "f2", "at": "t2", "models_used": ["claude-opus-5"]}]
        self.assertEqual(sorted(good_outputs(outputs, ledger)), [("claude-opus-5-5", "f1"), ("claude-sonnet-5", "f1")])


if __name__ == "__main__":
    unittest.main()
