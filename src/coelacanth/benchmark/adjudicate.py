"""Adjudication of two annotators' readings (eval/protocol.md decision 4, LABELING.md section 5).

    PYTHONPATH=src python -m coelacanth.benchmark.adjudicate report <batch.json> <labels-1.json> <labels-2.json>
        [--out-dir DIR] [--offline] [--adjudicator damir] [--force | --carry-over]
    PYTHONPATH=src python -m coelacanth.benchmark.adjudicate apply <batch.json> <labels-1.json> <labels-2.json>
        <adjudication.json> <out.jsonl> [--offline]

`report` writes three files to DIR (default eval/benchmark/v0/adjudication/<batch>-<id 1>-<id 2>/, one folder per pair
of annotators, since coelacanth.benchmark.assign gives each item to two of them): `report.md` and `report.html`,
the two readings of every item side by side for the adjudicator (claim text, skeleton, coined names, chart with key
flags, each candidate's relation, covered, analogous and restricted elements, cross-field flag, attribution,
confidence, note and quote pairs, added candidates, item notes and minutes), what differs marked and what agrees
collapsed; and `adjudication.json`, the decisions to fill. It never overwrites an existing adjudication.json without
--force or --carry-over, since that file may already hold decisions. A kept file records the SHA-256 of the inputs it
was made from, so after a labels file or a batch copy changed (or after switching between online and --offline)
apply refuses it, and the report prints a warning saying so. `--carry-over` then writes the file anew from the
current inputs and copies every decision, reason and override whose entry (key, kind, options, allowed decisions) is
unchanged, and each item's claim_checked_by; it keeps the old file beside it as
`adjudication.replaced-<first 12 hex of its SHA-256>.json` and lists the decided entries that must be decided again.
`--force` writes an empty file and keeps nothing.

Only items both annotators marked done are adjudicated (decision 4: two readings); an item only one of the two
did is listed: it is adjudicated with its other reader's labels, or stays `labelled`. The labels are checked against
the copy each was made on, as in coelacanth.benchmark.agreement (assigned/ or crossover copies next to the batch). Each item yields entries, each with its options, the allowed decisions and empty `decision`, `reason`
(one line) and `override` fields:

- `disagreement` on `claim`: the two readings' claims differ (text, skeleton, coined names, chart or key flags).
  Decide whose claim the item keeps (an annotator id). `override` may replace `chart` or `coined_names`.
- `disagreement` on a candidate: the decision-critical fields differ (gold or not; for gold, the relation or the
  cross-field flag). Decide whose labels the candidate keeps. A work both annotators added is matched by identifier.
  A work only one annotator added and rated gold has one reading: decide the annotator's id (keep it) or `omit`.
  A work only one annotator added and rated not gold is kept as labelled, with no entry.
- `evidence_check`: both readings agree that a candidate is `equivalent` or `specialization`; the adjudicator checks
  its evidence (decision 4) and decides `confirmed`.
- `ai_origin_check`: an AI-origin item on which the readings agree; the adjudicator decides every AI-origin item
  (LABELING.md section 5) and confirms it with `confirmed`.

Any entry may be decided `exclude`: the item is then written with status `excluded` and exclusion_reason
`annotators_disagree` (or `excluded_at_adjudication` when the entry was a check). `override` on a candidate entry may
set relation, covered_elements, analogous_elements, restricted_elements, cross_field, attribution and confidence;
gold follows from the relation, and a cross-field flag set or made necessary by an override is confirmed by the
adjudicator. Where no decision applies, the item keeps the reading whose claim was chosen (the first labels file when
the claims agree), so element ids stay consistent with the chart.

A claim an annotator wrote on a bare packet (decision 25's crossover) needs a second person's check against the
source before adjudication (SCHEMA.md rule 5): the item's `claim_checked_by` names that person and is required when
such a claim is chosen and nobody checked it yet; it must differ from the writer. A contrast item's claim (written by
one annotator, LABELING.md section 5) is checked by the other annotator's reading, which merge records; the adjudicator
names the checker only when the kept reading lacks one. A claim checked on a pre-filled packet keeps its checker.

`apply` refuses, writing nothing, when the labels or the batch changed since the report (SHA-256), when any entry
lacks an allowed decision or a one-line reason, when an override is malformed, or when an adjudicated item fails
items.validate. Each written item has status `adjudicated` (or `excluded`), both readings in
`annotation.annotators`, the adjudicator in `annotation.adjudicated_by` and every entry's decision and reason in
`annotation.adjudication`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..env import REPO_ROOT
from ..quotes import QuoteChecker
from . import annotation
from .agreement import Reading, load_readings, read_json, sha256
from .items import ALREADY_DONE, ATTRIBUTIONS, PRECEDENCE, RELATIONS, derive_verdict, guide_dois, validate

DECISIONS_FORMAT = "coelacanth-adjudication/v1"
ADJUDICATOR = "damir"
ADJUDICATION_DIR = REPO_ROOT / "eval" / "benchmark" / "v0" / "adjudication"
EXCLUDE, OMIT, CONFIRMED = "exclude", "omit", "confirmed"
CANDIDATE_OVERRIDE = ("relation", "covered_elements", "analogous_elements", "restricted_elements", "cross_field",
                      "attribution", "confidence")
CLAIM_OVERRIDE = ("chart", "coined_names")
ELEMENT_FIELDS = ("covered_elements", "analogous_elements", "restricted_elements")
CANDIDATE_ROWS = (("relation", "relation"), ("gold", "gold"), ("cross_field", "cross-field"),
                  ("covered_elements", "covered"), ("analogous_elements", "analogous"),
                  ("restricted_elements", "restricted"), ("attribution", "attribution"),
                  ("confidence", "confidence"), ("note", "note"))
HOW_TO_FILL = ("For every entry, set `decision` to one of `allowed` and write a one-line `reason` from the evidence. "
               "An annotator id keeps that reading; `confirmed` keeps the agreed labels after checking them; `omit` "
               "leaves a work only one annotator found out of the item; `exclude` excludes the whole item. "
               "`override` (optional) changes fields of the kept reading, listed in `override_fields`. When a claim "
               "an annotator wrote (a bare packet or a contrast item) is kept, `claim_checked_by` names the person who checked it against the "
               "source. Then run the apply command.")


# The two readings of each item.

def _evidence_id(ev: Mapping) -> tuple:
    return tuple(ev.get(f) for f in annotation.EVIDENCE_FIELDS)


def _side(reading: Reading, item: Mapping, merged: Mapping) -> dict:
    """One annotator's reading of one item: the labelled item, the merged SCHEMA item and the packet of the copy the
    labels were made on, with its candidates keyed (packet cid, or the added work's identifiers)."""
    packet = next(p for p in reading.batch["items"] if p.get("id") == item["id"])
    packet_cands = packet.get("candidates") or []
    labelled = {c.get("cid"): c for c in item.get("candidates") or [] if isinstance(c, Mapping)}
    merged_cands = merged["candidates"]
    cands = []
    for pc, mc in zip(packet_cands, merged_cands):
        cands.append({"cid": pc["cid"], "added": False, "record": mc, "labels": labelled.get(pc["cid"]) or {},
                      "packet_evidence": pc.get("evidence") or []})
    added = [a for a in item.get("added_candidates") or [] if isinstance(a, Mapping)]
    for ac, mc in zip(added, merged_cands[len(packet_cands):]):
        cands.append({"cid": ac.get("cid"), "added": True, "record": mc, "labels": ac, "packet_evidence": [],
                      "ids": annotation.work_ids(mc.get("work") or {}) | annotation.work_ids(ac.get("work") or {})})
    return {"annotator": reading.annotator, "labels": item, "item": merged, "packet": packet,
            "bare": annotation.is_bare(packet), "candidates": cands}


def _claim_view(item: Mapping) -> dict:
    claim = item["claim"]
    return {"text": claim.get("text"), "skeleton": claim.get("skeleton"),
            "coined_names": list(claim.get("coined_names") or []),
            "chart": [{"id": e.get("id"), "text": e.get("text"), "key": e.get("key")} for e in claim.get("chart") or []],
            "written_by": claim.get("written_by"), "checked_by": claim.get("checked_by")}


def _claim_differs(views: Sequence[Mapping]) -> list[str]:
    a, b = views
    out = [label for field, label in (("text", "text"), ("skeleton", "skeleton"), ("coined_names", "coined names"))
           if a[field] != b[field]]
    if [(e["id"], e["text"]) for e in a["chart"]] != [(e["id"], e["text"]) for e in b["chart"]]:
        out.append("chart elements")
    if {e["id"]: e["key"] for e in a["chart"]} != {e["id"]: e["key"] for e in b["chart"]}:
        out.append("key flags")
    return out


def _critical(record: Mapping | None) -> tuple:
    """The decision-critical fields of a candidate (decision 4): gold or not; for gold, relation and cross-field."""
    if record is None:
        return (None,)
    gold = record.get("relation") in PRECEDENCE
    return (gold, record.get("relation") if gold else None, record.get("cross_field") if gold else None)


def _minor_differs(cands: Sequence[Mapping]) -> list[str]:
    a, b = cands
    out = []
    for field in ELEMENT_FIELDS + ("attribution", "confidence"):
        if a["record"].get(field) != b["record"].get(field):
            out.append(field.replace("_elements", "").replace("_", " "))
    if [_evidence_id(e) for e in a["record"]["evidence"]] != [_evidence_id(e) for e in b["record"]["evidence"]]:
        out.append("quote pairs")
    if (a["labels"].get("note") or "").strip() != (b["labels"].get("note") or "").strip():
        out.append("note")
    return out


def _cand_summary(cand: Mapping) -> dict:
    record, labels = cand["record"], cand["labels"]
    out = {field: record.get(field) for field in ("relation", "gold", "cross_field")}
    out.update({field: list(record.get(field) or []) for field in ELEMENT_FIELDS})
    out.update({"attribution": record.get("attribution"), "confidence": record.get("confidence"),
                "quote_pairs": len(record.get("evidence") or []), "note": (labels.get("note") or "").strip()})
    if cand["added"]:
        work = record.get("work") or {}
        out["work"] = {k: work.get(k) for k in annotation.WORK_IDS + ("title", "year") if work.get(k)}
    return out


def _entry(key: str, kind: str, what: str, options: dict, allowed: list[str], override_fields: Sequence[str],
           **extra) -> dict:
    return {"key": key, "kind": kind, "what": what, **extra, "options": options, "allowed": allowed,
            "override_fields": list(override_fields), "decision": "", "reason": "", "override": {}}


def _match_added(sides: Sequence[Mapping]) -> list[dict]:
    """Added candidates of the two readings, the same work (a shared identifier) matched: [{annotator: cand}]."""
    a, b = sides
    rest = [c for c in b["candidates"] if c["added"]]
    groups = []
    for cand in (c for c in a["candidates"] if c["added"]):
        other = next((o for o in rest if o["ids"] & cand["ids"]), None)
        if other is not None:
            rest.remove(other)
            groups.append({a["annotator"]: cand, b["annotator"]: other})
        else:
            groups.append({a["annotator"]: cand})
    groups += [{b["annotator"]: o} for o in rest]
    return groups


def _added_key(group: Mapping[str, Mapping]) -> str:
    annotator, cand = next(iter(group.items()))
    return annotation.added_key(cand["record"].get("work") or {}) or f"{annotator}:{cand['cid']}"


def compare_item(item_id: str, sides: Sequence[Mapping], meta: Mapping | None) -> dict:
    """The adjudication view of one item read by two annotators: what differs, and the entries to decide."""
    names = [s["annotator"] for s in sides]
    by = dict(zip(names, sides))
    claims = {s["annotator"]: _claim_view(s["item"]) for s in sides}
    differs = _claim_differs([claims[n] for n in names])
    entries = []
    if differs:
        options = {n: dict(claims[n], made_on="bare copy" if by[n]["bare"] else "pre-filled copy") for n in names}
        entries.append(_entry(f"{item_id}/claim", "disagreement", "claim", options, names + [EXCLUDE],
                              CLAIM_OVERRIDE, differs=differs))
    candidates = []
    for pc in sides[0]["packet"].get("candidates") or []:
        group = {s["annotator"]: next(c for c in s["candidates"] if not c["added"] and c["cid"] == pc["cid"])
                 for s in sides}
        candidates.append({"key": pc["cid"], "added": False, "group": group})
    for group in _match_added(sides):
        candidates.append({"key": _added_key(group), "added": True, "group": group})
    for cv in candidates:
        group, key = cv["group"], f"{item_id}/{cv['key']}"
        if len(group) == 2:
            pair = [group[n] for n in names]
            cv["critical_differs"] = _critical(pair[0]["record"]) != _critical(pair[1]["record"])
            cv["minor"] = [] if cv["critical_differs"] else _minor_differs(pair)
            what = "added candidate" if cv["added"] else "candidate"
            if cv["critical_differs"]:
                entries.append(_entry(key, "disagreement", what, {n: _cand_summary(group[n]) for n in names},
                                      names + [EXCLUDE], CANDIDATE_OVERRIDE))
            elif pair[0]["record"].get("relation") in ALREADY_DONE:
                entries.append(_entry(key, "evidence_check", what, {"agreed": _cand_summary(pair[0])},
                                      [CONFIRMED, EXCLUDE], CANDIDATE_OVERRIDE))
        else:
            (adder, cand), = group.items()
            cv["critical_differs"], cv["minor"] = False, []
            if cand["record"].get("relation") in PRECEDENCE:
                cv["one_sided"] = "entry"
                entries.append(_entry(key, "disagreement", "added candidate", {adder: _cand_summary(cand)},
                                      [adder, OMIT, EXCLUDE], CANDIDATE_OVERRIDE,
                                      note=f"only {adder} found this work; the other reading has no label for it"))
            else:
                cv["one_sided"] = "kept"
    origin = (meta or {}).get("origin") or {}
    ai_origin = origin.get("kind") == "ai"
    if ai_origin and not entries:
        entries.append(_entry(f"{item_id}/ai-origin", "ai_origin_check", "item",
                              {"agreed": {"verdict": sides[0]["item"]["verdict"]}}, [CONFIRMED, EXCLUDE], ()))
    return {"id": item_id, "annotators": names, "sides": by, "claims": claims, "claim_differs": differs,
            "verdicts": {n: by[n]["item"]["verdict"] for n in names}, "candidates": candidates, "entries": entries,
            "bare": [n for n in names if by[n]["bare"]], "ai_origin": ai_origin,
            "assessment": (meta or {}).get("prefill_assessment"), "packet": sides[0]["packet"]}


def _meta_path(batch_path: Path, batch: Mapping) -> Path:
    name = batch.get("batch")
    return Path(batch_path).with_name(f"{name if isinstance(name, str) and name else Path(batch_path).stem}-meta.json")


def prepare(batch_path: Path, labels_paths: Sequence[Path], offline: bool = False,
            fetch: Callable[[str], dict] | None = None, checker: QuoteChecker | None = None
            ) -> tuple[dict, list[Reading], dict, list[dict], dict[str, str], list[str], list[str]]:
    """Read and check the inputs (the same gate as annotation merge), merge each reading and compare the items both
    annotators marked done. Returns the batch, the readings, the meta, the item views, the items with one reading only
    (id: who), the problems and the notes."""
    batch, readings, problems = load_readings(Path(batch_path), [Path(p) for p in labels_paths])
    notes: list[str] = []
    if len(readings) != 2:
        problems.append(f"adjudication takes two labels files, got {len(readings)}")
    for r in readings:
        problems += r.problems
    meta_path = _meta_path(batch_path, batch)
    if not meta_path.exists():
        return batch, readings, {}, [], {}, problems + [f"meta: {meta_path.name} not found next to the batch"], notes
    meta = read_json(meta_path)
    if problems:
        return batch, readings, meta, [], {}, problems, notes
    checker = checker or QuoteChecker()
    merged: dict[str, dict] = {}
    for r in readings:
        works: dict = {}
        if offline:
            notes.append(f"{r.annotator}: offline, added works not resolved and no quote checked verbatim")
        else:
            works, unresolved = annotation.resolve_added(r.batch, r.labels, fetch, notes)
            problems += [f"{r.annotator}: added: {p}" for p in unresolved]
            problems += [f"{r.annotator}: added: {p}" for p in annotation.added_quote_problems(r.batch, r.labels,
                                                                                              works, checker)]
        try:
            merged[r.annotator] = {i["id"]: i for i in annotation.merge(r.batch, meta, r.labels, works)}
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            problems.append(f"{r.annotator}: merge failed ({type(err).__name__}: {err})")
    if problems:
        return batch, readings, meta, [], {}, problems, notes
    views, single = [], {}
    for packet in batch.get("items") or []:
        item_id = packet.get("id")
        have = [r for r in readings if item_id in merged[r.annotator]]
        if len(have) == 2:
            sides = [_side(r, r.done()[item_id], merged[r.annotator][item_id]) for r in have]
            views.append(compare_item(item_id, sides, (meta.get("items") or {}).get(item_id)))
        elif have:
            single[item_id] = have[0].annotator
    return batch, readings, meta, views, single, problems, notes


def inputs_record(batch_path: Path, readings: Sequence[Reading]) -> dict:
    return {"batch": {"path": str(batch_path), "sha256": sha256(batch_path)},
            "labels": [{"annotator": r.annotator, "path": str(r.labels_path), "sha256": sha256(r.labels_path),
                        "batch_copy": str(r.batch_path), "batch_copy_sha256": sha256(r.batch_path)}
                       for r in readings]}


def needs_claim_check(view: Mapping) -> bool:
    """Whether the decisions file needs the item's claim_checked_by: a claim an annotator wrote, on a bare copy
    (decision 25's crossover) or on a contrast item (prefill.by `annotator:<id>`), that a side keeps unchecked."""
    if view["bare"]:
        return True
    return any(side["item"]["claim"].get("written_by") not in (None, annotation.PREFILL_AUTHOR)
               and not side["item"]["claim"].get("checked_by") for side in view["sides"].values())


def decisions_template(batch: Mapping, readings: Sequence[Reading], views: Sequence[Mapping],
                       single: Mapping[str, str], inputs: Mapping, offline: bool, adjudicator: str) -> dict:
    items = []
    for view in views:
        if not view["entries"] and not needs_claim_check(view):
            continue
        entry = {"id": view["id"], "verdicts": view["verdicts"]}
        if needs_claim_check(view):
            entry["claim_checked_by"] = ""
        entry["entries"] = view["entries"]
        items.append(entry)
    return {"format": DECISIONS_FORMAT, "batch": batch.get("batch"), "created": date.today().isoformat(),
            "annotators": [r.annotator for r in readings], "adjudicator": adjudicator,
            "resolved_works": not offline, "inputs": inputs, "how_to_fill": HOW_TO_FILL,
            "agreed_items": [v["id"] for v in views if not v["entries"] and not needs_claim_check(v)],
            "single_reading": dict(single), "items": items}


# Rendering the report.

def _show(value) -> str:
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(map(str, value)) if value else "none"
    return str(value)


def _work_line(work: Mapping) -> str:
    bits = [str(work.get("year") or (work.get("publication_date") or "")[:4] or "year unknown")]
    bits += [str(work[k]) for k in ("venue", "field") if work.get(k)]
    return f"{work.get('title') or 'untitled'} ({'; '.join(bits)})"


def _work_links(work: Mapping) -> list[tuple[str, str]]:
    out = []
    for kind, url in (("doi", "https://doi.org/{}"), ("openalex", "https://openalex.org/{}"),
                      ("arxiv", "https://arxiv.org/abs/{}"), ("pmid", "https://pubmed.ncbi.nlm.nih.gov/{}/")):
        n = annotation.norm_id(kind, work.get(kind))
        if n:
            out.append((f"{kind}:{n}", url.format(n)))
    return out


def _evidence_rows(cand: Mapping) -> list[dict]:
    """A reading's quote pairs for one candidate, each marked pre-filled or added; then the pre-filled ones it
    rejected."""
    prefilled = {_evidence_id(e) for e in cand["packet_evidence"]}
    rows = [dict(ev, origin="pre-filled" if _evidence_id(ev) in prefilled else "added by the annotator")
            for ev in cand["record"].get("evidence") or []]
    rejected = cand["labels"].get("rejected_evidence") or [] if not cand["added"] else []
    for n in rejected:
        if isinstance(n, int) and 0 <= n < len(cand["packet_evidence"]):
            rows.append(dict(cand["packet_evidence"][n], origin=f"pre-filled #{n + 1}, rejected"))
    return rows


def _cand_rows(cv: Mapping, names: Sequence[str]) -> list[tuple[str, dict, bool]]:
    rows = []
    for field, label in CANDIDATE_ROWS:
        values = {}
        for n in names:
            cand = cv["group"].get(n)
            if cand is None:
                values[n] = "not found by this annotator"
            elif field == "note":
                values[n] = (cand["labels"].get("note") or "").strip() or "none"
            else:
                values[n] = _show(cand["record"].get(field))
        rows.append((label, values, len(cv["group"]) == 2 and len(set(values.values())) > 1))
    return rows


def _chart_rows(view: Mapping) -> list[tuple[str, dict, bool]]:
    names = view["annotators"]
    ids: list[str] = []
    for n in names:
        ids += [e["id"] for e in view["claims"][n]["chart"] if e["id"] not in ids]
    rows = []
    for eid in ids:
        values = {}
        for n in names:
            e = next((x for x in view["claims"][n]["chart"] if x["id"] == eid), None)
            values[n] = "not in this chart" if e is None else f"[{'key' if e['key'] else 'not key'}] {e['text']}"
        rows.append((eid, values, len(set(values.values())) > 1))
    return rows


def _cand_title(cv: Mapping) -> str:
    cand = next(iter(cv["group"].values()))
    return f"{cv['key']}: {_work_line(cand['record'].get('work') or {})}"


def _status_of(cv: Mapping) -> str:
    if cv.get("one_sided") == "entry":
        return "found by one annotator, rated gold: decide"
    if cv.get("one_sided") == "kept":
        return "found by one annotator, not gold: kept as labelled"
    if cv["critical_differs"]:
        return "decision-critical fields differ"
    return "agree" + (f" (other differences: {', '.join(cv['minor'])})" if cv["minor"] else "")


def _md(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def render_markdown(data: Mapping) -> str:
    names = data["annotators"]
    out = [f"# Adjudication report, batch {data['batch']}", "",
           f"Readings: {', '.join(names)}. Created {data['created']}. Decisions go into `adjudication.json` next to "
           "this file; the HTML version shows the same content.", ""]
    out += [f"- Items with two readings: {len(data['views'])}",
            f"- Items with something to decide: {sum(1 for v in data['views'] if v['entries'])}",
            f"- Entries: {data['counts']}",
            f"- Items read by only one of the two: "
            f"{', '.join(f'{k} ({v})' for k, v in data['single'].items()) or 'none'}", ""]
    if not data["resolved"]:
        out += ["Offline: added works were not resolved and no quote was checked verbatim.", ""]
    for view in data["views"]:
        out += _md_item(view, names)
    return "\n".join(out) + "\n"


def _md_item(view: Mapping, names: Sequence[str]) -> list[str]:
    claim_src = view["packet"]["claim"]
    out = [f"## {view['id']}" + (f" ({len(view['entries'])} to decide)" if view["entries"] else " (readings agree)"),
           "", f"Source: {_work_line(claim_src.get('source_work') or {})}. Claim date {claim_src.get('date')}."]
    verdicts = " / ".join(f"{n}: `{view['verdicts'][n]}`" for n in names)
    same = len(set(view["verdicts"].values())) == 1
    out += ["", f"Verdict: {verdicts}" + ("" if same else " **DIFFERS**"), ""]
    if view["bare"]:
        out += [f"Bare copy (nothing pre-filled) for: {', '.join(view['bare'])}.", ""]
    if view["entries"]:
        out += ["To decide:", ""]
        out += [f"- `{e['key']}` ({e['kind']}, {e['what']}): one of {', '.join(f'`{a}`' for a in e['allowed'])}"
                for e in view["entries"]]
        out.append("")
    claim_block = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for field in ("text", "skeleton", "coined_names", "written_by"):
        values = [_md(_show(view["claims"][n][field])) for n in names]
        mark = " **DIFFERS**" if len(set(values)) > 1 else ""
        claim_block.append(f"| {field.replace('_', ' ')}{mark} | " + " | ".join(values) + " |")
    for eid, values, differs in _chart_rows(view):
        claim_block.append(f"| {eid}{' **DIFFERS**' if differs else ''} | " +
                           " | ".join(_md(values[n]) for n in names) + " |")
    if view["claim_differs"]:
        out += [f"### Claim **DIFFERS**: {', '.join(view['claim_differs'])}", ""] + claim_block + [""]
    else:
        out += ["<details><summary>Claim: the readings agree</summary>", ""] + claim_block + ["", "</details>", ""]
    for cv in view["candidates"]:
        block = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for label, values, differs in _cand_rows(cv, names):
            block.append(f"| {label}{' **DIFFERS**' if differs else ''} | " +
                         " | ".join(_md(values[n]) for n in names) + " |")
        block.append("")
        for n in names:
            cand = cv["group"].get(n)
            if cand is None:
                continue
            block.append(f"Quote pairs, {n}:")
            rows = _evidence_rows(cand)
            block += [f"- {r['element']} ({r['origin']}; {r.get('text_level')}"
                      f"{'; general form' if r.get('general_form') else ''}): candidate \"{_md(r.get('quote_candidate'))}\""
                      f" ({_md(r.get('location_candidate'))}); claim \"{_md(r.get('quote_claim'))}\""
                      f" ({_md(r.get('location_claim'))})" for r in rows] or ["- none"]
            block.append("")
        work = next(iter(cv["group"].values()))["record"].get("work") or {}
        links = ", ".join(f"[{label}]({url})" for label, url in _work_links(work))
        title = f"Candidate {_cand_title(cv)}" + (f" ({links})" if links else "")
        if cv["critical_differs"] or cv.get("one_sided") == "entry":
            out += [f"### {_md(title)} **DIFFERS**", "", _status_of(cv), ""] + block
        else:
            out += [f"<details><summary>{html.escape(title)}: {html.escape(_status_of(cv))}</summary>", ""] + block + \
                   ["</details>", ""]
    for n in names:
        lab = view["sides"][n]["labels"]
        out.append(f"Notes, {n} ({_show(lab.get('minutes'))} minutes): {_md((lab.get('notes') or '').strip() or 'none')}")
    out.append("")
    if view["assessment"]:
        out += ["<details><summary>Claude's draft assessment from the hidden meta (annotators never saw it; not "
                "evidence)</summary>", "", _md(json.dumps(view["assessment"], ensure_ascii=False)
                                               if not isinstance(view["assessment"], str) else view["assessment"]),
                "", "</details>", ""]
    return out


CSS = """
:root { color-scheme: dark; --bg: #0e0e0f; --fg: #e8e8e6; --muted: #a6a6a2; --line: #262628; --line-strong: #3b3b3e;
  --accent: #f97316; --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
@media (prefers-color-scheme: light) {
  :root { color-scheme: light; --bg: #fbfbfa; --fg: #1c1c1b; --muted: #5b5b58; --line: #e3e3df; --line-strong: #c9c9c4;
    --accent: #c2410c; }
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.5 var(--font); }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 80px; }
h1 { font-size: 20px; margin: 0 0 8px; } h2 { font-size: 17px; margin: 0 0 6px; } h3 { font-size: 14px; margin: 14px 0 6px; }
a { color: inherit; text-underline-offset: 2px; }
.muted { color: var(--muted); } .mono { font-family: var(--mono); font-size: 12.5px; }
.item { border-top: 1px solid var(--line-strong); padding: 18px 0 8px; }
.tag { display: inline-block; font-size: 12px; border: 1px solid var(--line-strong); border-radius: 4px; padding: 0 6px;
  margin-left: 6px; max-width: 100%; overflow-wrap: anywhere; }
.tag.diff, .diffmark { color: var(--accent); border-color: var(--accent); }
.block { border-left: 2px solid var(--line); padding: 2px 0 2px 12px; margin: 10px 0; }
.block.diff { border-left-color: var(--accent); }
table { border-collapse: collapse; width: 100%; table-layout: fixed; margin: 6px 0; }
th, td { border-bottom: 1px solid var(--line); padding: 5px 8px; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { color: var(--muted); font-weight: 500; }
col.lab { width: 150px; }
tr.diff th { color: var(--accent); }
tr.diff td { border-left: 2px solid var(--accent); }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--muted); }
ul.quotes { margin: 4px 0 8px; padding-left: 18px; } ul.quotes li { margin: 3px 0; }
.decide { border: 1px solid var(--line-strong); border-radius: 6px; padding: 8px 12px; margin: 10px 0; }
.cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 760px) { .cols { grid-template-columns: minmax(0, 1fr); } col.lab { width: 92px; } }
"""


def _h(text) -> str:
    return html.escape(str(text))


def _html_table(rows: Sequence[tuple[str, dict, bool]], names: Sequence[str]) -> str:
    head = "<tr><th></th>" + "".join(f"<th>{_h(n)}</th>" for n in names) + "</tr>"
    body = "".join(f"<tr class=\"{'diff' if differs else ''}\"><th>{_h(label)}"
                   f"{' <span class=diffmark>differs</span>' if differs else ''}</th>" +
                   "".join(f"<td>{_h(values[n])}</td>" for n in names) + "</tr>"
                   for label, values, differs in rows)
    cols = "<colgroup><col class=lab>" + "<col>" * len(names) + "</colgroup>"
    return f"<table>{cols}{head}{body}</table>"


def _html_item(view: Mapping, names: Sequence[str]) -> str:
    claim_src = view["packet"]["claim"]
    parts = [f"<section class=item id=\"{_h(view['id'])}\"><h2>{_h(view['id'])}"]
    parts.append(f"<span class=\"tag diff\">{len(view['entries'])} to decide</span>" if view["entries"]
                 else "<span class=tag>readings agree</span>")
    if view["ai_origin"]:
        parts.append("<span class=tag>AI-origin</span>")
    parts.append("</h2>")
    parts.append(f"<p class=muted>Source: {_h(_work_line(claim_src.get('source_work') or {}))}. Claim date "
                 f"{_h(claim_src.get('date'))}.</p>")
    same = len(set(view["verdicts"].values())) == 1
    parts.append(f"<p>Verdict: " + " / ".join(f"{_h(n)} <span class=mono>{_h(view['verdicts'][n])}</span>"
                                             for n in names) +
                 ("" if same else " <span class=\"tag diff\">differs</span>") + "</p>")
    if view["bare"]:
        parts.append(f"<p class=muted>Bare copy (nothing pre-filled) for {_h(', '.join(view['bare']))}.</p>")
    if view["entries"]:
        parts.append("<div class=decide><strong>To decide in adjudication.json</strong><ul>" + "".join(
            f"<li><span class=mono>{_h(e['key'])}</span> ({_h(e['kind'])}, {_h(e['what'])}): one of "
            + ", ".join(f"<span class=mono>{_h(a)}</span>" for a in e["allowed"]) + "</li>"
            for e in view["entries"]) + "</ul></div>")
    claim_rows = [(field.replace("_", " "), {n: _show(view["claims"][n][field]) for n in names},
                   len({_show(view["claims"][n][field]) for n in names}) > 1)
                  for field in ("text", "skeleton", "coined_names", "written_by")] + _chart_rows(view)
    claim_table = _html_table(claim_rows, names)
    if view["claim_differs"]:
        parts.append(f"<div class=\"block diff\"><h3>Claim <span class=\"tag diff\">differs: "
                     f"{_h(', '.join(view['claim_differs']))}</span></h3>{claim_table}</div>")
    else:
        parts.append(f"<details><summary>Claim: the readings agree</summary>{claim_table}</details>")
    for cv in view["candidates"]:
        work = next(iter(cv["group"].values()))["record"].get("work") or {}
        links = " ".join(f"<a href=\"{_h(url)}\" rel=noreferrer>{_h(label)}</a>" for label, url in _work_links(work))
        quotes = []
        for n in names:
            cand = cv["group"].get(n)
            if cand is None:
                quotes.append(f"<div><strong>{_h(n)}</strong><p class=muted>did not find this work</p></div>")
                continue
            items = "".join(
                f"<li><span class=mono>{_h(r['element'])}</span> <span class=muted>({_h(r['origin'])}; "
                f"{_h(r.get('text_level'))}{'; general form' if r.get('general_form') else ''})</span><br>"
                f"candidate: \u201c{_h(r.get('quote_candidate'))}\u201d <span class=muted>"
                f"({_h(r.get('location_candidate'))})</span><br>claim: \u201c{_h(r.get('quote_claim'))}\u201d "
                f"<span class=muted>({_h(r.get('location_claim'))})</span></li>" for r in _evidence_rows(cand))
            quotes.append(f"<div><strong>{_h(n)}: quote pairs</strong><ul class=quotes>{items or '<li>none</li>'}"
                          f"</ul></div>")
        body = (f"<p class=muted>{_h(_status_of(cv))} {links}</p>" + _html_table(_cand_rows(cv, names), names) +
                f"<div class=cols>{''.join(quotes)}</div>")
        title = f"Candidate {_cand_title(cv)}"
        if cv["critical_differs"] or cv.get("one_sided") == "entry":
            parts.append(f"<div class=\"block diff\"><h3>{_h(title)} <span class=\"tag diff\">differs</span></h3>"
                         f"{body}</div>")
        else:
            parts.append(f"<details><summary>{_h(title)}: {_h(_status_of(cv))}</summary>{body}</details>")
    notes = "".join(f"<p><strong>{_h(n)}</strong> ({_h(_show(view['sides'][n]['labels'].get('minutes')))} minutes): "
                    f"{_h((view['sides'][n]['labels'].get('notes') or '').strip() or 'no notes')}</p>"
                    for n in names)
    parts.append(f"<div class=block><h3>Item notes</h3>{notes}</div>")
    if view["assessment"]:
        text = view["assessment"] if isinstance(view["assessment"], str) else json.dumps(view["assessment"],
                                                                                         ensure_ascii=False)
        parts.append("<details><summary>Claude's draft assessment from the hidden meta (annotators never saw it; not "
                     f"evidence)</summary><p>{_h(text)}</p></details>")
    parts.append("</section>")
    return "".join(parts)


def render_html(data: Mapping) -> str:
    names = data["annotators"]
    toc = "".join(f"<li><a href=\"#{_h(v['id'])}\">{_h(v['id'])}</a> <span class=muted>"
                  f"{len(v['entries'])} to decide</span></li>" for v in data["views"] if v["entries"])
    single = ", ".join(f"{k} ({v})" for k, v in data["single"].items()) or "none"
    head = (f"<h1>Adjudication report, batch {_h(data['batch'])}</h1>"
            f"<p class=muted>Readings {_h(', '.join(names))}. Created {_h(data['created'])}. Decisions go into "
            f"<span class=mono>adjudication.json</span>. What differs is marked; what agrees is collapsed.</p>"
            f"<ul><li>Items with two readings: {len(data['views'])}</li>"
            f"<li>Entries: {_h(data['counts'])}</li><li>Read by only one of the two: {_h(single)}</li></ul>"
            + ("" if data["resolved"] else "<p class=diffmark>Offline: added works were not resolved and no quote was "
                                           "checked verbatim.</p>")
            + (f"<h3>Items to decide</h3><ul>{toc}</ul>" if toc else "<p>Nothing to decide.</p>"))
    items = "".join(_html_item(v, names) for v in data["views"])
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width, initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'\">"
            f"<title>Adjudication {_h(data['batch'])}</title><style>{CSS}</style></head>"
            f"<body><main>{head}{items}</main></body></html>\n")


def _counts(views: Sequence[Mapping]) -> str:
    kinds: dict[str, int] = {}
    for v in views:
        for e in v["entries"]:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) or "none"


