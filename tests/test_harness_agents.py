"""Tests for the agent league's briefs, call and trace parsing (docs/design/harness.md section 9). No model is
called: `cli_call` is patched, and the Hunch and prior-art-skill system prompts, which read a local git repository
and a cached file, are patched too.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coelacanth import llm, priorart
from coelacanth.harness import agents

PROMPTS = "eval/results/measurement-a/prompts/agents"
VARIANTS = {"single": ("brief-single.txt", "coelacanth.priorart.SINGLE_SYSTEM"),
            "prior_art_skill": ("brief-prior-art-skill.txt", "coelacanth.priorart.prior_art_system"),
            "hunch": ("brief-hunch.txt", "coelacanth.priorart.hunch_system")}
VIEW = {"text": "Estimate the coefficients by least squares with a constant added to the diagonal; it costs $5.",
        "date": "1970-02-01"}
WIKI = "https://en.wikipedia.org/wiki/Tikhonov_regularization"
ANSWER = ('Report.\n```json\n[{"rank": 1, "title": "Solution of incorrectly formulated problems", "authors": "T", '
          '"year": 1963, "venue": null, "doi": null, "url": null, "field": "Mathematics", '
          '"relation": "specialization", "why": "Same penalty."}]\n```')
FORBIDDEN = ("cross-field", "cross field", "benchmark", "rediscover", "ai system", "ai-origin", "ai origin",
             "generated", "artificial", "proposed as new", "any field", "other field", "under any name",
             "vocabular", "skeleton", "method", "field:")


def config(variant: str) -> dict:
    brief_file, source = VARIANTS[variant]
    return {"system": variant, "adapter": "agent", "variant": variant, "model": "claude-sonnet-5-5", "effort": "high",
            "tools": ["WebSearch", "WebFetch"], "max_turns": 20, "search_cap": 3, "fetch_cap": 4,
            "per_item_cap_usd": 0.4, "timeout_s": 1200, "top_k": 10,
            "prompts": {"system": {"source": source, "sha256": "<hex>"},
                        "brief": {"path": f"{PROMPTS}/{brief_file}", "sha256": "<hex>"},
                        "contract": {"path": f"{PROMPTS}/contract.txt", "sha256": "<hex>"}}}


def header(variant: str) -> str:
    return (agents.REPO_ROOT / PROMPTS / VARIANTS[variant][0]).read_text(encoding="utf-8").rstrip("\n")


def stream(answer: str = ANSWER, with_result: bool = True) -> str:
    """A synthetic stream-json trace: two WebSearch calls (one emitted twice), a WebFetch of an encyclopedia page."""
    def use(block_id, name, **args):
        return {"type": "tool_use", "id": block_id, "name": name, "input": args}

    def assistant(*blocks):
        return {"type": "assistant", "message": {"content": list(blocks)}}

    events = [{"type": "system", "subtype": "init", "tools": ["WebSearch", "WebFetch"]},
              assistant({"type": "text", "text": "Searching."}, use("toolu_1", "WebSearch", query="diagonal constant")),
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                        "content": "results"}]}},
              assistant(use("toolu_2", "WebSearch", query="ill-posed penalty")),
              assistant(use("toolu_2", "WebSearch", query="ill-posed penalty")),
              assistant(use("toolu_3", "WebFetch", url=WIKI, prompt="When was it published?"))]
    if with_result:
        events.append({"type": "result", "subtype": "success", "is_error": False, "result": answer, "num_turns": 5,
                       "total_cost_usd": 0.2, "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 100,
                                                                                   "webSearchRequests": 2}},
                       "usage": {"input_tokens": 50}})
    return "noise line\n" + "\n".join(json.dumps(e) for e in events) + "\n"


def ctx(tmp: str) -> SimpleNamespace:
    return SimpleNamespace(task="job-1", cwd=tmp, cap_usd=0.4, search=None, log_call=lambda row: None,
                           trace_path=Path(tmp) / "runs" / "job-1.jsonl")


def fake_cli(trace_text: str, timed_out: bool = False):
    """Stands in for llm.cli_call: writes the trace where it was asked to and returns what cli_call would."""
    def call(model, message, *, system, trace=None, **kwargs):
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(trace_text, encoding="utf-8")
        if timed_out:
            return {"is_error": True, "result": "timed out after 1200 s", "returncode": None, "stderr": None,
                    "timed_out": True, "wall_seconds": 1200.0}
        return {**llm.last_result_event(trace_text), "returncode": 0, "stderr": None, "wall_seconds": 74.2}
    return call


class TestBrief(unittest.TestCase):
    def test_briefs_differ_only_in_header_and_carry_no_cue(self):
        briefs = {v: agents.brief(config(v), VIEW) for v in VARIANTS}
        rests = set()
        for variant, text in briefs.items():
            self.assertTrue(text.startswith(header(variant) + "\n\n"))
            rests.add(text[len(header(variant)):])
        self.assertEqual(len(rests), 1)
        rest = rests.pop()
        self.assertTrue(rest.startswith(f"\n\nDate: 1970-02-01\n\nClaim: {VIEW['text']}\n\nSearch budget: at most 3 "
                                        "web searches and at most 4 fetched pages"))
        self.assertIn("published before 1970-02-01 count", rest)
        self.assertIn("at most 10 earlier works", rest)
        self.assertNotIn("$", rest.replace("$5", ""))
        for variant, text in briefs.items():
            scaffold = text.replace(VIEW["text"], "").lower()
            for word in FORBIDDEN:
                self.assertNotIn(word, scaffold, f"{word!r} in the {variant} brief")

    def test_hunch_header_keeps_its_headings_and_steps_1_to_3(self):
        text = header("hunch")
        for heading in ("Goal:", "Study:", "Acceptance:", "Ownership:"):
            self.assertIn(heading, text)
        self.assertIn("prior-art check of one claim", text)
        self.assertIn("this brief is the whole context", text)
        self.assertIn("steps 1 to 3 only", text)

    def test_contract_is_the_priorart_contract_reworded_to_claim(self):
        expected = priorart.CONTRACT.replace("the method", "the claim")
        for name in ("searches", "fetches", "date", "k"):
            expected = expected.replace("{" + name + "}", "$" + name)
        contract = (agents.REPO_ROOT / PROMPTS / "contract.txt").read_text(encoding="utf-8")
        self.assertEqual(contract, expected + "\n")
        self.assertNotIn("method", contract)

    def test_view_boundary(self):
        cfg = config("single")
        for extra in ("skeleton", "id", "exclusions", "field"):
            with self.assertRaises(ValueError):
                agents.brief(cfg, {**VIEW, extra: "SENTINEL"})
        with self.assertRaises(ValueError):
            agents.brief(cfg, {"text": VIEW["text"]})

    def test_claim_text_is_carried_verbatim(self):
        text = agents.brief(config("single"), {"text": "Cost $date and ${k} units.", "date": "2001-01-01"})
        self.assertIn("Claim: Cost $date and ${k} units.", text)


class TestRunAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = ctx(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_variant(self, variant, trace_text=None, timed_out=False):
        cli = mock.Mock(side_effect=fake_cli(trace_text or stream(), timed_out))
        with mock.patch("coelacanth.harness.agents.llm.cli_call", cli), \
                mock.patch("coelacanth.priorart.hunch_system", return_value="HUNCH SYSTEM"), \
                mock.patch("coelacanth.priorart.prior_art_system", return_value="SKILL SYSTEM"):
            out = agents.run_agent(VIEW, config(variant), self.ctx)
        return out, cli

    def test_cli_call_arguments(self):
        cfg = config("single")
        out, cli = self.run_variant("single")
        cli.assert_called_once_with("claude-sonnet-5-5", agents.brief(cfg, VIEW), system=priorart.SINGLE_SYSTEM,
                                    effort="high", cwd=self.tmp.name, tools=("WebSearch", "WebFetch"),
                                    max_budget_usd=0.4, max_turns=20, timeout=1200, trace=self.ctx.trace_path)
        self.assertFalse(out["is_error"])

    def test_each_variant_gets_its_pinned_system_prompt(self):
        for variant, system in (("hunch", "HUNCH SYSTEM"), ("prior_art_skill", "SKILL SYSTEM")):
            _, cli = self.run_variant(variant)
            self.assertEqual(cli.call_args.kwargs["system"], system)
            self.assertTrue(cli.call_args.args[1].startswith(header(variant)))

    def test_config_naming_another_system_prompt_is_refused(self):
        cfg = config("hunch")
        cfg["prompts"]["system"]["source"] = "coelacanth.priorart.SINGLE_SYSTEM"
        with mock.patch("coelacanth.harness.agents.llm.cli_call") as cli, self.assertRaises(ValueError):
            agents.run_agent(VIEW, cfg, self.ctx)
        cli.assert_not_called()

    def test_no_trace_is_refused(self):
        with self.assertRaises(ValueError):
            agents.run_agent(VIEW, config("single"), SimpleNamespace(**{**vars(self.ctx), "trace_path": None}))

    def test_native_output(self):
        out, _ = self.run_variant("single")
        native = out["native"]
        self.assertEqual(native["candidates"], [
            {"rank": 1, "title": "Solution of incorrectly formulated problems", "authors": "T", "year": 1963,
             "venue": None, "doi": None, "url": None, "openalex": None, "publication_date": None,
             "field": "Mathematics", "relation": "specialization", "score": None}])
        self.assertEqual(native["evidence"],
                         [{"kind": "fetched_page", "url": WIKI, "openalex": None, "for_rank": None}])
        self.assertEqual(native["searches"], ["diagonal constant", "ill-posed penalty"])
        self.assertEqual((native["fetches"], native["llm_calls"], native["label"]), (1, 1, None))
        self.assertNotIn("Same penalty.", json.dumps(native))
        self.assertEqual((out["subtype"], out["num_turns"], out["timed_out"], out["booked_cost"]),
                         ("success", 5, False, None))
        self.assertEqual(out["modelUsage"]["claude-sonnet-5-5"]["webSearchRequests"], 2)

    def test_answer_without_json_has_no_candidates_and_empty_list_is_kept(self):
        out, _ = self.run_variant("single", stream(answer="I could not finish."))
        self.assertIsNone(out["native"]["candidates"])
        out, _ = self.run_variant("single", stream(answer="Nothing.\n```json\n[]\n```"))
        self.assertEqual(out["native"]["candidates"], [])

    def test_timed_out_call_keeps_its_evidence(self):
        out, _ = self.run_variant("single", stream(with_result=False), timed_out=True)
        self.assertTrue(out["timed_out"] and out["is_error"])
        self.assertIsNone(out["native"]["candidates"])
        self.assertEqual([e["url"] for e in out["native"]["evidence"]], [WIKI])


class TestTraceEvents(unittest.TestCase):
    def test_searches_fetches_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text(stream(), encoding="utf-8")
            events = agents.trace_events(path)
        self.assertEqual(events["searches"], ["diagonal constant", "ill-posed penalty"])
        self.assertEqual(events["fetched_urls"], [WIKI])
        self.assertEqual((events["n_searches"], events["n_fetches"]), (2, 1))
        self.assertEqual((events["result"]["subtype"], events["result"]["num_turns"]), ("success", 5))

    def test_missing_trace_is_empty(self):
        events = agents.trace_events(Path("/nonexistent/trace.jsonl"))
        self.assertEqual((events["searches"], events["fetched_urls"], events["result"]), ([], [], None))


if __name__ == "__main__":
    unittest.main()
