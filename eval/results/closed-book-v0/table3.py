"""Composition of benchmark v0 batches b01 and b02 for the paper's Table 3 (counts only, no model call).

    PYTHONPATH=src python eval/results/closed-book-v0/table3.py

Reads eval/benchmark/v0/quiz/labels.json (final relation and cross-field answers of the quiz, after adjudication),
eval/benchmark/v0/batches/b01.json and b02.json (the packets: computational flag), eval/benchmark/v0/checks/attribution.json
(`R`) and eval/results/closed-book-v0/results.json (recalled, `H`), and writes table3.json beside this file with one
row per group and the markdown table to paste into paper/ecir2027/draft.md. Batch b03 (two items, labelled apart) is
not in these counts.

Groups: `positive` is a human-origin item that is not an attributed control and has no built negative; `control` is an
attributed control (subset `C`); `ai_origin` is the generated-method stratum (`A`); `negative` carries a negative kind.
`R` is attribution.json's list; `recalled` is an `R` item for which some sample names a cross-field gold work
(results.json, named_cross_low, the decided matches, or a work named closed-book that the author adjudicated cross-field gold:
checks/named-final.json items_out_of_H; `recalled_if_undecided_accepted` uses named_cross_high); `H` is an `R` item that is not recalled and has no model without a
valid sample.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
V0 = REPO / "eval" / "benchmark" / "v0"
HERE = Path(__file__).resolve().parent


def main() -> int:
    labels = json.loads((V0 / "quiz" / "labels.json").read_text(encoding="utf-8"))["items"]
    packets = {p["id"]: p for b in ("b01", "b02")
               for p in json.loads((V0 / "batches" / f"{b}.json").read_text(encoding="utf-8"))["items"]}
    R = set(json.loads((V0 / "checks" / "attribution.json").read_text(encoding="utf-8"))["R"])
    rows = {r["pid"]: r for r in json.loads((HERE / "results.json").read_text(encoding="utf-8"))["per_item"]}
    # Items the author's adjudication of the named works moved out of H (a cross-field gold work named closed-book).
    final_path = V0 / "checks" / "named-final.json"
    via_named = set(json.loads(final_path.read_text(encoding="utf-8"))["items_out_of_H"]) if final_path.exists() else set()

    def group(v: dict) -> str:
        if v["negative"]:
            return "negative"
        if v["origin_kind"] != "human":
            return "ai_origin"
        return "control" if v["role"] == "attributed_control" else "positive"

    table: dict[tuple, dict] = defaultdict(lambda: {"items": 0, "computational": 0, "with_gold": 0, "R": 0,
                                                   "recalled": 0, "recalled_if_undecided_accepted": 0, "H": 0})
    for pid, v in sorted(labels.items()):
        key = (group(v), v["source"])
        t = table[key]
        t["items"] += 1
        t["computational"] += bool(packets[pid]["claim"].get("computational"))
        t["with_gold"] += any(c.get("gold") for c in v["candidates"].values())
        if pid in R:
            t["R"] += 1
            r = rows[pid]
            # decided matches only (low bound); `recalled_if_undecided_accepted` adds the loose matches still undecided
            recalled = bool(r["named_cross_low"]) or pid in via_named
            t["recalled"] += recalled
            t["recalled_if_undecided_accepted"] += bool(r["named_cross_high"]) or pid in via_named
            t["H"] += (not recalled) and not r["models_without_valid_sample"]
    out = [{"group": g, "source": s, **t} for (g, s), t in sorted(table.items())]
    total = {k: sum(r[k] for r in out) for k in ("items", "computational", "with_gold", "R", "recalled",
                                                 "recalled_if_undecided_accepted", "H")}
    # Items of the final R for which a model names a cross-field gold work, per model and prompt (the borderline
    # decisions are complete, so the low and high bounds are equal; both are kept).
    per_model = {}
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"):
        for prompt in ("frozen", "cross_field"):
            key = f"{model}:{prompt}"
            per_model[key] = {b: sorted(i for i in R if key in rows[i][f"named_cross_{b}"]) for b in ("low", "high")}
    by_prompt = {p: {b: sorted(i for i in R if any(x.endswith(f":{p}") for x in rows[i][f"named_cross_{b}"]))
                     for b in ("low", "high")} for p in ("frozen", "cross_field")}
    (HERE / "table3.json").write_text(json.dumps({"format": "coelacanth-table3/v1", "batches": ["b01", "b02"],
                                                  "rows": out, "total": total, "R": sorted(R), "recalled_via_named_works": sorted(via_named & R),
                                                  "R_items_named_cross_per_model_prompt": per_model,
                                                  "R_items_named_cross_any_model_per_prompt": by_prompt},
                                                 indent=1) + "\n", encoding="utf-8")
    print("| Group | Source | Items | Computational | With gold | `R` | Recalled | `H` |")
    print("|---|---|---|---|---|---|---|---|")
    for r in out:
        print(f"| {r['group']} | {r['source']} | {r['items']} | {r['computational']} | {r['with_gold']} | {r['R']} | "
              f"{r['recalled']} | {r['H']} |")
    print(f"| total | | {total['items']} | {total['computational']} | {total['with_gold']} | {total['R']} | "
          f"{total['recalled']} | {total['H']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
