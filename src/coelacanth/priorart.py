"""Pooled prior-art search on the AI-origin methods (Damir, 2026-10-06): three systems under test of measurement A
each return their top 10 earlier works per method, and the pooled lists decide which methods get annotation
packets. Humans label later; nothing here is a label.

Systems, all run as headless Claude Code calls with only WebSearch and WebFetch (pre-approved), the same model and
the same output contract:
- `single`: a single Claude agent with web search and a generic instruction (eval/protocol.md section 6).
- `hunch`: AgentMash Lab's `hunch-hypotheses` agent, the author's own tool, from agentmash-crew at commit 2ae56e1,
  read with `git show` and checked against its SHA-256; its preloaded `lab-protocol` skill is appended, as the
  agent's frontmatter preloads it. Adapter: a brief with Goal, Study and Acceptance; it may not write files, so it
  returns its report in the answer, and it stops after its prior-art steps (1 to 3).
- `prior_art_skill`: the MIT prompt package github.com/rianczerwinski/prior-art-skill (single commit of 2026-08-04),
  SKILL.md with its references, from the copy cached on 2026-09-24.
The method text given to every system is the method's field, problem, method and evaluation, without its coined
name and without the generator's own novelty argument (`why_new`), so no system is pointed at prior work.

Every answer ends with a JSON list of at most 10 earlier works dated before the method's date. Each call is told
to run at most MAX_SEARCHES web searches and MAX_FETCHES fetches before answering (the per-call budget is the hard
stop): unlimited, the first pilot's calls searched until the budget ran out and never answered. For Hunch this limit
overrides its own rule to search every field's vocabulary, an adapter change the results must state. Costs are booked per
call in the ledger at the official prices, tokens plus web searches at $10 per 1,000 (coelacanth.pricing).

    PYTHONPATH=src python -m coelacanth.priorart run --limit 2 --cap 15      # pilot: the first 2 methods
    PYTHONPATH=src python -m coelacanth.priorart run --cap 15                # the rest, resumes where it stopped
    PYTHONPATH=src python -m coelacanth.priorart pool                        # pooled candidates per method
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

from .env import REPO_ROOT
from .llm import auth_problem, append_jsonl, cli_call, cli_version, read_jsonl, run_jobs
from .pricing import web_searches

OUT_DIR = REPO_ROOT / "eval" / "results" / "ai-origin-prior-art"
METHODS = REPO_ROOT / "data" / "candidates" / "ai-origin" / "methods.jsonl"
MODEL = "claude-haiku-4-5-20251001"
TOOLS = ("WebSearch", "WebFetch")
SYSTEMS = ("single", "hunch", "prior_art_skill")
TOP_K = 10
MAX_BUDGET_PER_CALL = 0.15  # the first pilot ran at 0.40 and no call finished its list
MAX_SEARCHES = 3  # the first pilot, unlimited, used 14 (single) and 10 (hunch) searches without answering
MAX_FETCHES = 3
RELATIONS = ("equivalent", "specialization", "generalization", "partial", "similar_mechanism", "different")

HUNCH_REPO = Path.home() / "dev" / "agentmash-crew"
HUNCH_COMMIT = "2ae56e145be0e2fa2eba744296edbe8bcd5342a3"
HUNCH_FILES = {"lab/agents/hunch-hypotheses.md": "711b3dec575121fe11ad6f984293cc56504173ada623816a795d2651890797b5",
               "lab/skills/lab-protocol/SKILL.md": "70f71a9462dd79996dfa83d602cf5778cb6117b933c4a87b933c37bcce85504e"}
PRIOR_ART_SKILL = REPO_ROOT / "cache" / "fulltext" / "prior-art-skill-czerwinski-2026.txt"

SINGLE_SYSTEM = ("You are a research assistant with web search and web fetch. Your task is to find the earliest "
                 "published work that states the same method or result as the one you are given, in any field and "
                 "under any name, and to say how closely each work matches.")

CONTRACT = """Search budget: at most {searches} web searches and at most {fetches} fetched pages in total, then stop \
searching and write your answer; an answer that is never written is worth nothing. Only works published before {date} \
count. End your answer with one fenced JSON block (```json ... ```) holding a \
list of at most {k} earlier works, closest first, each an object with the keys "rank", "title", "authors", "year", \
"venue", "doi", "url", "field" and "relation", plus "why" (one sentence on what the work shares with the method). \
"relation" is how the method relates to the work: "equivalent", "specialization" (the method is a special case of \
the work), "generalization", "partial" (the work covers at least two key parts of the method but not all), \
"similar_mechanism" (same mechanism in a different system or meaning) or "different". Use null for unknown fields. \
If nothing close exists, return an empty list []."""


def load_methods(path: Path = METHODS) -> list[dict]:
    return read_jsonl(path)


def method_text(m: dict) -> str:
    """What every system sees: field, problem, method and evaluation; no coined name, no novelty argument."""
    return (f"Field: {m['field']}\n\nProblem: {m['problem']}\n\nMethod: {m['method']}\n\n"
            f"Evaluation: {m.get('evaluation') or 'not stated'}")


def _git_show(path: str) -> str:
    out = subprocess.run(["git", "-C", str(HUNCH_REPO), "show", f"{HUNCH_COMMIT}:{path}"], capture_output=True,
                         env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}, check=True)
    text = out.stdout
    digest = hashlib.sha256(text).hexdigest()
    if digest != HUNCH_FILES[path]:
        raise RuntimeError(f"{path} at {HUNCH_COMMIT[:7]} has SHA-256 {digest}, expected {HUNCH_FILES[path]}")
    return text.decode("utf-8")


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S).strip()


def hunch_system() -> str:
    agent = _strip_frontmatter(_git_show("lab/agents/hunch-hypotheses.md"))
    skill = _strip_frontmatter(_git_show("lab/skills/lab-protocol/SKILL.md"))
    return f"{agent}\n\n# Preloaded skill: lab-protocol\n\n{skill}"


def prior_art_system() -> str:
    text = PRIOR_ART_SKILL.read_text(encoding="utf-8")
    start = text.index("===== SKILL.md =====")
    return text[start:].strip()


def systems() -> dict[str, str]:
    return {"single": SINGLE_SYSTEM, "hunch": hunch_system(), "prior_art_skill": prior_art_system()}


def user_message(system: str, m: dict) -> str:
    contract = CONTRACT.format(date=m["date"], k=TOP_K, searches=MAX_SEARCHES, fetches=MAX_FETCHES)
    if system == "hunch":
        return (f"Brief (task ai-origin-{m['id']}, agent hunch-hypotheses)\n\n"
                f"Goal: find out whether the method below has already been done, in any field and under any name, "
                f"and lay out the closest prior art.\n"
                f"Study: a benchmark of cross-field rediscoveries. This method was proposed as new by an AI system on "
                f"{m['date']}. There is no SSOT, decisions.md or pre-registration for this study: this brief is the "
                f"whole context. You cannot read or write files; return your report in your answer.\n"
                f"Acceptance: your process steps 1 to 3 only (the claim and its plain version, the vocabulary table "
                f"for at least three fields, the typed prior-art table with fetched evidence); no proposals. Then the "
                f"JSON block below.\nOwnership: none.\n\n{method_text(m)}\n\n{contract}")
    if system == "prior_art_skill":
        return (f"Search prior art for this proposal and deliver the novelty boundary.\n\n{method_text(m)}\n\n"
                f"{contract}")
    return f"Find the earliest published prior work for this method.\n\n{method_text(m)}\n\n{contract}"


def parse_candidates(text: str | None) -> list[dict] | None:
    """The last fenced JSON list in the answer, cleaned; None when there is none."""
    blocks = re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", text or "", flags=re.S)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        out = []
        for n, c in enumerate(x for x in data if isinstance(x, dict)):
            if not c.get("title"):
                continue
            relation = c.get("relation") if c.get("relation") in RELATIONS else None
            year = c.get("year")
            out.append({"rank": n + 1, "title": str(c["title"]).strip(), "authors": c.get("authors"),
                        "year": int(year) if isinstance(year, (int, float)) or str(year or "").isdigit() else None,
                        "venue": c.get("venue"), "doi": c.get("doi"), "url": c.get("url"), "field": c.get("field"),
                        "relation": relation, "why": c.get("why")})
            if len(out) == TOP_K:
                break
        return out
    return None


def run(cap: float, limit: int | None, workers: int = 2) -> dict:
    problem = auth_problem()
    if problem:
        raise SystemExit(problem)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prompts = systems()
    (OUT_DIR / "prompts.json").write_text(json.dumps(
        {"model": MODEL, "tools": list(TOOLS), "top_k": TOP_K, "max_budget_per_call_usd": MAX_BUDGET_PER_CALL,
         "max_searches": MAX_SEARCHES, "max_fetches": MAX_FETCHES,
         "hunch": {"repo": "github.com/agentmash-dev/agentmash-crew", "commit": HUNCH_COMMIT, "sha256": HUNCH_FILES},
         "prior_art_skill": {"source": "github.com/rianczerwinski/prior-art-skill", "cached": str(PRIOR_ART_SKILL.name)},
         "systems": {name: text if name == "single" else {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                                          "words": len(text.split())}
                     for name, text in prompts.items()},
         "note": "the hunch and prior-art-skill system prompts are not copied here: they are rebuilt from the pinned "
                 "sources above, and their SHA-256 identifies the exact text used",
         "contract": CONTRACT}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    methods = load_methods()
    methods = methods[:limit] if limit else methods
    done = {(a["method"], a["system"]) for a in read_jsonl(OUT_DIR / "answers.jsonl") if not a.get("is_error")}
    jobs = [(m, s) for m in methods for s in SYSTEMS if (m["id"], s) not in done]
    version = cli_version()
    lock = threading.Lock()

    def call(job):
        m, s = job
        with tempfile.TemporaryDirectory() as cwd:
            return cli_call(MODEL, user_message(s, m), system=prompts[s], cwd=cwd, tools=TOOLS,
                            max_budget_usd=MAX_BUDGET_PER_CALL, timeout=1200)

    def save(job, out, at):
        m, s = job
        cands = parse_candidates(out.get("result")) if not out.get("is_error") else None
        with lock:
            append_jsonl(OUT_DIR / "answers.jsonl", {
                "method": m["id"], "system": s, "model": MODEL, "at": at, "cli": version,
                "is_error": bool(out.get("is_error")), "declined": bool(out.get("declined")),
                "model_mismatch": out.get("model_mismatch"), "web_searches": web_searches(out),
                "num_turns": out.get("num_turns"), "answer": out.get("result"),
                "candidates": cands, "parsed": cands is not None, "stderr": out.get("stderr")})

    summary = run_jobs(jobs, call, save, ledger_path=OUT_DIR / "ledger.jsonl", cap_usd=cap, workers=workers,
                       describe=lambda job: {"method": job[0]["id"], "system": job[1], "model": MODEL},
                       expected_model=lambda job: MODEL)
    return {**summary, "jobs": len(jobs), "cli": version}


def _key(c: dict) -> str:
    doi = (c.get("doi") or "").lower().replace("https://doi.org/", "").strip()
    return doi or re.sub(r"[^a-z0-9]", "", (c.get("title") or "").lower())[:80]


def pool() -> dict:
    """Pooled candidates per method: the union of every system's top 10, with who found it at which rank."""
    answers = [a for a in read_jsonl(OUT_DIR / "answers.jsonl") if not a.get("is_error") and a.get("candidates")]
    pooled: dict[str, dict[str, dict]] = defaultdict(dict)
    for a in answers:
        for c in a["candidates"]:
            entry = pooled[a["method"]].setdefault(_key(c), {**c, "found_by": [], "relations": {}})
            entry["found_by"].append(f"sys:{a['system']}@{c['rank']}")
            entry["relations"][a["system"]] = c.get("relation")
    out = {mid: sorted(cands.values(), key=lambda c: min(int(f.rsplit("@", 1)[1]) for f in c["found_by"]))
           for mid, cands in pooled.items()}
    (OUT_DIR / "pooled.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {mid: len(c) for mid, c in out.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.priorart")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cap", type=float, required=True, help="dollar cap for all calls in the ledger")
    r.add_argument("--limit", type=int, help="first N methods only (pilot)")
    r.add_argument("--workers", type=int, default=2)
    sub.add_parser("pool")
    args = parser.parse_args(argv)
    if args.command == "pool":
        print(json.dumps(pool(), indent=1))
        return 0
    print(json.dumps(run(args.cap, args.limit, args.workers), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
