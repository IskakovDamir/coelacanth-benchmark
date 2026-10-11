"""Tests for the verbatim quote check.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import unittest

from coelacanth.quotes import QuoteChecker, match_level, normalize

LEFT, RIGHT, EN_DASH, SOFT_HYPHEN, FI_LIGATURE = chr(0x201C), chr(0x201D), chr(0x2013), chr(0x00AD), chr(0xFB01)


class TestNormalization(unittest.TestCase):
    def test_quotes_dashes_ligatures_and_whitespace(self):
        self.assertEqual(normalize(f"{LEFT}{FI_LIGATURE}rst{RIGHT}\n  pages 1{EN_DASH}3"), '"first" pages 1-3')
        self.assertEqual(normalize(f"{FI_LIGATURE}t"), "fit")
        self.assertEqual(normalize(f"hy{SOFT_HYPHEN}phen"), "hyphen")

    def test_levels(self):
        text = "the estimate is biased, but its mean square er-\nror can be smaller"
        self.assertEqual(match_level("the estimate is  biased", text), "strict")
        self.assertEqual(match_level("mean square error can be smaller", text), "hyphen")
        self.assertIsNone(match_level("mean squared error", text))


class TestChecker(unittest.TestCase):
    def test_checker_reads_each_text_once_and_reports_missing_texts(self):
        reads = []

        def texts(key):
            reads.append(key)
            return {"a": "Plotting the estimates against k shows where they stabilise."}.get(key)

        checker = QuoteChecker(texts)
        self.assertEqual(checker.level("where they stabilise", "a"), "strict")
        self.assertIsNone(checker.level("where they converge", "a"))
        self.assertEqual(reads, ["a"])
        with self.assertRaises(FileNotFoundError):
            checker.level("anything", "missing")


if __name__ == "__main__":
    unittest.main()
