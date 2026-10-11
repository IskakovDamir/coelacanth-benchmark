"""Label mapping for measurement A (docs/design/harness.md section 7, item 6; eval/protocol.md sections 6 and 7).

Each system's rule is fixed in its config before the config hash:
- `precedence_over_candidates` (agents, reference pipeline): the six-way verdict is derived from the system's own
  relations on its list after leakage cleaning, by SCHEMA.md's precedence, so a leaked or excluded work can never
  give "already done";
- `native_item_label` (systems that give one label per item): the config's map; an unknown label is unmappable, and
  the row is kept and counted;
- `none` (Kulikowski's ranking): no verdict by design, which is not counted as unmappable.
Every failure status gives null labels. `mapped` is True for a mapped label, False for an unmappable one and None
when no label is expected (rule `none`, or a failure).
"""

from __future__ import annotations

from typing import Mapping, Sequence

from ..benchmark.items import derive_verdict

# Protocol section 7: already done = equivalent or specialization; related = generalization, partial,
# similar_mechanism; nothing_close.
THREE_CLASS = {"equivalent": "already_done", "specialization": "already_done", "generalization": "related",
               "partial": "related", "similar_mechanism": "related", "nothing_close": "nothing_close"}
CLASS3 = ("already_done", "related", "nothing_close")
RULES = ("precedence_over_candidates", "native_item_label", "none")


def _relations(candidates: Sequence[Mapping]) -> dict:
    """The candidates as derive_verdict reads them. A system's output names the relation `relation`; resolved.jsonl
    names it `relation_native`. Either is accepted, so a resolved list is never read as having no relations."""
    return {"candidates": [{"relation": c["relation"] if "relation" in c else c.get("relation_native")}
                           for c in candidates]}


def map_item(cfg: Mapping, native_label: str | None, cleaned_candidates: Sequence[Mapping] | None, status: str, *,
             raw_candidates: Sequence[Mapping] | None = None) -> dict:
    """The `verdict` block of one resolved.jsonl row (spec 8.8). `cleaned_candidates` is the list after leakage,
    exclusion and duplicate removal; `raw_candidates`, if given, the list before, for `six_before_cleaning`. Only
    status `ok` yields labels."""
    rule = cfg["label_map"]["rule"]
    if rule not in RULES:
        raise ValueError(f"label_map.rule {rule!r} is not one of {RULES}")
    verdict = {"rule": rule, "native": native_label, "six": None, "class3": None, "mapped": None,
               "six_before_cleaning": None}
    if status != "ok" or rule == "none":
        return verdict
    if rule == "precedence_over_candidates":
        six = derive_verdict(_relations(cleaned_candidates or []))
        before = derive_verdict(_relations(raw_candidates)) if raw_candidates is not None else None
        return {**verdict, "six": six, "class3": THREE_CLASS[six], "mapped": True, "six_before_cleaning": before}
    target = (cfg["label_map"].get("map") or {}).get(native_label) if native_label is not None else None
    if not isinstance(target, Mapping):
        return {**verdict, "mapped": False}
    return {**verdict, "six": target.get("six"), "class3": target.get("class3"), "mapped": True}
