"""Adjudicate the quiz: list the pairs whose two readings differ, let Damir decide them, then write the final labels.

    PYTHONPATH=src python3 tools/quiz/adjudicate_quiz.py report
    PYTHONPATH=src python3 tools/quiz/adjudicate_quiz.py apply eval/benchmark/v0/quiz/adjudication/decisions.json

Humans choose every label: the tool never decides a disagreement. A pair's relation or cross-field answer stands as
agreed only when both readings give the same value; anything else (two different values, "Не могу решить", a text-only
answer, a missing reading) is a disagreement for the adjudicator.

`report` reads the quiz assignment, every answers file (tools/quiz/import_quiz.py) and the batches named in the
assignment, and writes to eval/benchmark/v0/quiz/adjudication/ (--out):
- decisions.json: the template, one entry per disagreement (key "<item>/<cid>/relation" or "<item>/<cid>/cross_field")
  with both readings (annotator, value, comment), the allowed values (the six relations plus "exclude", or yes and
  no), an empty decision and reason, the SHA-256 of each answers file and of the assignment, and the adjudicator;
- disagreements.html: one self-contained page in Russian (inline CSS and JS, no network requests) on which the
  adjudicator decides each entry and downloads decisions.json with the decisions filled. It never contains the
  proposals' private model_top, model_confidence or reason (the build checks it);
- disagreements.md: counts by kind and by annotator pair, and the list of keys.

`apply` checks a filled decisions.json (the inputs are unchanged, the entries are exactly those the inputs give now,
every entry is decided with an allowed value and a non-empty reason) and writes eval/benchmark/v0/quiz/labels.json
(--labels): the final relation and cross-field answer of every pair and who decided each, the excluded pairs, and per
item its provenance, the packets' attribution hints (a model pre-fill, not confirmed by people), its gold and
cross-field gold candidates and whether it is provisionally in R. Attribution was not asked in the quiz, so R stays
provisional until a person confirms attribution, and H additionally needs the v0 closed-book runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_quiz  # noqa: E402
import make_quiz  # noqa: E402
from coelacanth.benchmark.items import PRECEDENCE, RELATIONS  # noqa: E402

DECISIONS_FORMAT = "coelacanth-quiz-decisions/v1"
LABELS_FORMAT = "coelacanth-quiz-labels/v1"
ADJ_DIR = make_quiz.QUIZ_DIR / "adjudication"
TEMPLATE = Path(__file__).resolve().parent / "adjudicate-template.html"
DATA_RE = re.compile(r'(<script type="application/json" id="adj-data">)(.*?)(</script>)', re.S)
ADJUDICATOR = "damir"
EXCLUDE = "exclude"
KINDS = ("relation", "cross_field")
ALLOWED = {"relation": list(RELATIONS) + [EXCLUDE], "cross_field": ["yes", "no"]}
CANNOT_DECIDE = import_quiz.CANNOT_DECIDE
ATTRIBUTED_CONTROL = "attributed_control"
HINT_STATUS = "model pre-fill hint (candidates[].attribution_hint of the packet), not confirmed by people"
FIXED_ENTRY_FIELDS = ("key", "item", "candidate", "batch", "kind", "readings", "allowed")


class AdjudicationProblems(ValueError):
    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value) -> str:
    return make_quiz.sha256_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8"))


# ---------------------------------------------------------------- inputs

def load_inputs(assignment_path: Path, answers_dir: Path) -> dict:
    """The assignment, the answers by annotator and the input records (file and SHA-256)."""
    assignment_path = Path(assignment_path)
    assignment = make_quiz.read_json(assignment_path)
    answers, answer_inputs = import_quiz.load_answers(Path(answers_dir))
    return {"assignment": assignment, "answers": answers,
            "inputs": {"answers": answer_inputs,
                       "assignment": {"file": make_quiz.display_path(assignment_path),
                                      "sha256": sha256_file(assignment_path)}}}


def batch_paths(assignment: Mapping, override: Sequence[Path] | None = None) -> list[Path]:
    if override:
        return [Path(p) for p in override]
    return [make_quiz.REPO / b["file"] for b in assignment.get("batch_files") or []]


def meta_path(batch: Path) -> Path:
    return Path(batch).with_name(Path(batch).stem + "-meta.json")


def load_batches(paths: Sequence[Path], assignment: Mapping | None = None) -> tuple[dict, dict, list[dict], list[str]]:
    """Packets by item id, meta records by item id, input records and problems (a batch whose SHA-256 differs
    from the one the assignment recorded is a problem: the quiz showed other packets)."""
    packets, metas, records, problems = {}, {}, [], []
    recorded = {Path(b["file"]).name: b["sha256"] for b in (assignment or {}).get("batch_files") or []}
    for path in paths:
        path = Path(path)
        sha = sha256_file(path)
        if path.name in recorded and recorded[path.name] != sha:
            problems.append(f"{path.name}: SHA-256 differs from the one in the assignment (the quiz showed another "
                            f"version of the batch)")
        data = make_quiz.read_json(path)
        records.append({"file": make_quiz.display_path(path), "sha256": sha})
        for p in data.get("items") or []:
            packets[p["id"]] = dict(p, _batch=data.get("batch"))
        mp = meta_path(path)
        if mp.exists():
            records.append({"file": make_quiz.display_path(mp), "sha256": sha256_file(mp)})
            for item_id, m in (make_quiz.read_json(mp).get("items") or {}).items():
                metas[item_id] = m
    return packets, metas, records, problems


def reading_value(answer: Mapping, kind: str):
    if kind == "cross_field":
        return None if answer.get("cross_field") is None else ("yes" if answer["cross_field"] else "no")
    if answer.get("relation") in RELATIONS:
        return answer["relation"]
    return CANNOT_DECIDE if answer.get("cannot_decide") else None


def readings_by_pair(assignment: Mapping, answers: Mapping[str, Mapping]) -> dict[str, list[dict]]:
    """For each question key, the answers given, in the order of the question's annotators."""
    by_coder = {a: {make_quiz.question_key(x["item"], x["candidate"]): x for x in rec["answers"]}
                for a, rec in answers.items()}
    out = {}
    for key, q in assignment["questions"].items():
        out[key] = [dict(by_coder[a][key], annotator=a) for a in q["annotators"] if key in by_coder.get(a, {})]
    return out


