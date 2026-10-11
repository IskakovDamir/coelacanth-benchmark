"""Contrast items (eval/protocol.md decision 14; SCHEMA.md `negative` and rule 7; LABELING.md section 5, "Contrast
items"; the annotators' procedure in Russian: docs/annotators/kontrastnye-primery.md).

A contrast item is an adjudicated rediscovery positive (the pivot) in which one annotator, the writer, edits one key
chart element so that it no longer has the same meaning as in the pivot's gold candidates, keeping the source field's
vocabulary, the claim rules and everything else. No model drafts or tries the edit. The writer and the other
annotator, the checker, then each label the contrast item in tools/annotate.html like any item, the checker blind to
which element was edited and to the writer's intended labels, and Damir adjudicates the two readings with
coelacanth.benchmark.adjudicate. Whether a contrast item is a negative follows from its adjudicated verdict (`N` holds
the `nothing_close` items), never from how it was built (SCHEMA.md rule 7).

    PYTHONPATH=src python -m coelacanth.benchmark.contrast pivots <items.jsonl> [--seed N] [--out FILE]
    PYTHONPATH=src python -m coelacanth.benchmark.contrast template <items.jsonl> <pivot id> --id cf-NNNN \\
        --writer a1 --checker a2 [--out FILE]
    PYTHONPATH=src python -m coelacanth.benchmark.contrast check <items.jsonl> <contrast.json> [...]
    PYTHONPATH=src python -m coelacanth.benchmark.contrast packet <items.jsonl> <batch> <contrast.json> [...] \\
        [--out-dir DIR]
    PYTHONPATH=src python -m coelacanth.benchmark.contrast cache <contrast.json> [...]
    PYTHONPATH=src python -m coelacanth.benchmark.contrast finish <adjudicated.jsonl> <out.jsonl> [--offline]

`pivots` prints the eligible pivots of an items file in a seeded order (or writes it to --out, never over an existing
file), stratified by `provenance.source` and `claim.computational`: each stratum in a seeded order, the strata in a
seeded order, taken round robin. A pivot is eligible when it is adjudicated, has a gold candidate that its source
does not cite (a rediscovery, not only an attributed import of subset C), was not itself built as a negative, is of
human origin (AI-origin items form stratum A, decision 15) and is not a guide example. Damir hands pivots out in this
order, alternating writers; a pivot for which the writer finds no edit that keeps the claim coherent and does not turn
it into another known method or result is skipped, and the skip is logged. The order never depends on what any system
found.

`template` writes the contrast file of one pivot (default eval/benchmark/v0/contrast/<id>.json, never over an existing
file), format CONTRAST_FORMAT:

    {"format", "id": the new item's id, "pivot_id", "writer", "checker", "created", "how_to_fill",
     "edited_element": "",       # the writer fills these six: the id of the one key element edited,
     "new_element_text": "",     # its new text,
     "claim_text": "...",        # the claim text, the pivot's to start with, edited where the element appears,
     "skeleton": "...",          # the skeleton, likewise,
     "what_changed": "",         # in English, for Damir: what changed and why the gold candidates lose the element,
     "minutes": null,            # the minutes spent writing,
     "pivot": {"claim_text", "skeleton", "coined_names", "chart": [{"id", "text", "key"}],
               "candidates": [{"cid", "title", "first_author", "year", "field", "relation", "gold",
                               "covered_elements", "analogous_elements", "restricted_elements"}]}}

The pivot section is the adjudicated pivot as the writer needs it (judged candidates numbered c1, c2, ... in the
pivot's order, the numbering of the contrast packet) and must not be edited. The new id must be unused in the items
file, the batches and the contrast folder, and above every id used there, so that no skipped number is reused.

`check` reports what is wrong with filled contrast files, each against its pivot in the items file: the pivot still
eligible and unchanged since the template; the edited element a key element of the pivot that a gold candidate covers
or matches by role only; its new text, the claim text and the skeleton all changed; the claim text at most 120 words
and passing rule 5's script check (no author surname of the source or a candidate, no three consecutive title words,
no year, no coined name; skeleton present and different); writer and checker two different annotator IDs; the minutes
above 0; and the meta the item will carry (rule 7's `negative` record with `from_item` and `edited_element`,
provenance, exposure, origin, found_by) passing annotation.meta_problems. Notes give the word-level difference
between the pivot's claim text and the edited one, so that a person can see that only the edited element changed:
whether the edit keeps the field's vocabulary and changes the meaning of exactly one element is a person's check.

`packet` checks the contrast files, then writes the contrast batch like any batch, so tools/annotate.html,
annotation check-labels and merge, agreement and adjudicate work on it unchanged: `<batch>.json` with every contrast
packet, `<batch>-meta.json`, and in `assigned/` one copy per annotator naming them, holding the items they wrote and
the items they check. A contrast packet carries the writer's claim text, skeleton and chart (key flags null), the
pivot's coined names, date, field, domain and judged candidate works (no evidence, attribution hints `unknown`,
generated neutral notes) and the standard instruction CONTRAST_NOTES; `prefill.by` is `annotator:<writer>`. Its source
work is the pivot's with `cache_key` `ctr-<id>`, whose cached text (written to cache/fulltext/) is the claim text
itself: claim-side quotes come from the claim text, since no source states the edited element. The meta records
`negative: {kind: contrast, from_item, edited_element}`, provenance `{source: negative, mined_by: manual}`, the
pivot's origin, found_by and exclusions (the pivot's source work included), a null exposure, and the writer's
`what_changed` as `prefill_assessment`, which only the adjudicator sees. Nothing is written while a contrast file has
a problem, an id is already used, or the batch file exists.

`cache` writes the cached claim texts (`ctr-<id>`) again from the contrast files, for a machine whose cache/ lacks
them (cache/ is not in git); the quote checks of check-labels, adjudicate and finish need them.

`finish` checks the adjudicated contrast items (adjudicate apply's output) against their contrast files and writes
them unchanged once they pass. annotation.merge records the writer named in `prefill.by` as `claim.written_by`, and
at adjudication `claim_checked_by` names the checker, the person who compared the claim with the source field's
wording (SCHEMA.md rule 5). It refuses an item whose writer or checker is not the contrast file's, whose claim text,
skeleton or chart differs from its contrast file (readers do not edit a contrast claim; such an item is excluded at
adjudication), whose `negative` record differs, or whose readings do not include both the writer and the checker;
every item must then pass items.validate (quotes checked unless --offline). Nothing is written while any problem
remains.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import random
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from ..env import REPO_ROOT
from ..quotes import FULLTEXT_DIR, QuoteChecker, normalize
from .annotation import BATCH_FORMAT, BATCHES_DIR, META_FORMAT, meta_problems, neutral_note, packet_problems
from .assign import ANNOTATOR_ID_RE
from .items import (ID_RE, MAX_CLAIM_WORDS, RELATIONS, claim_text_problems, guide_dois, load_items, validate)

CONTRAST_FORMAT = "coelacanth-contrast/v1"
PIVOTS_FORMAT = "coelacanth-contrast-pivots/v1"
CONTRAST_DIR = REPO_ROOT / "eval" / "benchmark" / "v0" / "contrast"
CACHE_PREFIX = "ctr-"
SEED = 20261006  # the v0 draws' seed (coelacanth.mining.draw), salted with "contrast"
FILL_FIELDS = ("edited_element", "new_element_text", "claim_text", "skeleton", "what_changed", "minutes")
FIXED_FIELDS = ("format", "id", "pivot_id", "writer", "checker", "created", "how_to_fill", "pivot")
HOW_TO_FILL = ("Fill the six fields edited_element to minutes and nothing else "
               "(docs/annotators/kontrastnye-primery.md): the id of the one key element you change, its new text, "
               "the claim text and the skeleton rewritten where that element appears, in English what you changed "
               "and why the gold candidates no longer have the element with the same meaning, and your minutes. The "
               "pivot section is for reading only.")
CONTRAST_NOTES = ("Contrast example: another annotator wrote this claim from an item you have labelled, with one part "
                  "changed. Label it like any other item and search for earlier work yourself. Copy every claim-side "
                  "quote from this item's claim text. Do not edit the claim text, the skeleton or the chart; write "
                  "any problem with them in your notes.")
PREFILL_FIELDS = ["claim.text", "claim.skeleton", "claim.chart"]


# Pivots.

def pivot_problems(item: Mapping) -> list[str]:
    """Why an item cannot be a contrast pivot; an empty list means it is eligible."""
    out = []
    if item.get("status") != "adjudicated":
        out.append(f"status is {item.get('status')!r}, not 'adjudicated'")
    gold = [c for c in item.get("candidates") or [] if c.get("gold") is True]
    if not gold:
        out.append("no gold candidate: not a positive")
    elif all(c.get("attribution") == "cited" for c in gold):
        out.append("every gold candidate is cited by the source: an attributed import (subset C), not a rediscovery")
    if item.get("negative") is not None:
        out.append("built as a negative itself")
    if (item.get("origin") or {}).get("kind") != "human":
        out.append("not of human origin (AI-origin items form stratum A)")
    if (item.get("provenance") or {}).get("source") == "guide":
        out.append("a guide example")
    return out


def judged(item: Mapping) -> list[Mapping]:
    """The pivot's judged candidates, in its order; the contrast packet numbers them c1, c2, ..."""
    return [c for c in item.get("candidates") or [] if c.get("relation") is not None]


