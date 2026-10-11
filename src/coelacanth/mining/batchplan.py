"""The plan of annotation batch b02 (docs/v0-composition.md; eval/protocol.md decisions 14, 15, 16 and 25), fixed
before any packet is drafted and before any system run.

Members, from the seeded draws (`draws-v0.json`) and the queue:
- the 4 Kulikowski claims that name their method partly or not at all and are not in b01 (queue status `ready`);
- the 4 attributed Kulikowski controls drawn in `draws-v0.json`;
- the 10 AI-origin methods drawn in `draws-v0.json`;
- 10 natural near-negatives: the drawn ones, except that a pick with no published source work is replaced by the next
  eligible distractor in the seeded order (the catalogue's failed unification, gci#18, cites no published work);
- 2 of the 10 usable screened declarations (`screening.jsonl`), drawn with a seed.
Item IDs continue after b01 (cf-0037 on) in a seeded shuffle. The 12 crossover items (decision 25) are drawn with a
seed and split into two groups of 6: group X is pre-filled for annotator 1 and bare for annotator 2, group Y the
other way round; both annotators label all 12, which also serve as the fresh shared items of decision 18.

    PYTHONPATH=src python -m coelacanth.mining.batchplan
writes eval/benchmark/v0/batches/b02-plan.json.
"""

from __future__ import annotations

import json
import random

from ..env import REPO_ROOT
from ..llm import read_jsonl
from . import CANDIDATES_DIR
from .draw import OUT as DRAWS
from .queue import BATCHES_DIR

SEED = 20261006
FIRST_ID = 37  # b01 used cf-0001 to cf-0036
CROSSOVER = 12
DECLARATIONS = 2
SCREENING = CANDIDATES_DIR / "declarations-triage" / "screening.jsonl"
NO_SOURCE_WORK = {"gci#18": "the catalogue entry cites no published work, so there is no source work to label"}
# Seeds the drafter could not make into a sound packet: the ID keeps its number and is not reused, and the next
# member of the same seeded order takes a new ID after the last one (the b01 rule).
NOT_DRAFTED = {"W2032600016": ("cf-0048", "the later work of the declared pair could not be resolved to a usable "
                                          "identifier (draft of 2026-10-07)")}
OUT = BATCHES_DIR / "b02-plan.json"


def members() -> list[dict]:
    draws = json.loads(DRAWS.read_text(encoding="utf-8"))
    queue = read_jsonl(CANDIDATES_DIR / "queue.jsonl")
    # `ready` when the plan was made; once b02 exists the queue marks them in that batch, and the plan stays the same
    out = [{"kind": "kulikowski", "role": "positive", "seed": r["ref"]} for r in queue
           if r["source"] == "kulikowski" and r["role"] == "positive"
           and (r["status"] == "ready" or r.get("batch") == "b02")]
    out += [{"kind": "kulikowski", "role": "attributed_control", "seed": ref} for ref in draws["controls"]["picked"]]
    out += [{"kind": "ai_origin", "role": "ai_origin", "seed": ref}
            for ref in draws["ai_origin"]["picked_all_fields_readable"]]
    negatives = draws["natural_negatives"]
    picked = [ref for ref in negatives["picked"] if ref not in NO_SOURCE_WORK]
    spare = [ref for ref in negatives["eligible_order"] if ref not in picked]
    while len(picked) < len(negatives["picked"]) and spare:
        picked.append(spare.pop(0))
    out += [{"kind": "catalogue" if ref.startswith("gci#") else "kulikowski", "role": "natural_distractor",
             "seed": ref} for ref in picked]
    usable = sorted(r["openalex"] for r in read_jsonl(SCREENING) if r["check"] == "cross_field_specific")
    random.Random(f"{SEED}:b02:declarations").shuffle(usable)
    out += [{"kind": "declaration", "role": "positive", "seed": ref} for ref in usable[:DECLARATIONS]]
    return out


def replacements() -> list[dict]:
    """The next usable screened declarations in the seeded order, one for each declaration in NOT_DRAFTED."""
    usable = sorted(r["openalex"] for r in read_jsonl(SCREENING) if r["check"] == "cross_field_specific")
    random.Random(f"{SEED}:b02:declarations").shuffle(usable)
    spare = [ref for ref in usable[DECLARATIONS:] if ref not in NOT_DRAFTED]
    n = sum(1 for ref in NOT_DRAFTED if ref in usable[:DECLARATIONS])
    return [{"kind": "declaration", "role": "positive", "seed": ref} for ref in spare[:n]]


def plan() -> dict:
    items = sorted(members(), key=lambda m: m["seed"])
    random.Random(f"{SEED}:b02:ids").shuffle(items)
    for n, m in enumerate(items, FIRST_ID):
        m["id"] = f"cf-{n:04d}"
    ids = [m["id"] for m in items]
    cross = sorted(ids)
    random.Random(f"{SEED}:b02:crossover").shuffle(cross)
    cross = cross[:CROSSOVER]
    excluded = []
    for m in items:
        if m["seed"] in NOT_DRAFTED:
            excluded.append({"id": m["id"], "seed": m["seed"], "reason": NOT_DRAFTED[m["seed"]][1]})
    items = [m for m in items if m["seed"] not in NOT_DRAFTED]
    for n, m in enumerate(replacements(), FIRST_ID + len(ids)):
        m["id"] = f"cf-{n:04d}"
        m["replaces"] = excluded[0]["id"] if excluded else None
        items.append(m)
    return {"batch": "b02", "seed": SEED, "items": sorted(items, key=lambda m: m["id"]),
            "replaced": NO_SOURCE_WORK, "not_drafted": excluded,
            "crossover": {"X_prefilled_for_annotator_1": sorted(cross[:CROSSOVER // 2]),
                          "Y_prefilled_for_annotator_2": sorted(cross[CROSSOVER // 2:])}}


def main() -> int:
    out = plan()
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {}
    for m in out["items"]:
        counts[m["role"]] = counts.get(m["role"], 0) + 1
    print(len(out["items"]), "items", counts, "->", OUT.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
