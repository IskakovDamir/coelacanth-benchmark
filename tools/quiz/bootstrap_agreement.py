"""Percentile bootstrap intervals for the quiz agreement (protocol decision 18: Krippendorff's alpha, 95% interval from
a seeded bootstrap over items, 2,000 resamples, as in coelacanth.benchmark.agreement).

    PYTHONPATH=src python3 tools/quiz/bootstrap_agreement.py

Reads eval/benchmark/v0/quiz/assignment.json and answers/*.json (b01 and b02; the b03 pairs are outside the
reliability sample, see quiz/b03/returned/PROVENANCE.md), forms the same units and values as import_quiz.agreement
(one unit per claim-candidate pair, one coder per annotator ID), and resamples items, not pairs. Writes
eval/benchmark/v0/quiz/agreement-bootstrap.json with the point alpha (which must equal agreement.json's), the interval
and the number of resamples where alpha was undefined.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_quiz  # noqa: E402
import make_quiz  # noqa: E402
from coelacanth.benchmark import agreement as ag  # noqa: E402

QUIZ = REPO / "eval" / "benchmark" / "v0" / "quiz"


def main() -> int:
    assignment = json.loads((QUIZ / "assignment.json").read_text(encoding="utf-8"))
    answers, _ = import_quiz.load_answers(QUIZ / "answers")
    coders = [a for a in assignment["annotators"] if a in answers]
    by_coder = {a: {make_quiz.question_key(x["item"], x["candidate"]): x for x in answers[a]["answers"]} for a in coders}
    keys = list(assignment["questions"])
    items = sorted({k.split("/")[0] for k in keys})
    stored = json.loads((QUIZ / "agreement.json").read_text(encoding="utf-8"))["distinctions"]
    draws = ag.resample_indices(len(items))
    out = {"format": "coelacanth-quiz-agreement-bootstrap/v1", "made_by": "tools/quiz/bootstrap_agreement.py",
           "unit": "claim-candidate pair", "resampled": "items", "items": len(items), "pairs": len(keys),
           "resamples": ag.RESAMPLES, "seed": ag.SEED, "annotators": coders, "distinctions": {}}
    for d, label in import_quiz.DISTINCTIONS.items():
        per_pair = {k: [import_quiz._value(by_coder[a].get(k), d) for a in coders] for k in keys}
        point = ag.krippendorff_alpha(per_pair.values())
        grouped = defaultdict(list)
        for k, unit in per_pair.items():
            grouped[k.split("/")[0]].append(unit)
        per_item = [ag.coincidences(grouped[i]) for i in items]
        boot = ag.bootstrap(per_item, draws)
        defined = sorted(v for v in boot if v is not None)
        if abs((point or 0) - (stored[d]["alpha"] or 0)) > 1e-9:
            print(f"{d}: alpha {point} differs from agreement.json {stored[d]['alpha']}; not written")
            return 1
        out["distinctions"][d] = {
            "label": label, "alpha": point,
            "ci95": [round(ag.quantile(defined, 0.025), 3), round(ag.quantile(defined, 0.975), 3)] if defined else None,
            "undefined_resamples": len(boot) - len(defined)}
        print(f"{d}: alpha {point:.3f}, 95% interval {out['distinctions'][d]['ci95']}, "
              f"undefined in {out['distinctions'][d]['undefined_resamples']} of {len(boot)}")
    (QUIZ / "agreement-bootstrap.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
