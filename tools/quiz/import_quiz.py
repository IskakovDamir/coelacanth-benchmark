"""Import the annotators' quiz results and measure agreement between the two readings of each pair.

    PYTHONPATH=src python3 tools/quiz/import_quiz.py quiz-a1-results.csv quiz-a2-results.json ...

Each file is what a quiz page exported: CSV (Скачать CSV), JSON (Скачать JSON) or the tab-separated table the page
copies (Скопировать таблицу, pasted into a .tsv or .txt file). A file is checked against the quiz assignment (default
eval/benchmark/v0/quiz/assignment.json, written by make_quiz.py): a known annotator, the quiz hash of that annotator's
page, every assigned question answered exactly once and nothing else, a valid answer (a letter A, B or C whose relation
is the option shown, or "other" with one of the three relations not shown, "cannot_decide" with text, or text alone),
the cross-field answer (yes or no) and the seconds. A file with any problem is not imported and the command exits 1.

A valid file becomes <answers-dir>/<ID>.json (default eval/benchmark/v0/quiz/answers/); an existing one is kept
unless --replace, because the protocol counts first readings. Then agreement is computed over every answers file in
<answers-dir> and written to --agreement (default eval/benchmark/v0/quiz/agreement.json):
- Krippendorff's alpha, nominal (coelacanth.benchmark.agreement.krippendorff_alpha), one unit per claim-candidate
  pair and one coder per annotator, on the relation (six-way), already done (equivalent or specialization) or not,
  gold (similar_mechanism or stronger) or not, and the cross-field answer. "Не могу решить" and text-only answers
  count as missing for the relation and its two derived distinctions. Raw agreement over pairs with two readings
  is reported alongside, and per annotator pair;
- an anchoring check, separate from agreement: how often people chose the option the model ranked first
  (model_top, from the proposals files; never shown to people), per annotator and overall, with the share of A, B, C
  and Другое. One in three is what a uniform choice among the three options would give; it is a reference, not a test.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_quiz  # noqa: E402
from coelacanth.benchmark.agreement import krippendorff_alpha  # noqa: E402
from coelacanth.benchmark.items import ALREADY_DONE, PRECEDENCE, RELATIONS  # noqa: E402

RESULTS_FORMAT = "coelacanth-quiz-results/v1"
ANSWERS_FORMAT = "coelacanth-quiz-answers/v1"
AGREEMENT_FORMAT = "coelacanth-quiz-agreement/v1"
COLUMNS = ("annotator", "quiz_hash", "item", "candidate", "letter", "relation", "other_text", "cross_field", "comment",
           "seconds")
LETTERS = ("A", "B", "C")
CANNOT_DECIDE = "cannot_decide"
DISTINCTIONS = {
    "relation": "six-way relation",
    "already_done": "already done (equivalent or specialization) or not",
    "gold": "gold (similar_mechanism or stronger) or not",
    "cross_field": "cross-field answer (yes or no)",
}


class ImportProblems(ValueError):
    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def parse_results(raw: bytes, name: str = "") -> tuple[str | None, str | None, list[dict], list[str]]:
    """(annotator, quiz hash, rows, problems) from an exported file: JSON, CSV or tab-separated text."""
    text = raw.decode("utf-8-sig", errors="strict")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as err:
            return None, None, [], [f"{name}: not valid JSON ({err})"]
        if not isinstance(data, Mapping) or data.get("format") != RESULTS_FORMAT:
            return None, None, [], [f"{name}: format must be {RESULTS_FORMAT!r}"]
        answers = data.get("answers")
        if not isinstance(answers, list) or not all(isinstance(a, Mapping) for a in answers):
            return data.get("annotator"), data.get("quiz_hash"), [], [f"{name}: answers must be a list of objects"]
        return data.get("annotator"), data.get("quiz_hash"), [dict(a) for a in answers], []
    first = stripped.splitlines()[0] if stripped else ""
    delimiter = "\t" if "\t" in first else ","
    reader = csv.DictReader(io.StringIO(stripped), delimiter=delimiter)
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        return None, None, [], [f"{name}: columns missing: {missing}"]
    rows = [dict(r) for r in reader]
    problems = []
    annotators = {r.get("annotator") for r in rows}
    hashes = {r.get("quiz_hash") for r in rows}
    if len(annotators) > 1:
        problems.append(f"{name}: rows of several annotators {sorted(map(str, annotators))}")
    if len(hashes) > 1:
        problems.append(f"{name}: rows with several quiz hashes")
    return (next(iter(annotators)) if len(annotators) == 1 else None,
            next(iter(hashes)) if len(hashes) == 1 else None, rows, problems)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def validate(annotator, quiz_hash, rows: Sequence[Mapping], assignment: Mapping, name: str = "") -> tuple[list[dict], list[str]]:
    """The answers in the assignment's question order, normalised, and every problem found."""
    problems = []
    info = (assignment.get("per_annotator") or {}).get(annotator)
    if info is None:
        return [], [f"{name}: unknown annotator {annotator!r} (the assignment has {assignment.get('annotators')})"]
    if quiz_hash != info["hash"]:
        problems.append(f"{name}: quiz hash {str(quiz_hash)[:12]!r} is not the hash of {annotator}'s page "
                        f"({info['hash'][:12]}): a page of another build?")
    mine = set(info["questions"])
    got: dict[str, dict] = {}
    for n, row in enumerate(rows, 1):
        key = make_quiz.question_key(_text(row.get("item")), _text(row.get("candidate")))
        where = f"{name} row {n} ({key})"
        if key not in mine:
            problems.append(f"{where}: not a question of {annotator}")
            continue
        if key in got:
            problems.append(f"{where}: answered twice")
            continue
        options = assignment["questions"][key]["options"]
        letter = _text(row.get("letter"))
        relation = _text(row.get("relation"))
        other_text = _text(row.get("other_text"))
        cannot = False
        if letter in LETTERS:
            shown = options[LETTERS.index(letter)]
            if relation and relation != shown:
                problems.append(f"{where}: letter {letter} is {shown}, the row says {relation}")
            relation = shown
        elif letter == "other":
            if relation == CANNOT_DECIDE:
                cannot, relation = True, ""
                if not other_text:
                    problems.append(f"{where}: cannot decide without text")
            elif relation:
                if relation not in RELATIONS:
                    problems.append(f"{where}: unknown relation {relation!r}")
                elif relation in options:
                    problems.append(f"{where}: {relation} was option {LETTERS[options.index(relation)]}, not Другое")
            elif not other_text:
                problems.append(f"{where}: Другое without a relation needs text")
        else:
            problems.append(f"{where}: letter must be A, B, C or other, got {letter!r}")
        cross = _text(row.get("cross_field")).lower()
        if cross not in ("yes", "no"):
            problems.append(f"{where}: cross_field must be yes or no, got {cross!r}")
        try:
            seconds = float(_text(row.get("seconds")) or "nan")
            if not seconds >= 0:
                raise ValueError
        except ValueError:
            problems.append(f"{where}: seconds must be a number of at least 0, got {row.get('seconds')!r}")
            seconds = None
        got[key] = {"item": _text(row.get("item")), "candidate": _text(row.get("candidate")), "letter": letter,
                    "relation": relation or None, "cannot_decide": cannot, "other_text": other_text,
                    "cross_field": cross == "yes" if cross in ("yes", "no") else None,
                    "comment": _text(row.get("comment")), "seconds": seconds}
    unanswered = [k for k in info["questions"] if k not in got]
    if unanswered:
        problems.append(f"{name}: {len(unanswered)} assigned question(s) unanswered, such as {unanswered[:5]}")
    return [got[k] for k in info["questions"] if k in got], problems


