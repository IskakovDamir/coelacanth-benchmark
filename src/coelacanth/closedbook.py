"""Closed-book runs (eval/protocol.md section 3): each model answers from memory, without tools, whether a claim was
published before its date; the answers are then matched against the item's gold works.

Calls go through the Claude Code CLI with the isolation described in coelacanth.llm (no tools, no MCP servers, no
CLAUDE.md, a minimal system prompt, a neutral working directory), at each model's default effort. Every call goes to
<run>/answers.jsonl and <run>/ledger.jsonl; a run resumes where it stopped and stops before its projected spend
passes the cap.

    PYTHONPATH=src python -m coelacanth.closedbook run eval/results/closed-book-pilot --cap 20
    PYTHONPATH=src python -m coelacanth.closedbook score eval/results/closed-book-pilot
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Iterable, Mapping

from .benchmark.items import STOPWORDS, _surname
from .llm import DEFAULT_EFFORT, append_jsonl, auth_problem, cli_call, cli_version, read_jsonl, run_jobs

PROMPT = ('If this idea or result was published before the given date, name the earliest publications you know that '
          'state it: title, authors, year, field. If you do not know, answer "unknown".')
# The cross-field-framed sensitivity probe (eval/protocol.md section 3; reference/research-notes/method-contamination.md
# item 8): no gold cue, the same answer format.
CROSS_FIELD_PROMPT = ('Has this method or result appeared earlier in a different field, possibly under a different name? '
                      'If so, name the earliest such publications: title, authors, year, field. If you do not know, '
                      'answer "unknown".')
PROMPTS = {"frozen": PROMPT, "cross_field": CROSS_FIELD_PROMPT}
FORMAT = "List at most five publications, one per line."
SYSTEM = "Answer from your own knowledge. You have no tools and cannot look anything up."
# The probed models of protocol v1 (decision 8); the pilot also ran Sonnet 5, now legacy.
MODELS = {m: DEFAULT_EFFORT[m] for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001")}
APOSTROPHES = "['`" + chr(0x2018) + chr(0x2019) + "]"


def user_message(text: str, date: str, prompt: str = "frozen") -> str:
    return f"Date: {date}\n\nClaim: {text}\n\n{PROMPTS[prompt]} {FORMAT}"


def done_keys(answers: Iterable[Mapping]) -> set[tuple]:
    """(pid, model, sample, prompt) of the calls a resumed run skips: those with a valid answer, and those declined
    twice, since a declined sample is retried once with the identical prompt and no more (decision 6). Other errors
    (low credit, a model mismatch) are re-run."""
    valid, declined = set(), {}
    for a in answers:
        key = (a["pid"], a["model"], a["sample"], a.get("prompt", "frozen"))
        if not a.get("is_error"):
            valid.add(key)
        elif a.get("declined"):
            declined[key] = declined.get(key, 0) + 1
    return valid | {key for key, n in declined.items() if n >= 2}


def run(run_dir: Path, models: Mapping[str, str | None], samples: int, cap_usd: float, workers: int = 3,
        limit: int | None = None, prompt: str = "frozen", first_sample: int = 1) -> dict:
    problem = auth_problem()
    if problem:
        raise SystemExit(f"not started: {problem}")
    items = read_jsonl(run_dir / "items.jsonl")[:limit]
    answers_path = run_dir / "answers.jsonl"
    done = done_keys(read_jsonl(answers_path))
    jobs = [(item, model, s) for item in items for model in models for s in range(first_sample, samples + 1)
            if (item["pid"], model, s, prompt) not in done]
    version = cli_version()
    neutral = tempfile.mkdtemp(prefix="closedbook-")

    def call(job):
        item, model, _ = job
        return cli_call(model, user_message(item["text"], item["date"], prompt), system=SYSTEM, effort=models[model],
                        cwd=neutral)

    def save(job, out, at):
        item, model, sample = job
        append_jsonl(answers_path, {"pid": item["pid"], "model": model, "effort": models[model], "sample": sample,
                                    "prompt": prompt, "answer": out.get("result"), "is_error": bool(out.get("is_error")),
                                    "model_mismatch": out.get("model_mismatch"), "declined": bool(out.get("declined")),
                                    "returncode": out.get("returncode"), "stderr": out.get("stderr"),
                                    "model_usage": out.get("modelUsage"), "cli": version, "at": at})

    summary = run_jobs(jobs, call, save, ledger_path=run_dir / "ledger.jsonl", cap_usd=cap_usd, workers=workers,
                       describe=lambda job: {"pid": job[0]["pid"], "model": job[1], "sample": job[2], "prompt": prompt},
                       expected_model=lambda job: job[1])
    return {**summary, "cli": version}


# ---------- matching ----------

def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(APOSTROPHES, "", text).casefold()


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _fold(text)) if w not in STOPWORDS and len(w) > 1]


def match_gold(answer_line: str, gold: Mapping) -> str | None:
    """'strict', 'loose' or None for one answer line (or window of lines) against one gold work."""
    words = set(re.findall(r"[a-z0-9]+", _fold(answer_line)))
    squashed = re.sub(r"[^a-z0-9]", "", _fold(answer_line))
    surname = _fold(_surname(gold.get("first_author") or "") or "")
    surname = re.sub(r"[^a-z0-9]", "", surname)
    has_name = bool(surname) and (surname in words or (len(surname) >= 6 and surname in squashed))
    year = gold.get("year")
    years = {int(y) for y in re.findall(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)", answer_line)}
    year_ok = year is not None and any(abs(y - year) <= 1 for y in years)
    title_words = _content_words(gold.get("title") or "")
    overlap = (sum(w in words for w in title_words) / len(title_words)) if title_words else 0.0
    if (overlap >= 0.8 and len(title_words) >= 3) or (has_name and year_ok and overlap >= 0.3):
        return "strict"
    if (has_name and year_ok) or overlap >= 0.5:
        return "loose"
    return None


def match_answer(answer: str | None, gold: list[Mapping], concepts: Iterable[str] = ()) -> dict:
    """Best level per gold work over the answer's lines and pairs of adjacent lines; concept mentions listed."""
    lines = [line for line in (answer or "").splitlines() if line.strip()]
    windows = lines + [a + " " + b for a, b in zip(lines, lines[1:])]
    levels = []
    for g in gold:
        found = [match_gold(w, g) for w in windows]
        levels.append("strict" if "strict" in found else "loose" if "loose" in found else None)
    text = _fold(answer or "")
    return {"levels": levels, "concepts": [c for c in concepts if _fold(c) in text],
            "unknown": bool(re.fullmatch(r"\W*unknown\W*", text.strip()))}


