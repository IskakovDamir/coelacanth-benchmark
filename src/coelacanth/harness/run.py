"""Running one system under test on a list of items (docs/design/harness.md sections 5, 6 and 8.6 to 8.9).

A run is a RunSpec (budget.py): system, kind ("pilot" or "scored"), subset, tier, condition ("text" or "skeleton"),
cap and items. `preflight` refuses it unless every rule of section 6 holds; then each pending item becomes one job:
the view (`items.system_view` or `items.skeleton_view`, nothing else), a fresh working directory and an `ItemCtx`,
one adapter call, a decline retried once with the identical input, and a status from the fixed taxonomy. Jobs run
through `llm.run_jobs`, which books every call; a system's own failure (cap, turns, timeout, unparsed answer, a second
decline) is final and does not stop the run, while an infrastructure error is re-run on resume.

The two attempts of a declined item share the item's one dollar cap: the retry gets what the first attempt left, in
`ctx.cap_usd` and in the config's `per_item_cap_usd` (protocol 6, decision 12). A timed-out attempt without usage is
booked at its own cap as an upper bound. An exception raised inside an adapter (OpenAlex `BudgetExceeded` or
`OpenAlexError`, any other failure of the adapter) is an `infra_error` output, booked at the paid sub-calls the job
logged to calls.jsonl; a model mismatch that run_jobs flags after `call` is saved as `model_mismatch`. Both are re-run
on resume (section 6, decision 33).

Resume: `execute` runs only `pending` items, so running it again on the same run directory finishes an aborted or
interrupted run without resampling a final failure; the command line reaches this with `--resume <run_dir>`.

The frozen item set (decision 3). The 19 Oct freeze writes into the splits file, besides `items_sha256` (the SHA-256 of
items.jsonl at the freeze, kept as a recorded fact), one hash per item of what any condition can show a system:

    "views_sha256": {"cf-0001": "<frozen_view_sha256(item)>", ...}

where `frozen_view_sha256` hashes {"text": claim.text, "skeleton": claim.skeleton, "date": claim.date} as `view_sha256`
does (sorted keys, compact separators, UTF-8). Pooled judgments merged into items.jsonl later change the file but not
these views, so a scored run's preflight checks the views of its requested items, not the file's hash.

What is written (all under the run directory, except raw text):
    outputs.jsonl   one row per item, failures included, no model free text (section 8.6)
    ledger.jsonl    one row per item job, from run_jobs
    calls.jsonl     one row per sub-call of a multi-step system
    run.json        the manifest (section 8.9): written without `finished` before the first job, so an unfinished run
                    still names its line, and rewritten with counts per status when the run ends, aborts or is
                    interrupted
    cache/runs/<run_id>/<task>.{jsonl,answer.txt}   traces and answers, gitignored
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import llm, pricing
from ..benchmark.items import skeleton_view, system_view
from ..env import REPO_ROOT
from . import budget as budget_mod
from . import config as config_mod
from .budget import RunSpec
from .retrieval import canonical_id, work_keys

MEASUREMENT_A = REPO_ROOT / "eval" / "results" / "measurement-a"
SPLITS = REPO_ROOT / "eval" / "results" / "splits" / "splits-v1.json"
ITEMS = REPO_ROOT / "eval" / "benchmark" / "v0" / "items.jsonl"
BATCHES = REPO_ROOT / "eval" / "benchmark" / "v0" / "batches"
RAW = REPO_ROOT / "cache" / "runs"
WORK_ID_KINDS = ("openalex", "doi", "arxiv", "pmid")  # canonical_id kinds that name one work; `url:` keys do not
OUTPUT_FORMAT = "coelacanth-system-output/v1"
RUN_FORMAT = "coelacanth-run/v1"
MAX_WORKERS = 4  # Damir's limit on parallel agents
STATUSES = ("ok", "unparsed", "capped", "turn_limit", "timeout", "declined", "error", "model_mismatch", "infra_error")
INFRA = ("model_mismatch", "infra_error")


@dataclass(frozen=True)
class ItemCtx:
    """What an adapter gets besides the view and the config. `task` is run-local and never the item ID. `cap_usd` is
    what the attempt may spend: the config's per-item cap, or on a decline's retry what the first attempt left."""
    task: str
    cwd: str
    cap_usd: float
    search: Callable[..., list] | None
    log_call: Callable[[dict], None]
    trace_path: Path | None


def view(item: Mapping, condition: str) -> dict:
    if condition == "text":
        return system_view(item)
    if condition == "skeleton":
        return skeleton_view(item)
    raise ValueError(f"unknown condition {condition!r}")


def view_sha256(v: Mapping) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def frozen_view_sha256(item: Mapping) -> str:
    """The hash the splits file's `views_sha256` holds for an item: claim text, skeleton and date, the fields
    `system_view` and `skeleton_view` read."""
    claim = item.get("claim") or {}
    return view_sha256({"text": claim.get("text"), "skeleton": claim.get("skeleton"), "date": claim.get("date")})


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return llm.read_jsonl(path)


def _id_keys(values: Iterable) -> set[str]:
    """Canonical keys of work identifiers (W ID, DOI in any prefix form, arXiv ID, PMID); URLs are left out."""
    keys = {canonical_id(v) for v in values if isinstance(v, (str, int)) and str(v).strip()}
    return {k for k in keys if k.partition(":")[0] in WORK_ID_KINDS}


def _source_keys(item: Mapping) -> set[str]:
    """Canonical keys of the item's `claim.source_work` (SCHEMA.md), URLs left out."""
    source = (item.get("claim") or {}).get("source_work")
    keys = work_keys(source) if isinstance(source, Mapping) else set()
    return {k for k in keys if k.partition(":")[0] in WORK_ID_KINDS}


