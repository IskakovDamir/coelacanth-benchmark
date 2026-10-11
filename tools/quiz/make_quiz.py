"""Build the annotators' quiz: one self-contained HTML page per annotator, from the batches and the model's proposals.

    PYTHONPATH=src python3 tools/quiz/make_quiz.py --annotators a1 a2 a3 --out eval/benchmark/v0/quiz/send

For every claim-candidate pair of the batches (eval/benchmark/v0/batches/b01.json and b02.json), a person chooses the
relation from three options proposed by a model (A, B, C, always in precedence order) or "Другое", and says whether the
candidate is from a different field. Humans choose every label; the options are model output and the data files say
so (eval/benchmark/v0/quiz/README.md).

Inputs:
- the batches; items without candidates (the AI-origin items of b02) are left out of the quiz and listed;
- every eval/benchmark/v0/quiz/proposals-*.json (format coelacanth-quiz-proposals/v1). The run stops, writing
  nothing, when a pair of the batches has no proposal, a pair is proposed twice or is not in the batches, options are
  not exactly three distinct relations sorted by precedence, model_top is not one of them, or model_confidence is not
  1 to 3.

The assignment comes from coelacanth.benchmark.assign.assign, in memory, once per batch, on the batch's items that
have candidates: every item goes to exactly two annotators (the batch's shared reliability set to the first two named,
as assign's protocol rule says; --shared overrides it). No crossover plan is passed: assign does not need one, and
the quiz shows the pre-filled quotes to everyone. Nothing is written to batches/assigned/.

Outputs (deterministic: the same inputs give the same bytes):
- <out>/quiz-<ID>.html for each annotator: only their items, each candidate with its options in precedence order and
  WITHOUT model_top, model_confidence or reason;
- the assignment (default eval/benchmark/v0/quiz/assignment.json): items and questions per annotator, each
  question's two annotators and options, counts, the data hash embedded in each page and the page's SHA-256, and the
  inputs' SHA-256. tools/quiz/import_quiz.py checks returned results against it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from coelacanth.benchmark.assign import annotator_problems, assign  # noqa: E402
from coelacanth.benchmark.items import RELATIONS  # noqa: E402

QUIZ_DIR = REPO / "eval" / "benchmark" / "v0" / "quiz"
BATCHES = [REPO / "eval" / "benchmark" / "v0" / "batches" / "b01.json",
           REPO / "eval" / "benchmark" / "v0" / "batches" / "b02.json"]
TEMPLATE = Path(__file__).resolve().parent / "quiz-template.html"
PROPOSALS_GLOB = "proposals-*.json"
PROPOSALS_FORMAT = "coelacanth-quiz-proposals/v1"
QUIZ_FORMAT = "coelacanth-quiz/v1"
ASSIGNMENT_FORMAT = "coelacanth-quiz-assignment/v1"
MINUTES_PER_QUESTION = 1.5
# Fields of the proposals that never go into a page.
PRIVATE_FIELDS = ("model_top", "model_confidence", "reason")
WORK_FIELDS = ("title", "first_author", "n_authors", "year", "publication_date", "venue", "field", "doi", "openalex",
               "arxiv", "pmid", "oa_url")
EVIDENCE_FIELDS = ("element", "quote_candidate", "quote_claim", "location_candidate", "location_claim", "text_level",
                   "general_form")
DATA_RE = re.compile(r'(<script type="application/json" id="quiz-data">)(.*?)(</script>)', re.S)


class QuizError(ValueError):
    """Raised with every problem found; nothing has been written."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def display_path(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def question_key(item_id: str, cid: str) -> str:
    return f"{item_id}/{cid}"


def batch_pairs(batches: Sequence[Mapping]) -> list[tuple[str, str]]:
    """Every (item id, candidate id) of the batches, in batch order."""
    return [(p["id"], c["cid"]) for b in batches for p in b["items"] for c in p.get("candidates") or []]


