"""The memory reference of measurement A: Sonnet 5.5 without tools under the frozen closed-book prompt, on `R`.

    PYTHONPATH=src python eval/results/measurement-a/memory_reference.py

An item counts as a success when a valid sample names a cross-field gold work (strict match of title, first author
and year by coelacanth.closedbook.match_answer; borderline matches are not counted). Two readings are given, because
the protocol defines the memory reference as the first sample and the paper scores all five:
- `sample_1`: only sample 1;
- `any_of_5`: any of the five samples.
Two gold sets: `quiz_gold` (eval/benchmark/v0/items.jsonl, the frozen items) and `gold_with_named` (adds the 14 named
works the author adjudicated as gold, items-with-named.jsonl). Writes memory-reference.json beside this file, with
Wilson 95% intervals for the success share. The memory reference gives no ranking, so there is no success@10 here.
"""

from __future__ import annotations

import json
from math import sqrt
from pathlib import Path

from coelacanth.closedbook import match_answer

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODEL, PROMPT = "claude-sonnet-5-5", "frozen"


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def main() -> int:
    R = json.loads((REPO / "eval/results/splits/splits-v1.json").read_text(encoding="utf-8"))["subsets"]["R"]
    answers = [json.loads(l) for l in (REPO / "eval/results/closed-book-v0/answers.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    out = {"format": "coelacanth-memory-reference/v1", "model": MODEL, "prompt": PROMPT, "n_items": len(R)}
    for name, path in (("quiz_gold", "eval/benchmark/v0/items.jsonl"),
                       ("gold_with_named", "eval/results/measurement-a/items-with-named.jsonl")):
        items = {i["id"]: i for i in (json.loads(l) for l in (REPO / path).read_text(encoding="utf-8").splitlines()
                                      if l.strip())}
        hit_any, hit_1 = set(), set()
        for a in answers:
            if a["pid"] not in R or a["model"] != MODEL or a["prompt"] != PROMPT or a.get("declined") or a.get("is_error"):
                continue
            gold = [{"title": c["work"].get("title"), "first_author": c["work"].get("first_author"),
                     "year": c["work"].get("year")}
                    for c in items[a["pid"]]["candidates"] if c.get("gold") and c.get("cross_field")]
            if "strict" in match_answer(a["answer"], gold)["levels"]:
                hit_any.add(a["pid"])
                if a["sample"] == 1:
                    hit_1.add(a["pid"])
        out[name] = {"sample_1": {"items": sorted(hit_1), "k": len(hit_1), "n": len(R), "wilson95_success": wilson(len(hit_1), len(R))},
                     "any_of_5": {"items": sorted(hit_any), "k": len(hit_any), "n": len(R),
                                  "wilson95_success": wilson(len(hit_any), len(R))}}
    (HERE / "memory-reference.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for name in ("quiz_gold", "gold_with_named"):
        print(name, {k: (v["k"], v["items"]) for k, v in out[name].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