def report_data(batch: Mapping, readings: Sequence[Reading], views: Sequence[Mapping], single: Mapping,
                offline: bool) -> dict:
    return {"batch": batch.get("batch"), "annotators": [r.annotator for r in readings],
            "created": date.today().isoformat(), "views": views, "single": dict(single), "resolved": not offline,
            "counts": _counts(views)}


# Applying the decisions.

def _one_line(reason) -> bool:
    return isinstance(reason, str) and bool(reason.strip()) and "\n" not in reason.strip() and "\r" not in reason


def _override_problems(key: str, entry: Mapping, override) -> list[str]:
    if override in (None, {}):
        return []
    if not isinstance(override, Mapping):
        return [f"{key}: override must be an object"]
    out = []
    if entry.get("decision") in (EXCLUDE, OMIT):
        return [f"{key}: an override needs a decision that keeps a reading, not {entry.get('decision')!r}"]
    allowed = entry.get("override_fields") or []
    for field, value in override.items():
        if field not in allowed:
            out.append(f"{key}: override field {field!r} is not one of {list(allowed)}")
        elif field == "relation" and value not in RELATIONS:
            out.append(f"{key}: override relation must be one of the six labels, got {value!r}")
        elif field in ELEMENT_FIELDS + ("coined_names",) and not (isinstance(value, list)
                                                                   and all(isinstance(v, str) for v in value)):
            out.append(f"{key}: override {field} must be a list of strings")
        elif field == "cross_field" and not isinstance(value, bool):
            out.append(f"{key}: override cross_field must be true or false")
        elif field == "attribution" and value not in ATTRIBUTIONS:
            out.append(f"{key}: override attribution must be one of {ATTRIBUTIONS}")
        elif field == "confidence" and (isinstance(value, bool) or value not in (1, 2, 3)):
            out.append(f"{key}: override confidence must be 1, 2 or 3")
        elif field == "chart" and not (isinstance(value, list) and all(
                isinstance(e, Mapping) and isinstance(e.get("id"), str) and isinstance(e.get("text"), str)
                and isinstance(e.get("key"), bool) and set(e) == {"id", "text", "key"} for e in value)):
            out.append(f"{key}: override chart must be a list of {{id, text, key}} elements")
    return out


