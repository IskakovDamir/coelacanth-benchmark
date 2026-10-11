"""Anthropic API prices and the cost of a response computed from its token usage.

Prices in USD per million tokens, from the official pricing page read on 2026-09-24
(https://platform.claude.com/docs/en/about-claude/pricing, cached as cache/fulltext/anthropic-pricing-2026-09-24.txt):
base input, output, 5-minute cache write, 1-hour cache write, cache read. The Batch API takes 50% off input and output,
and the cache multipliers stack on the batch price, so a batch response costs half of the same usage at list price.
Thinking tokens are billed as output and are included in `output_tokens`. Server-side web search costs $10 per 1,000
searches on top of tokens; web fetch costs tokens only (same page, read 2026-09-24).
"""

from __future__ import annotations

from typing import Mapping, NamedTuple


class Price(NamedTuple):
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


PRICES = {
    "claude-opus-5-5": Price(4.00, 20.00, 5.00, 8.00, 0.20),
    "claude-sonnet-5-5": Price(2.00, 10.00, 2.50, 4.00, 0.20),  # model page read 2026-10-06
    "claude-sonnet-5": Price(2.00, 10.00, 2.50, 4.00, 0.20),
    "claude-haiku-4-5-20251001": Price(1.00, 5.00, 1.25, 2.00, 0.10),
    "claude-opus-5": Price(5.00, 25.00, 6.25, 10.00, 0.50),  # served in place of Opus 5.5 when the CLI falls back
}
BATCH_DISCOUNT = 0.5
WEB_SEARCH_USD = 10.0 / 1000


def price(model: str) -> Price:
    """Price for a model ID or its undated alias ('claude-haiku-4-5' matches 'claude-haiku-4-5-20251001', but
    'claude-opus-5' never matches 'claude-opus-5-5'); unknown models raise KeyError."""
    if model in PRICES:
        return PRICES[model]
    for key, value in PRICES.items():
        if key.startswith(model + "-20") or model.startswith(key + "-20"):
            return value
    raise KeyError(f"no price for model {model!r}")


def usage_cost(model: str, usage: Mapping, *, batch: bool = False) -> float:
    """Cost in USD of one Messages API usage block (snake_case fields, as the API and the CLI's `usage` give them)."""
    p = price(model)
    written = usage.get("cache_creation") or {}
    write_5m = written.get("ephemeral_5m_input_tokens")
    write_1h = written.get("ephemeral_1h_input_tokens")
    if write_5m is None and write_1h is None:
        write_5m, write_1h = usage.get("cache_creation_input_tokens") or 0, 0
    total = ((usage.get("input_tokens") or 0) * p.input + (usage.get("output_tokens") or 0) * p.output
             + (write_5m or 0) * p.cache_write_5m + (write_1h or 0) * p.cache_write_1h
             + (usage.get("cache_read_input_tokens") or 0) * p.cache_read) / 1_000_000
    return total * (BATCH_DISCOUNT if batch else 1.0)


def web_searches(out: Mapping) -> int:
    """Web searches in one CLI call: the per-model counts, else the top-level usage's server tool count."""
    per_model = out.get("modelUsage") or {}
    counted = sum(int(u.get("webSearchRequests") or 0) for u in per_model.values())
    if counted:
        return counted
    return int(((out.get("usage") or {}).get("server_tool_use") or {}).get("web_search_requests") or 0)


def cli_cost(out: Mapping) -> float:
    """Cost of one Claude Code CLI call from its JSON output: tokens plus web searches, from `modelUsage`, which
    counts every API call the CLI made, including the separate calls its WebSearch and WebFetch tools make (the
    top-level `usage` block covers only the main loop). Cache writes are priced at the 5-minute rate, except that
    with one model the 1-hour writes the top-level block reports are priced at the 1-hour rate. Raises KeyError when
    the output has no `modelUsage`."""
    per_model = out.get("modelUsage") or {}
    if not per_model:
        raise KeyError("no modelUsage in the CLI output")
    top = out.get("usage") or {}
    one_hour = int(((top.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)) if len(per_model) == 1 else 0
    total = 0.0
    for model, u in per_model.items():
        written = int(u.get("cacheCreationInputTokens") or 0)
        h1 = min(one_hour, written)
        total += usage_cost(model, {"input_tokens": u.get("inputTokens"), "output_tokens": u.get("outputTokens"),
                                    "cache_creation": {"ephemeral_5m_input_tokens": written - h1,
                                                       "ephemeral_1h_input_tokens": h1},
                                    "cache_read_input_tokens": u.get("cacheReadInputTokens")})
    return total + web_searches(out) * WEB_SEARCH_USD