def import_file(path: Path, assignment: Mapping, answers_dir: Path, replace: bool = False) -> dict:
    """Check one results file and write <answers_dir>/<ID>.json; raises ImportProblems, writing nothing."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        annotator, quiz_hash, rows, problems = parse_results(raw, path.name)
    except UnicodeDecodeError as err:
        raise ImportProblems([f"{path.name}: not UTF-8 text ({err})"]) from err
    if problems:
        raise ImportProblems(problems)
    answers, problems = validate(annotator, quiz_hash, rows, assignment, path.name)
    if problems:
        raise ImportProblems(problems)
    target = Path(answers_dir) / f"{annotator}.json"
    if target.exists() and not replace:
        raise ImportProblems([f"{target} exists (the first reading is kept); pass --replace to overwrite it"])
    record = {"format": ANSWERS_FORMAT, "annotator": annotator, "quiz_hash": quiz_hash,
              "note": "Answers chosen by the annotator in the quiz; the options shown were proposed by a model.",
              "source": {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()},
              "imported_by": "tools/quiz/import_quiz.py", "answers": answers}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def _value(answer: Mapping | None, distinction: str):
    if answer is None:
        return None
    if distinction == "cross_field":
        return None if answer.get("cross_field") is None else ("cross_field" if answer["cross_field"] else "same_field")
    relation = answer.get("relation")
    if relation not in RELATIONS:
        return None
    if distinction == "relation":
        return relation
    if distinction == "already_done":
        return "already_done" if relation in ALREADY_DONE else "not_already_done"
    return "gold" if relation in PRECEDENCE else "not_gold"


def agreement(assignment: Mapping, answers: Mapping[str, Mapping], proposals: Mapping[tuple[str, str], Mapping] | None,
              inputs: Sequence[Mapping] = ()) -> dict:
    """The agreement report over the annotators whose answers are given (annotator -> answers record)."""
    coders = [a for a in assignment["annotators"] if a in answers]
    by_coder = {a: {make_quiz.question_key(x["item"], x["candidate"]): x for x in answers[a]["answers"]} for a in coders}
    keys = list(assignment["questions"])
    readings = Counter(sum(k in by_coder[a] for a in coders) for k in keys)
    report = {"format": AGREEMENT_FORMAT, "made_by": "tools/quiz/import_quiz.py", "inputs": list(inputs),
              "annotators": coders, "unit": "claim-candidate pair",
              "pairs": {"total": len(keys), "two_or_more_readings": sum(v for n, v in readings.items() if n >= 2),
                        "one_reading": readings.get(1, 0), "no_reading": readings.get(0, 0)},
              "cannot_decide": {a: sum(x["cannot_decide"] for x in by_coder[a].values()) for a in coders},
              "text_only": {a: sum(not x["relation"] and not x["cannot_decide"] for x in by_coder[a].values())
                            for a in coders},
              "distinctions": {}, "pairs_of_annotators": {}}
    for d, label in DISTINCTIONS.items():
        units = [[_value(by_coder[a].get(k), d) for a in coders] for k in keys]
        pairable = [[v for v in u if v is not None] for u in units]
        pairable = [u for u in pairable if len(u) >= 2]
        report["distinctions"][d] = {
            "label": label, "alpha": krippendorff_alpha(units), "pairable_units": len(pairable),
            "raw_agreement": round(sum(len(set(u)) == 1 for u in pairable) / len(pairable), 4) if pairable else None,
            "values": dict(sorted(Counter(v for u in units for v in u if v is not None).items()))}
    for a, b in combinations(coders, 2):
        shared = [k for k in keys if k in by_coder[a] and k in by_coder[b]]
        row = {"shared_pairs": len(shared)}
        for d in DISTINCTIONS:
            both = [(_value(by_coder[a][k], d), _value(by_coder[b][k], d)) for k in shared]
            both = [p for p in both if None not in p]
            row[d] = {"pairs": len(both), "raw_agreement": round(sum(x == y for x, y in both) / len(both), 4)
                      if both else None, "alpha": krippendorff_alpha([list(p) for p in both]) if both else None}
        report["pairs_of_annotators"][f"{a} {b}"] = row
    report["anchoring"] = anchoring(by_coder, proposals, assignment)
    return report


def anchoring(by_coder: Mapping[str, Mapping[str, Mapping]], proposals, assignment: Mapping) -> dict:
    out = {"note": ("How often people chose the option the model ranked first (model_top, never shown). A uniform "
                    "choice among the three options would give one in three; this is a reference, not a test."),
           "per_annotator": {}, "overall": None}
    if not proposals:
        out["note"] += " Proposals not available: not computed."
        return out
    mismatched = sorted(k for k, q in assignment["questions"].items()
                        if tuple(k.split("/", 1)) not in proposals
                        or list(proposals[tuple(k.split("/", 1))]["options"]) != list(q["options"]))
    if mismatched:
        out["options_differ_from_assignment"] = mismatched
    total = Counter()
    for a, answers in by_coder.items():
        c = Counter()
        for key, x in answers.items():
            c["answers"] += 1
            c["letter " + (x["letter"] or "none")] += 1
            prop = proposals.get(tuple(key.split("/", 1)))
            if x["relation"] in RELATIONS and prop is not None:
                c["with_relation"] += 1
                c["chose_model_top"] += x["relation"] == prop["model_top"]
        total.update(c)
        out["per_annotator"][a] = _anchor_row(c)
    out["overall"] = _anchor_row(total)
    return out


def _anchor_row(c: Counter) -> dict:
    n = c["answers"]
    return {"answers": n, "with_relation": c["with_relation"], "chose_model_top": c["chose_model_top"],
            "share_model_top": round(c["chose_model_top"] / c["with_relation"], 4) if c["with_relation"] else None,
            "letters": {k: round(c[f"letter {k}"] / n, 4) if n else None for k in ("A", "B", "C", "other")}}


def load_answers(answers_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    answers, inputs = {}, []
    for path in sorted(Path(answers_dir).glob("*.json")):
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        if data.get("format") == ANSWERS_FORMAT:
            answers[data["annotator"]] = data
            inputs.append({"file": make_quiz.display_path(path), "sha256": hashlib.sha256(raw).hexdigest()})
    return answers, inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 tools/quiz/import_quiz.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="exported results (CSV, JSON or tab-separated)")
    parser.add_argument("--assignment", default=str(make_quiz.QUIZ_DIR / "assignment.json"))
    parser.add_argument("--proposals", nargs="+", help="proposals files for the anchoring check (default: "
                                                       "eval/benchmark/v0/quiz/proposals-*.json)")
    parser.add_argument("--answers-dir", default=str(make_quiz.QUIZ_DIR / "answers"))
    parser.add_argument("--agreement", default=str(make_quiz.QUIZ_DIR / "agreement.json"))
    parser.add_argument("--replace", action="store_true", help="overwrite an existing answers file")
    args = parser.parse_args(argv)
    assignment = make_quiz.read_json(Path(args.assignment))
    failed = False
    for f in args.files:
        try:
            record = import_file(Path(f), assignment, Path(args.answers_dir), args.replace)
        except ImportProblems as err:
            failed = True
            print(f"not imported: {f}")
            for p in err.problems:
                print(f"  {p}")
            continue
        print(f"imported {f}: {record['annotator']}, {len(record['answers'])} answers -> "
              f"{Path(args.answers_dir) / (record['annotator'] + '.json')}")
    answers, inputs = load_answers(Path(args.answers_dir))
    paths = [Path(p) for p in args.proposals] if args.proposals else sorted(make_quiz.QUIZ_DIR.glob(make_quiz.PROPOSALS_GLOB))
    proposals, _, problems = make_quiz.load_proposals(paths) if paths else ({}, [], ["no proposals files"])
    if problems:
        print(f"anchoring check skipped: {problems[0]}")
        proposals = {}
    report = agreement(assignment, answers, proposals, inputs)
    Path(args.agreement).parent.mkdir(parents=True, exist_ok=True)
    Path(args.agreement).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"pairs with two readings: {report['pairs']['two_or_more_readings']} of {report['pairs']['total']}")
    for d, r in report["distinctions"].items():
        alpha = "undefined" if r["alpha"] is None else f"{r['alpha']:.3f}"
        print(f"  {r['label']}: alpha {alpha}, raw agreement {r['raw_agreement']}, {r['pairable_units']} pairs")
    overall = report["anchoring"]["overall"]
    if overall:
        print(f"anchoring: model_top chosen in {overall['chose_model_top']} of {overall['with_relation']} answers "
              f"with a relation ({overall['share_model_top']})")
    print(f"agreement: {args.agreement}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
