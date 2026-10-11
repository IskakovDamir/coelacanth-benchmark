"""Describe how returned quiz files were filled in, before their answers are imported as first readings.

Prints, per returned JSON file: answer count, seconds per question (total, median, max, how many under 20), how many
answers carry a comment, comment length, comment characters per second of active time, comments ending a line with
a web-search source label or site name, comments with LaTeX, how often the answer is the proposals' model_top, and,
for every two files, agreement on the pairs they share. It judges nothing: the numbers go to Damir, who decides
whether the answers are imported (tools/quiz/README.md).

    python tools/quiz/check_returns.py eval/benchmark/v0/quiz/returned/quiz-a1-results.json ... [--out report.json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

QUIZ_DIR = Path(__file__).resolve().parents[2] / "eval" / "benchmark" / "v0" / "quiz"
# A line that ends with a source name or a bare domain, as web-search answers print their citations.
SOURCE_LABEL = re.compile(r"(?:\b(?:arXiv|PLOS|ResearchGate|PubMed Central \(PMC\)|VoteView|Springer|CiNii Research|"
                          r"Optica Publishing Group|Duke University Courses|University of Adelaide Digital Library)"
                          r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:org|edu|gov|jp|cn|com))\s*$", re.M)
LATEX = re.compile(r"\\\(|\\\[")


def load_proposals(quiz_dir: Path = QUIZ_DIR) -> dict:
    out = {}
    for path in sorted(quiz_dir.glob("proposals-*.json")):
        for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
            for cand in item["candidates"]:
                out[(item["id"], cand["cid"])] = cand
    return out


def describe(results: dict, proposals: dict) -> dict:
    answers = results["answers"]
    secs = [a["seconds"] for a in answers]
    commented = [a for a in answers if a["comment"].strip()]
    rates = [len(a["comment"]) / max(a["seconds"], 1) for a in commented]
    return {
        "annotator": results["annotator"], "exported_at": results.get("exported_at"), "answers": len(answers),
        "seconds_total": sum(secs), "seconds_median": statistics.median(secs) if secs else None,
        "seconds_max": max(secs, default=None), "answers_under_20_seconds": sum(s < 20 for s in secs),
        "answers_with_comment": len(commented),
        "comment_chars_median": statistics.median(len(a["comment"]) for a in commented) if commented else None,
        "comment_chars_per_second_median": round(statistics.median(rates), 1) if rates else None,
        "comments_with_source_label_line": sum(bool(SOURCE_LABEL.search(a["comment"])) for a in commented),
        "comments_with_latex": sum(bool(LATEX.search(a["comment"])) for a in commented),
        "relations": dict(Counter(a["relation"] or "(text only)" for a in answers)),
        "answers_equal_to_model_top": sum((proposals.get((a["item"], a["candidate"])) or {}).get("model_top")
                                          == a["relation"] for a in answers),
    }


def shared(a: dict, b: dict) -> dict:
    ra = {(x["item"], x["candidate"]): x for x in a["answers"]}
    rb = {(x["item"], x["candidate"]): x for x in b["answers"]}
    keys = sorted(set(ra) & set(rb))
    return {"annotators": [a["annotator"], b["annotator"]], "pairs": len(keys),
            "same_relation": sum(ra[k]["relation"] == rb[k]["relation"] for k in keys),
            "same_cross_field": sum(ra[k]["cross_field"] == rb[k]["cross_field"] for k in keys)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="returned quiz-<ID>-results.json files")
    parser.add_argument("--proposals-dir", type=Path, default=QUIZ_DIR)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    proposals = load_proposals(args.proposals_dir)
    results = [json.loads(p.read_text(encoding="utf-8")) for p in args.files]
    report = {"files": [str(p) for p in args.files], "per_file": [describe(r, proposals) for r in results],
              "shared": [shared(a, b) for a, b in itertools.combinations(results, 2)]}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
