"""Scoring for measurement A (eval/protocol.md decisions 5, 23 and 31 and sections 6 to 8; docs/design/harness.md
section 8.8): the metrics of Table 7 from the `resolved.jsonl` files `resolve_run` writes.

    python -m coelacanth.harness.score RUN_DIR [RUN_DIR ...] --splits PATH --items PATH --out PATH

reads each run directory's `resolved.jsonl` (cost from its `outputs.jsonl`, else its `ledger.jsonl`), the benchmark
items (gold works and their cross-field flags) and the splits file (docs/design/harness.md 8.3), and writes
`scores.json` and a markdown table of Table 7 rows. `--out` is a directory, or a path ending in `.json` (the table is
written beside it with the suffix `.md`). Nothing here calls a network or a model; the functions take plain dicts.

Per system and condition (one pair is one scored system; runs of the same pair are merged), per subset:
- the subset is `splits["subsets"][name]` restricted to the items the system has a row for (`n_run`; `n_subset` is
  the size in the splits file). Subsets are listed H first, then the other H variants, R, the recall stratum
  `R_recalled` (derived as R minus H when the splits file lacks it), P40, P20, ALL, C, A, N, K, then any others.
  Subsets with no item run are left out. A subset is `descriptive_only` when fewer than 20 items were run (decision
  23) and always when it is in the H family (`H`, `H_any`, `H_post`, `H+C`, ...) or is `R_recalled` (decision 23:
  nothing on H is inferential; recall strata are descriptive).
- Retrieval (decision 5, section 7). Gold and cross-field flags come from the items file only: a gold work is a
  candidate with `gold: true`, a cross-field gold work one with `cross_field: true` too. A system's top 10 is its
  kept candidates with `rank_clean` of at most 10 (leaks, exclusions and duplicates already removed by the resolver;
  placeholders, which hold a rank, never match gold). A candidate matches a gold work when they share an OpenAlex ID,
  DOI, arXiv ID or PMID (or a W ID in the candidate's `same_work`); matching uses identifiers only. A failure
  (status not ok, or `failure: true`) has no ranking: it counts as an item with no output, so a miss, never dropped.
  Cross-field metrics are over the items with at least one cross-field gold work (`n_eval`), the all-gold metrics
  over the items with at least one gold work, the usual rule for queries without a relevant document; the other
  items are counted (`n_without_cf_gold`). The primary metric is reported as the MISS rate k of n_eval with its
  Wilson 95% interval (`closedbook.wilson`); the success rate is given with the same k and n. MRR@10 is the mean over
  items of 1/rank_clean of the first cross-field gold work (0 beyond rank 10 or with none). Recall@10 is the share of
  an item's gold works found in the top 10, averaged over items. The protocol lists "success@1" without saying over
  which gold: it is read as cross-field success@1 (the primary metric's list), and the all-gold variant is given too.
- Verdicts (decision 5, section 7). The adjudicated verdict is `item["verdict"]` (else derived from the item's
  relations); its three-class form is `labels.THREE_CLASS`. The verdict set of a subset that lies inside R is that
  subset plus N ("R plus N", so every row of the R family has the nothing_close class); for any other subset (ALL, C,
  A, N, K) it is the subset itself. Without an R subset in the splits file, every subset but N, ALL, C, A and K counts
  as R family. Macro-F1 averages the F1 of the classes that occur in the adjudicated or predicted labels (the default
  of scikit-learn's macro average), F1 = 2TP / (2TP + FP + FN). An item with no label (a failure, or an unmappable
  native label) is a false negative of its true class and a false positive of none; both are counted in the
  confusion matrix as the columns `failure` and `unmappable`. A system whose rule is `none` (Kulikowski) gives no
  verdict by design: its rows are left out of the verdict metrics and counted. The six-class matrix (descriptive)
  holds the items with a six-way label; a system with only three-class labels has none (`six_class` null) and its
  labelled items count in the column `no_six`. Already-done precision is TP / predicted for the class already_done.
  The false already-done rate is the share of items whose adjudicated verdict is weaker than specialization (every
  item not equivalent or specialization) that the system called already_done; failures and unmappable items stay in
  its denominator. For item-label systems (rule `native_item_label`) a sensitivity line drops the items with a
  leakage hit in the top 10 or the evidence (decision 31).
- Calibration (section 7). `probability` is read as the system's probability that its three-class verdict is correct
  (`calibration_target="label_correct"`, the default; the outcome is 1 when the predicted class equals the
  adjudicated one); `already_done` reads it as the probability that the claim is already done (the outcome is 1
  when the adjudicated class is already_done). Over the labelled items of the verdict set that carry a probability;
  Brier score and ECE with 10 equal-width bins (a probability of 1 falls in the last bin), with the bins for a
  reliability diagram. Null when no item carries a probability. A probability outside [0, 1] is an error.
- Leakage: the share of items run with a top 10 or evidence hit in the resolved `leakage` block, and hits per item.
  An item whose block is missing (a resolution error) counts without a hit and is listed in `n_leakage_unknown`.
- Cost: dollars per item from each row's `cost.usd` (outputs.jsonl, else the ledger's `cost_usd`; for an item with
  several rows the last, the one that was scored), mean, maximum and total over the items with a figure; OpenAlex
  list-price spend of the bound searches is separate (`openalex_usd_per_item`), the resolver's spend is the
  harness's and is never booked here.
- Status counts, failures and rows still due for a re-run (`infra_error`, `model_mismatch`) are reported; the latter
  count as no output, and the run is not final until they are re-run (decision 33).

H's k of n (decision 23, section 1): `subsets` of the output gives, for every H-family subset and the strata, its
size against R (k = size of the subset, n = size of R, Wilson 95%). Table rows are alphabetical by system within a
subset; systems run at different budgets are not ranked against each other (section 7).

Guards: pilot runs (run.json `kind` other than scored) are refused, an item answered twice by one system and
condition is refused, and runs of one system and condition made under different config hashes are refused.

`mcnemar_exact` is the exact two-sided McNemar test of section 8 on two systems' per-item binary outcomes
(`per_item` of the output), a helper only: no confirmatory family and no Holm step live here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..benchmark.items import ALREADY_DONE, VERDICTS, derive_verdict, load_items
from ..closedbook import wilson
from ..data.openalex import PRICE_USD
from ..env import REPO_ROOT
from ..llm import read_jsonl
from .labels import CLASS3, THREE_CLASS
from .retrieval import canonical_id, work_keys

FORMAT = "coelacanth-scores/v1"
K = 10
ECE_BINS = 10
MIN_ITEMS = 20  # decision 23: subsets under 20 items are descriptive
ID_KINDS = ("openalex", "doi", "arxiv", "pmid")
INFRA = ("infra_error", "model_mismatch")  # due for a re-run (run.INFRA)
SUBSET_ORDER = ("H", "H_any", "H_post", "H_half", "R", "R_recalled", "P40", "P20", "ALL", "C", "A", "N", "K")
NOT_R_FAMILY = ("N", "ALL", "C", "A", "K")  # when the splits file has no R subset
CALIBRATION_TARGETS = ("label_correct", "already_done")
H_FAMILY_RE = re.compile(r"H(?:$|[_+\-])")
PRED_COLUMNS = (*CLASS3, "unmappable", "failure")
SIX_COLUMNS = (*VERDICTS, "no_six", "unmappable", "failure")


# ---------- small statistics ----------

def rate(k: int, n: int) -> dict:
    """k of n with the share and its Wilson 95% interval (null when n is 0)."""
    return {"k": k, "n": n, "rate": k / n if n else None, "wilson95": list(wilson(k, n)) if n else None}


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def mcnemar_exact(b: int, c: int) -> dict:
    """Exact two-sided McNemar test: `b` items system A got and B did not, `c` the reverse. The p-value is twice the
    lower binomial tail of min(b, c) in b + c fair coin flips, capped at 1 (b = 5, c = 1 gives 0.21875); 1.0 when
    there are no discordant items. Computed in exact integer arithmetic."""
    if b < 0 or c < 0:
        raise ValueError("discordant counts cannot be negative")
    n = b + c
    p = 1.0 if n == 0 else min(1.0, float(Fraction(2 * sum(comb(n, i) for i in range(min(b, c) + 1)), 2 ** n)))
    return {"b": b, "c": c, "discordant": n, "p_value": p}


def mcnemar_paired(a: Mapping[str, Any], b: Mapping[str, Any], items: Iterable[str] | None = None) -> dict:
    """McNemar on two systems' per-item outcomes (item ID to truthy, falsy or None; None means the metric is
    undefined for the item and the item is left out). Pairs only the items both systems have an outcome for, within
    `items` when given; items present for one system alone are counted, not dropped. `diff` is the paired effect
    (success rate of A minus that of B)."""
    pool = set(a) | set(b) if items is None else set(items)
    both = sorted(i for i in pool if a.get(i) is not None and b.get(i) is not None)
    yes_a = sum(1 for i in both if a[i] and not b[i])
    yes_b = sum(1 for i in both if b[i] and not a[i])
    return {**mcnemar_exact(yes_a, yes_b), "n": len(both), "both": sum(1 for i in both if a[i] and b[i]),
            "neither": sum(1 for i in both if not a[i] and not b[i]),
            "diff": (yes_a - yes_b) / len(both) if both else None,
            "n_unpaired": len(pool) - len(both)}


# ---------- gold and rankings ----------

def _id_keys(work: Mapping) -> frozenset[str]:
    return frozenset(k for k in work_keys(work) if k.partition(":")[0] in ID_KINDS)


def gold_works(item: Mapping) -> list[dict]:
    """The item's gold works as `{keys, cross_field}`, from its candidates (SCHEMA.md: `gold`, `cross_field`)."""
    return [{"keys": _id_keys(c.get("work") or {}), "cross_field": bool(c.get("cross_field"))}
            for c in item.get("candidates") or [] if c.get("gold")]