def pivot_order(items: Sequence[Mapping], seed: int = SEED) -> dict:
    """The eligible pivots in a seeded order, stratified by provenance.source and claim.computational."""
    rng = random.Random(f"{seed}:contrast")
    strata: dict[str, list[str]] = defaultdict(list)
    ineligible = {}
    for item in sorted(items, key=lambda i: str(i.get("id"))):
        problems = pivot_problems(item)
        if problems:
            ineligible[item.get("id")] = problems
            continue
        computational = (item.get("claim") or {}).get("computational")
        strata[f"{(item.get('provenance') or {}).get('source')} | "
               f"{'computational' if computational else 'non-computational'}"].append(item["id"])
    for ids in strata.values():
        rng.shuffle(ids)
    stratum_order = sorted(strata)
    rng.shuffle(stratum_order)
    order, depth = [], 0
    while len(order) < sum(len(v) for v in strata.values()):
        order += [strata[s][depth] for s in stratum_order if depth < len(strata[s])]
        depth += 1
    return {"format": PIVOTS_FORMAT, "seed": seed, "stratum_order": stratum_order,
            "strata": {s: strata[s] for s in stratum_order}, "order": order, "ineligible": ineligible}


# Templates.

def pivot_snapshot(item: Mapping) -> dict:
    """What the writer needs of the adjudicated pivot."""
    claim = item.get("claim") or {}
    cands = []
    for n, c in enumerate(judged(item), 1):
        work = c.get("work") or {}
        cands.append({"cid": f"c{n}", "title": work.get("title"), "first_author": work.get("first_author"),
                      "year": work.get("year"), "field": work.get("field"), "relation": c.get("relation"),
                      "gold": c.get("gold"), "covered_elements": list(c.get("covered_elements") or []),
                      "analogous_elements": list(c.get("analogous_elements") or []),
                      "restricted_elements": list(c.get("restricted_elements") or [])})
    return {"claim_text": claim.get("text"), "skeleton": claim.get("skeleton"),
            "coined_names": list(claim.get("coined_names") or []),
            "chart": [{"id": e.get("id"), "text": e.get("text"), "key": e.get("key")}
                      for e in claim.get("chart") or []],
            "candidates": cands}