def _bench_items(items_path: Path, batches_dir: Path) -> list[dict]:
    """Every benchmark item: items.jsonl and the item lists of batches/b*.json (not their -meta or -plan files)."""
    items = _jsonl(items_path)
    for batch in sorted(batches_dir.glob("b*.json")):
        if not batch.name.endswith("-meta.json") and not batch.name.endswith("-plan.json"):
            items += json.loads(batch.read_text(encoding="utf-8")).get("items") or []
    return items


def _view_problems(item_ids: Iterable[str], splits: Mapping, items_path: Path) -> list[str]:
    """Requested items whose view differs from the one frozen in the splits file (decision 3)."""
    frozen = splits.get("views_sha256")
    if not isinstance(frozen, Mapping):
        return ["the splits file records no views_sha256, so the items' views cannot be checked against the freeze"]
    items = {i.get("id"): i for i in _jsonl(items_path)}
    item_ids = list(item_ids)
    missing = [i for i in item_ids if i not in items]
    unfrozen = [i for i in item_ids if i in items and i not in frozen]
    changed = [i for i in item_ids if i in items and i in frozen and frozen[i] != frozen_view_sha256(items[i])]
    out = []
    if missing:
        out.append(f"{len(missing)} items are not in {items_path.name}: {missing[:5]}")
    if unfrozen:
        out.append(f"{len(unfrozen)} items have no frozen view in the splits file: {unfrozen[:5]}")
    if changed:
        out.append(f"the view of {len(changed)} items differs from the frozen views_sha256: {changed[:5]}")
    return out