def option_problems(where: str, cand: Mapping) -> list[str]:
    out = []
    options = cand.get("options")
    if not isinstance(options, list) or len(options) != 3:
        return [f"{where}: options must be a list of exactly three relations, got {options!r}"]
    if any(o not in RELATIONS for o in options):
        out.append(f"{where}: unknown relation in options {options}")
        return out
    if len(set(options)) != 3:
        out.append(f"{where}: options repeat a relation {options}")
    elif options != sorted(options, key=RELATIONS.index):
        out.append(f"{where}: options {options} are not sorted by precedence "
                   f"({sorted(options, key=RELATIONS.index)})")
    if cand.get("model_top") not in options:
        out.append(f"{where}: model_top {cand.get('model_top')!r} is not one of the options {options}")
    conf = cand.get("model_confidence")
    if not (isinstance(conf, int) and not isinstance(conf, bool) and 1 <= conf <= 3):
        out.append(f"{where}: model_confidence must be 1, 2 or 3, got {conf!r}")
    if not isinstance(cand.get("reason"), str) or not cand["reason"].strip():
        out.append(f"{where}: reason must be a non-empty sentence")
    return out


def load_proposals(paths: Sequence[Path]) -> tuple[dict[tuple[str, str], dict], list[dict], list[str]]:
    """The proposals by pair, a record of each file (path, SHA-256, made_by, made_at, part), and every problem found
    in the files themselves (format, duplicates, options)."""
    by_pair: dict[tuple[str, str], dict] = {}
    seen_in: dict[tuple[str, str], str] = {}
    files, problems = [], []
    if not paths:
        problems.append("no proposals files found")
    for path in sorted(Path(p) for p in paths):
        raw = path.read_bytes()
        name = path.name
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            problems.append(f"{name}: not valid JSON ({err})")
            continue
        if not isinstance(data, Mapping) or data.get("format") != PROPOSALS_FORMAT:
            problems.append(f"{name}: format must be {PROPOSALS_FORMAT!r}")
            continue
        if not isinstance(data.get("made_by"), str) or not data["made_by"].strip():
            problems.append(f"{name}: made_by is missing (proposals are model output and must say so)")
        files.append({"file": display_path(path), "sha256": sha256_bytes(raw), "made_by": data.get("made_by"),
                      "made_at": data.get("made_at"), "part": data.get("part")})
        for item in data.get("items") or []:
            item_id = item.get("id") if isinstance(item, Mapping) else None
            if not isinstance(item_id, str):
                problems.append(f"{name}: an item without an id")
                continue
            for cand in item.get("candidates") or []:
                cid = cand.get("cid") if isinstance(cand, Mapping) else None
                if not isinstance(cid, str):
                    problems.append(f"{name} {item_id}: a candidate without a cid")
                    continue
                pair = (item_id, cid)
                where = f"{name} {question_key(*pair)}"
                if pair in by_pair:
                    problems.append(f"{where}: proposed twice (also in {seen_in[pair]})")
                    continue
                problems.extend(option_problems(where, cand))
                by_pair[pair] = dict(cand)
                seen_in[pair] = name
    return by_pair, files, problems


def coverage_problems(by_pair: Mapping[tuple[str, str], Mapping], batches: Sequence[Mapping]) -> list[str]:
    pairs = batch_pairs(batches)
    known = set(pairs)
    out = [f"{question_key(*p)}: no proposal" for p in pairs if p not in by_pair]
    out += [f"{question_key(*p)}: proposed, but not a pair of the batches" for p in by_pair if p not in known]
    return out


def work_view(work: Mapping | None) -> dict:
    work = work or {}
    return {k: work[k] for k in WORK_FIELDS if work.get(k) not in (None, "")}


def item_view(packet: Mapping, batch_name: str, by_pair: Mapping) -> dict:
    claim = packet.get("claim") or {}
    return {
        "id": packet["id"], "batch": batch_name,
        "claim": {"date": claim.get("date"), "field": claim.get("field"), "text": claim.get("text"),
                  "skeleton": claim.get("skeleton"),
                  "chart": [{"id": e.get("id"), "text": e.get("text")} for e in claim.get("chart") or []]},
        "source": work_view(claim.get("source_work")),
        "candidates": [{"cid": c["cid"], "work": work_view(c.get("work")), "note": c.get("note") or "",
                        "evidence": [{k: e.get(k) for k in EVIDENCE_FIELDS} for e in c.get("evidence") or []],
                        "options": list(by_pair[(packet["id"], c["cid"])]["options"])}
                       for c in packet.get("candidates") or []]}


