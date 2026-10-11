"""Inter-annotator agreement on independent first readings (eval/protocol.md section 11 and decision 18).

    PYTHONPATH=src python -m coelacanth.benchmark.agreement <batch.json> <labels-1.json> <labels-2.json> [...]
        [--batch <another-batch.json> ...] [--out report.json] [--items cf-0001,cf-0002] [--require-all]
        [--seed N] [--resamples 2000] [--ignore-problems]
    PYTHONPATH=src python -m coelacanth.benchmark.agreement <batch.json> <first-export.json> <later-export.json>
        --changes

Agreement is computed on the items at least two of the annotators marked done (with two annotators, the items both
did; with more, as `coelacanth.benchmark.assign` gives each item to a rotating pair, every item enters with the
readings it has and the others missing; `--require-all` keeps only items every annotator did; `--items` narrows the
set further), for the four distinctions of the protocol:

- `verdict`: the six-way item verdict (items.derive_verdict over the packet's and the annotator's added candidates);
- `already_done`: the verdict is `equivalent` or `specialization`, or not;
- `gold`: per packet candidate, the relation is `similar_mechanism` or stronger, or not;
- `cross_field`: per packet candidate, the cross-field flag, counted only from annotators who rated it gold (so with
  two annotators, on the candidates both rated gold).

Added candidates are each annotator's own finds and are judged by one annotator only, so they enter the verdict but
not the per-candidate distinctions; the report counts the works both annotators added.

For each distinction: Krippendorff's alpha, nominal, for any number of coders with missing values (Krippendorff 2011,
"Computing Krippendorff's Alpha-Reliability", sections A to C, cached as cache/fulltext/method-krippendorff-2011.txt;
the tests reproduce its three worked examples), with a percentile bootstrap 95% interval over items (seeded, 2,000
resamples by default; an item's candidates are resampled together, so candidates of one claim are not treated as
independent) and the share of resamples in which alpha falls below 0.667 and below 0.800 (Hayes and Krippendorff
2007 report this probability, q, from a bootstrap over pairable values; ours resamples items, as the protocol states).
Alongside: Cohen's kappa and raw agreement for each pair of annotators, the confusion matrix of each pair, and whether
the distinction passes its protocol threshold (alpha at least 0.80 for already done and for gold, at least 0.667 for
the six-way verdict; the cross-field flag has none and is descriptive). Alpha is undefined (null) when the pairable
values show no variation at all; a resample where it is undefined is counted and left out of the interval.

The labels files are checked first (annotation.labels_problems) against the copy each was made on: the first of
`assigned/<batch>-<id>.json` next to the batch (coelacanth.benchmark.assign), `<batch>-<id>.json` next to the batch
(decision 25's crossover) and the batch itself that names that annotator or none and passes the check; when none
passes, the first that exists is reported. Any problem stops the command unless --ignore-problems.
The protocol counts independent first readings only: run this on the files as the annotators first exported them,
never after discussion. The report records each input file's SHA-256 so the measured files can be traced.

Pooled over batches (`--batch`, once per further batch): the final reliability covers every item with two readings
(section 11), across b01, b02 and later batches. Each labels file goes with the batch its `batch` field names; the
items of all batches are measured together (their coincidences summed, one bootstrap over all items), an annotator who
labelled several batches is one coder, and item ids must not repeat across batches. Alphas of separate batches are
never averaged.

`--changes` compares two exports of one annotator for one batch, typically the first export and a re-export after a
form fix (an empty field, a quote that was not verbatim), on the decision-critical fields of decision 4: the item
verdict, gold or not per packet candidate, and for gold the relation and the cross-field flag; added candidates are
matched by identifier and compared the same way, and an item marked done in only one of the two files is listed. It
prints both files' SHA-256 (for docs/decisions.md) and every change, and exits 1 when anything critical changed: then
agreement is also reported on the first export (with --ignore-problems) as a sensitivity line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Hashable, Iterable, Mapping, Sequence

from ..env import REPO_ROOT
from .annotation import ANNOTATOR_RE, is_bare, labels_problems, work_ids
from .items import ALREADY_DONE, PRECEDENCE, RELATIONS, VERDICTS, derive_verdict

REPORT_FORMAT = "coelacanth-agreement/v1"
RESAMPLES = 2000
SEED = 20261012
CHECK_THRESHOLDS = (0.667, 0.800)  # the protocol asks for P(alpha < 0.667) and P(alpha < 0.800)
RESULTS_DIR = REPO_ROOT / "eval" / "results" / "agreement"
DISTINCTIONS = {
    "verdict": {"label": "six-way item verdict", "unit": "item", "categories": list(VERDICTS),
                "threshold": 0.667},
    "already_done": {"label": "already done (equivalent or specialization) or not", "unit": "item",
                     "categories": ["already_done", "not_already_done"], "threshold": 0.800},
    "gold": {"label": "gold (similar_mechanism or stronger) or not", "unit": "packet candidate",
             "categories": ["gold", "not_gold"], "threshold": 0.800},
    "cross_field": {"label": "cross-field flag on candidates rated gold", "unit": "packet candidate rated gold",
                    "categories": ["cross_field", "same_field"], "threshold": None},
}
SHORT_NAMES = {"verdict": "six-way verdict", "already_done": "already done", "gold": "gold",
               "cross_field": "cross-field on gold"}


# Krippendorff's alpha, nominal (Krippendorff 2011, sections B and C).

def coincidences(units: Iterable[Sequence[Hashable | None]]) -> Counter:
    """The coincidence matrix {(c, k): o_ck} of nominal reliability data, exactly (fractions). Each unit lists one
    value per coder, None where the coder gave none; a unit with m_u >= 2 values adds 1/(m_u - 1) for each ordered
    pair of values from different coders; a unit with fewer than two values cannot be paired and adds nothing."""
    o: Counter = Counter()
    for unit in units:
        values = [v for v in unit if v is not None]
        m = len(values)
        if m < 2:
            continue
        weight = Fraction(1, m - 1)
        for i, c in enumerate(values):
            for j, k in enumerate(values):
                if i != j:
                    o[(c, k)] += weight
    return o


def alpha_from(o: Mapping[tuple, float | Fraction]) -> float | None:
    """Nominal alpha from a coincidence matrix: 1 - (n - 1) * (n - sum o_cc) / (n^2 - sum n_c^2). None (undefined)
    when fewer than two values are pairable or every pairable value is the same category."""
    n = sum(o.values())
    if n < 2:
        return None
    totals: dict = defaultdict(int)
    matches = 0
    for (c, k), v in o.items():
        totals[c] += v
        if c == k:
            matches += v
    expected = n * n - sum(t * t for t in totals.values())
    if expected <= 1e-12 * n * n:
        return None
    return float(1 - (n - 1) * (n - matches) / expected)


def krippendorff_alpha(units: Iterable[Sequence[Hashable | None]]) -> float | None:
    """Krippendorff's alpha for nominal data, any number of coders, missing values allowed (None)."""
    return alpha_from(coincidences(units))


