"""Tests for measurement A's budget rules: the $20 approval, line remainders (runs in flight held at their cap, spend
outside the harness subtracted), tier order, plan rows for every scored run and the pilot projection (mean x 1.5 x
items, from ledgers and statuses only, infrastructure errors left out). Runs are written to a temporary directory;
the last tests read the committed budget.json and the ledgers it names.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth.env import REPO_ROOT
from coelacanth.harness import budget
from coelacanth.harness.budget import RunSpec
from coelacanth.llm import read_jsonl

CFG = {"system": "single-agent", "line": "llm_baselines"}
SENTINEL = "SENTINEL-CANDIDATE-7f3a"


def budget_file(**changes):
    out = {"format": "coelacanth-budget/v1", "price_date": "2026-10-06",
           "lines": {"pilots": 40, "refpipe": 150, "theorizer": 200, "owl": 60, "llm_baselines": 30, "reserve": 20},
           "systems": {"single-agent": "llm_baselines", "inc-style": "llm_baselines", "refpipe-v0": "refpipe"},
           "plan": [{"tier": "T1", "system": "single-agent", "subset": "H", "condition": "text", "items": 25},
                    {"tier": "T1", "system": "inc-style", "subset": "H", "condition": "text", "items": 25},
                    {"tier": "T2", "system": "single-agent", "subset": "ALL_minus_H", "condition": "text",
                     "items": 50},
                    {"tier": "T3", "system": "inc-style", "subset": "H", "condition": "skeleton", "items": 25}],
           "approvals": []}
    out.update(changes)
    return out


def spec(**changes):
    fields = {"system": "single-agent", "kind": "scored", "subset": "H", "tier": "T1", "condition": "text",
              "cap_usd": 10.0, "items": ("cf-0001",)}
    fields.update(changes)
    return RunSpec(**fields)


class RunDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_run(self, rel, run, ledger=(), outputs=()):
        d = self.root / rel
        d.mkdir(parents=True)
        if run is not None:
            (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
        if ledger is not None:
            (d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ledger), encoding="utf-8")
        (d / "outputs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in outputs), encoding="utf-8")
        return d

    def scored_run(self, system, condition, tier, subset, *, run_id="r1", spent=0.0, finished=True, aborted=None,
                   line="llm_baselines", pending=0):
        run = {"format": "coelacanth-run/v1", "system": system, "condition": condition, "tier": tier,
               "subset": subset, "kind": "scored", "line": line,
               "finished": "2026-10-20T10:00:00+00:00" if finished else None,
               "summary": {"aborted": aborted, "pending": pending}}
        return self.write_run(f"runs/{system}/{condition}/{run_id}", run, [{"item": "cf-0001", "cost_usd": spent}])

    def check(self, run_spec, b=None, cfg=CFG):
        return budget.check(run_spec, cfg, b or budget_file(), root=self.root)


class TestApproval(RunDir):
    def test_run_over_20_needs_approval(self):
        b = budget_file(lines={"llm_baselines": 100, "pilots": 40})
        self.assertEqual(self.check(spec(cap_usd=20.0), b), [])
        self.assertTrue(any("run_over_20" in p for p in self.check(spec(cap_usd=25.0), b)))
        approval = {"at": "2026-10-18T09:00:00+00:00", "kind": "run_over_20", "scope": "single-agent:T1:H:text",
                    "usd": 25.0, "from_line": None, "to_line": None, "damir_words": "ok", "recorded_by": "claude"}
        self.assertEqual(self.check(spec(cap_usd=25.0), {**b, "approvals": [approval]}), [])
        for wrong in ({"usd": 24.0}, {"scope": "single-agent:T1:H:skeleton"}, {"kind": "line_move"}):
            problems = self.check(spec(cap_usd=25.0), {**b, "approvals": [{**approval, **wrong}]})
            self.assertTrue(any("run_over_20" in p for p in problems), wrong)


class TestLines(RunDir):
    def test_line_remainder_enforced(self):
        self.scored_run("inc-style", "text", "T1", "H", spent=25.0)
        self.scored_run("refpipe-v0", "text", "T1", "H", spent=100.0, line="refpipe")
        self.assertEqual(budget.line_spent("llm_baselines", budget.ledger_paths(self.root)), 25.0)
        self.assertEqual(self.check(spec(cap_usd=5.0)), [])
        problems = self.check(spec(cap_usd=6.0))
        self.assertEqual(len(problems), 1)
        self.assertIn("has left: $5.00 of $30", problems[0])

    def test_pilots_book_to_the_pilots_line(self):
        self.write_run("pilots/single-agent/p1", {"system": "single-agent", "kind": "pilot", "line": "llm_baselines"},
                       [{"item": "guide-S1", "cost_usd": 38.5}])
        self.assertEqual(budget.line_spent("pilots", budget.ledger_paths(self.root)), 38.5)
        self.assertEqual(budget.line_spent("llm_baselines", budget.ledger_paths(self.root)), 0.0)
        problems = self.check(spec(kind="pilot", subset="pilot", tier="pilot", cap_usd=2.0))
        self.assertEqual(len(problems), 1)
        self.assertIn("line pilots", problems[0])
        self.assertEqual(self.check(spec(kind="pilot", subset="pilot", tier="pilot", cap_usd=1.5)), [])

    def test_ledger_without_run_json_is_refused(self):
        self.write_run("runs/single-agent/text/r1", None, [{"item": "cf-0001", "cost_usd": 1.0}])
        problems = self.check(spec(cap_usd=1.0))
        self.assertTrue(any("run.json is missing" in p for p in problems))

    def test_run_in_flight_holds_its_cap(self):
        # run 1 has started (run.json written without `finished`) and booked nothing yet; before, it was invisible
        # and a second $14 run passed on a $20 line
        b = budget_file(lines={"llm_baselines": 20, "pilots": 40})
        stub = {"format": "coelacanth-run/v1", "system": "inc-style", "condition": "text", "tier": "T1", "subset": "H",
                "kind": "scored", "line": "llm_baselines", "cap_usd": 14.0}
        d = self.write_run("runs/inc-style/text/r1", stub, ledger=None)
        self.assertEqual(budget.line_spent("llm_baselines", budget.ledger_paths(self.root)), 14.0)
        problems = self.check(spec(cap_usd=14.0), b)
        self.assertEqual(len(problems), 1)
        self.assertIn("has left: $6.00 of $20", problems[0])
        (d / "ledger.jsonl").write_text(json.dumps({"item": "cf-0001", "cost_usd": 15.5}) + "\n", encoding="utf-8")
        self.assertEqual(budget.line_spent("llm_baselines", budget.ledger_paths(self.root)), 15.5)  # spent above cap
        (d / "run.json").write_text(json.dumps({**stub, "finished": "2026-10-20T10:00:00+00:00"}), encoding="utf-8")
        (d / "ledger.jsonl").write_text(json.dumps({"item": "cf-0001", "cost_usd": 3.0}) + "\n", encoding="utf-8")
        self.assertEqual(self.check(spec(cap_usd=14.0), b), [])  # finished: its real spend, $17 left

    def test_spend_outside_the_harness_is_subtracted(self):
        b = budget_file(spent_outside_harness={"llm_baselines": {"usd": 20.0, "sources": ["x/ledger.jsonl"]},
                                               "reserve": {"usd": 19.0, "sources": []}})
        self.assertEqual(self.check(spec(cap_usd=10.0), b), [])
        problems = self.check(spec(cap_usd=10.5), b)
        self.assertEqual(len(problems), 1)
        self.assertIn("has left: $10.00 of $30", problems[0])
        pilot = spec(kind="pilot", subset="pilot", tier="T0", cap_usd=27.2)
        b["spent_outside_harness"]["pilots"] = {"usd": 12.87, "sources": []}
        self.assertTrue(any("has left: $27.13 of $40" in p for p in self.check(pilot, b)))

    def test_config_and_budget_must_name_the_same_line(self):
        problems = self.check(spec(), cfg={"system": "single-agent", "line": "reserve"})
        self.assertTrue(any("budget.json books single-agent to line 'llm_baselines'" in p for p in problems))


class TestTiers(RunDir):
    def test_next_tier_refused_before_current_complete(self):
        t2 = spec(subset="ALL_minus_H", tier="T2", cap_usd=5.0)
        self.assertEqual(len(self.check(t2)), 2)
        self.scored_run("single-agent", "text", "T1", "H")
        self.scored_run("inc-style", "text", "T1", "H", aborted="projected $31.00 passes the cap $30.00")
        problems = self.check(t2)
        self.assertEqual(len(problems), 1)
        self.assertIn("inc-style:H:text", problems[0])
        self.scored_run("inc-style", "text", "T1", "H", run_id="r2", finished=False)
        self.assertEqual(len(self.check(t2)), 1)
        self.scored_run("inc-style", "text", "T1", "H", run_id="r3", pending=2)  # infrastructure errors left
        self.assertEqual(len(self.check(t2)), 1)
        self.scored_run("inc-style", "text", "T1", "H", run_id="r4")
        self.assertEqual(self.check(t2), [])

    def test_every_scored_run_needs_its_exact_plan_row(self):
        # before, only skeleton runs were matched against the plan
        problems = self.check(spec(system="inc-style", subset="ALL_minus_H", tier="T2"))
        self.assertTrue(any("no plan row funds the text condition for inc-style in T2 on ALL_minus_H" in p
                            for p in problems), problems)
        problems = self.check(spec(subset="H_half"))  # the plan funds single-agent on H in T1
        self.assertEqual(problems, ["no plan row funds the text condition for single-agent in T1 on H_half"])
        self.assertEqual(self.check(spec()), [])

    def test_plan_rows_of_a_tier_share_one_nested_subset(self):
        b = budget_file()
        b["plan"][1] = {**b["plan"][1], "subset": "H_half"}  # inc-style on H_half beside single-agent on H in T1
        problems = self.check(spec(), b)
        self.assertEqual(len(problems), 1)
        self.assertIn("T1 rows mix ['H', 'H_half']", problems[0])
        inc = spec(system="inc-style", subset="H_half")
        self.assertIn("T1 rows mix ['H', 'H_half']", self.check(inc, b, cfg={"system": "inc-style",
                                                                            "line": "llm_baselines"})[0])
        b["plan"][0] = {**b["plan"][0], "subset": "H_half"}
        self.assertEqual(self.check(spec(subset="H_half"), b), [])

    def test_skeleton_run_refused_without_plan_row(self):
        problems = self.check(spec(condition="skeleton", tier="T1"))
        self.assertTrue(any("skeleton condition for single-agent in T1" in p for p in problems))
        b = budget_file()
        b["plan"].append({"tier": "T1", "system": "single-agent", "subset": "H", "condition": "skeleton", "items": 25})
        self.assertEqual(self.check(spec(condition="skeleton", tier="T1"), b), [])


class TestProjection(RunDir):
    def pilot(self, system, run_id, costs, statuses, cap=0.4):
        ledger = [{"item": item, "cost_usd": usd, "tokens_all": {"input": 1000, "output": 100},
                   "web_searches": 3, "wall_seconds": 60.0} for item, usd in costs]
        outputs = [{"item": item, "status": status, "caps": {"usd_item": cap},
                    "native": {"candidates": [{"rank": 1, "title": SENTINEL, "relation": "equivalent"}]}}
                   for item, status in statuses]
        d = self.write_run(f"pilots/{system}/{run_id}", {"system": system, "kind": "pilot"}, ledger, outputs)
        (d / "resolved.jsonl").write_text(json.dumps({"verdict": SENTINEL}) + "\n", encoding="utf-8")
        (d / "calls.jsonl").write_text(json.dumps({"step": SENTINEL}) + "\n", encoding="utf-8")
        return d

    def test_projection_formula(self):
        d = self.pilot("single-agent", "p1", [("guide-S1", 0.30), ("guide-E1", 0.50)],
                       [("guide-S1", "ok"), ("guide-E1", "capped")])
        out_path = self.root / "projection.json"
        result = budget.project([d], budget_file()["plan"], out_path=out_path)
        row = result["rows"][0]
        self.assertEqual((row["system"], row["items"]), ("single-agent", 25))
        self.assertAlmostEqual(row["pilot_mean_usd"], 0.40)
        self.assertEqual(row["pilot_max_usd"], 0.50)
        self.assertEqual(row["projected_usd"], 15.0)
        self.assertEqual([i["ok_within_cap"] for i in row["pilot_items"]], [False, True])
        self.assertEqual(row["pilot_items"][1]["tokens_all"], {"input": 1000, "output": 100})
        self.assertIsNone(result["rows"][1]["projected_usd"])
        self.assertEqual(result["without_pilot"], ["inc-style"])
        self.assertEqual(json.loads(out_path.read_text(encoding="utf-8")), result)

    def test_spend_of_a_rerun_item_is_summed(self):
        d = self.pilot("single-agent", "p1", [("guide-S1", 0.0), ("guide-S1", 0.2), ("guide-E1", 0.4)],
                       [("guide-S1", "infra_error"), ("guide-S1", "ok"), ("guide-E1", "ok")])
        row = budget.project([d], budget_file()["plan"][:1], out_path=None)["rows"][0]
        self.assertEqual([(i["item"], i["usd"], i["status"]) for i in row["pilot_items"]],
                         [("guide-E1", 0.4, "ok"), ("guide-S1", 0.2, "ok")])
        self.assertAlmostEqual(row["projected_usd"], 0.3 * 1.5 * 25)

    def test_infrastructure_errors_are_left_out_of_the_mean(self):
        # the infra item was re-run in a second pilot directory (the CLI path); before, its $0 halved the mean
        p1 = self.pilot("single-agent", "p1", [("guide-P2", 0.0), ("guide-E1", 0.4)],
                        [("guide-P2", "infra_error"), ("guide-E1", "ok")])
        row = budget.project([p1], budget_file()["plan"][:1], out_path=None)["rows"][0]
        self.assertEqual((row["pilot_mean_usd"], row["pilot_max_usd"], row["projected_usd"]), (0.4, 0.4, 15.0))
        self.assertEqual([(i["item"], i["status"]) for i in row["pilot_items"]],
                         [("guide-E1", "ok"), ("guide-P2", "infra_error")])
        p2 = self.pilot("single-agent", "p2", [("guide-P2", 0.4)], [("guide-P2", "ok")])
        row = budget.project([p1, p2], budget_file()["plan"][:1], out_path=None)["rows"][0]
        self.assertEqual((row["pilot_mean_usd"], row["projected_usd"]), (0.4, 15.0))
        mismatch = self.pilot("inc-style", "p3", [("guide-P2", 0.3)], [("guide-P2", "model_mismatch")])
        row = budget.project([mismatch], budget_file()["plan"][1:2], out_path=None)
        self.assertIsNone(row["rows"][0]["projected_usd"])
        self.assertEqual(row["without_pilot"], ["inc-style"])

    def test_projection_reads_no_candidates(self):
        d = self.pilot("single-agent", "p1", [("guide-S1", 0.3)], [("guide-S1", "ok")])
        read = []
        real = Path.read_text

        def spy(path, *args, **kwargs):
            read.append(path.name)
            return real(path, *args, **kwargs)

        out_path = self.root / "projection.json"
        with mock.patch.object(Path, "read_text", spy):
            result = budget.project([d], budget_file()["plan"], out_path=out_path)
        self.assertLessEqual(set(read), {"ledger.jsonl", "outputs.jsonl", "run.json"})
        self.assertNotIn(SENTINEL, json.dumps(result))
        self.assertNotIn(SENTINEL, out_path.read_text(encoding="utf-8"))
        self.assertNotIn("equivalent", out_path.read_text(encoding="utf-8"))


class TestCommittedBudget(unittest.TestCase):
    """The committed budget.json: the approved caps (PLAN.md, Budget), and spend outside the harness that matches the
    ledgers it names and leaves no ledger out."""
    APPROVED = {"pilots": 40, "refpipe": 150, "theorizer": 200, "owl": 60, "llm_baselines": 30, "reserve": 20}
    # Booked at the CLI's own figure: their cost_usd misses the web tool's own calls (that step's README).
    CLI_FIGURE = {"eval/results/ai-origin-prior-art/probes.jsonl"}

    def setUp(self):
        self.budget = json.loads((budget.MEASUREMENT_A / "budget.json").read_text(encoding="utf-8"))

    def test_lines_are_the_approved_caps(self):
        self.assertEqual(self.budget["lines"], self.APPROVED)

    @unittest.skipUnless((budget.MEASUREMENT_A / "runs").exists(), "needs the data archive (run ledgers)")
    def test_spend_outside_the_harness_matches_its_ledgers(self):
        outside = self.budget["spent_outside_harness"]
        self.assertEqual(set(outside), {"pilots", "reserve"})
        for line, entry in outside.items():
            total = sum(float(row.get("cli_cost_usd" if source in self.CLI_FIGURE else "cost_usd") or 0.0)
                        for source in entry["sources"] for row in read_jsonl(REPO_ROOT / source))
            self.assertAlmostEqual(entry["usd"], total, places=6, msg=line)
        self.assertAlmostEqual(budget.spent_outside_harness(self.budget, "pilots"), 35.4190696, places=6)  # + $0.3440 and $3.6979 on 2026-10-11

    def test_every_ledger_outside_the_harness_is_booked(self):
        listed = {s for entry in self.budget["spent_outside_harness"].values() for s in entry["sources"]}
        harness = ("eval/results/measurement-a/pilots/", "eval/results/measurement-a/runs/")
        found = {p.relative_to(REPO_ROOT).as_posix() for base in ("eval", "data")
                 for p in (REPO_ROOT / base).rglob("*.jsonl") if p.name == "ledger.jsonl" or "probes" in p.name}
        self.assertEqual({p for p in found if not p.startswith(harness)} - listed, set())


if __name__ == "__main__":
    unittest.main()
