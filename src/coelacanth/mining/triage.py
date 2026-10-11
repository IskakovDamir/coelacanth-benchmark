"""Triage of the OpenAlex declaration hits with Haiku 4.5 on title and abstract, through the Message Batches API
(Damir, 2026-09-24).

Question per record: does this document declare that a specific earlier work is equivalent to, a special case of,
or rediscovered by a later one, across fields? One request per record, temperature 0, at most MAX_TOKENS output
tokens; the answer gives a label (`cross_field`, `same_field`, `no`), the relation, the two works if the record names
them, and a confidence. Abstracts are cut to MAX_ABSTRACT_WORDS words and never written to disk. Before a batch is
submitted, its projected cost (the mean cost per request so far, or a worst case before any request has been priced)
plus the spend so far in the same round must stay within the cap. Round 1 (2026-09-24, cap $5) covers the first pages
of every query; round 2 (2026-10-06, cap $3) covers the records the paged queries added (`round` in
openalex-declarations.jsonl; rows without it are round 1). Writes, under data/candidates/declarations-triage/:
    labels.jsonl    one row per record and attempt (the last row per record counts)
    raw.jsonl       the model's raw answer per request, for audit
    ledger.jsonl    cost per request, from its token usage at the batch price
    batches.jsonl   each submitted batch's final state

    PYTHONPATH=src python -m coelacanth.mining.triage --limit 2 --cap 5      # pilot
    PYTHONPATH=src python -m coelacanth.mining.triage --cap 5                # the rest
    PYTHONPATH=src python -m coelacanth.mining.triage --sample 40            # hand-check sample of positives
    PYTHONPATH=src python -m coelacanth.mining.triage --summarise            # precision and projection
    PYTHONPATH=src python -m coelacanth.mining.triage --round 2 --cap 3      # round 2: the paged records
    PYTHONPATH=src python -m coelacanth.mining.triage --round 2 --sample 30  # fresh sample of round-2 positives
    PYTHONPATH=src python -m coelacanth.mining.triage --round 3 --cap 5      # round 3 (2026-10-10): new phrases

The hand-check samples list randomly drawn `cross_field` records with the model's answer: hand-check.jsonl (round 1,
40 records, seed 20260924) and hand-check-round2.jsonl (round 2, seed 20261006, records checked before excluded). Two
readings per record, both Claude, neither an annotator (`readers`): reader 1 is the main session (round 1 on
2026-09-24, round 2 on 2026-10-06), reader 2 a separate agent that reads title and abstract blind to reader 1. `check`
is the lower of the two readings in the order cross_field_specific > cross_field_vague > same_field > no, the
labelling guide's rule for doubt. The usable-item projection is stratified by round: each round's distinct positive
titles times that round's precision, summed, with the Wilson bounds summed for the interval.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from ..anthropic_batch import BatchClient, message_text
from ..closedbook import wilson
from ..data.openalex import OpenAlexClient
from ..llm import append_jsonl, read_jsonl, utc_stamp
from ..pricing import BATCH_DISCOUNT, price, usage_cost
from . import CANDIDATES_DIR, write_jsonl
from .declarations import PAGED, abstracts

MODEL = "claude-haiku-4-5-20251001"
OUT_DIR = CANDIDATES_DIR / "declarations-triage"
MAX_ABSTRACT_WORDS = 300
MAX_TOKENS = 400
SAMPLE_SEED = 20260924
SAMPLE_SEEDS = {1: 20260924, 2: 20261006, 3: 20261010}
LABELS = ("cross_field", "same_field", "no")
RELATIONS = ("equivalent", "special_case", "rediscovery")
SYSTEM = "You screen bibliographic records for a study of cross-field rediscoveries in science. Answer only with JSON."
INSTRUCTIONS = """For each record below, decide whether the document declares that a specific earlier work (a method, \
result, formula, model or concept) is equivalent to, a special case of, or rediscovered by a later work, where the two \
works come from different fields or research communities.

Labels:
- "cross_field": the document makes such a declaration, and the two works come from different fields or communities.
- "same_field": the document makes such a declaration within one field or community.
- "no": anything else, including treatments or tests found equivalent in a trial, renewed interest in an old idea, \
rediscovered species, places, texts or artefacts, applications of a known method to a new problem, and essays on \
novelty that name no specific earlier work.

