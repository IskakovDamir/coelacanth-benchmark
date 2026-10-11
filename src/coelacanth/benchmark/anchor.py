"""Pre-fill anchor (eval/protocol.md decision 25 and section 11): how far annotators move from Claude's draft, and
whether a reading made without the draft reaches the same verdict.

    PYTHONPATH=src python -m coelacanth.benchmark.anchor <batch.json> <labels-1.json> [<labels-2.json> ...]
        [--batch <another-batch.json> ...] [--out report.json] [--ignore-problems]

Inputs as in coelacanth.benchmark.agreement (agreement.load_pooled): each labels file is checked
(annotation.labels_problems) against the copy of the batch it was made on (agreement.batch_copy: first
`assigned/<batch>-<id>.json`, then `<batch>-<id>.json` next to the batch, then the batch), and any problem stops the
command unless --ignore-problems. `--batch` adds a further batch; each labels file goes with the batch its `batch`
field names. One labels file is enough: its readings on pre-filled packets are measured, and its crossover items are
listed as lacking their other reading. Run it on the files as the annotators first exported them, the same files
agreement measures.

What is measured, for every item a labels file marks done (a skipped item has no final text):

- A reading on a pre-filled packet (prefill status `draft` in the copy the labels were made on) is compared with that
  packet, the draft the annotator saw:
  - `text` and `skeleton`: the draft's claim.text (claim.skeleton) against the reading's: the word counts, the word
    edit distance, the share of changed words (defined below) and `changed`, true when the two texts differ at all
    after normalisation (an edit of punctuation alone is `changed` with share 0);
  - `chart`: elements matched by id (the tool keeps a packet element's id and gives an added element e7 onwards):
    `removed` lists the draft ids missing from the reading, `added` the reading's ids not in the draft, `edited` maps
    each id in both whose normalised text differs to the share of changed words in that element's text, and
    `unchanged` counts the rest. Key flags are not compared: the draft carries none, annotators set every one.
- A reading on a bare packet (prefill status `none`, decision 25's crossover) is compared, for `text` and `skeleton`,
  with the pre-filled packet of the same item, which its annotator never saw (the batch's own packet; when that one is
  bare, the first reading copy where the item is pre-filled): a reference for how far an independent text lands from
  the draft. Its chart is not compared (`chart` null): its element ids are the annotator's own and match nothing in
  the draft.
- Crossover items: the plan's groups X and Y (`<batch>-plan.json` next to the batch, the file annotation.crossover
  reads; without a plan, the items some reading copy has bare). For each pair of a done pre-filled reading and a done
  bare reading of an item: both verdicts (agreement.item_verdict: precedence over the packet's and the added
  candidates), whether they agree, both readings' claim-text shares against the draft and both readings' minutes. An
  item without such a pair is listed with the reason (labels missing or skipped). Verdict agreement is reported as k of
  n with a Wilson 95% interval, descriptively (protocol section 8: fewer than 20 items). Beside it, as a reference on
  other items, the verdict agreement of every pair of readings made on the same pre-filled draft.

Words and the share of changed words. A text is normalised as the quote checker normalises it (quotes.normalize:
NFKC, typographic quotes and dashes to ASCII, soft hyphens and zero-width characters dropped, runs of whitespace to one
space), split at whitespace, and each token loses its leading and trailing punctuation (Unicode categories P*); tokens
left empty are dropped. Case and every other character are kept, so `N` and `n`, or `<` and `>`, are different words.
For the draft's words a and the reading's words b, the edit distance d(a, b) is the Levenshtein distance over words:
the fewest insertions, deletions and substitutions of one word, each costing 1, that turn a into b (a moved word costs
a deletion and an insertion). The share of changed words is d(a, b) / max(len(a), len(b)), and 0 when both are empty.
It is 0 for the same words in the same order and at most 1, reached for example when every word is replaced or when a
text is written where the draft had none.

Output: the JSON report (default eval/results/anchor/<batch>-anchor.json, batch names joined by `+` when pooled) and
the short table the command prints, also written next to the report as `<report stem>.txt`. Every number is
descriptive; nothing here is a test.
"""

from __future__ import annotations

import argparse
import json
import statistics
import unicodedata
from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Mapping, Sequence

