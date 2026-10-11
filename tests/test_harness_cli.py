"""Tests for the harness command line (docs/design/harness.md section 6): one run per scored scope, `--resume`, the
status command and refusals printed instead of tracebacks. No model, network or paid API is called: the adapters are
fakes, and the measurement-A directory, the splits file, the items and the raw cache are temporary.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import contextlib
import functools
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth.harness import __main__ as cli
from coelacanth.harness import config, run

CFG = {"system": "single-agent", "adapter": "agent", "model": "claude-sonnet-5-5", "aux_models": [],
       "per_item_cap_usd": 0.4, "conditions": ["text", "skeleton"], "allowed_subsets": ["H"],
       "line": "llm_baselines", "cli": {"version": "2.1.286 (Claude Code)", "stop_subtypes": {}}}
OK = {"is_error": False, "result": "```json\n[]\n```", "total_cost_usd": 0.01,
      "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 5000}}, "native": {"candidates": []}}
UNPARSED = {**OK, "native": {"candidates": None}}
INFRA = {"is_error": True, "result": "connection reset"}


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "measurement-a"
        self.root.mkdir()
        self.ids = [f"cf-{n:04d}" for n in range(1, 7)]
        items = [{"id": i, "claim": {"text": f"claim {i}", "skeleton": "s", "date": "2001-02-03"}} for i in self.ids]
        (self.dir / "items.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
        self.splits = self.dir / "splits" / "splits-v1.json"
        self.splits.parent.mkdir()
        self.splits.write_text(json.dumps({"subsets": {"H": self.ids}}), encoding="utf-8")
        self.calls = []
        self.answers = {}
        for target, name, value in ((run, "MEASUREMENT_A", self.root), (run, "SPLITS", self.splits),
                                    (run, "ITEMS", self.dir / "items.jsonl"), (run, "RAW", self.dir / "raw"),
                                    (cli, "ADAPTERS", {"agent": self.adapter})):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self, view, cfg, ctx):
        self.calls.append(view["text"])
        return dict(self.answers.get(view["text"], OK))

    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        text = out.getvalue()
        return code, json.loads(text) if text.strip() else None

    def scored(self, *extra, preflight=None):
        """`run` with a logged config and a preflight that passes, unless `preflight` is given."""
        with mock.patch.object(config, "frozen", return_value=(dict(CFG), "a" * 64)), \
                mock.patch.object(run, "preflight", preflight or (lambda *a, **k: [])):
            return self.main("run", "--system", "single-agent", "--subset", "H", "--tier", "T1", "--cap", "5",
                             "--workers", "1", *extra)

    def runs(self):
        return sorted((self.root / "runs" / "single-agent" / "text").iterdir())


class TestOneRunPerScope(CliTestCase):
    def test_second_run_refused_and_resume_runs_only_pending_items(self):
        self.answers = {"claim cf-0002": UNPARSED, "claim cf-0003": INFRA, "claim cf-0004": INFRA,
                        "claim cf-0005": INFRA}
        code, summary = self.scored()
        self.assertEqual(code, 0)
        self.assertIn("3 failed calls in a row", summary["aborted"])
        self.assertEqual(len(self.calls), 5)  # cf-0006 never started
        code, refused = self.scored()
        self.assertEqual(code, 1)
        self.assertTrue(any("--resume" in p for p in refused["refused"]))
        self.assertEqual(len(self.calls), 5)
        self.answers = {}
        run_dir, = self.runs()
        code, summary = self.scored("--resume", str(run_dir))
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[5:], ["claim cf-0003", "claim cf-0004", "claim cf-0005", "claim cf-0006"])
        self.assertEqual(summary["by_status"], {"ok": 5, "unparsed": 1})  # the unparsed item is never resampled
        self.assertEqual(len(self.runs()), 1)

    def test_resume_of_another_scope_or_config_refused(self):
        self.answers = {"claim cf-0001": INFRA}
        self.scored()
        run_dir, = self.runs()
        with mock.patch.object(config, "frozen", return_value=(dict(CFG), "a" * 64)), \
                mock.patch.object(run, "preflight", lambda *a, **k: []):
            code, refused = self.main("run", "--system", "single-agent", "--subset", "H", "--tier", "T2", "--cap", "5",
                                      "--resume", str(run_dir))
        self.assertEqual(code, 1)
        self.assertTrue(any("not of" in p for p in refused["refused"]))
        with mock.patch.object(config, "frozen", return_value=(dict(CFG), "b" * 64)), \
                mock.patch.object(run, "preflight", lambda *a, **k: []):
            code, refused = self.main("run", "--system", "single-agent", "--subset", "H", "--tier", "T1", "--cap", "5",
                                      "--resume", str(run_dir))
        self.assertEqual(code, 1)
        self.assertTrue(any("was run under config" in p for p in refused["refused"]))
        code, refused = self.scored("--resume", str(self.dir))
        self.assertEqual(code, 1)
        self.assertTrue(any("is not a run directory" in p for p in refused["refused"]))
        self.assertEqual(len(self.calls), 6)

    def test_pilot_resume_refused_after_the_config_file_changed(self):
        configs = self.root / "configs"
        configs.mkdir()
        (configs / "single-agent.json").write_bytes(config.canonical_bytes(CFG))
        pilots = [{"id": i, "status": "excluded", "provenance": {"source": "guide"},
                   "claim": {"text": f"claim {i}", "date": "2001-02-03"}} for i in ("guide-A", "guide-B")]
        (self.root / "pilot-items.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pilots))
        self.answers = {"claim guide-B": INFRA}
        with mock.patch.object(run, "preflight", lambda *a, **k: []):
            pilot = ("pilot", "--system", "single-agent", "--items", "guide-A", "guide-B", "--cap", "1",
                     "--workers", "1")
            self.assertEqual(self.main(*pilot)[0], 0)
            run_dir, = sorted((self.root / "pilots" / "single-agent").iterdir())
            (configs / "single-agent.json").write_bytes(config.canonical_bytes({**CFG, "per_item_cap_usd": 0.5}))
            code, refused = self.main(*pilot, "--resume", str(run_dir))
            self.assertEqual(code, 1)
            self.assertTrue(any("config file of single-agent changed" in p for p in refused["refused"]))
            (configs / "single-agent.json").write_bytes(config.canonical_bytes(CFG))
            self.answers = {}
            code, summary = self.main(*pilot, "--resume", str(run_dir))
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, ["claim guide-A", "claim guide-B", "claim guide-B"])
        self.assertEqual(summary["by_status"], {"ok": 2})


class TestRefusalsAndStatus(CliTestCase):
    def test_run_before_the_freeze_is_refused_not_a_traceback(self):
        self.splits.unlink()
        checked = functools.partial(run.preflight, auth=lambda: None, cli_version=lambda: "2.1.286 (Claude Code)")
        code, refused = self.scored(preflight=checked)
        self.assertEqual(code, 1)
        self.assertTrue(any("no splits file" in p for p in refused["refused"]))
        self.assertEqual(self.calls, [])

    def test_status_counts_items_that_never_started(self):
        run_dir = self.dir / "a-run"
        run_dir.mkdir()
        ids = [f"x{n}" for n in range(10)]
        (run_dir / "run.json").write_text(json.dumps({"item_ids": ids, "finished": "t",
                                                      "summary": {"aborted": "3 failed calls in a row"}}))
        (run_dir / "outputs.jsonl").write_text("".join(json.dumps({"item": i, "status": "infra_error"}) + "\n"
                                                       for i in ids[:3]))
        code, status = self.main("status", str(run_dir))
        self.assertEqual(code, 0)
        self.assertEqual(status["items"], 10)
        self.assertEqual(status["no_row"], ids[3:])
        self.assertEqual(status["pending"], ids)
        self.assertEqual(status["aborted"], "3 failed calls in a row")


if __name__ == "__main__":
    unittest.main()
