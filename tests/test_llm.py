"""Tests for the CLI plumbing: the child environment and the job runner's ledger, cap and error stop.
No model is called: a fake call stands in for the CLI.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import subprocess

from coelacanth.harness.run import classify
from coelacanth.llm import (child_env, claude_bin, cli_call, key_problem, last_result_event, read_jsonl, run_jobs,
                           usage_totals)


def fake_call(cost, is_error=False):
    """A CLI output whose token usage costs `cost` dollars (Haiku 4.5 input at $1 per million tokens)."""
    def call(job):
        return {"total_cost_usd": cost, "is_error": is_error, "result": f"answer {job}",
                "modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": int(round(cost * 1_000_000))}},
                "usage": {"input_tokens": int(round(cost * 1_000_000)), "output_tokens": 0}, "duration_ms": 1}
    return call


def slow_call(cost, seconds=0.05, cost_of=None):
    """`fake_call` that takes `seconds`, so parallel workers overlap; `cost_of(job)` overrides the cost per job. The
    most calls seen running at once is kept in `call.peak`."""
    lock, running = threading.Lock(), [0]

    def call(job):
        with lock:
            running[0] += 1
            call.peak = max(call.peak, running[0])
        time.sleep(seconds)
        with lock:
            running[0] -= 1
        return fake_call(cost_of(job) if cost_of else cost)(job)
    call.peak = 0
    return call


class TestCliCall(unittest.TestCase):
    def run_with(self, **kwargs):
        done = mock.Mock(returncode=0, stdout='{"result": "ok"}', stderr="")
        with mock.patch("coelacanth.llm.subprocess.run", return_value=done) as run:
            cli_call("claude-haiku-4-5-20251001", "hello", system="s", **kwargs)
        return run.call_args.args[0]

    def test_no_tools_by_default(self):
        cmd = self.run_with()
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", cmd)

    def test_tool_free_calls_are_bare_and_tool_calls_are_not(self):
        self.assertIn("--bare", self.run_with())
        cmd = self.run_with(tools=("WebSearch",))
        self.assertNotIn("--bare", cmd)
        self.assertIn("--safe-mode", cmd)

    def test_named_tools_are_the_only_ones_and_pre_approved(self):
        cmd = self.run_with(tools=("WebSearch", "WebFetch"))
        i, j = cmd.index("--tools"), cmd.index("--allowedTools")
        self.assertEqual(cmd[i + 1:i + 3], ["WebSearch", "WebFetch"])
        self.assertEqual(cmd[j + 1:j + 3], ["WebSearch", "WebFetch"])

    def test_max_turns_flag(self):
        cmd = self.run_with(max_turns=12)
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "12")
        self.assertNotIn("--max-turns", self.run_with())

    def test_trace_streams_events_and_returns_the_last_result(self):
        stream = "\n".join([json.dumps({"type": "system"}),
                             json.dumps({"type": "assistant", "message": {"content": [
                                 {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}}]}}),
                             json.dumps({"type": "result", "result": "final", "is_error": False, "num_turns": 3,
                                         "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10}}})])
        done = mock.Mock(returncode=0, stdout=stream, stderr="")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("coelacanth.llm.subprocess.run", return_value=done) as run:
            trace = Path(tmp) / "sub" / "t.jsonl"
            out = cli_call("claude-sonnet-5-5", "hi", system="s", tools=("WebSearch",), trace=trace)
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
            self.assertIn("--verbose", cmd)
            self.assertEqual(trace.read_text(encoding="utf-8"), stream)
        self.assertEqual(out["result"], "final")
        self.assertEqual(out["num_turns"], 3)

    def test_trace_without_a_result_event_is_an_error(self):
        done = mock.Mock(returncode=1, stdout='{"type": "system"}', stderr="boom")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("coelacanth.llm.subprocess.run", return_value=done):
            out = cli_call("claude-sonnet-5-5", "hi", system="s", trace=Path(tmp) / "t.jsonl")
        self.assertTrue(out["is_error"])
        self.assertEqual(out["assistant_events"], 0)
        self.assertNotIn("booked_cost", out)  # nothing ran: an infrastructure error, re-run on resume
        self.assertEqual(classify(out, {"per_item_cap_usd": 0.4}), "infra_error")

    def test_stream_that_dies_after_model_turns_is_booked_at_the_call_cap(self):
        turn = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}}],
            "usage": {"input_tokens": 5000, "output_tokens": 300}}})
        done = mock.Mock(returncode=137, stdout="\n".join([json.dumps({"type": "system"})] + [turn] * 8), stderr="")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("coelacanth.llm.subprocess.run", return_value=done):
            out = cli_call("claude-sonnet-5-5", "hi", system="s", tools=("WebSearch",), max_budget_usd=0.4,
                           trace=Path(tmp) / "t.jsonl")
            ledger = Path(tmp) / "ledger.jsonl"
            run_jobs([1], lambda job: out, lambda job, o, at: None, ledger_path=ledger, cap_usd=10.0, workers=1)
            entry = read_jsonl(ledger)[0]
        self.assertTrue(out["is_error"])
        self.assertEqual(out["assistant_events"], 8)
        self.assertEqual(out["booked_cost"], {"usd": 0.4, "source": "upper_bound"})
        self.assertEqual((entry["cost_usd"], entry["cost_source"]), (0.4, "upper_bound"))
        self.assertEqual(classify(out, {"per_item_cap_usd": 0.4}), "error")  # spend: a final failure, not re-run

    def test_timeout_is_flagged(self):
        with mock.patch("coelacanth.llm.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 5)):
            out = cli_call("claude-haiku-4-5-20251001", "hi", system="s", timeout=5)
        self.assertTrue(out["is_error"] and out["timed_out"])

    def test_last_result_event_skips_noise(self):
        self.assertEqual(last_result_event('noise\n{"type": "result", "result": 1}\n{bad'), {"type": "result", "result": 1})
        self.assertIsNone(last_result_event('{"type": "assistant"}'))


class TestChildEnvironment(unittest.TestCase):
    def test_parent_session_and_oauth_variables_go_and_the_api_key_stays(self):
        fake = {"PATH": "/bin", "CLAUDE_EFFORT": "max", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDECODE": "1",
                "CLAUDE_CODE_OAUTH_SCOPES": "s", "CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k"}
        with mock.patch.dict(os.environ, fake, clear=True):
            self.assertEqual(child_env(), {"PATH": "/bin", "ANTHROPIC_API_KEY": "k",
                                           "CLAUDE_CODE_NO_MODEL_FALLBACK": "1"})


class TestClaudeBinary(unittest.TestCase):
    def test_override_and_default(self):
        with mock.patch.dict(os.environ, {"COELACANTH_CLAUDE_BIN": "/opt/claude"}, clear=True):
            self.assertEqual(claude_bin(), "/opt/claude")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("coelacanth.env.load_dotenv"):
            self.assertEqual(claude_bin(), "claude")


class TestKeyProblem(unittest.TestCase):
    def test_oauth_tokens_and_missing_keys_are_refused_without_showing_them(self):
        token = "sk-ant-oat01-" + "x" * 60
        problem = key_problem(token)
        self.assertIn("OAuth token", problem)
        self.assertNotIn(token, problem)
        self.assertIn("neither in the environment", key_problem(None))
        self.assertIsNone(key_problem("sk-ant-api03-" + "y" * 60))


class TestRunJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "ledger.jsonl"
        self.saved = []

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, job, out, at):
        self.saved.append((job, out["result"]))

    def test_every_call_is_booked_at_its_usage_cost_and_saved(self):
        summary = run_jobs([1, 2, 3], fake_call(0.01), self.save, ledger_path=self.ledger, cap_usd=1.0, workers=1,
                           describe=lambda job: {"job": job})
        self.assertEqual(summary["calls"], 3)
        self.assertAlmostEqual(summary["spent_usd"], 0.03)
        self.assertIsNone(summary["aborted"])
        ledger = read_jsonl(self.ledger)
        self.assertEqual([e["job"] for e in ledger], [1, 2, 3])
        self.assertEqual({e["cost_source"] for e in ledger}, {"usage_at_official_prices"})
        self.assertEqual(ledger[0]["models_used"], ["claude-haiku-4-5-20251001"])
        self.assertEqual(len(self.saved), 3)

    def test_stops_when_projected_spend_passes_the_cap(self):
        summary = run_jobs(list(range(10)), fake_call(0.5), self.save, ledger_path=self.ledger, cap_usd=2.0,
                           workers=1)
        self.assertLessEqual(summary["calls"], 4)
        self.assertIn("passes the cap", summary["aborted"])

    def test_calls_in_flight_count_at_the_call_cap_from_the_first_call(self):
        # 4 workers, no mean yet: before, all 4 calls started at once and spent $1.60 of a $1.00 cap
        call = slow_call(0.4)
        summary = run_jobs(list(range(4)), call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=4,
                           call_cap_usd=0.4)
        self.assertLessEqual(summary["spent_usd"], 1.0)
        self.assertLessEqual(call.peak, 2)
        self.assertEqual(summary["calls"], 2)
        self.assertIn("passes the cap", summary["aborted"])

    def test_projection_counts_calls_in_flight(self):
        # protocol $20 run, 50 items at $0.41 and 4 workers: before, every projection left out the calls in flight
        # ($19.27), the run never stopped and spent $20.50
        summary = run_jobs(list(range(50)), slow_call(0.41, seconds=0.01), self.save, ledger_path=self.ledger,
                           cap_usd=20.0, workers=4, call_cap_usd=0.4)
        self.assertLessEqual(summary["spent_usd"], 20.0)
        self.assertIn("passes the cap", summary["aborted"])

    def test_cap_holds_when_early_calls_are_cheap(self):
        call = slow_call(0.4, seconds=0.02, cost_of=lambda job: 0.01 if job < 3 else 0.4)
        summary = run_jobs(list(range(20)), call, self.save, ledger_path=self.ledger, cap_usd=2.0, workers=4,
                           call_cap_usd=0.4)
        self.assertLessEqual(summary["spent_usd"], 2.0)
        self.assertLessEqual(sum(e["cost_usd"] for e in read_jsonl(self.ledger)), 2.0)

    def test_a_call_that_does_not_fit_beside_the_calls_in_flight_waits_for_them(self):
        call = slow_call(0.1)
        summary = run_jobs(list(range(6)), call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=4,
                           call_cap_usd=0.4)
        self.assertEqual((summary["calls"], summary["aborted"]), (6, None))
        self.assertAlmostEqual(summary["spent_usd"], 0.6)
        self.assertLessEqual(call.peak, 2)  # 0 + 3 x $0.40 passes $1.00, so a third call waited
        # with $0.60 booked a seventh call fits ($0.60 + $0.40); after it, an eighth could pass the cap and never starts
        summary = run_jobs([6, 7], call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=4,
                           call_cap_usd=0.4)
        self.assertEqual(summary["calls"], 1)
        self.assertIn("may cost up to $0.40, which passes the cap", summary["aborted"])

    def test_ledger_exists_before_the_first_call(self):
        seen = []

        def call(job):
            seen.append(self.ledger.exists())
            return fake_call(0.01)(job)
        run_jobs([1], call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=1)
        self.assertEqual(seen, [True])

    def test_a_job_that_raises_stops_the_run_and_frees_the_calls_waiting_for_room(self):
        started, errors = [], []

        def call(job):
            started.append(job)
            time.sleep(0.05)
            if job == 0:
                raise RuntimeError("adapter bug")
            return fake_call(0.4)(job)

        def go():
            try:
                run_jobs(list(range(6)), call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=4,
                         call_cap_usd=0.4)
            except RuntimeError as err:
                errors.append(err)
        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())  # jobs 2 and 3 waited for room under the cap and were released
        self.assertEqual([str(e) for e in errors], ["adapter bug"])
        self.assertEqual(sorted(started), [0, 1])  # no call starts after the exception

    def test_costs_from_an_earlier_run_count(self):
        run_jobs([1, 2, 3], fake_call(0.3), self.save, ledger_path=self.ledger, cap_usd=10.0, workers=1)
        summary = run_jobs(list(range(10)), fake_call(0.3), self.save, ledger_path=self.ledger, cap_usd=2.0, workers=1)
        self.assertEqual(summary["calls"], 0)

    def test_stops_at_once_on_a_low_credit_balance(self):
        def call(job):
            return {"is_error": True, "result": "Your credit balance is too low to access the Anthropic API."}
        summary = run_jobs(list(range(10)), call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=1)
        self.assertEqual(summary["calls"], 1)
        self.assertIn("low credit balance", summary["aborted"])

    def test_an_answer_from_another_model_is_a_failed_call_but_its_cost_is_booked(self):
        def call(job):
            return {"is_error": False, "result": "text", "modelUsage": {"claude-opus-5": {"outputTokens": 1_000_000}},
                    "usage": {"input_tokens": 0, "output_tokens": 1_000_000}}
        summary = run_jobs([1], call, self.save, ledger_path=self.ledger, cap_usd=100.0, workers=1,
                           expected_model=lambda job: "claude-opus-5-5")
        entry = read_jsonl(self.ledger)[0]
        self.assertTrue(entry["is_error"] and entry["model_mismatch"])
        self.assertAlmostEqual(entry["cost_usd"], 25.0)
        self.assertAlmostEqual(summary["spent_usd"], 25.0)

    def test_declined_requests_are_recorded_and_do_not_stop_the_run(self):
        def call(job):
            return {"is_error": True, "result": "API Error: Opus 5.5's safeguards flagged this session"}
        summary = run_jobs(list(range(5)), call, self.save, ledger_path=self.ledger, cap_usd=1.0, workers=1)
        self.assertEqual(summary["calls"], 5)
        self.assertIsNone(summary["aborted"])
        self.assertTrue(all(e["declined"] for e in read_jsonl(self.ledger)))

    def test_stops_after_three_failures_in_a_row(self):
        summary = run_jobs(list(range(10)), fake_call(0.0, is_error=True), self.save, ledger_path=self.ledger,
                           cap_usd=1.0, workers=1)
        self.assertEqual(summary["calls"], 3)
        self.assertIn("failed calls in a row", summary["aborted"])

    def test_ledger_tokens_come_from_model_usage(self):
        def call(job):
            return {"is_error": False, "result": "x", "num_turns": 4, "subtype": "success",
                    "modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": 15762, "outputTokens": 100,
                                                                  "webSearchRequests": 2}},
                    "usage": {"input_tokens": 5387, "output_tokens": 100}}
        run_jobs([1], call, self.save, ledger_path=self.ledger, cap_usd=10.0, workers=1)
        entry = read_jsonl(self.ledger)[0]
        self.assertEqual(entry["tokens_all"]["input"], 15762)
        self.assertEqual(entry["input_tokens"], 5387)
        self.assertEqual(entry["web_searches"], 2)
        self.assertEqual((entry["num_turns"], entry["subtype"]), (4, "success"))

    def test_usage_totals_falls_back_to_usage(self):
        self.assertEqual(usage_totals({"usage": {"input_tokens": 3, "output_tokens": 2}}),
                         {"input": 3, "output": 2, "cache_read": 0, "cache_creation": 0})

    def test_declared_aux_model_accepted_and_undeclared_is_mismatch(self):
        def call(job):
            return {"is_error": False, "result": "x",
                    "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 1}, "claude-haiku-4-5-20251001": {"inputTokens": 1}}}
        run_jobs([1], call, self.save, ledger_path=self.ledger, cap_usd=10.0, workers=1,
                 expected_model=lambda job: ("claude-sonnet-5-5", "claude-haiku-4-5-20251001"))
        run_jobs([2], call, self.save, ledger_path=self.ledger, cap_usd=10.0, workers=1,
                 expected_model=lambda job: "claude-sonnet-5-5")
        first, second = read_jsonl(self.ledger)
        self.assertFalse(first["model_mismatch"])
        self.assertTrue(second["model_mismatch"])

    def test_system_failures_do_not_stop_the_run(self):
        summary = run_jobs(list(range(6)), fake_call(0.0, is_error=True), self.save, ledger_path=self.ledger,
                           cap_usd=1.0, workers=1, outcome=lambda job, out: "system_failure")
        self.assertEqual(summary["calls"], 6)
        self.assertIsNone(summary["aborted"])

    def test_booked_cost_used_when_usage_is_missing(self):
        def call(job):
            return {"is_error": True, "result": "timed out", "timed_out": True,
                    "booked_cost": {"usd": 0.4, "source": "upper_bound"}}
        summary = run_jobs([1], call, self.save, ledger_path=self.ledger, cap_usd=10.0, workers=1,
                           outcome=lambda job, out: "system_failure")
        entry = read_jsonl(self.ledger)[0]
        self.assertEqual((entry["cost_usd"], entry["cost_source"], entry["timed_out"]), (0.4, "upper_bound", True))
        self.assertAlmostEqual(summary["spent_usd"], 0.4)

    def test_booked_cost_added_to_usage_of_the_other_steps(self):
        def call(job):
            return {"is_error": True, "result": "step 2 timed out",
                    "modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": 100_000}},
                    "booked_cost": {"usd": 0.2, "source": "upper_bound"}}
        run_jobs([1], call, self.save, ledger_path=self.ledger, cap_usd=10.0, workers=1,
                 outcome=lambda job, out: "system_failure")
        entry = read_jsonl(self.ledger)[0]
        self.assertAlmostEqual(entry["cost_usd"], 0.3)
        self.assertEqual(entry["cost_source"], "usage_plus_upper_bound")


if __name__ == "__main__":
    unittest.main()
