"""System configs for measurement A (docs/design/harness.md sections 8.1 and 8.2; eval/protocol.md decisions 3,
10 and 12): canonical config files, prompt and code hashes, league checks and the append-only hash log.

A config file must be exactly `canonical_bytes` of its content, so `shasum -a 256` of the file is the logged hash.
`log` appends one row to config-hashes.jsonl before the system's first scored run; `frozen` returns a config only
while the config file, its prompts and the code that shapes the system's inputs and outputs still hash to that row,
and only once config-hashes.jsonl is committed and pushed (spec 8.2). Every refusal raises ConfigError.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..benchmark.items import RELATIONS
from ..env import REPO_ROOT
from ..llm import append_jsonl, read_jsonl, utc_stamp
from .labels import CLASS3, RULES

MEASUREMENT_A = REPO_ROOT / "eval" / "results" / "measurement-a"
SCHEMA = "coelacanth-system-config/v1"
REIMPL_ADAPTERS = ("inc_style", "opennovelty_style", "ideacatalyst_style")
LEAGUE_ADAPTERS = {"claude-code-agents": ("agent",), "reimpl": REIMPL_ADAPTERS}
# Spec 8.1: members of a league are equal on these fields (dotted paths); everything else is the method.
LEAGUE_FIELDS = {"claude-code-agents": ("model", "effort", "aux_models", "tools", "max_turns", "search_cap",
                                        "fetch_cap", "per_item_cap_usd", "timeout_s", "top_k",
                                        "prompts.contract.sha256", "cli.version"),
                 "reimpl": ("model", "effort", "retrieval", "cli.version")}
SUBSETS = ("ALL", "H", "H_any", "H_post", "H_half", "R", "R_half", "R_recalled", "P40", "P20", "C", "A", "N", "N_built", "K",
           "ALL_minus_H")
CONDITIONS = ("text", "skeleton")
# Keys every config has (the value may be null where spec 8.1 shows null), then those of the systems run through
# the CLI. Zero is the placeholder the pilots replace before the hash, so the POSITIVE keys must be above zero.
REQUIRED = ("schema", "system", "row_label", "disclosure", "adapter", "league", "allowed_subsets", "conditions",
            "label_map", "probability", "line", "price_date", "tuning")
_CLI_RUN = ("model", "effort", "models_called", "aux_models", "temperature", "cli", "tools", "per_item_cap_usd",
            "timeout_s", "top_k", "prompts", "retrieval", "native")
REQUIRED_BY_ADAPTER = {"agent": _CLI_RUN + ("variant", "max_turns", "search_cap", "fetch_cap",
                                            "answer_after_n_searches"),
                       **{a: _CLI_RUN + ("steps", "clean_room", "primad") for a in REIMPL_ADAPTERS}}
POSITIVE = ("max_turns", "search_cap", "fetch_cap", "per_item_cap_usd", "timeout_s", "top_k")
# Spec 8.2: the modules that shape a system's inputs and outputs. Re-implementations add the retrieval and
# re-implementation framework and their own module, harness/<adapter>.py.
AGENT_CODE = ("src/coelacanth/llm.py", "src/coelacanth/pricing.py", "src/coelacanth/priorart.py",
              "src/coelacanth/harness/run.py", "src/coelacanth/harness/agents.py")
REIMPL_CODE = ("src/coelacanth/harness/retrieval.py", "src/coelacanth/harness/reimpl.py")
PINNED_SOURCES = ("coelacanth.priorart.SINGLE_SYSTEM", "coelacanth.priorart.hunch_system",
                  "coelacanth.priorart.prior_art_system")
_MISSING = "<missing>"


class ConfigError(ValueError):
    """A config that cannot be loaded, logged or used as frozen."""


def canonical_bytes(cfg: Mapping) -> bytes:
    return (json.dumps(cfg, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return _sha256(path.read_bytes())
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist, so it cannot be hashed") from None


def load(system: str, configs_dir: Path = MEASUREMENT_A / "configs") -> tuple[dict, str]:
    """The config of `system` and the SHA-256 of its file; a file that is not canonical is refused."""
    path = configs_dir / f"{system}.json"
    raw = path.read_bytes()
    cfg = json.loads(raw.decode("utf-8"))
    if canonical_bytes(cfg) != raw:
        raise ConfigError(f"{path} is not canonical: write it with config.canonical_bytes (sorted keys, indent 1, "
                          f"UTF-8, one final newline)")
    if cfg.get("system") != system:
        raise ConfigError(f"{path} holds the config of {cfg.get('system')!r}, not {system!r}")
    return cfg, _sha256(raw)


def _prompt_entries(cfg: Mapping) -> dict[str, Mapping]:
    """Every prompt the config names: `prompts` by name, and each re-implementation step's prompt as `steps.<name>`."""
    entries = dict(cfg.get("prompts") or {})
    entries.update({f"steps.{step.get('name')}": step.get("prompt") or {} for step in cfg.get("steps") or []})
    return entries


