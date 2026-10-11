"""Check and apply Damir's decisions on the works the models named closed-book (page: make_named_decisions.py).

    PYTHONPATH=src python tools/checks/import_named_decisions.py ~/Downloads/named-decisions.json

The file (format coelacanth-named-decisions/v1, decided_by damir) is checked against the page it came from (its hash is
recomputed from the same entries), every work on the page must have a relation, a cross-field answer and a reason, and
the file is stored unchanged as eval/benchmark/v0/checks/returned/named-decisions.json (a different file is refused
unless --replace; a partial file is refused unless --partial).

It then writes eval/benchmark/v0/checks/named-final.json: for every named work the final relation and cross-field
answer (Damir's decision where there is one, otherwise the student's reading, which is `different` for every work
nobody rated gold), per item the number of works, gold works and cross-field gold works, and `H` before and after:
protocol decision 1 and section 3 put an item outside `H` when a work a model named closed-book is cross-field gold.
Nothing else changes: `R` is not touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import make_named_decisions  # noqa: E402
from coelacanth.benchmark.items import PRECEDENCE, RELATIONS  # noqa: E402

REPO = make_named_decisions.REPO
CHECKS = make_named_decisions.CHECKS
FORMAT = "coelacanth-named-decisions/v1"
FINAL_FORMAT = "coelacanth-named-final/v1"
RESULTS = REPO / "eval" / "results" / "closed-book-v0" / "results.json"


def page_hash() -> str:
    data = {"entries": make_named_decisions.entries(), "relations": make_named_decisions.make_named.RELATIONS}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def problems(data: dict, ids: list[str], expected_hash: str, partial: bool = False) -> list[str]:
    out = []
    if data.get("format") != FORMAT:
        out.append(f"format {data.get('format')!r}")
    if data.get("decided_by") != "damir":
        out.append(f"decided_by {data.get('decided_by')!r}, expected 'damir'")
    if data.get("page_hash") != expected_hash:
        out.append("page hash differs from the page built from the current files")
    decisions = data.get("decisions") or {}
    for wid in sorted(set(decisions) - set(ids)):
        out.append(f"{wid}: not on the page")
    for wid in ids:
        d = decisions.get(wid)
        if d is None:
            if not partial:
                out.append(f"{wid}: no decision")
            continue
        if d.get("relation") not in RELATIONS:
            out.append(f"{wid}: no valid relation")
        if d.get("cross_field") not in ("yes", "no"):
            out.append(f"{wid}: no cross-field answer")
        if not (d.get("reason") or "").strip():
            out.append(f"{wid}: no reason")
    return out


def finalize(merged: dict, decisions: dict, h_before: list[str]) -> dict:
    """Final reading of every named work and `H` after them. `merged` is named.json, `decisions` Damir's."""
    works = {}
    for wid, w in merged["works"].items():
        d = decisions.get(wid)
        relation, cross, by = (d["relation"], d["cross_field"], "damir") if d else (w["relation"], w["cross_field"], w["annotator"])
        works[wid] = {"item": w["item"], "relation": relation, "cross_field": cross, "by": by,
                      "student_relation": w["relation"], "student_cross_field": w["cross_field"],
                      "gold": relation in PRECEDENCE, "cross_field_gold": relation in PRECEDENCE and cross == "yes"}
    for wid, d in decisions.items():  # works nobody had judged (no student reading in named.json)
        if wid not in works:
            item = wid.split("/")[0]
            gold = d["relation"] in PRECEDENCE
            works[wid] = {"item": item, "relation": d["relation"], "cross_field": d["cross_field"], "by": "damir",
                          "student_relation": None, "student_cross_field": None, "gold": gold,
                          "cross_field_gold": gold and d["cross_field"] == "yes"}
    by_item: dict[str, dict] = {}
    for w in works.values():
        s = by_item.setdefault(w["item"], {"works": 0, "gold": 0, "cross_field_gold": 0})
        s["works"] += 1
        s["gold"] += w["gold"]
        s["cross_field_gold"] += w["cross_field_gold"]
    out_of_h = sorted(i for i in h_before if by_item.get(i, {}).get("cross_field_gold"))
    changed = [wid for wid, d in decisions.items() if works[wid]["student_relation"] is not None
               and (d["relation"], d["cross_field"]) != (works[wid]["student_relation"], works[wid]["student_cross_field"])]
    return {"format": FINAL_FORMAT, "works": dict(sorted(works.items())), "by_item": dict(sorted(by_item.items())),
            "H_before": sorted(h_before), "H_after": sorted(set(h_before) - set(out_of_h)), "items_out_of_H": out_of_h,
            "decided": len(decisions), "changed_from_student": sorted(changed)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    raw = Path(args.file).read_bytes()
    data = json.loads(raw)
    ids = [e["id"] for e in make_named_decisions.entries()]
    errs = problems(data, ids, page_hash(), args.partial)
    if errs:
        print(f"{args.file}: not stored")
        for e in errs:
            print("  " + e)
        return 1
    dest = CHECKS / "returned" / "named-decisions.json"
    if dest.exists() and dest.read_bytes() != raw and not args.replace:
        print(f"{dest.name} already stored with other content; pass --replace to overwrite it")
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    merged = json.loads((CHECKS / "named.json").read_text(encoding="utf-8"))
    h_before = json.loads(RESULTS.read_text(encoding="utf-8"))["summary"]["low"]["H"]
    final = finalize(merged, data["decisions"], h_before)
    (CHECKS / "named-final.json").write_text(json.dumps(final, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"stored as {dest.name} (sha256 {hashlib.sha256(raw).hexdigest()[:12]}); {final['decided']} decisions, "
          f"{len(final['changed_from_student'])} differ from the student's reading")
    for item, s in final["by_item"].items():
        print(f"{item}: {s['works']} works, {s['gold']} gold, {s['cross_field_gold']} cross-field gold")
    print(f"H before {final['H_before']}; after {final['H_after']}; out of H: {final['items_out_of_H']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
