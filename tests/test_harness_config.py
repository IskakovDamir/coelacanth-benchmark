"""Tests for measurement A's system configs: canonical files and their hash, the hash log and its refusals, frozen
configs (only once the hash log is committed and pushed), pinned third-party prompts, league checks, the models a run
accepts, label maps and tuning. No model or network is called: each test that logs builds a small git repository in a
temporary directory, laid out like this one, with a bare repository beside it as its upstream.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coelacanth import priorart
from coelacanth.benchmark.items import RELATIONS
from coelacanth.harness import config
from coelacanth.harness.config import ConfigError

MA = "eval/results/measurement-a"
BRIEF, CONTRACT, STEP = (f"{MA}/prompts/agents/brief-single.txt", f"{MA}/prompts/agents/contract.txt",
                         f"{MA}/prompts/inc-style/rerank.txt")
TEXTS = {BRIEF: "Find the earliest published prior work for this claim.\n",
         CONTRACT: "Search budget: at most $searches web searches.\n", STEP: "Rank the works by facet.\n"}
GUIDE = {"status": "excluded", "exclusion_reason": "guide_example", "provenance": {"source": "guide"}}
PILOTS = [{"id": "guide-S1", **GUIDE}, {"id": "guide-E1", **GUIDE},
          {"id": "guide-X1", "status": "labelled", "provenance": {"source": "guide"}}]
HUNCH_FILES = {"lab/agents/hunch-hypotheses.md": "---\nname: hunch\n---\nHunch agent.\n",
               "lab/skills/lab-protocol/SKILL.md": "---\nname: lab-protocol\n---\nLab protocol.\n"}


def sha(text) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def agent_cfg(system="single-agent", **changes) -> dict:
    cfg = {"schema": config.SCHEMA, "system": system, "row_label": "Single Claude agent with web search",
           "disclosure": None, "adapter": "agent", "variant": "single", "league": "claude-code-agents",
           "allowed_subsets": ["H", "H_half", "ALL_minus_H"], "conditions": ["text", "skeleton"],
           "model": "claude-sonnet-5-5", "effort": "high", "models_called": ["claude-sonnet-5-5"], "aux_models": [],
           "temperature": "CLI default, not settable",
           "cli": {"version": "2.1.286 (Claude Code)", "stop_subtypes": {"capped": None, "turn_limit": None}},
           "tools": ["WebSearch", "WebFetch"], "max_turns": 20, "search_cap": 3, "fetch_cap": 3,
           "per_item_cap_usd": 0.4, "timeout_s": 1200, "top_k": 10,
           "prompts": {"system": {"source": "coelacanth.priorart.SINGLE_SYSTEM", "sha256": sha(priorart.SINGLE_SYSTEM)},
                       "brief": {"path": BRIEF, "sha256": sha(TEXTS[BRIEF])},
                       "contract": {"path": CONTRACT, "sha256": sha(TEXTS[CONTRACT])}},
           "answer_after_n_searches": "first sentence of contract.txt",
           "retrieval": {"backend": "claude_code_web", "date_filter": "contract sentence; post hoc in resolve",
                         "exclusions": "post hoc in resolve"},
           "native": {"kind": "per_candidate_relation", "labels": list(RELATIONS)},
           "label_map": {"rule": "precedence_over_candidates", "empty_list": "nothing_close", "no_json": "unparsed"},
           "probability": None, "line": "llm_baselines", "price_date": "2026-10-06",
           "tuning": {"items": ["guide-S1", "guide-E1"],
                      "iterations": [{"n": 1, "date": "2026-10-10", "change": "cap 0.40", "reason": "cost only"}],
                      "complete": True}}
    cfg.update(changes)
    return cfg


def hunch_cfg(**changes) -> dict:
    cfg = agent_cfg("hunch", variant="hunch", allowed_subsets=["P40", "P20"], disclosure="the author's own tool",
                    pinned={"repo": "github.com/agentmash-dev/agentmash-crew", "commit": priorart.HUNCH_COMMIT,
                            "files": dict(priorart.HUNCH_FILES), "archive": "10.5281/zenodo.0"})
    cfg["prompts"] = {**cfg["prompts"], "system": {"source": "coelacanth.priorart.hunch_system", "sha256": None}}
    cfg.update(changes)
    return cfg


def reimpl_cfg(system="inc-style", **changes) -> dict:
    cfg = {"schema": config.SCHEMA, "system": system, "row_label": "INC-style: our re-implementation",
           "disclosure": None, "adapter": "inc_style", "league": "reimpl",
           "allowed_subsets": ["H", "H_half", "ALL_minus_H"], "conditions": ["text", "skeleton"],
           "model": "claude-sonnet-5-5", "effort": "high", "models_called": ["claude-sonnet-5-5"], "aux_models": [],
           "temperature": "CLI default, not settable", "cli": {"version": "2.1.286 (Claude Code)", "stop_subtypes": {}},
           "tools": [], "per_item_cap_usd": 0.3, "timeout_s": 600, "top_k": 10, "prompts": {},
           "retrieval": {"backend": "openalex", "filter": "to_publication_date: claim.date minus 1 day",
                         "per_page": 25, "max_searches": 30, "exclusions": "dropped in code before any LLM step"},
           "steps": [{"name": "rerank", "prompt": {"path": STEP, "sha256": sha(TEXTS[STEP])}}],
           "clean_room": {"paper": "arXiv 2506.22026, CC BY 4.0", "sections": [], "repo_used": False},
           "primad": ["I'", "D' Sonnet 5.5", "P' OpenAlex"],
           "native": {"kind": "item_label", "labels": ["novel", "not novel"]},
           "label_map": {"rule": "native_item_label",
                         "map": {"novel": {"six": None, "class3": "nothing_close"},
                                 "not novel": {"six": None, "class3": "already_done"}}},
           "probability": None, "line": "llm_baselines", "price_date": "2026-10-06",
           "tuning": {"items": [], "iterations": [], "complete": True}}
    cfg.update(changes)
    return cfg


class Repo(unittest.TestCase):
    """A temporary git repository with the code files, prompt files and pilot items a config names."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "repo"
        self.ma = self.root / MA
        self.pilot_items = self.write(f"{MA}/pilot-items.jsonl", "".join(json.dumps(r) + "\n" for r in PILOTS))
        for rel in config.AGENT_CODE + config.REIMPL_CODE + ("src/coelacanth/harness/inc_style.py",):
            self.write(rel, f"# {rel}\n")
        for rel, text in TEXTS.items():
            self.write(rel, text)
        remote = self.root.parent / "remote.git"
        self.git("init", "-q", "--bare", str(remote), cwd=self.root.parent)
        self.git("init", "-q")
        self.git("remote", "add", "origin", str(remote))
        self.commit(push=False)
        self.git("push", "-q", "-u", "origin", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def put(self, cfg, commit=True) -> Path:
        path = self.ma / "configs" / f"{cfg['system']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(config.canonical_bytes(cfg))
        if commit:
            self.commit()
        return path

    def git(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        subprocess.run(["git", "-C", str(cwd or self.root), "-c", "user.name=test", "-c", "user.email=test@example.org",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                       check=True, capture_output=True, env=env)

    def commit(self, push=True):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "test")
        if push:
            self.git("push", "-q")

    def log(self, system="single-agent"):
        return config.log(system, root=self.ma, repo_root=self.root)

    def frozen(self, system="single-agent"):
        return config.frozen(system, root=self.ma, repo_root=self.root)


class TestCanonical(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_is_file_sha256(self):
        cfg = agent_cfg(row_label="Agent with a non-ASCII label: Schrödinger")
        path = self.dir / "single-agent.json"
        path.write_bytes(config.canonical_bytes(cfg))
        loaded, digest = config.load("single-agent", self.dir)
        self.assertEqual(loaded, cfg)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(path.read_bytes().endswith(b"}\n"))
        self.assertIn("Schrödinger".encode("utf-8"), path.read_bytes())

    def test_non_canonical_file_refused(self):
        cfg = agent_cfg(row_label="Schrödinger")
        path = self.dir / "single-agent.json"
        variants = (json.dumps(cfg, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    json.dumps(cfg, sort_keys=True, indent=1, ensure_ascii=False),
                    json.dumps(dict(reversed(list(cfg.items()))), indent=1, ensure_ascii=False) + "\n",
                    json.dumps(cfg, sort_keys=True, indent=1) + "\n")
        for text in variants:
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError):
                config.load("single-agent", self.dir)

    def test_file_must_hold_its_own_system(self):
        (self.dir / "hunch.json").write_bytes(config.canonical_bytes(agent_cfg()))
        with self.assertRaisesRegex(ConfigError, "not 'hunch'"):
            config.load("hunch", self.dir)


class TestLog(Repo):
    def test_log_appends_a_row_and_frozen_returns_the_config(self):
        cfg = agent_cfg()
        path = self.put(cfg)
        row = self.log()
        self.assertEqual(row["config_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(row["prompts"], {"system": sha(priorart.SINGLE_SYSTEM), "brief": sha(TEXTS[BRIEF]),
                                          "contract": sha(TEXTS[CONTRACT])})
        self.assertEqual(set(row["code"]), set(config.AGENT_CODE))
        self.assertEqual(row["code"]["src/coelacanth/llm.py"], sha("# src/coelacanth/llm.py\n"))
        self.assertRegex(row["git_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(row["cli_version"], "2.1.286 (Claude Code)")
        logged = [json.loads(line) for line in (self.ma / "config-hashes.jsonl").read_text().splitlines()]
        self.assertEqual(logged, [row])
        self.commit()
        self.assertEqual(self.frozen(), (cfg, row["config_sha256"]))

    def test_prompt_or_code_change_after_log_refused(self):
        self.put(agent_cfg())
        self.log()
        self.commit()
        agents = "src/coelacanth/harness/agents.py"
        for rel, label in ((BRIEF, "prompt brief"), (agents, f"code {agents}")):
            original = (self.root / rel).read_text(encoding="utf-8")
            self.write(rel, original + "one more line\n")
            with self.assertRaisesRegex(ConfigError, label):
                self.frozen()
            self.write(rel, original)
            self.frozen()
        self.put(agent_cfg(per_item_cap_usd=0.5))
        with self.assertRaisesRegex(ConfigError, "the config file"):
            self.frozen()

    def test_frozen_refused_until_the_hash_log_is_committed_and_pushed(self):
        # before, frozen read the file from disk: a row that existed only locally was enough for a scored run
        self.put(agent_cfg())
        self.log()
        with self.assertRaisesRegex(ConfigError, "config-hashes.jsonl has uncommitted changes"):
            self.frozen()
        self.commit(push=False)
        with self.assertRaisesRegex(ConfigError, "not pushed yet"):
            self.frozen()
        self.git("push", "-q")
        self.frozen()
        with (self.ma / "config-hashes.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"system": "hunch", "config_sha256": "0" * 64}) + "\n")  # appended by hand
        with self.assertRaisesRegex(ConfigError, "uncommitted changes"):
            self.frozen()
        self.git("checkout", "--", f"{MA}/config-hashes.jsonl")
        self.git("branch", "-q", "--unset-upstream")
        with self.assertRaisesRegex(ConfigError, "has no upstream"):
            self.frozen()

    def test_frozen_refused_before_any_log(self):
        self.put(agent_cfg())
        with self.assertRaisesRegex(ConfigError, "no row"):
            self.frozen()

    def test_log_refused_with_uncommitted_changes(self):
        self.put(agent_cfg(), commit=False)
        with self.assertRaisesRegex(ConfigError, "uncommitted changes in .*single-agent.json"):
            self.log()
        self.commit()
        self.write("src/coelacanth/pricing.py", "# edited\n")
        with self.assertRaisesRegex(ConfigError, "uncommitted changes in src/coelacanth/pricing.py"):
            self.log()
        self.commit()
        self.log()

    def test_log_refused_with_existing_outputs(self):
        _, digest = config.load("single-agent", self.put(agent_cfg()).parent)
        outputs = self.write(f"{MA}/runs/single-agent/text/run1/outputs.jsonl",
                             json.dumps({"item": "cf-0001", "kind": "scored", "config_sha256": "0" * 64}) + "\n")
        self.commit()
        with self.assertRaisesRegex(ConfigError, "under another config hash"):
            self.log()
        outputs.write_text(json.dumps({"item": "cf-0001", "kind": "scored", "config_sha256": digest}) + "\n")
        self.commit()
        self.assertEqual(self.log()["config_sha256"], digest)

    def test_log_refused_when_a_recorded_prompt_hash_is_stale(self):
        cfg = agent_cfg()
        cfg["prompts"]["brief"]["sha256"] = "<hex>"
        self.put(cfg)
        with self.assertRaisesRegex(ConfigError, "prompt brief: the config records <hex>"):
            self.log()

    def test_log_refused_with_problems(self):
        self.put(agent_cfg(tuning={"items": ["guide-S1"], "iterations": [], "complete": False}))
        with self.assertRaisesRegex(ConfigError, "tuning.complete is not true"):
            self.log()
        self.assertFalse((self.ma / "config-hashes.jsonl").exists())

    def test_reimpl_step_prompts_are_hashed(self):
        self.put(reimpl_cfg())
        row = self.log("inc-style")
        self.assertEqual(row["prompts"], {"steps.rerank": sha(TEXTS[STEP])})
        self.assertIn("src/coelacanth/harness/inc_style.py", row["code"])
        self.assertIn("src/coelacanth/harness/retrieval.py", row["code"])


class TestPinned(Repo):
    def fake_git_show(self, files):
        return mock.patch.object(priorart, "_git_show", side_effect=lambda path: files[path])

    def test_pinned_hash_mismatch_refused(self):
        with self.fake_git_show(HUNCH_FILES):
            self.assertEqual(config.prompt_hashes(hunch_cfg(), self.root)["system"],
                             sha("Hunch agent.\n\n# Preloaded skill: lab-protocol\n\nLab protocol."))
            files = {**priorart.HUNCH_FILES, "lab/agents/hunch-hypotheses.md": "0" * 64}
            with self.assertRaisesRegex(ConfigError, "pinned commit and file hashes"):
                config.prompt_hashes(hunch_cfg(pinned={"commit": priorart.HUNCH_COMMIT, "files": files}), self.root)
            with self.assertRaisesRegex(ConfigError, "pinned commit and file hashes"):
                config.prompt_hashes(hunch_cfg(pinned={"commit": "1" * 40, "files": priorart.HUNCH_FILES}),
                                     self.root)
        changed = mock.Mock(returncode=0, stdout=b"an edited agent file\n", stderr=b"")
        with mock.patch.object(priorart.subprocess, "run", return_value=changed):
            with self.assertRaisesRegex(RuntimeError, "expected"):
                config.prompt_hashes(hunch_cfg(), self.root)

        skill = self.write("cache/fulltext/prior-art-skill.txt", "preamble\n===== SKILL.md =====\nSkill text.\n")
        cfg = agent_cfg("prior-art-skill", variant="prior_art_skill",
                        pinned={"source": "github.com/rianczerwinski/prior-art-skill",
                                "cache": "cache/fulltext/prior-art-skill.txt", "sha256": sha(skill.read_bytes())})
        cfg["prompts"]["system"] = {"source": "coelacanth.priorart.prior_art_system", "sha256": None}
        with mock.patch.object(priorart, "PRIOR_ART_SKILL", skill):
            self.assertEqual(config.prompt_hashes(cfg, self.root)["system"], sha("===== SKILL.md =====\nSkill text."))
            skill.write_text("preamble\n===== SKILL.md =====\nEdited skill text.\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "the config pins"):
                config.prompt_hashes(cfg, self.root)

    def test_pinned_text_change_after_log_refused(self):
        cfg = hunch_cfg()
        with self.fake_git_show(HUNCH_FILES):
            cfg["prompts"]["system"]["sha256"] = config.prompt_hashes(cfg, self.root)["system"]
            self.put(cfg)
            self.log("hunch")
            self.commit()
            self.frozen("hunch")
        with self.fake_git_show({**HUNCH_FILES, "lab/agents/hunch-hypotheses.md": "Hunch agent, edited.\n"}):
            with self.assertRaisesRegex(ConfigError, "prompt system"):
                self.frozen("hunch")

    def test_unknown_prompt_source_refused(self):
        cfg = agent_cfg()
        cfg["prompts"]["system"] = {"source": "os.environ", "sha256": None}
        with self.assertRaisesRegex(ConfigError, "is not one of"):
            config.prompt_hashes(cfg, self.root)


class TestLeagues(Repo):
    def test_agent_league_mismatch_refused(self):
        single, hunch = agent_cfg(), hunch_cfg()
        self.assertEqual(config.league_problems([single, hunch]), [])
        for field, value in (("per_item_cap_usd", 0.6), ("tools", ["WebSearch"]), ("max_turns", 30)):
            problems = config.league_problems([single, {**hunch, field: value}])
            self.assertEqual(len(problems), 1)
            self.assertIn(f"{field} differs", problems[0])
        other_contract = {**hunch["prompts"], "contract": {"path": CONTRACT, "sha256": "f" * 64}}
        self.assertIn("prompts.contract.sha256 differs",
                      config.league_problems([single, {**hunch, "prompts": other_contract}])[0])
        self.assertIn("cli.version differs",
                      config.league_problems([single, {**hunch, "cli": {"version": "2.1.290 (Claude Code)"}}])[0])
        matched = agent_cfg("single-agent-matched", league=None, line="refpipe", per_item_cap_usd=1.2)
        self.assertEqual(config.league_problems([single, matched]), [])

        self.put(single)
        self.put(agent_cfg("prior-art-skill", variant="prior_art_skill", per_item_cap_usd=0.6))
        with self.assertRaisesRegex(ConfigError, "per_item_cap_usd differs"):
            self.log()

    def test_reimpl_league_needs_same_retrieval_and_model(self):
        inc = reimpl_cfg()
        catalyst = reimpl_cfg("ideacatalyst-style", adapter="ideacatalyst_style",
                              steps=[{"name": "translate", "prompt": {"path": STEP, "sha256": sha(TEXTS[STEP])}}],
                              native={"kind": "item_label", "labels": ["fully", "partly", "not at all"]},
                              per_item_cap_usd=0.5)
        self.assertEqual(config.league_problems([inc, catalyst]), [])
        other_retrieval = {**inc["retrieval"], "per_page": 50}
        self.assertIn("retrieval differs", config.league_problems([inc, {**catalyst, "retrieval": other_retrieval}])[0])
        haiku = {**catalyst, "model": "claude-haiku-4-5-20251001"}
        self.assertIn("model differs", config.league_problems([inc, haiku])[0])
        self.assertIn("effort differs", config.league_problems([inc, {**catalyst, "effort": "low"}])[0])


class TestProblems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pilot_items = Path(self.tmp.name) / "pilot-items.jsonl"
        self.pilot_items.write_text("".join(json.dumps(r) + "\n" for r in PILOTS), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, cfg):
        return config.problems(cfg, self.pilot_items)

    def test_complete_configs_have_no_problems(self):
        self.assertEqual(self.problems(agent_cfg()), [])
        self.assertEqual(self.problems(hunch_cfg()), [])
        self.assertEqual(self.problems(reimpl_cfg()), [])

    def test_label_map_covers_native_labels(self):
        cfg = reimpl_cfg(native={"kind": "item_label", "labels": ["novel", "not novel", "unclear"]})
        self.assertEqual(self.problems(cfg), ["native label 'unclear' has no entry in label_map.map"])
        bad_map = {"novel": {"six": None, "class3": "nothing_close"},
                   "not novel": {"six": None, "class3": "done"},
                   "extra": {"six": None, "class3": "related"}}
        problems = self.problems(reimpl_cfg(label_map={"rule": "native_item_label", "map": bad_map}))
        self.assertEqual(len(problems), 2)
        self.assertIn("'extra', which is not a native label", problems[0])
        self.assertIn("class3 'done' is not one of", problems[1])
        cfg = agent_cfg(native={"kind": "per_candidate_relation", "labels": list(RELATIONS) + ["unsure"]})
        self.assertEqual(self.problems(cfg), ["native label 'unsure' is not one of the six relations"])
        self.assertIn("label_map.rule", self.problems(agent_cfg(label_map={"rule": "majority"}))[0])

    def test_coarse_systems_get_no_six_class_label(self):
        # before, a six-way label that agreed with its class (here equivalent / already done) was accepted, so a
        # coarse system entered the six-class confusion matrix (decision 32)
        for six in ("equivalent", "nothing_close", "partial"):
            target = {"six": six, "class3": "already_done" if six == "equivalent" else "nothing_close"}
            mapping = {"novel": {"six": None, "class3": "nothing_close"}, "not novel": target}
            problems = self.problems(reimpl_cfg(label_map={"rule": "native_item_label", "map": mapping}))
            self.assertEqual(len(problems), 1, six)
            self.assertIn(f"label_map.map['not novel'].six is {six!r}", problems[0])
            self.assertIn("decision 32", problems[0])

    def test_models_called_names_every_model_the_run_accepts(self):
        # before, an Opus run passed the H check through a models_called list that named Sonnet only
        opus = agent_cfg(model="claude-opus-5-5", models_called=["claude-sonnet-5-5"])
        self.assertEqual(self.problems(opus), ["models_called ['claude-sonnet-5-5'] omits ['claude-opus-5-5'], which "
                                               "the run accepts (model and aux_models), so H would not cover them "
                                               "(decision 8)"])
        aux = agent_cfg(aux_models=["claude-haiku-4-5-20251001"])
        self.assertIn("omits ['claude-haiku-4-5-20251001']", self.problems(aux)[0])
        self.assertEqual(self.problems(agent_cfg(aux_models=["claude-haiku-4-5-20251001"],
                                                 models_called=["claude-sonnet-5-5", "claude-haiku-4-5-20251001"])), [])
        self.assertIn("neither a list", self.problems(agent_cfg(models_called="claude-sonnet-5-5"))[0])
        service = {k: v for k, v in agent_cfg(models_called="undisclosed").items() if k != "model"}
        self.assertEqual([p for p in self.problems(service) if "models_called" in p], [])

    def test_tuning_on_non_pilot_item_refused(self):
        tuning = {"items": ["guide-S1", "cf-0007"], "iterations": [], "complete": True}
        self.assertEqual(self.problems(agent_cfg(tuning=tuning)),
                         ["tuning item 'cf-0007' is not in pilot-items.jsonl; tuning happens on pilot items only"])
        tuning = {"items": ["guide-X1"], "iterations": [], "complete": True}
        self.assertIn("not an excluded LABELING.md worked example", self.problems(agent_cfg(tuning=tuning))[0])
        tuning = {"items": ["guide-S1"], "iterations": [], "complete": False}
        self.assertEqual(self.problems(agent_cfg(tuning=tuning)), ["tuning.complete is not true"])

    def test_unknown_subset_condition_and_league_refused(self):
        self.assertIn("subset 'P30'", self.problems(agent_cfg(allowed_subsets=["H", "P30"]))[0])
        self.assertIn("condition 'abstract'", self.problems(agent_cfg(conditions=["abstract"]))[0])
        self.assertIn("league 'agents'", self.problems(agent_cfg(league="agents"))[0])
        self.assertIn("cannot join league 'reimpl'", self.problems(agent_cfg(league="reimpl"))[0])
        self.assertIn("must be in league 'reimpl'", self.problems(reimpl_cfg(league=None))[0])

    def test_missing_fields_and_placeholder_zeros(self):
        cfg = agent_cfg(max_turns=0, per_item_cap_usd=0.0)
        del cfg["tools"]
        placeholder = "zero is a placeholder the pilots replace before the hash"
        self.assertEqual(self.problems(cfg), ["tools is missing", f"max_turns is 0; {placeholder}",
                                              f"per_item_cap_usd is 0.0; {placeholder}"])


if __name__ == "__main__":
    unittest.main()
