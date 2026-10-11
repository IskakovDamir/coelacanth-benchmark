"""Assign a batch to annotators: one personal copy per annotator, every item labelled in full by two of them.

The rule keeps the protocol's design for any number of annotators and never lowers it:
- every item of the batch is labelled in full by exactly two different annotators;
- the first 25 items by ID of b01, the shared reliability set (eval/protocol.md decision 18 and section 11), go to the
  first two annotators named (a1 and a2);
- a batch with a crossover plan (decision 25, `<batch>-plan.json`) gives its crossover items to a1 and a2, group X bare
  for a2 and group Y bare for a1, exactly as `annotation.crossover` makes them (built with `annotation.strip_prefill`);
- the other items set each annotator's final load: given one by one to the two annotators with the fewest items (of
  those, the pair that has shared the fewest), they leave the loads within one item of each other, unless the items fixed to a1 and a2 make that impossible (then the
  largest load is as small as it can be, and the smallest as large). Then, in ID order, each of these items goes to the
  pair that has shared the fewest items so far, then to the pair with the fewest items so far (then the order the
  annotators were named in), among the pairs that still let every annotator reach their final load. Pairs rotate: with
  up to five annotators and no fixed items, no pair shares more than one item more than another.
  With two annotators every item goes to both, so each copy holds the whole batch (with a plan, the same copies as
  `annotation.crossover`).

Files written to --out-dir (default: `assigned/` next to the batch):
- `<batch>-<annotator>.json`: the copy to send to that annotator, the batch's items assigned to them in the batch's
  order, crossover items bare where the plan says. It names its annotator (`"annotator": "a3"`) like the crossover
  copies, so tools/annotate.html keeps its work apart from other copies and check-labels refuses another annotator's
  labels against it;
- `<batch>-assignment.json`, for Damir: each item's two annotators, for whom it is bare and why it went to them, and
  each annotator's load;
- `<batch>-tracker.csv`, for Damir, opens in a spreadsheet: one row per annotator and item (annotator, item, status,
  minutes, sent, returned, checked), status `assigned`, the other columns empty. An existing tracker for the same
  assignment is kept as it is (it may hold Damir's entries); one for another assignment stops the run before anything
  is written.
Nothing written depends on the time of the run, so the same inputs always give the same files.

Then check-batch runs on every copy, with the meta checks against `<batch>-meta.json` next to the source batch (a copy
in assigned/ has no meta file next to it), and every copy is compared with the assignment. The printed report gives
each annotator's items and an hours estimate at docs/annotator-brief.md's first-pass rate (46.9 hours for 80 items,
24.1 to 89.9).

    PYTHONPATH=src python -m coelacanth.benchmark.assign eval/benchmark/v0/batches/b01.json --annotators a1 a2
    PYTHONPATH=src python -m coelacanth.benchmark.assign eval/benchmark/v0/batches/b02.json --annotators a1 a2 a3 \\
        --plan eval/benchmark/v0/batches/b02-plan.json

Without --plan, a `<batch>-plan.json` with crossover groups next to the batch is used, and the report says so: a
crossover batch is never assigned without its crossover.
"""

from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import re
from itertools import combinations
from pathlib import Path
from typing import Mapping, Sequence

from .annotation import check_batch, crossover, is_bare, meta_problems, strip_prefill

ASSIGNMENT_FORMAT = "coelacanth-assignment/v1"
# Items shared by a1 and a2 by batch: the first N by ID (decision 18: the 25 first items of b01).
SHARED_ITEMS = {"b01": 25}
# What tools/annotate.html accepts as an annotator ID (ANNOTATOR_INPUT_RE); stricter than annotation.ANNOTATOR_RE.
ANNOTATOR_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
TRACKER_COLUMNS = ("annotator", "item", "status", "minutes", "sent", "returned", "checked")
TRACKER_STATUS = "assigned"
# docs/annotator-brief.md, "Hours for about 80 items": the first pass of 80 items takes 46.9 hours (24.1 to 89.9).
BRIEF_ITEMS = 80
BRIEF_FIRST_PASS_HOURS = {"central": 46.9, "low": 24.1, "high": 89.9}
REASONS = {"shared": "shared reliability set (decision 18)", "crossover": "crossover (decision 25)",
           "rotation": "rotation"}