def is_failure(row: Mapping) -> bool:
    return bool(row.get("failure")) or row.get("status") != "ok"


def ranked(row: Mapping, k: int = K) -> list[tuple[int, frozenset[str]]]:
    """`(rank_clean, identifier keys)` of the row's kept, non-placeholder candidates up to rank `k`, best first; empty
    for a failure. A row without a `candidates` list falls back to its `top10` identifiers."""
    if is_failure(row):
        return []
    if row.get("candidates") is None and row.get("top10"):
        return [(i, frozenset({canonical_id(w)})) for i, w in enumerate(row["top10"][:k], 1) if w]
    out = []
    for c in row.get("candidates") or []:
        rank = c.get("rank_clean")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank > k or c.get("placeholder") or c.get("leak"):
            continue
        keys = set(_id_keys(c)) | {canonical_id(w) for w in c.get("same_work") or [] if str(w or "").strip()}
        out.append((rank, frozenset(keys)))
    return sorted(out, key=lambda r: r[0])


def _outcomes(ranking: Sequence[tuple[int, frozenset[str]]], works: Sequence[Mapping]) -> dict | None:
    """Success@k, success@1, recall@k and the reciprocal rank of the first gold work, or None without gold works."""
    if not works:
        return None
    best = [min((r for r, keys in ranking if keys & w["keys"]), default=None) for w in works]
    found = [b for b in best if b is not None]
    return {"success": bool(found), "success_1": 1 in found, "recall": len(found) / len(works),
            "rr": 1 / min(found) if found else 0.0}