def _league_refusals(cfg: Mapping, root: Path) -> list[str]:
    """Decision 10 against the configs the league's members were logged and run under: every other member with a row
    in config-hashes.jsonl must still hash to its last row, and the members' configs must agree on the league
    fields. Config files of other leagues are not read beyond their `league`."""
    if cfg.get("league") is None:
        return []
    configs_dir = root / "configs"
    last = {r.get("system"): r.get("config_sha256") for r in _jsonl(root / "config-hashes.jsonl")}
    members, out = [cfg], []
    for path in sorted(configs_dir.glob("*.json")):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("league") != cfg["league"]:
                continue
            other, sha = config_mod.load(path.stem, configs_dir)
        except (config_mod.ConfigError, ValueError) as err:
            out.append(str(err))
            continue
        if other.get("system") == cfg.get("system"):
            continue
        if other.get("system") in last and last[other["system"]] != sha:
            out.append(f"league member {other['system']} changed since its hash was logged, so its scored runs used "
                       f"another config (decision 10)")
        members.append(other)
    return out + config_mod.league_problems(members)


def preflight(spec: RunSpec, cfg: Mapping, *, root: Path = MEASUREMENT_A, splits_path: Path = SPLITS,
              items_path: Path = ITEMS, batches_dir: Path = BATCHES, budget: Mapping | None = None,
              cli_version: Callable[[], str] = llm.cli_version,
              auth: Callable[[], str | None] = llm.auth_problem) -> list[str]:
    """Why the run may not start (section 6), or an empty list."""
    out = []
    problem = auth()
    if problem:
        out.append(problem)
    if spec.condition not in (cfg.get("conditions") or ()):
        out.append(f"condition {spec.condition!r} is not among the config's conditions")
    if spec.kind == "pilot":
        pilots = {p["id"]: p for p in _jsonl(root / "pilot-items.jsonl")}
        bench_items = _bench_items(items_path, batches_dir)
        bench = {i.get("id") for i in bench_items}
        bench_works = set().union(*(_source_keys(i) for i in bench_items))
        for item_id in spec.items:
            p = pilots.get(item_id)
            if p is None:
                out.append(f"pilot item {item_id} is not in pilot-items.jsonl")
            elif (p.get("provenance") or {}).get("source") != "guide" or p.get("status") != "excluded":
                out.append(f"pilot item {item_id} is not an excluded guide example")
            if item_id in bench:
                out.append(f"pilot item {item_id} is a benchmark item")
            shared = (_source_keys(p) | _id_keys(p.get("exclusions") or [])) & bench_works if p is not None else set()
            if shared:
                out.append(f"pilot item {item_id} shares its source work with a benchmark item: {sorted(shared)}")
    elif spec.kind == "scored":
        if not splits_path.exists():
            out.append("no splits file: scored runs start after the items freeze (decision 3)")
        else:
            splits = json.loads(splits_path.read_text(encoding="utf-8"))
            sums = splits_path.parent / "SHA256SUMS"
            if not sums.exists() or _sha256_file(splits_path) not in sums.read_text(encoding="utf-8"):
                out.append("the splits file does not match SHA256SUMS")
            out += _view_problems(spec.items, splits, items_path)
            readiness = splits.get("refpipe_readiness")
            if readiness not in ("ready", "not_ready"):
                out.append("the splits file records no reference pipeline readiness (decision 13)")
            elif readiness == "not_ready" and (cfg.get("line") == "refpipe" or cfg.get("adapter") == "refpipe"):
                out.append(f"{spec.system} books to the reference pipeline's line and the pipeline is not ready: its "
                           f"comparisons are listed as planned and not run (decision 13)")
            allowed = cfg.get("allowed_subsets") or ()
            if spec.subset not in allowed:
                out.append(f"subset {spec.subset} is not allowed for {spec.system}: {allowed}")
            members = set((splits.get("subsets") or {}).get(spec.subset) or [])
            if spec.subset != "ALL_minus_H" and not members:
                out.append(f"subset {spec.subset} is not in the splits file")
            if spec.subset == "ALL_minus_H":
                subsets = splits.get("subsets") or {}
                members = set(subsets.get("ALL") or []) - set(subsets.get("H") or [])
            outside = [i for i in spec.items if i not in members]
            if outside:
                out.append(f"{len(outside)} items are outside subset {spec.subset}")
            called = cfg.get("models_called")
            if called != "undisclosed" and not set(called or ()) <= set(splits.get("models_for_H") or ()):
                out.append(f"models {called} are not all among the models that define H {splits.get('models_for_H')}")
        try:
            config_mod.frozen(spec.system, root=root)
        except (config_mod.ConfigError, FileNotFoundError) as err:
            out.append(str(err))
        out += _league_refusals(cfg, root)
        version = (cfg.get("cli") or {}).get("version")
        if cfg.get("adapter") in ("agent",) + config_mod.REIMPL_ADAPTERS and version != cli_version():
            out.append(f"CLI version differs from the config's {version!r}")
    else:
        out.append(f"unknown run kind {spec.kind!r}")
    if budget is not None:
        out += budget_mod.check(spec, cfg, budget, root=root)
    return out