def agreed(readings: Sequence[Mapping], kind: str):
    """The agreed value, or None when the pair needs adjudication on this kind."""
    values = [reading_value(r, kind) for r in readings]
    if len(values) < 2 or any(v is None or v == CANNOT_DECIDE for v in values) or len(set(values)) != 1:
        return None
    return values[0]


# ---------------------------------------------------------------- report

def build_template(assignment: Mapping, answers: Mapping[str, Mapping], inputs: Mapping) -> dict:
    entries = []
    for key, readings in readings_by_pair(assignment, answers).items():
        item, cid = key.split("/", 1)
        for kind in KINDS:
            if agreed(readings, kind) is not None:
                continue
            rs = []
            for r in readings:
                row = {"annotator": r["annotator"], "value": reading_value(r, kind), "comment": r.get("comment") or ""}
                if kind == "relation" and r.get("other_text"):
                    row["other_text"] = r["other_text"]
                rs.append(row)
            entries.append({"key": f"{key}/{kind}", "item": item, "candidate": cid,
                            "batch": assignment["questions"][key].get("batch"), "kind": kind, "readings": rs,
                            "allowed": list(ALLOWED[kind]), "decision": "", "reason": ""})
    template = {"format": DECISIONS_FORMAT, "made_by": "tools/quiz/adjudicate_quiz.py report",
                "adjudicator": ADJUDICATOR,
                "note": ("One entry per disagreement between the two quiz readings of a pair. The adjudicator fills "
                         "decision (one of allowed) and a one-line reason; the tool never decides. 'exclude' on a "
                         "relation entry removes the pair from the labels."),
                "inputs": inputs, "entries": entries}
    template["template_hash"] = canonical_hash(template)
    return template


def item_view(packet: Mapping, cids: Sequence[str]) -> dict:
    claim = packet.get("claim") or {}
    return {
        "id": packet["id"], "batch": packet.get("_batch"), "prefill_by": (packet.get("prefill") or {}).get("by"),
        "claim": {"date": claim.get("date"), "field": claim.get("field"), "text": claim.get("text"),
                  "skeleton": claim.get("skeleton"),
                  "chart": [{"id": e.get("id"), "text": e.get("text")} for e in claim.get("chart") or []]},
        "source": make_quiz.work_view(claim.get("source_work")),
        "candidates": [{"cid": c["cid"], "work": make_quiz.work_view(c.get("work")),
                        "evidence": [{k: e.get(k) for k in make_quiz.EVIDENCE_FIELDS} for e in c.get("evidence") or []]}
                       for c in packet.get("candidates") or [] if c["cid"] in cids]}