def input_problems(decisions: Mapping, batch_path: Path, batch: Mapping, readings: Sequence[Reading],
                   offline: bool) -> list[str]:
    """The decisions file belongs to these inputs, unchanged since the report."""
    out = []
    if decisions.get("format") != DECISIONS_FORMAT:
        out.append(f"decisions: format must be {DECISIONS_FORMAT}")
    if decisions.get("batch") != batch.get("batch"):
        out.append(f"decisions: for batch {decisions.get('batch')!r}, not {batch.get('batch')!r}")
    if decisions.get("annotators") != [r.annotator for r in readings]:
        out.append(f"decisions: for readings {decisions.get('annotators')}, not {[r.annotator for r in readings]} "
                   "(give the labels files in the same order as for the report)")
    if decisions.get("resolved_works") is not (not offline):
        out.append("decisions: the report was made " + ("online" if decisions.get("resolved_works") else "offline") +
                   "; run apply the same way, or the report again")
    recorded = decisions.get("inputs") or {}
    if (recorded.get("batch") or {}).get("sha256") != sha256(batch_path):
        out.append("decisions: the batch file changed since the report; run the report again")
    by = {e.get("annotator"): e for e in recorded.get("labels") or [] if isinstance(e, Mapping)}
    for r in readings:
        if (by.get(r.annotator) or {}).get("sha256") != sha256(r.labels_path):
            out.append(f"decisions: the labels of {r.annotator} changed since the report; run the report again")
        recorded = by.get(r.annotator) or {}
        if recorded.get("batch_copy_sha256") != sha256(r.batch_path):
            was = recorded.get("batch_copy")
            if was and Path(was).resolve() != r.batch_path.resolve():
                out.append(f"decisions: the labels of {r.annotator} are now checked against {r.batch_path}, not "
                           f"{was} as in the report; run the report again")
            else:
                out.append(f"decisions: the batch copy {r.batch_path.name} changed since the report; run the report "
                           "again")
    return out


