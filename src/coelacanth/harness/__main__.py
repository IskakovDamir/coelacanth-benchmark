"""Command line of the measurement-A harness (docs/design/harness.md section 6).

    python -m coelacanth.harness hash    --system single-agent
    python -m coelacanth.harness pilot   --system single-agent --items guide-S1 guide-E1 --cap 2 [--resume <run_dir>]
    python -m coelacanth.harness run     --system single-agent --subset H --tier T1 --condition text --cap 14 \
                                         [--resume <run_dir>]
    python -m coelacanth.harness resolve <run_dir>
    python -m coelacanth.harness pool    --round 1 <run_dir> [<run_dir> ...]
    python -m coelacanth.harness project <pilot_dir> [<pilot_dir> ...]
    python -m coelacanth.harness status  <run_dir>

Every command that runs or scores checks first and exits non-zero on any problem. No command reads `.env` values
beyond what llm.auth_problem checks.

One scope, one run (decision 33): `run` refuses a second scored run of the same (system, condition, tier, subset), which
would resample the first run's final failures and leave two runs to pool. An aborted or interrupted run is finished
with `--resume <run_dir>`, which runs only the items with no row or with an infrastructure error, under the same config;
the run's earlier spend counts toward its cap. Pilots may be repeated (tuning) and resumed the same way.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .. import llm
from . import agents, budget as budget_mod, config as config_mod, pool as pool_mod, refpipe, resolve
from . import run as run_mod
from .budget import RunSpec

ADAPTERS = {"agent": agents.run_agent, "refpipe": refpipe.run}


def _budget() -> dict:
    path = run_mod.MEASUREMENT_A / "budget.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _items(spec_kind: str) -> dict:
    path = run_mod.MEASUREMENT_A / "pilot-items.jsonl" if spec_kind == "pilot" else run_mod.ITEMS
    return {i["id"]: i for i in llm.read_jsonl(path)}


def _subset_items(subset: str) -> tuple[str, ...]:
    if not run_mod.SPLITS.exists():
        return ()  # preflight refuses: no splits file before the items freeze (decision 3)
    splits = json.loads(run_mod.SPLITS.read_text(encoding="utf-8"))
    subsets = splits.get("subsets") or {}
    if subset == "ALL_minus_H":
        return tuple(sorted(set(subsets.get("ALL") or []) - set(subsets.get("H") or [])))
    return tuple(subsets.get(subset) or [])


def _manifest(run_dir: Path) -> dict:
    path = run_dir / "run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _records(run_dir: Path) -> list[dict]:
    """run.json and every outputs and ledger row of a run directory."""
    manifest = _manifest(run_dir)
    return ([manifest] if manifest else []) + llm.read_jsonl(run_dir / "outputs.jsonl") + \
        llm.read_jsonl(run_dir / "ledger.jsonl")


def _scopes(run_dir: Path) -> set[tuple]:
    return {(r.get("system"), r.get("condition"), r.get("tier"), r.get("subset"), r.get("kind"))
            for r in _records(run_dir)}


def _run_dir(spec: RunSpec, base: Path, sha: str | None, file_sha: str, resume: str | None) -> tuple[Path, list[str]]:
    """The directory the run writes to and why it may not: a new one, refused for a scored scope that already has a
    run; or the `--resume` directory, refused unless it is a run of this very spec, items and config under `base`."""
    want = (spec.system, spec.condition, spec.tier, spec.subset, spec.kind)
    run_dir = Path(resume) if resume else None
    same = [d for d in sorted(base.glob("*")) if spec.kind == "scored" and d.is_dir() and want in _scopes(d)
            and (run_dir is None or d.resolve() != run_dir.resolve())]
    out = [f"a run of {spec.scope} exists ({', '.join(d.name for d in same)}); continue it with --resume <run_dir>, "
           f"since a second run would resample its final failures (decision 33)"] if same else []
    if run_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return base / f"{spec.system}.{spec.condition}.{spec.tier}.{stamp}", out
    if not run_dir.is_dir() or run_dir.resolve().parent != base.resolve():
        return run_dir, out + [f"{resume} is not a run directory under {base}"]
    records = _records(run_dir)
    scopes = _scopes(run_dir)
    if not records:
        out.append(f"{run_dir} holds no run.json and no rows to resume")
    elif scopes != {want}:
        out.append(f"{run_dir} holds runs of {sorted(map(str, scopes))}, not of {want}")
    manifest = _manifest(run_dir)
    if manifest and tuple(manifest.get("item_ids") or ()) != tuple(spec.items):
        out.append(f"{run_dir} was run on other items than the {len(spec.items)} asked for")
    hashes = {r.get("config_sha256") for r in records}
    if hashes - {sha}:
        out.append(f"{run_dir} was run under config {sorted(map(str, hashes))}, now {sha}")
    if manifest.get("config_file_sha256") not in (None, file_sha):
        out.append(f"the config file of {spec.system} changed since {run_dir.name} started: "
                   f"{manifest.get('config_file_sha256')}, now {file_sha}")
    return run_dir, out


def _go(spec: RunSpec, workers: int, resume: str | None = None) -> int:
    configs = run_mod.MEASUREMENT_A / "configs"
    try:
        if spec.kind == "pilot":
            cfg, file_sha = config_mod.load(spec.system, configs)
            sha = None
        else:
            cfg, sha = config_mod.frozen(spec.system, root=run_mod.MEASUREMENT_A)
            file_sha = sha
    except (FileNotFoundError, config_mod.ConfigError) as err:
        print(json.dumps({"refused": [str(err)]}, indent=1, ensure_ascii=False))
        return 1
    base = (run_mod.MEASUREMENT_A / "pilots" / spec.system if spec.kind == "pilot"
            else run_mod.MEASUREMENT_A / "runs" / spec.system / spec.condition)
    run_dir, problems = _run_dir(spec, base, sha, file_sha, resume)
    problems += run_mod.preflight(spec, cfg, root=run_mod.MEASUREMENT_A, splits_path=run_mod.SPLITS,
                                  items_path=run_mod.ITEMS, budget=_budget())
    adapter = ADAPTERS.get(cfg.get("adapter"))
    if adapter is None:
        problems.append(f"no adapter for {cfg.get('adapter')!r} yet")
    if problems:
        print(json.dumps({"refused": problems}, indent=1, ensure_ascii=False))
        return 1
    r = run_mod.Run(spec=spec, cfg=cfg, config_sha256=sha, run_id=run_dir.name, run_dir=run_dir,
                    items=_items(spec.kind), adapter=adapter, raw_dir=run_mod.RAW, splits_path=run_mod.SPLITS,
                    config_file_sha256=file_sha)
    print(json.dumps(run_mod.execute(r, workers=workers), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.harness")
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hash", help="log a system's config hash before its first scored run")
    h.add_argument("--system", required=True)
    for name in ("pilot", "run"):
        p = sub.add_parser(name)
        p.add_argument("--system", required=True)
        p.add_argument("--cap", type=float, required=True)
        p.add_argument("--condition", default="text", choices=("text", "skeleton"))
        p.add_argument("--workers", type=int, default=2)
        p.add_argument("--resume", metavar="RUN_DIR", help="continue this run directory; only pending items run")
        if name == "pilot":
            p.add_argument("--items", nargs="+", required=True)
        else:
            p.add_argument("--subset", required=True)
            p.add_argument("--tier", required=True)
    rs = sub.add_parser("resolve")
    rs.add_argument("run_dir")
    po = sub.add_parser("pool")
    po.add_argument("--round", type=int, required=True)
    po.add_argument("run_dirs", nargs="+")
    pr = sub.add_parser("project")
    pr.add_argument("pilot_dirs", nargs="+")
    st = sub.add_parser("status")
    st.add_argument("run_dir")
    args = parser.parse_args(argv)

    if args.command == "hash":
        print(json.dumps(config_mod.log(args.system), indent=1))
        return 0
    if args.command == "pilot":
        return _go(RunSpec(system=args.system, kind="pilot", subset="pilot", tier="T0", condition=args.condition,
                           cap_usd=args.cap, items=tuple(args.items)), args.workers, args.resume)
    if args.command == "run":
        return _go(RunSpec(system=args.system, kind="scored", subset=args.subset, tier=args.tier,
                           condition=args.condition, cap_usd=args.cap, items=_subset_items(args.subset)), args.workers,
                   args.resume)
    if args.command == "resolve":
        print(json.dumps(resolve.resolve_run(args.run_dir), indent=1, default=str))
        return 0
    if args.command == "pool":
        print(json.dumps(pool_mod.build_pool(args.round, args.run_dirs, _items("scored").values()), indent=1,
                         default=str)[:4000])
        return 0
    if args.command == "project":
        print(json.dumps(budget_mod.project([Path(p) for p in args.pilot_dirs], _budget().get("plan") or []),
                         indent=1))
        return 0
    if args.command == "status":
        run_dir = Path(args.run_dir)
        rows = {r["item"]: r["status"] for r in llm.read_jsonl(run_dir / "outputs.jsonl")}
        manifest = _manifest(run_dir)
        ids = list(manifest.get("item_ids") or rows)  # run.json lists the items that never started too
        spent = sum(r.get("cost_usd") or 0 for r in llm.read_jsonl(run_dir / "ledger.jsonl"))
        print(json.dumps({"items": len(ids), "by_status": dict(Counter(rows.values())), "spent_usd": round(spent, 4),
                          "finished": manifest.get("finished"),
                          "aborted": (manifest.get("summary") or {}).get("aborted"),
                          "no_row": [i for i in ids if i not in rows],
                          "pending": run_mod.pending(run_dir, ids)}, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
