"""The reference pipeline's place in the harness (docs/design/harness.md sections 4 and 9). The pipeline is not built;
its adapter contract is fixed here so the harness, its tests and the readiness check on 19 Oct (eval/protocol.md
decision 13) do not wait for it."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class NotReady(RuntimeError):
    """The reference pipeline does not exist yet; a run that names it stops before any call."""


def run(view: Mapping, cfg: Mapping, ctx: Any) -> dict:
    """One item: the same contract as every adapter (section 5), relations per candidate and a probability."""
    raise NotReady("the reference pipeline is not built; readiness is decided on 19 Oct (decision 13)")


def rank_closed_pool(query_text: str, candidates: Sequence[Mapping]) -> list[dict]:
    """Closed-pool ranking for the Kulikowski comparison (protocol section 5): every candidate with a score."""
    raise NotReady("the reference pipeline is not built")