def data_hash(data: Mapping) -> str:
    body = {k: v for k, v in data.items() if k != "hash"}
    return sha256_bytes(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def private_keys(value) -> set[str]:
    """Keys named in PRIVATE_FIELDS anywhere in a JSON value."""
    if isinstance(value, Mapping):
        return {k for k in value if k in PRIVATE_FIELDS} | set().union(*(private_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(private_keys(v) for v in value))
    return set()


def render_page(template: str, data: Mapping) -> str:
    # "<" only occurs inside JSON strings, where < is the same character: the data can never close the tag
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    matches = DATA_RE.findall(template)
    if len(matches) != 1:
        raise QuizError([f"the template must hold exactly one quiz-data script, found {len(matches)}"])
    return DATA_RE.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), template, count=1)


def build(batches: Sequence[Mapping], by_pair: Mapping[tuple[str, str], Mapping], annotators: Sequence[str],
          proposal_files: Sequence[Mapping] = (), shared: int | None = None) -> tuple[dict[str, dict], dict]:
    """Each annotator's page data and the assignment record (without the page files' SHA-256)."""
    problems = annotator_problems(annotators) + coverage_problems(by_pair, batches)
    if problems:
        raise QuizError(problems)
    annotators = list(annotators)
    names = [b.get("batch") for b in batches]
    if len(set(names)) != len(names):
        raise QuizError([f"batch names repeat: {names}"])
    prefill_by = sorted({(p.get("prefill") or {}).get("by") for b in batches for p in b["items"]} - {None})
    options_by = sorted({f.get("made_by") for f in proposal_files} - {None})
    mine: dict[str, list[dict]] = {a: [] for a in annotators}
    questions: dict[str, dict] = {}
    batch_records = []
    for batch in batches:
        with_cands = [p for p in batch["items"] if p.get("candidates")]
        dropped = [p["id"] for p in batch["items"] if not p.get("candidates")]
        if not with_cands:
            raise QuizError([f"batch {batch.get('batch')} has no item with candidates"])
        try:
            plan = assign(dict(batch, items=with_cands), annotators, None, shared)
        except ValueError as err:
            raise QuizError([f"batch {batch.get('batch')}: {err}"]) from err
        who = {i["id"]: i["annotators"] for i in plan["items"]}
        for packet in with_cands:
            view = item_view(packet, batch["batch"], by_pair)
            for a in who[packet["id"]]:
                mine[a].append(view)
            for c in packet["candidates"]:
                questions[question_key(packet["id"], c["cid"])] = {
                    "batch": batch["batch"], "annotators": list(who[packet["id"]]),
                    "options": list(by_pair[(packet["id"], c["cid"])]["options"])}
        batch_records.append({"batch": batch["batch"], "items": len(batch["items"]), "items_in_quiz": len(with_cands),
                              "pairs": sum(len(p["candidates"]) for p in with_cands),
                              "dropped_without_candidates": dropped, "shared": plan["shared"],
                              "loads": {a: plan["loads"][a]["items"] for a in annotators}, "pairs_of_annotators": plan["pairs"]})
    pages, per_annotator = {}, {}
    for a in annotators:
        data = {"format": QUIZ_FORMAT, "annotator": a, "hash": "", "prefill_by": ", ".join(prefill_by),
                "options_by": ", ".join(options_by), "relations": list(RELATIONS), "items": mine[a]}
        data["hash"] = data_hash(data)
        pages[a] = data
        keys = [question_key(i["id"], c["cid"]) for i in mine[a] for c in i["candidates"]]
        per_annotator[a] = {"file": f"quiz-{a}.html", "hash": data["hash"], "items": [i["id"] for i in mine[a]],
                            "n_items": len(mine[a]), "questions": keys, "n_questions": len(keys),
                            "estimated_minutes": round(len(keys) * MINUTES_PER_QUESTION)}
    assignment = {
        "format": ASSIGNMENT_FORMAT, "made_by": "tools/quiz/make_quiz.py",
        "note": ("Options are model output (proposals files); people choose every label. model_top, model_confidence "
                 "and reason stay in the proposals files and are not in the pages."),
        "annotators": annotators, "minutes_per_question": MINUTES_PER_QUESTION, "proposals": list(proposal_files),
        "batches": batch_records,
        "counts": {"items": sum(r["items_in_quiz"] for r in batch_records), "pairs": len(questions),
                   "readings": 2 * len(questions),
                   "dropped_without_candidates": sum(len(r["dropped_without_candidates"]) for r in batch_records)},
        "per_annotator": per_annotator, "questions": questions}
    return pages, assignment