def page_data(template: Mapping, packets: Mapping[str, Mapping]) -> dict:
    cids: dict[str, list[str]] = {}
    for e in template["entries"]:
        cids.setdefault(e["item"], [])
        if e["candidate"] not in cids[e["item"]]:
            cids[e["item"]].append(e["candidate"])
    missing = [i for i in cids if i not in packets]
    if missing:
        raise AdjudicationProblems([f"{i}: not in the batches" for i in missing])
    return {"decisions": template, "relations": list(RELATIONS),
            "items": [item_view(packets[i], c) for i, c in cids.items()]}


def render_page(template_html: str, data: Mapping, private_texts: Sequence[str] = ()) -> str:
    """The page with the data embedded. Refused when the data holds a private proposal field (model_top,
    model_confidence, or a reason other than the adjudicator's own empty reason field of each entry), when the page
    names model_top or model_confidence, or when any of private_texts (the proposals' reasons) occurs in it."""
    entries = [{k: v for k, v in e.items() if k != "reason"} for e in data["decisions"]["entries"]]
    stripped = dict(data, decisions=dict(data["decisions"], entries=entries))
    private = make_quiz.private_keys(stripped)
    if any(e.get("reason") for e in data["decisions"]["entries"]):
        private.add("reason (filled)")
    if private:
        raise AdjudicationProblems([f"page data holds private proposal fields {sorted(private)}"])
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    if len(DATA_RE.findall(template_html)) != 1:
        raise AdjudicationProblems(["the page template must hold exactly one adj-data script"])
    page = DATA_RE.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), template_html, count=1)
    leaked = [f for f in make_quiz.PRIVATE_FIELDS if f != "reason" and f in page]
    # a reason may appear verbatim or as the payload encodes it (JSON escapes, "<" as <)
    leaked += [f"proposal reason {t[:40]!r}" for t in private_texts
               if t in page or json.dumps(t, ensure_ascii=False)[1:-1].replace("<", "\\u003c") in page]
    if leaked:
        raise AdjudicationProblems([f"the page holds private proposal content: {leaked}"])
    return page


def proposal_reasons(paths: Sequence[Path]) -> list[str]:
    """The private reason sentences of the proposals files (for the leak check only)."""
    by_pair, _, _ = make_quiz.load_proposals(paths) if paths else ({}, [], [])
    return sorted({str(p.get("reason") or "").strip() for p in by_pair.values()} - {""})


def summary_md(template: Mapping, assignment: Mapping) -> str:
    entries = template["entries"]
    kinds = Counter(e["kind"] for e in entries)
    pairs_of = {}
    for e in entries:
        pair = " ".join(sorted(r["annotator"] for r in e["readings"]))
        pairs_of.setdefault(pair, Counter())[e["kind"]] += 1
    shared = Counter(" ".join(sorted(q["annotators"])) for q in assignment["questions"].values())
    lines = ["# Quiz disagreements", "",
             "Written by `tools/quiz/adjudicate_quiz.py report`; decide them on `disagreements.html` (or in "
             "`decisions.json`), then run `apply`. The tool never decides.", "",
             f"Pairs in the quiz: {len(assignment['questions'])}. Disagreements: {len(entries)} "
             f"({kinds['relation']} on the relation, {kinds['cross_field']} on cross-field), on "
             f"{len({(e['item'], e['candidate']) for e in entries})} pairs.", "",
             "| Annotators | Shared pairs | Relation | Cross-field |", "|---|---|---|---|"]
    for pair in sorted(set(shared) | set(pairs_of)):
        c = pairs_of.get(pair, Counter())
        lines.append(f"| {pair} | {shared.get(pair, 0)} | {c['relation']} | {c['cross_field']} |")
    for kind in KINDS:
        lines += ["", f"## {kind.replace('_', '-').capitalize()}", ""]
        for e in entries:
            if e["kind"] == kind:
                vals = ", ".join(f"{r['annotator']} {r['value'] if r['value'] is not None else '(text only)'}"
                                 for r in e["readings"])
                lines.append(f"- `{e['key']}`: {vals}")
    lines += ["", f"Template hash: `{template['template_hash']}`", ""]
    return "\n".join(lines)


