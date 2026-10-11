"""The Claude Code agent league of measurement A (docs/design/harness.md sections 5 and 9): the single agent, the
prior-art skill and Hunch, each one headless Claude Code call with WebSearch and WebFetch only.

The three systems differ only in their pinned system prompt (`priorart.SINGLE_SYSTEM`, `priorart.prior_art_system`,
`priorart.hunch_system`) and in the header of their brief. A brief is the variant's header file, then the shared
block `Date: $date\\n\\nClaim: $text`, then `contract.txt` (`priorart.CONTRACT` with "method" reworded to "claim"),
filled from the config and the view only. The view is exactly `{"text", "date"}` (SCHEMA.md rule 11); anything else
is refused before a prompt is built.

Every agent call streams its events to `ctx.trace_path` (gitignored), and the search queries and fetched URLs are read
back from the trace's `tool_use` events: fetched pages are the evidence the leakage rule dates (section 7, step 5);
search-result lists are kept in the trace and not counted. The answer itself stays in the trace and the CLI output,
so the `why` sentences never reach `native`.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any, Mapping

from .. import llm, priorart
from ..env import REPO_ROOT

TOOLS = ("WebSearch", "WebFetch")
SHARED_BLOCK = "Date: $date\n\nClaim: $text"
# The system prompt each variant is pinned to; the config names the same source, and the two must agree.
PINNED_SYSTEMS = {"single": "coelacanth.priorart.SINGLE_SYSTEM",
                  "prior_art_skill": "coelacanth.priorart.prior_art_system",
                  "hunch": "coelacanth.priorart.hunch_system"}


def _read(path: str) -> str:
    p = Path(path)
    return (p if p.is_absolute() else REPO_ROOT / p).read_text(encoding="utf-8").rstrip("\n")


def brief(cfg: Mapping, view: Mapping) -> str:
    """The user message of one agent call: header, shared block and contract, filled from the config and the view."""
    if set(view) != {"text", "date"}:
        raise ValueError(f"a view holds exactly text and date (SCHEMA.md rule 11), got keys {sorted(view)}")
    template = (f"{_read(cfg['prompts']['brief']['path'])}\n\n{SHARED_BLOCK}\n\n"
                f"{_read(cfg['prompts']['contract']['path'])}")
    return Template(template).substitute(date=view["date"], text=view["text"], searches=cfg["search_cap"],
                                         fetches=cfg["fetch_cap"], k=cfg["top_k"])


def system_prompt(cfg: Mapping) -> str:
    """The pinned system prompt of the config's variant (Hunch and the skill are rebuilt from their pinned sources)."""
    source = PINNED_SYSTEMS[cfg["variant"]]
    if cfg["prompts"]["system"]["source"] != source:
        raise ValueError(f"variant {cfg['variant']!r} is pinned to {source}, the config names "
                         f"{cfg['prompts']['system']['source']}")
    value = getattr(priorart, source.rsplit(".", 1)[1])
    return value() if callable(value) else value


def trace_events(path: Path | None) -> dict:
    """Search queries, fetched URLs, their counts and the final result event of one stream-json trace. A tool_use
    block repeated under the same id is counted once; a missing or partial trace (a timeout) gives what it holds."""
    text = path.read_text(encoding="utf-8") if path is not None and path.exists() else ""
    searches, fetched, seen = [], [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("id"):
                if block["id"] in seen:
                    continue
                seen.add(block["id"])
            args = block.get("input") or {}
            if block.get("name") == "WebSearch" and args.get("query"):
                searches.append(str(args["query"]))
            elif block.get("name") == "WebFetch" and args.get("url"):
                fetched.append(str(args["url"]))
    return {"searches": searches, "fetched_urls": fetched, "n_searches": len(searches), "n_fetches": len(fetched),
            "result": llm.last_result_event(text)}


def _candidate(c: Mapping) -> dict:
    return {"rank": c["rank"], "title": c["title"], "authors": c.get("authors"), "year": c.get("year"),
            "venue": c.get("venue"), "doi": c.get("doi"), "url": c.get("url"), "openalex": None,
            "publication_date": None, "field": c.get("field"), "relation": c.get("relation"), "score": None}


def run_agent(view: Mapping, cfg: Mapping, ctx: Any) -> dict:
    """One agent call on one view; returns the CLI output with `native` filled (section 5). Candidates are None when
    the call failed or its answer has no JSON list; the evidence is read from the trace either way."""
    if ctx.trace_path is None:
        raise ValueError("an agent call needs a trace: its fetched pages are the evidence the leakage rule dates")
    if tuple(cfg["tools"]) != TOOLS:
        raise ValueError(f"the agent league uses the tools {TOOLS}, the config names {cfg['tools']}")
    out = llm.cli_call(cfg["model"], brief(cfg, view), system=system_prompt(cfg), effort=cfg["effort"], cwd=ctx.cwd,
                       tools=TOOLS, max_budget_usd=cfg["per_item_cap_usd"], max_turns=cfg["max_turns"],
                       timeout=cfg["timeout_s"], trace=ctx.trace_path)
    events = trace_events(ctx.trace_path)
    parsed = None if out.get("is_error") else priorart.parse_candidates(out.get("result"))
    native = {"label": None, "probability": None,
              "candidates": None if parsed is None else [_candidate(c) for c in parsed[:cfg["top_k"]]],
              "evidence": [{"kind": "fetched_page", "url": url, "openalex": None, "for_rank": None}
                           for url in dict.fromkeys(events["fetched_urls"])],
              "searches": events["searches"], "fetches": events["n_fetches"], "llm_calls": 1,
              "openalex": {"search": 0, "list": 0, "entity": 0}}
    return {**out, "subtype": out.get("subtype"), "num_turns": out.get("num_turns"),
            "timed_out": bool(out.get("timed_out")), "booked_cost": out.get("booked_cost"), "native": native}