# ---------- per-item records ----------

def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def item_record(row: Mapping, item: Mapping, k: int = K) -> dict:
    """Everything the aggregates need from one resolved row and its item."""
    ranking, gold = ranked(row, k), gold_works(item)
    cf = _outcomes(ranking, [g for g in gold if g["cross_field"]])
    anyg = _outcomes(ranking, gold)
    true_six = item.get("verdict") or derive_verdict(item)
    if true_six not in VERDICTS:
        raise ValueError(f"item {item.get('id')}: verdict {true_six!r} is not one of {VERDICTS}")
    verdict = row.get("verdict") or {}
    rule = verdict.get("rule")
    if rule == "none":
        outcome = "none_by_design"
    elif is_failure(row):
        outcome = "failure"
    elif verdict.get("class3") in CLASS3 and verdict.get("mapped") is not False:
        outcome = "labelled"
    else:
        outcome = "unmappable"
    probability = row.get("probability")
    if probability is not None and not (isinstance(probability, (int, float)) and 0 <= probability <= 1):
        raise ValueError(f"{row.get('system')} {row.get('item')}: probability {probability!r} is outside [0, 1]")
    leakage = row.get("leakage")
    cost = row.get("cost") or {}
    return {"item": row["item"], "status": row.get("status"), "failure": is_failure(row),
            "n_gold": len(gold), "n_cf_gold": sum(g["cross_field"] for g in gold),
            "cf_success_10": cf["success"] if cf else None, "cf_success_1": cf["success_1"] if cf else None,
            "cf_recall_10": cf["recall"] if cf else None, "cf_rr_10": cf["rr"] if cf else None,
            "any_success_10": anyg["success"] if anyg else None, "any_success_1": anyg["success_1"] if anyg else None,
            "any_recall_10": anyg["recall"] if anyg else None,
            "true_six": true_six, "true_class3": THREE_CLASS[true_six], "rule": rule, "outcome": outcome,
            "pred_six": verdict.get("six") if outcome == "labelled" else None,
            "pred_class3": verdict.get("class3") if outcome == "labelled" else None,
            "probability": None if probability is None else float(probability),
            "leak_hit": bool(leakage and (leakage.get("hit_top10") or leakage.get("hit_evidence"))),
            "n_leak_hits": int((leakage or {}).get("n_hits") or 0), "leakage_known": isinstance(leakage, Mapping),
            "cost_usd": _number(cost.get("usd")), "openalex_usd": _number(cost.get("openalex_usd"))}