def annotator_problems(annotators: Sequence[str]) -> list[str]:
    out = []
    if len(annotators) < 2:
        out.append(f"at least two annotators are needed, got {list(annotators)}")
    bad = [a for a in annotators if not (isinstance(a, str) and ANNOTATOR_ID_RE.fullmatch(a))]
    if bad:
        out.append(f"annotator IDs are letters, digits, - and _ (at most 32), such as a1: {bad}")
    if len(set(annotators)) != len(annotators):
        out.append(f"annotator IDs repeat: {list(annotators)}")
    return out


def assign(batch: Mapping, annotators: Sequence[str], plan: Mapping | None = None, shared: int | None = None) -> dict:
    """The assignment of a batch: for each item its two annotators (in the order they were named), for whom it is
    bare and why. `shared` overrides SHARED_ITEMS (the first N items by ID go to the first two annotators). Raises
    ValueError on bad annotator IDs, a batch that is already a personal copy, or a plan that does not fit the batch."""
    problems = annotator_problems(annotators)
    if problems:
        raise ValueError("; ".join(problems))
    annotators = list(annotators)
    if "annotator" in batch:
        raise ValueError(f"this file is already the copy for annotator {batch['annotator']!r}: assign the batch itself")
    name = batch.get("batch")
    if not isinstance(name, str) or not name:
        raise ValueError("the batch has no name")
    ids = [p.get("id") if isinstance(p, Mapping) else None for p in batch.get("items") or []]
    if not ids or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("the batch needs items with unique ids")
    first, second = annotators[0], annotators[1]
    n_shared = SHARED_ITEMS.get(name, 0) if shared is None else shared
    shared_ids = sorted(ids)[:n_shared]
    bare_for: dict[str, list[str]] = {}
    groups = None
    if plan is not None:
        # crossover() checks the plan against the batch and decides who gets which group bare
        copies = crossover(batch, plan, (first, second))
        for annotator in (first, second):
            for packet in copies[annotator]["items"]:
                if is_bare(packet):
                    bare_for.setdefault(packet["id"], []).append(annotator)
        groups = {"X_bare_for": second, "X": list(plan["crossover"]["X_prefilled_for_annotator_1"]),
                  "Y_bare_for": first, "Y": list(plan["crossover"]["Y_prefilled_for_annotator_2"])}
    reasons = {i: "shared" for i in shared_ids}
    reasons.update({i: "crossover" for i in bare_for})
    load = {a: 0 for a in annotators}
    together = {pair: 0 for pair in combinations(annotators, 2)}
    pairs: dict[str, tuple[str, str]] = {}

    def give(item_id: str, pair: tuple[str, str]) -> None:
        pairs[item_id] = pair
        together[pair] += 1
        for a in pair:
            load[a] += 1

    for item_id in sorted(reasons):
        give(item_id, (first, second))
    free = sorted(set(ids) - set(reasons))
    # The final loads: each free item to the two annotators with the fewest items, and of those to the pair that has
    # shared the fewest. min() keeps the first of equal pairs; combinations() yields them in the order named.
    final, shared_so_far = dict(load), dict(together)
    for _ in free:
        pair = min(shared_so_far, key=lambda p: (final[p[0]] + final[p[1]], shared_so_far[p]))
        shared_so_far[pair] += 1
        for a in pair:
            final[a] += 1
    left = {a: final[a] - load[a] for a in annotators}

    def completes(pair: tuple[str, str], remaining: int) -> bool:
        """Both still have items to take, and after this item nobody needs more items than remain. While the items
        left to take add up to twice the items that remain and nobody needs more than remain, some pair completes."""
        return bool(left[pair[0]] and left[pair[1]]) and all(left[a] - (a in pair) < remaining for a in annotators)

    for remaining, item_id in zip(range(len(free), 0, -1), free):
        # the pair that has shared the fewest items, then the one with the fewest items so far
        pair = min((p for p in together if completes(p, remaining)),
                   key=lambda p: (together[p], load[p[0]] + load[p[1]]))
        give(item_id, pair)
        for a in pair:
            left[a] -= 1
        reasons[item_id] = "rotation"
    idle = [a for a in annotators if not load[a]]
    if idle:
        raise ValueError(f"annotators {idle} would get no item of batch {name} ({len(ids)} items): name fewer")
    items = [{"id": i, "annotators": list(pairs[i]), "bare_for": bare_for.get(i, []), "reason": REASONS[reasons[i]]}
             for i in ids]
    loads = {a: {"items": load[a], "prefilled": load[a] - sum(a in i["bare_for"] for i in items),
                 "bare": sum(a in i["bare_for"] for i in items)} for a in annotators}
    return {"format": ASSIGNMENT_FORMAT, "batch": name, "annotators": annotators,
            "files": {a: copy_name(name, a) for a in annotators},
            "shared": {"annotators": [first, second], "items": shared_ids}, "crossover": groups,
            "loads": loads, "pairs": {f"{p[0]} {p[1]}": n for p, n in together.items() if n},
            "items": items}