GOLD_SETS = ("cross", "any")  # cross-field gold only (defines H) and any gold (H-any), eval/protocol.md section 3
LEVELS = {"strict": {"strict"}, "loose": {"strict", "loose"}}  # loose counts borderline matches as hits


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def score(run_dir: Path) -> dict:
    items = {i["pid"]: i for i in read_jsonl(run_dir / "items.jsonl")}
    all_answers = read_jsonl(run_dir / "answers.jsonl")
    answers = [a for a in all_answers if not a.get("is_error")]
    declined = [a for a in all_answers if a.get("declined")]
    per_call = []
    for a in answers:
        item = items[a["pid"]]
        m = match_answer(a["answer"], item["gold"], item.get("gold_concepts", []))
        hits = {"any": [], "cross": []}
        for g, level in zip(item["gold"], m["levels"]):
            if level:
                label = f"{g['first_author']} {g['year']} [{g['kind']}] {level}"
                hits["any"].append((level, label))
                if g["cross_field"]:
                    hits["cross"].append((level, label))
        per_call.append({"pid": a["pid"], "model": a["model"], "sample": a["sample"], "unknown": m["unknown"],
                         "concepts": m["concepts"], "hits_any": hits["any"], "hits_cross": hits["cross"]})
    models = sorted({c["model"] for c in per_call})
    per_item = []
    for pid, item in items.items():
        calls = [c for c in per_call if c["pid"] == pid]
        row = {"pid": pid, "source": item["source"], "ref": item["ref"], "date": item["date"], "n_calls": len(calls)}
        for gold_set in GOLD_SETS:
            for level, ok in LEVELS.items():
                row[f"hit_{gold_set}_{level}"] = sorted({c["model"] for c in calls
                                                         if any(h[0] in ok for h in c[f"hits_{gold_set}"])})
        row["declined_calls"] = sorted({(a["model"], a["sample"]) for a in declined if a["pid"] == pid})
        row["concepts"] = sorted({x for c in calls for x in c["concepts"]})
        row["unknown_calls"] = sum(c["unknown"] for c in calls)
        row["hits"] = sorted({h[1] for c in calls for h in c["hits_any"]})
        per_item.append(row)
    return {"models": models, "per_item": per_item, "per_call": per_call,
            "summary": summarise(models, per_item, per_call)}


