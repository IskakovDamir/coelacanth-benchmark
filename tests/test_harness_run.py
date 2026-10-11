"""Tests for the harness run module: the view boundary, preflight, statuses, resume and the output rows. No model,
network or paid API is called: the CLI and the adapters are fakes.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth import priorart
from coelacanth.data.openalex import BudgetExceeded
from coelacanth.harness import agents, config, run
from coelacanth.harness.budget import RunSpec

PROMPTS = "eval/results/measurement-a/prompts/agents"
SENTINELS = ["SENTINEL-SKELETON", "SENTINEL-FIELD", "SENTINEL-DOMAIN", "SENTINEL-SOURCE", "SENTINEL-COINED",
             "SENTINEL-CHART", "SENTINEL-CANDIDATE", "SENTINEL-EXCLUSION", "SENTINEL-EXPOSURE", "cf-9999"]


def sentinel_item():
    return {"id": "cf-9999", "claim": {"text": "a field-native claim", "date": "2001-02-03",
                                       "skeleton": "SENTINEL-SKELETON", "field": "SENTINEL-FIELD",
                                       "domain": "SENTINEL-DOMAIN", "coined_names": ["SENTINEL-COINED"],
                                       "chart": [{"id": "e1", "text": "SENTINEL-CHART", "key": True}]},
            "source_work": {"title": "SENTINEL-SOURCE"},
            "candidates": [{"cid": "c1", "work": {"title": "SENTINEL-CANDIDATE"}}],
            "exclusions": ["SENTINEL-EXCLUSION"], "exposure": {"kind": "SENTINEL-EXPOSURE"}}


def agent_cfg(**over):
    cfg = {"system": "single-agent", "adapter": "agent", "variant": "single", "model": "claude-sonnet-5-5",
           "effort": "high", "aux_models": [], "tools": ["WebSearch", "WebFetch"], "max_turns": 12, "search_cap": 3,
           "fetch_cap": 3, "per_item_cap_usd": 0.4, "timeout_s": 600, "top_k": 10, "conditions": ["text", "skeleton"],
           "allowed_subsets": ["H"], "models_called": ["claude-sonnet-5-5"], "line": "llm_baselines",
           "cli": {"version": "2.1.286 (Claude Code)", "stop_subtypes": {"capped": None, "turn_limit": None}},
           "prompts": {"system": {"source": "coelacanth.priorart.SINGLE_SYSTEM"},
                       "brief": {"path": f"{PROMPTS}/brief-single.txt"},
                       "contract": {"path": f"{PROMPTS}/contract.txt"}}}
    cfg.update(over)
    return cfg


def cli_out(result="```json\n[]\n```", is_error=False, cost=0.01, **extra):
    return {"is_error": is_error, "result": result, "total_cost_usd": cost, "num_turns": 2,
            "modelUsage": {"claude-sonnet-5-5": {"inputTokens": int(cost * 500_000)}}, **extra}


class RunTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_run(self, adapter, items, cfg=None, condition="text", cap=5.0, kind="pilot", **fields):
        spec = RunSpec(system="single-agent", kind=kind, subset="pilot" if kind == "pilot" else "H",
                       tier="T0" if kind == "pilot" else "T1", condition=condition, cap_usd=cap,
                       items=tuple(i["id"] for i in items))
        return run.Run(spec=spec, cfg=cfg or agent_cfg(), config_sha256=None, run_id="test-run",
                       run_dir=self.dir / "run", items={i["id"]: i for i in items}, adapter=adapter,
                       raw_dir=self.dir / "raw", **fields)

    def rows(self, r, name="outputs.jsonl"):
        return run._jsonl(r.run_dir / name)


class TestViewBoundary(RunTestCase):
    def test_sentinels_never_reach_any_prompt(self):
        seen = []

        def fake_cli(model, message, *, system, **kwargs):
            seen.extend([message, system, repr(kwargs)])
            return cli_out()
        r = self.make_run(agents.run_agent, [sentinel_item()])
        with mock.patch("coelacanth.harness.agents.llm.cli_call", side_effect=fake_cli):
            out = r.call((1, "cf-9999"))
        self.assertEqual(out["status"], "ok")
        joined = "\n".join(seen)
        self.assertIn("a field-native claim", joined)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, joined)

    def test_text_condition_never_reads_the_skeleton(self):
        self.assertEqual(run.view(sentinel_item(), "text"), {"text": "a field-native claim", "date": "2001-02-03"})
        self.assertEqual(run.view(sentinel_item(), "skeleton")["text"], "SENTINEL-SKELETON")

    def test_context_holds_no_item_field(self):
        captured = []
        r = self.make_run(lambda v, cfg, ctx: captured.append(ctx) or cli_out(), [sentinel_item()])
        r.call((1, "cf-9999"))
        text = repr(captured[0])
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, text)


class TestStatuses(unittest.TestCase):
    def test_budget_stop_is_capped_failure(self):
        out = {"is_error": True, "result": None, "total_cost_usd": 0.406934,
               "modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": 1}}}
        self.assertEqual(run.classify(out, agent_cfg()), "capped")

    def test_pinned_subtypes_win(self):
        cfg = agent_cfg(cli={"version": "x", "stop_subtypes": {"capped": "error_max_budget", "turn_limit": "error_max_turns"}})
        self.assertEqual(run.classify({"is_error": True, "subtype": "error_max_turns", "total_cost_usd": 0.1}, cfg),
                         "turn_limit")

    def test_timeout_low_credit_and_unparsed(self):
        cfg = agent_cfg()
        self.assertEqual(run.classify({"is_error": True, "timed_out": True}, cfg), "timeout")
        self.assertEqual(run.classify({"is_error": True, "result": "Your credit balance is too low"}, cfg), "infra_error")
        self.assertEqual(run.classify({"is_error": False, "result": "no json", "native": {"candidates": None}}, cfg),
                         "unparsed")
        self.assertEqual(run.classify({"is_error": False, "native": {"candidates": []}}, cfg), "ok")

    def test_budget_exhausted_is_capped(self):
        out = {"is_error": True, "result": None, "subtype": "item_budget_exhausted", "budget_exhausted": True}
        self.assertEqual(run.classify(out, agent_cfg()), "capped")


class TestRunFlow(RunTestCase):
    def test_decline_retried_once_identically_then_failure(self):
        calls = []

        def adapter(v, cfg, ctx):
            calls.append(dict(v))
            return cli_out(result="API Error: the safeguards flagged this request", is_error=True)
        out = self.make_run(adapter, [sentinel_item()]).call((1, "cf-9999"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(out["status"], "declined")
        self.assertEqual(out["attempts"], 2)

    def test_decline_then_answer_is_ok(self):
        answers = iter([cli_out(result="API Error: the safeguards flagged this request", is_error=True),
                        {**cli_out(), "native": {"candidates": []}}])
        out = self.make_run(lambda v, cfg, ctx: next(answers), [sentinel_item()]).call((1, "cf-9999"))
        self.assertEqual(out["status"], "ok")

    def test_failed_items_keep_rows_and_only_infra_errors_are_pending(self):
        items = [dict(sentinel_item(), id=f"cf-{n:04d}") for n in (1, 2, 3)]
        results = {"cf-0001": {**cli_out(result="text\n```json\n[{\"rank\": 1, \"title\": \"T\", \"why\": \"secret why\"}]\n```"),
                               "native": {"candidates": [{"rank": 1, "title": "T", "why": "secret why"}]}},
                   "cf-0002": cli_out(result=None, is_error=True, cost=0.39),
                   "cf-0003": {"is_error": True, "result": "connection reset"}}
        r = self.make_run(lambda v, cfg, ctx: results[next(i for i in ("cf-0001", "cf-0002", "cf-0003")
                                                          if ctx.task == f"job-{int(i[-1])}")],
                          items, cfg=agent_cfg(per_item_cap_usd=0.4))
        summary = run.execute(r, workers=1)
        rows = {row["item"]: row for row in run._jsonl(r.run_dir / "outputs.jsonl")}
        self.assertEqual(set(rows), {"cf-0001", "cf-0002", "cf-0003"})
        self.assertEqual(rows["cf-0001"]["status"], "ok")
        self.assertEqual(rows["cf-0002"]["status"], "capped")
        self.assertEqual(rows["cf-0003"]["status"], "infra_error")
        self.assertEqual(summary["by_status"], {"ok": 1, "capped": 1, "infra_error": 1})
        self.assertEqual(run.pending(r.run_dir, ["cf-0001", "cf-0002", "cf-0003"]), ["cf-0003"])
        text = (r.run_dir / "outputs.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret why", text)
        self.assertNotIn("text\n", text)
        self.assertTrue((r.raw_dir / "test-run" / "job-1.answer.txt").exists())

    def test_timeout_booked_at_cap(self):
        out = self.make_run(lambda v, cfg, ctx: {"is_error": True, "timed_out": True, "result": "timed out"},
                            [sentinel_item()]).call((1, "cf-9999"))
        self.assertEqual(out["booked_cost"], {"usd": 0.4, "source": "upper_bound"})
        self.assertEqual(out["status"], "timeout")

    def test_every_subcall_in_calls_jsonl(self):
        def adapter(v, cfg, ctx):
            ctx.log_call({"step": "a"})
            ctx.log_call({"step": "b"})
            return {**cli_out(), "native": {"candidates": []}}
        r = self.make_run(adapter, [sentinel_item()])
        run.execute(r, workers=1)
        calls = self.rows(r, "calls.jsonl")
        self.assertEqual([(c["run_id"], c["item"], c["step"]) for c in calls],
                         [("test-run", "cf-9999", "a"), ("test-run", "cf-9999", "b")])

    def test_model_mismatch_is_saved_pending_and_stops_the_run(self):
        items = [dict(sentinel_item(), id=f"cf-{n:04d}") for n in range(1, 7)]
        other = {**cli_out(), "modelUsage": {"claude-opus-5": {"inputTokens": 5000}}, "native": {"candidates": []}}
        r = self.make_run(lambda v, cfg, ctx: dict(other), items)
        summary = run.execute(r, workers=1)
        rows = self.rows(r)
        self.assertEqual([(row["status"], row["failure"]) for row in rows], [("model_mismatch", True)] * 3)
        self.assertEqual(summary["by_status"], {"model_mismatch": 3})
        self.assertIn("3 failed calls in a row", summary["aborted"])
        self.assertEqual(run.pending(r.run_dir, [i["id"] for i in items]), [i["id"] for i in items])

    def test_adapter_exception_is_an_infra_error_booked_from_its_sub_calls(self):
        items = [dict(sentinel_item(), id=f"cf-{n:04d}") for n in (1, 2)]

        def adapter(v, cfg, ctx):
            if ctx.task == "job-2":
                ctx.log_call({"step": "inc.rerank", "kind": "llm", "cost_usd": 0.05})
                ctx.log_call({"step": "openalex.search", "kind": "openalex", "cost_usd": 0.001})
                raise BudgetExceeded("the call would exceed the daily budget")
            return {**cli_out(), "native": {"candidates": []}}
        r = self.make_run(adapter, items)
        summary = run.execute(r, workers=1)
        rows = {row["item"]: row for row in self.rows(r)}
        self.assertEqual((rows["cf-0002"]["status"], rows["cf-0002"]["failure"]), ("infra_error", True))
        ledger = {row["item"]: row for row in self.rows(r, "ledger.jsonl")}
        self.assertEqual((ledger["cf-0002"]["cost_usd"], ledger["cf-0002"]["cost_source"]), (0.05, "logged_sub_calls"))
        self.assertEqual(rows["cf-0002"]["cost"]["usd"], 0.05)
        self.assertEqual((summary["by_status"], summary["pending"]), ({"ok": 1, "infra_error": 1}, 1))
        self.assertTrue((r.run_dir / "run.json").exists())
        self.assertEqual(run.pending(r.run_dir, ["cf-0001", "cf-0002"]), ["cf-0002"])

    def test_interrupted_run_still_writes_run_json(self):
        items = [dict(sentinel_item(), id=f"cf-{n:04d}") for n in (1, 2)]

        def adapter(v, cfg, ctx):
            if ctx.task == "job-2":
                raise KeyboardInterrupt
            return {**cli_out(), "native": {"candidates": []}}
        r = self.make_run(adapter, items)
        with self.assertRaises(KeyboardInterrupt):
            run.execute(r, workers=1)
        manifest = json.loads((r.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["summary"]["aborted"].startswith("interrupted"))
        self.assertEqual(manifest["summary"]["by_status"], {"ok": 1})
        spent = sum(row["cost_usd"] for row in self.rows(r, "ledger.jsonl"))
        self.assertEqual(manifest["summary"]["spent_usd"], round(spent, 4))

    def test_decline_then_timeout_books_the_rest_of_the_item_cap(self):
        answers = iter([cli_out(result="API Error: the safeguards flagged this request", is_error=True, cost=0.05),
                        {"is_error": True, "timed_out": True, "result": "timed out after 600 s"}])
        r = self.make_run(lambda v, cfg, ctx: next(answers), [sentinel_item()])
        run.execute(r, workers=1)
        row, = self.rows(r)
        ledger, = self.rows(r, "ledger.jsonl")
        self.assertEqual((row["status"], row["attempts"]), ("timeout", 2))
        self.assertAlmostEqual(ledger["cost_usd"], 0.4)  # $0.05 of usage plus the $0.35 left as the retry's cap
        self.assertEqual(ledger["cost_source"], "usage_plus_upper_bound")

    def test_decline_retry_gets_what_is_left_of_the_item_cap(self):
        answers = iter([cli_out(result="API Error: the safeguards flagged this request", is_error=True, cost=0.38),
                        cli_out()])
        r = self.make_run(agents.run_agent, [sentinel_item()])
        with mock.patch("coelacanth.harness.agents.llm.cli_call", side_effect=lambda *a, **k: next(answers)) as cli:
            out = r.call((1, "cf-9999"))
        first, second = cli.call_args_list
        self.assertEqual(first.args, second.args)  # the identical input
        self.assertEqual(first.kwargs["max_budget_usd"], 0.4)
        self.assertAlmostEqual(second.kwargs["max_budget_usd"], 0.02)
        self.assertEqual((out["status"], out["attempts"]), ("ok", 2))

    def test_decline_with_nothing_left_of_the_cap_is_capped_without_retry(self):
        calls = []

        def adapter(v, cfg, ctx):
            calls.append(ctx.cap_usd)
            return cli_out(result="API Error: the safeguards flagged this request", is_error=True, cost=0.41)
        out = self.make_run(adapter, [sentinel_item()]).call((1, "cf-9999"))
        self.assertEqual(calls, [0.4])
        self.assertEqual(out["status"], "capped")

    def test_outputs_hold_search_count_cost_and_run_records(self):
        splits = self.dir / "splits-v1.json"
        splits.write_text("{}", encoding="utf-8")
        query = "MODEL-WRITTEN QUERY quoting a field-native claim"
        native = {"candidates": [], "searches": [query, "another query"], "fetches": 1, "llm_calls": 1,
                  "openalex": {"search": 0, "list": 0, "entity": 0}}
        r = self.make_run(lambda v, cfg, ctx: {**cli_out(cost=0.02), "native": dict(native)}, [sentinel_item()],
                          kind="scored", splits_path=splits)
        run.execute(r, workers=1)
        self.assertNotIn(query, (r.run_dir / "outputs.jsonl").read_text(encoding="utf-8"))
        row, = self.rows(r)
        ledger, = self.rows(r, "ledger.jsonl")
        self.assertEqual(row["native"]["searches"], 2)
        self.assertEqual((row["cost"]["usd"], row["cost"]["cost_source"]),
                         (ledger["cost_usd"], ledger["cost_source"]))
        self.assertEqual(row["cost"]["by_model"], {"claude-sonnet-5-5": {"usd": 0.02, "web_searches": 0}})
        self.assertEqual((row["cost"]["llm_calls"], row["cost"]["openalex_nominal"]), (1, native["openalex"]))
        manifest = json.loads((r.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["splits_sha256"], hashlib.sha256(splits.read_bytes()).hexdigest())
        self.assertRegex(manifest["git_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(manifest["started"] <= manifest["finished"])

    def test_resume_runs_only_pending_items_and_keeps_the_start(self):
        items = [dict(sentinel_item(), id=f"cf-{n:04d}") for n in (1, 2, 3)]
        first = {"job-1": {**cli_out(), "native": {"candidates": []}}, "job-2": cli_out(result=None, is_error=True),
                 "job-3": {"is_error": True, "result": "connection reset"}}
        run.execute(self.make_run(lambda v, cfg, ctx: first[ctx.task], items), workers=1)
        started = json.loads((self.dir / "run" / "run.json").read_text(encoding="utf-8"))["started"]
        again = []
        r = self.make_run(lambda v, cfg, ctx: again.append(ctx.task) or {**cli_out(), "native": {"candidates": []}},
                          items)
        summary = run.execute(r, workers=1)
        self.assertEqual(again, ["job-3"])
        self.assertEqual(summary["by_status"], {"ok": 2, "error": 1})
        manifest = json.loads((r.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["started"], len(manifest["resumed"])), (started, 1))


class TestPreflight(RunTestCase):
    def test_scored_run_refused_without_splits(self):
        spec = RunSpec(system="single-agent", kind="scored", subset="H", tier="T1", condition="text", cap_usd=5,
                       items=("cf-0001",))
        problems = run.preflight(spec, agent_cfg(), root=self.dir, splits_path=self.dir / "none.json",
                                 items_path=self.dir / "items.jsonl", cli_version=lambda: "2.1.286 (Claude Code)",
                                 auth=lambda: None)
        self.assertTrue(any("no splits file" in p for p in problems))

    def test_pilot_refuses_non_guide_item(self):
        (self.dir / "pilot-items.jsonl").write_text(json.dumps({"id": "guide-X", "status": "labelled",
                                                                "provenance": {"source": "kulikowski"}}) + "\n")
        spec = RunSpec(system="single-agent", kind="pilot", subset="pilot", tier="T0", condition="text", cap_usd=1,
                       items=("guide-X", "guide-Y"))
        problems = run.preflight(spec, agent_cfg(), root=self.dir, items_path=self.dir / "items.jsonl",
                                 auth=lambda: None)
        self.assertTrue(any("not an excluded guide example" in p for p in problems))
        self.assertTrue(any("guide-Y is not in pilot-items" in p for p in problems))

    def test_items_hash_mismatch_and_subset_refused(self):
        splits = self.dir / "splits" / "splits-v1.json"
        splits.parent.mkdir()
        (self.dir / "items.jsonl").write_text('{"id": "cf-0001"}\n')
        splits.write_text(json.dumps({"items_sha256": "0" * 64, "refpipe_readiness": "ready",
                                      "models_for_H": ["claude-opus-5-5"], "subsets": {"H": ["cf-0001"]}}))
        spec = RunSpec(system="single-agent", kind="scored", subset="P40", tier="T1", condition="text", cap_usd=5,
                       items=("cf-0001",))
        problems = run.preflight(spec, agent_cfg(), root=self.dir, splits_path=splits,
                                 items_path=self.dir / "items.jsonl", cli_version=lambda: "2.1.286 (Claude Code)",
                                 auth=lambda: None)
        self.assertTrue(any("SHA256SUMS" in p for p in problems))
        self.assertTrue(any("records no views_sha256" in p for p in problems))
        self.assertTrue(any("not allowed" in p for p in problems))
        self.assertTrue(any("models that define H" in p for p in problems))

    def scored(self, splits_extra=None, cfg=None, item=None, cli="2.1.286 (Claude Code)", **spec_fields):
        """Preflight of a scored run on cf-0001 against a frozen splits file that passes SHA256SUMS."""
        item = item or {"id": "cf-0001", "claim": {"text": "a claim", "skeleton": "a skeleton", "date": "2001-02-03"}}
        (self.dir / "items.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
        frozen = {"id": "cf-0001", "claim": {"text": "a claim", "skeleton": "a skeleton", "date": "2001-02-03"}}
        splits = {"items_sha256": "0" * 64, "views_sha256": {"cf-0001": run.frozen_view_sha256(frozen)},
                  "refpipe_readiness": "ready", "models_for_H": ["claude-sonnet-5-5"], "subsets": {"H": ["cf-0001"]}}
        splits.update(splits_extra or {})
        splits = {k: v for k, v in splits.items() if v is not None}
        path = self.dir / "splits" / "splits-v1.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(splits), encoding="utf-8")
        (path.parent / "SHA256SUMS").write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  splits-v1.json\n")
        spec = RunSpec(**{"system": "single-agent", "kind": "scored", "subset": "H", "tier": "T1",
                          "condition": "text", "cap_usd": 5, "items": ("cf-0001",), **spec_fields})
        return run.preflight(spec, cfg or agent_cfg(), root=self.dir, splits_path=path,
                             items_path=self.dir / "items.jsonl", cli_version=lambda: cli, auth=lambda: None)

    def test_merged_judgments_keep_the_views_frozen(self):
        judged = {"id": "cf-0001", "claim": {"text": "a claim", "skeleton": "a skeleton", "date": "2001-02-03"},
                  "candidates": [{"cid": "c9", "found_by": ["system:single-agent:text:1"], "relation": "partial"}]}
        problems = self.scored(item=judged)
        self.assertFalse([p for p in problems if "view" in p or "items_sha256" in p or "SHA256SUMS" in p], problems)

    def test_changed_or_unfrozen_view_refused(self):
        changed = {"id": "cf-0001",
                   "claim": {"text": "a reworded claim", "skeleton": "a skeleton", "date": "2001-02-03"}}
        self.assertTrue(any("differs from the frozen views_sha256" in p for p in self.scored(item=changed)))
        self.assertTrue(any("no frozen view" in p for p in self.scored(splits_extra={"views_sha256": {}})))

    def test_cli_version_mismatch_refused(self):
        self.assertTrue(any("CLI version differs" in p for p in self.scored(cli="2.1.300 (Claude Code)")))
        self.assertFalse(any("CLI version differs" in p for p in self.scored()))

    def test_scored_run_refused_without_readiness(self):
        self.assertTrue(any("reference pipeline readiness" in p
                            for p in self.scored(splits_extra={"refpipe_readiness": None})))
        matched = agent_cfg(system="single-agent-matched", line="refpipe")
        not_ready = {"refpipe_readiness": "not_ready"}
        self.assertTrue(any("pipeline is not ready" in p
                            for p in self.scored(splits_extra=not_ready, cfg=matched, system="single-agent-matched")))
        self.assertFalse(any("ready" in p for p in self.scored(splits_extra=not_ready)))

    def test_league_member_changed_since_its_hash_was_logged_refused(self):
        configs = self.dir / "configs"
        configs.mkdir()

        def write(system, cap):
            cfg = agent_cfg(system=system, league="claude-code-agents", per_item_cap_usd=cap)
            (configs / f"{system}.json").write_bytes(config.canonical_bytes(cfg))
            return cfg, hashlib.sha256(config.canonical_bytes(cfg)).hexdigest()
        _, hunch_then = write("hunch", 0.4)
        logged = {"system": "hunch", "config_sha256": hunch_then}
        (self.dir / "config-hashes.jsonl").write_text(json.dumps(logged) + "\n")
        write("hunch", 0.6)
        single, _ = write("single-agent", 0.6)
        problems = self.scored(cfg=single)
        self.assertTrue(any("league member hunch changed" in p for p in problems), problems)
        write("hunch", 0.4)
        problems = self.scored(cfg=single)
        self.assertFalse(any("league member hunch changed" in p for p in problems))
        self.assertTrue(any("per_item_cap_usd differs" in p for p in problems), problems)

    def test_pilot_sharing_a_source_work_with_a_benchmark_item_refused(self):
        guide = {"status": "excluded", "provenance": {"source": "guide"}}
        pilots = [{"id": "guide-Z", **guide, "claim": {"source_work": {"openalex": "W2139996507",
                                                                       "doi": "10.1073/pnas.95.7.3597"}}},
                  {"id": "guide-X", **guide, "claim": {"source_work": {"doi": "10.2337/diacare.17.2.152"}},
                   "exclusions": ["https://openalex.org/W2139996507"]},
                  {"id": "guide-Q", **guide, "claim": {"source_work": {"doi": "10.1000/other"}}}]
        (self.dir / "pilot-items.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pilots))
        batches = self.dir / "batches"
        batches.mkdir()
        (batches / "b01.json").write_text(json.dumps({"items": [{"id": "cf-0001", "claim": {"source_work": {
            "openalex": "https://openalex.org/W2139996507", "doi": "https://doi.org/10.1073/PNAS.95.7.3597"}}}]}))
        (batches / "b01-meta.json").write_text(json.dumps({"items": [{"id": "guide-Q", "claim": {"source_work": {
            "doi": "10.1000/other"}}}]}))
        spec = RunSpec(system="single-agent", kind="pilot", subset="pilot", tier="T0", condition="text", cap_usd=1,
                       items=("guide-Z", "guide-X", "guide-Q"))
        problems = run.preflight(spec, agent_cfg(), root=self.dir, items_path=self.dir / "items.jsonl",
                                 batches_dir=batches, auth=lambda: None)
        shared = [p for p in problems if "shares its source work" in p]
        self.assertEqual([p.split()[2] for p in shared], ["guide-Z", "guide-X"])


if __name__ == "__main__":
    unittest.main()