def cohen_kappa(pairs: Iterable[tuple[Hashable | None, Hashable | None]]) -> float | None:
    """Cohen's kappa for two coders over the units both valued: (p_o - p_e) / (1 - p_e), with p_e from each coder's
    own marginals. None when no unit was valued by both or p_e is 1 (both used one and the same category)."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if not n:
        return None
    observed = Fraction(sum(a == b for a, b in pairs), n)
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = Fraction(sum(first[c] * second[c] for c in first), n * n)
    if expected == 1:
        return None
    return float((observed - expected) / (1 - expected))


def quantile(values: Sequence[float], q: float) -> float:
    """The q-quantile of sorted values, linear between order statistics (numpy's default)."""
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def resample_indices(n_items: int, resamples: int = RESAMPLES, seed: int = SEED) -> list[list[int]]:
    """The bootstrap's item draws, the same for every distinction (one seeded generator)."""
    rng = random.Random(seed)
    return [[rng.randrange(n_items) for _ in range(n_items)] for _ in range(resamples)]


def bootstrap(per_item: Sequence[Mapping], draws: Sequence[Sequence[int]]) -> list[float | None]:
    """Alpha for each bootstrap resample of items: the coincidence matrices of the drawn items are summed (an item
    drawn twice counts twice) and alpha is computed on the sum."""
    floats = [{key: float(v) for key, v in o.items()} for o in per_item]
    out = []
    for draw in draws:
        total: dict = defaultdict(float)
        for index in draw:
            for key, v in floats[index].items():
                total[key] += v
        out.append(alpha_from(total))
    return out


# Reading the inputs.

@dataclass
class Reading:
    """One annotator's labels file, with the copy of the batch it was made on and the problems check-labels finds."""
    annotator: str
    labels_path: Path
    labels: dict
    batch_path: Path
    batch: dict
    problems: list[str] = field(default_factory=list)

    def done(self) -> dict[str, dict]:
        """The labelled items marked done, by id."""
        return {item["id"]: item for item in self.labels.get("items") or []
                if isinstance(item, Mapping) and item.get("status") == "done" and isinstance(item.get("id"), str)}


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copy_candidates(batch_path: Path, batch: Mapping, annotator) -> list[tuple[Path, dict]]:
    """The copies of the batch an annotator's labels may have been made on, in the order they are tried: the
    annotator's copy in `assigned/` next to the batch (coelacanth.benchmark.assign), the one next to the batch
    (annotation.crossover), then the batch itself when it names that annotator or none. A copy counts only when it is
    of the same batch and names that annotator."""
    name = batch.get("batch")
    batch_path = Path(batch_path)
    out = []
    if isinstance(annotator, str) and ANNOTATOR_RE.fullmatch(annotator) and isinstance(name, str) and name:
        for path in (batch_path.parent / "assigned" / f"{name}-{annotator}.json",
                     batch_path.with_name(f"{name}-{annotator}.json")):
            if path.exists() and path.resolve() != batch_path.resolve():
                data = read_json(path)
                if data.get("batch") == name and data.get("annotator") == annotator:
                    out.append((path, data))
    if batch.get("annotator") in (None, annotator):
        out.append((batch_path, dict(batch)))
    return out


def batch_copy(batch_path: Path, batch: Mapping, labels: Mapping) -> tuple[Path, dict, list[str]]:
    """The copy of the batch a labels file was made on (copy_candidates: the first that passes labels_problems, else
    the first there is, else the batch) and the problems of the labels against it."""
    candidates = copy_candidates(batch_path, batch, labels.get("annotator")) or [(Path(batch_path), dict(batch))]
    checked = []
    for path, data in candidates:
        problems = labels_problems(data, labels)
        if not problems:
            return path, data, []
        checked.append((path, data, problems))
    return checked[0]


def load_readings(batch_path: Path, labels_paths: Sequence[Path]) -> tuple[dict, list[Reading], list[str]]:
    """The batch, one Reading per labels file (each checked against its own copy) and the file-level problems."""
    batch = read_json(batch_path)
    readings, problems = [], []
    for path in labels_paths:
        labels = read_json(path)
        copy_path, copy_batch, found = batch_copy(batch_path, batch, labels)
        reading = Reading(str(labels.get("annotator")), Path(path), labels, copy_path, copy_batch)
        reading.problems = [f"{Path(path).name} (checked against {copy_path.name}): {p}" for p in found]
        readings.append(reading)
    if len(readings) < 2:
        problems.append("agreement needs at least two labels files")
    seen = [r.annotator for r in readings]
    repeated = sorted({a for a in seen if seen.count(a) > 1})
    if repeated:
        problems.append(f"the same annotator appears in more than one labels file: {repeated}")
    return batch, readings, problems


@dataclass
class Group:
    """One batch of a pooled measurement with the readings made on it."""
    batch_path: Path
    batch: dict
    readings: list[Reading]


def load_pooled(batch_paths: Sequence[Path], labels_paths: Sequence[Path]) -> tuple[list[Group], list[str]]:
    """One Group per batch, each labels file with the batch its `batch` field names (checked against its own copy, as
    in load_readings), and the file-level problems: a labels file for none of the batches, a batch given twice, an
    annotator twice on one batch, an item id in two batches, fewer than two readings in all."""
    groups: list[Group] = []
    problems: list[str] = []
    by_name: dict[str, Group] = {}
    for path in batch_paths:
        batch = read_json(path)
        name = batch.get("batch")
        if name in by_name:
            problems.append(f"batch {name!r} is given twice ({by_name[name].batch_path} and {path})")
            continue
        group = Group(Path(path), batch, [])
        by_name[name] = group
        groups.append(group)
    for path in labels_paths:
        labels = read_json(path)
        group = by_name.get(labels.get("batch"))
        if group is None:
            problems.append(f"{Path(path).name} holds labels for batch {labels.get('batch')!r}, which is not among "
                            f"the batches given ({', '.join(map(repr, by_name))})")
            continue
        copy_path, copy_batch, found = batch_copy(group.batch_path, group.batch, labels)
        reading = Reading(str(labels.get("annotator")), Path(path), labels, copy_path, copy_batch)
        reading.problems = [f"{Path(path).name} (checked against {copy_path.name}): {p}" for p in found]
        group.readings.append(reading)
    if sum(len(g.readings) for g in groups) < 2:
        problems.append("agreement needs at least two labels files")
    owner: dict[str, str] = {}
    for group in groups:
        seen = [r.annotator for r in group.readings]
        repeated = sorted({a for a in seen if seen.count(a) > 1})
        if repeated:
            problems.append(f"the same annotator appears in more than one labels file of batch "
                            f"{group.batch.get('batch')!r}: {repeated}")
        for packet in group.batch.get("items") or []:
            item_id = packet.get("id") if isinstance(packet, Mapping) else None
            if item_id in owner:
                problems.append(f"item {item_id} is in batch {owner[item_id]!r} and in batch "
                                f"{group.batch.get('batch')!r}: pooled batches must not share items")
            elif isinstance(item_id, str):
                owner[item_id] = group.batch.get("batch")
    return groups, problems


# The four distinctions.

def _all_candidates(item: Mapping) -> list:
    cands = item.get("candidates") if isinstance(item.get("candidates"), list) else []
    added = item.get("added_candidates") if isinstance(item.get("added_candidates"), list) else []
    return [c for c in cands + added if isinstance(c, Mapping)]


def item_verdict(item: Mapping) -> str:
    """The item verdict an annotator's labels imply: precedence over the packet's and the added candidates."""
    return derive_verdict({"candidates": _all_candidates(item)})


def _gold_value(cand: Mapping | None) -> str | None:
    relation = (cand or {}).get("relation")
    if relation not in RELATIONS:
        return None
    return "gold" if relation in PRECEDENCE else "not_gold"


def _cross_field_value(cand: Mapping | None) -> str | None:
    cand = cand or {}
    if cand.get("relation") not in PRECEDENCE or not isinstance(cand.get("cross_field"), bool):
        return None
    return "cross_field" if cand["cross_field"] else "same_field"


def item_units(packet: Mapping, labelled: Sequence[Mapping | None]) -> dict[str, list[list]]:
    """The units one item contributes to each distinction; each unit lists one value per coder (None for none)."""
    verdicts = [item_verdict(item) if item else None for item in labelled]
    units = {"verdict": [verdicts],
             "already_done": [[None if v is None else ("already_done" if v in ALREADY_DONE else "not_already_done")
                               for v in verdicts]],
             "gold": [], "cross_field": []}
    for pc in packet.get("candidates") or []:
        cid = pc.get("cid")
        cands = [next((c for c in (item or {}).get("candidates") or [] if isinstance(c, Mapping)
                       and c.get("cid") == cid), None) for item in labelled]
        units["gold"].append([_gold_value(c) for c in cands])
        units["cross_field"].append([_cross_field_value(c) for c in cands])
    return units


def _pairs(units: Iterable[Sequence], i: int, j: int) -> list[tuple]:
    return [(u[i], u[j]) for u in units if u[i] is not None and u[j] is not None]


def summarise(name: str, per_item_units: Sequence[Sequence[Sequence]], coders: Sequence[str],
              draws: Sequence[Sequence[int]]) -> dict:
    """Every statistic of one distinction, from its units grouped by item."""
    spec = DISTINCTIONS[name]
    units = [u for item_units_ in per_item_units for u in item_units_]
    per_item = [coincidences(item_units_) for item_units_ in per_item_units]
    o = coincidences(units)
    alpha = alpha_from(o)
    n_values = sum(o.values())
    matches = sum(v for (c, k), v in o.items() if c == k)
    boot = bootstrap(per_item, draws) if per_item else []
    defined = sorted(a for a in boot if a is not None)
    categories = list(spec["categories"])
    used = {c for unit in units for c in unit if c is not None}
    categories += sorted(str(c) for c in used if c not in categories)
    pairs = []
    for i, j in combinations(range(len(coders)), 2):
        both = _pairs(units, i, j)
        matrix = [[sum(1 for a, b in both if a == row and b == col) for col in categories] for row in categories]
        pairs.append({"coders": [coders[i], coders[j]], "n_units": len(both),
                      "raw_agreement": (sum(a == b for a, b in both) / len(both)) if both else None,
                      "kappa": cohen_kappa(both),
                      "confusion": {"rows": coders[i], "columns": coders[j], "categories": categories,
                                    "matrix": matrix}})
    threshold = spec["threshold"]
    if threshold is None:
        result = "descriptive"
    elif alpha is None:
        result = "undefined"
    else:
        result = "pass" if alpha >= threshold else "fail"
    return {
        "label": spec["label"], "unit": spec["unit"], "categories": categories, "threshold": threshold,
        "n_units": sum(1 for u in units if sum(v is not None for v in u) >= 2),
        "n_values": int(n_values) if n_values == int(n_values) else float(n_values),
        "alpha": alpha,
        "ci95": [quantile(defined, 0.025), quantile(defined, 0.975)] if defined else None,
        "p_below": {f"{t:.3f}": (sum(a < t for a in defined) / len(defined)) if defined else None
                    for t in CHECK_THRESHOLDS},
        "bootstrap": {"defined": len(defined), "undefined": len(boot) - len(defined)},
        "observed_agreement": float(matches / n_values) if n_values else None,
        "category_counts": {str(c): float(sum(v for (a, _), v in o.items() if a == c)) for c in categories},
        "pairs": pairs, "result": result}


def measure(batch: Mapping, readings: Sequence[Reading], item_ids: Sequence[str] | None = None,
            resamples: int = RESAMPLES, seed: int = SEED, require_all: bool = False) -> dict:
    """The agreement report (without the input record) for the items at least two readings marked done (every
    reading, with `require_all`)."""
    return measure_pooled([(batch, readings)], item_ids, resamples, seed, require_all)


def measure_pooled(groups: Sequence[tuple[Mapping, Sequence[Reading]]], item_ids: Sequence[str] | None = None,
                   resamples: int = RESAMPLES, seed: int = SEED, require_all: bool = False) -> dict:
    """The agreement report over the items of one or more batches, each given with the readings made on it: the
    items at least two readings marked done (every reading of the item's batch, with `require_all`), measured
    together. An annotator who labelled several batches is one coder; where a coder has no reading of an item, the
    value is missing. Item ids are assumed unique across the batches (load_pooled checks it)."""
    coders: list[str] = []
    for _, readings in groups:
        for r in readings:
            if r.annotator not in coders:
                coders.append(r.annotator)
    wanted = set(item_ids) if item_ids else None
    used, excluded, all_ids = [], {}, set()
    grouped = {name: [] for name in DISTINCTIONS}
    for batch, readings in groups:
        done = {r.annotator: r.done() for r in readings}
        for packet in batch.get("items") or []:
            item_id = packet.get("id")
            all_ids.add(item_id)
            if wanted is not None and item_id not in wanted:
                continue
            have = [c for c in coders if item_id in done.get(c, {})]
            if len(have) < (len(readings) if require_all else 2):
                excluded[item_id] = f"done by {', '.join(have)} only" if have else "done by none"
                continue
            used.append(item_id)
            units = item_units(packet, [done.get(c, {}).get(item_id) for c in coders])
            for name in DISTINCTIONS:
                grouped[name].append(units[name])
    unknown = sorted(wanted - all_ids) if wanted is not None else []
    draws = resample_indices(len(used), resamples, seed) if used else []
    distinctions = {name: summarise(name, grouped[name], coders, draws) for name in DISTINCTIONS}
    gated = [d["result"] for d in distinctions.values() if d["threshold"] is not None]
    decision = "fail" if "fail" in gated else ("undefined" if "undefined" in gated else "proceed")
    pooled = len(groups) > 1
    rule = ("done by every annotator" + (" of its batch" if pooled else "")) if require_all else \
        "done by at least two annotators"
    added = [added_overlap(readings, used) for _, readings in groups]
    return {"coders": coders, "batches": [batch.get("batch") for batch, _ in groups],
            "items": {"used": used, "excluded": excluded, "unknown": unknown, "rule": rule},
            "bootstrap": {"resamples": resamples, "seed": seed, "unit": "item",
                          "interval": "percentile, 95%"},
            "distinctions": distinctions, "decision": decision,
            "added_candidates": {"added": {c: sum(a["added"].get(c, 0) for a in added) for c in coders
                                           if any(c in a["added"] for a in added)},
                                 "added_by_two": sum(a["added_by_two"] for a in added)}}


def added_overlap(readings: Sequence[Reading], item_ids: Sequence[str]) -> dict:
    """Descriptive: how many works each annotator added on the measured items and how many of them another annotator
    added too (the same identifier)."""
    per = {}
    ids_by = {}
    for r in readings:
        done = r.done()
        keys = []
        for item_id in item_ids:
            for cand in (done.get(item_id) or {}).get("added_candidates") or []:
                if isinstance(cand, Mapping) and isinstance(cand.get("work"), Mapping):
                    keys.append((item_id, frozenset(work_ids(cand["work"]))))
        ids_by[r.annotator] = keys
        per[r.annotator] = len(keys)
    shared = 0
    names = list(ids_by)
    for a, b in combinations(names, 2):
        for item_id, ids in ids_by[a]:
            if any(item_id == other_id and ids & other for other_id, other in ids_by[b]):
                shared += 1
    return {"added": per, "added_by_two": shared}


# Two exports of one annotator: did a decision-critical field change?

def _critical_value(cand: Mapping | None) -> tuple:
    """Decision 4's critical fields of one candidate: gold or not; for gold, the relation and the cross-field flag.
    None when the candidate has no judged relation."""
    relation = (cand or {}).get("relation")
    if relation not in RELATIONS:
        return (None,)
    gold = relation in PRECEDENCE
    return (gold, relation if gold else None, cand.get("cross_field") if gold else None)


def _show_critical(value: tuple) -> str:
    if value[0] is None:
        return "no relation"
    if not value[0]:
        return "not gold"
    flag = {True: "cross-field", False: "same field"}.get(value[2], "cross-field not set")
    return f"gold ({value[1]}, {flag})"


def critical_changes(packets: Sequence[Mapping], first: Mapping, later: Mapping) -> list[str]:
    """What changed between two labels files of one annotator on the decision-critical fields: each item's verdict
    and done status, gold, relation and cross-field per packet candidate, and the same for added candidates matched
    by identifier (a work added in only one file counts as a change when it is gold there)."""
    def by_id(labels: Mapping) -> dict:
        return {i.get("id"): i for i in labels.get("items") or [] if isinstance(i, Mapping)}

    one, two = by_id(first), by_id(later)
    out = []
    for packet in packets:
        item_id = packet.get("id")
        a, b = one.get(item_id), two.get(item_id)
        done_a, done_b = bool(a) and a.get("status") == "done", bool(b) and b.get("status") == "done"
        if done_a != done_b:
            out.append(f"{item_id}: marked done only in the {'first' if done_a else 'later'} file")
            continue
        if not done_a:
            continue
        va, vb = item_verdict(a), item_verdict(b)
        if va != vb:
            out.append(f"{item_id}: item verdict {va} -> {vb}")
        for pc in packet.get("candidates") or []:
            cid = pc.get("cid")
            ca = next((c for c in a.get("candidates") or [] if isinstance(c, Mapping) and c.get("cid") == cid), None)
            cb = next((c for c in b.get("candidates") or [] if isinstance(c, Mapping) and c.get("cid") == cid), None)
            xa, xb = _critical_value(ca), _critical_value(cb)
            if xa != xb:
                out.append(f"{item_id}/{cid}: {_show_critical(xa)} -> {_show_critical(xb)}")
        added_b = [c for c in b.get("added_candidates") or [] if isinstance(c, Mapping)]
        matched = []
        for ca in (c for c in a.get("added_candidates") or [] if isinstance(c, Mapping)):
            ids = work_ids(ca.get("work") or {})
            cb = next((c for c in added_b if c not in matched and ids & work_ids(c.get("work") or {})), None)
            if cb is not None:
                matched.append(cb)
            name = sorted(ids)[0] if ids else f"added {ca.get('cid')}"
            xa, xb = _critical_value(ca), _critical_value(cb)
            if xa != xb and (cb is not None or xa[0]):
                out.append(f"{item_id}/{name}: {_show_critical(xa)} -> "
                           f"{_show_critical(xb) if cb is not None else 'not in the later file'}")
        for cb in (c for c in added_b if c not in matched):
            xb = _critical_value(cb)
            if xb[0]:
                ids = work_ids(cb.get("work") or {})
                name = sorted(ids)[0] if ids else f"added {cb.get('cid')}"
                out.append(f"{item_id}/{name}: not in the first file -> {_show_critical(xb)}")
    return out


# Output.

def _fmt(value, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def table(report: Mapping) -> str:
    """The short table the command prints."""
    coders = report["coders"]
    lines = [f"{'distinction':<20} {'units':>5}  {'alpha':>6}  {'95% interval':<16} {'P<0.667':>7} {'P<0.800':>7}  "
             f"{'kappa':>6} {'raw':>6}  {'needs':>5}  result"]
    for name, d in report["distinctions"].items():
        pair = d["pairs"][0] if len(coders) == 2 and d["pairs"] else None
        interval = f"[{_fmt(d['ci95'][0])}, {_fmt(d['ci95'][1])}]" if d["ci95"] else "-"
        lines.append(f"{SHORT_NAMES[name]:<20} {d['n_units']:>5}  {_fmt(d['alpha']):>6}  {interval:<16} "
                     f"{_fmt(d['p_below']['0.667'], 2):>7} {_fmt(d['p_below']['0.800'], 2):>7}  "
                     f"{_fmt(pair['kappa'] if pair else None):>6} {_fmt(pair['raw_agreement'] if pair else None):>6}  "
                     f"{_fmt(d['threshold']) if d['threshold'] is not None else '-':>5}  {d['result']}")
    return "\n".join(lines)


def decision_line(report: Mapping) -> str:
    return {"proceed": "decision 18: every gated distinction reaches its threshold; proceed",
            "fail": "decision 18: a gated distinction falls short; revise LABELING.md once (log it in eval/protocol.md "
                    "section 14) and measure again on fresh shared items, never on the discussed ones",
            "undefined": "decision 18: alpha is undefined for a gated distinction (no variation in the values); "
                         "agreement cannot be judged on these items"}[report["decision"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.agreement",
                                     description="Krippendorff's alpha, Cohen's kappa and raw agreement on the four "
                                                 "distinctions of eval/protocol.md decision 18")
    parser.add_argument("batch")
    parser.add_argument("labels", nargs="+")
    parser.add_argument("--batch", dest="more_batches", action="append", default=[], metavar="BATCH",
                        help="pool another batch with the first (repeat for more); each labels file goes with the "
                             "batch its own `batch` field names")
    parser.add_argument("--out", help="the JSON report (default: eval/results/agreement/<batch>-agreement.json)")
    parser.add_argument("--items", help="comma-separated item ids to measure on (default: every item at least two "
                                        "annotators marked done)")
    parser.add_argument("--require-all", action="store_true", help="only items every annotator marked done")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--resamples", type=int, default=RESAMPLES)
    parser.add_argument("--ignore-problems", action="store_true",
                        help="measure even when check-labels finds problems in a labels file")
    parser.add_argument("--changes", action="store_true",
                        help="compare two exports of one annotator (the first and a later one) on the decision-"
                             "critical fields instead of measuring agreement")
    args = parser.parse_args(argv)
    batch_path = Path(args.batch)
    if args.changes:
        return changes_command(batch_path, [Path(p) for p in args.labels], bool(args.more_batches))
    if args.more_batches:
        groups, problems = load_pooled([batch_path] + [Path(p) for p in args.more_batches],
                                       [Path(p) for p in args.labels])
    else:
        batch, readings, problems = load_readings(batch_path, [Path(p) for p in args.labels])
        groups = [Group(batch_path, batch, readings)]
    readings = [r for g in groups for r in g.readings]
    label_problems = [p for r in readings for p in r.problems]
    for problem in problems + label_problems:
        print(problem)
    if problems or (label_problems and not args.ignore_problems):
        print(f"nothing measured: {len(problems) + len(label_problems)} problems; fix them, or pass --ignore-problems "
              "to measure the labels as they are")
        return 1
    item_ids = [i.strip() for i in args.items.split(",") if i.strip()] if args.items else None
    pooled = len(groups) > 1
    name = "+".join(str(g.batch.get("batch")) for g in groups)
    report = {"format": REPORT_FORMAT, "created": date.today().isoformat(), "batch": name,
              "inputs": {"batch": {"path": str(batch_path), "sha256": sha256(batch_path)},
                         "batches": [{"batch": g.batch.get("batch"), "path": str(g.batch_path),
                                      "sha256": sha256(g.batch_path)} for g in groups],
                         "labels": [{"annotator": r.annotator, "batch": g.batch.get("batch"),
                                     "path": str(r.labels_path),
                                     "sha256": sha256(r.labels_path), "exported_at": r.labels.get("exported_at"),
                                     "batch_copy": str(r.batch_path), "batch_copy_sha256": sha256(r.batch_path)}
                                    for g in groups for r in g.readings]},
              "label_problems": label_problems,
              "note": "independent first readings only (eval/protocol.md decision 18): never labels changed after "
                      "discussion" + ("; pooled over batches: one measurement over all their items, never an average "
                                      "of per-batch alphas" if pooled else "")}
    report.update(measure_pooled([(g.batch, g.readings) for g in groups], item_ids, args.resamples, args.seed,
                                 args.require_all))
    report["prefill"] = prefill_mix(groups, report["items"]["used"])
    out = Path(args.out) if args.out else RESULTS_DIR / f"{name}-agreement.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    used, excluded = report["items"]["used"], report["items"]["excluded"]
    print(f"agreement on {report['batch']}: {len(used)} items {report['items']['rule']} of "
          f"{', '.join(report['coders'])}{f' ({len(excluded)} left out)' if excluded else ''}; bootstrap over items, "
          f"{args.resamples} resamples, seed {args.seed}")
    if report["items"]["unknown"]:
        print(f"not in the batch{'es' if pooled else ''}: {', '.join(report['items']['unknown'])}")
    print(table(report))
    added = report["added_candidates"]
    print(f"added candidates (descriptive): {added['added']}, the same work added by two annotators: "
          f"{added['added_by_two']}")
    print(prefill_line(report["prefill"]))
    print(decision_line(report) if not pooled else
          "pooled (section 11): the final reliability over every item with two readings, reported as measured; "
          "decision 18's gate is read on the shared items of one batch, not here")
    print(f"report: {out}")
    return 0


def prefill_mix(groups: Sequence[Group], used: Sequence[str]) -> dict:
    """Decision 25: how the measured items were read. `shared_prefill`: every reading on the same pre-filled draft
    (b01: report this agreement as agreement under a shared pre-fill); `one_bare`: pre-filled and bare readings mixed
    (the crossover items: the anchor effect is part of any disagreement); `all_bare`: no reading saw a draft."""
    out = {"shared_prefill": [], "one_bare": [], "all_bare": []}
    wanted = set(used)
    for g in groups:
        copies = [({p.get("id"): p for p in r.batch.get("items") or []}, r.done()) for r in g.readings]
        for packet in g.batch.get("items") or []:
            item_id = packet.get("id")
            if item_id not in wanted:
                continue
            bare = [is_bare(packets[item_id]) for packets, done in copies if item_id in done and item_id in packets]
            key = "all_bare" if all(bare) else ("one_bare" if any(bare) else "shared_prefill")
            out[key].append(item_id)
    return out


def prefill_line(mix: Mapping) -> str:
    parts = [f"{len(mix['shared_prefill'])} items read on the same pre-filled draft by every reader (report as "
             "agreement under a shared pre-fill, decision 25)"]
    if mix["one_bare"]:
        parts.append(f"{len(mix['one_bare'])} read once bare and once pre-filled (crossover: a disagreement there "
                     "mixes in the anchor effect)")
    if mix["all_bare"]:
        parts.append(f"{len(mix['all_bare'])} read bare by every reader")
    return "pre-fill: " + "; ".join(parts)


def changes_command(batch_path: Path, labels_paths: Sequence[Path], pooled: bool) -> int:
    """`--changes`: two exports of one annotator for one batch, compared on the decision-critical fields."""
    if pooled or len(labels_paths) != 2:
        print("--changes takes one batch and exactly two labels files of one annotator: the first export, then the "
              "later one")
        return 2
    batch = read_json(batch_path)
    files = [read_json(p) for p in labels_paths]
    problems = []
    if files[0].get("annotator") != files[1].get("annotator"):
        problems.append(f"the files are of annotators {files[0].get('annotator')!r} and "
                        f"{files[1].get('annotator')!r}; --changes compares two exports of one annotator")
    for path, labels in zip(labels_paths, files):
        if labels.get("batch") != batch.get("batch"):
            problems.append(f"{path.name} holds labels for batch {labels.get('batch')!r}, not {batch.get('batch')!r}")
    if problems:
        for problem in problems:
            print(problem)
        return 2
    copy_path, copy_batch, _ = batch_copy(batch_path, batch, files[0])
    for label, path in (("first", labels_paths[0]), ("later", labels_paths[1])):
        print(f"{label}: {path} sha256 {sha256(path)}")
    print(f"items compared on {copy_path.name}")
    found = critical_changes(copy_batch.get("items") or [], files[0], files[1])
    for line in found:
        print(line)
    if not found:
        print("no decision-critical field changed: the later file may stand for the first reading; record both "
              "SHA-256 in docs/decisions.md")
        return 0
    print(f"{len(found)} decision-critical changes: measure agreement on the later file and also on the first export "
          "(--ignore-problems) as a sensitivity line, and record both SHA-256 in docs/decisions.md")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