def _plain(value):
    """A value as it reads back from JSON, so options compare like apply compares them."""
    return json.loads(json.dumps(value))


def carry_over(old: Mapping, new: dict) -> tuple[int, list[str]]:
    """Copy the decisions of an older decisions file into a fresh template made from the current inputs: an entry's
    decision, reason and override are carried when the new file has an entry with the same key, kind, subject,
    options and allowed decisions (apply checks the options against the labels again); an item's claim_checked_by is
    carried when the item still needs one. Changes `new` in place; returns how many entries were carried and the keys
    of decided old entries that were not (their readings changed, so decide them again)."""
    old_entries = {}
    old_checked = {}
    for item in old.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("claim_checked_by") not in (None, ""):
            old_checked[item.get("id")] = item["claim_checked_by"]
        for entry in item.get("entries") or []:
            if isinstance(entry, Mapping) and entry.get("key") is not None:
                old_entries[entry["key"]] = entry
    carried, taken = 0, set()
    for item in new.get("items") or []:
        if "claim_checked_by" in item and item["id"] in old_checked:
            item["claim_checked_by"] = old_checked[item["id"]]
        for entry in item.get("entries") or []:
            was = old_entries.get(entry["key"])
            if was is None or was.get("decision") in (None, ""):
                continue
            same = all(_plain(was.get(f)) == _plain(entry.get(f)) for f in ("kind", "what", "options", "allowed"))
            if not same:
                continue
            entry["decision"] = was.get("decision")
            entry["reason"] = was.get("reason", "")
            entry["override"] = copy.deepcopy(was.get("override") or {})
            carried += 1
            taken.add(entry["key"])
    dropped = sorted(k for k, e in old_entries.items() if e.get("decision") not in (None, "") and k not in taken)
    return carried, dropped