def copy_name(batch: str, annotator: str) -> str:
    return f"{batch}-{annotator}.json"


def personal_copies(batch: Mapping, assignment: Mapping) -> dict[str, dict]:
    """Each annotator's copy of the batch: the batch with the annotator named after its `batch` field (as
    annotation.crossover writes it) and only the items assigned to them, bare (strip_prefill) where the assignment
    says."""
    who = {i["id"]: i for i in assignment["items"]}
    out = {}
    for annotator in assignment["annotators"]:
        data = {}
        for key, value in batch.items():
            data[key] = copy.deepcopy(value) if key != "items" else None
            if key == "batch":
                data["annotator"] = annotator
        data["items"] = [strip_prefill(p) if annotator in who[p["id"]]["bare_for"] else copy.deepcopy(p)
                         for p in batch["items"] if annotator in who[p["id"]]["annotators"]]
        out[annotator] = data
    return out


def copy_problems(data: Mapping, assignment: Mapping, annotator: str) -> list[str]:
    """A written copy against the assignment: its annotator, its items in the batch's order, bare where it should be."""
    out = []
    if data.get("annotator") != annotator:
        out.append(f"the copy names annotator {data.get('annotator')!r}, not {annotator!r}")
    if data.get("batch") != assignment["batch"]:
        out.append(f"the copy is of batch {data.get('batch')!r}, not {assignment['batch']!r}")
    mine = [i for i in assignment["items"] if annotator in i["annotators"]]
    got = [p.get("id") for p in data.get("items") or []]
    if got != [i["id"] for i in mine]:
        out.append(f"the copy holds items {got}, the assignment gives {[i['id'] for i in mine]}")
    else:
        for packet, item in zip(data["items"], mine):
            if is_bare(packet) != (annotator in item["bare_for"]):
                out.append(f"{item['id']} is {'bare' if is_bare(packet) else 'pre-filled'} in the copy, the "
                           f"assignment says {'bare' if annotator in item['bare_for'] else 'pre-filled'}")
    return out


def tracker_rows(assignment: Mapping) -> list[dict]:
    """One row per annotator and item, annotators in the order named, items in the batch's order."""
    return [{"annotator": a, "item": i["id"], "status": TRACKER_STATUS, "minutes": "", "sent": "", "returned": "",
             "checked": ""} for a in assignment["annotators"] for i in assignment["items"] if a in i["annotators"]]


def tracker_csv(assignment: Mapping) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=TRACKER_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(tracker_rows(assignment))
    return buffer.getvalue()


def estimated_hours(items: int) -> dict[str, float]:
    """Hours for `items` items labelled in full, at docs/annotator-brief.md's first-pass rate."""
    return {k: round(items * hours / BRIEF_ITEMS, 1) for k, hours in BRIEF_FIRST_PASS_HOURS.items()}


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(data) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def find_plan(batch_path: Path, batch: Mapping) -> Path | None:
    """`<batch>-plan.json` next to the batch when it has crossover groups, else None."""
    path = Path(batch_path).with_name(f"{batch.get('batch')}-plan.json")
    if not path.exists():
        return None
    return path if (_read_json(path) or {}).get("crossover") else None


