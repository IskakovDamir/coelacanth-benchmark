"""Budget rules for measurement A (docs/design/harness.md section 8.5; eval/protocol.md section 9 and decision 9).

budget.json (committed) holds the approved line caps, the spend booked on a line outside the harness
(`spent_outside_harness`: {line: {"usd", "sources"}}, the ledgers of closed-book runs, probes and benchmark
construction), each system's line, the tier plan and Damir's approvals in his words. `check` refuses a run when:
- its cap is above $20 and no `run_over_20` approval covers its scope at that amount;
- its cap is above its line's cap minus what the line's harness ledgers booked and what was spent outside the harness;
  a run still in flight (run.json without `finished`) counts at least at its cap, so parallel runs cannot overspend a
  line together;
- a lower tier of the plan has a planned run without a finished run.json;
- the plan has no row for its system, tier, subset and condition, or the plan's rows of its tier mix the nested
  subsets of one headline set (H and H_half; P40 and P20), since all systems of a tier run on the same subset
  (decision 9 (4)).
Pilots book to the `pilots` line and are outside the tier rules. `project` turns the 2-item pilots into projected
costs, pilot mean x 1.5 x planned items, from ledgers and statuses only, never candidates or scores (decision 9).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from ..llm import read_jsonl
from .config import MEASUREMENT_A

APPROVAL_ABOVE_USD = 20.0
MARGIN = 1.5
PILOT_LINE = "pilots"
INFRA = ("infra_error", "model_mismatch")  # re-run on resume (run.INFRA; not imported, since run imports budget)
NESTED = {"H": "H", "H_half": "H", "R": "R", "R_half": "R", "P40": "P40", "P20": "P40"}  # subset -> the headline set it is drawn from


@dataclass(frozen=True)
class RunSpec:
    """One run as it is asked for. `kind` is "pilot" or "scored"; `items` are the item IDs."""
    system: str
    kind: str
    subset: str
    tier: str
    condition: str
    cap_usd: float
    items: tuple[str, ...] = ()

    @property
    def scope(self) -> str:
        """The scope an approval names, as budget.json writes it: "single-agent:T1:H:text"."""
        return f"{self.system}:{self.tier}:{self.subset}:{self.condition}"


def ledger_paths(root: Path = MEASUREMENT_A) -> list[Path]:
    """The ledger of every run, laid out as in spec 8.12: pilots/<system>/<run_id>/ and
    runs/<system>/<condition>/<run_id>/. A run directory counts once it holds a ledger or a run.json, so a run that
    has started but booked nothing yet is listed too (its ledger path may not exist)."""
    out = []
    for pattern in ("pilots/*/*", "runs/*/*/*"):
        dirs = {p.parent for name in ("ledger.jsonl", "run.json") for p in root.glob(f"{pattern}/{name}")}
        out += [d / "ledger.jsonl" for d in sorted(dirs)]
    return out


def _run_json(run_dir: Path) -> dict | None:
    path = run_dir / "run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _line_of(run_dir: Path) -> str:
    run = _run_json(run_dir)
    if run is None:
        raise ValueError(f"{run_dir / 'run.json'} is missing (a run in progress, or one that crashed before writing "
                         f"it), so the line its ledger books to is unknown")
    return PILOT_LINE if run.get("kind") == "pilot" else str(run.get("line"))


def line_spent(line: str, ledgers: Iterable[Path]) -> float:
    """Dollars the given ledgers booked on `line`: every row of each run whose run.json names that line (pilot runs
    book to the pilots line). A run whose run.json has no `finished` is in flight (or crashed) and counts at least at
    its cap, `cap_usd`, until it finishes. A ledger without a run.json raises ValueError rather than being left
    out."""
    total = 0.0
    for path in ledgers:
        if _line_of(path.parent) != line:
            continue
        run = _run_json(path.parent) or {}
        spent = sum(float(row.get("cost_usd") or 0.0) for row in read_jsonl(path))
        total += spent if run.get("finished") else max(spent, float(run.get("cap_usd") or 0.0))
    return round(total, 8)


def spent_outside_harness(budget: Mapping, line: str) -> float:
    """Dollars budget.json books on `line` from ledgers outside the harness (closed-book runs, probes, construction)."""
    return float(((budget.get("spent_outside_harness") or {}).get(line) or {}).get("usd") or 0.0)


def _tier(value) -> int | None:
    match = re.fullmatch(r"T(\d)", str(value or ""))
    return int(match.group(1)) if match else None


def _finished(root: Path, row: Mapping) -> bool:
    """Whether a planned run has a run.json that finished without aborting and with no item pending (an
    infrastructure error is re-run on resume, so a run that still has one is not complete)."""
    for path in sorted(root.glob(f"runs/{row.get('system')}/{row.get('condition')}/*/run.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        summary = run.get("summary") or {}
        if ((run.get("tier"), run.get("subset")) == (row.get("tier"), row.get("subset")) and run.get("finished")
                and not summary.get("aborted") and not summary.get("pending")):
            return True
    return False


def check(spec: RunSpec, cfg: Mapping, budget: Mapping, *, root: Path = MEASUREMENT_A) -> list[str]:
    """Why the run may not start, or an empty list."""
    out = []
    line = PILOT_LINE if spec.kind == "pilot" else cfg.get("line")
    booked = (budget.get("systems") or {}).get(spec.system)
    if spec.kind != "pilot" and booked != line:
        out.append(f"budget.json books {spec.system} to line {booked!r}, its config to {line!r}")
    approved = any(a.get("kind") == "run_over_20" and a.get("scope") == spec.scope
                   and float(a.get("usd") or 0) >= spec.cap_usd for a in budget.get("approvals") or [])
    if spec.cap_usd > APPROVAL_ABOVE_USD and not approved:
        out.append(f"cap ${spec.cap_usd:.2f} is above ${APPROVAL_ABOVE_USD:.0f} and no run_over_20 approval covers "
                   f"{spec.scope} at that amount")
    cap = (budget.get("lines") or {}).get(line)
    if cap is None:
        out.append(f"line {line!r} has no cap in budget.json")
    else:
        try:
            left = float(cap) - line_spent(str(line), ledger_paths(root)) - spent_outside_harness(budget, str(line))
        except ValueError as err:
            out.append(str(err))
        else:
            if spec.cap_usd > left:
                out.append(f"cap ${spec.cap_usd:.2f} is above what line {line} has left: ${left:.2f} of ${cap}")
    if spec.kind == "pilot":
        return out
    plan = budget.get("plan") or []
    tier = _tier(spec.tier)
    if tier is None:
        out.append(f"tier {spec.tier!r} is not a tier (T0, T1, ...)")
    else:
        for row in plan:
            lower = _tier(row.get("tier"))
            if lower is not None and lower < tier and not _finished(root, row):
                out.append(f"tier {row.get('tier')} is not complete: {row.get('system')}:{row.get('subset')}:"
                           f"{row.get('condition')} has no finished run.json")
    if not any((row.get("system"), row.get("tier"), row.get("subset"), row.get("condition"))
               == (spec.system, spec.tier, spec.subset, spec.condition) for row in plan):
        out.append(f"no plan row funds the {spec.condition} condition for {spec.system} in {spec.tier} on "
                   f"{spec.subset}")
    mixed: dict[str, set] = defaultdict(set)
    for row in plan:
        if row.get("tier") == spec.tier and row.get("subset") in NESTED:
            mixed[NESTED[row["subset"]]].add(row["subset"])
    out += [f"the plan's {spec.tier} rows mix {sorted(subsets)}: all systems of a tier run on the same nested subset "
            f"(decision 9 (4))" for _, subsets in sorted(mixed.items()) if len(subsets) > 1]
    return out


def _system_of(run_dir: Path) -> str:
    run = _run_json(run_dir)
    return (run or {}).get("system") or run_dir.parent.name


def _pilot_items(run_dir: Path) -> list[dict]:
    """Cost per item of one pilot run from its ledger, and the item's status and cap from outputs.jsonl. Of an output
    row only `item`, `status` and `caps.usd_item` are kept: candidates, answers and evidence never enter."""
    costs: dict[str, dict] = {}
    for row in read_jsonl(run_dir / "ledger.jsonl"):
        c = costs.setdefault(str(row.get("item")), {"usd": 0.0, "tokens_all": {}, "web_searches": 0, "seconds": 0.0})
        c["usd"] += float(row.get("cost_usd") or 0.0)
        for key, n in (row.get("tokens_all") or {}).items():
            c["tokens_all"][key] = c["tokens_all"].get(key, 0) + int(n or 0)
        c["web_searches"] += int(row.get("web_searches") or 0)
        c["seconds"] += float(row.get("wall_seconds") or 0.0)
    ended = {str(row.get("item")): (row.get("status"), (row.get("caps") or {}).get("usd_item"))
             for row in read_jsonl(run_dir / "outputs.jsonl")}
    out = []
    for item, c in sorted(costs.items()):
        status, cap = ended.get(item, (None, None))
        usd = round(c["usd"], 6)
        out.append({"run": run_dir.name, "item": item, "usd": usd, "tokens_all": c["tokens_all"],
                    "web_searches": c["web_searches"], "seconds": round(c["seconds"], 1), "status": status,
                    "cap_usd": cap, "ok_within_cap": status == "ok" and (usd <= cap if cap is not None else None)})
    return out


def project(pilot_dirs: Iterable[Path], plan: Iterable[Mapping], *,
            out_path: Path | None = MEASUREMENT_A / "projection.json") -> dict:
    """Projected cost of every planned run: the mean dollars per pilot item of its system x 1.5 x its planned items.
    Every pilot item of the given runs of a system counts once per run, except items whose last status is an
    infrastructure error (`infra_error`, `model_mismatch`): they are re-run, so their cost says nothing about the
    system's, and they stay listed in `pilot_items` but are left out of the mean and the maximum. An item re-run in
    the same run directory counts once, at its summed spend and last status. Writes `out_path` unless it is None."""
    pilots: dict[str, list[dict]] = defaultdict(list)
    for run_dir in pilot_dirs:
        pilots[_system_of(run_dir)] += _pilot_items(run_dir)
    rows = []
    for row in plan:
        items = pilots.get(str(row.get("system")), [])
        usd = [i["usd"] for i in items if i["status"] not in INFRA]
        mean = sum(usd) / len(usd) if usd else None
        rows.append({**{k: row.get(k) for k in ("tier", "system", "subset", "condition", "items")},
                     "pilot_items": items, "pilot_mean_usd": round(mean, 6) if mean is not None else None,
                     "pilot_max_usd": max(usd) if usd else None,
                     "projected_usd": round(mean * MARGIN * row["items"], 4) if mean is not None else None})
    result = {"format": "coelacanth-projection/v1", "margin": MARGIN, "rows": rows,
              "projected_usd": round(sum(r["projected_usd"] or 0.0 for r in rows), 4),
              "without_pilot": sorted({str(r["system"]) for r in rows if r["projected_usd"] is None})}
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
