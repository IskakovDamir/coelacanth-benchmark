"""Import the annotators' returned check files (tools/checks/make_checks.py) and merge them.

    PYTHONPATH=src python tools/checks/import_checks.py attribution-a1.json claimcheck-a2.json ...

Each file is checked against eval/benchmark/v0/checks/assignment.json (kind, annotator, the page's data hash, the
entries on that page, an answer from the page's options, a fixed text when the answer is "fix", and who answered),
stored unchanged under eval/benchmark/v0/checks/returned/, and merged:

- attribution.json: per pair, the readings and a value when both readings agree on cited or not_cited; every other
  pair (a disagreement, "cannot_check", a missing reading) is listed for Damir, who decides it in
  attribution-decisions.json ({"decisions": {"cf-0007/c1": {"value": "cited", "reason": "...", "decided_by": "..."}}};
  value cited, not_cited or unknown, an unknown pair never puts an item in R). Then R per item:
  an item that could be in R (labels.json) is in R when at least one cross-field gold pair is not cited by people's
  decision; R is final only when no pair is pending.
- claimcheck.json: per item, ok or the fixed text, with who answered; items with a fixed text are probed again
  closed-book (eval/results/closed-book-v0/README.md).

A file with a problem is not stored and every problem is printed (exit code 1).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKS = REPO / "eval" / "benchmark" / "v0" / "checks"
LABELS = REPO / "eval" / "benchmark" / "v0" / "quiz" / "labels.json"
OPTIONS = {"attribution": {"cited", "not_cited", "cannot_check"}, "claimcheck": {"ok", "fix"}}


def problems(data: dict, assignment: dict) -> list[str]:
    out = []
    kind, ann = data.get("kind"), data.get("annotator")
    if data.get("format") != "coelacanth-checks/v1":
        out.append(f"format {data.get('format')!r}")
    page = ((assignment["kinds"].get(kind) or {}).get("pages") or {}).get(ann)
    if not page:
        return out + [f"no page for kind {kind!r} and annotator {ann!r}"]
    if data.get("page_hash") != page["data_hash"]:
        out.append("page hash differs from the page sent")
    if not (data.get("answered_by") or "").strip():
        out.append("answered_by is empty")
    answers = data.get("answers") or {}
    for eid in sorted(set(answers) - set(page["ids"])):
        out.append(f"{eid}: not on this page")
    for eid in page["ids"]:
        a = answers.get(eid) or {}
        if a.get("answer") not in OPTIONS[kind]:
            out.append(f"{eid}: no answer")
        elif kind == "claimcheck" and a["answer"] == "fix" and not (a.get("fixed_text") or "").strip():
            out.append(f"{eid}: 'fix' without a fixed text")
    return out


def merge_attribution(returned: list[dict]) -> dict:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    readings: dict[str, list[dict]] = {}
    for data in returned:
        for eid, a in data["answers"].items():
            readings.setdefault(eid, []).append({"annotator": data["annotator"], "answered_by": data["answered_by"],
                                                 "value": a["answer"], "note": a.get("note", "")})
    decisions_path = CHECKS / "attribution-decisions.json"
    decided = json.loads(decisions_path.read_text(encoding="utf-8"))["decisions"] if decisions_path.exists() else {}
    pairs, pending = {}, []
    for item, v in sorted(labels["items"].items()):
        if v["origin_kind"] != "human" or v["role"] == "attributed_control" or v["negative"]:
            continue
        for cid in v["cross_field_gold_candidates"]:
            eid = f"{item}/{cid}"
            rs = readings.get(eid, [])
            values = {r["value"] for r in rs}
            if len(rs) >= 2 and len(values) == 1 and values <= {"cited", "not_cited"}:
                final, by = rs[0]["value"], "agreed: " + ",".join(r["annotator"] for r in rs)
            elif eid in decided:
                final, by = decided[eid]["value"], decided[eid].get("decided_by", "adjudicated: damir")
            else:
                final, by = None, None
                pending.append(eid)
            pairs[eid] = {"readings": rs, "value": final, "decided_by": by,
                          "reason": decided.get(eid, {}).get("reason") if eid in decided else None}
    items = {}
    for eid, p in pairs.items():
        item = eid.split("/")[0]
        items.setdefault(item, {"pairs": [], "in_R": False, "pending": False})
        items[item]["pairs"].append(eid)
        items[item]["in_R"] |= p["value"] == "not_cited"
        items[item]["pending"] |= p["value"] is None
    return {"pairs": pairs, "pending": pending, "items": items,
            "R": sorted(i for i, v in items.items() if v["in_R"]),
            "R_final": not pending}


def merge_claimcheck(returned: list[dict]) -> dict:
    out = {}
    for data in returned:
        for eid, a in data["answers"].items():
            out[eid] = {"answer": a["answer"], "fixed_text": (a.get("fixed_text") or "").strip() or None,
                        "note": a.get("note", ""), "annotator": data["annotator"], "answered_by": data["answered_by"]}
    return {"items": dict(sorted(out.items())), "fixed": sorted(k for k, v in out.items() if v["answer"] == "fix")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    assignment = json.loads((CHECKS / "assignment.json").read_text(encoding="utf-8"))
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
        dest = CHECKS / "returned" / f"{data['kind']}-{data['annotator']}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() != raw:
            print(f"{f}: {dest.name} already stored with other content; first returns are kept")
            bad = True
            continue
        dest.write_bytes(raw)
        print(f"{f}: stored as {dest.name} (sha256 {hashlib.sha256(raw).hexdigest()[:12]})")
    returned = {k: [json.loads(p.read_text(encoding="utf-8")) for p in sorted((CHECKS / "returned").glob(f"{k}-*.json"))]
                for k in OPTIONS} if (CHECKS / "returned").exists() else {k: [] for k in OPTIONS}
    if returned["attribution"]:
        att = merge_attribution(returned["attribution"])
        (CHECKS / "attribution.json").write_text(json.dumps(att, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"attribution: {len(att['pairs'])} pairs, {len(att['pending'])} pending for Damir; R {len(att['R'])} "
              f"({'final' if att['R_final'] else 'not final'}): {', '.join(att['R'])}")
    if returned["claimcheck"]:
        cc = merge_claimcheck(returned["claimcheck"])
        (CHECKS / "claimcheck.json").write_text(json.dumps(cc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"claimcheck: {len(cc['items'])} items checked, {len(cc['fixed'])} with a fixed text: {', '.join(cc['fixed'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
