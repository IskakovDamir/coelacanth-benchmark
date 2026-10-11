"""Tests for the practice kit in docs/annotators/practice: the batch, the key, the explanations and compare.py.

    PYTHONPATH=src python -m unittest tests.test_practice_kit -v

The verbatim quote checks read the cached texts in cache/fulltext (gitignored); those tests are skipped when the
texts are missing. Nothing here uses the network: the key adds no candidate, so check_labels resolves nothing.
"""

import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from coelacanth.benchmark import annotation, items
from coelacanth.quotes import FULLTEXT_DIR

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "docs" / "annotators" / "practice"
BATCH_PATH, META_PATH, KEY_PATH = KIT / "practice.json", KIT / "practice-meta.json", KIT / "key.json"
EXPLANATIONS_PATH = KIT / "key-explanations.json"
IDS = ["cf-9001", "cf-9002", "cf-9003"]
GUIDE_MESSAGE = f"meta: provenance.source must be one of {annotation.PROVENANCE_SOURCES}"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cache_keys(batch) -> set[str]:
    keys = set()
    for packet in batch["items"]:
        keys.add(packet["claim"]["source_work"]["cache_key"])
        keys.update(c["work"]["cache_key"] for c in packet["candidates"])
    return keys


BATCH = read(BATCH_PATH)
TEXTS_CACHED = all((FULLTEXT_DIR / f"{key}.txt").exists() for key in cache_keys(BATCH))
NEEDS_TEXTS = unittest.skipUnless(TEXTS_CACHED, "the cached texts of the practice works are not in cache/fulltext")


def load_compare():
    spec = importlib.util.spec_from_file_location("practice_compare", KIT / "compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_compare(labels: dict, *args: str) -> tuple[int, str]:
    """compare.py's main on a labels file written to a temporary folder: exit status and printed text."""
    compare = load_compare()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "practice-a1-labels.json"
        path.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            status = compare.main([*args, str(path)])
    return status, out.getvalue()


def student(**changes) -> dict:
    """A copy of the key as annotator a1's export, with the candidate c1 of some items changed."""
    labels = copy.deepcopy(read(KEY_PATH))
    labels["annotator"] = "a1"
    by_id = {i["id"]: i for i in labels["items"]}
    for item_id, fields in changes.items():
        item = by_id[item_id.replace("_", "-")]
        item["candidates"][0].update(fields)
        item["verdict"] = items.derive_verdict(item)
    return labels


class TestBatch(unittest.TestCase):
    def test_three_guide_items_in_the_packet_format(self):
        self.assertEqual(BATCH["format"], annotation.BATCH_FORMAT)
        self.assertEqual(BATCH["batch"], "practice")
        self.assertEqual(BATCH["labels"], list(annotation.RELATIONS))
        self.assertEqual([p["id"] for p in BATCH["items"]], IDS)
        self.assertTrue(all(p["prefill"]["status"] == "draft" for p in BATCH["items"]))

    @NEEDS_TEXTS
    def test_packets_pass_check_packet(self):
        for packet in BATCH["items"]:
            self.assertEqual(annotation.packet_problems(packet), [], packet["id"])

    @NEEDS_TEXTS
    def test_batch_passes_check_batch(self):
        report = annotation.check_batch(BATCH_PATH)
        if "guide" in annotation.PROVENANCE_SOURCES:
            self.assertEqual(report, {})
        else:
            # annotation.PROVENANCE_SOURCES does not list SCHEMA.md's 'guide' yet: that rejection, once per item,
            # is the only problem check-batch may report.
            self.assertEqual(report, {item_id: [GUIDE_MESSAGE] for item_id in IDS})

    def test_meta_marks_every_item_as_a_guide_example(self):
        meta = read(META_PATH)
        self.assertEqual(meta["format"], annotation.META_FORMAT)
        self.assertEqual(meta["batch"], "practice")
        self.assertEqual(sorted(meta["items"]), IDS)
        for packet in BATCH["items"]:
            item_meta = meta["items"][packet["id"]]
            self.assertEqual(item_meta["provenance"]["source"], "guide")
            cids = [c["cid"] for c in packet["candidates"]]
            others = [p for p in annotation.meta_problems(item_meta, cids) if p != GUIDE_MESSAGE]
            self.assertEqual(others, [], packet["id"])


class TestKey(unittest.TestCase):
    def setUp(self):
        self.key = read(KEY_PATH)

    def test_key_passes_labels_problems(self):
        self.assertEqual(self.key["format"], annotation.LABELS_FORMAT)
        self.assertEqual(self.key["annotator"], "key")
        self.assertEqual(annotation.labels_problems(BATCH, self.key), [])

    def test_key_gives_the_guide_labels(self):
        by_id = {i["id"]: i for i in self.key["items"]}
        self.assertEqual([by_id[i]["verdict"] for i in IDS], ["equivalent", "similar_mechanism", "nothing_close"])
        relations = [by_id[i]["candidates"][0]["relation"] for i in IDS]
        self.assertEqual(relations, ["equivalent", "similar_mechanism", "different"])
        # cf-9002: specialization needs the unstated premise of normal errors (clarification 4), so the key takes
        # the lower label with confidence 1 (clarification 7; decided 2026-10-09).
        cand = by_id["cf-9002"]["candidates"][0]
        self.assertEqual((cand["analogous_elements"], cand["restricted_elements"]), (["e1", "e2", "e3"], []))
        self.assertEqual(cand["confidence"], 1)
        self.assertTrue(all(i["status"] == "done" for i in self.key["items"]))

    def test_check_labels_offline_reports_only_rule_8(self):
        problems = annotation.check_labels(BATCH_PATH, KEY_PATH, offline=True)
        self.assertEqual([p for p in problems if ": rule 8: " not in p], [])
        self.assertIn(len(problems), (0, len(IDS)))

    @NEEDS_TEXTS
    def test_check_labels_with_quotes_reports_only_rule_8(self):
        # Rule 8 keeps guide examples out of the benchmark: merge writes status 'labelled', which a guide item may
        # never have. Everything else, the added quote pair for cf-9002's e3 checked verbatim included, passes.
        problems = annotation.check_labels(BATCH_PATH, KEY_PATH)
        self.assertEqual([p for p in problems if ": rule 8: " not in p], [])


class TestExplanations(unittest.TestCase):
    def test_every_label_of_the_key_is_explained(self):
        explanations = read(EXPLANATIONS_PATH)
        key = {i["id"]: i for i in read(KEY_PATH)["items"]}
        packets = {p["id"]: p for p in BATCH["items"]}
        for item_id in IDS:
            expl = explanations["items"][item_id]
            self.assertTrue(expl["verdict"] and expl["discuss"], item_id)
            self.assertEqual(sorted(expl["chart"]), sorted(e["id"] for e in key[item_id]["claim"]["chart"]))
            for cand in key[item_id]["candidates"]:
                ce = expl["candidates"][cand["cid"]]
                for field in ("relation", "covered_elements", "analogous_elements", "restricted_elements",
                              "added_evidence", "cross_field", "attribution", "confidence"):
                    self.assertTrue(ce.get(field), f"{item_id} {cand['cid']} {field}")
                self.assertEqual(set(ce["not"]), set(annotation.RELATIONS) - {cand["relation"]})
                pairs = next(c for c in packets[item_id]["candidates"] if c["cid"] == cand["cid"])["evidence"]
                self.assertEqual(sorted(ce["evidence"]), [str(n) for n in range(len(pairs))])

    def test_no_em_or_en_dashes(self):
        for path in sorted(KIT.iterdir()):
            if path.suffix in (".md", ".json", ".py"):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text, path.name)
                self.assertNotIn("\u2013", text, path.name)