def used_ids(items: Sequence[Mapping], batches_dir: Path = BATCHES_DIR,
             contrast_dir: Path | None = CONTRAST_DIR) -> set[str]:
    """Item ids used in the items file, in every batch file (and personal copy) and in the contrast folder (None
    leaves the contrast folder out)."""
    ids = {str(i.get("id")) for i in items}
    for path in sorted(batches_dir.glob("*.json")) + sorted((batches_dir / "assigned").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, Mapping) and data.get("format") == BATCH_FORMAT:
            ids |= {str(p.get("id")) for p in data.get("items") or [] if isinstance(p, Mapping)}
    for path in sorted(contrast_dir.glob("cf-*.json")) if contrast_dir is not None and contrast_dir.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ids.add(path.stem)
            continue
        ids.add(str(data.get("id") if isinstance(data, Mapping) and data.get("id") else path.stem))
    return {i for i in ids if ID_RE.fullmatch(i)}


def id_problems(new_id: str, used: set[str]) -> list[str]:
    if not isinstance(new_id, str) or not ID_RE.fullmatch(new_id):
        return [f"id must look like cf-0042, got {new_id!r}"]
    top = max(used, default="cf-0000")
    if new_id in used:
        return [f"id {new_id} is already used; the next free id is cf-{int(top[3:]) + 1:04d}"]
    if new_id < top:
        return [f"id {new_id} is below {top}, the highest id in use: ids are never reused; the next free id is "
                f"cf-{int(top[3:]) + 1:04d}"]
    return []


def person_problems(writer, checker) -> list[str]:
    out = [f"{role} must be an annotator ID such as a1, got {who!r}" for role, who in (("writer", writer),
                                                                                      ("checker", checker))
           if not (isinstance(who, str) and ANNOTATOR_ID_RE.fullmatch(who))]
    if not out and writer == checker:
        out.append("the checker must be a different annotator from the writer")
    return out