from ..closedbook import wilson
from ..env import REPO_ROOT
from ..quotes import normalize
from .agreement import Group, Reading, item_verdict, load_pooled, read_json, sha256
from .annotation import is_bare

REPORT_FORMAT = "coelacanth-anchor/v1"
RESULTS_DIR = REPO_ROOT / "eval" / "results" / "anchor"
# load_pooled's file-level problem for a single labels file: agreement needs two readings, the anchor does not.
ONE_FILE_PROBLEM = "agreement needs at least two labels files"
DEFINITIONS = {
    "words": "quotes.normalize, split at whitespace, leading and trailing Unicode punctuation (P*) removed from each "
             "token, empty tokens dropped, case kept",
    "share": "word-level Levenshtein distance (insertion, deletion, substitution of one word, each 1) divided by the "
             "longer text's word count; 0 when both texts are empty",
    "changed": "the normalised texts differ (punctuation-only edits count, whitespace-only edits do not)",
    "chart": "elements matched by id; removed, added and edited (normalised text differs); key flags not compared",
    "bare_reference": "a bare reading's text and skeleton against the pre-filled draft its annotator never saw",
}


# Words and distances.

def _strip_punctuation(token: str) -> str:
    start, end = 0, len(token)
    while start < end and unicodedata.category(token[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(token[end - 1]).startswith("P"):
        end -= 1
    return token[start:end]


def words(text) -> list[str]:
    """The words of a text, as the module docstring defines them; [] for anything that is not text."""
    if not isinstance(text, str):
        return []
    return [w for w in (_strip_punctuation(t) for t in normalize(text).split()) if w]


def levenshtein(a: Sequence, b: Sequence) -> int:
    """The fewest insertions, deletions and substitutions of one element, each costing 1, that turn a into b."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def _normalised(text) -> str:
    return normalize(text) if isinstance(text, str) else ""


def text_change(draft, final) -> dict:
    """The draft's text against the final one: word counts, word edit distance, share of changed words, changed."""
    a, b = words(draft), words(final)
    distance = levenshtein(a, b)
    longest = max(len(a), len(b))
    return {"draft_words": len(a), "final_words": len(b), "edit_distance": distance,
            "share": distance / longest if longest else 0.0, "changed": _normalised(draft) != _normalised(final)}


def chart_change(draft_chart, final_chart) -> dict:
    """The draft's chart against the reading's, elements matched by id; texts only (the draft has no key flags)."""
    def texts(chart) -> dict:
        return {e.get("id"): e.get("text") for e in chart or [] if isinstance(e, Mapping)}

    draft, final = texts(draft_chart), texts(final_chart)
    removed = [i for i in draft if i not in final]
    added = [i for i in final if i not in draft]
    edited = {i: text_change(draft[i], final[i])["share"] for i in draft
              if i in final and _normalised(draft[i]) != _normalised(final[i])}
    return {"draft_elements": len(draft), "final_elements": len(final), "removed": removed, "added": added,
            "edited": edited, "unchanged": len(draft) - len(removed) - len(edited)}


# Readings.

def _packets(batch: Mapping) -> dict[str, Mapping]:
    return {p.get("id"): p for p in batch.get("items") or [] if isinstance(p, Mapping)}


def draft_packets(group: Group) -> dict[str, Mapping]:
    """The pre-filled packet of each item: the batch's own, else the first reading copy where it is pre-filled."""
    out: dict[str, Mapping] = {}
    for packets in [_packets(group.batch)] + [_packets(r.batch) for r in group.readings]:
        for item_id, packet in packets.items():
            if item_id not in out and not is_bare(packet):
                out[item_id] = packet
    return out


def plan_path(batch_path: Path, batch: Mapping) -> Path:
    """<batch>-plan.json next to the batch, named after it (as annotation._meta_path finds the meta file); a batch in
    assigned/ looks one folder up."""
    name = batch.get("batch")
    path = Path(batch_path).with_name(f"{name if isinstance(name, str) and name else Path(batch_path).stem}-plan.json")
    if not path.exists() and (path.parent.parent / path.name).exists():
        return path.parent.parent / path.name
    return path


def crossover_plan(group: Group) -> tuple[dict[str, str], str | None]:
    """{item id: 'X' or 'Y'} from the batch's plan, and the plan's file name; ({}, None) without crossover groups."""
    path = plan_path(group.batch_path, group.batch)
    if not path.exists():
        return {}, None
    groups = read_json(path).get("crossover") or {}
    out = {i: "X" for i in groups.get("X_prefilled_for_annotator_1") or []}
    out.update({i: "Y" for i in groups.get("Y_prefilled_for_annotator_2") or []})
    return out, (path.name if out else None)


def reading_record(group: Group, reading: Reading, item: Mapping, packet: Mapping,
                   draft: Mapping | None) -> dict:
    """One done reading against the draft: its packet when pre-filled, the item's pre-filled packet when bare."""
    claim = item.get("claim") if isinstance(item.get("claim"), Mapping) else {}
    bare = is_bare(packet)
    record = {"batch": group.batch.get("batch"), "item": item["id"], "annotator": reading.annotator,
              "copy": reading.batch_path.name, "read_on": "bare" if bare else "draft",
              "minutes": item.get("minutes"), "verdict": item_verdict(item)}
    if draft is None:
        record.update(text=None, skeleton=None, chart=None,
                      note="no pre-filled packet of this item among the inputs, so nothing to compare with")
        return record
    draft_claim = draft.get("claim") or {}
    record["text"] = text_change(draft_claim.get("text"), claim.get("text"))
    record["skeleton"] = text_change(draft_claim.get("skeleton"), claim.get("skeleton"))
    record["chart"] = None if bare else chart_change(draft_claim.get("chart"), claim.get("chart"))
    return record


def _side(record: Mapping | None) -> dict | None:
    if record is None:
        return None
    return {"annotator": record["annotator"], "verdict": record["verdict"],
            "text_share": record["text"]["share"] if record.get("text") else None, "minutes": record["minutes"]}


def _missing_reason(kind: str, readers: Sequence[tuple[str, bool]]) -> str:
    if not readers:
        return f"no labels file was made on a copy where it is {kind}"
    return f"the {kind} reading ({', '.join(a for a, _ in readers)}) is missing or skipped"


def measure(groups: Sequence[Group]) -> dict:
    """The anchor report (without the input record) over the readings of one or more batches."""
    prefilled: list[dict] = []
    bare: list[dict] = []
    crossover: list[dict] = []
    shared: list[dict] = []
    plans: dict[str, str | None] = {}
    for group in groups:
        name = group.batch.get("batch")
        drafts = draft_packets(group)
        plan, plan_name = crossover_plan(group)
        plans[name] = plan_name
        records: dict[str, list[dict]] = defaultdict(list)
        readers: dict[str, dict[str, list[tuple[str, bool]]]] = defaultdict(lambda: {"draft": [], "bare": []})
        for reading in group.readings:
            packets, done = _packets(reading.batch), reading.done()
            for item_id, packet in packets.items():
                readers[item_id]["bare" if is_bare(packet) else "draft"].append((reading.annotator, item_id in done))
            for item_id, item in done.items():
                packet = packets.get(item_id)
                if packet is None:
                    continue  # labels_problems reports an item that is not in the copy
                draft = drafts.get(item_id) if is_bare(packet) else packet
                record = reading_record(group, reading, item, packet, draft)
                (bare if is_bare(packet) else prefilled).append(record)
                records[item_id].append(record)
        cross_ids = set(plan) or {i for i, r in readers.items() if r["bare"]}
        for item_id in _packets(group.batch):
            on_draft = [r for r in records.get(item_id, []) if r["read_on"] == "draft"]
            on_bare = [r for r in records.get(item_id, []) if r["read_on"] == "bare"]
            if item_id not in cross_ids:
                for one, two in combinations(on_draft, 2):
                    shared.append({"batch": name, "item": item_id, "annotators": [one["annotator"], two["annotator"]],
                                   "verdicts": [one["verdict"], two["verdict"]],
                                   "verdict_agrees": one["verdict"] == two["verdict"]})
                continue
            entry = {"batch": name, "item": item_id, "group": plan.get(item_id)}
            if not on_draft or not on_bare:
                reasons = [_missing_reason(kind, readers[item_id][key])
                           for kind, key, found in (("pre-filled", "draft", on_draft), ("bare", "bare", on_bare))
                           if not found]
                crossover.append(dict(entry, prefilled=_side(on_draft[0] if on_draft else None),
                                      bare=_side(on_bare[0] if on_bare else None), verdict_agrees=None,
                                      missing="; ".join(reasons)))
                continue
            for one in on_draft:
                for two in on_bare:
                    crossover.append(dict(entry, prefilled=_side(one), bare=_side(two),
                                          verdict_agrees=one["verdict"] == two["verdict"], missing=None))
    pairs = [c for c in crossover if c["missing"] is None]
    return {"batches": [g.batch.get("batch") for g in groups], "plans": plans,
            "prefilled": {"summary": summarise(prefilled, chart=True),
                          "by_batch": {g.batch.get("batch"): summarise([r for r in prefilled
                                                                        if r["batch"] == g.batch.get("batch")],
                                                                       chart=True) for g in groups},
                          "readings": prefilled},
            "bare": {"summary": summarise(bare, chart=False), "readings": bare},
            "crossover": {"items": crossover,
                          "verdict_agreement": agreement_count(c["verdict_agrees"] for c in pairs),
                          "missing": [c["item"] for c in crossover if c["missing"] is not None],
                          "prefilled_text_share": spread(c["prefilled"]["text_share"] for c in pairs),
                          "bare_text_share": spread(c["bare"]["text_share"] for c in pairs)},
            "shared_draft": {"pairs": shared,
                             "verdict_agreement": agreement_count(p["verdict_agrees"] for p in shared)}}


# Summaries.

def spread(values) -> dict:
    """n, mean, median, minimum, maximum and the number of zeros of the values that are not None."""
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "zero": 0}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "min": min(values), "max": max(values), "zero": sum(1 for v in values if v == 0)}