def summarise(models: list[str], per_item: list[dict], per_call: list[dict]) -> dict:
    """Hit rates per model and failed shares (the H candidates), overall and by source, with Wilson intervals."""
    n = len(per_item)
    per_model = {}
    for model in models:
        calls = [c for c in per_call if c["model"] == model]
        entry = {"items": sum(1 for r in per_item if any(c["pid"] == r["pid"] for c in calls)), "samples": len(calls),
                 "unknown_samples": sum(c["unknown"] for c in calls)}
        for gold_set in GOLD_SETS:
            for level, ok in LEVELS.items():
                k = sum(1 for r in per_item if model in r[f"hit_{gold_set}_{level}"])
                entry[f"items_hit_{gold_set}_{level}"] = k
                entry[f"samples_hit_{gold_set}_{level}"] = sum(
                    1 for c in calls if any(h[0] in ok for h in c[f"hits_{gold_set}"]))
        per_model[model] = entry
    failed = {}
    for gold_set in GOLD_SETS:
        for level in LEVELS:
            k = sum(1 for r in per_item if not r[f"hit_{gold_set}_{level}"])
            failed[f"{gold_set}_{level}"] = {"items": k, "of": n, "share": round(k / n, 3) if n else None,
                                             "ci95": [round(x, 3) for x in wilson(k, n)]}
    groups = {source: [r for r in per_item if r["source"] == source] for source in sorted({r["source"] for r in per_item})}
    groups["non_kulikowski"] = [r for r in per_item if r["source"] != "kulikowski"]
    by_source = {name: {"items": len(rows), **{f"failed_{g}_{lv}": sum(1 for r in rows if not r[f"hit_{g}_{lv}"])
                                               for g in GOLD_SETS for lv in LEVELS}}
                 for name, rows in groups.items()}
    return {"items": n, "per_model": per_model, "failed": failed, "by_source": by_source}


def project(summary: dict, positives: int, mix: dict[str, int]) -> dict:
    """Projected H (cross-field gold) for a benchmark with `positives` items: from the pooled failed share, and from
    the failed share per source weighted by `mix` (source -> number of positives). Loose matches counted as hits give
    the low end, strict-only matches the high end."""
    out = {"positives": positives, "mix": mix, "pooled": {}, "by_mix": {}}
    for level in LEVELS:
        f = summary["failed"][f"cross_{level}"]
        out["pooled"][level] = {"H": round(positives * f["share"], 1),
                                "ci95": [round(positives * x, 1) for x in f["ci95"]]}
        total, lo, hi = 0.0, 0.0, 0.0
        for source, count in mix.items():
            s = summary["by_source"].get(source)
            if not s or not s["items"]:
                continue
            k, m = s[f"failed_cross_{level}"], s["items"]
            a, b = wilson(k, m)
            total, lo, hi = total + count * k / m, lo + count * a, hi + count * b
        out["by_mix"][level] = {"H": round(total, 1), "ci95": [round(lo, 1), round(hi, 1)]}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.closedbook")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("run_dir")
    r.add_argument("--cap", type=float, required=True, help="dollar cap for this run")
    r.add_argument("--samples", type=int, default=5, help="last sample number (protocol v1: 5)")
    r.add_argument("--first-sample", type=int, default=1)
    r.add_argument("--prompt", choices=sorted(PROMPTS), default="frozen")
    r.add_argument("--models", nargs="+", default=list(MODELS))
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--limit", type=int, help="first N items only (smoke tests)")
    s = sub.add_parser("score")
    s.add_argument("run_dir")
    s.add_argument("--positives", type=int, default=70, help="positives in the benchmark, for the H projection")
    s.add_argument("--mix", nargs="*", default=["kulikowski=20", "non_kulikowski=50"],
                   help="source=count pairs that add up to --positives (non_kulikowski pools every other source)")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if args.cmd == "run":
        print(json.dumps(run(run_dir, {m: DEFAULT_EFFORT[m] for m in args.models}, args.samples, args.cap,
                             args.workers, args.limit, args.prompt, args.first_sample)))
    else:
        result = score(run_dir)
        mix = dict(item.split("=") for item in args.mix)
        result["projection"] = project(result["summary"], args.positives, {k: int(v) for k, v in mix.items()})
        (run_dir / "scores.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for row in result["per_item"]:
            print(row["pid"], row["source"], "cross strict/loose:", len(row["hit_cross_strict"]),
                  len(row["hit_cross_loose"]), "any strict/loose:", len(row["hit_any_strict"]), len(row["hit_any_loose"]))
        print(json.dumps({"summary": result["summary"], "projection": result["projection"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