def make(batch_paths: Sequence[Path], proposal_paths: Sequence[Path], annotators: Sequence[str], out_dir: Path,
         assignment_path: Path, template_path: Path = TEMPLATE, shared: int | None = None) -> dict:
    """Validate everything, then write the pages and the assignment. Raises QuizError before writing anything."""
    batches = [read_json(p) for p in batch_paths]
    by_pair, files, problems = load_proposals(proposal_paths)
    if problems:
        raise QuizError(problems)
    pages, assignment = build(batches, by_pair, annotators, files, shared)
    assignment["batch_files"] = [{"file": display_path(p), "sha256": sha256_bytes(Path(p).read_bytes())}
                                 for p in batch_paths]
    template = Path(template_path).read_text(encoding="utf-8")
    html = {a: render_page(template, data) for a, data in pages.items()}
    for a, text in html.items():
        leaks = sorted(private_keys(pages[a])) + [f for f in PRIVATE_FIELDS[:2] if f in text]
        if leaks:
            raise QuizError([f"quiz-{a}.html would contain {leaks}"])
        assignment["per_annotator"][a]["sha256"] = sha256_bytes(text.encode("utf-8"))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for a, text in html.items():
        (out_dir / f"quiz-{a}.html").write_text(text, encoding="utf-8")
    assignment["out_dir"] = display_path(out_dir)
    Path(assignment_path).parent.mkdir(parents=True, exist_ok=True)
    Path(assignment_path).write_text(json.dumps(assignment, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return assignment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 tools/quiz/make_quiz.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--annotators", nargs="+", required=True, metavar="ID")
    parser.add_argument("--out", default=str(QUIZ_DIR / "send"), help="folder for quiz-<ID>.html")
    parser.add_argument("--batches", nargs="+", default=[str(p) for p in BATCHES])
    parser.add_argument("--proposals", nargs="+", help=f"proposals files (default: {display_path(QUIZ_DIR)}/{PROPOSALS_GLOB})")
    parser.add_argument("--assignment", default=str(QUIZ_DIR / "assignment.json"))
    parser.add_argument("--template", default=str(TEMPLATE))
    parser.add_argument("--shared", type=int, help="override assign's shared set (first N items by ID of each batch "
                                                   "to the first two annotators; default: assign's protocol rule)")
    args = parser.parse_args(argv)
    proposals = [Path(p) for p in args.proposals] if args.proposals else sorted(QUIZ_DIR.glob(PROPOSALS_GLOB))
    try:
        assignment = make([Path(b) for b in args.batches], proposals, args.annotators, Path(args.out),
                          Path(args.assignment), Path(args.template), args.shared)
    except QuizError as err:
        print(f"nothing written: {len(err.problems)} problem(s)")
        for p in err.problems:
            print(f"  {p}")
        return 1
    c = assignment["counts"]
    print(f"{c['items']} items, {c['pairs']} pairs, each read by two annotators ({c['readings']} readings)")
    for r in assignment["batches"]:
        if r["dropped_without_candidates"]:
            print(f"left out of {r['batch']} (no candidates): {', '.join(r['dropped_without_candidates'])}")
    for a, info in assignment["per_annotator"].items():
        print(f"{a}: {info['n_items']} items, {info['n_questions']} questions, about {info['estimated_minutes']} "
              f"minutes at {MINUTES_PER_QUESTION} minutes per question -> {Path(args.out) / info['file']}")
    print(f"assignment: {args.assignment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
