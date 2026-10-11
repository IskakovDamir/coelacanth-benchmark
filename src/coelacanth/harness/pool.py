"""Pooling rounds for judging (eval/protocol.md section 12, decision 17; docs/design/harness.md section 8.10).

For each item, the top `depth` cleaned candidates (`rank_clean`) of every scored, logged run join the pool, with
`skeleton_depth` for skeleton-condition runs, plus the works named in closed-book answers (decision 1). A round is
written to `eval/results/measurement-a/pool/round<k>/`:
- `pool.jsonl`, what judges get: `{"item", "work"}` per pooled work, the work as SCHEMA.md's work object with its
  abstract cached, sorted by item and then by OpenAlex ID (numerically). No system, rank, count, relation, rationale
  or H membership.
- `pool-meta.jsonl`, hidden from judges: who found each work (`found_by` tags in SCHEMA.md's form) and, when the work
  already is one of the item's candidates, that candidate's index (`matched_existing_candidate`). Such works are
  judged through the item's own annotation and are left out of `pool.jsonl`.
- `exclusions.jsonl`: every candidate the cleaning removed (`unresolved`, `excluded_work`, `leakage`, `duplicate`,
  in that order of precedence; placeholders are `unresolved`), counted per item in `pool-manifest.json`, with 20 of
  them drawn for a person to check in `exclusion-sample.json`.

Only runs whose `run.json` says `kind: scored` and whose config hash is logged in `config-hashes.jsonl` are pooled;
pilots never are. Kulikowski's closed-pool rankings have no `resolved.jsonl` and are not pooled (section 13, point 9).
Only valid lists are pooled (protocol 3 and 12, decision 33): a row still due for a re-run (`infra_error`, which
includes a failed resolution, or `model_mismatch`) refuses the round, a row with a final system failure is skipped
and counted per run as `skipped_failures`, and a second run of one system and condition on the same item is refused.
One work found under two keys (an OpenAlex W ID in one run, the arXiv ID of the same preprint in another) is judged
once, with every `found_by` tag; the merges are counted per item as `n_merged`. Work lookups by ID are free OpenAlex
entity calls.

`found_by` tags: `sys:<system>@<rank_clean>` for the field-native condition, `sys:<system>.<condition>@<rank_clean>`
otherwise (`sys:inc-style.skeleton@7`), `mining:closedbook` for closed-book works.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping

from ..benchmark import texts
from ..benchmark.items import FOUND_BY_RE
from ..data.openalex import OpenAlexClient
from ..env import REPO_ROOT
from ..llm import read_jsonl

MEASUREMENT_A = REPO_ROOT / "eval" / "results" / "measurement-a"
CLOSED_BOOK_TAG = "mining:closedbook"
REASONS = ("unresolved", "excluded_work", "leakage", "duplicate")
RERUN = ("infra_error", "model_mismatch")  # run.INFRA; a failed resolution is written as infra_error too
W_RE = re.compile(r"W\d+")


def found_by(system: str, condition: str, rank: int) -> str:
    """The SCHEMA.md `found_by` tag of a pooled candidate; the condition is a suffix unless it is `text`."""
    tag = f"sys:{system}@{rank}" if condition == "text" else f"sys:{system}.{condition}@{rank}"
    if "." in system or not isinstance(rank, int) or not FOUND_BY_RE.fullmatch(tag):
        raise ValueError(f"no found_by tag for system {system!r}, condition {condition!r}, rank {rank!r}")
    return tag


def _openalex(value) -> str | None:
    w = str(value or "").rsplit("/", 1)[-1].upper()
    return w if W_RE.fullmatch(w) else None


def _work_key(c: Mapping) -> str:
    key = _openalex(c.get("openalex")) or (f"arXiv:{c['arxiv']}" if c.get("arxiv") else None)
    if key is None:
        raise ValueError(f"resolved candidate at rank_raw {c.get('rank_raw')} has no OpenAlex or arXiv ID")
    return key


def _order(key: str) -> tuple:
    return (0, int(key[1:]), "") if W_RE.fullmatch(key) else (1, 0, key)


def _ids(work: Mapping) -> set[str]:
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", str(work.get("doi") or "").strip().lower())
    return ({_openalex(work.get("openalex"))} | ({f"doi:{doi}"} if doi else set())
            | ({f"arxiv:{str(work['arxiv']).lower()}"} if work.get("arxiv") else set())) - {None}


def _existing(item: Mapping, work: Mapping) -> int | None:
    keys = _ids(work)
    return next((i for i, c in enumerate(item.get("candidates") or []) if keys & _ids(c.get("work") or {})), None)


def exclusion_reason(c: Mapping) -> str | None:
    """Why the cleaning removed a resolved candidate, or None when it was kept."""
    if c.get("placeholder") or c.get("resolved_by") in (None, "none"):
        return "unresolved"
    if c.get("leak") == "excluded_work":
        return "excluded_work"
    if c.get("leak"):
        return "leakage"
    if c.get("duplicate_of") is not None:
        return "duplicate"
    return None


def pool_work(key: str, client: OpenAlexClient) -> dict:
    """SCHEMA.md's work object of one pooled work, with its abstract cached for the judges."""
    if key.startswith("arXiv:"):
        return texts.arxiv_work(key[len("arXiv:"):], client.cache)[0]
    work = client.work(key, select=texts.WORK_SELECT)
    return texts.work_object(work, texts.cache_abstract(work))


