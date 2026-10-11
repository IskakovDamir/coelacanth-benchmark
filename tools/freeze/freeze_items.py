"""Items and splits freeze tool of measurement A (docs/design/harness.md sections 8.3 and 12; eval/protocol.md
decision 3 and sections 1, 2 and 9).

    PYTHONPATH=src python tools/freeze/freeze_items.py [--out DIR]            # dry run into a scratch directory
    PYTHONPATH=src python tools/freeze/freeze_items.py --write               # the freeze; refused while provisional

It builds the benchmark items file in SCHEMA.md format from the real data of batches b01 and b02 (b03 is not part of
this freeze) and derives the subsets of protocol section 1 with a seeded generator. The harness only reads what this
tool writes (`coelacanth.harness.run`: SPLITS, ITEMS, `_view_problems`, `preflight`).

Inputs, all read-only, no network, no model call:
    eval/benchmark/v0/batches/b0{1,2}.json and -meta.json   packets (claim, candidates) and hidden meta
    eval/benchmark/v0/quiz/labels.json                       final relation and cross-field answer per pair
    eval/benchmark/v0/checks/claim-texts.json                checked claim texts (the text rule of
                                                             eval/results/closed-book-v0/build_items.py `apply_texts`
                                                             and tools/checks/make_named.py: the checked text if the
                                                             item has one, else the packet's draft)
    eval/benchmark/v0/checks/attribution.json                attribution answers and R
    eval/results/closed-book-v0/                             answers.jsonl, borderline decisions, via that
                                                             directory's own analyse.py (`hit_table`, `answers`), so
                                                             H follows exactly the logic that produced results.json
    eval/benchmark/v0/checks/named-final.json               Damir's adjudication of the works the models named
                                                             closed-book (`tools/checks/import_named_decisions.py`)
    eval/results/measurement-a/closed-pool/kulikowski-fp/unigram/rankings.jsonl   the 92 K queries

Outputs:
    items.jsonl, one object per line, sorted by id
    splits-v1.json with `items_path`, `items_sha256`, `seed`, `views_sha256`, `refpipe_readiness`, `models_for_H`
    and `subsets`, plus `R_recalled`; freeze-report.json (counts, closed-book completeness, SCHEMA validation, the
    readings chosen below); SHA256SUMS (--write only).

PROVISIONAL. While anything the protocol needs is missing (named-final.json absent, closed-book probes incomplete or
borderline matches undecided for an R item, attribution not final, a record inconsistent with another), the output
carries `"provisional": true` and `"missing": [...]`, is named splits-v1.provisional.json, and is written only to
--out. Writing to eval/results/splits/ needs --write and no provisional flag; the tool also writes SHA256SUMS then,
and refuses to replace an existing splits-v1.json (the freeze happens once).

Readings chosen where the protocol or the data is ambiguous (each is also listed in freeze-report.json):
  1. ALL is every item with status `adjudicated` (protocol section 1: "every adjudicated item"). Under decision 4 as
     updated on 2026-10-10 that means: every judged pair settled by the quiz (two readings, Damir deciding
     disagreements). The 10 AI-origin items have no judged candidates yet and keep status `candidate`, so they are in
     A and in `views_sha256` but not in ALL. An item whose only candidate was excluded at adjudication (cf-0004: only
     chapter titles were available) has no adjudicated pair and is `excluded` (reason `no_full_text`).
  2. R is attribution.json's `R` list (its `R_final` flag must be true); the tool recomputes it from the built items
     (adjudicated, human, not an attributed control, no built-negative record, at least one gold, person-confirmed
     cross-field candidate whose attribution is `not_cited`) and records any difference as missing.
  3. H is the R items that no closed-book sample of the three probed models names as cross-field gold, under either
     prompt, and that no model leaves without a valid sample (decision 6: a declined sample is retried once, and an
     item some model still has no valid sample for leaves H, `R_out_of_H_by_declines`); borderline matches nobody has
     decided are counted as hits (the conservative bound), and any item whose membership differs between the bounds is
     listed.
     When named-final.json exists, items it moves out (a named work judged cross-field gold) leave H; `H_any` also
     loses items with any gold named work. An R item that is neither in H nor out by declines is `R_recalled`.
  4. H_post is H restricted to items dated on or after the latest post-cutoff date among `models_for_H` (protocol
     section 2 table), i.e. post-cutoff for every probed model.
  5. N is the set of items whose adjudicated verdict is `nothing_close` whatever their construction (decision 14,
     SCHEMA.md, docs/v0-composition.md), not counting C and A. `N_built` (the natural distractors among them) is
     written beside it; `--n-definition built` makes N equal to it.
  6. C is the attributed Kulikowski controls (hidden meta role `attributed_control`); A is the AI-origin items.
  7. K is the 92 query papers of Kulikowski's closed pool (protocol section 5: package IDs such as
     `kalman-control-001`, not benchmark items). The benchmark items drawn from the pool (provenance source
     `kulikowski`) are written as `K_items`.
  8. P40 is all of R when R has at most 45 items (decision 2), else 40 items drawn from R stratified by
     `provenance.source` and recall. The nested halves (R_half, H_half, P20) take ceil(n/2) items of their parent set
     with the same stratified seeded draw. `H_half` follows the harness code (budget.NESTED), `R_half` the protocol
     text (section 9: "half of R").
  9. The default seed is 20261019 (the day of the freeze); the protocol says only "seed recorded on 19 Oct".

Items carry what the quiz collected and nothing invented: key flags, confidence, covered/analogous/restricted
elements and per-quote confirmations were not collected, so `claim.chart[].key` stays null, those candidate fields are
empty or null and the evidence is the packet's model-written pre-fill. `items.validate` therefore reports violations
for the labelled items; they are counted in freeze-report.json (`validation`) and listed as `schema_gaps` in each
item's `annotation`. The derived `splits` and `pair_stats` fields of SCHEMA.md are not written; the splits file is the
authority.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import random
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from coelacanth.benchmark.annotation import PREFILL_AUTHOR  # noqa: E402
from coelacanth.benchmark.items import ATTRIBUTIONS, PRECEDENCE, derive_verdict, guide_dois, validate  # noqa: E402
from coelacanth.harness.run import frozen_view_sha256  # noqa: E402

FORMAT = "coelacanth-splits/v1"
REPORT_FORMAT = "coelacanth-freeze-report/v1"
DEFAULT_SEED = 20261019
BATCHES = ("b01", "b02")  # b03 (cf-0068, cf-0069) is labelled apart and is not part of this freeze
ITEMS_REL = "eval/benchmark/v0/items.jsonl"
SPLITS_DIR_REL = "eval/results/splits"
SPLITS_NAME = "splits-v1.json"
PROVISIONAL_NAME = "splits-v1.provisional.json"
REPORT_NAME = "freeze-report.json"
SUMS_NAME = "SHA256SUMS"
CLOSED_BOOK_REL = "eval/results/closed-book-v0"
CLOSED_POOL_REL = "eval/results/measurement-a/closed-pool/kulikowski-fp/unigram/rankings.jsonl"
EXPECTED_SAMPLES = {"frozen": 5, "cross_field": 3}  # protocol decision 8: samples per item and model, per prompt
P40_ALL_UP_TO = 45  # decision 2: all of R if R has 45 items or fewer
P40_SIZE = 40
# protocol section 2: claim.date on or after this makes an item post-cutoff for the model
POST_CUTOFF = {"claude-opus-5-5": "2026-08-01", "claude-sonnet-5-5": "2026-08-01",
               "claude-haiku-4-5-20251001": "2025-09-01"}
SCHEMA_GAPS = ["claim.chart[].key was not collected (null)",
               "candidates[].confidence, covered_elements, analogous_elements and restricted_elements were not "
               "collected in the quiz",
               "candidates[].evidence is the packet's model-written pre-fill, not confirmed by people"]


class FreezeRefused(Exception):
    """A write the protocol does not allow (provisional output into eval/results/splits, a second freeze)."""


@dataclass
class Inputs:
    batches: dict[str, dict]  # batch name -> batch file (packets under "items")
    metas: dict[str, dict]  # batch name -> -meta file
    labels: dict
    texts: dict  # claim-texts.json
    attribution: dict
    named_final: dict | None = None
    closed_book: dict | None = None  # closed_book_state() result; None when the probes cannot be read
    models: Sequence[str] = ()  # the probed models, models_for_H
    k_queries: list[str] | None = None
    guide: frozenset = frozenset()
    hashes: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- items


def pick_claim_text(packet: Mapping, texts: Mapping | None) -> tuple[str, str | None, str | None, bool]:
    """The claim text of an item: the checked text of claim-texts.json when the item has one, else the packet's
    draft (as build_items.apply_texts and make_named.py do). Returns text, checked_by, decided_by, changed."""
    entry = (texts or {}).get(packet["id"])
    if entry and entry.get("text"):
        return entry["text"], entry.get("checked_by"), entry.get("decided_by"), bool(entry.get("changed"))
    return packet["claim"]["text"], None, None, False


def _who(decided: str | None) -> str | None:
    """'agreed: a1,a2' -> 'a1+a2'; 'adjudicated: damir' -> 'damir'."""
    if not decided or ":" not in decided:
        return decided
    return decided.split(":", 1)[1].strip().replace(",", "+").replace(" ", "")


def _readers(decided_by: Mapping) -> list[str]:
    ids = set()
    for value in decided_by.values():
        if isinstance(value, str) and value.startswith("agreed:"):
            ids.update(x.strip() for x in value.split(":", 1)[1].split(",") if x.strip())
    return sorted(ids)


def _work(work: Mapping) -> dict:
    return {k: copy.deepcopy(v) for k, v in work.items() if k != "method_text"}


def build_item(packet: Mapping, meta: Mapping, batch: str, pairs: Mapping[str, Mapping], texts: Mapping | None,
               attribution: Mapping) -> tuple[dict, list[str]]:
    """One SCHEMA.md item from a packet, its hidden meta and the quiz's final pair labels. `pairs` maps cid to the
    labels.json pair. Returns the item and the problems found while building it."""
    pid, problems = packet["id"], []
    claim = packet["claim"]
    text, checked_by, decided_by, changed = pick_claim_text(packet, texts)
    origin = copy.deepcopy(meta.get("origin"))
    ai = (origin or {}).get("kind") == "ai"
    att_pairs = attribution.get("pairs") or {}
    candidates, excluded_pairs, basis, decisions = [], {}, {}, {}
    for pc in packet.get("candidates") or []:
        cid = pc["cid"]
        pair = pairs.get(cid)
        if pair is None:
            problems.append(f"{pid}/{cid}: no pair in labels.json")
            continue
        decisions[cid] = pair.get("decided_by")
        if pair.get("excluded"):
            excluded_pairs[cid] = (pair.get("adjudication_reason") or {}).get("relation") or "excluded"
            continue
        key = f"{pid}/{cid}"
        if key in att_pairs:
            value, why = att_pairs[key].get("value"), f"checks/attribution.json ({att_pairs[key].get('decided_by')})"
        else:
            value, why = pc.get("attribution_hint"), "packet hint (model pre-fill, not confirmed by people)"
        if value not in ATTRIBUTIONS:
            problems.append(f"{key}: attribution {value!r} is not one of {ATTRIBUTIONS}")
            value = "unknown"
        basis[cid] = why
        gold = bool(pair["gold"])
        confirmer = _who((pair.get("decided_by") or {}).get("cross_field")) if gold else None
        candidates.append({
            "work": _work(pc["work"]), "found_by": list((meta.get("found_by") or {}).get(cid) or []),
            "relation": pair["relation"], "covered_elements": [], "analogous_elements": [],
            "restricted_elements": [], "evidence": copy.deepcopy(pc.get("evidence") or []),
            "attribution": value, "cross_field": bool(pair["cross_field"]), "field_confirmed_by": confirmer,
            "gold": gold, "confidence": None})
    status, reason = ("candidate", None) if ai else ("adjudicated", None)
    if not ai and not candidates:
        status, reason = "excluded", "no_full_text"
    item: dict[str, Any] = {"id": pid, "version": "v0", "status": status}
    if reason:
        item["exclusion_reason"] = reason
    item["claim"] = {
        "text": text, "skeleton": claim["skeleton"], "coined_names": list(claim.get("coined_names") or []),
        "written_by": PREFILL_AUTHOR, "checked_by": checked_by, "source_work": _work(claim["source_work"]),
        "date": claim["date"], "field": claim["field"], "domain": claim["domain"],
        "computational": claim["computational"], "chart": copy.deepcopy(claim.get("chart") or [])}
    item["candidates"] = candidates
    item["verdict"] = derive_verdict(item) if status == "adjudicated" else None
    provenance = copy.deepcopy(meta.get("provenance") or {})
    provenance["role"] = (meta.get("seed_detail") or {}).get("role")
    quiz = not ai
    item.update({
        "pool": None, "exposure": copy.deepcopy(meta.get("exposure")), "origin": origin,
        "negative": copy.deepcopy(meta.get("negative")), "provenance": provenance,
        "annotation": {
            "annotators": [{"id": a} for a in sorted({a for d in decisions.values() for a in _readers(d or {})})],
            "prefill": copy.deepcopy(packet.get("prefill")), "batch": batch,
            "adjudicated_by": "damir" if quiz and candidates else None, "notes": None,
            "label_basis": "quiz" if quiz else None, "pair_decisions": decisions, "excluded_pairs": excluded_pairs,
            "claim_text": {"source": "checks/claim-texts.json" if checked_by else "packet draft", "changed": changed,
                           "decided_by": decided_by},
            "attribution_basis": basis, "schema_gaps": list(SCHEMA_GAPS) if quiz else []},
        "exclusions": list(meta.get("exclusions") or [])})
    return item, problems


def build_items(inp: Inputs) -> tuple[list[dict], list[str]]:
    """Every packet of the batches as an item, sorted by id, and the problems found."""
    pairs: dict[str, dict[str, Mapping]] = defaultdict(dict)
    for p in inp.labels.get("pairs") or []:
        pairs[p["item"]][p["candidate"]] = p
    items, problems = [], []
    for name, batch in inp.batches.items():
        metas = inp.metas[name]["items"]
        for packet in batch["items"]:
            if packet["id"] not in metas:
                problems.append(f"{packet['id']}: no meta in {name}-meta.json")
                continue
            item, probs = build_item(packet, metas[packet["id"]], batch.get("batch", name), pairs[packet["id"]],
                                     inp.texts, inp.attribution)
            items.append(item)
            problems += probs
    items.sort(key=lambda i: i["id"])
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        problems.append("item ids repeat across batches")
    return items, problems


def items_text(items: Sequence[Mapping]) -> str:
    return "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in sorted(items, key=lambda i: i["id"]))


def validation_summary(items: Sequence[Mapping], guide: frozenset = frozenset()) -> dict:
    """items.validate (without the quote check, which needs the cached texts) over every item, counted by rule."""
    by_rule: dict[str, dict] = {}
    with_violations = 0
    for item in items:
        probs = validate(item, check_quotes=False, guide=guide)
        with_violations += bool(probs)
        for p in probs:
            rule = p.split(":", 1)[0]
            slot = by_rule.setdefault(rule, {"count": 0, "items": set(), "examples": []})
            slot["count"] += 1
            slot["items"].add(item["id"])
            if len(slot["examples"]) < 3:
                slot["examples"].append(f"{item['id']}: {p}")
    return {"checked": len(items), "items_with_violations": with_violations,
            "by_rule": {r: {"violations": s["count"], "items": len(s["items"]), "examples": s["examples"]}
                        for r, s in sorted(by_rule.items())},
            "note": "quote checks (rule 4) skipped; the quiz did not collect key flags, confidence or element "
                    "coverage, see annotation.schema_gaps"}


# ---------------------------------------------------------------- R, H and the other subsets


def compute_R(items: Sequence[Mapping]) -> list[str]:
    """Rediscovery positives with a gold, person-confirmed cross-field candidate the source does not cite (protocol
    section 1 and checks/attribution.json): adjudicated, human origin, not an attributed control, no built-negative
    record."""
    out = []
    for it in items:
        if it["status"] != "adjudicated" or (it.get("origin") or {}).get("kind") != "human":
            continue
        if (it.get("provenance") or {}).get("role") == "attributed_control" or it.get("negative"):
            continue
        if any(c["gold"] and c["cross_field"] and c["attribution"] == "not_cited" for c in it["candidates"]):
            out.append(it["id"])
    return sorted(out)


def closed_book_state(cb_dir: Path) -> tuple[dict, list[str]]:
    """Per item and probed model, from the directory's own analyse.py (its `hit_table` and `answers`, applied to the
    current answers.jsonl and borderline-decisions.json): the model:prompt pairs whose samples name cross-field gold
    and any gold, under the low bound (undecided borderline matches not counted) and the high bound (all counted),
    the number of valid samples per model and prompt, and the models with no valid sample. Returns it and the models.
    Reads files only."""
    spec = importlib.util.spec_from_file_location("_closed_book_v0_analyse", cb_dir / "analyse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HERE = cb_dir
    dec_path = cb_dir / "borderline-decisions.json"
    decisions = json.loads(dec_path.read_text(encoding="utf-8"))["decisions"] if dec_path.exists() else None
    table = mod.hit_table(decisions)
    valid: dict[tuple, int] = defaultdict(int)
    for a in mod.answers():
        valid[(a["pid"], a["model"], a["prompt"])] += 1
    models, prompts = list(mod.MODELS), tuple(mod.PROMPTS)
    state = {}
    for pid in mod.items():
        row = {"cross_low": set(), "cross_high": set(), "any_low": set(), "any_high": set(),
               "valid": {f"{m}:{p}": valid[(pid, m, p)] for m in models for p in prompts}}
        for (q, m, p), v in table.items():
            if q == pid:
                for gs in ("cross", "any"):
                    for bound in ("low", "high"):
                        if v[gs][bound]:
                            row[f"{gs}_{bound}"].add(f"{m}:{p}")
        row["models_without_valid"] = [m for m in models if not any(valid[(pid, m, p)] for p in prompts)]
        state[pid] = {k: (sorted(v) if isinstance(v, set) else v) for k, v in row.items()}
    return state, models


def closed_book_sets(r_ids: Sequence[str], state: Mapping[str, Mapping], expected: Mapping[str, int] = EXPECTED_SAMPLES
                     ) -> dict:
    """H and H_any over R from `closed_book_state`, and what is not settled. An item is `recalled` when some sample
    (high bound) names cross-field gold. `uncertain` items change membership between the low and the high bound;
    `incomplete` maps an item that could still be in H to the model:prompt cells with fewer valid samples than the
    protocol plans (decision 8); `declines` are items some model has no valid sample for (decision 6), which leave H
    and are not `recalled`; `short_cells` lists the short cells of every R item, for the report."""
    out = {"H": [], "H_any": [], "recalled": [], "declines": [], "uncertain": [], "incomplete": {}, "no_record": [],
           "short_cells": {}}
    for pid in sorted(r_ids):
        s = state.get(pid)
        if s is None:
            out["no_record"].append(pid)
            continue
        rec_low, rec_high = bool(s["cross_low"]), bool(s["cross_high"])
        declined = bool(s["models_without_valid"])
        if declined:
            out["declines"].append(pid)
        in_h = not rec_high and not declined
        if rec_high and not declined:
            out["recalled"].append(pid)
        any_differs = bool(s["any_low"]) != bool(s["any_high"])
        if not declined and (rec_low != rec_high or (in_h and any_differs)):
            out["uncertain"].append(pid)
        out["short_cells"][pid] = sorted(cell for cell, n in s["valid"].items() if n < expected[cell.split(":")[-1]])
        if not rec_low and not declined:  # could be in H once the probes are complete and the matches decided
            short = sorted(cell for cell, n in s["valid"].items() if n < expected[cell.split(":")[-1]])
            if short:
                out["incomplete"][pid] = short
        if in_h:
            out["H"].append(pid)
            if not s["any_high"]:
                out["H_any"].append(pid)
    return out


def apply_named_final(cb: Mapping, named_final: Mapping | None) -> tuple[list[str], list[str], list[str], list[str]]:
    """H and H_any after the works the models named closed-book were judged (decision 1). An item leaves H when a
    named work is cross-field gold (`items_out_of_H`, or `by_item[...].cross_field_gold`), and leaves H_any when any
    named work is gold. Returns H, H_any, the items moved out of H and the problems (a recorded H_before that differs
    from the H recomputed from the current probes)."""
    if named_final is None:
        return list(cb["H"]), list(cb["H_any"]), [], []
    by_item = named_final.get("by_item") or {}
    moved = set(named_final.get("items_out_of_H") or []) | {i for i, s in by_item.items() if s.get("cross_field_gold")}
    moved_any = moved | {i for i, s in by_item.items() if s.get("gold")}
    problems = []
    before = sorted(named_final.get("H_before") or [])
    if before != sorted(cb["H"]):
        problems.append(f"named-final.json H_before {before} differs from H recomputed from the current closed-book "
                        f"answers {sorted(cb['H'])}; rebuild named-final.json after the probes are complete")
    h = [i for i in cb["H"] if i not in moved]
    h_any = [i for i in cb["H_any"] if i not in moved_any]
    return h, h_any, sorted(moved & set(cb["H"])), problems


def half_size(n: int) -> int:
    return (n + 1) // 2


def stratified_sample(ids: Sequence[str], stratum: Mapping[str, Any], k: int, seed: int, label: str) -> list[str]:
    """k ids drawn with proportional allocation over strata (largest remainder), a seeded draw inside each stratum:
    random.Random(f"{seed}:{label}:{stratum}"). The same ids, strata, k, seed and label always give the same draw."""
    ids = sorted(ids)
    k = min(k, len(ids))
    if k <= 0:
        return []
    cells: dict[Any, list[str]] = {}
    for i in ids:
        cells.setdefault(stratum[i], []).append(i)
    keys = sorted(cells, key=str)
    quota = {c: k * len(cells[c]) / len(ids) for c in keys}
    base = {c: int(math.floor(quota[c])) for c in keys}
    for c in sorted(keys, key=lambda c: (-(quota[c] - base[c]), str(c)))[:k - sum(base.values())]:
        base[c] += 1
    chosen: list[str] = []
    for c in keys:
        chosen += random.Random(f"{seed}:{label}:{c}").sample(cells[c], base[c])
    return sorted(chosen)


def derive_subsets(items: Sequence[Mapping], *, r_ids: Sequence[str], h: Sequence[str], h_any: Sequence[str],
                   r_recalled: Sequence[str], k_queries: Sequence[str], models_for_h: Sequence[str], seed: int,
                   n_definition: str = "verdict") -> tuple[dict, list[str]]:
    """The subsets of protocol section 1 over the built items. Returns them and the problems."""
    by_id = {i["id"]: i for i in items}
    problems = []
    adjudicated = [i for i in items if i["status"] == "adjudicated"]
    a = sorted(i["id"] for i in items if (i.get("origin") or {}).get("kind") == "ai" and i["status"] != "excluded")
    c = sorted(i["id"] for i in adjudicated if (i.get("origin") or {}).get("kind") == "human"
               and (i.get("provenance") or {}).get("role") == "attributed_control")
    nothing_close = [i for i in adjudicated if i["verdict"] == "nothing_close" and i["id"] not in a and i["id"] not in c]
    n_verdict = sorted(i["id"] for i in nothing_close)
    n_built = sorted(i["id"] for i in nothing_close if i.get("negative"))
    cutoffs = [POST_CUTOFF.get(m) for m in models_for_h]
    if not all(cutoffs):
        problems.append(f"no post-cutoff date for models {[m for m, d in zip(models_for_h, cutoffs) if not d]}")
    boundary = max(d for d in cutoffs if d) if any(cutoffs) else None
    recalled_set = set(r_recalled)
    stratum = {i: ((by_id[i].get("provenance") or {}).get("source"), i in recalled_set) for i in by_id}
    r_ids = sorted(r_ids)
    p40 = sorted(r_ids) if len(r_ids) <= P40_ALL_UP_TO else stratified_sample(r_ids, stratum, P40_SIZE, seed, "P40")

    def half(parent: Sequence[str]) -> list[str]:
        return stratified_sample(parent, stratum, half_size(len(parent)), seed, "half")

    subsets = {
        "ALL": sorted(i["id"] for i in adjudicated), "R": r_ids, "R_half": half(r_ids),
        "R_recalled": sorted(r_recalled),
        "H": sorted(h), "H_any": sorted(h_any),
        "H_post": sorted(i for i in h if boundary and by_id[i]["claim"]["date"] >= boundary),
        "H_half": half(sorted(h)), "P40": p40, "P20": half(p40),
        "C": c, "A": a, "N": n_built if n_definition == "built" else n_verdict, "N_built": n_built,
        "K": sorted(k_queries),
        "K_items": sorted(i["id"] for i in items if (i.get("provenance") or {}).get("source") == "kulikowski"
                          and i["status"] != "excluded")}
    known = set(by_id)
    for name, members in subsets.items():
        if name != "K" and set(members) - known:
            problems.append(f"subset {name} names items that are not in the item set: {sorted(set(members) - known)}")
    return subsets, problems


# ---------------------------------------------------------------- assembling and writing


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assemble(inp: Inputs, *, seed: int = DEFAULT_SEED, frozen_at: str | None = None,
             refpipe_readiness: str = "not_ready", n_definition: str = "verdict") -> dict:
    """Items, splits and report from loaded inputs; nothing is written. The splits dict carries `provisional` and
    `missing` while anything is missing."""
    missing: list[str] = []
    items, problems = build_items(inp)
    missing += [f"build: {p}" for p in problems]
    text = items_text(items)
    att = inp.attribution
    r_ids = sorted(att.get("R") or [])
    if not att.get("R_final"):
        missing.append("attribution.json: R_final is not true, attribution is still pending")
    if att.get("pending"):
        missing.append(f"attribution.json: {len(att['pending'])} pairs pending")
    computed = compute_R(items)
    if computed != r_ids:
        missing.append(f"R: attribution.json lists {r_ids}, recomputed from the items {computed}")

    cb: dict = {"H": [], "H_any": [], "recalled": [], "declines": [], "uncertain": [], "incomplete": {},
                "no_record": list(r_ids)}
    if inp.closed_book is None:
        missing.append("closed-book results unreadable")
    else:
        cb = closed_book_sets(r_ids, inp.closed_book)
        if cb["no_record"]:
            missing.append(f"closed-book: no record for R items {cb['no_record']}")
        if cb["incomplete"]:
            missing.append("closed-book probes incomplete for R items that could still be in H (cells with fewer "
                           f"valid samples than planned): {cb['incomplete']}")
        if cb["uncertain"]:
            missing.append(f"closed-book: undecided borderline matches change the membership of R items "
                           f"{cb['uncertain']}")
    if inp.named_final is None:
        missing.append("eval/benchmark/v0/checks/named-final.json is absent: the works the models named closed-book "
                       "are not adjudicated, so H can still shrink (decision 1)")
    h, h_any, moved, nf_problems = apply_named_final(cb, inp.named_final)
    missing += [f"named-final: {p}" for p in nf_problems]
    r_recalled = sorted(set(r_ids) - set(h) - set(cb["declines"]))
    if inp.k_queries is None:
        missing.append(f"{CLOSED_POOL_REL} is absent: K cannot be listed")
    models = list(inp.models)
    subsets, sub_problems = derive_subsets(
        items, r_ids=r_ids, h=h, h_any=h_any, r_recalled=r_recalled, k_queries=inp.k_queries or [],
        models_for_h=models, seed=seed, n_definition=n_definition)
    missing += sub_problems
    validation = validation_summary(items, inp.guide)
    splits: dict[str, Any] = {"format": FORMAT}
    if missing:
        splits.update({"provisional": True, "missing": missing})
    splits.update({
        "frozen_at": frozen_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items_path": ITEMS_REL, "items_sha256": sha256_text(text), "seed": seed,
        "refpipe_readiness": refpipe_readiness, "models_for_H": models,
        "views_sha256": {i["id"]: frozen_view_sha256(i) for i in items if i["status"] != "excluded"},
        "subsets": subsets, "R_recalled": r_recalled, "R_out_of_H_by_declines": sorted(cb["declines"]),
        "inputs": dict(sorted(inp.hashes.items()))})
    by_status: dict[str, int] = defaultdict(int)
    for i in items:
        by_status[i["status"]] += 1
    report = {
        "format": REPORT_FORMAT, "provisional": bool(missing), "missing": missing, "seed": seed,
        "counts": {k: len(v) for k, v in subsets.items()}, "items": {"total": len(items), "by_status": dict(by_status)},
        "excluded": {i["id"]: i.get("exclusion_reason") for i in items if i["status"] == "excluded"},
        "closed_book": {"H_before_named_works": cb["H"], "recalled": cb["recalled"], "declines": cb["declines"],
                        "uncertain": cb["uncertain"], "incomplete": cb["incomplete"],
                        "short_cells_all_R": {k: v for k, v in cb.get("short_cells", {}).items() if v},
                        "moved_out_of_H_by_named_works": moved,
                        "named_final": None if inp.named_final is None else
                        {"H_before": inp.named_final.get("H_before"), "H_after": inp.named_final.get("H_after")}},
        "claim_texts_unchecked": sorted(i["id"] for i in items if not i["claim"]["checked_by"]),
        "validation": validation, "readings": READINGS}
    return {"items": items, "items_text": text, "splits": splits, "report": report}


READINGS = [
    "ALL is every item with status adjudicated; the 10 AI-origin items (status candidate, no judged candidates) are in "
    "A and in views_sha256 but not in ALL; cf-0004 (its only candidate excluded at adjudication) is excluded",
    "R is attribution.json's R list, recomputed from the built items as a check",
    "H uses the high bound for undecided borderline matches; named-final.json moves items out of H and H_any",
    "H_post: dated on or after the latest post-cutoff date among models_for_H",
    "N is every adjudicated nothing_close item outside C and A (decision 14); N_built is the natural distractors "
    "among them",
    "K is the 92 closed-pool query papers (package IDs); K_items are the benchmark items drawn from the pool",
    "P40 is all of R while R has at most 45 items; nested halves take ceil(n/2) items with the stratified seeded draw",
    f"default seed {DEFAULT_SEED}",
]


def guard_output(out_dir: Path, *, provisional: bool, write: bool, repo: Path = REPO) -> None:
    """Raise FreezeRefused unless the write is allowed: provisional output never goes to eval/results/splits/ nor
    with --write, and a final output goes there only with --write."""
    protected = (repo / SPLITS_DIR_REL).resolve()
    out = Path(out_dir).resolve()
    inside = out == protected or protected in out.parents
    if provisional and (write or inside):
        raise FreezeRefused("the output is provisional: it can only be written to a scratch --out directory outside "
                            f"{SPLITS_DIR_REL}/ and never with --write")
    if inside and not write:
        raise FreezeRefused(f"writing into {SPLITS_DIR_REL}/ needs --write")


def write_outputs(result: Mapping, out_dir: Path | None, *, write: bool, repo: Path = REPO) -> list[Path]:
    """Write items, splits and report. Without `write`: into `out_dir` (items.jsonl, splits-v1.json or
    splits-v1.provisional.json, freeze-report.json). With `write` (final output only): items to
    eval/benchmark/v0/items.jsonl, splits, report and SHA256SUMS to eval/results/splits/. Returns the paths."""
    provisional = bool(result["splits"].get("provisional"))
    splits_dir = repo / SPLITS_DIR_REL
    target = splits_dir if write else Path(out_dir)
    guard_output(target, provisional=provisional, write=write, repo=repo)
    items_path = repo / ITEMS_REL if write else target / "items.jsonl"
    splits_path = target / (PROVISIONAL_NAME if provisional else SPLITS_NAME)
    report_path = target / REPORT_NAME
    if write:
        if splits_path.exists():
            raise FreezeRefused(f"{splits_path} exists: the items and splits freeze happens once")
        if items_path.exists() and items_path.read_text(encoding="utf-8") != result["items_text"]:
            raise FreezeRefused(f"{items_path} exists with other content")
    target.mkdir(parents=True, exist_ok=True)
    items_path.parent.mkdir(parents=True, exist_ok=True)
    items_path.write_text(result["items_text"], encoding="utf-8")
    splits_text = json.dumps(result["splits"], indent=1, ensure_ascii=False) + "\n"
    splits_path.write_text(splits_text, encoding="utf-8")
    report_text = json.dumps(result["report"], indent=1, ensure_ascii=False) + "\n"
    report_path.write_text(report_text, encoding="utf-8")
    written = [items_path, splits_path, report_path]
    if write:
        sums = target / SUMS_NAME
        sums.write_text(f"{sha256_text(splits_text)}  {SPLITS_NAME}\n{sha256_text(report_text)}  {REPORT_NAME}\n",
                        encoding="utf-8")
        written.append(sums)
    return written


# ---------------------------------------------------------------- real data


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(repo: Path = REPO, batches: Sequence[str] = BATCHES) -> Inputs:
    v0 = repo / "eval" / "benchmark" / "v0"
    cb_dir = repo / CLOSED_BOOK_REL
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        hashes[str(path.relative_to(repo))] = _file_hash(path)
        return _json(path)

    batch_files = {b: read(v0 / "batches" / f"{b}.json") for b in batches}
    metas = {b: read(v0 / "batches" / f"{b}-meta.json") for b in batches}
    labels = read(v0 / "quiz" / "labels.json")
    texts = read(v0 / "checks" / "claim-texts.json")["items"]
    attribution = read(v0 / "checks" / "attribution.json")
    nf_path = v0 / "checks" / "named-final.json"
    named_final = read(nf_path) if nf_path.exists() else None
    state, models = None, []
    try:
        for name in ("answers.jsonl", "items.jsonl", "borderline-decisions.json", "borderline-keys.json"):
            if (cb_dir / name).exists():
                hashes[str((cb_dir / name).relative_to(repo))] = _file_hash(cb_dir / name)
        state, models = closed_book_state(cb_dir)
    except (OSError, KeyError, ValueError, AttributeError) as err:  # reported as missing by assemble
        print(f"closed-book results unreadable: {type(err).__name__}: {err}", file=sys.stderr)
    from coelacanth.closedbook import MODELS  # the three probed models define H (decision 8)
    models = models or list(MODELS)
    pool = repo / CLOSED_POOL_REL
    k = None
    if pool.exists():
        hashes[CLOSED_POOL_REL] = _file_hash(pool)
        k = sorted(json.loads(line)["query"] for line in pool.read_text(encoding="utf-8").splitlines()
                   if line.strip() and json.loads(line).get("query_role") == "query")
    labeling = repo / "eval" / "benchmark" / "LABELING.md"
    guide = guide_dois(labeling.read_text(encoding="utf-8")) if labeling.exists() else frozenset()
    return Inputs(batches=batch_files, metas=metas, labels=labels, texts=texts, attribution=attribution,
                  named_final=named_final, closed_book=state, models=models, k_queries=k, guide=guide, hashes=hashes)


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "coelacanth-freeze-scratch"),
                        help="scratch directory for the dry run (never eval/results/splits)")
    parser.add_argument("--write", action="store_true",
                        help=f"write items.jsonl and {SPLITS_DIR_REL}/ (refused while the output is provisional)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--frozen-at", help="ISO timestamp recorded in the splits file (default: now, UTC)")
    parser.add_argument("--refpipe-readiness", choices=("ready", "not_ready"), default="not_ready")
    parser.add_argument("--n-definition", choices=("verdict", "built"), default="verdict")
    args = parser.parse_args(argv)
    result = assemble(load_inputs(repo), seed=args.seed, frozen_at=args.frozen_at,
                      refpipe_readiness=args.refpipe_readiness, n_definition=args.n_definition)
    splits = result["splits"]
    counts = result["report"]["counts"]
    print(f"{result['report']['items']['total']} items {result['report']['items']['by_status']}")
    print("subsets: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    if splits.get("provisional"):
        print("PROVISIONAL, missing:")
        for m in splits["missing"]:
            print("  - " + m)
    try:
        for path in write_outputs(result, Path(args.out), write=args.write, repo=repo):
            print(f"wrote {path}")
    except FreezeRefused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