# ---------- aggregates over per-item records ----------

def retrieval_block(records: Sequence[Mapping]) -> dict:
    cf = [r for r in records if r["n_cf_gold"]]
    anyg = [r for r in records if r["n_gold"]]
    wins = sum(r["cf_success_10"] for r in cf)
    return {"cf": {"n_eval": len(cf), "n_without_cf_gold": len(records) - len(cf),
                   "miss_at_10": rate(len(cf) - wins, len(cf)), "success_at_10": rate(wins, len(cf)),
                   "recall_at_10": _mean([r["cf_recall_10"] for r in cf]), "mrr_at_10": _mean([r["cf_rr_10"] for r in cf]),
                   "success_at_1": rate(sum(r["cf_success_1"] for r in cf), len(cf))},
            "any": {"n_eval": len(anyg), "n_without_gold": len(records) - len(anyg),
                    "success_at_10": rate(sum(r["any_success_10"] for r in anyg), len(anyg)),
                    "recall_at_10": _mean([r["any_recall_10"] for r in anyg]),
                    "success_at_1": rate(sum(r["any_success_1"] for r in anyg), len(anyg))}}


def verdict_block(records: Sequence[Mapping]) -> dict | None:
    """Three-class metrics, the confusion matrices, already-done precision and the false already-done rate over
    `records`; None when no record carries a verdict (rule `none`, or nothing run)."""
    scored = [r for r in records if r["outcome"] != "none_by_design"]
    if not scored:
        return None
    conf3 = {t: dict.fromkeys(PRED_COLUMNS, 0) for t in CLASS3}
    for r in scored:
        conf3[r["true_class3"]][r["pred_class3"] if r["outcome"] == "labelled" else r["outcome"]] += 1
    classes = {}
    for c in CLASS3:
        tp, support = conf3[c][c], sum(conf3[c].values())
        predicted = sum(conf3[t][c] for t in CLASS3)
        fp, fn = predicted - tp, support - tp
        classes[c] = {"support": support, "predicted": predicted, "tp": tp, "fp": fp, "fn": fn,
                      "precision": tp / predicted if predicted else None, "recall": tp / support if support else None,
                      "f1": 2 * tp / (2 * tp + fp + fn) if support + predicted else None}
    six = None
    if any(r["pred_six"] in VERDICTS for r in scored):
        six = {t: dict.fromkeys(SIX_COLUMNS, 0) for t in VERDICTS}
        for r in scored:
            col = r["outcome"] if r["outcome"] != "labelled" else r["pred_six"] if r["pred_six"] in VERDICTS else "no_six"
            six[r["true_six"]][col] += 1
    weaker = [r for r in scored if r["true_six"] not in ALREADY_DONE]
    called = sum(1 for r in weaker if r["pred_class3"] == "already_done")
    return {"n": len(scored), "n_labelled": sum(r["outcome"] == "labelled" for r in scored),
            "n_unmappable": sum(r["outcome"] == "unmappable" for r in scored),
            "n_failure": sum(r["outcome"] == "failure" for r in scored),
            "n_no_verdict_by_design": len(records) - len(scored),
            "macro_f1": _mean([c["f1"] for c in classes.values() if c["f1"] is not None]), "per_class": classes,
            "confusion_3": conf3, "six_class": six,
            "already_done_precision": rate(classes["already_done"]["tp"], classes["already_done"]["predicted"]),
            "false_already_done": rate(called, len(weaker)),
            "n_weaker_than_specialization_failed_or_unmappable": sum(r["outcome"] != "labelled" for r in weaker)}


