"""The works the scored agents retrieved that are not in the adjudicated gold, for later judging (paper Section 7).

    PYTHONPATH=src python eval/results/measurement-a/unjudged.py

Reads every scored run's resolved.jsonl (eval/results/measurement-a/runs/*/*/*/) and eval/benchmark/v0/items.jsonl and
writes unjudged-candidates.jsonl beside this file: one row per cleaned candidate (not a leak, not a duplicate) that
matches no gold work of its item, with identifiers only (OpenAlex ID, DOI, arXiv ID, date, the relation the agent
gave, how it was resolved). Model free text and third-party content are not copied. Unresolved candidates (no
identifier) are listed too, with `placeholder` true, because they cannot be judged without a lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def main() -> int:
    items = {}
    for line in (REPO / "eval" / "benchmark" / "v0" / "items.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            it = json.loads(line)
            items[it["id"]] = it
    rows = []
    for run in sorted((HERE / "runs").glob("*/*/*/")):
        meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
        path = run / "resolved.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            gold = {c["work"].get("openalex") for c in items[r["item"]].get("candidates", []) if c.get("gold")}
            for c in r["candidates"]:
                if c.get("leak") or c.get("duplicate_of") or c.get("openalex") in gold and c.get("openalex"):
                    continue
                rows.append({"system": meta["system"], "condition": meta["condition"], "tier": meta["tier"],
                             "run_id": meta.get("run_id") or run.name, "item": r["item"],
                             "rank_clean": c.get("rank_clean"), "openalex": c.get("openalex"), "doi": c.get("doi"),
                             "arxiv": c.get("arxiv"), "date": c.get("date"), "resolved_by": c.get("resolved_by"),
                             "relation_native": c.get("relation_native"), "placeholder": bool(c.get("placeholder"))})
    out = HERE / "unjudged-candidates.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    by = {}
    for r in rows:
        by.setdefault((r["system"], r["condition"], r["tier"]), 0)
        by[(r["system"], r["condition"], r["tier"])] += 1
    print(f"{out.relative_to(REPO)}: {len(rows)} candidates")
    for k, v in sorted(by.items()):
        print("  ", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
