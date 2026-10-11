"""Framework for the three re-implemented checkers (docs/design/harness.md sections 4, 5 and 9): one dollar budget per
item shared by every step, one tool-free model call per step, the merge of a system's sub-calls into one CLI-shaped
output (so `pricing.cli_cost` and `llm.run_jobs` treat it like any other system), and JSON parsing of answers.

The step prompts themselves are clean-room work (decision 11) and live in the prompt files the config names; this
module only fills them with `string.Template` and runs them.
"""

from __future__ import annotations

import json
import re
import string
from typing import Any, Iterable, Mapping

from .. import llm, pricing
from ..env import REPO_ROOT

SYSTEM = ("You carry out one step of a literature check. Follow the instructions in the message and answer in the "
          "format they ask for.")
PER_CALL_CONSTANTS = ("contextWindow", "maxOutputTokens")  # modelUsage fields that describe the model, not the call
FENCE_RE = re.compile(r"```[^\n`]*\n?(.*?)```", re.S)
_decoder = json.JSONDecoder()


class ItemBudget:
    """The per-item dollar cap of a multi-step system: every step's call is capped at what is left, and every
    step's cost is charged here, so the steps of one item share one cap (eval/protocol.md section 6)."""

    def __init__(self, cap_usd: float) -> None:
        self.cap = float(cap_usd)
        self.spent = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.cap

    def call_cap(self, step_cap: float | None = None) -> float:
        """`--max-budget-usd` for the next call: what is left, or the step's own cap if that is smaller."""
        return self.remaining if step_cap is None else min(self.remaining, float(step_cap))

    def charge(self, usd: float) -> None:
        self.spent += float(usd)


def _step(cfg: Mapping, step: str) -> Mapping:
    for spec in cfg.get("steps") or []:
        if spec.get("name") == step:
            return spec
    raise KeyError(f"no step {step!r} in the config of {cfg.get('system')!r}")


def llm_step(cfg: Mapping, step: str, fields: Mapping[str, Any], budget: ItemBudget, ctx: Any) -> tuple[Any, dict]:
    """Run one step: fill its prompt file with `fields`, make one tool-free call capped by the item budget, charge
    the budget, log the sub-call through `ctx.log_call` (calls.jsonl, section 8.7) and parse the answer's JSON.
    Returns (parsed JSON or None, CLI output). With the budget already spent, no call is made and the output is an
    error marked `budget_exhausted`, which the run classifies as `capped`."""
    spec = _step(cfg, step)
    if budget.exhausted:
        return None, {"is_error": True, "result": None, "subtype": "item_budget_exhausted", "budget_exhausted": True}
    template = (REPO_ROOT / spec["prompt"]["path"]).read_text(encoding="utf-8")
    message = string.Template(template).substitute(fields)
    cap = budget.call_cap(spec.get("cap_usd"))
    out = llm.cli_call(cfg["model"], message, system=SYSTEM, effort=cfg.get("effort"), cwd=ctx.cwd,
                       timeout=cfg.get("timeout_s") or 900, max_budget_usd=cap, tools=())
    try:
        cost, source = pricing.cli_cost(out), "usage_at_official_prices"
    except KeyError:  # no modelUsage: a timeout is booked at the call's cap, anything else at the CLI's own figure
        if out.get("timed_out"):
            cost, source = cap, "upper_bound"
            out["booked_cost"] = {"usd": cap, "source": source}
        else:
            cost, source = float(out.get("total_cost_usd") or 0.0), "cli_reported"
    budget.charge(cost)
    ctx.log_call({"at": llm.utc_stamp(), "step": step, "kind": "llm", "model": cfg["model"],
                  "cost_usd": round(cost, 8), "cost_source": source, "tokens_all": llm.usage_totals(out),
                  "budget_left_usd": round(budget.remaining, 8), "is_error": bool(out.get("is_error")),
                  "models_used": sorted(out.get("modelUsage") or {}), "timed_out": bool(out.get("timed_out"))})
    return (None if out.get("is_error") else parse_json(out.get("result"))), out


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def merge(outs: Iterable[Mapping]) -> dict:
    """One CLI-shaped output for an item from the outputs of its steps: `modelUsage` summed per model and field (the
    model's context window and output limit kept as they are), the top-level `usage` numbers summed, `is_error` if
    any step erred, the result of the last step, and the subtype of the first failed step (else of the last step).
    Booked upper bounds of steps without `modelUsage` are summed into `booked_cost`; `llm.run_jobs` adds it to the
    cost of the priced `modelUsage` (`usage_plus_upper_bound`), or books it alone when no step reported `modelUsage`,
    and calls.jsonl holds every step's cost either way."""
    outs = list(outs)
    per_model: dict[str, dict] = {}
    usage: dict[str, float] = {}
    for out in outs:
        for model, fields in (out.get("modelUsage") or {}).items():
            agg = per_model.setdefault(model, {})
            for name, value in fields.items():
                if name in PER_CALL_CONSTANTS or not _number(value):
                    agg.setdefault(name, value)
                else:
                    agg[name] = agg.get(name, 0) + value
        for name, value in (out.get("usage") or {}).items():
            if _number(value):
                usage[name] = usage.get(name, 0) + value
    failed = [out for out in outs if out.get("is_error")]
    last = outs[-1] if outs else {}
    booked = [out["booked_cost"] for out in outs if out.get("booked_cost") and not out.get("modelUsage")]
    merged = {"is_error": bool(failed), "result": last.get("result"),
              "subtype": (failed[0] if failed else last).get("subtype"),
              "num_turns": sum(int(out.get("num_turns") or 0) for out in outs),
              "modelUsage": per_model, "usage": usage,
              "total_cost_usd": round(sum(float(out.get("total_cost_usd") or 0.0) for out in outs), 8),
              "wall_seconds": round(sum(float(out.get("wall_seconds") or 0.0) for out in outs), 1),
              "timed_out": any(out.get("timed_out") for out in outs),
              "booked_cost": ({"usd": round(sum(float(b["usd"]) for b in booked), 8), "source": "upper_bound"}
                              if booked else None),
              "llm_calls": sum(1 for out in outs if not out.get("budget_exhausted"))}
    for flag in ("budget_exhausted", "declined"):
        if any(out.get(flag) for out in outs):
            merged[flag] = True
    return merged


def _last_json(text: str) -> Any:
    """The last top-level JSON object or list in `text`, scanning left to right; None if there is none."""
    found, i = None, 0
    while True:
        starts = [p for p in (text.find("{", i), text.find("[", i)) if p >= 0]
        if not starts:
            return found
        i = min(starts)
        try:
            found, i = _decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1


def parse_json(text: str | None) -> Any:
    """The last JSON object or list in a model's answer: from the last fenced block that holds one, else the last
    one in the bare text. None when there is none."""
    if not text:
        return None
    for block in reversed(FENCE_RE.findall(text)):
        found = _last_json(block)
        if found is not None:
            return found
    return _last_json(text)