def verdict_sensitivity(records: Sequence[Mapping]) -> dict | None:
    """Decision 31: for item-label systems, the verdict metrics again without the items that had a leakage hit."""
    if not any(r["rule"] == "native_item_label" for r in records):
        return None
    kept = [r for r in records if not r["leak_hit"]]
    block = verdict_block(kept)
    if block is None:
        return {"n_dropped": len(records), "macro_f1": None, "already_done_precision": None, "false_already_done": None}
    return {"n_dropped": len(records) - len(kept), **{k: block[k] for k in
            ("n", "macro_f1", "already_done_precision", "false_already_done")}}


def calibration_block(records: Sequence[Mapping], target: str = "label_correct") -> dict | None:
    """Brier score and ECE (10 equal-width bins) of the probabilities of the labelled records; None without any."""
    if target not in CALIBRATION_TARGETS:
        raise ValueError(f"calibration target {target!r} is not one of {CALIBRATION_TARGETS}")
    labelled = [r for r in records if r["outcome"] == "labelled"]
    points = [(r["probability"], int(r["pred_class3"] == r["true_class3"] if target == "label_correct"
                                     else r["true_class3"] == "already_done"))
              for r in labelled if r["probability"] is not None]
    if not points:
        return None
    bins = [[] for _ in range(ECE_BINS)]
    for p, y in points:
        bins[min(math.floor(p * ECE_BINS + 1e-12), ECE_BINS - 1)].append((p, y))
    table = [{"lo": i / ECE_BINS, "hi": (i + 1) / ECE_BINS, "n": len(b),
              "mean_probability": _mean([p for p, _ in b]), "accuracy": _mean([y for _, y in b])}
             for i, b in enumerate(bins)]
    return {"target": target, "n": len(points), "n_labelled_without_probability": len(labelled) - len(points),
            "brier": _mean([(p - y) ** 2 for p, y in points]),
            "ece": sum(len(b) / len(points) * abs(_mean([y for _, y in b]) - _mean([p for p, _ in b]))
                       for b in bins if b),
            "bins": table}


def leakage_block(records: Sequence[Mapping]) -> dict:
    hits = sum(r["leak_hit"] for r in records)
    return {"rate": rate(hits, len(records)), "hits_per_item": _mean([r["n_leak_hits"] for r in records]),
            "n_leakage_unknown": sum(not r["leakage_known"] for r in records)}


def cost_block(records: Sequence[Mapping]) -> dict:
    costs = [r["cost_usd"] for r in records if r["cost_usd"] is not None]
    nominal = [r["openalex_usd"] for r in records if r["openalex_usd"] is not None]
    return {"n_costed": len(costs), "usd_total": sum(costs) if costs else None, "usd_per_item": _mean(costs),
            "usd_max_item": max(costs) if costs else None, "openalex_usd_per_item": _mean(nominal)}


def status_block(records: Sequence[Mapping]) -> dict:
    return {"status_counts": dict(sorted(Counter(r["status"] for r in records).items())),
            "n_failures": sum(r["failure"] for r in records),
            "n_pending_rerun": sum(r["status"] in INFRA for r in records)}


# ---------- subsets ----------

def is_h_family(name: str) -> bool:
    return bool(H_FAMILY_RE.match(name))


def descriptive_reasons(name: str, n_run: int) -> list[str]:
    """Why a subset is descriptive only (decision 23): the H family and the recall strata at any size, any subset
    with fewer than 20 items run."""
    out = []
    if is_h_family(name) or name == "R_recalled":
        out.append("h_family_or_recall_stratum")
    if n_run < MIN_ITEMS:
        out.append("under_20_items")
    return out