def decision_problems(views: Sequence[Mapping], decisions: Mapping) -> tuple[dict[str, dict], dict[str, str],
                                                                            list[str]]:
    """Every entry of the views decided in the file, with an allowed decision, a one-line reason and a well-formed
    override. Returns the decided entries by key, claim_checked_by by item id and the problems."""
    filed: dict[str, dict] = {}
    checked: dict[str, str] = {}
    out = []
    for item in decisions.get("items") or []:
        if not isinstance(item, Mapping):
            out.append("decisions: an item is not an object")
            continue
        if item.get("claim_checked_by") not in (None, ""):
            checked[str(item.get("id"))] = item["claim_checked_by"]
        for entry in item.get("entries") or []:
            key = entry.get("key") if isinstance(entry, Mapping) else None
            if key in filed:
                out.append(f"{key}: appears twice in the decisions file")
            filed[str(key)] = entry
    expected = {e["key"]: e for v in views for e in v["entries"]}
    for key in filed:
        if key not in expected:
            out.append(f"{key}: not an entry of these labels (did the labels change since the report?)")
    for key, want in expected.items():
        entry = filed.get(key)
        if entry is None:
            out.append(f"{key}: missing from the decisions file; run the report again")
            continue
        if entry.get("options") != json.loads(json.dumps(want["options"])):
            out.append(f"{key}: the options differ from the labels; run the report again")
        decision = entry.get("decision")
        if decision in (None, ""):
            out.append(f"{key}: no decision (one of {want['allowed']})")
        elif decision not in want["allowed"]:
            out.append(f"{key}: decision {decision!r} is not one of {want['allowed']}")
        if not _one_line(entry.get("reason")):
            out.append(f"{key}: the reason must be one non-empty line")
        out += _override_problems(key, dict(want, decision=decision), entry.get("override"))
    for item_id, who in checked.items():
        if not isinstance(who, str) or not annotation.ANNOTATOR_RE.fullmatch(who):
            out.append(f"{item_id}: claim_checked_by must be a person's id, got {who!r}")
    return {k: filed[k] for k in expected if k in filed}, checked, out