def _load_run(run_dir: Path, logged: set[tuple[str, str]]) -> dict:
    path = run_dir / "run.json"
    if not path.exists():
        raise ValueError(f"{run_dir}: no run.json, so nothing shows it is a scored run")
    run = json.loads(path.read_text(encoding="utf-8"))
    if run.get("kind") != "scored":
        raise ValueError(f"{run_dir}: kind {run.get('kind')!r}; only scored runs are pooled, pilots never")
    if (run.get("system"), run.get("config_sha256")) not in logged:
        raise ValueError(f"{run_dir}: config {run.get('config_sha256')} of {run.get('system')} is not in "
                         "config-hashes.jsonl")
    if not (run_dir / "resolved.jsonl").exists():
        raise ValueError(f"{run_dir}: no resolved.jsonl; run resolve first")
    ident = (run["run_id"], run["system"], run["condition"], run["config_sha256"])
    rows = read_jsonl(run_dir / "resolved.jsonl")
    for row in rows:
        if (row.get("run_id"), row.get("system"), row.get("condition"), row.get("config_sha256")) != ident:
            raise ValueError(f"{run_dir}: resolved row for {row.get('item')} does not match run.json")
    return {"run_id": ident[0], "system": ident[1], "condition": ident[2], "config_sha256": ident[3], "rows": rows}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def build_pool(round_: int, run_dirs: Iterable[str | Path], items: Iterable[Mapping], *, depth: int = 10,
               skeleton_depth: int = 10, closed_book_works: Iterable[tuple[str, str]] = (),
               client: OpenAlexClient | None = None, root: Path = MEASUREMENT_A) -> dict:
    """Write pooling round `round_` from the runs' `resolved.jsonl` files and return what was written.
    `closed_book_works` holds `(item ID, OpenAlex ID)` pairs already resolved and dated before the claim date.
    Refuses pilot and unlogged runs, rows of items not in `items`, rows due for a re-run, a second run of one system
    and condition on an item, and an existing round. Rows with a final system failure are not pooled."""
    root = Path(root)
    out_dir = root / "pool" / f"round{round_}"
    if (out_dir / "pool.jsonl").exists():
        raise FileExistsError(f"{out_dir} already holds a pool; a round is never rewritten")
    by_id = {item["id"]: item for item in items}
    logged = {(r.get("system"), r.get("config_sha256")) for r in read_jsonl(root / "config-hashes.jsonl")}
    runs = sorted((_load_run(Path(d), logged) for d in run_dirs), key=lambda r: r["run_id"])
    if len({r["run_id"] for r in runs}) != len(runs):
        raise ValueError("a run is listed twice")

    found: dict[str, dict[str, list[str]]] = defaultdict(dict)
    removed: dict[str, Counter] = defaultdict(Counter)
    seen, exclusions, answered, skipped = set(), [], {}, Counter()
    for run in runs:
        limit = skeleton_depth if run["condition"] == "skeleton" else depth
        for row in run["rows"]:
            item_id = row["item"]
            if item_id not in by_id:
                raise ValueError(f"{run['run_id']}: item {item_id} is not among the items given")
            if row.get("status") in RERUN or row.get("resolution_error"):
                why = f"resolution failed ({row['resolution_error']})" if row.get("resolution_error") else \
                    f"status {row.get('status')}"
                raise ValueError(f"{run['run_id']} {item_id}: {why}; re-run it before pooling (decision 33)")
            other = answered.setdefault((run["system"], run["condition"], item_id), run["run_id"])
            if other != run["run_id"]:
                raise ValueError(f"{run['system']} ({run['condition']}) answered {item_id} twice, in {other} and "
                                 f"{run['run_id']}; a system failure is never resampled (decision 33)")
            seen.add(item_id)
            if row.get("status") != "ok" or row.get("failure"):  # a final system failure: no valid list to pool
                skipped[run["run_id"]] += 1
                continue
            for c in row.get("candidates") or []:
                reason = exclusion_reason(c)
                if reason:
                    removed[item_id][reason] += 1
                    exclusions.append({"item": item_id, "run_id": run["run_id"], "system": run["system"],
                                       "condition": run["condition"], "rank_raw": c.get("rank_raw"),
                                       "reason": reason, "leak": c.get("leak"), "openalex": c.get("openalex"),
                                       "doi": c.get("doi"), "arxiv": c.get("arxiv"), "date": c.get("date")})
                    continue
                rank = c.get("rank_clean")
                if not isinstance(rank, int):
                    raise ValueError(f"{run['run_id']} {item_id}: kept candidate {c.get('rank_raw')} has no rank_clean")
                if rank <= limit:
                    tags = found[item_id].setdefault(_work_key(c), [])
                    tags.append(found_by(run["system"], run["condition"], rank))
    closed_book_works = list(closed_book_works)
    for item_id, wid in closed_book_works:
        if item_id not in by_id or _openalex(wid) is None:
            raise ValueError(f"closed-book work {wid!r} of item {item_id!r}: unknown item or not an OpenAlex ID")
        tags = found[item_id].setdefault(_openalex(wid), [])
        if CLOSED_BOOK_TAG not in tags:
            tags.append(CLOSED_BOOK_TAG)
    exclusions.sort(key=lambda e: (e["item"], e["run_id"], e["rank_raw"] or 0))

    client = client or OpenAlexClient()
    pool_rows, meta_rows, per_item = [], [], {}
    for item_id in sorted(seen | set(found)):
        n_pooled = n_matched = n_merged = 0
        emitted: dict[str, dict] = {}  # every identifier of a work already written for this item -> its meta row
        for key in sorted(found.get(item_id, {}), key=_order):  # W IDs first, so the OpenAlex record is kept
            work = pool_work(key, client)
            prior = next((emitted[k] for k in sorted(_ids(work)) if k in emitted), None)
            if prior is not None:  # the same work under another key (a W ID and an arXiv fallback): judged once
                prior["found_by"].extend(t for t in found[item_id][key] if t not in prior["found_by"])
                n_merged += 1
                continue
            match = _existing(by_id[item_id], work)
            meta_rows.append({"item": item_id, "openalex": work.get("openalex"), "arxiv": work.get("arxiv"),
                              "found_by": list(found[item_id][key]), "matched_existing_candidate": match})
            emitted.update(dict.fromkeys(_ids(work), meta_rows[-1]))
            if match is None:
                pool_rows.append({"item": item_id, "work": work})
                n_pooled += 1
            else:
                n_matched += 1
        per_item[item_id] = {"n_pooled": n_pooled, "n_matched_existing": n_matched, "n_merged": n_merged,
                             "auto_removed": {r: removed[item_id][r] for r in REASONS if removed[item_id][r]}}
    manifest = {"round": round_, "depth": depth, "skeleton_depth": skeleton_depth,
                "runs": [{**{k: r[k] for k in ("run_id", "system", "condition", "config_sha256")},
                          "skipped_failures": skipped[r["run_id"]]} for r in runs],
                "closed_book_works": len(closed_book_works), "per_item": per_item}
    result = {"dir": str(out_dir), "pool": pool_rows, "meta": meta_rows, "exclusions": exclusions,
              "manifest": manifest}
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "pool.jsonl", pool_rows)
    _write_jsonl(out_dir / "pool-meta.jsonl", meta_rows)
    _write_jsonl(out_dir / "exclusions.jsonl", exclusions)
    _write_json(out_dir / "exclusion-sample.json", exclusion_sample(result))
    _write_json(out_dir / "pool-manifest.json", manifest)
    return result


def exclusion_sample(pool: Mapping, n: int = 20, seed: int = 20261006) -> list[dict]:
    """`n` automatic exclusions for a person to check, drawn with `random.Random("<seed>:auto_exclusions")`."""
    rows = sorted(pool["exclusions"], key=lambda e: (e["item"], e["run_id"], e["rank_raw"] or 0))
    return random.Random(f"{seed}:auto_exclusions").sample(rows, min(n, len(rows)))