def _source_text(source: str, cfg: Mapping) -> str:
    """The text of a prompt pinned in coelacanth.priorart, imported here so that tests can patch it. Hunch's pinned
    commit and file hashes must be priorart's (whose `_git_show` checks the bytes); the prior-art skill's cached copy
    must have the SHA-256 the config pins."""
    if source not in PINNED_SOURCES:
        raise ConfigError(f"prompt source {source!r} is not one of {PINNED_SOURCES}")
    from .. import priorart
    pinned = cfg.get("pinned") or {}
    if source.endswith(".hunch_system") and (pinned.get("commit") != priorart.HUNCH_COMMIT
                                             or pinned.get("files") != priorart.HUNCH_FILES):
        raise ConfigError(f"pinned commit and file hashes {pinned.get('commit')}, {pinned.get('files')} differ "
                          f"from coelacanth.priorart's {priorart.HUNCH_COMMIT}, {priorart.HUNCH_FILES}")
    if source.endswith(".prior_art_system"):
        digest = _file_sha256(priorart.PRIOR_ART_SKILL)
        if pinned.get("sha256") != digest:
            raise ConfigError(f"{priorart.PRIOR_ART_SKILL.name} has SHA-256 {digest}, the config pins "
                              f"{pinned.get('sha256')}")
    value = getattr(priorart, source.rsplit(".", 1)[1])
    return value() if callable(value) else value


