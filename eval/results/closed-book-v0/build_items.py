"""Build the v0 closed-book items (items.jsonl) from the annotation batches, and carry over the declarations pilot's
answers for items whose claim text and date are unchanged.

Items: every packet of the batches named on the command line whose hidden meta has no `negative` (eval/protocol.md
section 3: rediscoveries, `C` and `A` are probed; negatives are not), plus, with `--labels`, every built negative the
people found gold for (it is a positive then, SCHEMA.md rule 7). The text and date are the packet's
`claim.text` and `claim.date`: Claude's pre-filled drafts, not yet checked by a person (Damir's decision of
2026-10-10: probe now, re-probe any item whose text changes at the check). Candidates are stored with their dating
facts; which of them are gold is set at scoring time from the adjudicated labels.

Carry-over: the declarations pilot (`../closed-book-declarations-pilot/`) ran the protocol's settings (frozen prompt
samples 1 to 5, cross-field prompt samples 1 to 3, the three probed models) on 13 b01 items. For each of them whose
text and date equal the packet's, its answers are copied into answers.jsonl with `reused_from`; the runner then skips
those calls. Their cost stays booked in the pilot's ledger.

    PYTHONPATH=src python eval/results/closed-book-v0/build_items.py eval/benchmark/v0/batches/b01.json eval/benchmark/v0/batches/b02.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coelacanth.env import REPO_ROOT
from coelacanth.llm import read_jsonl
from coelacanth.mining import write_jsonl

HERE = Path(__file__).resolve().parent
PILOT = REPO_ROOT / "eval" / "results" / "closed-book-declarations-pilot"
WORK_KEYS = ("openalex", "doi", "arxiv", "pmid", "title", "first_author", "year", "publication_date", "field")


def build(batches: list[Path], with_gold: frozenset = frozenset()) -> tuple[list[dict], list[str]]:
    """`with_gold`: built negatives that labelling found gold for; they are positives now (SCHEMA.md rule 7) and
    are probed."""
    items, skipped = [], []
    for path in batches:
        batch = json.loads(path.read_text(encoding="utf-8"))
        meta = json.loads(path.with_name(path.stem + "-meta.json").read_text(encoding="utf-8"))["items"]
        for packet in batch["items"]:
            hidden = meta[packet["id"]]
            if hidden.get("negative") and packet["id"] not in with_gold:
                skipped.append(packet["id"])
                continue
            claim = packet["claim"]
            items.append({
                "pid": packet["id"], "batch": batch["batch"], "source": hidden["provenance"]["source"],
                "origin": hidden["origin"]["kind"], "role": (hidden.get("seed_detail") or {}).get("role"),
                "text": claim["text"], "date": claim["date"], "claim_field": claim["field"],
                "claim_source": {k: claim["source_work"].get(k) for k in WORK_KEYS},
                "candidates": [{"cid": c["cid"], **{k: c["work"].get(k) for k in WORK_KEYS}}
                               for c in packet["candidates"]],
                "text_checked_by": None})
    return items, skipped


def carry_over(items: list[dict]) -> list[dict]:
    pilot_items = {i["pid"]: i for i in read_jsonl(PILOT / "items.jsonl")}
    same = {i["pid"] for i in items
            if i["pid"] in pilot_items and pilot_items[i["pid"]]["text"] == i["text"]
            and pilot_items[i["pid"]]["date"] == i["date"]}
    return [{**a, "reused_from": "closed-book-declarations-pilot"} for a in read_jsonl(PILOT / "answers.jsonl")
            if a["pid"] in same]


def add_gold(items: list[dict], labels: dict) -> list[dict]:
    """Each item's `gold`: its candidates the people labelled `partial` or stronger (labels.json `gold_candidates`),
    with `cross_field` as the people answered it; an item outside the quiz (AI origin) has none yet."""
    out = []
    for item in items:
        entry = labels["items"].get(item["pid"])
        cross = set(entry["cross_field_gold_candidates"]) if entry else set()
        gold = [{**c, "kind": "labelled_gold", "cross_field": c["cid"] in cross}
                for c in item["candidates"] if entry and c["cid"] in entry["gold_candidates"]]
        out.append({**item, "gold": gold, "in_R_provisional": bool(entry and entry["in_R_provisional"]),
                    "labels_hash": labels.get("decisions_template_hash")})
    return out


def apply_texts(items: list[dict], texts: dict) -> list[dict]:
    """Checked claim texts replace the drafts; answers given to a text that is no longer the item's text are moved
    from answers.jsonl to answers-superseded.jsonl (kept, never scored), so the runner probes the new text."""
    before = {i["pid"]: i["text"] for i in read_jsonl(HERE / "items.jsonl")} if (HERE / "items.jsonl").exists() else {}
    out, changed = [], set()
    for item in items:
        t = texts["items"].get(item["pid"])
        if t:
            item = {**item, "text": t["text"], "text_checked_by": t["checked_by"],
                    "text_decided_by": t.get("decided_by")}
        if item["pid"] in before and before[item["pid"]] != item["text"]:
            changed.add(item["pid"])
        out.append(item)
    if changed:
        answers = read_jsonl(HERE / "answers.jsonl")
        keep = [a for a in answers if a["pid"] not in changed]
        moved = [{**a, "superseded": "claim text changed at the annotators' check (claim-texts.json)"}
                 for a in answers if a["pid"] in changed]
        with (HERE / "answers-superseded.jsonl").open("a", encoding="utf-8") as f:
            for a in moved:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        write_jsonl(HERE / "answers.jsonl", keep)
        print(f"text changed for {len(changed)} items ({', '.join(sorted(changed))}): {len(moved)} answers superseded")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batches", nargs="+")
    parser.add_argument("--no-carry-over", action="store_true")
    parser.add_argument("--labels", help="labels.json: add each item's gold and rewrite items.jsonl only")
    parser.add_argument("--attribution", help="checks/attribution.json: R from people's attribution answers")
    parser.add_argument("--claim-texts", help="claim-texts.json: the checked claim texts; an item whose text changed "
                                              "has its earlier answers moved to answers-superseded.jsonl, to be probed again")
    args = parser.parse_args()
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8")) if args.labels else None
    with_gold = frozenset(k for k, v in (labels or {}).get("items", {}).items() if v["negative"] and v["gold_candidates"])
    items, skipped = build([Path(b) for b in args.batches], with_gold)
    if labels:
        items = add_gold(items, labels)
        if args.claim_texts:
            items = apply_texts(items, json.loads(Path(args.claim_texts).read_text(encoding="utf-8")))
        if args.attribution:
            att = json.loads(Path(args.attribution).read_text(encoding="utf-8"))
            # R with people's attribution in place of the packets' model hints (eval/benchmark/v0/checks/)
            items = [{**i, "in_R_provisional": i["pid"] in att["R"] if i["pid"] in att["items"] or
                      i["in_R_provisional"] else False, "R_basis": "checks/attribution.json"
                      if i["pid"] in att["items"] else "labels.json"} for i in items]
        write_jsonl(HERE / "items.jsonl", items)
        print(f"{len(items)} items with gold from {args.labels}: {sum(bool(i['gold']) for i in items)} have gold, "
              f"{sum(any(g['cross_field'] for g in i['gold']) for i in items)} cross-field gold, "
              f"{sum(i['in_R_provisional'] for i in items)} in R (provisional)")
        return 0
    write_jsonl(HERE / "items.jsonl", items)
    print(f"{len(items)} items written; {len(skipped)} negatives not probed: {', '.join(skipped)}")
    answers = HERE / "answers.jsonl"
    if not args.no_carry_over:
        if answers.exists():
            raise SystemExit("answers.jsonl exists; carry-over is done once, before the first call")
        rows = carry_over(items)
        write_jsonl(answers, rows)
        print(f"{len(rows)} answers carried over for {len({r['pid'] for r in rows})} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