def subset_order(names: Iterable[str]) -> list[str]:
    """H first, the other H variants next, then the rest in the order of SUBSET_ORDER, unknown names last."""
    def key(name: str) -> tuple:
        return (0 if is_h_family(name) else 1, SUBSET_ORDER.index(name) if name in SUBSET_ORDER else len(SUBSET_ORDER),
                name)
    return sorted(set(names), key=key)


def subsets_of(splits: Mapping) -> dict[str, list[str]]:
    """The splits file's subsets (docs/design/harness.md 8.3) as sorted ID lists, with `R_recalled` derived as R minus
    H when R and H are given and it is not."""
    subsets = {name: sorted(set(ids)) for name, ids in (splits.get("subsets") or {}).items()}
    if "R" in subsets and "H" in subsets and "R_recalled" not in subsets:
        subsets["R_recalled"] = sorted(set(subsets["R"]) - set(subsets["H"]))
    return subsets


def verdict_members(name: str, members: Iterable[str], subsets: Mapping[str, Sequence[str]]) -> set[str]:
    """The verdict set of a subset: R-family subsets get N added ("R plus N", decision 5)."""
    members = set(members)
    negatives = set(subsets.get("N") or ())
    if not members or not negatives or name == "N":
        return members
    family = members <= set(subsets["R"]) if "R" in subsets else name not in NOT_R_FAMILY
    return members | negatives if family else members


def composition(subsets: Mapping[str, Sequence[str]]) -> dict:
    """Subset sizes. For every H-family subset and the recall strata, k of n against R (k its size, n the size of
    R) with a Wilson 95% interval: the `H: k of n` line that opens every headline table (section 1)."""
    out = {}
    for name in subset_order(subsets):
        entry = {"n": len(subsets[name]), "descriptive_reasons": descriptive_reasons(name, len(subsets[name]))}
        if "R" in subsets and name != "R" and (is_h_family(name) or name == "R_recalled"):
            entry["of_R"] = rate(len(subsets[name]), len(subsets["R"]))
        out[name] = entry
    return out


def score_system(rows: Sequence[Mapping], items: Mapping[str, Mapping], subsets: Mapping[str, Sequence[str]], *,
                 calibration_target: str = "label_correct", k: int = K) -> dict:
    """The scores of one system and condition: `rows` are its resolved rows (one per item), `items` the benchmark
    items by ID, `subsets` the splits file's subsets (see `subsets_of`)."""
    records: dict[str, dict] = {}
    for row in rows:
        if row["item"] in records:
            raise ValueError(f"{row.get('system')} answered {row['item']} twice")
        if row["item"] not in items:
            raise KeyError(f"item {row['item']} of {row.get('system')} is not in the items file")
        records[row["item"]] = item_record(row, items[row["item"]], k)
    out = {}
    for name in subset_order(subsets):
        run_ids = [i for i in subsets[name] if i in records]
        if not run_ids:
            continue
        recs = [records[i] for i in run_ids]
        vset = [records[i] for i in sorted(verdict_members(name, subsets[name], subsets)) if i in records]
        reasons = descriptive_reasons(name, len(recs))
        out[name] = {"n_subset": len(subsets[name]), "n_run": len(recs), "n_not_run": len(subsets[name]) - len(recs),
                     "descriptive_only": bool(reasons), "descriptive_reasons": reasons, **status_block(recs),
                     "retrieval": retrieval_block(recs), "verdict": verdict_block(vset),
                     "verdict_n": len(vset), "verdict_sensitivity_no_leak": verdict_sensitivity(vset),
                     "calibration": calibration_block(vset, calibration_target), "leakage": leakage_block(recs),
                     "cost": cost_block(recs)}
    return {"subsets": out, "per_item": {i: records[i] for i in sorted(records)}}


