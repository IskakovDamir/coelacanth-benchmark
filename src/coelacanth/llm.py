"""Model calls through the Claude Code CLI in headless mode, with a per-run ledger and a dollar cap.

Authentication is by API key only (Damir, 2026-09-24): ANTHROPIC_API_KEY from the environment or .env (gitignored) is
passed to the child process, and `--bare` makes the CLI use nothing else (no OAuth, no keychain). Every call runs with
no tools, no MCP servers, customisations off (`--bare` and `--safe-mode`, so no CLAUDE.md, hooks or plugins), a
caller-supplied system prompt, a neutral working directory and the parent session's variables removed from the
child's environment. The ledger books the real cost of each call, computed from its token usage at the official
prices (coelacanth.pricing), next to the CLI's own `total_cost_usd`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from . import env
from .pricing import cli_cost, web_searches

# Default effort per the Anthropic models overview (read 2026-09-24; Sonnet 5.5 from the copy read 2026-10-06); Haiku 4.5
# has no effort setting.
DEFAULT_EFFORT = {"claude-opus-5-5": "medium", "claude-sonnet-5-5": "high", "claude-sonnet-5": "high",
                  "claude-haiku-4-5-20251001": None}
PARENT_VARS = ("CLAUDE_EFFORT", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_AGENT_SDK_VERSION",
               "CLAUDE_PREVIEW_CLASSIFIER_FLOOR", "BAGGAGE", "AI_AGENT")
MAX_CONSECUTIVE_ERRORS = 3
LOW_CREDIT = re.compile(r"credit balance", re.IGNORECASE)  # the API's wording when credits run out
# A model's safeguards declining a request is an outcome, not a system failure: it is recorded as `declined` and does
# not count toward the stop after MAX_CONSECUTIVE_ERRORS failures.
DECLINED = re.compile(r"safeguards flagged|usage polic|acceptable use polic", re.IGNORECASE)


def child_env() -> dict[str, str]:
    """This session's environment without the variables that tie a child process to it (effort, session IDs, OAuth);
    ANTHROPIC_API_KEY passes through, and CLAUDE_CODE_NO_MODEL_FALLBACK=1 stops the CLI from answering with another
    model when the requested one is busy (it did: some Opus 5.5 calls were served by Opus 5)."""
    kept = {k: v for k, v in os.environ.items() if k not in PARENT_VARS and not k.startswith("CLAUDE_CODE_")}
    return {**kept, "CLAUDE_CODE_NO_MODEL_FALLBACK": "1"}


def key_problem(key: str | None) -> str | None:
    """Why a value cannot serve as an API key, or None. Only the prefix is inspected; the key is never shown."""
    if not key:
        return f"ANTHROPIC_API_KEY is neither in the environment nor in {env.REPO_ROOT / '.env'}"
    if key.startswith("sk-ant-oat"):
        return ("ANTHROPIC_API_KEY holds a subscription OAuth token (prefix sk-ant-oat), not an API key "
                "(prefix sk-ant-api); create an API key in the Anthropic Console")
    return None


def auth_problem() -> str | None:
    """None when an API key is available (environment or the repo's .env); runs use API-key auth only."""
    env.load_dotenv()
    return key_problem(os.environ.get("ANTHROPIC_API_KEY"))


def claude_bin() -> str:
    """The Claude Code CLI to run: COELACANTH_CLAUDE_BIN (environment or .env), else `claude` on the PATH. Opus 5.5 and
    Sonnet 5 need CLI 2.1.280 or newer; the desktop app bundles one."""
    env.load_dotenv()
    return os.environ.get("COELACANTH_CLAUDE_BIN") or "claude"


def cli_version() -> str:
    return subprocess.run([claude_bin(), "--version"], capture_output=True, text=True, env=child_env()).stdout.strip()


def cli_call(model: str, message: str, *, system: str, effort: str | None = None, cwd: str | None = None,
             timeout: float = 900, max_budget_usd: float = 1.0, tools: tuple[str, ...] = (),
             max_turns: int | None = None, trace: Path | None = None) -> dict:
    """One headless call; returns the CLI's JSON output plus returncode, stderr and wall time. No tools by default;
    `tools` names the only tools the call may use (for example WebSearch and WebFetch), all pre-approved.
    `--safe-mode` turns off CLAUDE.md, skills, plugins, hooks, MCP servers and custom agents in every call. Tool-free
    calls also run with `--bare`; calls with tools do not, because `--bare` removes WebSearch and WebFetch (probes of
    2026-10-06, `eval/results/ai-origin-prior-art/probes.jsonl`). Auth is the API key either way (child_env).

    `max_turns` adds `--max-turns`. With `trace`, the CLI streams events (`stream-json`), every event line is written
    to `trace`, and the last `result` event is returned; without one the call is an error, `assistant_events` counts
    the model turns the stream shows, and when there is at least one the task ran and paid for usage that no result
    reports, so `booked_cost` is set to the call's cap (`max_budget_usd`) as an upper bound, as for a timeout. A
    timeout returns `timed_out: True` (no usage is reported then; run_jobs books `booked_cost` if the caller set
    one)."""
    cmd = [claude_bin(), "-p", *([] if tools else ["--bare"]), "--safe-mode", "--system-prompt", system,
           "--model", model, "--output-format", "stream-json" if trace else "json", *(["--verbose"] if trace else []),
           "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
           "--max-budget-usd", str(max_budget_usd)]
    cmd += ["--tools", *tools, "--allowedTools", *tools] if tools else ["--tools", ""]
    if effort:
        cmd += ["--effort", effort]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, input=message, capture_output=True, text=True, cwd=cwd, timeout=timeout,
                              env=child_env())
    except subprocess.TimeoutExpired as exc:
        if trace and exc.stdout:
            partial = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
            trace.parent.mkdir(parents=True, exist_ok=True)
            trace.write_text(partial, encoding="utf-8")
        return {"is_error": True, "result": f"timed out after {timeout:.0f} s", "returncode": None, "stderr": None,
                "timed_out": True, "wall_seconds": round(time.monotonic() - started, 1)}
    if trace:
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(proc.stdout, encoding="utf-8")
        out = last_result_event(proc.stdout)
        if out is None:
            turns = sum(1 for event in _events(proc.stdout) if event.get("type") == "assistant")
            out = {"is_error": True, "result": None, "stdout": proc.stdout[-2000:], "assistant_events": turns}
            if turns:
                out["booked_cost"] = {"usd": float(max_budget_usd), "source": "upper_bound"}
    else:
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out = {"is_error": True, "result": None, "stdout": proc.stdout[-2000:]}
    out["returncode"] = proc.returncode
    out["stderr"] = proc.stderr[-2000:] or None
    out["wall_seconds"] = round(time.monotonic() - started, 1)
    return out