def template(pivot: Mapping, new_id: str, writer: str, checker: str, created: str | None = None) -> dict:
    """The contrast file for one pivot, the fill fields started from the pivot's claim text and skeleton. Raises
    ValueError when the pivot is not eligible or the people are not two annotator IDs (the id is checked by the
    caller, against what is in use)."""
    problems = [f"pivot {pivot.get('id')}: {p}" for p in pivot_problems(pivot)] + person_problems(writer, checker)
    if problems:
        raise ValueError("; ".join(problems))
    snapshot = pivot_snapshot(pivot)
    return {"format": CONTRAST_FORMAT, "id": new_id, "pivot_id": pivot["id"], "writer": writer, "checker": checker,
            "created": created or date.today().isoformat(), "how_to_fill": HOW_TO_FILL,
            "edited_element": "", "new_element_text": "", "claim_text": snapshot["claim_text"],
            "skeleton": snapshot["skeleton"], "what_changed": "", "minutes": None, "pivot": snapshot}


# Checks.

def _same(a, b) -> bool:
    return " ".join(normalize(str(a or "")).casefold().split()) == " ".join(normalize(str(b or "")).casefold().split())


def edited_chart(contrast: Mapping, chart: Sequence[Mapping]) -> list[dict]:
    """A pivot chart with the edited element's text replaced, key flags left to the annotators (null)."""
    eid = contrast.get("edited_element")
    return [{"id": e.get("id"), "text": contrast.get("new_element_text") if e.get("id") == eid else e.get("text"),
             "key": None} for e in chart]


def contrast_meta(contrast: Mapping, pivot: Mapping) -> dict:
    """The hidden meta of a contrast item (annotation.py's meta shape)."""
    provenance = pivot.get("provenance") or {}
    return {"seed": pivot.get("id"),
            "seed_detail": {"kind": "contrast", "pivot": pivot.get("id"), "writer": contrast.get("writer"),
                            "checker": contrast.get("checker"), "edited_element": contrast.get("edited_element")},
            "found_by": {f"c{n}": list(c.get("found_by") or []) for n, c in enumerate(judged(pivot), 1)},
            "exposure": {"kind": None, "work": None, "date": None},
            "origin": copy.deepcopy(pivot.get("origin")),
            "negative": {"kind": "contrast", "from_item": pivot.get("id"),
                         "edited_element": contrast.get("edited_element")},
            "provenance": {"source": "negative", "mined_by": "manual",
                           "dataset": f"contrast edit of {pivot.get('id')}",
                           "license": provenance.get("license") or "CC-BY-4.0",
                           "attribution": provenance.get("attribution")},
            "exclusions": list(pivot.get("exclusions") or []),
            "prefill_assessment": (f"Contrast edit of {pivot.get('id')} by {contrast.get('writer')}, element "
                                   f"{contrast.get('edited_element')}: {contrast.get('what_changed')}"),
            "drafting_notes": (f"Edited by {contrast.get('writer')} from {pivot.get('id')}; labelled blind by "
                               f"{contrast.get('checker')}.")}