def group_rows(rows: Iterable[Mapping]) -> dict[tuple[str, str], list[Mapping]]:
    """Resolved rows by (system, condition). Refuses an item answered twice (a failure is never resampled, decision
    33) and rows of one system and condition made under different config hashes."""
    groups: dict[tuple[str, str], list[Mapping]] = {}
    for row in rows:
        groups.setdefault((row["system"], row.get("condition") or "text"), []).append(row)
    for (system, condition), group in groups.items():
        seen: dict[str, str] = {}
        for row in group:
            if row["item"] in seen:
                raise ValueError(f"{system} ({condition}) answered {row['item']} twice, in {seen[row['item']]} and "
                                 f"{row.get('run_id')}")
            seen[row["item"]] = str(row.get("run_id"))
        hashes = {row.get("config_sha256") for row in group}
        if len(hashes) > 1:
            raise ValueError(f"{system} ({condition}) was run under different configs: {sorted(map(str, hashes))}")
    return groups


def score(rows: Iterable[Mapping], items: Mapping[str, Mapping], splits: Mapping, *,
          calibration_target: str = "label_correct") -> dict:
    """The scores of every system and condition in `rows` (the content of scores.json, without its `inputs`)."""
    subsets = subsets_of(splits)
    systems = []
    for (system, condition), group in sorted(group_rows(rows).items()):
        scored = score_system(group, items, subsets, calibration_target=calibration_target)
        systems.append({"system": system, "condition": condition,
                        "label": system if condition == "text" else f"{system} ({condition})",
                        "config_sha256": sorted({str(r.get("config_sha256")) for r in group}),
                        "run_ids": sorted({str(r.get("run_id")) for r in group}), **scored})
    return {"format": FORMAT,
            "parameters": {"k": K, "ece_bins": ECE_BINS, "min_items": MIN_ITEMS,
                           "calibration_target": calibration_target},
            "subset_order": subset_order(subsets), "subsets": composition(subsets), "systems": systems}


def paired_outcomes(record_a: Mapping, record_b: Mapping, items: Iterable[str] | None = None,
                    metric: str = "cf_success_10") -> dict:
    """McNemar between two entries of `scores["systems"]` on a per-item binary metric (default: cross-field
    success@10), over `items` (a subset's members) or all items both ran."""
    def pick(rec: Mapping) -> dict:
        return {i: r[metric] for i, r in rec["per_item"].items()}
    return mcnemar_paired(pick(record_a), pick(record_b), items)


# ---------- files ----------

def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def _openalex_usd(nominal: Any) -> float | None:
    if not isinstance(nominal, Mapping):
        return None
    if isinstance(nominal.get("usd"), (int, float)):
        return float(nominal["usd"])
    return sum(int(nominal.get(kind) or 0) * price for kind, price in PRICE_USD.items())


def run_costs(run_dir: Path) -> dict[str, dict]:
    """`{item: {"usd", "openalex_usd"}}` of a run: the last outputs.jsonl row per item (`cost.usd`), else the last
    ledger.jsonl row (`cost_usd`)."""
    costs: dict[str, dict] = {}
    for row in read_jsonl(Path(run_dir) / "ledger.jsonl"):
        costs[row["item"]] = {"usd": row.get("cost_usd"), "openalex_usd": None}
    for row in read_jsonl(Path(run_dir) / "outputs.jsonl"):
        cost = row.get("cost") or {}
        costs[row["item"]] = {"usd": cost.get("usd"), "openalex_usd": _openalex_usd(cost.get("openalex_nominal"))}
    return costs


def read_run(run_dir: Path | str) -> list[dict]:
    """The resolved rows of a run directory with `cost` attached from its outputs or ledger. Refuses a run whose
    run.json says it is not a scored run, and a directory without resolved.jsonl."""
    run_dir = Path(run_dir)
    manifest = run_dir / "run.json"
    if manifest.exists():
        kind = json.loads(manifest.read_text(encoding="utf-8")).get("kind")
        if kind != "scored":
            raise ValueError(f"{run_dir}: run kind {kind!r}; only scored runs are scored, pilots only give costs")
    if not (run_dir / "resolved.jsonl").exists():
        raise FileNotFoundError(f"{run_dir}: no resolved.jsonl; run resolve first")
    costs = run_costs(run_dir)
    return [{**row, "cost": row.get("cost") or costs.get(row["item"])} for row in read_jsonl(run_dir / "resolved.jsonl")]