def _apply_candidate_override(record: dict, override: Mapping, adjudicator: str) -> dict:
    was_gold = record.get("relation") in PRECEDENCE
    record.update({k: (list(v) if isinstance(v, list) else v) for k, v in override.items()})
    record["gold"] = record.get("relation") in PRECEDENCE
    if not record["gold"]:
        record["field_confirmed_by"] = None
    elif "cross_field" in override or not was_gold:
        record["field_confirmed_by"] = adjudicator
    return record


def build_item(view: Mapping, decided: Mapping[str, Mapping], claim_checked_by: str | None, batch_name: str,
               adjudicator: str) -> tuple[dict, list[str]]:
    """The adjudicated item of one view under the decisions, and what is wrong with them for this item."""
    names = view["annotators"]
    item_id = view["id"]
    out = []
    claim_entry = decided.get(f"{item_id}/claim")
    base = claim_entry["decision"] if claim_entry and claim_entry["decision"] in names else names[0]
    base_item = view["sides"][base]["item"]
    item = copy.deepcopy({k: v for k, v in base_item.items() if k not in ("candidates", "annotation")})
    claim = item["claim"]
    if claim_entry and claim_entry.get("override"):
        for field, value in claim_entry["override"].items():
            claim[field] = copy.deepcopy(value)
    if claim.get("written_by") not in (None, annotation.PREFILL_AUTHOR):
        checker = claim_checked_by or claim.get("checked_by")
        if not checker:
            out.append(f"{item_id}: the claim kept was written by annotator {claim['written_by']} (a bare packet or a "
                       "contrast item); name the person who checked it against the source in claim_checked_by")
        elif checker == claim["written_by"]:
            out.append(f"{item_id}: claim_checked_by must be a person other than the writer {claim['written_by']}")
        else:
            claim["checked_by"] = checker
    chart_ids = {e.get("id") for e in claim.get("chart") or []}
    candidates, keys = [], []
    for cv in view["candidates"]:
        key = f"{item_id}/{cv['key']}"
        entry = decided.get(key)
        group = cv["group"]
        if len(group) == 1:
            (adder, cand), = group.items()
            if entry is not None and entry["decision"] == OMIT:
                continue
            who = adder
        else:
            who = entry["decision"] if entry is not None and entry["decision"] in names else base
            cand = group[who]
        record = copy.deepcopy(cand["record"])
        if len(group) == 2 and cv["added"]:
            record["found_by"] = sorted({f for c in group.values() for f in c["record"].get("found_by") or []})
        if entry is not None and entry.get("override"):
            _apply_candidate_override(record, entry["override"], adjudicator)
        used = {x for f in ELEMENT_FIELDS for x in record.get(f) or []}
        used |= {ev.get("element") for ev in record.get("evidence") or []}
        stray = sorted(used - chart_ids, key=str)
        if stray:
            out.append(f"{key}: the labels kept ({who}'s) refer to elements {stray} that are not in the chart kept "
                       f"({base}'s); keep the same reading for the claim, or override the element lists")
        candidates.append(record)
        keys.append(cv["key"])
    item["candidates"] = candidates
    item["verdict"] = derive_verdict(item)
    excluding = [e for e in view["entries"] if (decided.get(e["key"]) or {}).get("decision") == EXCLUDE]
    if excluding:
        item["status"] = "excluded"
        item["exclusion_reason"] = ("annotators_disagree" if any(e["kind"] == "disagreement" for e in excluding)
                                    else "excluded_at_adjudication")
    else:
        item["status"] = "adjudicated"
    annotators = []
    for n in names:
        for a in view["sides"][n]["item"]["annotation"]["annotators"]:
            annotators.append(dict(a, prefill_status="none" if view["sides"][n]["bare"] else "draft"))
    notes = []
    for n in names:
        text = view["sides"][n]["item"]["annotation"].get("notes")
        notes += [f"{n}: {line}" for line in (text or "").splitlines() if line.strip()]
    record = []
    for e in view["entries"]:
        d = decided.get(e["key"]) or {}
        line = {"key": e["key"], "kind": e["kind"], "what": e["what"], "decision": d.get("decision"),
                "reason": (d.get("reason") or "").strip()}
        if d.get("override"):
            line["override"] = d["override"]
        record.append(line)
    item["annotation"] = {"annotators": annotators, "prefill": base_item["annotation"].get("prefill"),
                          "batch": batch_name, "adjudicated_by": adjudicator, "adjudication": record,
                          "notes": "\n".join(notes) or None}
    order = ("id", "version", "status", "exclusion_reason", "claim", "candidates", "verdict")
    item = {**{k: item[k] for k in order if k in item}, **{k: v for k, v in item.items() if k not in order}}
    item["_keys"] = keys
    return item, out


