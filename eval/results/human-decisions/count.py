"""Count the human decisions recorded in v0 (paper Table 4).

    python eval/results/human-decisions/count.py

Every row is a count of recorded answers in a file of this repository, read from the file, not typed in. A count is of
answers, not of people: five people took part on the author's statement, under three IDs (a1 and a2 each used by two people, a3 by one),
with no record of which of two gave a given answer (paper Section 3.8). Who decided the borderline
closed-book matches is not recorded either (annotators and the author, in an unrecorded split), so those rows are kept
apart from the annotators' rows and from the author's. Writes human-decisions.json beside this file and prints the table.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
V0 = REPO / "eval/benchmark/v0"


def load(rel: str) -> dict:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def main() -> int:
    ids = ("a1", "a2", "a3")
    quiz = sum(len(load(f"eval/benchmark/v0/quiz/answers/{a}.json")["answers"]) for a in ids)
    b03 = sum(len(load(f"eval/benchmark/v0/quiz/b03/answers/{a}.json")["answers"]) for a in ids)
    attribution = sum(len(load(f"eval/benchmark/v0/checks/returned/attribution-{a}.json")["answers"]) for a in ids)
    claimcheck = sum(len(load(f"eval/benchmark/v0/checks/returned/claimcheck-{a}.json")["answers"]) for a in ids)
    named_read = sum(len(load(f"eval/benchmark/v0/checks/returned/named-{a}.json")["answers"]) for a in ("a1", "a2"))
    adjudicated = len(load("eval/benchmark/v0/quiz/adjudication/decisions-damir.json")["entries"])
    named_adj = len(load("eval/benchmark/v0/checks/returned/named-decisions.json")["decisions"])
    borderline = len(load("eval/results/closed-book-v0/borderline-decisions.json")["decisions"])
    recheck = len(load("eval/results/closed-book-v0/borderline-recheck.json")["decisions"])
    pairs = len(load("eval/benchmark/v0/quiz/agreement.json")["pairs"])

    rows = [
        {"task": "pair label (relation and cross-field answer)", "by": "annotators", "n": quiz,
         "covers": f"{pairs} pairs of b01 and b02, two readings each",
         "source": "eval/benchmark/v0/quiz/answers/a1.json, a2.json, a3.json"},
        {"task": "disagreements settled", "by": "author", "n": adjudicated, "covers": "one-line reason each",
         "source": "eval/benchmark/v0/quiz/adjudication/decisions-damir.json"},
        {"task": "attribution of cross-field gold pairs", "by": "annotators", "n": attribution,
         "covers": "24 pairs, two readings each",
         "source": "eval/benchmark/v0/checks/returned/attribution-a*.json"},
        {"task": "claim-text check", "by": "annotators", "n": claimcheck, "covers": "51 probed items",
         "source": "eval/benchmark/v0/checks/returned/claimcheck-a*.json"},
        {"task": "pair label, batch b03 (printed sheets)", "by": "annotators", "n": b03, "covers": "3 pairs, two readings each",
         "source": "eval/benchmark/v0/quiz/b03/answers/a*.json"},
        {"task": "works the models named closed-book", "by": "annotators", "n": named_read,
         "covers": "31 works on 3 items (printed sheets)",
         "source": "eval/benchmark/v0/checks/returned/named-a1.json, named-a2.json"},
        {"task": "named works rated gold", "by": "author", "n": named_adj, "covers": "14 of the 31 works",
         "source": "eval/benchmark/v0/checks/returned/named-decisions.json"},
        {"task": "borderline closed-book matches", "by": "people, split not recorded", "n": borderline,
         "covers": "first run", "source": "eval/results/closed-book-v0/borderline-decisions.json"},
        {"task": "re-decision of random borderline matches", "by": "author", "n": recheck,
         "covers": "repeats entries of the row above",
         "source": "eval/results/closed-book-v0/borderline-recheck.json"},
    ]
    by = {}
    for r in rows:
        by[r["by"]] = by.get(r["by"], 0) + r["n"]
    out = {"format": "coelacanth-human-decisions/v1", "rows": rows, "by": by, "total": sum(r["n"] for r in rows)}
    (HERE / "human-decisions.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['n']:4d}  {r['by']:<28} {r['task']}")
    print(f"{out['total']:4d}  total   by {by}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