def agreement_count(agrees) -> dict:
    """k of n agreeing pairs with a Wilson 95% interval (None for n = 0)."""
    agrees = list(agrees)
    k, n = sum(1 for a in agrees if a), len(agrees)
    return {"k": k, "n": n, "share": k / n if n else None, "wilson95": list(wilson(k, n)) if n else None}


def summarise(records: Sequence[Mapping], chart: bool) -> dict:
    compared = [r for r in records if r.get("text") is not None]
    out = {"readings": len(records), "without_draft": len(records) - len(compared)}
    for field in ("text", "skeleton"):
        out[field] = dict(spread(r[field]["share"] for r in compared),
                          changed=sum(1 for r in compared if r[field]["changed"]))
    if chart:
        charts = [r["chart"] for r in compared if r.get("chart") is not None]
        out["chart"] = {"draft_elements": sum(c["draft_elements"] for c in charts),
                        "removed": sum(len(c["removed"]) for c in charts),
                        "added": sum(len(c["added"]) for c in charts),
                        "edited": sum(len(c["edited"]) for c in charts),
                        "readings_changed": sum(1 for c in charts if c["removed"] or c["added"] or c["edited"])}
    return out


# Output.

def _fmt(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _agreement_line(label: str, count: Mapping) -> str:
    if not count["n"]:
        return f"{label}: no pairs"
    low, high = count["wilson95"]
    return f"{label}: {count['k']} of {count['n']} (Wilson 95% {low:.2f} to {high:.2f})"


def table(report: Mapping) -> str:
    """The short table the command prints."""
    pre = report["prefilled"]
    s = pre["summary"]
    per_batch = ", ".join(f"{name} {b['readings']}" for name, b in pre["by_batch"].items())
    lines = [f"readings on a pre-filled draft: {s['readings']} ({per_batch})"
             + (f", {s['without_draft']} without a draft to compare" if s["without_draft"] else ""),
             f"{'share of changed words':<24} {'n':>4} {'mean':>5} {'median':>6} {'max':>5} {'same words':>10} "
             f"{'any edit':>8}"]
    for field, label in (("text", "claim text"), ("skeleton", "skeleton")):
        d = s[field]
        lines.append(f"{label:<24} {d['n']:>4} {_fmt(d['mean']):>5} {_fmt(d['median']):>6} {_fmt(d['max']):>5} "
                     f"{d['zero']:>10} {d['changed']:>8}")
    c = s["chart"]
    lines.append(f"chart: {c['draft_elements']} drafted elements, {c['removed']} removed, {c['edited']} edited, "
                 f"{c['added']} added; {c['readings_changed']} of {s['readings']} readings changed the chart")
    cross = report["crossover"]
    if cross["items"]:
        lines += ["", f"crossover (decision 25): {len(cross['items'])} rows, {len(cross['missing'])} without a pair",
                  f"{'item':<9} {'grp':<3} {'pre-filled reading':<26} {'bare reading':<26} {'verdict':<7} "
                  f"{'text share':<11} minutes"]
        def show(side: Mapping | None, field: str) -> str:
            if side is None:
                return "-"
            if field == "reading":
                return f"{side['annotator']} {side['verdict']}"
            return _fmt(side["text_share"]) if field == "share" else str(side["minutes"])

        for row in cross["items"]:
            one, two = row["prefilled"], row["bare"]
            agrees = "-" if row["verdict_agrees"] is None else ("agrees" if row["verdict_agrees"] else "differs")
            shares = f"{show(one, 'share')} | {show(two, 'share')}"
            lines.append(f"{row['item']:<9} {row['group'] or '-':<3} {show(one, 'reading'):<26} "
                         f"{show(two, 'reading'):<26} {agrees:<7} {shares:<11} "
                         f"{show(one, 'minutes')} | {show(two, 'minutes')}")
            if row["missing"]:
                lines.append(f"{'':<14}{row['missing']}")
        lines.append(_agreement_line("verdict agreement, pre-filled against bare reading",
                                     cross["verdict_agreement"]) + "; descriptive (section 8: under 20 items)")
        pt, bt = cross["prefilled_text_share"], cross["bare_text_share"]
        lines.append(f"claim-text share on these pairs, median: pre-filled reading {_fmt(pt['median'])}, bare reading "
                     f"against the draft it never saw {_fmt(bt['median'])}")
    lines.append(_agreement_line("reference, verdict agreement of readings on the same draft (other items)",
                                 report["shared_draft"]["verdict_agreement"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.anchor",
                                     description="how far annotators move from the pre-filled draft, and verdicts "
                                                 "with and without it on the crossover items (eval/protocol.md "
                                                 "decision 25, section 11)")
    parser.add_argument("batch")
    parser.add_argument("labels", nargs="+")
    parser.add_argument("--batch", dest="more_batches", action="append", default=[], metavar="BATCH",
                        help="pool another batch with the first (repeat for more); each labels file goes with the "
                             "batch its own `batch` field names")
    parser.add_argument("--out", help="the JSON report (default: eval/results/anchor/<batch>-anchor.json); the table "
                                      "is written next to it as <stem>.txt")
    parser.add_argument("--ignore-problems", action="store_true",
                        help="measure even when check-labels finds problems in a labels file")
    args = parser.parse_args(argv)
    batch_paths = [Path(args.batch)] + [Path(p) for p in args.more_batches]
    groups, problems = load_pooled(batch_paths, [Path(p) for p in args.labels])
    problems = [p for p in problems if p != ONE_FILE_PROBLEM]
    readings = [r for g in groups for r in g.readings]
    if not readings:
        problems.append("no labels file goes with the batches given")
    label_problems = [p for r in readings for p in r.problems]
    for problem in problems + label_problems:
        print(problem)
    if problems or (label_problems and not args.ignore_problems):
        print(f"nothing measured: {len(problems) + len(label_problems)} problems; fix them, or pass --ignore-problems "
              "to measure the labels as they are")
        return 1
    name = "+".join(str(g.batch.get("batch")) for g in groups)
    report = {"format": REPORT_FORMAT, "created": date.today().isoformat(), "batch": name,
              "inputs": {"batches": [{"batch": g.batch.get("batch"), "path": str(g.batch_path),
                                      "sha256": sha256(g.batch_path)} for g in groups],
                         "labels": [{"annotator": r.annotator, "batch": g.batch.get("batch"),
                                     "path": str(r.labels_path), "sha256": sha256(r.labels_path),
                                     "exported_at": r.labels.get("exported_at"), "batch_copy": str(r.batch_path),
                                     "batch_copy_sha256": sha256(r.batch_path)}
                                    for g in groups for r in g.readings]},
              "label_problems": label_problems, "definitions": DEFINITIONS,
              "note": "descriptive (eval/protocol.md decision 25 and section 11); measured on independent first "
                      "readings, the files agreement measures"}
    report.update(measure(groups))
    out = Path(args.out) if args.out else RESULTS_DIR / f"{name}-anchor.json"
    text = table(report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    out.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"report: {out} (table: {out.with_suffix('.txt')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
