"""Import the annotators' judgments of the works the models named closed-book (pages from tools/checks/make_named.py).

    PYTHONPATH=src python tools/checks/import_named.py named-a1.json named-a2.json

Each file (format coelacanth-named-works/v1) is checked against eval/benchmark/v0/checks/named-assignment.json: a known
annotator, the page's data hash, exactly the works on that page, a relation from the page's options and a cross-field
answer (yes or no). A valid file is stored unchanged under eval/benchmark/v0/checks/returned/named-<annotator>.json;
a second, different file for the same annotator is refused, because the first return is kept.

`answered_by` is free text typed on the page. It is checked only for being non-empty and is recorded as typed, next to
`provenance_file`, which says how the answers were made; the importer does not turn it into a claim about who answered.

The merged record is eval/benchmark/v0/checks/named.json: one reading per work (the item's annotator), whether the
work is gold (a relation in PRECEDENCE, that is anything but `different`) and cross-field gold (gold and cross-field
yes), and `for_damir`, every gold-rated work, which Damir adjudicates (eval/protocol.md decision 17). Nothing here
changes `H`: that waits for the adjudication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from coelacanth.benchmark.items import PRECEDENCE, RELATIONS  # noqa: E402

CHECKS = REPO / "eval" / "benchmark" / "v0" / "checks"
FORMAT = "coelacanth-named-works/v1"
MERGED_FORMAT = "coelacanth-named-judgments/v1"
PROVENANCE = CHECKS / "returned" / "named-PROVENANCE.md"


def problems(data: dict, assignment: dict) -> list[str]:
    out = []
    ann = data.get("annotator")
    if data.get("format") != FORMAT:
        out.append(f"format {data.get('format')!r}")
    page = assignment.get(ann)
    if not isinstance(page, dict):
        return out + [f"no page for annotator {ann!r}"]
    if data.get("page_hash") != page["data_hash"]:
        out.append("page hash differs from the page sent")
    if not (data.get("answered_by") or "").strip():
        out.append("answered_by is empty")
    answers = data.get("answers") or {}
    for wid in sorted(set(answers) - set(page["ids"])):
        out.append(f"{wid}: not on this page")
    for wid in page["ids"]:
        a = answers.get(wid) or {}
        if a.get("relation") not in RELATIONS:
            out.append(f"{wid}: no valid relation")
        if a.get("cross_field") not in ("yes", "no"):
            out.append(f"{wid}: no cross-field answer")
    return out


def merge(returned: list[dict]) -> dict:
    works = {}
    for data in returned:
        for wid, a in data["answers"].items():
            gold = a["relation"] in PRECEDENCE
            works[wid] = {"item": wid.split("/")[0], "annotator": data["annotator"],
                          "answered_by_in_file": data["answered_by"], "relation": a["relation"],
                          "cross_field": a["cross_field"], "note": a.get("note", ""),
                          "gold": gold, "cross_field_gold": gold and a["cross_field"] == "yes"}
    works = dict(sorted(works.items()))
    by_item: dict[str, dict] = {}
    for w in works.values():
        s = by_item.setdefault(w["item"], {"works": 0, "gold": 0, "cross_field_gold": 0})
        s["works"] += 1
        s["gold"] += w["gold"]
        s["cross_field_gold"] += w["cross_field_gold"]
    return {"format": MERGED_FORMAT,
            "note": "One reading per work, by the item's annotator. Gold means a relation in PRECEDENCE (anything but "
                    "`different`). Damir adjudicates every gold-rated work (protocol decision 17); H is not recomputed "
                    "from this file.",
            "provenance_file": str(PROVENANCE.relative_to(REPO)) if PROVENANCE.exists() else None,
            "works": works, "by_item": by_item,
            "for_damir": [wid for wid, w in works.items() if w["gold"]]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    assignment = json.loads((CHECKS / "named-assignment.json").read_text(encoding="utf-8"))
    bad = False
    for f in args.files:
        raw = Path(f).read_bytes()
        data = json.loads(raw)
        errs = problems(data, assignment)
        if errs:
            bad = True
            print(f"{f}: not stored")
            for e in errs:
                print("  " + e)
            continue
        dest = CHECKS / "returned" / f"named-{data['annotator']}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() != raw:
            print(f"{f}: {dest.name} already stored with other content; first returns are kept")
            bad = True
            continue
        dest.write_bytes(raw)
        print(f"{f}: stored as {dest.name} (sha256 {hashlib.sha256(raw).hexdigest()[:12]})")
    stored = sorted((CHECKS / "returned").glob("named-a*.json")) if (CHECKS / "returned").exists() else []
    if stored:
        merged = merge([json.loads(p.read_text(encoding="utf-8")) for p in stored])
        (CHECKS / "named.json").write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for item, s in sorted(merged["by_item"].items()):
            print(f"{item}: {s['works']} works, {s['gold']} rated gold, {s['cross_field_gold']} cross-field gold")
        print(f"for Damir: {len(merged['for_damir'])} gold-rated works to adjudicate")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