def _name_candidates(problem: str, keys: Sequence[str]) -> str:
    return re.sub(r"candidate (\d+)", lambda m: f"candidate {m.group(1)} ({keys[int(m.group(1))]})"
                  if int(m.group(1)) < len(keys) else m.group(0), problem)


def apply(batch_path: Path, labels_paths: Sequence[Path], decisions: Mapping, offline: bool = False,
          fetch: Callable[[str], dict] | None = None, checker: QuoteChecker | None = None
          ) -> tuple[list[dict], list[str], list[str]]:
    """The adjudicated items (empty when anything is wrong), the problems and the notes."""
    checker = checker or QuoteChecker()
    batch, readings, _, views, single, problems, notes = prepare(batch_path, labels_paths, offline, fetch, checker)
    if problems:
        return [], problems, notes
    problems += input_problems(decisions, Path(batch_path), batch, readings, offline)
    decided, checked, found = decision_problems(views, decisions)
    problems += found
    adjudicator = decisions.get("adjudicator") or ADJUDICATOR
    if not isinstance(adjudicator, str) or not annotation.ANNOTATOR_RE.fullmatch(adjudicator):
        problems.append(f"decisions: adjudicator must be an id such as damir, got {adjudicator!r}")
    if problems:
        return [], problems, notes
    labeling = REPO_ROOT / "eval" / "benchmark" / "LABELING.md"
    guide = guide_dois(labeling.read_text(encoding="utf-8")) if labeling.exists() else frozenset()
    items = []
    for view in views:
        item, wrong = build_item(view, decided, checked.get(view["id"]), batch.get("batch"), adjudicator)
        keys = item.pop("_keys")
        problems += wrong
        problems += [f"{view['id']}: {_name_candidates(p, keys)}"
                     for p in validate(item, checker, guide, check_quotes=not offline)]
        items.append(item)
    for item_id, who in single.items():
        notes.append(f"{item_id}: read by {who} only of these two; adjudicate it with its other reader's labels, or it "
                     "stays labelled (decision 4)")
    if offline:
        notes.append("offline: quotes were not checked verbatim")
    return ([] if problems else items), problems, notes