def classify(out: Mapping, cfg: Mapping) -> str:
    """The status of one adapter output (section 6). Stop subtypes are pinned in the config after the first pilot
    call; until then a budget stop is recognised by an error with no answer and a cost near the call cap."""
    if out.get("model_mismatch"):
        return "model_mismatch"
    result = str(out.get("result") or "")
    if out.get("is_error") and llm.LOW_CREDIT.search(result):
        return "infra_error"
    if out.get("infra_error"):
        return "infra_error"
    if out.get("timed_out"):
        return "timeout"
    if out.get("budget_exhausted"):  # the item's cap was spent before a needed step or a decline's retry
        return "capped"
    if out.get("declined"):
        return "declined"
    stops = (cfg.get("cli") or {}).get("stop_subtypes") or {}
    subtype = out.get("subtype")
    if subtype and subtype == stops.get("turn_limit"):
        return "turn_limit"
    if subtype and subtype == stops.get("capped"):
        return "capped"
    if out.get("is_error"):
        if not out.get("modelUsage") and not out.get("total_cost_usd") and not out.get("booked_cost"):
            return "infra_error"
        cap = float(cfg.get("per_item_cap_usd") or 0)
        spent = float(out.get("total_cost_usd") or 0)
        if not out.get("result") and cap and spent >= 0.95 * cap:
            return "capped"
        return "error"
    native = out.get("native") or {}
    if native.get("candidates") is None and native.get("label") is None:
        return "unparsed"
    return "ok"


def pending(run_dir: Path, item_ids: list[str]) -> list[str]:
    """Items with no output row, or whose last row is an infrastructure error; a system failure is never re-run."""
    last = {}
    for row in _jsonl(run_dir / "outputs.jsonl"):
        last[row["item"]] = row["status"]
    return [i for i in item_ids if i not in last or last[i] in INFRA]


def _merge_attempts(first: dict, second: dict) -> dict:
    """Two attempts of one item booked as one: the second answer, the summed usage and the summed booked costs."""
    usage = {}
    for out in (first, second):
        for model, u in (out.get("modelUsage") or {}).items():
            acc = usage.setdefault(model, {})
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    acc[k] = acc.get(k, 0) + v
    merged = dict(second)
    if usage:
        merged["modelUsage"] = usage
    merged["total_cost_usd"] = (first.get("total_cost_usd") or 0) + (second.get("total_cost_usd") or 0)
    booked = [out["booked_cost"] for out in (first, second) if out.get("booked_cost")]
    merged["booked_cost"] = ({"usd": round(sum(float(b["usd"]) for b in booked), 8),
                              "source": "+".join(sorted({str(b["source"]) for b in booked}))} if booked else None)
    merged["attempts"] = 2
    return merged


