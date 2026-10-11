"""The frozen items plus the works the author adjudicated as gold, for scoring (paper Section 7).

    PYTHONPATH=src python eval/results/measurement-a/add_named_gold.py

Protocol decision 1 makes every work a model named closed-book, that resolves and predates the claim, a candidate of its
item, judged like any candidate, and decision 17 has the author adjudicate every one rated `similar_mechanism` or
stronger. The freeze tool (tools/freeze/freeze_items.py) built `eval/benchmark/v0/items.jsonl` from the quiz labels only,
so the 14 named works the author adjudicated as gold on 2026-10-11 (before any system ran) were not among the items'
candidates. This script appends them, with `gold` true, the author's relation and cross-field answer and
`found_by: ["closed_book_named"]`, and writes eval/results/measurement-a/items-with-named.jsonl. The frozen file is not
changed (its hash is in the splits file; views are unchanged by this). Item verdicts are checked with
`items.derive_verdict` and must not change; the script stops if one does.

Both scorings are kept: scores/ is against the frozen items alone, scores-with-named/ against this file.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from coelacanth.benchmark.items import derive_verdict

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def main() -> int:
    items = [json.loads(l) for l in (REPO / "eval/benchmark/v0/items.jsonl").read_text(encoding="utf-8").splitlines()
             if l.strip()]
    final = json.loads((REPO / "eval/benchmark/v0/checks/named-final.json").read_text(encoding="utf-8"))
    named = json.loads((REPO / "eval/results/closed-book-v0/named-works.json").read_text(encoding="utf-8"))
    by = {i["id"]: i for i in items}
    added = {}
    for wid, w in final["works"].items():
        if not w["gold"]:
            continue
        item, oid = wid.split("/")
        resolved = next(r["resolved"] for r in named[item]["lines"]
                        if (r.get("resolved") or {}).get("openalex") == oid)
        before = derive_verdict({"candidates": by[item]["candidates"]})
        by[item]["candidates"].append({
            "work": {"openalex": oid, "doi": resolved.get("doi"), "title": resolved.get("title"),
                     "first_author": resolved.get("first_author"),
                     "year": int((resolved.get("publication_date") or "0")[:4]) or None},
            "relation": w["relation"], "cross_field": w["cross_field"] == "yes", "gold": True,
            "found_by": ["closed_book_named"], "adjudicated_by": w["by"]})
        if derive_verdict({"candidates": by[item]["candidates"]}) != before:
            print(f"{item}: the verdict would change; stopping")
            return 1
        added[item] = added.get(item, 0) + 1
    out = HERE / "items-with-named.jsonl"
    out.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    print(f"{out.relative_to(REPO)}: added {sum(added.values())} gold works {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
