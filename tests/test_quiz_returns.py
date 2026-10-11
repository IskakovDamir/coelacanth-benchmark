"""Tests for tools/quiz/check_returns.py on synthetic returned files."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "quiz"))

import check_returns  # noqa: E402


def answer(item, cid, relation, seconds, comment="", cross="yes"):
    return {"item": item, "candidate": cid, "letter": "A", "relation": relation, "other_text": "",
            "cross_field": cross, "comment": comment, "seconds": seconds}


class TestCheckReturns(unittest.TestCase):
    def test_describe_and_shared(self):
        proposals = {("cf-1", "c1"): {"model_top": "partial"}, ("cf-1", "c2"): {"model_top": "different"}}
        a = {"annotator": "a1", "answers": [
            answer("cf-1", "c1", "partial", 10, "Formula \\(x\\) matches e2.\nVoteView"),
            answer("cf-1", "c2", "similar_mechanism", 40, "short note", cross="no")]}
        b = {"annotator": "a3", "answers": [answer("cf-1", "c1", "partial", 30),
                                            answer("cf-1", "c2", "different", 25)]}
        d = check_returns.describe(a, proposals)
        self.assertEqual((d["answers"], d["seconds_total"], d["answers_under_20_seconds"]), (2, 50, 1))
        self.assertEqual((d["comments_with_source_label_line"], d["comments_with_latex"]), (1, 1))
        self.assertEqual(d["answers_equal_to_model_top"], 1)
        self.assertEqual(check_returns.shared(a, b),
                         {"annotators": ["a1", "a3"], "pairs": 2, "same_relation": 1, "same_cross_field": 1})
        self.assertEqual(check_returns.SOURCE_LABEL.search("ends with pubmed.ncbi.nlm.nih.gov").group(0).strip(),
                         "pubmed.ncbi.nlm.nih.gov")
        self.assertIsNone(check_returns.SOURCE_LABEL.search("e.g. a plain sentence."))


if __name__ == "__main__":
    unittest.main()