def booked_usd(out: Mapping) -> tuple[float, str]:
    """Dollars and source of one output, by the rule `llm.run_jobs` books the ledger with: usage at official prices
    plus any `booked_cost`; without priced usage, the `booked_cost` alone, else the CLI's own figure."""
    booked = out.get("booked_cost")
    try:
        cost, source = pricing.cli_cost(out), "usage_at_official_prices"
        if booked:
            cost, source = cost + float(booked["usd"]), f"usage_plus_{booked['source']}"
    except KeyError:
        if booked:
            cost, source = float(booked["usd"]), str(booked["source"])
        else:
            cost, source = float(out.get("total_cost_usd") or 0.0), "cli_reported"
    return round(cost, 8), source


def _by_model(out: Mapping) -> dict:
    """Dollars and web searches per model of `modelUsage` (section 8.6); a model without a price gets usd null."""
    per_model = out.get("modelUsage") or {}
    rows = {}
    for model, u in per_model.items():
        part = {"modelUsage": {model: u}, "usage": out.get("usage") if len(per_model) == 1 else None}
        try:
            usd = round(pricing.cli_cost(part), 6)
        except KeyError:
            usd = None
        rows[model] = {"usd": usd, "web_searches": int(u.get("webSearchRequests") or 0)}
    return rows