def report(assignment_path: Path, answers_dir: Path, out_dir: Path, batches: Sequence[Path] | None = None,
           proposals: Sequence[Path] | None = None, page_template: Path = TEMPLATE) -> dict:
    loaded = load_inputs(assignment_path, answers_dir)
    if not loaded["answers"]:
        raise AdjudicationProblems([f"no answers files in {answers_dir}"])
    template = build_template(loaded["assignment"], loaded["answers"], loaded["inputs"])
    packets, _, _, problems = load_batches(batch_paths(loaded["assignment"], batches), loaded["assignment"])
    if problems:
        raise AdjudicationProblems(problems)
    if proposals is None:
        proposals = sorted(Path(assignment_path).parent.glob(make_quiz.PROPOSALS_GLOB))
    page = render_page(Path(page_template).read_text(encoding="utf-8"), page_data(template, packets),
                       proposal_reasons(proposals))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "decisions.json").write_text(json.dumps(template, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "disagreements.html").write_text(page, encoding="utf-8")
    (out_dir / "disagreements.md").write_text(summary_md(template, loaded["assignment"]), encoding="utf-8")
    return template


# ---------------------------------------------------------------- apply

def check_decisions(decisions: Mapping, assignment: Mapping, answers: Mapping, inputs: Mapping) -> list[str]:
    problems = []
    if decisions.get("format") != DECISIONS_FORMAT:
        problems.append(f"format must be {DECISIONS_FORMAT!r}")
    if decisions.get("adjudicator") != ADJUDICATOR:
        problems.append(f"adjudicator must be {ADJUDICATOR!r}, got {decisions.get('adjudicator')!r}")
    given = decisions.get("inputs") or {}
    if (given.get("assignment") or {}).get("sha256") != inputs["assignment"]["sha256"]:
        problems.append("the assignment changed since the template was written (SHA-256 differs)")
    now = {Path(r["file"]).name: r["sha256"] for r in inputs["answers"]}
    then = {Path(r["file"]).name: r["sha256"] for r in given.get("answers") or []}
    for name in sorted(set(now) | set(then)):
        if now.get(name) != then.get(name):
            problems.append(f"answers file {name} changed, appeared or disappeared since the template was written")
    expected = build_template(assignment, answers, inputs)
    if decisions.get("template_hash") != expected["template_hash"]:
        problems.append("template_hash differs from the template the current inputs give")
    exp = {e["key"]: e for e in expected["entries"]}
    got = {}
    for n, e in enumerate(decisions.get("entries") or []):
        key = e.get("key") if isinstance(e, Mapping) else None
        if key in got:
            problems.append(f"{key}: listed twice")
            continue
        got[key] = e
        if key not in exp:
            problems.append(f"entry {n} ({key}): not a disagreement of the current inputs")
            continue
        changed = [f for f in FIXED_ENTRY_FIELDS if e.get(f) != exp[key][f]]
        if changed:
            problems.append(f"{key}: fields changed from the template: {changed}")
        decision, reason = e.get("decision"), e.get("reason")
        if decision in (None, ""):
            problems.append(f"{key}: not decided")
        elif decision not in exp[key]["allowed"]:
            problems.append(f"{key}: decision {decision!r} is not one of {exp[key]['allowed']}")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{key}: reason is empty")
    for key in exp:
        if key not in got:
            problems.append(f"{key}: missing from the decisions")
    return problems


def build_labels(decisions: Mapping, assignment: Mapping, answers: Mapping, packets: Mapping, metas: Mapping,
                 inputs: Sequence[Mapping]) -> dict:
    decided = {e["key"]: e for e in decisions["entries"]}
    adj = f"adjudicated: {decisions['adjudicator']}"
    pairs, excluded = [], []
    by_item: dict[str, dict[str, dict]] = {}
    for key, readings in readings_by_pair(assignment, answers).items():
        item, cid = key.split("/", 1)
        row = {"item": item, "candidate": cid, "batch": assignment["questions"][key].get("batch")}
        decided_by, reasons = {}, {}
        for kind in KINDS:
            value = agreed(readings, kind)
            if value is not None:
                decided_by[kind] = "agreed: " + ",".join(r["annotator"] for r in readings)
            else:
                entry = decided[f"{key}/{kind}"]
                value = entry["decision"]
                decided_by[kind] = adj
                reasons[kind] = entry["reason"].strip()
            row[kind] = value
        row["cross_field"] = row["cross_field"] == "yes"
        row["excluded"] = row["relation"] == EXCLUDE
        if row["excluded"]:
            row["relation"] = None
            excluded.append(key)
        row["gold"] = row["relation"] in PRECEDENCE
        row["decided_by"] = decided_by
        if reasons:
            row["adjudication_reason"] = reasons
        pairs.append(row)
        by_item.setdefault(item, {})[cid] = row
    items = {}
    for item, cands in by_item.items():
        packet = packets.get(item) or {}
        meta = metas.get(item) or {}
        role = (meta.get("seed_detail") or {}).get("role")
        hints = {c["cid"]: c.get("attribution_hint") for c in packet.get("candidates") or []}
        gold = [c for c, r in cands.items() if r["gold"]]
        cross_gold = [c for c in gold if cands[c]["cross_field"]]
        origin_kind = (meta.get("origin") or {}).get("kind")
        negative = meta.get("negative")
        not_cited = [c for c in cross_gold if hints.get(c) != "cited"]
        why_not = []
        if origin_kind != "human":
            why_not.append(f"origin {origin_kind}")
        if role == ATTRIBUTED_CONTROL:
            why_not.append("attributed control (C)")
        if negative:
            why_not.append(f"built negative ({negative.get('kind')})")
        if not cross_gold:
            why_not.append("no cross-field gold candidate")
        elif not not_cited:
            why_not.append("every cross-field gold candidate has attribution_hint 'cited'")
        items[item] = {
            "batch": packet.get("_batch"),
            "source": (meta.get("provenance") or {}).get("source"),
            "mined_by": (meta.get("provenance") or {}).get("mined_by"),
            "role": role,
            "origin_kind": origin_kind,
            "negative": negative,
            "candidates": {c: {"relation": r["relation"], "cross_field": r["cross_field"], "gold": r["gold"],
                               "excluded": r["excluded"],
                               "attribution_hint": {"value": hints.get(c), "status": HINT_STATUS}}
                           for c, r in cands.items()},
            "gold_candidates": gold,
            "cross_field_gold_candidates": cross_gold,
            "cross_field_gold_not_cited_by_hint": not_cited,
            "in_R_provisional": not why_not,
            "not_in_R_because": why_not,
        }
    in_r = sorted(i for i, x in items.items() if x["in_R_provisional"])
    counts = {
        "pairs": len(pairs), "excluded_pairs": len(excluded),
        "relation": dict(Counter(p["relation"] for p in pairs if not p["excluded"])),
        "relation_decided_by": dict(Counter(p["decided_by"]["relation"].split(":")[0] for p in pairs)),
        "cross_field_decided_by": dict(Counter(p["decided_by"]["cross_field"].split(":")[0] for p in pairs)),
        "gold_pairs": sum(p["gold"] for p in pairs), "cross_field_gold_pairs": sum(p["gold"] and p["cross_field"]
                                                                                    for p in pairs),
        "items": len(items), "items_with_gold": sum(bool(x["gold_candidates"]) for x in items.values()),
        "items_with_cross_field_gold": sum(bool(x["cross_field_gold_candidates"]) for x in items.values()),
        "in_R_provisional": len(in_r),
        "built_negatives_with_gold": sorted(i for i, x in items.items() if x["negative"] and x["gold_candidates"]),
        "attributed_controls_with_cross_field_gold": sorted(i for i, x in items.items() if x["role"] == ATTRIBUTED_CONTROL
                                                            and x["cross_field_gold_candidates"]),
    }
    return {
        "format": LABELS_FORMAT, "made_by": "tools/quiz/adjudicate_quiz.py apply",
        "adjudicator": decisions["adjudicator"],
        "notes": [
            "Relations and cross-field answers were chosen by people: two annotators per pair in the quiz, and the "
            "adjudicator where the two readings differ. The quiz options were proposed by a model.",
            "decided_by 'agreed: x,y' means both readings gave the same value; 'adjudicated: <name>' means the "
            "adjudicator decided it (adjudication_reason holds the reason).",
            "Attribution was not asked in the quiz. attribution_hint is the packets' model pre-fill, not confirmed "
            "by people, so in_R_provisional is provisional until a person confirms attribution.",
            "in_R_provisional: human origin, not an attributed control (meta seed_detail.role 'attributed_control'), "
            "not a built negative (meta negative is null), and at least one cross-field gold candidate whose "
            "attribution_hint is not 'cited'. Gold is similar_mechanism or stronger.",
            "H additionally needs the v0 closed-book runs (eval/protocol.md section 3); nothing here decides H.",
            "Items without candidates (AI origin) were not in the quiz and are not listed.",
        ],
        "inputs": list(inputs), "decisions_template_hash": decisions["template_hash"], "counts": counts,
        "in_R_provisional": in_r, "excluded_pairs": excluded, "pairs": pairs, "items": items,
    }


def apply(decisions_path: Path, assignment_path: Path, answers_dir: Path, labels_path: Path,
          batches: Sequence[Path] | None = None) -> dict:
    decisions_path = Path(decisions_path)
    decisions = make_quiz.read_json(decisions_path)
    loaded = load_inputs(assignment_path, answers_dir)
    problems = check_decisions(decisions, loaded["assignment"], loaded["answers"], loaded["inputs"])
    packets, metas, batch_records, batch_problems = load_batches(batch_paths(loaded["assignment"], batches),
                                                                 loaded["assignment"])
    problems += batch_problems
    if problems:
        raise AdjudicationProblems(problems)
    inputs = (loaded["inputs"]["answers"] + [loaded["inputs"]["assignment"]] + batch_records
              + [{"file": make_quiz.display_path(decisions_path), "sha256": sha256_file(decisions_path)}])
    labels = build_labels(decisions, loaded["assignment"], loaded["answers"], packets, metas, inputs)
    labels_path = Path(labels_path)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(json.dumps(labels, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return labels


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 tools/quiz/adjudicate_quiz.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("report", "apply"):
        p = sub.add_parser(name)
        if name == "apply":
            p.add_argument("decisions", help="the filled decisions.json")
            p.add_argument("--labels", default=str(make_quiz.QUIZ_DIR / "labels.json"))
        else:
            p.add_argument("--out", default=str(ADJ_DIR))
        p.add_argument("--assignment", default=str(make_quiz.QUIZ_DIR / "assignment.json"))
        p.add_argument("--answers-dir", default=str(make_quiz.QUIZ_DIR / "answers"))
        p.add_argument("--batches", nargs="+", help="batch files (default: those named in the assignment)")
        if name == "report":
            p.add_argument("--proposals", nargs="+", help="proposals files whose private reasons must not reach "
                                                          "the page (default: proposals-*.json next to the assignment)")
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            t = report(Path(args.assignment), Path(args.answers_dir), Path(args.out), args.batches, args.proposals)
            kinds = Counter(e["kind"] for e in t["entries"])
            print(f"disagreements: {len(t['entries'])} ({kinds['relation']} relation, {kinds['cross_field']} "
                  f"cross-field) -> {args.out}/decisions.json, disagreements.html, disagreements.md")
            return 0
        labels = apply(Path(args.decisions), Path(args.assignment), Path(args.answers_dir), Path(args.labels),
                       args.batches)
    except AdjudicationProblems as err:
        print(f"{args.command} refused, nothing written:")
        for p in err.problems:
            print(f"  {p}")
        return 1
    c = labels["counts"]
    print(f"pairs: {c['pairs']} (excluded {c['excluded_pairs']}); relation decided by {c['relation_decided_by']}; "
          f"cross-field decided by {c['cross_field_decided_by']}")
    print(f"relations: {c['relation']}")
    print(f"gold pairs: {c['gold_pairs']}, cross-field gold pairs: {c['cross_field_gold_pairs']}")
    print(f"items: {c['items']}, with gold {c['items_with_gold']}, with cross-field gold "
          f"{c['items_with_cross_field_gold']}, in R (provisional) {c['in_R_provisional']}")
    if c["built_negatives_with_gold"]:
        print(f"built negatives with gold: {c['built_negatives_with_gold']}")
    print(f"labels: {args.labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