class TestCompare(unittest.TestCase):
    def test_the_key_agrees_with_itself(self):
        status, out = run_compare(read(KEY_PATH))
        self.assertEqual(status, 0)
        self.assertIn("Всего расхождений: 0.", out)

    def test_a_wrong_relation_is_found_and_explained(self):
        labels = student(cf_9003={"relation": "similar_mechanism", "analogous_elements": ["e2"]})
        status, out = run_compare(labels)
        expl = read(EXPLANATIONS_PATH)["items"]["cf-9003"]["candidates"]["c1"]
        self.assertEqual(status, 1)
        section = out[out.index("==== cf-9003"):out.index("==== Итог")]
        self.assertIn("Отношение: у вас similar_mechanism, в ключе different.", section)
        self.assertIn(expl["relation"], section)
        self.assertIn(expl["not"]["similar_mechanism"], section)
        self.assertIn("Аналогичные элементы (analogous): у вас e2; в ключе нет.", section)
        self.assertIn("Вердикт пункта: у вас similar_mechanism, в ключе nothing_close.", section)
        self.assertIn("gold или нет по кандидатам: совпало 2 из 3", out)
        self.assertNotIn("у вас", out[:out.index("==== cf-9003")])  # the other items agree

    def test_a_missing_added_pair_and_a_kept_pair_are_found(self):
        labels = student(cf_9002={"added_evidence": []}, cf_9003={"rejected_evidence": [], "confidence": 2})
        status, out = run_compare(labels)
        self.assertEqual(status, 1)
        self.assertIn("Доказательства: в ключе есть пары цитат для e3, у вас нет.", out)
        self.assertIn("Пара цитат #1 (e2): вы её оставили, ключ её отклоняет.", out)
        self.assertIn("Уверенность: у вас 2, в ключе 3.", out)

    def test_a_missing_general_form_pair_is_found(self):
        # No pair of the key is general form since cf-9002 became similar_mechanism, so this runs on a key whose
        # added pair for cf-9002's e3 is marked general form, against labels that keep the pair unmarked.
        key = read(KEY_PATH)
        cand = next(i for i in key["items"] if i["id"] == "cf-9002")["candidates"][0]
        cand["added_evidence"][0]["general_form"] = True
        rep = load_compare().compare(BATCH, key, read(EXPLANATIONS_PATH), student())
        self.assertIn("    Общая форма (general form): в ключе есть такая пара для e3, у вас нет.", rep.lines)
        self.assertEqual(rep.differences, 1)

    def test_labels_of_another_batch_are_refused(self):
        labels = read(KEY_PATH)
        labels["batch"] = "b01"
        status, out = run_compare(labels)
        self.assertEqual(status, 2)
        self.assertIn("Это не метки учебного батча", out)

    def test_command_line(self):
        labels = student(cf_9001={"relation": "partial", "covered_elements": ["e1", "e2"]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "practice-a1-labels.json"
            path.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            run = subprocess.run([sys.executable, str(KIT / "compare.py"), str(path)], capture_output=True,
                                 env=env, timeout=60)
        out = run.stdout.decode("utf-8")
        self.assertEqual(run.returncode, 1, run.stderr.decode("utf-8", "replace"))
        self.assertIn("Проверка формата: проблем нет.", out)
        self.assertIn("Отношение: у вас partial, в ключе equivalent.", out)
        self.assertIn("Почему не partial:", out)


if __name__ == "__main__":
    unittest.main()