@dataclass
class Run:
    spec: RunSpec
    cfg: Mapping
    config_sha256: str | None
    run_id: str
    run_dir: Path
    items: Mapping[str, Mapping]
    adapter: Callable[[Mapping, Mapping, ItemCtx], dict]
    search_factory: Callable[[Mapping], Callable | None] | None = None
    raw_dir: Path = RAW
    splits_path: Path = SPLITS
    config_file_sha256: str | None = None  # the config file's SHA-256 also for pilots, whose config is not logged
    lock: threading.Lock = field(default_factory=threading.Lock)

    def call(self, job: tuple[int, str]) -> dict:
        n, item_id = job
        item = self.items[item_id]
        v = view(item, self.spec.condition)
        task = f"job-{n}"
        raw = self.raw_dir / self.run_id
        cap = float(self.cfg.get("per_item_cap_usd") or 0)
        paid: list[float] = []  # dollars of the paid sub-calls this job logged to calls.jsonl

        def log_call(row: Mapping) -> None:
            if row.get("kind") == "llm":
                paid.append(float(row.get("cost_usd") or 0.0))
            self._log_call(item_id, row)

        ctx = ItemCtx(task=task, cwd=tempfile.mkdtemp(prefix="coelacanth-run-"), cap_usd=cap,
                      search=self.search_factory(item) if self.search_factory else None,
                      log_call=log_call, trace_path=raw / f"{task}.jsonl")
        out = self._attempt(v, self.cfg, ctx, paid)
        if out.get("is_error") and not out.get("infra_error") and llm.DECLINED.search(str(out.get("result") or "")):
            left = round(max(0.0, cap - booked_usd(out)[0]), 8)
            if cap > 0 and left <= 0:  # the first attempt spent the item's whole cap, so no retry fits within it
                out["budget_exhausted"] = True
            else:  # the identical input; the retry may spend only what is left of the item's one cap
                retry_cfg, retry_ctx = (self.cfg, ctx)
                if cap > 0:
                    retry_cfg = {**self.cfg, "per_item_cap_usd": left}
                    retry_ctx = dataclasses.replace(ctx, cap_usd=left)
                retry = self._attempt(v, retry_cfg, retry_ctx, paid)
                out = _merge_attempts(out, retry)
                if retry.get("is_error") and llm.DECLINED.search(str(retry.get("result") or "")):
                    out["declined"] = True
                else:
                    out.pop("declined", None)
                    out["is_error"] = bool(retry.get("is_error"))
        out["status"] = classify(out, self.cfg)
        out["view_sha256"] = view_sha256(v)
        out["task"] = task
        return out

    def _attempt(self, v: Mapping, cfg: Mapping, ctx: ItemCtx, paid: list[float]) -> dict:
        """One adapter call. A timeout without usage is booked at the attempt's cap as an upper bound. An exception
        inside the adapter is an infrastructure error (section 6: OpenAlex `BudgetExceeded` or `OpenAlexError`, or
        any other adapter failure), booked at the paid sub-calls the attempt logged; Ctrl-C is not caught."""
        before = len(paid)
        try:
            out = self.adapter(v, cfg, ctx)
        except Exception as err:
            spent = paid[before:]
            return {"is_error": True, "infra_error": True, "result": f"{type(err).__name__}: {err}"[:500],
                    "booked_cost": {"usd": round(sum(spent), 8), "source": "logged_sub_calls"} if spent else None}
        if out.get("timed_out") and not out.get("modelUsage") and not out.get("booked_cost"):
            out["booked_cost"] = {"usd": float(ctx.cap_usd), "source": "upper_bound"}
        return out

    def _log_call(self, item_id: str, row: Mapping) -> None:
        with self.lock:
            llm.append_jsonl(self.run_dir / "calls.jsonl", {"run_id": self.run_id, "item": item_id, **row})

    def _status(self, out: dict) -> str:
        """The status `call` set, or `model_mismatch` when run_jobs flagged the answering model after `call` had
        classified the output; the corrected status is stored in `out`, so `save` writes it."""
        out["status"] = "model_mismatch" if out.get("model_mismatch") else out.get("status") or classify(out, self.cfg)
        return out["status"]

    def outcome(self, job, out: dict) -> str:
        status = self._status(out)
        return "infra_error" if status in INFRA else "ok" if status == "ok" else "system_failure"

    def save(self, job: tuple[int, str], out: Mapping, at: str) -> None:
        _, item_id = job
        item = self.items[item_id]
        raw = self.raw_dir / self.run_id
        raw.mkdir(parents=True, exist_ok=True)
        answer = str(out.get("result") or "")
        (raw / f"{out['task']}.answer.txt").write_text(answer, encoding="utf-8")
        trace = raw / f"{out['task']}.jsonl"
        native = dict(out.get("native") or {})
        for cand in native.get("candidates") or []:
            cand.pop("why", None)
        if "searches" in native:  # the model-written queries stay in the trace; the row keeps their count
            searches = native["searches"]
            native["searches"] = len(searches) if isinstance(searches, list) else int(searches or 0)
        status = self._status(out)
        usage = llm.usage_totals(out)
        usd, cost_source = booked_usd(out)
        row = {"format": OUTPUT_FORMAT, "run_id": self.run_id, "system": self.spec.system,
               "condition": self.spec.condition, "tier": self.spec.tier, "subset": self.spec.subset,
               "kind": self.spec.kind, "item": item_id, "task": out["task"], "claim_date": item["claim"]["date"],
               "config_sha256": self.config_sha256, "view_sha256": out["view_sha256"], "at": at,
               "cli": (self.cfg.get("cli") or {}).get("version"), "status": status, "failure": status != "ok",
               "attempts": out.get("attempts", 1), "native": native,
               "caps": {"usd_item": self.cfg.get("per_item_cap_usd"), "max_turns": self.cfg.get("max_turns"),
                        "search_cap": self.cfg.get("search_cap"), "fetch_cap": self.cfg.get("fetch_cap"),
                        "searches_exceeded": bool(self.cfg.get("search_cap")) and
                        int(native.get("searches") or 0) > int(self.cfg.get("search_cap") or 0),
                        "fetches_exceeded": bool(self.cfg.get("fetch_cap")) and
                        int(native.get("fetches") or 0) > int(self.cfg.get("fetch_cap") or 0)},
               "cost": {"usd": usd, "cost_source": cost_source, "tokens_all": usage, "by_model": _by_model(out),
                        "web_searches": llm.web_searches(out), "llm_calls": native.get("llm_calls"),
                        "openalex_nominal": native.get("openalex"), "num_turns": out.get("num_turns"),
                        "seconds": out.get("wall_seconds")},
               "raw": {"answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                       "trace_path": str(trace.relative_to(REPO_ROOT)) if trace.is_relative_to(REPO_ROOT) else str(trace),
                       "trace_sha256": _sha256_file(trace) if trace.exists() else None}}
        llm.append_jsonl(self.run_dir / "outputs.jsonl", row)