For each record return an object with these keys: "n" (the record number); "label"; "relation" ("equivalent", \
"special_case", "rediscovery" or null); "earlier" (the earlier work as the record names it, or null); "later" (the \
later work as the record names it, or null); "fields" (the two fields, or null); "confidence" (a number from 0 to 1). \
Return one JSON array with one object per record, in record order, and nothing else."""


def record_text(n: int, row: dict, abstract: str | None) -> str:
    words = (abstract or "").split()
    cut = " ".join(words[:MAX_ABSTRACT_WORDS]) + (" [...]" if len(words) > MAX_ABSTRACT_WORDS else "")
    return f"[{n}]\nTitle: {row.get('title') or '(no title)'}\nAbstract: {cut or '(no abstract)'}"


def batch_message(batch: list[tuple[dict, str | None]]) -> str:
    return INSTRUCTIONS + "\n\nRecords:\n\n" + "\n\n".join(record_text(i, row, ab) for i, (row, ab) in
                                                             enumerate(batch, 1))


def request(row: dict, abstract: str | None) -> dict:
    """One Batch API request for one record; custom_id is the OpenAlex work ID."""
    return {"custom_id": row["openalex"],
            "params": {"model": MODEL, "max_tokens": MAX_TOKENS, "temperature": 0, "system": SYSTEM,
                       "messages": [{"role": "user", "content": batch_message([(row, abstract)])}]}}


def worst_case_cost(req: dict) -> float:
    """Upper bound for one request before any has been priced: about 3 characters per input token, all output
    tokens used, at the batch price."""
    chars = len(req["params"]["system"]) + sum(len(m["content"]) for m in req["params"]["messages"])
    p = price(MODEL)
    return (chars / 3 * p.input + MAX_TOKENS * p.output) / 1_000_000 * BATCH_DISCOUNT


def parse_answer(text: str | None, size: int) -> dict[int, dict]:
    """Objects by record number from the model's answer; malformed or missing entries are left out."""
    if not text:
        return {}
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    out = {}
    for obj in data if isinstance(data, list) else []:
        if not isinstance(obj, dict):
            continue
        try:
            n = int(obj.get("n"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= size and obj.get("label") in LABELS:
            out[n] = obj
    return out


def label_row(openalex: str, batch_id: str, has_abstract: bool, text: str | None, result_type: str | None,
              round_: int = 1) -> dict:
    obj = parse_answer(text, 1).get(1)
    label = obj.get("label") if obj else ("parse_error" if result_type == "succeeded" else f"api_{result_type}")
    return {"openalex": openalex, "batch": batch_id, "model": MODEL, "round": round_, "has_abstract": has_abstract,
            "label": label,
            "relation": obj.get("relation") if obj and obj.get("relation") in RELATIONS else None,
            "earlier": obj.get("earlier") if obj else None, "later": obj.get("later") if obj else None,
            "fields": obj.get("fields") if obj else None, "confidence": obj.get("confidence") if obj else None}


def latest_labels(path: Path | None = None) -> dict[str, dict]:
    """The last label written for each record (a failed attempt is replaced when the record is rerun)."""
    latest = {}
    for r in read_jsonl(path or OUT_DIR / "labels.jsonl"):
        latest[r["openalex"]] = r
    return latest


def hand_check_path(round_: int) -> Path:
    return OUT_DIR / ("hand-check.jsonl" if round_ == 1 else f"hand-check-round{round_}.jsonl")


def _rows() -> dict[str, dict]:
    with (CANDIDATES_DIR / "openalex-declarations.jsonl").open(encoding="utf-8") as f:
        return {r["openalex"]: r for r in map(json.loads, f)}


def _round(row: dict) -> int:
    return row.get("round") or 1


def sample_positives(n: int, seed: int | None = None, round_: int = 1) -> list[dict]:
    """A reproducible random sample of one round's `cross_field` records for the hand check, with title and the
    model's answer; records already in an earlier hand check are left out, so a later sample is fresh."""
    rows = _rows()
    latest = latest_labels()
    checked = {c["openalex"] for r in range(1, round_) if hand_check_path(r).exists()
               for c in read_jsonl(hand_check_path(r))}
    positives = sorted(oid for oid, r in latest.items() if r["label"] == "cross_field" and oid in rows
                       and _round(rows[oid]) == round_ and oid not in checked)
    seed = SAMPLE_SEEDS.get(round_, SAMPLE_SEED) if seed is None else seed
    picked = random.Random(seed).sample(positives, min(n, len(positives)))
    keys = ("relation", "earlier", "later", "fields", "confidence")
    return [{"openalex": oid, "title": rows[oid]["title"], "type": rows[oid].get("type"),
             "date": rows[oid].get("publication_date"), "model_answer": {k: latest[oid].get(k) for k in keys},
             "check": None, "works_identifiable": None, "note": None} for oid in picked]


def _title_key(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


CHECK_ORDER = ("cross_field_specific", "cross_field_vague", "same_field", "no")


def adjudicate(first: str, second: str) -> str:
    """The lower of two readings (LABELING.md: when unsure, the label that claims less)."""
    return max(first, second, key=CHECK_ORDER.index)


VERDICT_SETS = (("usable_cross_field", ("cross_field_specific",)),
                ("any_cross_field", ("cross_field_specific", "cross_field_vague")),
                ("any_declaration", ("cross_field_specific", "cross_field_vague", "same_field")))


def summarise_hand_check() -> dict:
    """Per round: precision of the `cross_field` label from that round's hand check, with Wilson 95% intervals, the
    projected number of usable items among the round's positives (duplicate records of one work counted once, and
    titles already counted in an earlier round left out), and positives per query. Then the stratified total."""
    rows, latest = _rows(), latest_labels()
    out, seen_titles = {}, set()
    for round_ in sorted({_round(r) for r in rows.values()}):
        if not hand_check_path(round_).exists():
            continue
        positives = [oid for oid, r in latest.items() if r["label"] == "cross_field" and oid in rows
                     and _round(rows[oid]) == round_]
        titles = {_title_key(rows[oid]["title"]) for oid in positives} - seen_titles
        seen_titles |= titles
        out[f"round_{round_}"] = _summarise_round(read_jsonl(hand_check_path(round_)), positives, len(titles), rows)
    rounds = [v for k, v in out.items() if k.startswith("round_")]
    out["stratified"] = {name: {"projected_items": round(sum(r[name]["projected_items"] for r in rounds), 1),
                                "projected_ci95": [round(sum(r[name]["projected_ci95"][i] for r in rounds), 1)
                                                   for i in (0, 1)]}
                         for name, _ in VERDICT_SETS}
    out["stratified"]["positives_distinct_titles"] = sum(r["positives_distinct_titles"] for r in rounds)
    return out


def _summarise_round(checks: list[dict], positives: list[str], distinct: int, rows: dict[str, dict]) -> dict:
    """Precision is computed over the distinct titles in the sample (duplicate records of one work, such as several
    peer-review comments on one manuscript, count once), since the projection counts distinct titles; the
    record-level counts are kept alongside."""
    n = len(checks)
    counts = Counter(c["check"] for c in checks)
    seen, unique = set(), []
    for c in checks:
        key = _title_key(rows[c["openalex"]]["title"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    u = len(unique)
    ucounts = Counter(c["check"] for c in unique)
    out = {"positives": len(positives), "positives_distinct_titles": distinct, "checked": n,
           "checked_distinct_titles": u, "checked_by": sorted({c.get("checked_by") for c in checks}),
           "verdicts": dict(counts), "verdicts_distinct_titles": dict(ucounts)}
    for name, keys in VERDICT_SETS:
        k = sum(ucounts[x] for x in keys)
        lo, hi = wilson(k, u)
        out[name] = {"k": k, "of": u, "precision": round(k / u, 3), "ci95": [round(lo, 3), round(hi, 3)],
                     "projected_items": round(distinct * k / u, 1),
                     "projected_ci95": [round(distinct * lo, 1), round(distinct * hi, 1)],
                     "records": {"k": sum(counts[x] for x in keys), "of": n,
                                 "precision": round(sum(counts[x] for x in keys) / n, 3)}}
    readers = sorted({r for c in unique for r in (c.get("readers") or {})})
    if readers:
        out["by_reader"] = {}
        for reader in readers:
            k = sum(1 for c in unique if (c["readers"].get(reader) or {}).get("check") == "cross_field_specific")
            lo, hi = wilson(k, u)
            out["by_reader"][reader] = {"usable_k": k, "of": u, "precision": round(k / u, 3),
                                        "ci95": [round(lo, 3), round(hi, 3)],
                                        "projected_items": round(distinct * k / u, 1)}
        both = [c for c in unique if len(c.get("readers") or {}) == 2]
        agree = sum(1 for c in both if len({r["check"] for r in c["readers"].values()}) == 1)
        out["reader_agreement"] = {"agree": agree, "of": len(both)}
    by_query = Counter(q for oid in positives for q in rows[oid].get("queries", []))
    checked_ok = Counter(q for c in checks if c["check"] == "cross_field_specific"
                         for q in rows[c["openalex"]].get("queries", []))
    out["positives_per_query"] = {q: {"positives": v, "usable_in_sample": checked_ok.get(q, 0)}
                                  for q, v in by_query.most_common()}
    return out


def _priority(row: dict) -> int:
    """Round-2 order: records of the more productive paged queries first, so a cap cuts the least productive."""
    return min((PAGED.index(q) for q in row.get("queries", []) if q in PAGED), default=len(PAGED))


def run(cap: float, limit: int | None, client: BatchClient | None = None, poll_seconds: float = 30.0,
        round_: int = 1, fit: bool = False, resume: str | None = None) -> dict:
    """Triage the unlabelled records of one round, in priority order for round 2. The cap covers that round's
    spend; the projection uses the mean cost of the round's priced requests once there are at least two, else of
    every request priced so far. With `fit`, a batch that would pass the cap is cut to the records that fit within
    95% of what is left, and the rest stay unlabelled. With `resume`, nothing is submitted: the run waits for that
    already-submitted batch (its records are the round's unlabelled ones it holds) and books its results."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = OUT_DIR / "ledger.jsonl"
    with (CANDIDATES_DIR / "openalex-declarations.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    done = {oid for oid, r in latest_labels().items() if r["label"] in LABELS}
    todo = [r for r in rows if r["openalex"] not in done and _round(r) == round_]
    if round_ > 1:
        todo.sort(key=_priority)
    todo = todo[:limit] if limit else todo
    if not todo:
        return {"submitted": 0, "note": f"every round-{round_} record already has a label"}
    texts = abstracts(OpenAlexClient(), paged=round_ == 2, with_round3=round_ == 3)
    requests = [request(r, texts.get(r["openalex"])) for r in todo]
    ledger = read_jsonl(ledger_path)
    past = [e["cost_usd"] for e in ledger if e.get("result") == "succeeded"]
    same = [e["cost_usd"] for e in ledger if e.get("result") == "succeeded" and (e.get("round") or 1) == round_]
    past = same if len(same) >= 2 else past
    spent = sum(e["cost_usd"] for e in ledger if (e.get("round") or 1) == round_)
    mean = sum(past) / len(past) if len(past) >= 2 else None
    projected = mean * len(requests) if mean else sum(map(worst_case_cost, requests))
    if spent + projected > cap and fit and mean and not resume:
        keep = int(max(0.0, 0.95 * (cap - spent)) / mean)
        todo, requests = todo[:keep], requests[:keep]
        projected = mean * len(requests)
        if not requests:
            return {"submitted": 0, "spent_usd": round(spent, 4), "note": "nothing fits under the cap"}
    if spent + projected > cap and not resume:  # a resumed batch was checked against the cap when it was submitted
        return {"submitted": 0, "spent_usd": round(spent, 4), "projected_usd": round(projected, 4),
                "note": f"not submitted: spend so far plus the projected ${projected:.2f} passes the cap ${cap:.2f}"}
    client = client or BatchClient()
    if resume:
        batch = client.get(resume)
        print(json.dumps({"batch": batch["id"], "resumed": True, "counts": batch.get("request_counts")}), flush=True)
    else:
        batch = client.create(requests)
        print(json.dumps({"batch": batch["id"], "requests": len(requests), "projected_usd": round(projected, 4)}),
              flush=True)
    batch = client.wait(batch["id"], poll_seconds=poll_seconds,
                        on_poll=lambda b: print(json.dumps({"status": b.get("processing_status"),
                                                            "counts": b.get("request_counts")}), flush=True))
    append_jsonl(OUT_DIR / "batches.jsonl", {k: batch.get(k) for k in ("id", "processing_status", "request_counts",
                                                                       "created_at", "ended_at")})
    has_abstract = {r["openalex"]: bool(texts.get(r["openalex"])) for r in todo}
    cost = 0.0
    for result in client.results(batch):
        oid, kind = result["custom_id"], (result.get("result") or {}).get("type")
        text = message_text(result)
        usage = ((result.get("result") or {}).get("message") or {}).get("usage") or {}
        c = usage_cost(MODEL, usage, batch=True) if kind == "succeeded" else 0.0
        cost += c
        at = utc_stamp()
        append_jsonl(ledger_path, {"at": at, "batch": batch["id"], "round": round_, "custom_id": oid, "model": MODEL,
                                   "result": kind,
                                   "cost_usd": round(c, 8), "cost_source": "usage_at_batch_prices",
                                   "input_tokens": usage.get("input_tokens"),
                                   "output_tokens": usage.get("output_tokens")})
        append_jsonl(OUT_DIR / "raw.jsonl", {"custom_id": oid, "batch": batch["id"], "result": kind, "answer": text,
                                             "at": at})
        append_jsonl(OUT_DIR / "labels.jsonl", label_row(oid, batch["id"], has_abstract.get(oid, False), text, kind,
                                                         round_))
    latest = latest_labels()
    return {"batch": batch["id"], "round": round_, "submitted": len(requests), "request_counts": batch.get("request_counts"),
            "batch_cost_usd": round(cost, 4), "spent_usd": round(spent + cost, 4),
            "cost_per_record": round(cost / len(requests), 6), "records_labelled": len(latest),
            "labels": dict(Counter(r["label"] for r in latest.values())),
            "labels_this_batch": dict(Counter(r["label"] for oid, r in latest.items()
                                              if oid in {t["openalex"] for t in todo})),
            "round_unlabelled_left": sum(1 for r in rows if _round(r) == round_ and
                                         latest.get(r["openalex"], {}).get("label") not in LABELS)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.mining.triage")
    parser.add_argument("--cap", type=float, help="dollar cap for all triage requests so far")
    parser.add_argument("--sample", type=int, help="write a hand-check sample of this many positives and stop")
    parser.add_argument("--summarise", action="store_true", help="summarise the hand check and stop")
    parser.add_argument("--limit", type=int, help="first N unlabelled records only (pilot)")
    parser.add_argument("--poll", type=float, default=30.0, help="seconds between status checks")
    parser.add_argument("--round", type=int, default=1, dest="round_", help="triage round (1: first pages, 2: paged, 3: new phrases)")
    parser.add_argument("--fit", action="store_true", help="cut a batch that would pass the cap to what fits")
    parser.add_argument("--resume", help="wait for and book an already-submitted batch instead of submitting")
    args = parser.parse_args(argv)
    if args.summarise:
        summary = summarise_hand_check()
        (OUT_DIR / "hand-check-summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=1))
        return 0
    if args.sample:
        path = hand_check_path(args.round_)
        if path.exists():
            parser.error(f"{path} exists; a hand-check sample is drawn once")
        sample = sample_positives(args.sample, round_=args.round_)
        write_jsonl(path, sample)
        print(f"{len(sample)} round-{args.round_} positives sampled (seed {SAMPLE_SEEDS.get(args.round_)}) to {path}")
        return 0
    if args.cap is None:
        parser.error("--cap is required for a triage run")
    print(json.dumps(run(args.cap, args.limit, poll_seconds=args.poll, round_=args.round_, fit=args.fit,
                         resume=args.resume)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