def word_changes(old: str, new: str) -> list[str]:
    """The runs of words that differ between two texts, as '"old words" -> "new words"'; changes at most two equal
    words apart form one run, so that a rewritten phrase reads as one change."""
    a, b = str(old or "").split(), str(new or "").split()
    runs: list[list[int]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if runs and i1 - runs[-1][1] <= 2 and j1 - runs[-1][3] <= 2:
            runs[-1][1], runs[-1][3] = i2, j2
        else:
            runs.append([i1, i2, j1, j2])
    return [f'"{" ".join(a[i1:i2])}" -> "{" ".join(b[j1:j2])}"' for i1, i2, j1, j2 in runs]


def check_contrast(contrast, pivot: Mapping | None) -> tuple[list[str], list[str]]:
    """(problems, notes) of a filled contrast file against its pivot (None when the items file lacks it)."""
    if not isinstance(contrast, Mapping):
        return ["the file is not a JSON object"], []
    out, notes = [], []
    missing = [k for k in FIXED_FIELDS + FILL_FIELDS if k not in contrast]
    extra = sorted(set(contrast) - set(FIXED_FIELDS + FILL_FIELDS))
    if missing:
        out.append(f"fields missing: {missing}")
    if extra:
        out.append(f"unknown fields (fill only the six fields): {extra}")
    if contrast.get("format") != CONTRAST_FORMAT:
        out.append(f"format must be {CONTRAST_FORMAT}")
    new_id = contrast.get("id")
    if not (isinstance(new_id, str) and ID_RE.fullmatch(new_id)):
        out.append(f"id must look like cf-0042, got {new_id!r}")
    out += person_problems(contrast.get("writer"), contrast.get("checker"))
    if pivot is None:
        return out + [f"pivot {contrast.get('pivot_id')!r} is not in the items file"], notes
    if new_id == pivot.get("id"):
        out.append("the contrast item needs its own id, not the pivot's")
    out += [f"pivot {pivot.get('id')}: {p}" for p in pivot_problems(pivot)]
    if contrast.get("pivot") != pivot_snapshot(pivot):
        out.append(f"the pivot section differs from {pivot.get('id')} in the items file: it was edited, or the "
                   "pivot changed since the template was written (write a new template)")
    claim = pivot.get("claim") or {}
    chart = {e.get("id"): e for e in claim.get("chart") or []}
    eid = contrast.get("edited_element")
    if eid not in chart:
        out.append(f"edited_element must be the id of one element of the pivot's chart ({', '.join(chart)}), "
                   f"got {eid!r}")
    else:
        if chart[eid].get("key") is not True:
            out.append(f"edited_element {eid} is not a key element of the pivot: edit a key element")
        matched = [f"c{n}" for n, c in enumerate(judged(pivot), 1) if c.get("gold") is True
                   and eid in set(c.get("covered_elements") or []) | set(c.get("analogous_elements") or [])]
        if not matched:
            out.append(f"no gold candidate of the pivot has {eid} (covered or by role): editing it changes no "
                       "candidate's label; edit an element a gold candidate has")
        new_text = contrast.get("new_element_text")
        if not isinstance(new_text, str) or not new_text.strip():
            out.append("new_element_text is empty")
        elif _same(new_text, chart[eid].get("text")):
            out.append(f"new_element_text is the pivot's text of {eid}: nothing was edited")
    for field, pivot_value in (("claim_text", claim.get("text")), ("skeleton", claim.get("skeleton"))):
        value = contrast.get(field)
        if not isinstance(value, str) or not value.strip():
            out.append(f"{field} is empty")
        elif _same(value, pivot_value):
            out.append(f"{field} is still the pivot's: rewrite it where the edited element appears")
    text = contrast.get("claim_text") if isinstance(contrast.get("claim_text"), str) else ""
    if len(text.split()) > MAX_CLAIM_WORDS:
        out.append(f"claim_text has {len(text.split())} words; the limit is {MAX_CLAIM_WORDS}")
    pseudo = {"claim": {"text": text, "skeleton": contrast.get("skeleton") or "",
                        "coined_names": claim.get("coined_names") or [], "source_work": claim.get("source_work")},
              "candidates": judged(pivot)}
    out += [f"rule 5: {p}" for p in claim_text_problems(pseudo) if p != "claim.skeleton is missing"]  # said above
    what = contrast.get("what_changed")
    if not isinstance(what, str) or len(what.split()) < 5:
        out.append("what_changed is empty or too short: say in English what you changed and why the gold candidates "
                   "no longer have the element with the same meaning")
    minutes = contrast.get("minutes")
    if not (isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes > 0):
        out.append(f"minutes must be a number above 0, got {minutes!r}")
    out += meta_problems(contrast_meta(contrast, pivot), [f"c{n}" for n in range(1, len(judged(pivot)) + 1)])
    changes = word_changes(claim.get("text"), text)
    notes.append(f"claim text, {len(changes)} changed run{'s' if len(changes) != 1 else ''} of words: "
                 + ("; ".join(changes) or "none"))
    if eid in chart and isinstance(contrast.get("new_element_text"), str):
        notes.append(f"{eid}: {'; '.join(word_changes(chart[eid].get('text'), contrast['new_element_text']))}")
    return out, notes


# Packets.

def build_packet(contrast: Mapping, pivot: Mapping, created: str) -> dict:
    """The contrast item as a batch packet (annotation.py's packet shape)."""
    claim = pivot["claim"]
    source = copy.deepcopy(claim.get("source_work") or {})
    source.pop("method_text", None)
    source["cache_key"] = f"{CACHE_PREFIX}{contrast['id']}"
    candidates = []
    for n, c in enumerate(judged(pivot), 1):
        cand = {"cid": f"c{n}", "work": copy.deepcopy(c.get("work") or {}), "evidence": [],
                "attribution_hint": "unknown"}
        cand["note"] = neutral_note(cand)
        candidates.append(cand)
    return {"id": contrast["id"],
            "prefill": {"by": f"annotator:{contrast['writer']}", "date": created, "status": "draft",
                        "fields": list(PREFILL_FIELDS)},
            "claim": {"text": contrast["claim_text"], "skeleton": contrast["skeleton"],
                      "coined_names": list(claim.get("coined_names") or []), "date": claim.get("date"),
                      "field": claim.get("field"), "domain": claim.get("domain"),
                      "computational": claim.get("computational"), "source_work": source,
                      "chart": edited_chart(contrast, claim.get("chart") or [])},
            "candidates": candidates, "notes": CONTRAST_NOTES}


def write_texts(contrasts: Sequence[Mapping], text_dir: Path = FULLTEXT_DIR) -> list[Path]:
    """Cache each contrast item's claim text as its source's text, `ctr-<id>.txt`; returns the paths written."""
    text_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for c in contrasts:
        if not (isinstance(c.get("id"), str) and ID_RE.fullmatch(c["id"]) and isinstance(c.get("claim_text"), str)):
            raise ValueError(f"a contrast file needs an id and a claim_text, got id {c.get('id')!r}")
        path = text_dir / f"{CACHE_PREFIX}{c['id']}.txt"
        path.write_text(c["claim_text"], encoding="utf-8")
        paths.append(path)
    return paths


def _batch(name: str, created: str, packets: list[dict], annotator: str | None = None) -> dict:
    out = {"format": BATCH_FORMAT, "batch": name}
    if annotator is not None:
        out["annotator"] = annotator
    out.update({"created": created, "labels": list(RELATIONS), "items": packets})
    return out


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_packets(contrasts: Sequence[Mapping], items: Sequence[Mapping], batch: str, out_dir: Path = BATCHES_DIR,
                  text_dir: Path = FULLTEXT_DIR, created: str | None = None,
                  checker: QuoteChecker | None = None) -> dict:
    """Write the contrast batch, its meta, one copy per annotator and the cached claim texts; returns the report.
    Raises ValueError, writing nothing, on any problem of the contrast files, a used id or an existing batch."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", batch or ""):
        raise ValueError(f"the batch name must be letters, digits, - and _, got {batch!r}")
    if (out_dir / f"{batch}.json").exists():
        raise ValueError(f"{out_dir / f'{batch}.json'} exists: a batch annotators may be labelling is never "
                         "overwritten; choose a new batch name")
    by_id = {i.get("id"): i for i in items}
    problems = []
    ids = [c.get("id") if isinstance(c, Mapping) else None for c in contrasts]
    if len(set(ids)) != len(ids):
        problems.append(f"contrast ids repeat: {ids}")
    taken = used_ids(items, out_dir, None)  # the contrast folder holds these very ids
    problems += [f"{i}: already used by an item or a batch" for i in ids if i in taken]
    for c in contrasts:
        found, _ = check_contrast(c, by_id.get(c.get("pivot_id")) if isinstance(c, Mapping) else None)
        problems += [f"{c.get('id') if isinstance(c, Mapping) else '?'}: {p}" for p in found]
    if problems:
        raise ValueError("; ".join(problems))
    created = created or date.today().isoformat()
    pairs = sorted(contrasts, key=lambda c: c["id"])
    packets = [build_packet(c, by_id[c["pivot_id"]], created) for c in pairs]
    metas = {c["id"]: contrast_meta(c, by_id[c["pivot_id"]]) for c in pairs}
    annotators = []
    for c in pairs:
        for who in (c["writer"], c["checker"]):
            if who not in annotators:
                annotators.append(who)
    write_texts(pairs, text_dir)
    _dump(out_dir / f"{batch}.json", _batch(batch, created, packets))
    _dump(out_dir / f"{batch}-meta.json", {"format": META_FORMAT, "batch": batch, "items": metas})
    checker = checker or QuoteChecker()
    report = {"batch": batch, "items": [p["id"] for p in packets], "copies": {}}
    for who in annotators:
        mine = [p for p, c in zip(packets, pairs) if who in (c["writer"], c["checker"])]
        path = out_dir / "assigned" / f"{batch}-{who}.json"
        _dump(path, _batch(batch, created, mine, who))
        found = {p["id"]: packet_problems(p, metas[p["id"]], checker, text_dir) for p in mine}
        report["copies"][path.name] = {
            "writes": [c["id"] for c in pairs if c["writer"] == who],
            "checks blind": [c["id"] for c in pairs if c["checker"] == who],
            "problems": {k: v for k, v in found.items() if v}}
    return report


# Finishing adjudicated contrast items.

def finish(items: Sequence[Mapping], contrasts: Mapping[str, Mapping], checker: QuoteChecker | None = None,
           guide: frozenset = frozenset(), check_quotes: bool = True) -> tuple[list[dict], list[str]]:
    """The adjudicated contrast items, unchanged, and what is wrong with them against their contrast files (an empty
    list means every item matches its file and passes items.validate)."""
    out, problems = [], []
    checker = checker or QuoteChecker()
    for item in items:
        item = copy.deepcopy(dict(item))
        item_id = item.get("id")
        negative = item.get("negative") or {}
        contrast = contrasts.get(item_id)
        if negative.get("kind") != "contrast":
            problems.append(f"{item_id}: not a contrast item (negative.kind is {negative.get('kind')!r})")
            continue
        if contrast is None:
            problems.append(f"{item_id}: no contrast file for it")
            continue
        expected = {"kind": "contrast", "from_item": contrast.get("pivot_id"),
                    "edited_element": contrast.get("edited_element")}
        if dict(negative) != expected:
            problems.append(f"{item_id}: negative is {dict(negative)}, the contrast file says {expected}")
        if item.get("status") not in ("adjudicated", "excluded"):
            problems.append(f"{item_id}: status is {item.get('status')!r}; finish adjudicated items only")
        claim = item.get("claim") or {}
        if claim.get("text") != contrast.get("claim_text") or claim.get("skeleton") != contrast.get("skeleton"):
            problems.append(f"{item_id}: the claim text or skeleton differs from the contrast file: readers do not "
                            "edit a contrast claim (exclude the item at adjudication)")
        chart_texts = [(e.get("id"), e.get("text")) for e in claim.get("chart") or []]
        pivot_chart = (contrast.get("pivot") or {}).get("chart") or []
        written = [(e["id"], e["text"]) for e in edited_chart(contrast, pivot_chart)]
        if chart_texts != written:
            problems.append(f"{item_id}: the chart differs from the contrast file's (elements removed, added or "
                            "edited by a reader)")
        readers = [a.get("id") for a in (item.get("annotation") or {}).get("annotators") or []]
        for role in ("writer", "checker"):
            if contrast.get(role) not in readers:
                problems.append(f"{item_id}: the {role} {contrast.get(role)!r} has no reading among {readers}")
        if claim.get("written_by") != contrast.get("writer"):
            problems.append(f"{item_id}: claim.written_by is {claim.get('written_by')!r}, the contrast file's writer "
                            f"is {contrast.get('writer')!r} (merge records the writer named in prefill.by)")
        if claim.get("checked_by") != contrast.get("checker"):
            problems.append(f"{item_id}: claim.checked_by is {claim.get('checked_by')!r}, the contrast file's checker "
                            f"is {contrast.get('checker')!r} (name the checker in claim_checked_by at adjudication)")
        problems += [f"{item_id}: {p}" for p in validate(item, checker, guide, check_quotes)]
        out.append(item)
    return sorted(out, key=lambda i: str(i.get("id"))), problems


# Command line.

def _read(path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_contrasts(paths: Sequence[str]) -> list[tuple[str, object, str | None]]:
    """(path, data, None) for each readable file, (path, None, problem) for the others."""
    out = []
    for path in paths:
        try:
            out.append((path, _read(path), None))
        except ValueError as err:
            out.append((path, None, f"not valid JSON ({err}); a double quote inside a text is written \\\""))
        except OSError as err:
            out.append((path, None, str(err)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.contrast")
    sub = parser.add_subparsers(dest="command", required=True)
    pv = sub.add_parser("pivots", help="eligible pivots of an items file in a seeded, stratified order")
    pv.add_argument("items")
    pv.add_argument("--seed", type=int, default=SEED)
    pv.add_argument("--out", help="write the order here (never over an existing file); default: print it")
    tp = sub.add_parser("template", help="write the contrast file of one pivot for its writer")
    tp.add_argument("items")
    tp.add_argument("pivot")
    tp.add_argument("--id", required=True, help="the new item's id, above every id in use")
    tp.add_argument("--writer", required=True)
    tp.add_argument("--checker", required=True)
    tp.add_argument("--out", help=f"default: {CONTRAST_DIR.relative_to(REPO_ROOT)}/<id>.json")
    ck = sub.add_parser("check", help="check filled contrast files against their pivots")
    ck.add_argument("items")
    ck.add_argument("contrasts", nargs="+")
    pk = sub.add_parser("packet", help="check the contrast files, then write the contrast batch and its copies")
    pk.add_argument("items")
    pk.add_argument("batch", help="the contrast batch's name, such as c01")
    pk.add_argument("contrasts", nargs="+")
    pk.add_argument("--out-dir", help=f"default: {BATCHES_DIR.relative_to(REPO_ROOT)}")
    ca = sub.add_parser("cache", help="write the cached claim texts (ctr-<id>) again from contrast files")
    ca.add_argument("contrasts", nargs="+")
    fn = sub.add_parser("finish", help="check adjudicated contrast items against their contrast files, validate them")
    fn.add_argument("adjudicated")
    fn.add_argument("out")
    fn.add_argument("--contrast-dir", help=f"default: {CONTRAST_DIR.relative_to(REPO_ROOT)}")
    fn.add_argument("--offline", action="store_true", help="check no quote verbatim (no cached texts)")
    args = parser.parse_args(argv)

    if args.command == "cache":
        loaded = _load_contrasts(args.contrasts)
        unreadable = [f"{path}: {problem}" for path, _, problem in loaded if problem]
        try:
            if unreadable:
                raise ValueError("; ".join(unreadable))
            paths = write_texts([data for _, data, _ in loaded if isinstance(data, Mapping)])
        except ValueError as err:
            print(f"nothing written: {err}")
            return 1
        print(f"wrote {len(paths)} cached claim texts: {', '.join(p.name for p in paths)}")
        return 0
    if args.command == "finish":
        contrast_dir = Path(args.contrast_dir) if args.contrast_dir else CONTRAST_DIR
        contrasts = {}
        for path in sorted(contrast_dir.glob("cf-*.json")):
            data = _read(path)
            contrasts[data.get("id")] = data
        labeling = REPO_ROOT / "eval" / "benchmark" / "LABELING.md"
        guide = guide_dois(labeling.read_text(encoding="utf-8")) if labeling.exists() else frozenset()
        items, problems = finish(load_items(args.adjudicated), contrasts, guide=guide, check_quotes=not args.offline)
        for problem in problems:
            print(problem)
        if problems:
            print(f"nothing written: {len(problems)} problems")
            return 1
        Path(args.out).write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
        print(f"wrote {len(items)} contrast items to {args.out}; each passes items.validate"
              + (" (quotes not checked: --offline)" if args.offline else ""))
        return 0

    items = load_items(args.items)
    by_id = {i.get("id"): i for i in items}
    if args.command == "pivots":
        order = pivot_order(items, args.seed)
        order = {"format": order.pop("format"), "items": str(args.items), "items_sha256": _sha256(Path(args.items)),
                 **order}
        text = json.dumps(order, indent=1, ensure_ascii=False) + "\n"
        if args.out:
            out = Path(args.out)
            if out.exists():
                print(f"nothing written: {out} exists (a draw is made once)")
                return 1
            _dump(out, order)
            print(f"wrote {len(order['order'])} pivots in seeded order to {out}")
        else:
            print(text, end="")
        return 0
    if args.command == "template":
        pivot = by_id.get(args.pivot)
        problems = [f"pivot {args.pivot!r} is not in {args.items}"] if pivot is None else []
        problems += id_problems(args.id, used_ids(items))
        out = Path(args.out) if args.out else CONTRAST_DIR / f"{args.id}.json"
        if out.exists():
            problems.append(f"{out} exists")
        if not problems:
            try:
                data = template(pivot, args.id, args.writer, args.checker)
            except ValueError as err:
                problems.append(str(err))
        if problems:
            for problem in problems:
                print(problem)
            print("nothing written")
            return 1
        _dump(out, data)
        print(f"wrote {out}: contrast item {args.id} from {args.pivot}, written by {args.writer}, checked blind by "
              f"{args.checker}")
        return 0
    loaded = _load_contrasts(args.contrasts)
    if args.command == "check":
        report = {}
        for path, data, problem in loaded:
            if problem:
                report[path] = {"ok": False, "problems": [problem], "notes": []}
                continue
            pivot = by_id.get(data.get("pivot_id")) if isinstance(data, Mapping) else None
            found, notes = check_contrast(data, pivot)
            report[path] = {"ok": not found, "problems": found, "notes": notes}
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0 if all(r["ok"] for r in report.values()) else 1
    unreadable = [f"{path}: {problem}" for path, _, problem in loaded if problem]
    try:
        if unreadable:
            raise ValueError("; ".join(unreadable))
        report = write_packets([data for _, data, _ in loaded], items, args.batch,
                               Path(args.out_dir) if args.out_dir else BATCHES_DIR)
    except ValueError as err:
        print(f"nothing written: {err}")
        return 1
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 1 if any(c["problems"] for c in report["copies"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