def _git_commit() -> str | None:
    try:
        return config_mod._git(REPO_ROOT, "rev-parse", "HEAD").strip() or None
    except config_mod.ConfigError:
        return None


def _write_manifest(run_dir: Path, manifest: Mapping) -> None:
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def execute(run: Run, *, workers: int = 2) -> dict:
    """Run the pending items of `run` and write run.json; returns its summary. run.json is written without
    `finished` before the first job and rewritten at the end, also when the run aborts, an exception escapes or the
    run is interrupted (then `aborted` says so). On resume `started` keeps the first start and `resumed` lists the
    later ones."""
    run.run_dir.mkdir(parents=True, exist_ok=True)
    path = run.run_dir / "run.json"
    prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now = llm.utc_stamp()
    manifest = {"format": RUN_FORMAT, "run_id": run.run_id, "system": run.spec.system,
                "condition": run.spec.condition, "tier": run.spec.tier, "subset": run.spec.subset,
                "kind": run.spec.kind, "item_ids": list(run.spec.items), "config_sha256": run.config_sha256,
                "config_file_sha256": run.config_file_sha256,
                "splits_sha256": (_sha256_file(run.splits_path)
                                  if run.spec.kind == "scored" and run.splits_path.exists() else None),
                "line": budget_mod.PILOT_LINE if run.spec.kind == "pilot" else run.cfg.get("line"),
                "cap_usd": run.spec.cap_usd, "cli": (run.cfg.get("cli") or {}).get("version"),
                "git_commit": _git_commit(), "started": prior.get("started") or now,
                "resumed": list(prior.get("resumed") or []) + ([now] if prior else [])}
    _write_manifest(run.run_dir, manifest)
    todo = pending(run.run_dir, list(run.spec.items))
    jobs = [(n, item_id) for n, item_id in enumerate(run.spec.items, 1) if item_id in todo]
    expected = tuple([run.cfg["model"], *(run.cfg.get("aux_models") or [])]) if run.cfg.get("model") else None
    summary = {"spent_usd": None, "aborted": "interrupted: the run stopped before run_jobs returned"}
    try:
        summary = llm.run_jobs(jobs, run.call, run.save, ledger_path=run.run_dir / "ledger.jsonl",
                               cap_usd=run.spec.cap_usd, workers=min(workers, MAX_WORKERS),
                               call_cap_usd=float(run.cfg.get("per_item_cap_usd") or 0),
                               describe=lambda job: {"run_id": run.run_id, "system": run.spec.system,
                                                     "condition": run.spec.condition, "tier": run.spec.tier,
                                                     "subset": run.spec.subset, "item": job[1], "kind": run.spec.kind,
                                                     "config_sha256": run.config_sha256,
                                                     "model": run.cfg.get("model")},
                               expected_model=(lambda job: expected) if expected else None, outcome=run.outcome)
    finally:
        last = {}
        for row in _jsonl(run.run_dir / "outputs.jsonl"):
            last[row["item"]] = row["status"]
        by_status = {}
        for status in last.values():
            by_status[status] = by_status.get(status, 0) + 1
        spent = summary["spent_usd"]
        if spent is None:
            spent = round(sum(float(r.get("cost_usd") or 0.0) for r in _jsonl(run.run_dir / "ledger.jsonl")), 4)
        manifest["finished"] = llm.utc_stamp()
        manifest["summary"] = {"items": len(run.spec.items), "by_status": by_status, "spent_usd": spent,
                               "aborted": summary["aborted"],
                               "pending": len(pending(run.run_dir, list(run.spec.items)))}  # to run on resume
        _write_manifest(run.run_dir, manifest)
    return manifest["summary"]
