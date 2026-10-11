"""Seeded draws for benchmark v0 (eval/protocol.md decisions 14 to 16; docs/v0-composition.md), fixed before any
system under test sees an item.

- AI-origin methods (stratum A): a seeded order within each field-and-generator cell, and a seeded order of the cells.
  v0 takes the first method of each cell whose field an annotator or Damir can read, then fills the remaining slots
  up to the target with the next methods, visiting cells in the seeded order. The order never depends on what a
  system found.
- Attributed Kulikowski controls (subset C): ready controls in a seeded order, taken while their family holds fewer
  than two controls, counting those already in a batch.
- Natural near-negatives: Kulikowski's topical distractors (manifest role `distractor`) that do not name a known
  method (`method_named` other than `yes`: a paper that names a known method imports it, so labelling would find
  attributed prior art), in a seeded order, plus the Gradient Coupling catalogue's failed unification.

    PYTHONPATH=src python -m coelacanth.mining.draw
writes data/candidates/draws-v0.json.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from ..llm import read_jsonl
from . import CANDIDATES_DIR
from .queue import MANIFEST

SEED = 20261006
TARGET = {"ai_origin": 10, "controls": 8, "natural_negatives": 10}
AI_METHODS = CANDIDATES_DIR / "ai-origin" / "methods.jsonl"
QUEUE = CANDIDATES_DIR / "queue.jsonl"
CATALOGUE = CANDIDATES_DIR / "gci-catalogue.jsonl"
OUT = CANDIDATES_DIR / "draws-v0.json"
MAX_PER_FAMILY = 2


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ai_origin_order(methods: list[dict], seed: int = SEED) -> dict:
    rng = random.Random(f"{seed}:ai_origin")
    cells = defaultdict(list)
    for m in sorted(methods, key=lambda m: m["id"]):
        cells[f"{m['field']} | {m['model']}"].append(m["id"])
    for ids in cells.values():
        rng.shuffle(ids)
    cell_order = sorted(cells)
    rng.shuffle(cell_order)
    return {"cell_order": cell_order, "cells": {c: cells[c] for c in cell_order}}


def ai_origin_pick(order: dict, readable_fields: set[str] | None = None, target: int = TARGET["ai_origin"]) -> list[str]:
    cells = [c for c in order["cell_order"] if readable_fields is None or c.split(" | ")[0] in readable_fields]
    picked, depth = [], 0
    while len(picked) < target and any(depth < len(order["cells"][c]) for c in cells):
        for c in cells:
            if len(picked) < target and depth < len(order["cells"][c]):
                picked.append(order["cells"][c][depth])
        depth += 1
    return picked


def control_pick(queue: list[dict], seed: int = SEED, target: int = TARGET["controls"]) -> dict:
    controls = [r for r in queue if r["source"] == "kulikowski" and r["role"] == "attributed_control"]
    in_batch = [r for r in controls if r["batch"]]
    per_family = Counter(r["family"] for r in in_batch)
    ready = sorted((r for r in controls if r["status"] == "ready"), key=lambda r: r["ref"])
    random.Random(f"{seed}:controls").shuffle(ready)
    picked = []
    for r in ready:
        if len(in_batch) + len(picked) >= target:
            break
        if per_family[r["family"]] < MAX_PER_FAMILY:
            picked.append(r["ref"])
            per_family[r["family"]] += 1
    return {"in_batches": sorted(r["ref"] for r in in_batch), "order": [r["ref"] for r in ready], "picked": picked}


def negative_pick(manifest: list[dict], catalogue: list[dict], seed: int = SEED,
                  target: int = TARGET["natural_negatives"]) -> dict:
    distractors = sorted((r for r in manifest if r.get("role") == "distractor"), key=lambda r: r["id"])
    eligible = [r["id"] for r in distractors if r.get("method_named") != "yes"]
    random.Random(f"{seed}:negatives").shuffle(eligible)
    failed = [f"gci#{r['number']}" for r in catalogue if r.get("kind") == "B(neg)"]
    return {"distractors": len(distractors), "eligible_order": eligible, "catalogue_failed_unification": failed,
            "picked": failed + eligible[:max(0, target - len(failed))]}


def main() -> int:
    order = ai_origin_order(read_jsonl(AI_METHODS))
    out = {"seed": SEED, "target": TARGET,
           "inputs": {str(p.relative_to(CANDIDATES_DIR.parent.parent)): sha256(p)
                      for p in (AI_METHODS, QUEUE, MANIFEST, CATALOGUE)},
           "ai_origin": {**order, "picked_all_fields_readable": ai_origin_pick(order)},
           "controls": control_pick(read_jsonl(QUEUE)),
           "natural_negatives": negative_pick(read_jsonl(MANIFEST), read_jsonl(CATALOGUE))}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k]["picked" if k != "ai_origin" else "picked_all_fields_readable"]
                      for k in ("ai_origin", "controls", "natural_negatives")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