def score_runs(run_dirs: Sequence[Path | str], items: Mapping[str, Mapping], splits: Mapping, *,
               calibration_target: str = "label_correct") -> dict:
    rows = [row for d in run_dirs for row in read_run(d)]
    return score(rows, items, splits, calibration_target=calibration_target)


# ---------- the Table 7 rows ----------

def _f(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _rate(r: Mapping | None) -> str:
    return "n/a" if not r or r["rate"] is None else f"{r['rate']:.3f} [{r['wilson95'][0]:.3f}, {r['wilson95'][1]:.3f}]"


def table7(scores: Mapping) -> str:
    """The markdown table of Table 7 rows: one row per subset and system, H first. Miss rate is 1 minus cross-field
    success@10 with its Wilson 95% interval; recall, MRR and the verdict columns are descriptive point estimates."""
    lines = ["# Table 7, measurement A", ""]
    for name, entry in scores["subsets"].items():
        if "of_R" in entry:
            r = entry["of_R"]
            lines.append(f"{name}: {r['k']} of {r['n']} items of R, share {_rate(r)}"
                         f"{' (descriptive only)' if entry['descriptive_reasons'] else ''}.")
    lines += ["", "| system | subset | misses (k of n) | cross-field miss rate [Wilson 95%] | cross-field recall@10 "
              "| MRR@10 | macro-F1 | already-done precision | leakage rate | $ per item | note |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in scores["subset_order"]:
        for sys_ in sorted(scores["systems"], key=lambda s: (s["system"], s["condition"])):
            b = sys_["subsets"].get(name)
            if b is None:
                continue
            cf, v = b["retrieval"]["cf"], b["verdict"]
            notes = (["descriptive only"] if b["descriptive_only"] else [])
            if b["n_not_run"]:
                notes.append(f"run {b['n_run']} of {b['n_subset']}")
            if b["n_failures"]:
                notes.append(f"{b['n_failures']} failed")
            if v and v["n_unmappable"]:
                notes.append(f"{v['n_unmappable']} unmappable")
            if b["n_pending_rerun"]:
                notes.append(f"{b['n_pending_rerun']} due for re-run")
            miss = cf["miss_at_10"]
            lines.append("| " + " | ".join([
                sys_["label"], name, f"{miss['k']} of {miss['n']}" if miss["n"] else "n/a", _rate(miss),
                _f(cf["recall_at_10"]), _f(cf["mrr_at_10"]), _f(v["macro_f1"]) if v else "n/a",
                _rate(v["already_done_precision"]) if v else "n/a", _rate(b["leakage"]["rate"]),
                _f(b["cost"]["usd_per_item"], 4), "; ".join(notes)]) + " |")
    lines += ["", "Failures (a cap, turn limit, timeout, unparsed output, a repeated decline, or a row due for a re-run) "
              "count as items with no output: a miss, and a false negative for the verdict. Subsets under 20 items "
              "and the H family are descriptive.", ""]
    return "\n".join(lines)


def write_outputs(scores: dict, out: Path | str) -> tuple[Path, Path]:
    out = Path(out)
    json_path = out if out.suffix == ".json" else out / "scores.json"
    md_path = json_path.with_suffix(".md") if out.suffix == ".json" else out / "table7.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(scores, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(table7(scores), encoding="utf-8")
    return json_path, md_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.harness.score", description=__doc__.split("\n")[0])
    parser.add_argument("run_dirs", nargs="+", type=Path, metavar="RUN_DIR")
    parser.add_argument("--splits", required=True, type=Path)
    parser.add_argument("--items", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="a directory, or a .json path (table beside it)")
    parser.add_argument("--calibration-target", choices=CALIBRATION_TARGETS, default="label_correct")
    args = parser.parse_args(argv)
    splits = json.loads(args.splits.read_text(encoding="utf-8"))
    items = {i["id"]: i for i in load_items(args.items)}
    scores = score_runs(args.run_dirs, items, splits, calibration_target=args.calibration_target)
    scores["inputs"] = {"splits": {"path": _rel(args.splits), "sha256": _sha256(args.splits)},
                        "items": {"path": _rel(args.items), "sha256": _sha256(args.items)},
                        "run_dirs": [_rel(d) for d in args.run_dirs]}
    json_path, md_path = write_outputs(scores, args.out)
    print(f"{len(scores['systems'])} systems scored; wrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
