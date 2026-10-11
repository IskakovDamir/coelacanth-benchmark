"""AI-origin candidates (source 4 in PLAN.md): methods that Opus 5.5 and Sonnet 5 propose as new, dated the day they
are generated, so they are post-cutoff for every model by construction. They feed H-post and the AI part of the
paper; they are stored unlabelled and labelled later like any other claim (origin.kind: ai).

Five fields, six methods per field and model (30 per model): one field from each OpenAlex domain (ecology and
evolutionary biology, epidemiology, materials science, economics) plus cell and molecular biology for experimental,
non-computational methods. Each call gets the same neutral prompt, no tools and the model's default effort. Writes,
under data/candidates/ai-origin/:
    prompts.json   the exact system prompt, user prompt template, fields, models and date
    outputs.jsonl  each call's raw answer
    methods.jsonl  one row per proposed method, parsed from the answers
    ledger.jsonl   cost per call

    PYTHONPATH=src python -m coelacanth.mining.ai_origin --cap 10
"""

from __future__ import annotations

import argparse
import json
import tempfile

from ..llm import DEFAULT_EFFORT, append_jsonl, auth_problem, cli_call, cli_version, read_jsonl, run_jobs
from . import CANDIDATES_DIR, write_jsonl

OUT_DIR = CANDIDATES_DIR / "ai-origin"
DATE = "2026-09-24"
MODELS = ["claude-opus-5-5", "claude-sonnet-5"]
FIELDS = ["ecology and evolutionary biology", "epidemiology", "materials science", "economics",
          "cell and molecular biology"]
PER_FIELD = 6
KEYS = ("name", "problem", "method", "evaluation", "why_new")
SYSTEM = "You are an AI scientist. You have no tools and cannot look anything up."
TEMPLATE = ("Today is {date}. Propose {n} new methods for open problems in {field}: methods that you believe have not "
            "been published. For each method give: a short name; the problem it addresses; the method itself, stated "
            "precisely enough that an expert in {field} could implement or test it (at most 150 words, in the "
            "field's own vocabulary); how it would be evaluated (data or experiments); and why you believe it is "
            'new. Answer with a JSON array of {n} objects with the keys "name", "problem", "method", "evaluation" '
            'and "why_new", and nothing else.')


def prompt(field: str) -> str:
    return TEMPLATE.format(date=DATE, n=PER_FIELD, field=field)


def parse_methods(text: str | None) -> list[dict]:
    """The proposed methods from one answer; entries without the five keys are dropped."""
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [{k: str(obj[k]).strip() for k in KEYS} for obj in data if isinstance(obj, dict)
            and all(isinstance(obj.get(k), str) and obj[k].strip() for k in KEYS)]


def good_outputs(outputs: list[dict], ledger: list[dict]) -> dict[tuple[str, str], dict]:
    """The latest usable answer per (model, field): no error, parseable, and produced by the requested model only.
    Rows written before `models_used` was stored take it from the ledger row of the same call (same model, field and
    time)."""
    used_by_call = {(e.get("model"), e.get("field"), e.get("at")): e.get("models_used") for e in ledger}
    latest = {}
    for o in outputs:
        used = o.get("models_used") or used_by_call.get((o["model"], o["field"], o["at"]))
        if not o.get("is_error") and used == [o["model"]] and parse_methods(o.get("answer")):
            latest[(o["model"], o["field"])] = o
    return latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.mining.ai_origin")
    parser.add_argument("--cap", type=float, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    problem = auth_problem()
    if problem:
        raise SystemExit(f"not started: {problem}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    version = cli_version()
    (OUT_DIR / "prompts.json").write_text(json.dumps({
        "date": DATE, "models": {m: DEFAULT_EFFORT[m] for m in MODELS}, "fields": FIELDS, "per_field": PER_FIELD,
        "system": SYSTEM, "user_template": TEMPLATE, "cli": version,
        "flags": "claude -p --bare --safe-mode --tools '' --strict-mcp-config --disable-slash-commands "
                 "--no-session-persistence, default effort per model, CLAUDE_CODE_NO_MODEL_FALLBACK=1, API-key auth; "
                 "answers from any other model than the one requested are rejected"}, indent=1) + "\n",
        encoding="utf-8")
    outputs_path, ledger_path = OUT_DIR / "outputs.jsonl", OUT_DIR / "ledger.jsonl"
    done = set(good_outputs(read_jsonl(outputs_path), read_jsonl(ledger_path)))
    jobs = [(m, f) for m in MODELS for f in FIELDS if (m, f) not in done]
    neutral = tempfile.mkdtemp(prefix="ai-origin-")

    def call(job):
        model, field = job
        return cli_call(model, prompt(field), system=SYSTEM, effort=DEFAULT_EFFORT[model], cwd=neutral,
                        max_budget_usd=2.0)

    def save(job, out, at):
        model, field = job
        append_jsonl(outputs_path, {"model": model, "effort": DEFAULT_EFFORT[model], "field": field, "date": DATE,
                                    "prompt": prompt(field), "answer": out.get("result"),
                                    "is_error": bool(out.get("is_error")),
                                    "models_used": sorted(out.get("modelUsage") or {}), "cli": version, "at": at})

    summary = run_jobs(jobs, call, save, ledger_path=ledger_path, cap_usd=args.cap, workers=args.workers,
                       describe=lambda job: {"model": job[0], "field": job[1]}, expected_model=lambda job: job[0])
    latest = good_outputs(read_jsonl(outputs_path), read_jsonl(ledger_path))
    rows = []
    for model in MODELS:
        for field in FIELDS:
            o = latest.get((model, field))
            for i, method in enumerate(parse_methods(o["answer"]) if o else [], 1):
                rows.append({"id": f"ai-{model.split('-')[1]}-{FIELDS.index(field) + 1}-{i}", "model": model,
                             "field": field, "date": DATE, **method,
                             "origin": {"kind": "ai", "system": model, "mode": "unknown"}})
    write_jsonl(OUT_DIR / "methods.jsonl", rows)
    print(json.dumps({**summary, "cli": version, "methods": len(rows),
                      "per_model": {m: sum(r["model"] == m for r in rows) for m in MODELS}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