def prompt_hashes(cfg: Mapping, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """SHA-256 of every prompt the config names, recomputed now: a `path` entry by its file's bytes, a `source`
    entry by the UTF-8 text the pinned source gives."""
    out = {}
    for name, entry in _prompt_entries(cfg).items():
        if "path" in entry and "source" not in entry:
            out[name] = _file_sha256(repo_root / entry["path"])
        elif "source" in entry and "path" not in entry:
            out[name] = _sha256(_source_text(entry["source"], cfg).encode("utf-8"))
        else:
            raise ConfigError(f"prompt {name!r} needs exactly one of `path` and `source`")
    return out


def code_files(cfg: Mapping) -> tuple[str, ...]:
    adapter = cfg.get("adapter")
    if adapter == "agent":
        return AGENT_CODE
    if adapter in REIMPL_ADAPTERS:
        return AGENT_CODE + REIMPL_CODE + (f"src/coelacanth/harness/{adapter}.py",)
    raise ConfigError(f"spec 8.2 lists the code of agents and re-implementations only, not of adapter {adapter!r}")


def code_hashes(cfg: Mapping, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    return {rel: _file_sha256(repo_root / rel) for rel in code_files(cfg)}


def _label_map_problems(cfg: Mapping) -> list[str]:
    label_map = cfg.get("label_map") or {}
    rule = label_map.get("rule")
    labels = (cfg.get("native") or {}).get("labels") or []
    if rule not in RULES:
        return [f"label_map.rule {rule!r} is not one of {RULES}"]
    if rule == "precedence_over_candidates":
        return [f"native label {label!r} is not one of the six relations" for label in labels
                if label not in RELATIONS]
    if rule == "none":
        return []
    mapping = label_map.get("map") or {}
    out = [] if labels else ["native.labels is empty, so every output would be unmappable"]
    out += [f"native label {label!r} has no entry in label_map.map" for label in labels if label not in mapping]
    out += [f"label_map.map maps {label!r}, which is not a native label" for label in mapping if label not in labels]
    for label, target in mapping.items():
        target = target if isinstance(target, Mapping) else {}
        six, class3 = target.get("six"), target.get("class3")
        if six is not None:
            out.append(f"label_map.map[{label!r}].six is {six!r}; native_item_label systems are coarse and get six "
                       f"null, so they enter the three-class metrics only (protocol decision 32)")
        elif class3 not in CLASS3:
            out.append(f"label_map.map[{label!r}].class3 {class3!r} is not one of {CLASS3}")
    return out


def _tuning_problems(tuning: Mapping, pilot_items: Path) -> list[str]:
    pilots = {row.get("id"): row for row in read_jsonl(pilot_items)}
    out = []
    for item in tuning.get("items") or []:
        row = pilots.get(item)
        if row is None:
            out.append(f"tuning item {item!r} is not in {pilot_items.name}; tuning happens on pilot items only")
        elif (row.get("provenance") or {}).get("source") != "guide" or row.get("status") != "excluded":
            out.append(f"pilot item {item!r} is not an excluded LABELING.md worked example")
    if tuning.get("complete") is not True:
        out.append("tuning.complete is not true")
    return out


def _models_called_problems(cfg: Mapping) -> list[str]:
    """`models_called` defines H with the other frozen configs (decision 8; run.preflight checks it against the
    splits file's `models_for_H`), so it must name every model the run accepts: `model` and each of `aux_models`
    (run.execute passes exactly these to llm.run_jobs). Services that do not disclose their models write
    "undisclosed"."""
    called = cfg.get("models_called")
    if "models_called" not in cfg or called == "undisclosed":
        return []
    if not isinstance(called, list):
        return [f"models_called is {called!r}, neither a list of model IDs nor 'undisclosed'"]
    accepted = ([cfg["model"]] if cfg.get("model") else []) + list(cfg.get("aux_models") or [])
    missing = [m for m in accepted if m not in called]
    return [f"models_called {called} omits {missing}, which the run accepts (model and aux_models), so H would not "
            f"cover them (decision 8)"] if missing else []


def problems(cfg: Mapping, pilot_items: Path = MEASUREMENT_A / "pilot-items.jsonl") -> list[str]:
    """What keeps a config from being logged: missing fields, placeholder zeros, unknown subsets, conditions or
    league, models the run accepts but `models_called` omits, an incomplete label map, and tuning that is unfinished
    or not on pilot items."""
    adapter = cfg.get("adapter")
    required = REQUIRED + REQUIRED_BY_ADAPTER.get(adapter, ())
    out = [f"{key} is missing" for key in required if key not in cfg]
    if "schema" in cfg and cfg["schema"] != SCHEMA:
        out.append(f"schema is {cfg['schema']!r}, not {SCHEMA!r}")
    for key in POSITIVE:
        value = cfg.get(key)
        if key in required and key in cfg and not (isinstance(value, (int, float)) and value > 0):
            out.append(f"{key} is {value!r}; zero is a placeholder the pilots replace before the hash")
    subsets = cfg.get("allowed_subsets")
    if not subsets:
        out.append("allowed_subsets is empty")
    out += [f"subset {s!r} is not one of {SUBSETS}" for s in subsets or [] if s not in SUBSETS]
    out += [f"condition {c!r} is not one of {CONDITIONS}" for c in cfg.get("conditions") or [] if c not in CONDITIONS]
    league = cfg.get("league")
    if league is not None and league not in LEAGUE_ADAPTERS:
        out.append(f"league {league!r} is not one of {tuple(LEAGUE_ADAPTERS)} or null")
    elif league is not None and adapter not in LEAGUE_ADAPTERS[league]:
        out.append(f"adapter {adapter!r} cannot join league {league!r}")
    if adapter in REIMPL_ADAPTERS and league != "reimpl":
        out.append(f"re-implementation adapter {adapter!r} must be in league 'reimpl'")
    out += _models_called_problems(cfg)
    out += _label_map_problems(cfg)
    if isinstance(cfg.get("tuning"), Mapping):
        out += _tuning_problems(cfg["tuning"], pilot_items)
    return out


def _field(cfg: Mapping, dotted: str) -> Any:
    value: Any = cfg
    for key in dotted.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return _MISSING
        value = value[key]
    return value


def league_problems(cfgs: Iterable[Mapping]) -> list[str]:
    """Fields on which members of one league differ (spec 8.1). Configs with no league are not checked."""
    leagues: dict[str, list[Mapping]] = defaultdict(list)
    for cfg in cfgs:
        if cfg.get("league") in LEAGUE_FIELDS:
            leagues[cfg["league"]].append(cfg)
    out = []
    for league, members in sorted(leagues.items()):
        for dotted in LEAGUE_FIELDS[league]:
            values = {str(m.get("system")): _field(m, dotted) for m in members}
            if len({json.dumps(v, sort_keys=True) for v in values.values()}) > 1:
                out.append(f"league {league}: {dotted} differs: {json.dumps(values, sort_keys=True)}")
    return out


def _git(repo_root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    proc = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True,
                          env={**env, "GIT_OPTIONAL_LOCKS": "0"})
    if proc.returncode != 0:
        raise ConfigError(f"git {' '.join(args[:2])} failed in {repo_root}: {proc.stderr.strip()}")
    return proc.stdout


def _uncommitted(paths: Iterable[Path], repo_root: Path) -> list[str]:
    """The paths among `paths` that `git status --porcelain` lists: modified, staged, untracked or deleted."""
    root = repo_root.resolve()
    try:
        rel = [str(p.resolve().relative_to(root)) for p in paths]
    except ValueError as err:
        raise ConfigError(f"a hashed file lies outside the repository {root}: {err}") from None
    status = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *rel)
    return [line[3:] for line in status.splitlines() if line.strip()]


def _unpushed(path: Path, repo_root: Path) -> list[str]:
    """Commits that touch `path` and are not on the branch's upstream. The upstream is the local remote-tracking ref,
    so no network call is made; a branch without one cannot show that anything was pushed and is refused."""
    root = repo_root.resolve()
    rel = str(path.resolve().relative_to(root))
    try:
        upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").strip()
    except ConfigError:
        raise ConfigError(f"the branch checked out in {root} has no upstream, so {rel} cannot be shown to be pushed "
                          f"(spec 8.2)") from None
    return _git(root, "log", "--format=%h", f"{upstream}..HEAD", "--", rel).split()


def log(system: str, *, root: Path = MEASUREMENT_A, repo_root: Path = REPO_ROOT) -> dict:
    """Append the config's hashes to config-hashes.jsonl (spec 8.2) and return the row. Refused when the config has
    problems, its league has mismatches, a recorded prompt hash is stale, a hashed file has uncommitted changes, or
    scored outputs already exist for the system under another config hash."""
    configs_dir = root / "configs"
    cfg, sha = load(system, configs_dir)
    refusals = problems(cfg, root / "pilot-items.jsonl")
    if cfg.get("league") is not None:
        league = [c for c, _ in (load(p.stem, configs_dir) for p in sorted(configs_dir.glob("*.json")))
                  if c.get("league") == cfg["league"]]
        refusals += league_problems(league)
    if refusals:
        raise ConfigError(f"{system} not logged: " + "; ".join(refusals))
    prompts = prompt_hashes(cfg, repo_root)
    code = code_hashes(cfg, repo_root)
    entries = _prompt_entries(cfg)
    refusals += [f"prompt {name}: the config records {entries[name].get('sha256')}, it hashes to {digest}"
                 for name, digest in prompts.items() if entries[name].get("sha256") != digest]
    hashed = [configs_dir / f"{system}.json"] + [repo_root / e["path"] for e in entries.values() if "path" in e]
    dirty = _uncommitted(hashed + [repo_root / rel for rel in code], repo_root)
    if dirty:
        refusals.append(f"uncommitted changes in {', '.join(dirty)}")
    scored = {row.get("config_sha256") for path in sorted((root / "runs" / system).glob("*/*/outputs.jsonl"))
              for row in read_jsonl(path)}
    other = sorted(str(h) for h in scored - {sha})
    if other:
        refusals.append(f"scored outputs of {system} exist under another config hash: {other}")
    if refusals:
        raise ConfigError(f"{system} not logged: " + "; ".join(refusals))
    row = {"at": utc_stamp(), "system": system, "config_sha256": sha, "prompts": prompts, "code": code,
           "git_commit": _git(repo_root, "rev-parse", "HEAD").strip(),
           "cli_version": (cfg.get("cli") or {}).get("version")}
    path = root / "config-hashes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(path, row)
    return row


def frozen(system: str, *, root: Path = MEASUREMENT_A, repo_root: Path = REPO_ROOT) -> tuple[dict, str]:
    """The config and its hash, only if config-hashes.jsonl is committed and pushed (spec 8.2: the commit is the
    freeze's evidence, decision 3) and the config file, its prompts and its code hash as in the system's last row of
    it."""
    cfg, sha = load(system, root / "configs")
    hashes = root / "config-hashes.jsonl"
    rows = [r for r in read_jsonl(hashes) if r.get("system") == system]
    if not rows:
        raise ConfigError(f"{system} has no row in config-hashes.jsonl; log its config first")
    if _uncommitted([hashes], repo_root):
        raise ConfigError("config-hashes.jsonl has uncommitted changes; commit and push it before a scored run "
                          "(spec 8.2)")
    unpushed = _unpushed(hashes, repo_root)
    if unpushed:
        raise ConfigError(f"config-hashes.jsonl changed in commits not pushed yet ({', '.join(unpushed)}); push "
                          f"them before a scored run (spec 8.2)")
    last = rows[-1]
    changed = [] if last.get("config_sha256") == sha else ["the config file"]
    for kind, now, then in (("prompt", prompt_hashes(cfg, repo_root), last.get("prompts") or {}),
                            ("code", code_hashes(cfg, repo_root), last.get("code") or {})):
        changed += [f"{kind} {name}" for name in sorted(set(now) | set(then)) if now.get(name) != then.get(name)]
    if changed:
        raise ConfigError(f"{system} changed since its hash was logged at {last.get('at')}: {', '.join(changed)}")
    return cfg, sha
