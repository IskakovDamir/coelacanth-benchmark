"""Tests for the declaration triage: prompt, parsing, batch requests and a full run against a fake batch client.
No model is called.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth.llm import read_jsonl
from coelacanth.mining import triage
from coelacanth.mining.triage import MAX_ABSTRACT_WORDS, MODEL, batch_message, label_row, parse_answer, request


class TestPrompt(unittest.TestCase):
    def test_records_are_numbered_and_abstracts_cut(self):
        long_abstract = "word " * (MAX_ABSTRACT_WORDS + 50)
        message = batch_message([({"title": "First"}, "short abstract"), ({"title": "Second"}, long_abstract),
                                 ({"title": None}, None)])
        self.assertIn("[1]\nTitle: First\nAbstract: short abstract", message)
        self.assertIn("[2]\nTitle: Second", message)
        self.assertIn("[...]", message)
        self.assertIn("[3]\nTitle: (no title)\nAbstract: (no abstract)", message)
        self.assertLess(message.count("word"), MAX_ABSTRACT_WORDS + 10)


class TestParse(unittest.TestCase):
    def test_fenced_array_with_good_and_bad_entries(self):
        answer = """```json
[{"n": 1, "label": "cross_field", "relation": "equivalent", "earlier": "trapezoidal rule", "later": "Tai",
  "fields": "numerical analysis; medicine", "confidence": 0.9},
 {"n": 2, "label": "maybe"},
 {"n": "3", "label": "no", "relation": null, "earlier": null, "later": null, "fields": null, "confidence": 0.8},
 {"n": 9, "label": "no"}]
```"""
        parsed = parse_answer(answer, 3)
        self.assertEqual(sorted(parsed), [1, 3])
        self.assertEqual(parsed[1]["earlier"], "trapezoidal rule")

    def test_unparseable_answers_give_nothing(self):
        self.assertEqual(parse_answer("I cannot help with that.", 2), {})
        self.assertEqual(parse_answer("[{broken", 2), {})
        self.assertEqual(parse_answer(None, 2), {})


class TestBatchRequests(unittest.TestCase):
    def test_one_record_per_request_at_temperature_zero(self):
        req = request({"openalex": "W7", "title": "T"}, "an abstract")
        self.assertEqual(req["custom_id"], "W7")
        params = req["params"]
        self.assertEqual((params["model"], params["temperature"]), (MODEL, 0))
        self.assertIn("[1]\nTitle: T\nAbstract: an abstract", params["messages"][0]["content"])
        self.assertNotIn("[2]", params["messages"][0]["content"])

    def test_label_rows_for_success_parse_error_and_api_error(self):
        good = label_row("W1", "b", True, '[{"n": 1, "label": "cross_field", "relation": "equivalent", '
                                          '"earlier": "E", "later": "L", "fields": "a; b", "confidence": 0.8}]', "succeeded")
        self.assertEqual((good["label"], good["relation"], good["earlier"]), ("cross_field", "equivalent", "E"))
        self.assertEqual(label_row("W1", "b", True, "no json", "succeeded")["label"], "parse_error")
        self.assertEqual(label_row("W1", "b", True, None, "errored")["label"], "api_errored")


class FakeBatch:
    def __init__(self):
        self.created = None

    def create(self, requests):
        self.created = requests
        return {"id": "msgbatch_x", "processing_status": "in_progress"}

    def wait(self, batch_id, poll_seconds, on_poll=None):
        return {"id": batch_id, "processing_status": "ended", "results_url": "u",
                "request_counts": {"succeeded": len(self.created)}}

    def results(self, batch):
        for req in self.created:
            text = '[{"n": 1, "label": "no", "relation": null, "earlier": null, "later": null, "fields": null, ' \
                   '"confidence": 0.9}]'
            yield {"custom_id": req["custom_id"], "result": {"type": "succeeded", "message": {
                "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 1000, "output_tokens": 100}}}}


class TestRun(unittest.TestCase):
    def test_pilot_run_books_batch_costs_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            (cand / "openalex-declarations.jsonl").write_text(
                "\n".join(json.dumps({"openalex": f"W{i}", "title": f"T{i}"}) for i in range(5)) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), \
                    mock.patch.object(triage, "OUT_DIR", cand / "declarations-triage"), \
                    mock.patch.object(triage, "abstracts", lambda client, paged=False, with_round3=False: {"W0": "abstract zero"}), \
                    mock.patch.object(triage, "OpenAlexClient", lambda: None):
                fake = FakeBatch()
                summary = triage.run(cap=1.0, limit=2, client=fake)
            self.assertEqual(summary["submitted"], 2)
            self.assertEqual([r["custom_id"] for r in fake.created], ["W0", "W1"])
            ledger = read_jsonl(cand / "declarations-triage" / "ledger.jsonl")
            self.assertAlmostEqual(ledger[0]["cost_usd"], (1000 * 1.0 + 100 * 5.0) / 1e6 * 0.5)
            labels = read_jsonl(cand / "declarations-triage" / "labels.jsonl")
            self.assertEqual([(r["openalex"], r["label"], r["has_abstract"]) for r in labels],
                             [("W0", "no", True), ("W1", "no", False)])

    def test_hand_check_summary_counts_distinct_titles_and_precision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            out = cand / "declarations-triage"
            out.mkdir()
            works = [{"openalex": f"W{i}", "title": "Same title" if i < 2 else f"T{i}", "queries": ["q"]} for i in range(4)]
            (cand / "openalex-declarations.jsonl").write_text("\n".join(json.dumps(w) for w in works) + "\n")
            (out / "labels.jsonl").write_text("\n".join(json.dumps({"openalex": w["openalex"], "label": "cross_field"})
                                                         for w in works) + "\n")
            checks = [{"openalex": "W0", "check": "cross_field_specific"}, {"openalex": "W2", "check": "no"}]
            (out / "hand-check.jsonl").write_text("\n".join(json.dumps(c) for c in checks) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), mock.patch.object(triage, "OUT_DIR", out):
                s = triage.summarise_hand_check()["round_1"]
        self.assertEqual((s["positives"], s["positives_distinct_titles"], s["checked"]), (4, 3, 2))
        self.assertEqual(s["usable_cross_field"]["precision"], 0.5)
        self.assertEqual(s["usable_cross_field"]["projected_items"], 1.5)
        self.assertEqual(s["positives_per_query"]["q"], {"positives": 4, "usable_in_sample": 1})

    def test_two_readings_adjudicate_to_the_lower_and_are_reported(self):
        self.assertEqual(triage.adjudicate("cross_field_specific", "same_field"), "same_field")
        self.assertEqual(triage.adjudicate("no", "cross_field_vague"), "no")
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            out = cand / "declarations-triage"
            out.mkdir()
            works = [{"openalex": "W0", "title": "A"}, {"openalex": "W1", "title": "B"}]
            (cand / "openalex-declarations.jsonl").write_text("\n".join(json.dumps(w) for w in works) + "\n")
            (out / "labels.jsonl").write_text("\n".join(json.dumps({"openalex": w["openalex"], "label": "cross_field"})
                                                         for w in works) + "\n")
            checks = [{"openalex": "W0", "check": "same_field", "readers": {
                           "reader_1": {"check": "cross_field_specific"}, "reader_2": {"check": "same_field"}}},
                      {"openalex": "W1", "check": "cross_field_specific", "readers": {
                           "reader_1": {"check": "cross_field_specific"}, "reader_2": {"check": "cross_field_specific"}}}]
            (out / "hand-check.jsonl").write_text("\n".join(json.dumps(c) for c in checks) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), mock.patch.object(triage, "OUT_DIR", out):
                s = triage.summarise_hand_check()["round_1"]
        self.assertEqual(s["by_reader"]["reader_1"]["usable_k"], 2)
        self.assertEqual(s["by_reader"]["reader_2"]["usable_k"], 1)
        self.assertEqual(s["usable_cross_field"]["k"], 1)
        self.assertEqual(s["reader_agreement"], {"agree": 1, "of": 2})

    def test_duplicate_titles_in_the_sample_count_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            out = cand / "declarations-triage"
            out.mkdir()
            works = [{"openalex": "W0", "title": "Comment on X"}, {"openalex": "W1", "title": "Comment on X"},
                     {"openalex": "W2", "title": "Other"}]
            (cand / "openalex-declarations.jsonl").write_text("\n".join(json.dumps(w) for w in works) + "\n")
            (out / "labels.jsonl").write_text("\n".join(json.dumps({"openalex": w["openalex"], "label": "cross_field"})
                                                         for w in works) + "\n")
            checks = [{"openalex": "W0", "check": "cross_field_specific"},
                      {"openalex": "W1", "check": "cross_field_specific"}, {"openalex": "W2", "check": "no"}]
            (out / "hand-check.jsonl").write_text("\n".join(json.dumps(c) for c in checks) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), mock.patch.object(triage, "OUT_DIR", out):
                s = triage.summarise_hand_check()["round_1"]
        self.assertEqual((s["checked"], s["checked_distinct_titles"]), (3, 2))
        self.assertEqual(s["usable_cross_field"]["precision"], 0.5)
        self.assertEqual(s["usable_cross_field"]["records"]["precision"], 0.667)

    def test_rounds_are_projected_separately_and_summed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            out = cand / "declarations-triage"
            out.mkdir()
            works = [{"openalex": "W0", "title": "A", "queries": ["q"]}, {"openalex": "W1", "title": "B", "queries": ["q"]},
                     {"openalex": "W2", "title": "A", "queries": ["p"], "round": 2},
                     {"openalex": "W3", "title": "C", "queries": ["p"], "round": 2},
                     {"openalex": "W4", "title": "D", "queries": ["p"], "round": 2}]
            (cand / "openalex-declarations.jsonl").write_text("\n".join(json.dumps(w) for w in works) + "\n")
            (out / "labels.jsonl").write_text("\n".join(json.dumps({"openalex": w["openalex"], "label": "cross_field"})
                                                         for w in works) + "\n")
            (out / "hand-check.jsonl").write_text(json.dumps({"openalex": "W0", "check": "cross_field_specific"}) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), mock.patch.object(triage, "OUT_DIR", out):
                fresh = triage.sample_positives(5, round_=2)
                (out / "hand-check-round2.jsonl").write_text(
                    "\n".join(json.dumps({"openalex": f["openalex"], "check": "no" if f["openalex"] == "W4"
                                           else "cross_field_specific"}) for f in fresh) + "\n")
                s = triage.summarise_hand_check()
        self.assertEqual(sorted(f["openalex"] for f in fresh), ["W2", "W3", "W4"])
        # round 1: 2 distinct titles at precision 1; round 2: title A already counted, so C and D at precision 2/3
        self.assertEqual(s["round_1"]["positives_distinct_titles"], 2)
        self.assertEqual(s["round_2"]["positives_distinct_titles"], 2)
        self.assertEqual(s["round_2"]["usable_cross_field"]["projected_items"], 1.3)
        self.assertEqual(s["stratified"]["usable_cross_field"]["projected_items"], 3.3)

    def test_round_cap_counts_only_that_rounds_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            out = cand / "declarations-triage"
            out.mkdir()
            works = [{"openalex": "W0", "title": "T0"}, {"openalex": "W1", "title": "T1", "round": 2}]
            (cand / "openalex-declarations.jsonl").write_text("\n".join(json.dumps(w) for w in works) + "\n")
            (out / "labels.jsonl").write_text(json.dumps({"openalex": "W0", "label": "no"}) + "\n")
            (out / "ledger.jsonl").write_text("\n".join(json.dumps({"custom_id": f"X{i}", "result": "succeeded",
                                                                     "cost_usd": 0.5}) for i in range(4)) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), mock.patch.object(triage, "OUT_DIR", out), \
                    mock.patch.object(triage, "abstracts", lambda client, paged=False, with_round3=False: {}), \
                    mock.patch.object(triage, "OpenAlexClient", lambda: None):
                fake = FakeBatch()
                summary = triage.run(cap=1.0, limit=None, client=fake, round_=2)
            self.assertEqual(summary["submitted"], 1)  # $2 spent in round 1 does not count against round 2's $1
            self.assertEqual([r["custom_id"] for r in fake.created], ["W1"])
            self.assertEqual(read_jsonl(out / "ledger.jsonl")[-1]["round"], 2)
            self.assertEqual(read_jsonl(out / "labels.jsonl")[-1]["round"], 2)

    def test_fit_cuts_a_batch_to_the_cap_in_priority_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            out = cand / "declarations-triage"
            out.mkdir()
            works = [{"openalex": f"W{i}", "title": f"T{i}", "round": 2,
                      "queries": ["comment_equivalent" if i < 3 else "abstract_has_been_rediscovered"]}
                     for i in range(6)]
            (cand / "openalex-declarations.jsonl").write_text("\n".join(json.dumps(w) for w in works) + "\n")
            (out / "ledger.jsonl").write_text("\n".join(json.dumps({"custom_id": f"X{i}", "result": "succeeded",
                                                                     "round": 2, "cost_usd": 0.1})
                                                         for i in range(2)) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), mock.patch.object(triage, "OUT_DIR", out), \
                    mock.patch.object(triage, "abstracts", lambda client, paged=False, with_round3=False: {}), \
                    mock.patch.object(triage, "OpenAlexClient", lambda: None):
                refused = triage.run(cap=0.5, limit=None, client=FakeBatch(), round_=2)
                fake = FakeBatch()
                summary = triage.run(cap=0.5, limit=None, client=fake, round_=2, fit=True)
            self.assertEqual(refused["submitted"], 0)
            # $0.2 spent, mean $0.1, 95% of the $0.3 left fits 2 records: the most productive query's first
            self.assertEqual([r["custom_id"] for r in fake.created], ["W3", "W4"])
            self.assertEqual(summary["round_unlabelled_left"], 4)

    def test_cap_blocks_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp)
            (cand / "openalex-declarations.jsonl").write_text(json.dumps({"openalex": "W0", "title": "T"}) + "\n")
            with mock.patch.object(triage, "CANDIDATES_DIR", cand), \
                    mock.patch.object(triage, "OUT_DIR", cand / "declarations-triage"), \
                    mock.patch.object(triage, "abstracts", lambda client, paged=False, with_round3=False: {}), \
                    mock.patch.object(triage, "OpenAlexClient", lambda: None):
                fake = FakeBatch()
                summary = triage.run(cap=0.0, limit=None, client=fake)
            self.assertEqual(summary["submitted"], 0)
            self.assertIsNone(fake.created)



if __name__ == "__main__":
    unittest.main()