def write_assignment(batch_path: Path, annotators: Sequence[str], plan_path: Path | None = None,
                     out_dir: Path | None = None) -> dict:
    """Assign, write the copies, the assignment and the tracker, then check every copy; returns the report. Raises
    ValueError, before writing anything, when the assignment cannot be made or an existing tracker in out_dir belongs
    to another assignment."""
    batch_path = Path(batch_path)
    batch = _read_json(batch_path)
    found = plan_path is None and find_plan(batch_path, batch)
    if found:
        plan_path = found
    plan = _read_json(plan_path) if plan_path else None
    assignment = assign(batch, annotators, plan)
    name = assignment["batch"]
    out_dir = Path(out_dir) if out_dir else batch_path.parent / "assigned"
    tracker_path = out_dir / f"{name}-tracker.csv"
    tracker = tracker_csv(assignment)
    keep_tracker = False
    if tracker_path.exists():
        with tracker_path.open(encoding="utf-8", newline="") as f:
            old = {(r.get("annotator"), r.get("item")) for r in csv.DictReader(f)}
        if old != {(r["annotator"], r["item"]) for r in tracker_rows(assignment)}:
            raise ValueError(f"{tracker_path} belongs to another assignment of {name}; move it (and the copies "
                             "already sent) away first")
        keep_tracker = True
    out_dir.mkdir(parents=True, exist_ok=True)
    copies = personal_copies(batch, assignment)
    for annotator, data in copies.items():
        (out_dir / assignment["files"][annotator]).write_text(_dump(data), encoding="utf-8")
    (out_dir / f"{name}-assignment.json").write_text(_dump(assignment), encoding="utf-8")
    if not keep_tracker:
        tracker_path.write_text(tracker, encoding="utf-8")

    meta_path = batch_path.with_name(f"{name}-meta.json")
    metas = (_read_json(meta_path).get("items") or {}) if meta_path.exists() else None
    if (out_dir / meta_path.name).exists() or (out_dir.parent / meta_path.name).exists():
        metas = None  # check_batch finds the meta file next to the copies or one folder up and checks it itself
    report = {"batch": name, "source": str(batch_path), "out_dir": str(out_dir),
              "plan": None if plan_path is None else f"{plan_path}{' (found next to the batch)' if found else ''}",
              "assignment": f"{name}-assignment.json",
              "tracker": f"{tracker_path.name} ({'kept: it already holds this assignment' if keep_tracker else 'written'})",
              "meta": (str(out_dir / meta_path.name) if (out_dir / meta_path.name).exists()
                       else str(out_dir.parent / meta_path.name) if (out_dir.parent / meta_path.name).exists()
                       else str(meta_path) if metas is not None else f"{meta_path} not found: meta not checked"),
              "pairs": assignment["pairs"], "copies": {}}
    for annotator in assignment["annotators"]:
        path = out_dir / assignment["files"][annotator]
        problems = {k: list(v) for k, v in check_batch(path).items()}
        data = _read_json(path)
        for packet in data.get("items") or []:
            cids = [c.get("cid") for c in packet.get("candidates") or []]
            if metas is not None:
                found_meta = metas.get(packet.get("id"))
                extra = (meta_problems(found_meta, cids) if found_meta is not None
                         else [f"meta: no entry in {meta_path.name}"])
                if extra:
                    problems.setdefault(packet.get("id"), []).extend(extra)
        mismatch = copy_problems(data, assignment, annotator)
        if mismatch:
            problems.setdefault("<file>", []).extend(mismatch)
        load = assignment["loads"][annotator]
        report["copies"][path.name] = {
            "annotator": annotator, "items": load["items"], "prefilled": load["prefilled"], "bare": load["bare"],
            "bare_items": [i["id"] for i in assignment["items"] if annotator in i["bare_for"]],
            "estimated_hours": estimated_hours(load["items"]), "problems": problems}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.assign",
                                     description="Write each annotator's copy of a batch, every item to two "
                                                 "annotators, plus the assignment and a tracker for Damir.")
    parser.add_argument("batch", help="the batch file, such as eval/benchmark/v0/batches/b01.json")
    parser.add_argument("--annotators", nargs="+", required=True, metavar="ID",
                        help="annotator IDs; the first two get the shared and the crossover items")
    parser.add_argument("--plan", help="the batch's plan with crossover groups (default: <batch>-plan.json next to "
                                       "the batch, when it has crossover groups)")
    parser.add_argument("--out-dir", help="where the files go (default: assigned/ next to the batch)")
    args = parser.parse_args(argv)
    try:
        report = write_assignment(Path(args.batch), args.annotators, Path(args.plan) if args.plan else None,
                                  Path(args.out_dir) if args.out_dir else None)
    except ValueError as err:
        print(f"nothing written: {err}")
        return 1
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 1 if any(c["problems"] for c in report["copies"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
