"""The candidate queue toward benchmark v0's about 80 items (docs/v0-composition.md, decided 2026-10-06). One row per candidate item, from every source, with its role, its status
and its batch; then counts per source and what is still missing.

Sources and roles:
- declarations (OpenAlex phrase hits triaged by Haiku 4.5, hand-checked in two blind readings, the lower call kept):
  a hand-checked `cross_field_specific` record is a ready positive; unchecked Haiku positives need screening
  (expected yield: the round's adjudicated precision; on 6 Oct every remaining title was screened the same way,
  screening.jsonl); `cross_field_vague` records need the works identified;
  `same_field` records are possible same-field controls (PLAN.md source 7). One row per distinct title.
- Kulikowski's twins: claims that name their method only partly or not at all are positives; claims that name it are
  attributed controls (subset C, 8 in v0, drawn by `draw.py`); claims with no mark need a check.
- the Gradient Coupling catalogue: independent rediscoveries (`R`, `R/B`) are positives after a check; bridges are
  controls after a check; the failed unification (`B(neg)`) is a candidate negative.
- post-cutoff leads from the 60-day sweep (research/novelty/threats.md section 7.7), the non-computational controls,
  and the AI-origin methods (verdict unknown until labelled; post-cutoff by construction).
- negatives: none exist yet; the queue counts what must be made (eval/protocol.md decision 14).
Batch membership comes from eval/benchmark/v0/batches/*-meta.json (each item's `seed`).

    PYTHONPATH=src python -m coelacanth.mining.queue
writes data/candidates/queue.jsonl and data/candidates/queue.md.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from ..env import REPO_ROOT
from ..llm import read_jsonl
from . import CANDIDATES_DIR, write_jsonl
from .triage import OUT_DIR as TRIAGE_DIR
from .triage import _title_key, hand_check_path, latest_labels

MANIFEST = (REPO_ROOT / "data" / "raw" / "kulikowski-rdr" / "unzipped" / "same-problem-different-field-1.0.0"
            / "data" / "manifest.jsonl")
BATCHES_DIR = REPO_ROOT / "eval" / "benchmark" / "v0" / "batches"
SCREENING = TRIAGE_DIR / "screening.jsonl"  # every title the samples left unchecked, read twice (2026-10-06)
PILOT_ITEMS = REPO_ROOT / "eval" / "results" / "closed-book-pilot" / "items.jsonl"
TARGET = {"rediscoveries": 42, "attributed_controls": 8, "ai_origin": 10, "negatives": 20, "H": 25}  # docs/v0-composition.md
POST_CUTOFF_LEADS = [  # research/novelty/threats.md section 7.7; the Critical Line Algorithm lead is pilot item p20
    ("arXiv:2608.27985", "Monte Carlo Tree Search as every-visit Monte Carlo control"),
    ("arXiv:2608.29806", "nuclear resonances and black-hole quasinormal modes, a genealogy"),
    ("arXiv:2608.14520", "no-flow curves presented as new vs the 2000 integral curve of Douglas, Pereira and Yeh"),
    ("arXiv:2608.08445", "retrieval-augmented generation as a rediscovery of IR and QA ideas"),
    ("doi:10.32942/x2596j", "Taylor's law and Smith's law (EcoEvoRxiv, title only)"),
]
CONTROLS = [
    ("control:rankl", "positive", "RANKL = TRANCE = OPGL = ODF, one ligand under four names (immunology and bone)"),
    ("control:ilc2", "positive", "ILC2: nuocytes, natural helper cells and Ih2 cells (same-field control)"),
    ("control:microglia", "unresolved", "microglia states DAM, MGnD and ARM (unresolved control, PLAN.md source 5)"),
]
NEEDS = ("needs_check", "needs_screening", "needs_identification")
# Seeds whose packet drafting failed (eval/benchmark/v0/batches/README.md): excluded from the queue with the reason.
EXCLUDED = {
    "W2123556659": "no specific, resolvable pair of works (b01, cf-0002)",
    "W2117184661": "no specific, resolvable pair of works (b01, cf-0008)",
    "gci#1": "candidate dated by year only; the shared quantity is in no readable text (b01, cf-0021)",
    "W1971235904": "the declaration names no works and has no reference list (b01, cf-0029)",
    "W7220347187": "the 1895 work has no bibliographic identifier (b01, cf-0032)",
    "W4241384844": "no cacheable text; the source cites the candidate's author (b01, cf-0033)",
}


def batch_seeds() -> dict[str, tuple[str, str]]:
    """seed -> (batch, item id), from every assembled batch's meta file."""
    out = {}
    for path in sorted(BATCHES_DIR.glob("*-meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        for item_id, m in (meta.get("items") or {}).items():
            if m.get("seed"):
                out[str(m["seed"])] = (meta["batch"], item_id)
    return out


def _placed(row: dict, batch: tuple[str, str] | None, status: str) -> dict:
    excluded = EXCLUDED.get(row["ref"])
    row.update({"status": "in_batch" if batch else "excluded" if excluded else status,
                "batch": batch[0] if batch else None, "item": batch[1] if batch else None})
    if excluded and not batch:
        row["excluded_because"] = excluded
    return row


def declaration_rows(seeds: dict) -> list[dict]:
    rows = {r["openalex"]: r for r in read_jsonl(CANDIDATES_DIR / "openalex-declarations.jsonl")}
    labels = latest_labels()
    checks = {c["openalex"]: c for round_ in (1, 2) if hand_check_path(round_).exists()
              for c in read_jsonl(hand_check_path(round_))}
    checks.update({c["openalex"]: c for c in read_jsonl(SCREENING)})
    positives = sorted((oid for oid, lab in labels.items() if lab["label"] == "cross_field" and oid in rows),
                       key=lambda oid: (oid not in checks, rows[oid].get("round") or 1, oid))
    out, seen = [], set()
    for oid in positives:
        r = rows[oid]
        key = _title_key(r["title"])
        if key in seen:
            continue
        seen.add(key)
        check = (checks.get(oid) or {}).get("check")
        role, status = {"cross_field_specific": ("positive", "ready"),
                        "cross_field_vague": ("positive", "needs_identification"),
                        "same_field": ("same_field_control", "optional"),
                        "no": ("rejected", "rejected"),
                        None: ("positive", "needs_screening")}[check]
        out.append(_placed({"source": "declaration", "ref": oid, "round": r.get("round") or 1, "role": role,
                            "hand_check": check, "title": r["title"]}, seeds.get(oid), status))
    return out


def kulikowski_rows(seeds: dict) -> list[dict]:
    manifest = {r["id"]: r for r in read_jsonl(MANIFEST)}
    pilot = {i["ref"]: i["pid"] for i in read_jsonl(PILOT_ITEMS) if i["source"] == "kulikowski"}
    claims = defaultdict(list)
    for p in read_jsonl(CANDIDATES_DIR / "kulikowski-pairs.jsonl"):
        if p["order"] == "known":
            claims[p["later"]].append(p["earlier"])
    out = []
    for kid in sorted(claims):
        named = manifest[kid]["method_named"]
        role = "attributed_control" if named == "yes" else "positive" if named in ("no", "partial") else "unchecked"
        seed = pilot.get(kid)
        out.append(_placed({"source": "kulikowski", "ref": kid, "pilot": seed, "role": role, "method_named": named,
                            "family": manifest[kid]["family"], "field": manifest[kid]["field"],
                            "earlier_twins": len(claims[kid])},
                           seeds.get(seed) or seeds.get(kid), "needs_check" if role == "unchecked" else "ready"))
    return out


def catalogue_rows(seeds: dict) -> list[dict]:
    pilot = {i["ref"]: i["pid"] for i in read_jsonl(PILOT_ITEMS) if i["source"] == "gci"}
    out = []
    for g in read_jsonl(CANDIDATES_DIR / "gci-catalogue.jsonl"):
        ref = f"gci#{g['number']}"
        seed = pilot.get(ref)
        kind = g["kind"]
        role = "positive" if kind in ("R", "R/B") else "negative_candidate" if kind == "B(neg)" else "bridge_control"
        out.append(_placed({"source": "catalogue", "ref": ref, "pilot": seed, "role": role, "kind": kind,
                            "title": re.sub(r"[*`]", "", g["instance"])[:120]},
                           seeds.get(seed) if seed else None, "needs_check"))
    return out


def other_rows(seeds: dict) -> list[dict]:
    out = [_placed({"source": "post_cutoff", "ref": "arXiv:2609.25704", "pilot": "p20", "role": "positive",
                    "title": "lasso homotopy and the Critical Line Algorithm"}, seeds.get("p20"), "ready")]
    for ref, title in POST_CUTOFF_LEADS:
        out.append(_placed({"source": "post_cutoff", "ref": ref, "role": "positive", "title": title},
                           seeds.get(ref), "needs_check"))
    for ref, role, title in CONTROLS:
        out.append(_placed({"source": "control", "ref": ref, "role": role, "title": title}, seeds.get(ref),
                           "needs_check" if role == "unresolved" else "ready"))
    for m in read_jsonl(CANDIDATES_DIR / "ai-origin" / "methods.jsonl"):
        out.append(_placed({"source": "ai_origin", "ref": m["id"], "role": "unknown_verdict", "title": m["name"],
                            "model": m["model"], "field": m["field"]}, seeds.get(m["id"]), "not_drawn"))
    return out


def build() -> list[dict]:
    seeds = batch_seeds()
    rows = declaration_rows(seeds) + kulikowski_rows(seeds) + catalogue_rows(seeds) + other_rows(seeds)
    for n, row in enumerate(rows, 1):
        row["qid"] = f"q{n:04d}"
    return rows


def counts(rows: list[dict]) -> dict:
    hc_path = TRIAGE_DIR / "hand-check-summary.json"
    hc = json.loads(hc_path.read_text(encoding="utf-8")) if hc_path.exists() else {}
    in_batch = Counter(r["batch"] for r in rows if r["batch"])
    expected = {}
    for round_ in (1, 2):
        prec = ((hc.get(f"round_{round_}") or {}).get("usable_cross_field") or {}).get("precision")
        unscreened = sum(1 for r in rows if r["source"] == "declaration" and r["round"] == round_
                         and r["status"] == "needs_screening")
        expected[round_] = {"unscreened_titles": unscreened, "precision": prec,
                            "expected_usable": round(unscreened * prec, 1) if prec is not None else None}
    redisc_in = sum(1 for r in rows if r["batch"] and r["role"] in ("positive", "same_field_control"))
    controls_in = sum(1 for r in rows if r["batch"] and r["role"] == "attributed_control")
    pos_ready = sum(1 for r in rows if r["status"] == "ready" and r["role"] == "positive")
    att_ready = sum(1 for r in rows if r["status"] == "ready" and r["role"] == "attributed_control")
    to_draw = max(0, min(att_ready, TARGET["attributed_controls"] - controls_in))
    have = redisc_in + pos_ready
    ai_drawn = sum(1 for r in rows if r["batch"] and r["source"] == "ai_origin")
    return {"in_batches": dict(in_batch), "rediscoveries_in_batches": redisc_in, "controls_in_batches": controls_in,
            "positives_ready": pos_ready, "attributed_controls_ready": att_ready,
            "attributed_controls_to_draw": to_draw, "rediscoveries_in_hand": have,
            "rediscoveries_short": max(0, TARGET["rediscoveries"] - have), "declarations_expected": expected,
            "usable_declarations_projected": (hc.get("stratified") or {}).get("usable_cross_field"),
            "ai_origin_drawn": ai_drawn, "negatives_existing": 0, "negatives_short": TARGET["negatives"]}


def markdown(rows: list[dict]) -> str:
    c = counts(rows)

    def n(source=None, role=None, statuses=None) -> int:
        return sum(1 for r in rows if (source is None or r["source"] == source) and
                   (role is None or r["role"] == role) and (statuses is None or r["status"] in statuses))

    lines = ["# Candidate queue toward benchmark v0", "",
             "Generated by `python -m coelacanth.mining.queue`; `queue.jsonl` has one row per candidate item (one per "
             "distinct title for declarations). Target (docs/v0-composition.md, 6 Oct): about 80 labelled items, about "
             f"{TARGET['rediscoveries']} rediscovery positives, {TARGET['attributed_controls']} attributed Kulikowski "
             f"controls (subset C), {TARGET['ai_origin']} AI-origin items (stratum A) and about {TARGET['negatives']} "
             f"negatives; `H` target {TARGET['H']} rediscovery positives. Rows are "
             "candidates, not items: an item exists once annotators label it, and a pre-filled positive can still "
             "be labelled `nothing_close` or found to be same-field.", "",
             "## Counts per source", "",
             "| Source | Role | In a batch | Ready | To check, screen or identify | Other |",
             "|---|---|---|---|---|---|"]
    for source, role in sorted({(r["source"], r["role"]) for r in rows}):
        inb, ready, needs = n(source, role, ("in_batch",)), n(source, role, ("ready",)), n(source, role, NEEDS)
        other = n(source, role) - inb - ready - needs
        lines.append(f"| {source} | {role} | {inb or ''} | {ready or ''} | {needs or ''} | {other or ''} |")
    e1, e2 = c["declarations_expected"][1], c["declarations_expected"][2]
    proj = c["usable_declarations_projected"] or {}
    unscreened = e1["unscreened_titles"] + e2["unscreened_titles"]
    screen = (f"screening the {unscreened} unchecked declaration titles (about "
              f"{round((e1['expected_usable'] or 0) + (e2['expected_usable'] or 0), 1)} usable); " if unscreened else "")
    lines += ["", "## Toward v0", "",
              f"- In batches: {c['rediscoveries_in_batches']} rediscovery positives and {c['controls_in_batches']} attributed "
              f"Kulikowski controls ({', '.join(f'{k}: {v}' for k, v in sorted(c['in_batches'].items())) or 'none'}).",
              f"- Ready for the next batches: {c['positives_ready']} positives; {c['attributed_controls_ready']} "
              f"attributed Kulikowski controls, of which {c['attributed_controls_to_draw']} more are drawn with a seed (at "
              f"most 2 per family).",
              f"- In hand: {c['rediscoveries_in_hand']} of {TARGET['rediscoveries']} rediscovery positives, so "
              f"{c['rediscoveries_short']} short before any screening.",
              (f"- Declarations not yet hand-checked: {e1['unscreened_titles']} distinct titles from round 1 and "
               f"{e2['unscreened_titles']} from round 2. At the adjudicated precision (round 1 {e1['precision']}, "
               f"round 2 {e2['precision']}) screening them would add about {e1['expected_usable']} and "
               f"{e2['expected_usable']} usable items." if e1['unscreened_titles'] + e2['unscreened_titles'] else
               f"- Declarations: every distinct positive title is hand-checked by two blind readings (samples, then "
               f"the screening of the rest, `screening.jsonl`): {n('declaration', 'positive', ('ready', 'in_batch'))} "
               f"usable, in a batch or ready."),
              f"- Usable declarations projected over all Haiku positives: {proj.get('projected_items')} (95% "
              f"interval {proj.get('projected_ci95')}), two blind readings with the lower call kept "
              f"(`data/candidates/declarations-triage/hand-check-summary.json`).",
              f"- Declarations with a vague side (works still to identify): "
              f"{n('declaration', 'positive', ('needs_identification',))}; same-field declarations, possible "
              f"same-field controls: {n('declaration', 'same_field_control')}.",
              f"- AI-origin methods: {n('ai_origin')} generated, {n('ai_origin', None, ('in_batch',))} drawn "
              f"(target {TARGET['ai_origin']}, a seeded draw stratified by field and generator, eval/protocol.md decision 15); "
              f"verdicts unknown until labelled.",
              f"- Negatives: {c['negatives_existing']} exist. About 10 natural near-negatives come from Kulikowski's 17 "
              f"topical distractors and the catalogue's failed unification; about 10 contrast items are made from "
              f"adjudicated positives (eval/protocol.md decision 14).", "",
              "## What is missing", "",
              f"1. **{c['rediscoveries_short']} rediscovery positives** beyond what is in hand. Sources in order of expected yield per "
              f"hour: {screen}identifying the works behind the vague declarations; the 4 unmarked Kulikowski claims; the catalogue "
              f"rediscoveries not yet checked; the 5 post-cutoff leads; Semantic Scholar snippet mining once the key "
              f"arrives.",
              f"2. **{c['negatives_short']} negatives**: none exist. Contrast items wait for adjudicated positives; "
              f"the natural near-negatives can be pre-filled now.",
              "3. **Post-cutoff positives** for `H-post`: only the AI-origin items and the post-cutoff leads; "
              "most mined claims are old even when their exposure is recent.",
              "4. **Annotators**: the queue assumes two paid annotators label from 8 Oct; if fewer than two are "
              "labelling on 9 Oct, one annotator plus Damir's blind second reading (eval/protocol.md decision 19)."]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = build()
    write_jsonl(CANDIDATES_DIR / "queue.jsonl", rows)
    (CANDIDATES_DIR / "queue.md").write_text(markdown(rows), encoding="utf-8")
    print(json.dumps(counts(rows), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