# Commands.

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.adjudicate")
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("report", help="write report.md, report.html and adjudication.json (the decisions to fill)")
    rp.add_argument("batch")
    rp.add_argument("labels", nargs=2)
    rp.add_argument("--out-dir", help="default: eval/benchmark/v0/adjudication/<batch>-<id 1>-<id 2>/")
    rp.add_argument("--offline", action="store_true", help="do not resolve added works and check no quote verbatim")
    rp.add_argument("--adjudicator", default=ADJUDICATOR)
    replace = rp.add_mutually_exclusive_group()
    replace.add_argument("--force", action="store_true", help="replace an existing adjudication.json with an empty one")
    replace.add_argument("--carry-over", action="store_true",
                    help="replace an existing adjudication.json with one made from the current inputs, carrying over "
                         "every decision whose entry is unchanged; the old file is kept as "
                         "adjudication.replaced-<sha256 prefix>.json")
    ap = sub.add_parser("apply", help="check the filled decisions and write the adjudicated items (JSONL)")
    ap.add_argument("batch")
    ap.add_argument("labels", nargs=2)
    ap.add_argument("decisions")
    ap.add_argument("out")
    ap.add_argument("--offline", action="store_true", help="as for the report: no lookups, no verbatim checks")
    args = parser.parse_args(argv)
    batch_path = Path(args.batch)
    if args.command == "report":
        batch, readings, _, views, single, problems, notes = prepare(batch_path, args.labels, args.offline)
        for problem in problems:
            print(problem)
        if problems:
            print(f"nothing written: {len(problems)} problems (the same gate as annotation merge); fix them first")
            return 1
        pair = "-".join([str(batch.get("batch"))] + [r.annotator for r in readings])
        out_dir = Path(args.out_dir) if args.out_dir else ADJUDICATION_DIR / pair
        out_dir.mkdir(parents=True, exist_ok=True)
        data = report_data(batch, readings, views, single, args.offline)
        (out_dir / "report.md").write_text(render_markdown(data), encoding="utf-8")
        (out_dir / "report.html").write_text(render_html(data), encoding="utf-8")
        decisions_path = out_dir / "adjudication.json"
        exists = decisions_path.exists()
        kept = exists and not args.force and not args.carry_over
        stale, carried, dropped, backup = [], None, [], None
        if kept:
            try:
                stale = input_problems(read_json(decisions_path), batch_path, batch, readings, args.offline)
            except (OSError, ValueError, AttributeError) as err:
                stale = [f"decisions: {decisions_path.name} cannot be read ({err})"]
        else:
            template = decisions_template(batch, readings, views, single, inputs_record(batch_path, readings),
                                          args.offline, args.adjudicator)
            if exists and args.carry_over:
                old_text = decisions_path.read_text(encoding="utf-8")
                digest = hashlib.sha256(old_text.encode("utf-8")).hexdigest()[:12]
                backup = out_dir / f"adjudication.replaced-{digest}.json"
                backup.write_text(old_text, encoding="utf-8")
                carried, dropped = carry_over(json.loads(old_text), template)
            decisions_path.write_text(json.dumps(template, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for note in notes:
            print(f"note: {note}")
        n_entries = sum(len(v["entries"]) for v in views)
        print(f"{len(views)} items with two readings, {sum(1 for v in views if v['entries'])} with something to "
              f"decide ({n_entries} entries: {_counts(views)}); one reading only: {len(single)}")
        if kept:
            print(f"wrote {out_dir / 'report.md'} and {out_dir / 'report.html'}; kept the existing {decisions_path} "
                  "(it may hold decisions; --force replaces it)")
            if stale:
                print(f"warning: the kept {decisions_path.name} was made from other inputs, so apply will refuse it:")
                for problem in stale:
                    print(f"  {problem}")
                print("  run the report again with --carry-over: it writes the file anew from these inputs and carries "
                      "over every decision whose entry did not change (the old file is kept beside it)")
        else:
            print(f"wrote {out_dir / 'report.md'}, {out_dir / 'report.html'} and {decisions_path}")
            if carried is not None:
                print(f"carried over {carried} decisions from the old file, kept as {backup.name}")
                if dropped:
                    print(f"decide again (their readings changed): {', '.join(dropped)}")
        return 0
    decisions = read_json(Path(args.decisions))
    items, problems, notes = apply(batch_path, args.labels, decisions, args.offline)
    for problem in problems:
        print(problem)
    if problems:
        print(f"nothing written: {len(problems)} problems")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    for note in notes:
        print(f"note: {note}")
    excluded = sum(1 for i in items if i["status"] == "excluded")
    print(f"wrote {len(items)} items to {out} ({len(items) - excluded} adjudicated, {excluded} excluded); each passes "
          "items.validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
