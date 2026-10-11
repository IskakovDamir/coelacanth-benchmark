"""Tests for measurement A's label mapping: the three-class map of protocol section 7, the precedence rule on cleaned
lists, native item labels (unknown ones unmappable and kept) and null labels for failures.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import re
import unittest

from coelacanth.benchmark.items import ALREADY_DONE, VERDICTS
from coelacanth.env import REPO_ROOT
from coelacanth.harness.labels import THREE_CLASS, map_item

PRECEDENCE_CFG = {"label_map": {"rule": "precedence_over_candidates", "empty_list": "nothing_close",
                                "no_json": "unparsed"}}
ITEM_LABEL_CFG = {"label_map": {"rule": "native_item_label",
                                "map": {"not novel": {"six": None, "class3": "already_done"},
                                        "novel": {"six": "nothing_close", "class3": "nothing_close"}}}}
NONE_CFG = {"label_map": {"rule": "none"}}


class TestThreeClass(unittest.TestCase):
    def test_three_class_matches_protocol_7(self):
        self.assertEqual(set(THREE_CLASS), set(VERDICTS))
        line = next(line for line in (REPO_ROOT / "eval" / "protocol.md").read_text(encoding="utf-8").splitlines()
                    if line.startswith("- **Verdict**"))
        done = set(re.findall(r"`(\w+)`", re.search(r"already done = (.*?);", line).group(1)))
        related = set(re.findall(r"`(\w+)`", re.search(r"related = (.*?);", line).group(1)))
        self.assertEqual({k for k, v in THREE_CLASS.items() if v == "already_done"}, done)
        self.assertEqual(done, set(ALREADY_DONE))
        self.assertEqual({k for k, v in THREE_CLASS.items() if v == "related"}, related)
        self.assertEqual({k for k, v in THREE_CLASS.items() if v == "nothing_close"}, {"nothing_close"})


class TestPrecedence(unittest.TestCase):
    def test_strongest_relation_of_the_cleaned_list(self):
        cleaned = [{"rank": 1, "relation": "partial"}, {"rank": 2, "relation": "specialization"},
                   {"rank": 3, "relation": None}]
        raw = [{"rank": 1, "relation": "equivalent"}] + cleaned
        v = map_item(PRECEDENCE_CFG, None, cleaned, "ok", raw_candidates=raw)
        self.assertEqual((v["six"], v["class3"], v["mapped"]), ("specialization", "already_done", True))
        self.assertEqual(v["six_before_cleaning"], "equivalent")

    def test_empty_or_different_only_is_nothing_close(self):
        for cleaned in ([], [{"relation": "different"}]):
            v = map_item(PRECEDENCE_CFG, None, cleaned, "ok")
            self.assertEqual((v["six"], v["class3"], v["mapped"]), ("nothing_close", "nothing_close", True))

    def test_resolved_rows_read_relation_native(self):
        v = map_item(PRECEDENCE_CFG, None, [{"rank_clean": 1, "relation_native": "similar_mechanism"}], "ok")
        self.assertEqual((v["six"], v["class3"]), ("similar_mechanism", "related"))


class TestNativeItemLabel(unittest.TestCase):
    def test_known_labels_follow_the_config_map(self):
        v = map_item(ITEM_LABEL_CFG, "not novel", [{"relation": "equivalent"}], "ok")
        self.assertEqual((v["native"], v["six"], v["class3"], v["mapped"]), ("not novel", None, "already_done", True))
        self.assertEqual(map_item(ITEM_LABEL_CFG, "novel", [], "ok")["class3"], "nothing_close")

    def test_unknown_native_label_unmappable_and_kept(self):
        for label in ("maybe novel", None):
            v = map_item(ITEM_LABEL_CFG, label, [], "ok")
            self.assertEqual((v["native"], v["six"], v["class3"], v["mapped"]), (label, None, None, False))
            self.assertEqual(v["rule"], "native_item_label")


class TestNoLabel(unittest.TestCase):
    def test_failure_status_gives_null_labels(self):
        for cfg in (PRECEDENCE_CFG, ITEM_LABEL_CFG):
            for status in ("capped", "unparsed", "timeout", "declined", "error", "infra_error"):
                v = map_item(cfg, "not novel", [{"relation": "equivalent"}], status)
                self.assertEqual((v["six"], v["class3"], v["mapped"]), (None, None, None))

    def test_rule_none_is_not_unmappable(self):
        v = map_item(NONE_CFG, None, [{"relation": "equivalent"}], "ok")
        self.assertEqual((v["rule"], v["six"], v["class3"], v["mapped"]), ("none", None, None, None))

    def test_unknown_rule_raises(self):
        with self.assertRaises(ValueError):
            map_item({"label_map": {"rule": "vote"}}, None, [], "ok")


if __name__ == "__main__":
    unittest.main()