def _events(stream: str):
    """The JSON object events of a stream-json output, in order; other lines are skipped."""
    for line in stream.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def last_result_event(stream: str) -> dict | None:
    """The last `{"type": "result", ...}` event of a stream-json output, or None."""
    found = None
    for event in _events(stream):
        if event.get("type") == "result":
            found = event
    return found


def usage_totals(out: Mapping) -> dict[str, int]:
    """Tokens of every API call the CLI made (`modelUsage`, which includes the web tools' own calls), summed over
    models; the top-level `usage` block (main loop only) when there is no `modelUsage`."""
    per_model = out.get("modelUsage") or {}
    if per_model:
        keys = {"input": "inputTokens", "output": "outputTokens", "cache_read": "cacheReadInputTokens",
                "cache_creation": "cacheCreationInputTokens"}
        return {k: sum(int(u.get(v) or 0) for u in per_model.values()) for k, v in keys.items()}
    usage = out.get("usage") or {}
    return {"input": int(usage.get("input_tokens") or 0), "output": int(usage.get("output_tokens") or 0),
            "cache_read": int(usage.get("cache_read_input_tokens") or 0),
            "cache_creation": int(usage.get("cache_creation_input_tokens") or 0)}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_jobs(jobs: list, call: Callable[[Any], dict], save: Callable[[Any, dict, str], None], *, ledger_path: Path,
             cap_usd: float, workers: int = 3, describe: Callable[[Any], dict] = lambda job: {},
             expected_model: Callable[[Any], str | tuple[str, ...]] | None = None,
             outcome: Callable[[Any, dict], str] | None = None, call_cap_usd: float = 0.0) -> dict:
    """Run calls in parallel and book each one in the ledger.

    `call(job)` returns the CLI output; `save(job, out, at)` stores the result and runs under a lock; `describe(job)`
    gives the ledger fields that identify the job. The ledger file exists from the start, so a run in progress is
    visible to `harness.budget` before its first call is booked; costs from earlier runs in the same ledger count
    toward `cap_usd`.

    The cap. Before each call the run stops if the spend so far plus the mean cost per call (once 3 costs are booked)
    times the calls in flight and the calls left would pass `cap_usd`. `call_cap_usd` is the most one call can cost
    (the per-item cap the call runs under); with it the cap is a hard bound from the first call: a call starts only
    when the spend so far plus every call in flight and itself, each counted at `call_cap_usd` or at the mean if that
    is higher, stays within `cap_usd`. Otherwise it waits for a call in flight to finish, and the run stops when none
    is in flight. The bound holds up to the CLI's own overshoot of `--max-budget-usd`, a few cents per call (0.4069
    against 0.40 on 2026-10-06). Without `call_cap_usd`, calls in flight count at the mean only.

    It also stops after MAX_CONSECUTIVE_ERRORS failed calls in a row, at once when the API reports a low credit
    balance, and at once when a job raises (its cost is then unknown; the exception propagates). With
    `expected_model`, an answer that any other model produced counts as a failed call (`model_mismatch`); its cost is
    still booked. A request the model's safeguards decline is marked `declined` and does not count as a failure.
    Calls already running when it stops still finish and are booked.

    `expected_model` may return a tuple `(main, *auxiliary)`: the call must use `main` and no model outside the tuple.
    `outcome(job, out)` returns "ok", "system_failure" or "infra_error"; only "infra_error" counts toward the stop
    after MAX_CONSECUTIVE_ERRORS (a system stopped by its own cap is a counted failure of that item, not a reason to
    stop the run). Without `outcome`, every error counts. A `booked_cost` ({"usd", "source"}) set by the caller is
    added to the cost priced from `modelUsage` (source `usage_plus_<source>`), and when there is no `modelUsage` it is
    booked alone, before the CLI's own figure.
    """
    cond = threading.Condition()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.touch()
    past = [e["cost_usd"] for e in read_jsonl(ledger_path)]
    state = {"spent": sum(past), "costs": list(past), "started": 0, "done": 0, "aborted": None,
             "errors_in_a_row": 0}

    def stop(reason: str) -> None:  # under the lock
        if not state["aborted"]:
            state["aborted"] = reason
        cond.notify_all()

    def admit() -> bool:
        """Under the lock: wait until the next call fits the cap, then count it as started; False once the run
        stops."""
        while not state["aborted"]:
            spent, in_flight = state["spent"], state["started"] - state["done"]
            left = len(jobs) - state["started"]
            mean = sum(state["costs"]) / len(state["costs"]) if len(state["costs"]) >= 3 else 0.0
            projected = spent + (in_flight + left) * mean
            if projected > cap_usd or spent >= cap_usd:
                stop(f"projected ${projected:.2f} for {in_flight + left} calls left passes the cap ${cap_usd:.2f}")
                break
            bound = max(float(call_cap_usd), mean)
            if spent + (in_flight + 1) * bound <= cap_usd:
                state["started"] += 1
                return True
            if not in_flight:
                stop(f"the next call may cost up to ${bound:.2f}, which passes the cap ${cap_usd:.2f} with "
                     f"${spent:.2f} spent")
                break
            cond.wait()
        return False

    def one(job):
        with cond:
            if not admit():
                return
        counted = False
        try:
            out = call(job)
            used = sorted(out.get("modelUsage") or {})
            if expected_model and used:
                expected = expected_model(job)
                main, allowed = (expected, {expected}) if isinstance(expected, str) else (expected[0], set(expected))
                if main not in used or not set(used) <= allowed:
                    out["is_error"] = True
                    out["model_mismatch"] = used
            booked = out.get("booked_cost")
            try:
                cost, cost_source = cli_cost(out), "usage_at_official_prices"
                if booked:  # steps of a multi-step item that timed out report no usage; their upper bound is added
                    cost, cost_source = cost + float(booked["usd"]), f"usage_plus_{booked['source']}"
            except KeyError:
                if booked:
                    cost, cost_source = float(booked["usd"]), booked["source"]
                else:
                    cost, cost_source = float(out.get("total_cost_usd") or 0.0), "cli_reported"
            usage = out.get("usage") or {}
            kind = outcome(job, out) if outcome else ("infra_error" if out.get("is_error") else "ok")
            at = utc_stamp()
            with cond:
                counted = True
                state["done"] += 1
                state["spent"] += cost
                state["costs"].append(cost)
                if out.get("is_error") and DECLINED.search(str(out.get("result") or "")):
                    out["declined"] = True
                elif kind == "infra_error":
                    state["errors_in_a_row"] += 1
                else:
                    state["errors_in_a_row"] = 0
                if out.get("is_error") and LOW_CREDIT.search(str(out.get("result") or "")):
                    stop(f"the API reports a low credit balance: {str(out.get('result'))[:200]}")
                if state["errors_in_a_row"] >= MAX_CONSECUTIVE_ERRORS:
                    stop(f"{MAX_CONSECUTIVE_ERRORS} failed calls in a row; last: {str(out.get('result'))[:200]}")
                append_jsonl(ledger_path, {"at": at, **describe(job), "cost_usd": round(cost, 8),
                                           "cost_source": cost_source, "cli_cost_usd": out.get("total_cost_usd"),
                                           "models_used": sorted(out.get("modelUsage") or {}),
                                           "model_mismatch": bool(out.get("model_mismatch")),
                                           "declined": bool(out.get("declined")),
                                           "input_tokens": usage.get("input_tokens"),
                                           "output_tokens": usage.get("output_tokens"),
                                           "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                                           "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                                           "duration_ms": out.get("duration_ms"), "is_error": bool(out.get("is_error")),
                                           "tokens_all": usage_totals(out), "web_searches": web_searches(out),
                                           "num_turns": out.get("num_turns"), "subtype": out.get("subtype"),
                                           "wall_seconds": out.get("wall_seconds"),
                                           "timed_out": bool(out.get("timed_out"))})
                save(job, out, at)
                cond.notify_all()
        except BaseException as err:
            with cond:
                if not counted:
                    state["done"] += 1
                stop(f"a job raised {type(err).__name__}: {str(err)[:200]}")
            raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            list(pool.map(one, jobs))
        except BaseException as err:  # Ctrl-C or a job's exception: no new call starts, calls in flight finish
            with cond:
                stop(f"interrupted by {type(err).__name__}")
            raise
    return {"calls": state["started"], "spent_usd": round(state["spent"], 4), "aborted": state["aborted"]}
