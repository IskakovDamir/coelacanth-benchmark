"""Annotation packets: the pre-filled, draft items annotators label, and the checks they must pass.

A batch is two files under eval/benchmark/v0/batches/:
- `<batch>.json`, what annotators see: {"format": BATCH_FORMAT, "batch", "created", "labels": the six pair labels,
  "items": [packet, ...]}. No `found_by`, no provenance, no closed-book result: annotators work blind (LABELING.md).
- `<batch>-meta.json`, hidden from annotators: {"format": META_FORMAT, "batch", "items": {id: meta}}.

A packet is a draft item. Everything in it was pre-filled by Claude (on a contrast item, the claim text, skeleton and
chart by the annotator who wrote it, `prefill.by: annotator:<id>`, coelacanth.benchmark.contrast) and is marked as a
draft; humans choose every label (pair relations, key flags, covered elements, cross-field status, item verdict):

    {"id": "cf-0001",
     "prefill": {"by": "<model>", "date": "YYYY-MM-DD", "status": "draft",
                 "fields": ["claim.text", "claim.skeleton", "claim.chart", "candidates[].evidence", ...]},
     "claim": {"text", "skeleton", "coined_names": [...], "date", "field", "domain", "computational",
               "source_work": <work object with cache_key>,
               "chart": [{"id": "e1", "text": "...", "key": null}, ...]},       # key: null, the annotator sets it
     "candidates": [{"cid": "c1", "work": <work object with cache_key>,
                     "evidence": [{"element", "quote_candidate", "quote_claim", "location_candidate",
                                   "location_claim", "text_level", "general_form"}],
                     "attribution_hint": "cited" | "not_cited" | "unknown",   # from OpenAlex references, a draft
                     "note": "why this candidate is here, in one or two sentences (draft)"}],
     "notes": "draft notes for the annotator: what could not be read, what to check first"}

Meta per item: {"seed": what the item was built from, "found_by": {cid: [...]}, "exposure": {kind, work, date},
"origin": {kind, system, mode}, "negative": null or {kind}, "provenance": {source, mined_by, dataset, license,
attribution}, "exclusions": [work ids], "drafting_notes": "..."}.

Quotes are checked verbatim against the cached text named by each work's `cache_key` (coelacanth.quotes), at most
40 words each; `text_level` is `abstract` whenever either quote comes from an abstract-only text.

A bare packet (strip_prefill) has nothing pre-filled, for decision 25's crossover: `prefill` is {"status": "none",
"by": null, "date": null, "fields": []}, claim text, skeleton, chart and coined names are empty, candidates carry no
evidence, `attribution_hint` "unknown" and their generated neutral_note, and the packet notes are BARE_NOTES. It keeps
the facts: id, claim date, field, domain, computational, source work, candidate works, and no other field (check-batch
rejects one, since it could carry pre-filled text). `crossover` writes a batch's two annotator copies,
`<batch>-a1.json` and `<batch>-a2.json`, identical to the batch except that the plan's crossover items are bare for
one annotator (group X for the second, group Y for the first); each copy names its annotator (`"annotator": "a1"`)
and shares the batch's meta file.

Labels: what one annotator returns for one batch, exported by tools/annotate.html as `<batch>-<annotator>-labels.json`
(format v2; a v1 file, which has no added candidates, added evidence or chart edits, still checks and merges):

    {"format": LABELS_FORMAT, "batch": "b01", "annotator": "a1", "exported_at": "<ISO time>",
     "items": [{"id": "cf-0001",
                "minutes": 34.5,                                          # > 0 for done items
                "claim": {"text", "skeleton",                             # the annotator's versions
                          "chart": [{"id": "e1", "text": "...", "key": true}, ...],
                          "coined_names": [...]},                         # bare packets only: typed by the annotator
                "text_edited": false,                                     # claim.text differs from the pre-fill
                "candidates": [{"cid": "c1",
                                "relation": one of the six labels, or null only on a skipped item,
                                "covered_elements": [...], "analogous_elements": [...],
                                "restricted_elements": [...],
                                "attribution": "cited" | "not_cited" | "unknown",
                                "cross_field": true | false, "confidence": 1 | 2 | 3,
                                "rejected_evidence": [indices into the packet candidate's evidence],
                                "added_evidence": [evidence entries in the packet's shape],
                                "note": "..."}],
                "added_candidates": [{"cid": "a1",                        # works the annotator found
                                      "work": {"doi" | "openalex" | "arxiv" | "pmid": "...", "title", "year",
                                               "first_author", "field"},  # as typed
                                      "evidence": [evidence entries], the label fields of a packet candidate}],
                "verdict": derived by precedence over all candidates (items.derive_verdict),
                "status": "done" | "skipped",                             # anything not marked done is skipped
                "prefill_status": "draft" | "none",                       # v2: the packet's prefill.status when
                                                                          # labelled; it must match this copy's
                "notes": "..."}]}

Chart edits (v2): packet elements may be dropped and their texts edited; added elements take new ids from e7 (packet
charts hold e1 to e6, so an added element never takes a packet element's id), or from e1 on a bare packet; a done
item has two to six elements, at least one key, and no kept pre-filled evidence for a dropped element.

labels_problems() checks a labels file against its batch (the same rules the tool warns about live). check_labels()
adds what needs the network and the cache: each added work resolved by texts.fetch (free OpenAlex single-work lookups;
arXiv and Europe PMC texts cached for quotes), dated before the claim (by its arXiv version 1 when that is earlier
than OpenAlex's date, rule 6's earliest public version) and titled like the typed title, and every added quote
verbatim in its cached text (candidate quotes against the candidate's cache key, claim quotes against the source's),
each bad quote reported once; `--offline` skips both and says so. merge() turns the done items into SCHEMA.md items
(status `labelled`) with the hidden meta: added candidates get `found_by: ["annotator:<id>"]` and the resolved work
object, added evidence joins the kept pre-filled evidence. Skipped items are left out. In
`annotation.annotators[].relations` and the notes, packet candidates are keyed by cid (the same work in both
annotators' copies) and added candidates by their work (added_key: 'openalex:W123', else 'doi:...'), since a1, a2,
... are each annotator's own numbering. On a pre-filled packet the claim text and skeleton are recorded as written by
the pre-fill (claim_writer: `prefill:claude`, or on a contrast item the annotator named in `prefill.by`) and checked by
the annotator who labels it; on a bare packet, and on a contrast item its writer labels, as written by that annotator,
with `checked_by` empty until a second person checks them at adjudication (items.validate requires it from status
`adjudicated` on; check-labels and merge print a note). The merge command runs check-labels first and writes nothing
while it reports any problem.

    PYTHONPATH=src python -m coelacanth.benchmark.annotation check-packet <packet.json> [<meta.json>]
    PYTHONPATH=src python -m coelacanth.benchmark.annotation assemble b01 <drafts dir>
    PYTHONPATH=src python -m coelacanth.benchmark.annotation check-batch eval/benchmark/v0/batches/b01.json
    PYTHONPATH=src python -m coelacanth.benchmark.annotation crossover <batch.json> <plan.json> [--annotators a1 a2]
    PYTHONPATH=src python -m coelacanth.benchmark.annotation check-labels [--offline] <batch.json> <labels.json>
    PYTHONPATH=src python -m coelacanth.benchmark.annotation merge <batch.json> <labels.json> <out.jsonl>
"""

from __future__ import annotations

import argparse
import copy
import functools
import json
import re
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

from . import texts
from ..env import REPO_ROOT
from ..quotes import FULLTEXT_DIR, QuoteChecker
from .items import (ATTRIBUTIONS, CLAIM_CHECK_MISSING, FOUND_BY_RE, ID_RE, MAX_CLAIM_WORDS, MAX_QUOTE_WORDS,
                    NEGATIVE_KINDS, PRECEDENCE, RELATIONS, STOPWORDS, TEXT_LEVELS, WORK_IDS, SOURCE_IDS, _date, _words,
                    claim_text_problems, derive_verdict, evidence_key, guide_dois, validate)

BATCH_FORMAT = "coelacanth-batch/v1"
META_FORMAT = "coelacanth-batch-meta/v1"
BATCHES_DIR = REPO_ROOT / "eval" / "benchmark" / "v0" / "batches"
ABSTRACT_KEY_PREFIXES = ("oa-", "arxivabs-")
# pdf-<W id>: text of a PDF a person obtained (library access), cached by hand (cache/manual-pdf/README.md)
FULL_TEXT_KEY_PREFIXES = ("arxiv-", "pmc-", "aio-", "pdf-")
LABEL_FIELDS = ("relation", "covered_elements", "analogous_elements", "restricted_elements", "gold", "cross_field",
                "field_confirmed_by", "confidence", "attribution")
PROVENANCE_SOURCES = ("kulikowski", "declaration", "post_cutoff", "catalogue", "control", "negative", "ai_origin",
                      "guide")
# Words that say how a candidate relates to the claim. Notes annotators see may describe a work (what it is, its field,
# dates, identifiers, which text is cached) but never its relation to the claim: pre-annotation anchors annotators
# (Fort and Sagot 2010; Berzak et al. 2016; reference/research-notes/method-annotation.md). Such remarks belong in the
# hidden meta (`prefill_assessment`), for the adjudicator.
EVALUATIVE = re.compile(r"\b(match(es|ed|ing)?|by role|cover(s|ed|ing)?|identical|equivalen(t|ce)|correspond(s|ing|ence)?|"
                        r"special case|generali[sz](e|es|ed|ation)|speciali[sz](ation|ed)|recover(s|ed)?|analog(ous|ue|y)|"
                        r"similar mechanism|same (mechanism|method|claim|result|quantity|model|as)|partial(ly)?|already done|"
                        r"prior art|rediscover\w*|states the claim|is the claim)\b", re.IGNORECASE)
# Packet notes may describe what was read, identification and dating facts and neutral reading instructions, never
# which chart elements a candidate covers or how it relates; candidate notes are generated from facts (neutral_note).
NOTE_EXTRA = re.compile(r"\b(e\d+|role|roles|origin|credit\w*|declar\w*|attribut\w*)\b", re.IGNORECASE)
EXPOSURE_KINDS = ("letter", "comment", "erratum", "withdrawal", "review", "catalogue", "benchmark_import",
                  "our_search", None)


def key_level(key: str | None) -> str | None:
    """'abstract' or 'full_text' from a cache key's prefix; None when the prefix says nothing."""
    if not key:
        return None
    if key.startswith(ABSTRACT_KEY_PREFIXES):
        return "abstract"
    if key.startswith(FULL_TEXT_KEY_PREFIXES):
        return "full_text"
    return None


def _work_problems(work: Mapping | None, where: str, ids: tuple[str, ...] = WORK_IDS) -> list[str]:
    if not isinstance(work, Mapping):
        return [f"{where}: work is missing"]
    out = []
    if not any(work.get(k) for k in ids):
        out.append(f"{where}: work needs one of the identifiers {', '.join(ids)}")
    if not work.get("title"):
        out.append(f"{where}: work has no title")
    if not (_date(work.get("publication_date")) or isinstance(work.get("year"), int)):
        out.append(f"{where}: work has no date")
    return out


def packet_problems(packet: Mapping, meta: Mapping | None = None, quotes: QuoteChecker | None = None,
                    text_dir: Path | None = None) -> list[str]:
    """Everything wrong with one packet (and its meta, when given); an empty list means it is ready for annotators.
    A bare packet (is_bare) must carry no pre-filled text, chart, coined name or evidence."""
    out: list[str] = []
    text_dir = text_dir or FULLTEXT_DIR
    checker = quotes or QuoteChecker()
    if not isinstance(packet.get("id"), str) or not ID_RE.fullmatch(packet["id"]):
        out.append(f"id must look like cf-0042, got {packet.get('id')!r}")
    prefill = packet.get("prefill") or {}
    bare = is_bare(packet)
    source_work = (packet.get("claim") or {}).get("source_work") or {}
    if source_work.get("ai_origin"):
        cached = (text_dir / f"{source_work.get('cache_key')}.txt") if source_work.get("cache_key") else None
        if not source_work.get("method_text"):
            out.append("claim.source_work: an AI-origin method's text must travel in the packet (method_text)")
        elif cached is not None and cached.exists() and source_work["method_text"] != cached.read_text(encoding="utf-8"):
            out.append("claim.source_work.method_text differs from the cached text")
    if bare and dict(prefill) != BARE_PREFILL:
        out.append(f"a bare packet's prefill must be {BARE_PREFILL}")
    elif not bare and (prefill.get("status") != "draft" or not prefill.get("by") or not prefill.get("fields")):
        out.append("prefill needs by, fields and status 'draft'")
    elif not bare and str(prefill["by"]).startswith(ANNOTATOR_PREFILL) and not ANNOTATOR_RE.fullmatch(
            str(prefill["by"])[len(ANNOTATOR_PREFILL):]):
        out.append(f"prefill.by {prefill['by']!r}: an annotator's pre-fill names them as 'annotator:<id>'")
    claim = packet.get("claim") or {}
    for field in ("date", "source_work") if bare else ("text", "skeleton", "date", "source_work", "chart"):
        if not claim.get(field):
            out.append(f"claim.{field} is missing")
    if bare:
        # strip_prefill keeps these keys only: any other field could carry pre-filled text
        shapes = [("bare packet", packet, BARE_PACKET_KEYS), ("bare packet: claim", claim, BARE_CLAIM_KEYS),
                  ("bare packet: claim.source_work", claim.get("source_work"), BARE_WORK_KEYS)]
        for cand in packet.get("candidates") or []:
            if isinstance(cand, Mapping):
                shapes += [(f"bare packet: candidate {cand.get('cid')}", cand, BARE_CANDIDATE_KEYS),
                           (f"bare packet: candidate {cand.get('cid')} work", cand.get("work"), BARE_WORK_KEYS)]
        for where, obj, keys in shapes:
            extra = sorted(set(obj) - set(keys)) if isinstance(obj, Mapping) else []
            if extra:
                out.append(f"{where} has fields strip_prefill does not keep: {extra}")
        for field in ("text", "skeleton", "chart", "coined_names"):
            if claim.get(field):
                out.append(f"bare packet: claim.{field} is pre-filled; it must be empty")
        if packet.get("notes") != BARE_NOTES:
            out.append("bare packet: notes must be the standard instruction (BARE_NOTES)")
        for cand in packet.get("candidates") or []:
            where = f"bare packet: candidate {cand.get('cid')}"
            if cand.get("evidence"):
                out.append(f"{where} carries pre-filled evidence")
            if cand.get("attribution_hint") != "unknown":
                out.append(f"{where}: attribution_hint must be 'unknown'")
            if cand.get("note") != neutral_note(cand):
                out.append(f"{where}: note must be the generated neutral note")
    if not isinstance(claim.get("coined_names"), list):
        out.append("claim.coined_names must be a list (it may be empty)")
    if not isinstance(claim.get("computational"), bool):
        out.append("claim.computational must be true or false")
    for field in ("field", "domain"):
        if not claim.get(field):
            out.append(f"claim.{field} is missing (OpenAlex field and domain of the source work)")
    text = claim.get("text") or ""
    if len(text.split()) > MAX_CLAIM_WORDS:
        out.append(f"claim.text has {len(text.split())} words; the limit is {MAX_CLAIM_WORDS}")
    claim_date = _date(claim.get("date"))
    if claim.get("date") and claim_date is None:
        out.append(f"claim.date must be YYYY-MM-DD, got {claim.get('date')!r}")
    source = claim.get("source_work") or {}
    out += _work_problems(source, "claim.source_work", SOURCE_IDS)
    chart = claim.get("chart") or []
    chart_ids = [e.get("id") for e in chart]
    if not bare and not 2 <= len(chart) <= 6:
        out.append(f"claim.chart needs two to six elements, has {len(chart)}")
    if len(set(chart_ids)) != len(chart_ids) or not all(isinstance(i, str) and re.fullmatch(r"e\d+", i)
                                                        for i in chart_ids):
        out.append("claim.chart ids must be unique and look like e1, e2, ...")
    if any(e.get("key") is not None for e in chart):
        out.append("claim.chart key flags must be null: annotators choose the key elements")
    if any(not (e.get("text") or "").strip() for e in chart):
        out.append("claim.chart has an element without text")

    def check_quote(quote: str | None, key: str | None, where: str) -> str | None:
        if not quote:
            out.append(f"{where}: quote is empty")
            return None
        if len(quote.split()) > MAX_QUOTE_WORDS:
            out.append(f"{where}: quote has {len(quote.split())} words; the limit is {MAX_QUOTE_WORDS}")
        if not key:
            out.append(f"{where}: no cache_key to check the quote against")
            return None
        try:
            level = checker.level(quote, key)
        except FileNotFoundError:
            out.append(f"{where}: no cached text {key!r}")
            return None
        if level is None:
            out.append(f"{where}: quote not found verbatim in {key}: {quote[:80]!r}")
        return level

    if source.get("cache_key") and not (text_dir / f"{source['cache_key']}.txt").exists():
        out.append(f"claim.source_work: no cached text {source['cache_key']!r}")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list):
        out.append("candidates must be a list")
        candidates = []
    cids = [c.get("cid") for c in candidates]
    if len(set(cids)) != len(cids) or not all(isinstance(c, str) and re.fullmatch(r"c\d+", c) for c in cids):
        out.append("candidate cids must be unique and look like c1, c2, ...")
    for cand in candidates:
        where = f"candidate {cand.get('cid')}"
        work = cand.get("work") or {}
        out += _work_problems(work, where)
        labelled = [f for f in LABEL_FIELDS if cand.get(f) is not None]
        if labelled:
            out.append(f"{where}: carries label fields {labelled}; humans choose every label")
        if cand.get("attribution_hint") not in ("cited", "not_cited", "unknown"):
            out.append(f"{where}: attribution_hint must be cited, not_cited or unknown")
        cand_date = _date(work.get("publication_date"))
        if claim_date and cand_date and cand_date >= claim_date:
            out.append(f"{where}: dated {cand_date}, not before the claim date {claim_date} (rule 6)")
        elif claim_date and not cand_date and isinstance(work.get("year"), int) and work["year"] >= claim_date.year:
            out.append(f"{where}: year {work['year']} is not clearly before the claim date {claim_date} (rule 6)")
        for n, ev in enumerate(cand.get("evidence") or []):
            ew = f"{where} evidence {n + 1}"
            if ev.get("element") not in chart_ids:
                out.append(f"{ew}: element {ev.get('element')!r} is not in the chart")
            check_quote(ev.get("quote_candidate"), work.get("cache_key"), f"{ew} quote_candidate")
            check_quote(ev.get("quote_claim"), source.get("cache_key"), f"{ew} quote_claim")
            levels = {key_level(work.get("cache_key")), key_level(source.get("cache_key"))}
            if ev.get("text_level") not in TEXT_LEVELS:
                out.append(f"{ew}: text_level must be one of {TEXT_LEVELS}")
            elif "abstract" in levels and ev["text_level"] != "abstract":
                out.append(f"{ew}: text_level must be 'abstract' when a quote comes from an abstract-only text")
            if not isinstance(ev.get("general_form"), bool):
                out.append(f"{ew}: general_form must be true or false")
            for loc in ("location_candidate", "location_claim"):
                if not ev.get(loc):
                    out.append(f"{ew}: {loc} is missing")
    for where, text in [("notes", packet.get("notes") or "")] + [(f"candidate {c.get('cid')} note", c.get("note") or "")
                                                                 for c in candidates]:
        m = EVALUATIVE.search(text) or NOTE_EXTRA.search(text)
        if m:
            out.append(f"{where}: says how a candidate relates to the claim ({m.group(0)!r}); move it to the meta's "
                       "prefill_assessment")
    if not bare:  # a bare packet has no claim text yet; the annotator's is checked after labelling
        pseudo = {"claim": claim, "candidates": candidates}
        out += [f"rule 5: {p}" for p in claim_text_problems(pseudo)]
    if meta is not None:
        out += meta_problems(meta, cids)
    return out


BARE_PREFILL = {"status": "none", "by": None, "date": None, "fields": []}
BARE_NOTES = ("Nothing is pre-filled for this item. Read the source work, then write the claim text, the skeleton and "
              "the claim chart yourself. For each candidate, add the quote pairs your labels rest on, and add any "
              "earlier work you find.")
# The keys a bare packet may hold, the ones strip_prefill writes; work objects keep SCHEMA.md's fields (`ai_origin`
# for an AI-origin source).
BARE_PACKET_KEYS = ("id", "prefill", "claim", "candidates", "notes")
BARE_CLAIM_KEYS = ("text", "skeleton", "coined_names", "date", "field", "domain", "computational", "source_work", "chart")
BARE_CANDIDATE_KEYS = ("cid", "work", "evidence", "attribution_hint", "note")
BARE_WORK_KEYS = WORK_IDS + ("ai_origin", "title", "first_author", "n_authors", "year", "publication_date", "venue",
                             "field", "domain", "oa_url", "cache_key", "method_text")


def embed_method_text(packet: dict, text_dir: Path | None = None) -> dict:
    """An AI-origin claim's source is a generated method with no publisher page, so its text (the cached `aio-`
    record, written by this project) travels in the packet as `source_work.method_text`, where the offline tool
    shows it. Other sources are left as they are."""
    source = (packet.get("claim") or {}).get("source_work") or {}
    key = source.get("cache_key") or ""
    if source.get("ai_origin") and key.startswith("aio-"):
        path = (text_dir or FULLTEXT_DIR) / f"{key}.txt"
        if path.exists():
            source["method_text"] = path.read_text(encoding="utf-8")
    return packet


def is_bare(packet: Mapping) -> bool:
    """True for a packet with nothing pre-filled (strip_prefill)."""
    return (packet.get("prefill") or {}).get("status") == "none"


def claim_writer(packet: Mapping) -> str | None:
    """Who wrote a packet's claim text, skeleton and chart: the annotator's id when `prefill.by` is `annotator:<id>`
    (a contrast item), PREFILL_AUTHOR on any other pre-filled packet, None on a bare packet (its annotator writes
    it)."""
    if is_bare(packet):
        return None
    by = (packet.get("prefill") or {}).get("by")
    if isinstance(by, str) and by.startswith(ANNOTATOR_PREFILL):
        return by[len(ANNOTATOR_PREFILL):]
    return PREFILL_AUTHOR


def strip_prefill(packet: Mapping) -> dict:
    """The bare version of a packet, for decision 25's crossover: only the facts are kept (id, claim date, field,
    domain, computational, source work, candidate works); claim text, skeleton, chart, coined names and evidence are
    empty, attribution hints 'unknown', candidate notes generated from facts and the packet notes BARE_NOTES. Built
    from a whitelist, so a pre-filled field this function does not know about cannot slip through."""
    def work(w):
        return {k: copy.deepcopy(v) for k, v in w.items() if k in BARE_WORK_KEYS} if isinstance(w, Mapping) else w

    claim = packet.get("claim") or {}
    candidates = []
    for cand in packet.get("candidates") or []:
        bare_cand = {"cid": cand.get("cid"), "work": work(cand.get("work")), "evidence": [],
                     "attribution_hint": "unknown"}
        bare_cand["note"] = neutral_note(bare_cand)
        candidates.append(bare_cand)
    return {"id": packet.get("id"), "prefill": copy.deepcopy(BARE_PREFILL),
            "claim": {"text": "", "skeleton": "", "coined_names": [], "date": claim.get("date"),
                      "field": claim.get("field"), "domain": claim.get("domain"),
                      "computational": claim.get("computational"),
                      "source_work": work(claim.get("source_work")), "chart": []},
            "candidates": candidates, "notes": BARE_NOTES}


def neutral_note(cand: Mapping) -> str:
    """A candidate note built from facts only: field, year, venue, which text is cached and how many quote pairs are
    pre-filled. It never says how the candidate relates to the claim."""
    w = cand.get("work") or {}
    level = key_level(w.get("cache_key"))
    text = {"full_text": f"full text cached ({w['cache_key']})", "abstract": f"abstract only cached ({w['cache_key']})"}.get(
        level, "none cached, so quotes from it cannot be checked; read it from a library or publisher copy")
    year = w.get("year") or (w.get("publication_date") or "")[:4] or "year unknown"
    where = ", ".join(x for x in (str(year), w.get("venue")) if x)
    n = len(cand.get("evidence") or [])
    quotes = f"{n} pre-filled quote pair{'s' if n != 1 else ''}" if n else "no pre-filled quote pairs"
    return f"{w.get('field') or 'Field not in OpenAlex'}; {where}. Text: {text}. {quotes.capitalize()}."


def meta_problems(meta: Mapping, cids: list[str]) -> list[str]:
    out = []
    found = meta.get("found_by") or {}
    for cid in cids:
        entries = found.get(cid)
        if not entries:
            out.append(f"meta: found_by is empty for {cid}")
        elif not all(isinstance(e, str) and FOUND_BY_RE.fullmatch(e) for e in entries):
            out.append(f"meta: found_by for {cid} has an entry in an unknown form: {entries}")
    provenance = meta.get("provenance") or {}
    if provenance.get("source") not in PROVENANCE_SOURCES:
        out.append(f"meta: provenance.source must be one of {PROVENANCE_SOURCES}")
    for field in ("mined_by", "license"):
        if not provenance.get(field):
            out.append(f"meta: provenance.{field} is missing")
    exposure = meta.get("exposure")
    if not isinstance(exposure, Mapping) or exposure.get("kind") not in EXPOSURE_KINDS:
        out.append(f"meta: exposure.kind must be one of {EXPOSURE_KINDS}")
    if not isinstance(meta.get("exclusions"), list):
        out.append("meta: exclusions must be a list of work ids")
    origin = meta.get("origin") or {}
    if origin.get("kind") not in ("human", "ai"):
        out.append("meta: origin.kind must be human or ai")
    if "negative" not in meta:
        out.append("meta: negative must be present (null for positives)")
    elif meta["negative"] is not None and (not isinstance(meta["negative"], Mapping)
                                           or meta["negative"].get("kind") not in NEGATIVE_KINDS):
        out.append(f"meta: negative.kind must be one of {NEGATIVE_KINDS}")
    elif meta["negative"] is not None and meta["negative"]["kind"] == "contrast" and not meta["negative"].get("from_item"):
        out.append("meta: a contrast item names the positive it was edited from (negative.from_item)")
    return out


def assemble(batch: str, drafts: Path, out_dir: Path = BATCHES_DIR, created: str | None = None) -> dict:
    """Collect <drafts>/cf-*.json and <drafts>/meta/cf-*.json into the batch and meta files; returns the problems
    per item (the files are written either way, so a person can see them)."""
    packets = [embed_method_text(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(drafts.glob("cf-*.json"))]
    metas = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((drafts / "meta").glob("cf-*.json"))}
    checker = QuoteChecker()
    report = {}
    for packet in packets:
        problems = packet_problems(packet, metas.get(packet["id"]), checker)
        if packet["id"] not in metas:
            problems.append("meta file is missing")
        if problems:
            report[packet["id"]] = problems
    created = created or date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{batch}.json").write_text(json.dumps(
        {"format": BATCH_FORMAT, "batch": batch, "created": created, "labels": list(RELATIONS), "items": packets},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / f"{batch}-meta.json").write_text(json.dumps(
        {"format": META_FORMAT, "batch": batch, "items": metas}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return report


def check_batch(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    meta_path = _meta_path(path, data)
    metas = json.loads(meta_path.read_text(encoding="utf-8"))["items"] if meta_path.exists() else {}
    checker = QuoteChecker()
    report = {}
    if data.get("format") != BATCH_FORMAT:
        report["<file>"] = [f"format must be {BATCH_FORMAT}"]
    if "annotator" in data and not (isinstance(data["annotator"], str) and ANNOTATOR_RE.fullmatch(data["annotator"])):
        report.setdefault("<file>", []).append(f"annotator must be an ID such as a1, got {data['annotator']!r}")
    ids = [p.get("id") for p in data.get("items") or []]
    if len(set(ids)) != len(ids):
        report.setdefault("<file>", []).append("item ids repeat")
    for packet in data.get("items") or []:
        problems = packet_problems(packet, metas.get(packet.get("id")) if metas else None, checker)
        if metas and packet.get("id") not in metas:
            problems.append(f"meta: no entry in {meta_path.name}")
        if problems:
            report[packet.get("id")] = problems
    return report


def crossover(batch: Mapping, plan: Mapping, annotators: tuple[str, str] = ("a1", "a2")) -> dict[str, dict]:
    """Decision 25's crossover: the two annotators' copies of a batch, by annotator ID. The plan's group X
    (`crossover.X_prefilled_for_annotator_1`) is pre-filled for the first annotator and bare for the second, group Y
    the other way round; every other item is the same in both copies. Each copy names its annotator, so check-labels
    refuses the other annotator's labels against it."""
    groups = plan.get("crossover") or {}
    x = list(groups.get("X_prefilled_for_annotator_1") or [])
    y = list(groups.get("Y_prefilled_for_annotator_2") or [])
    if plan.get("batch") != batch.get("batch"):
        raise ValueError(f"the plan is for batch {plan.get('batch')!r}, not {batch.get('batch')!r}")
    if not x or not y:
        raise ValueError("the plan has no crossover groups X and Y")
    ids = {p.get("id") for p in batch.get("items") or []}
    missing = sorted(set(x + y) - ids)
    if missing:
        raise ValueError(f"crossover items not in batch {batch.get('batch')!r}: {missing}")
    overlap = sorted(set(x) & set(y))
    if overlap or len(set(x + y)) != len(x + y):
        raise ValueError(f"crossover items repeat or sit in both groups: {overlap or sorted(x + y)}")
    if len(set(annotators)) != 2 or not all(ANNOTATOR_RE.fullmatch(a) for a in annotators):
        raise ValueError(f"two different annotator IDs such as a1 and a2 are needed, got {list(annotators)}")
    copies = {}
    for annotator, bare in ((annotators[0], set(y)), (annotators[1], set(x))):
        out = {}
        for key, value in batch.items():
            out[key] = value
            if key == "batch":
                out["annotator"] = annotator
        out["items"] = [strip_prefill(p) if p.get("id") in bare else copy.deepcopy(p) for p in batch.get("items") or []]
        copies[annotator] = out
    return copies


LABELS_FORMAT_V1 = "coelacanth-labels/v1"
LABELS_FORMAT = "coelacanth-labels/v2"
LABELS_FORMATS = (LABELS_FORMAT_V1, LABELS_FORMAT)
LABEL_STATUSES = ("done", "skipped")
PREFILL_STATUSES = ("draft", "none")  # a packet's prefill.status: pre-filled or bare (strip_prefill)
ELEMENT_LISTS = ("covered_elements", "analogous_elements", "restricted_elements")
ANNOTATOR_RE = re.compile(r"[\w\-]+")
PREFILL_AUTHOR = "prefill:claude"
ANNOTATOR_PREFILL = "annotator:"  # prefill.by of a claim an annotator wrote (contrast items, benchmark.contrast)
ADDED_ELEMENT_FROM = 7  # packet charts hold e1 to e6, so an added element never takes a packet element's id
ELEMENT_ID_RE = re.compile(r"e[1-9]\d*")
ADDED_CID_RE = re.compile(r"a[1-9]\d*")
EVIDENCE_FIELDS = ("element", "quote_candidate", "quote_claim", "location_candidate", "location_claim", "text_level",
                   "general_form")
TYPED_WORK_FIELDS = ("title", "year", "first_author", "field")
# Identifiers an annotator may type, after the prefixes below are removed (DOI lowercased, OpenAlex uppercased, arXiv
# without its version).
TYPED_ID_RES = {"doi": re.compile(r"10\.\d{4,9}/\S+"), "openalex": re.compile(r"W\d+"),
                "arxiv": re.compile(r"\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Za-z\-]+)?/\d{7}"), "pmid": re.compile(r"\d+")}
ID_PREFIXES = {"doi": ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"),
               "openalex": ("https://openalex.org/",), "arxiv": ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                                                             "arxiv:"),
               "pmid": ("https://pubmed.ncbi.nlm.nih.gov/", "pmid:")}
ID_NAMES = {"doi": "a DOI (10.xxxx/...)", "openalex": "an OpenAlex work ID (W...)",
            "arxiv": "an arXiv ID (2101.00001)", "pmid": "a PubMed ID (digits)"}


def norm_id(kind: str, value) -> str | None:
    """A work identifier in one comparable form, or None when it does not look like one of its kind."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    v = str(value).strip()
    for prefix in ID_PREFIXES[kind]:
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
            break
    v = v.strip().rstrip("/")
    v = {"doi": v.lower(), "openalex": v.upper(), "arxiv": re.sub(r"v\d+$", "", v)}.get(kind, v)
    return v if TYPED_ID_RES[kind].fullmatch(v) else None


def work_ids(work: Mapping) -> set[str]:
    """'doi:10.1/x', 'openalex:W1', 'arxiv:1303.6700', 'pmid:123': a work's identifiers, comparable across sources."""
    return {f"{kind}:{n}" for kind in WORK_IDS if (n := norm_id(kind, work.get(kind)))}


def added_key(work: Mapping) -> str | None:
    """How merge names an added candidate in `annotation.annotators[].relations` and the notes: its first identifier
    in the order openalex, doi, arxiv, pmid, as 'openalex:W123' or 'doi:10.1/x'. Packet candidates keep their cid,
    which means the same work in both annotators' copies; an added candidate's cid (a1, a2, ...) does not."""
    for kind in WORK_IDS:
        n = norm_id(kind, work.get(kind))
        if n:
            return f"{kind}:{n}"
    return None


def fetch_identifier(work: Mapping) -> str | None:
    """What texts.fetch resolves for a typed work: its OpenAlex ID, DOI, pmid:N or arXiv:ID, in that order."""
    for kind, form in (("openalex", "{}"), ("doi", "{}"), ("pmid", "pmid:{}"), ("arxiv", "arXiv:{}")):
        n = norm_id(kind, work.get(kind))
        if n:
            return form.format(n)
    return None


def typed_work(work: Mapping) -> dict:
    """An added candidate's work as the annotator typed it, as a work object without a cached text; merge uses it
    only when the work was not resolved (check-labels --offline)."""
    obj = {kind: norm_id(kind, work.get(kind)) for kind in WORK_IDS}
    obj.update({field: work.get(field) for field in TYPED_WORK_FIELDS})
    obj["cache_key"] = None
    return obj


def first_added_element(packet: Mapping) -> int:
    """The lowest number an added chart element's id may take: e7 on a packet with a chart, e1 on a bare packet."""
    chart = (packet.get("claim") or {}).get("chart") or []
    if not chart:
        return 1
    numbers = [int(e["id"][1:]) for e in chart if isinstance(e.get("id"), str) and ELEMENT_ID_RE.fullmatch(e["id"])]
    return max([ADDED_ELEMENT_FROM] + [n + 1 for n in numbers])


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _evidence_problems(ev, where: str, chart_ids: list, abstract_only: bool, done: bool) -> list[str]:
    """An evidence entry an annotator added, in the packet's evidence shape with quotes of at most 40 words. On a
    skipped item only what is filled in must be well formed."""
    if not isinstance(ev, Mapping):
        return [f"{where}: must be an object with {', '.join(EVIDENCE_FIELDS)}"]
    out = []
    element = ev.get("element")
    if element in (None, ""):
        if done:
            out.append(f"{where}: no element")
    elif element not in chart_ids:
        out.append(f"{where}: element {element!r} is not in the chart")
    for field in ("quote_candidate", "quote_claim", "location_candidate", "location_claim"):
        value = ev.get(field)
        if value is not None and not isinstance(value, str):
            out.append(f"{where}: {field} must be text")
        elif not (value or "").strip():
            if done:
                out.append(f"{where}: {field} is {'empty' if field.startswith('quote') else 'missing'}")
        elif field.startswith("quote") and len(value.split()) > MAX_QUOTE_WORDS:
            out.append(f"{where}: {field} has {len(value.split())} words; the limit is {MAX_QUOTE_WORDS}")
    level = ev.get("text_level")
    if (level is None and done) or (level is not None and level not in TEXT_LEVELS):
        out.append(f"{where}: text_level must be one of {TEXT_LEVELS}")
    elif level is not None and abstract_only and level != "abstract":
        out.append(f"{where}: text_level must be 'abstract' when a quote comes from an abstract-only text")
    general = ev.get("general_form")
    if (general is None and done) or (general is not None and not isinstance(general, bool)):
        out.append(f"{where}: general_form must be true or false")
    return out


def _relation_problems(cand: Mapping, where: str, done: bool, chart_ids: list) -> tuple[list[str], dict]:
    """The relation and element lists of one candidate's labels; returns the problems and the element sets."""
    out = []
    relation = cand.get("relation")
    if relation is None:
        if done:
            out.append(f"{where}: no relation (every candidate of a done item needs one)")
    elif relation not in RELATIONS:
        out.append(f"{where}: relation {relation!r} is not one of the six labels")
    sets = {}
    for name in ELEMENT_LISTS:
        value = cand.get(name)
        if value is None:
            value = []
        if not isinstance(value, list):
            out.append(f"{where}: {name} must be a list")
            value = []
        unknown = sorted(set(value) - set(chart_ids), key=str)
        if unknown:
            out.append(f"{where}: {name} {unknown} are not in the chart")
        sets[name] = set(value)
    both = sorted(sets["covered_elements"] & sets["analogous_elements"])
    if both:
        out.append(f"{where}: elements {both} are marked both covered and analogous")
    return out, sets


def _judgement_problems(cand: Mapping, where: str, done: bool, sets: dict, key_ids: set, kept: list) -> list[str]:
    """Attribution, cross-field flag, confidence (at most 2 while abstract-level evidence is kept) and what each
    relation requires of the element lists."""
    out = []
    relation = cand.get("relation")
    attribution, cross_field, confidence = cand.get("attribution"), cand.get("cross_field"), cand.get("confidence")
    if (attribution is None and done) or (attribution is not None and attribution not in ATTRIBUTIONS):
        out.append(f"{where}: attribution must be one of {ATTRIBUTIONS}")
    if (cross_field is None and done) or (cross_field is not None and not isinstance(cross_field, bool)):
        out.append(f"{where}: cross_field must be true or false")
    valid_confidence = _number(confidence) and confidence in (1, 2, 3)
    if (confidence is None and done) or (confidence is not None and not valid_confidence):
        out.append(f"{where}: confidence must be 1, 2 or 3")
    abstract = any(isinstance(ev, Mapping) and ev.get("text_level") == "abstract" for ev in kept)
    if abstract and valid_confidence and confidence > 2:
        out.append(f"{where}: abstract-only evidence allows confidence at most 2 (reject that evidence or lower it)")
    if not done:
        return out
    covered, analogous, restricted = (sets[n] for n in ELEMENT_LISTS)
    if relation == "partial" and not (len(covered & key_ids) >= 2 and key_ids - covered):
        out.append(f"{where} (partial): needs at least two covered key elements and one uncovered, "
                   f"covers {sorted(covered & key_ids)} of {sorted(key_ids)}")
    if relation == "equivalent" and key_ids - covered:
        out.append(f"{where} (equivalent): key elements not covered: {sorted(key_ids - covered)}")
    if relation == "specialization" and not restricted:
        out.append(f"{where} (specialization): no restricted element")
    if relation == "similar_mechanism" and not analogous & key_ids:  # clarification 1: a key element by role only
        out.append(f"{where} (similar_mechanism): no analogous key element")
    if relation == "similar_mechanism" and key_ids - covered - analogous:
        out.append(f"{where} (similar_mechanism): key elements without a counterpart: "
                   f"{sorted(key_ids - covered - analogous)} (LABELING.md clarification 1: then the pair is different)")
    return out


def _typed_work_problems(work, where: str, done: bool, claim_date: date | None, known: dict) -> list[str]:
    """An added candidate's work as typed: an identifier of a known form, title, year, first author and field, not
    dated after the claim, not the source work, a packet candidate or another added candidate. `known` maps
    identifiers (work_ids) to what they belong to, and receives this work's."""
    if not isinstance(work, Mapping):
        return [f"{where}: work is missing"]
    out, ids = [], []
    typed = [kind for kind in WORK_IDS if work.get(kind) not in (None, "")]
    for kind in typed:
        n = norm_id(kind, work[kind])
        if n is None:
            out.append(f"{where}: {kind} {work[kind]!r} does not look like {ID_NAMES[kind]}")
        else:
            ids.append(f"{kind}:{n}")
    if not typed and done:
        out.append(f"{where}: work needs one of the identifiers {', '.join(WORK_IDS)}")
    for field in ("title", "first_author", "field"):
        value = work.get(field)
        if value is not None and not isinstance(value, str):
            out.append(f"{where}: work {field.replace('_', ' ')} must be text")
        elif done and not (value or "").strip():
            out.append(f"{where}: work has no {field.replace('_', ' ')}")
    year = work.get("year")
    if year is None:
        if done:
            out.append(f"{where}: work has no year")
    elif not (isinstance(year, int) and not isinstance(year, bool)):
        out.append(f"{where}: work year must be a whole number, got {year!r}")
    elif claim_date and year > claim_date.year:
        out.append(f"{where}: year {year} is after the claim date {claim_date} (rule 6)")
    same = sorted({known[k] for k in ids if k in known})
    if same:
        out.append(f"{where}: the same work as {', '.join(same)}")
    for k in ids:
        known.setdefault(k, where)
    return out


def _known_works(packet: Mapping) -> dict[str, str]:
    """The identifiers of a packet's source work and candidates, for spotting an added candidate that repeats one."""
    claim = packet.get("claim") or {}
    known = {k: "the source work" for k in work_ids(claim.get("source_work") or {})}
    for pc in packet.get("candidates") or []:
        for k in work_ids(pc.get("work") or {}):
            known.setdefault(k, f"candidate {pc.get('cid')}")
    return known


def _label_item_problems(packet: Mapping, item: Mapping, v2: bool = True) -> list[str]:
    """Problems of one labelled item against its packet. Completeness and the label rules apply to done items;
    on a skipped item only what is filled in must be well formed. `v2` (labels format v2) allows chart edits, added
    evidence and added candidates; a v1 item keeps the packet's chart ids and adds nothing."""
    out: list[str] = []
    status = item.get("status")
    if status not in LABEL_STATUSES:
        out.append(f"status must be one of {LABEL_STATUSES}, got {status!r}")
    done = status == "done"
    minutes = item.get("minutes")
    if done and not (_number(minutes) and minutes > 0):
        out.append(f"minutes must be a number above 0, got {minutes!r}")
    elif minutes is not None and not (_number(minutes) and minutes >= 0):
        out.append(f"minutes must be a number, got {minutes!r}")

    packet_claim = packet.get("claim") or {}
    bare = is_bare(packet)
    if v2:  # the copy the labels were made on: pre-filled labels must never pass on a bare packet, nor the reverse
        made_on = item.get("prefill_status")
        if made_on not in PREFILL_STATUSES:
            out.append(f"prefill_status must be 'draft' or 'none' (the copy the labels were made on), got {made_on!r}")
        elif made_on != (packet.get("prefill") or {}).get("status"):
            out.append(f"prefill_status {made_on!r} but the packet is {'bare' if bare else 'pre-filled'} (labels "
                       "made on another copy of the batch)")
    claim = item.get("claim")
    if not isinstance(claim, Mapping):
        return out + ["claim is missing"]
    if done:
        for field in ("text", "skeleton"):
            if not isinstance(claim.get(field), str) or not claim[field].strip():
                out.append(f"claim.{field} is empty")
    if not isinstance(item.get("text_edited"), bool):
        out.append("text_edited must be true or false")
    elif item["text_edited"] != (claim.get("text") != packet_claim.get("text")):
        out.append(f"text_edited is {str(item['text_edited']).lower()} but claim.text "
                   f"{'differs from' if claim.get('text') != packet_claim.get('text') else 'equals'} the pre-fill")
    packet_ids = [e.get("id") for e in packet_claim.get("chart") or []]
    chart = claim.get("chart") if isinstance(claim.get("chart"), list) else []
    if any(not isinstance(e, Mapping) for e in chart):
        out.append("claim.chart has an element that is not an object")
        chart = [e for e in chart if isinstance(e, Mapping)]
    chart_ids = [e.get("id") for e in chart]
    if not v2:
        if chart_ids != packet_ids:
            out.append(f"claim.chart ids {chart_ids} differ from the packet's {packet_ids} (texts may be edited, "
                       "ids not)")
    else:
        repeated = sorted({str(i) for i in chart_ids if chart_ids.count(i) > 1})
        if repeated:
            out.append(f"claim.chart ids repeat: {repeated}")
        start = first_added_element(packet)
        strange = [i for i in chart_ids if i not in packet_ids
                   and not (isinstance(i, str) and ELEMENT_ID_RE.fullmatch(i) and int(i[1:]) >= start)]
        if strange:
            out.append(f"claim.chart ids {strange} are neither packet elements nor new ids (added elements take "
                       f"e{start} onwards)")
        if done and not 2 <= len(chart) <= 6:
            out.append(f"claim.chart needs two to six elements, has {len(chart)}")
    for e in chart:
        if e.get("key") is not None and not isinstance(e.get("key"), bool):
            out.append(f"claim.chart {e.get('id')}: key must be true or false")
        elif done and e.get("key") is None:
            out.append(f"claim.chart {e.get('id')}: key must be true or false")
        if done and not (isinstance(e.get("text"), str) and e["text"].strip()):
            out.append(f"claim.chart {e.get('id')}: element text is empty")
    key_ids = {e.get("id") for e in chart if e.get("key") is True}
    if done and not key_ids:
        out.append("claim.chart has no key element")
    names = claim.get("coined_names")
    if bare:
        if names is None:
            if done:
                out.append("claim.coined_names is missing (the source work's coined names; the list may be empty)")
        elif not (isinstance(names, list) and all(isinstance(n, str) and n.strip() for n in names)):
            out.append("claim.coined_names must be a list of names")
    elif names is not None and names != list(packet_claim.get("coined_names") or []):
        out.append("claim.coined_names differ from the packet's: only a bare packet's coined names are typed")

    source_level = key_level((packet_claim.get("source_work") or {}).get("cache_key"))
    packet_cands = {c.get("cid"): c for c in packet.get("candidates") or []}
    cands = item.get("candidates")
    if not isinstance(cands, list):
        return out + ["candidates must be a list"]
    cids = [c.get("cid") for c in cands]
    if len(set(cids)) != len(cids):
        out.append(f"candidate cids repeat: {cids}")
    for cid in cids:
        if cid not in packet_cands:
            out.append(f"candidate {cid!r} is not in the packet")
    if done:
        for cid in packet_cands:
            if cid not in cids:
                out.append(f"candidate {cid}: not labelled")
    for cand in cands:
        cid = cand.get("cid")
        if cid not in packet_cands:
            continue
        where = f"candidate {cid}"
        problems, sets = _relation_problems(cand, where, done, chart_ids)
        out += problems
        evidence = packet_cands[cid].get("evidence") or []
        rejected = cand.get("rejected_evidence")
        if rejected is None:
            rejected = []
        if not isinstance(rejected, list) or not all(isinstance(n, int) and not isinstance(n, bool)
                                                     and 0 <= n < len(evidence) for n in rejected):
            out.append(f"{where}: rejected_evidence must list indices of the packet's {len(evidence)} evidence entries")
            rejected = []
        elif len(set(rejected)) != len(rejected):
            out.append(f"{where}: rejected_evidence repeats an index")
        kept = [ev for n, ev in enumerate(evidence) if n not in set(rejected)]
        if done:
            for n, ev in enumerate(evidence):
                if n not in set(rejected) and ev.get("element") not in chart_ids:
                    out.append(f"{where}: pre-filled evidence #{n + 1} is for {ev.get('element')}, which is not in "
                               "the chart (reject it)")
        added = cand.get("added_evidence")
        if added is not None and not v2:
            out.append(f"{where}: added_evidence needs labels format {LABELS_FORMAT}")
        elif added is not None and not isinstance(added, list):
            out.append(f"{where}: added_evidence must be a list")
        elif added:
            cand_level = key_level((packet_cands[cid].get("work") or {}).get("cache_key"))
            abstract_only = "abstract" in (source_level, cand_level)
            for n, ev in enumerate(added):
                out += _evidence_problems(ev, f"{where} added evidence {n + 1}", chart_ids, abstract_only, done)
            kept = kept + [ev for ev in added if isinstance(ev, Mapping)]
        out += _judgement_problems(cand, where, done, sets, key_ids, kept)

    added_cands = item.get("added_candidates")
    if added_cands is not None and not v2:
        out.append(f"added_candidates needs labels format {LABELS_FORMAT}")
        added_cands = []
    elif added_cands is not None and not isinstance(added_cands, list):
        out.append("added_candidates must be a list")
        added_cands = []
    if any(not isinstance(a, Mapping) for a in added_cands or []):
        out.append("added_candidates holds an entry that is not an object")
    added_cands = [a for a in added_cands or [] if isinstance(a, Mapping)]
    acids = [a.get("cid") for a in added_cands]
    if len(set(map(str, acids))) != len(acids) or not all(isinstance(c, str) and ADDED_CID_RE.fullmatch(c)
                                                          for c in acids):
        out.append(f"added candidate cids must be unique and look like a1, a2, ..., got {acids}")
    known = _known_works(packet)
    claim_date = _date(packet_claim.get("date"))
    for cand in added_cands:
        where = f"candidate {cand.get('cid')}"
        out += _typed_work_problems(cand.get("work"), where, done, claim_date, known)
        problems, sets = _relation_problems(cand, where, done, chart_ids)
        out += problems
        evidence = cand.get("evidence")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            out.append(f"{where}: evidence must be a list")
            evidence = []
        for n, ev in enumerate(evidence):
            out += _evidence_problems(ev, f"{where} evidence {n + 1}", chart_ids, source_level == "abstract", done)
        out += _judgement_problems(cand, where, done, sets, key_ids, evidence)

    derived = derive_verdict({"candidates": cands + added_cands})
    if done or item.get("verdict") is not None:
        if item.get("verdict") != derived:
            out.append(f"stored verdict {item.get('verdict')!r} differs from the derived verdict {derived!r}")
    return out


def labels_problems(batch: Mapping, labels: Mapping) -> list[str]:
    """Everything wrong with an annotator's labels file for this batch; an empty list means it can be merged. The
    network-free part of check-labels: added works are not resolved and quotes not checked here."""
    out: list[str] = []
    fmt = labels.get("format")
    if fmt not in LABELS_FORMATS:
        out.append(f"format must be one of {LABELS_FORMATS}, got {fmt!r}")
    v2 = fmt != LABELS_FORMAT_V1
    if labels.get("batch") != batch.get("batch"):
        out.append(f"labels are for batch {labels.get('batch')!r}, not {batch.get('batch')!r}")
    annotator = labels.get("annotator")
    if not isinstance(annotator, str) or not ANNOTATOR_RE.fullmatch(annotator):
        out.append(f"annotator must be an ID such as a1, got {annotator!r}")
    copy_for = batch.get("annotator")
    if copy_for is not None and annotator != copy_for:
        out.append(f"this batch file is the copy for annotator {copy_for}; these labels are by {annotator!r}")
    items = labels.get("items")
    if not isinstance(items, list):
        return out + ["items must be a list"]
    packets = {p.get("id"): p for p in batch.get("items") or []}
    ids = [item.get("id") for item in items]
    if len(set(ids)) != len(ids):
        out.append("item ids repeat")
    for item_id in ids:
        if item_id not in packets:
            out.append(f"item {item_id!r} is not in batch {batch.get('batch')!r}")
    for item_id in packets:
        if item_id not in ids:
            out.append(f"item {item_id} of the batch is missing (export it as skipped)")
    for item in items:
        packet = packets.get(item.get("id"))
        if packet is not None:
            out += [f"{item['id']}: {p}" for p in _label_item_problems(packet, item, v2)]
    return out


def _evidence_entry(ev: Mapping) -> dict:
    return {field: ev.get(field) for field in EVIDENCE_FIELDS}


def _merged_candidate(lc: Mapping, work: Mapping, found_by: list[str], evidence: list, annotator: str) -> dict:
    gold = lc["relation"] in PRECEDENCE
    return {"work": work, "found_by": found_by, "relation": lc["relation"],
            "covered_elements": list(lc.get("covered_elements") or []),
            "analogous_elements": list(lc.get("analogous_elements") or []),
            "restricted_elements": list(lc.get("restricted_elements") or []),
            "evidence": evidence, "attribution": lc["attribution"], "cross_field": lc["cross_field"],
            "field_confirmed_by": annotator if gold else None, "gold": gold, "confidence": lc["confidence"]}


def merge(batch: Mapping, meta: Mapping, labels: Mapping, works: Mapping | None = None) -> list[dict]:
    """The done items of a labels file as SCHEMA.md items (status `labelled`), with the hidden meta filled back in.
    Run labels_problems first; this assumes a well-formed file. Skipped items are left out. `works` maps (item id,
    cid) to the resolved work of each added candidate (resolve_added); one missing from it keeps the typed work,
    without a cached text."""
    works = works or {}
    packets = {p["id"]: p for p in batch["items"]}
    metas = meta["items"]
    annotator = labels["annotator"]
    out = []
    for lab in labels["items"]:
        if lab.get("status") != "done":
            continue
        packet = packets[lab["id"]]
        item_meta = metas.get(lab["id"])
        if item_meta is None:
            raise ValueError(f"{lab['id']}: no meta in the batch's meta file")
        pclaim, lclaim = packet["claim"], lab["claim"]
        bare = is_bare(packet)
        writer = annotator if bare else claim_writer(packet)
        claim = {"text": lclaim["text"], "skeleton": lclaim["skeleton"],
                 "coined_names": list((lclaim if bare else pclaim).get("coined_names") or []),
                 "written_by": writer, "checked_by": None if writer == annotator else annotator,
                 "source_work": pclaim["source_work"], "date": pclaim["date"], "field": pclaim["field"],
                 "domain": pclaim["domain"], "computational": pclaim["computational"],
                 "chart": [{"id": e["id"], "text": e["text"], "key": e["key"]} for e in lclaim["chart"]]}
        labelled = {c["cid"]: c for c in lab["candidates"]}
        candidates, relations, notes = [], {}, []
        for pc in packet.get("candidates") or []:
            cid = pc["cid"]
            lc = labelled[cid]
            rejected = set(lc.get("rejected_evidence") or [])
            relations[cid] = lc["relation"]
            if (lc.get("note") or "").strip():
                notes.append(f"{cid}: {lc['note'].strip()}")
            evidence = [ev for n, ev in enumerate(pc.get("evidence") or []) if n not in rejected]
            evidence += [_evidence_entry(ev) for ev in lc.get("added_evidence") or []]
            candidates.append(_merged_candidate(lc, pc["work"], list((item_meta.get("found_by") or {}).get(cid) or []),
                                                evidence, annotator))
        for ac in lab.get("added_candidates") or []:
            cid = ac["cid"]
            work = works.get((lab["id"], cid)) or typed_work(ac["work"])
            key = added_key(work) or cid  # a1, a2, ... are one annotator's own numbering
            relations[key] = ac["relation"]
            if (ac.get("note") or "").strip():
                notes.append(f"{key}: {ac['note'].strip()}")
            candidates.append(_merged_candidate(ac, work, [f"annotator:{annotator}"],
                                                [_evidence_entry(ev) for ev in ac.get("evidence") or []], annotator))
        if (lab.get("notes") or "").strip():
            notes.insert(0, lab["notes"].strip())
        item = {"id": lab["id"], "version": "v0", "status": "labelled", "claim": claim, "candidates": candidates}
        item["verdict"] = derive_verdict(item)
        item.update({
            "pool": None,
            "exposure": item_meta.get("exposure"),
            "origin": item_meta.get("origin"),
            "negative": item_meta.get("negative"),
            "provenance": item_meta.get("provenance"),
            "annotation": {"annotators": [{"id": annotator, "relations": relations, "verdict": item["verdict"],
                                           "minutes": lab["minutes"]}],
                           "prefill": packet.get("prefill"), "batch": batch.get("batch"), "adjudicated_by": None,
                           "notes": "\n".join(notes) or None},
            "exclusions": list(item_meta.get("exclusions") or [])})
        out.append(item)
    return sorted(out, key=lambda i: i["id"])


@functools.lru_cache(maxsize=None)
def _openalex():
    return texts.OpenAlexClient()


def fetch_work(identifier: str) -> dict:
    """texts.fetch with one OpenAlex client per run: the work object and its cached texts. Tests patch this."""
    return texts.fetch(identifier, _openalex())


def arxiv_published(aid: str) -> str | None:
    """The date of an arXiv preprint's first version, YYYY-MM-DD, from the arXiv API through the response cache
    (texts.arxiv_work, which also caches its abstract). Tests patch this."""
    return texts.arxiv_work(aid, _openalex().cache)[0].get("publication_date")


def _titles_agree(typed: str, resolved: str) -> bool:
    """At least half of the typed title's content words are in the resolved title: a subtitle or a spelling
    difference passes, a mistyped identifier that resolves to another work does not."""
    words = [w for w in _words(typed) if w not in STOPWORDS]
    found = set(_words(resolved))
    return not words or 2 * sum(w in found for w in words) >= len(words)


def _done_items(batch: Mapping, labels: Mapping):
    """(labelled item, packet) for every done item of the labels whose packet is in the batch."""
    packets = {p.get("id"): p for p in batch.get("items") or []}
    for item in labels.get("items") or []:
        if isinstance(item, Mapping) and item.get("status") == "done" and item.get("id") in packets:
            yield item, packets[item["id"]]


def _added_candidates(item: Mapping) -> list[Mapping]:
    added = item.get("added_candidates")
    return [a for a in added if isinstance(a, Mapping)] if isinstance(added, list) else []


def resolve_added(batch: Mapping, labels: Mapping, fetch: Callable[[str], dict] | None = None,
                  notes: list[str] | None = None,
                  arxiv_date: Callable[[str], str | None] | None = None) -> tuple[dict, list[str]]:
    """Resolve the works annotators added to done items with texts.fetch (free OpenAlex single-work lookups; arXiv
    and Europe PMC texts are cached for the quote checks). Returns {(item id, cid): work object} and the problems: an
    identifier that does not resolve, a work not dated before the claim, a resolved title unlike the typed one, the
    same work as the source, a packet candidate or another added candidate.

    Rule 6 dates a work by its earliest public version. OpenAlex's date is often the journal's, so when it is not
    before the claim and the work has an arXiv ID, the date of arXiv version 1 is read (`arxiv_date`, by default
    arxiv_published) and the earlier of the two is used and written into the work object; `notes` receives what
    was redated."""
    fetch = fetch or fetch_work
    arxiv_date = arxiv_date or arxiv_published
    notes = notes if notes is not None else []
    works, out = {}, []
    for item, packet in _done_items(batch, labels):
        claim_date = _date((packet.get("claim") or {}).get("date"))
        known = _known_works(packet)
        for cand in _added_candidates(item):
            typed = cand.get("work") if isinstance(cand.get("work"), Mapping) else {}
            ident = fetch_identifier(typed)
            if ident is None:
                continue  # labels_problems reports the missing or malformed identifier
            where = f"{item['id']}: candidate {cand.get('cid')}"
            try:
                work = dict(fetch(ident)["work"])
            except Exception as err:  # report it and go on with the other works
                out.append(f"{where}: {ident} could not be resolved ({type(err).__name__}: {err})")
                continue
            works[(item["id"], cand.get("cid"))] = work
            if not _titles_agree(str(typed.get("title") or ""), work.get("title") or ""):
                out.append(f"{where}: {ident} resolves to {work.get('title')!r}, not to the typed title "
                           f"{typed.get('title')!r}: check the identifier")
            cand_date, year = _date(work.get("publication_date")), work.get("year")
            used = "OpenAlex"
            aid = norm_id("arxiv", work.get("arxiv"))
            late = claim_date and ((cand_date and cand_date >= claim_date)
                                   or (not cand_date and isinstance(year, int) and year >= claim_date.year))
            if late and aid:
                try:
                    first = _date(arxiv_date(aid))
                except Exception as err:  # the OpenAlex date stands, and the problem says why
                    first, used = None, f"OpenAlex; arXiv version 1 not read: {type(err).__name__}: {err}"
                if first and (first < cand_date if cand_date else first.year <= year):
                    notes.append(f"{where}: dated by its arXiv version 1, {first}, not by the OpenAlex date "
                                 f"{cand_date or year}")
                    work["publication_date"], work["year"] = first.isoformat(), first.year
                    cand_date, year, used = first, first.year, "arXiv version 1"
                elif first:
                    used = f"OpenAlex; arXiv version 1 is {first}"
            if claim_date and cand_date and cand_date >= claim_date:
                out.append(f"{where}: dated {cand_date} ({used}), not before the claim date {claim_date} (rule 6)")
            elif claim_date and not cand_date and isinstance(year, int) and year >= claim_date.year:
                out.append(f"{where}: year {year} ({used}) is not clearly before the claim date {claim_date} (rule 6)")
            elif claim_date and not cand_date and not isinstance(year, int):
                out.append(f"{where}: {ident} resolves to a work without a date")
            this = f"candidate {cand.get('cid')}"
            same = sorted({known[k] for k in work_ids(work) if known.get(k, this) != this})
            if same:
                out.append(f"{where}: resolves to the same work as {', '.join(same)}")
            for k in work_ids(work):
                known.setdefault(k, this)
    return works, out


def _quote_problem(checker: QuoteChecker, quote, key: str | None) -> str | None:
    if not isinstance(quote, str) or not quote.strip():
        return None  # labels_problems reports empty quotes
    if not key:
        return "no cache_key to check the quote against"
    try:
        level = checker.level(quote, key)
    except FileNotFoundError:
        return f"no cached text {key!r}"
    return None if level else f"not found verbatim in {key}: {quote[:80]!r}"


def added_quote_problems(batch: Mapping, labels: Mapping, works: Mapping, checker: QuoteChecker) -> list[str]:
    """Every quote an annotator added to a done item, verbatim in its cached text: candidate quotes against the
    candidate's cache key (the resolved work's for an added candidate), claim quotes against the source's. An added
    candidate whose cached text is its abstract only gets abstract-level evidence."""
    out = []
    for item, packet in _done_items(batch, labels):
        source_key = ((packet.get("claim") or {}).get("source_work") or {}).get("cache_key")
        pcands = {c.get("cid"): c for c in packet.get("candidates") or []}
        entries = []
        for cand in item.get("candidates") if isinstance(item.get("candidates"), list) else []:
            pc = pcands.get(cand.get("cid")) if isinstance(cand, Mapping) else None
            added = cand.get("added_evidence") if pc else None
            for n, ev in enumerate(added if isinstance(added, list) else []):
                entries.append((f"candidate {cand['cid']} added evidence {n + 1}",
                                (pc.get("work") or {}).get("cache_key"), ev, False))
        for cand in _added_candidates(item):
            work = works.get((item["id"], cand.get("cid")))
            evidence = cand.get("evidence")
            if work is None or not isinstance(evidence, list):
                continue  # an unresolved work is reported by resolve_added
            for n, ev in enumerate(evidence):
                entries.append((f"candidate {cand.get('cid')} evidence {n + 1}", work.get("cache_key"), ev, True))
        for where, key, ev, added_work in entries:
            if not isinstance(ev, Mapping):
                continue
            for field, text_key in (("quote_candidate", key), ("quote_claim", source_key)):
                problem = _quote_problem(checker, ev.get(field), text_key)
                if problem:
                    out.append(f"{item['id']}: {where} {field}: {problem}")
            if added_work and key_level(key) == "abstract" and ev.get("text_level") != "abstract":
                out.append(f"{item['id']}: {where}: text_level must be 'abstract': only the abstract of this work is "
                           f"cached ({key})")
    return out


def _added_counts(batch: Mapping, labels: Mapping) -> tuple[int, int]:
    """Added works and added quote pairs in the done items."""
    works = quotes = 0
    for item, _ in _done_items(batch, labels):
        for cand in item.get("candidates") if isinstance(item.get("candidates"), list) else []:
            added = cand.get("added_evidence") if isinstance(cand, Mapping) else None
            quotes += len(added) if isinstance(added, list) else 0
        for cand in _added_candidates(item):
            works += 1
            quotes += len(cand.get("evidence")) if isinstance(cand.get("evidence"), list) else 0
    return works, quotes


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _meta_path(batch_path: Path, batch: Mapping | None = None) -> Path:
    """<batch>-meta.json next to the batch file, named after the batch: an annotator's copy (b02-a1.json) shares
    b02-meta.json."""
    name = batch.get("batch") if isinstance(batch, Mapping) else None
    path = batch_path.with_name(f"{name if isinstance(name, str) and name else batch_path.stem}-meta.json")
    if not path.exists() and (batch_path.parent.parent / path.name).exists():
        return batch_path.parent.parent / path.name  # an assigned copy in batches/assigned/ shares batches/<batch>-meta
    return path


def awaiting_claim_check(item: Mapping) -> bool:
    """A merged item whose claim an annotator wrote (on a bare packet, or as the writer of a contrast item they label
    too) and no second person has checked yet; valid as `labelled`, it needs claim.checked_by before it can be
    adjudicated."""
    claim = item.get("claim") or {}
    return claim.get("checked_by") is None and claim.get("written_by") not in (None, PREFILL_AUTHOR)


def _added_evidence_keys(batch: Mapping, labels: Mapping) -> dict[str, frozenset]:
    """For each done item, the merged evidence entries an annotator added (items.evidence_key, candidate indices in
    merge's order: packet candidates, then added ones). labels_problems and added_quote_problems check their quotes,
    so items.validate skips them (`skip_quotes`) instead of reporting each bad quote a second time."""
    out = {}
    for item, packet in _done_items(batch, labels):
        labelled = {c.get("cid"): c for c in item.get("candidates") or [] if isinstance(c, Mapping)}
        pcands = packet.get("candidates") or []
        lists = [(i, (labelled.get(pc.get("cid")) or {}).get("added_evidence")) for i, pc in enumerate(pcands)]
        lists += [(len(pcands) + j, ac.get("evidence")) for j, ac in enumerate(_added_candidates(item))]
        out[item["id"]] = frozenset(evidence_key(i, ev) for i, evidence in lists
                                    for ev in (evidence if isinstance(evidence, list) else []) if isinstance(ev, Mapping))
    return out


def _validate_merged(items: list[dict], checker: QuoteChecker, check_quotes: bool = True,
                     skip: Mapping[str, frozenset] | None = None) -> tuple[list[str], list[str]]:
    """items.validate on merged items, as (problems, notes); `skip` maps item ids to the entries validate leaves to
    the added-evidence checks (_added_evidence_keys). A bare packet's claim was written by the annotator and awaits a
    second person's check at adjudication: its empty claim.checked_by is a note here, not a problem."""
    labeling = REPO_ROOT / "eval" / "benchmark" / "LABELING.md"
    guide = guide_dois(labeling.read_text(encoding="utf-8")) if labeling.exists() else frozenset()
    unchecked = {"rule 0: claim.checked_by is missing", f"rule 5: {CLAIM_CHECK_MISSING}"}
    problems, notes = [], []
    for item in items:
        claim = item["claim"]
        awaiting = awaiting_claim_check(item)
        found = validate(item, checker, guide, check_quotes, (skip or {}).get(item["id"], frozenset()))
        if awaiting:
            bare = ((item.get("annotation") or {}).get("prefill") or {}).get("status") == "none"
            how = "on a bare packet" if bare else "as this item's pre-fill, and labelled by them too"
            notes.append(f"{item['id']}: claim written by {claim['written_by']} {how}; a second person "
                         "checks it against the source (claim.checked_by) before adjudication")
        problems += [f"{item['id']}: {p}" for p in found if not (awaiting and p in unchecked)]
    return problems, notes


def _check_labels(batch_path: Path, labels_path: Path, quotes: QuoteChecker | None = None, offline: bool = False,
                  fetch: Callable[[str], dict] | None = None,
                  notes: list[str] | None = None) -> tuple[list[str], list[dict]]:
    """check_labels, returning the merged items too (none when the meta file is missing or merge failed)."""
    notes = notes if notes is not None else []
    batch, labels = _read_json(batch_path), _read_json(labels_path)
    problems = [f"labels: {p}" for p in labels_problems(batch, labels)]
    meta_path = _meta_path(batch_path, batch)
    if not meta_path.exists():
        return problems + [f"meta: {meta_path.name} not found next to the batch"], []
    meta = _read_json(meta_path)
    if meta.get("batch") != batch.get("batch"):
        problems.append(f"meta: {meta_path.name} is for batch {meta.get('batch')!r}")
    checker = quotes or QuoteChecker()
    works: dict = {}
    if offline:
        n_works, n_quotes = _added_counts(batch, labels)
        notes.append(f"offline: {n_works} added works not resolved or dated, and no quote checked verbatim "
                     f"({n_quotes} added quote pairs)")
    else:
        works, unresolved = resolve_added(batch, labels, fetch, notes)
        problems += [f"added: {p}" for p in unresolved]
        problems += [f"added: {p}" for p in added_quote_problems(batch, labels, works, checker)]
    try:
        items = merge(batch, meta, labels, works)
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        return problems + [f"merge: failed ({type(err).__name__}: {err})"], []
    merged_problems, merged_notes = _validate_merged(items, checker, not offline, _added_evidence_keys(batch, labels))
    notes += merged_notes
    return problems + merged_problems, items


def check_labels(batch_path: Path, labels_path: Path, quotes: QuoteChecker | None = None, offline: bool = False,
                 fetch: Callable[[str], dict] | None = None, notes: list[str] | None = None) -> list[str]:
    """labels_problems; unless `offline`, the added works resolved and dated (resolve_added) and the added quotes
    checked verbatim (added_quote_problems); then items.validate on every merged item, quotes against cache/fulltext
    unless `offline` (the quotes annotators added are reported once, by the checks above). `notes`, when given,
    receives what is not a problem: what --offline skipped, works dated by their arXiv version 1, and bare claims
    that await a second person's check."""
    return _check_labels(batch_path, labels_path, quotes, offline, fetch, notes)[0]


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.annotation")
    sub = parser.add_subparsers(dest="command", required=True)
    cp = sub.add_parser("check-packet")
    cp.add_argument("packet")
    cp.add_argument("meta", nargs="?")
    asm = sub.add_parser("assemble")
    asm.add_argument("batch")
    asm.add_argument("drafts")
    cb = sub.add_parser("check-batch")
    cb.add_argument("path")
    cx = sub.add_parser("crossover", help="write the two annotators' copies of a batch, the plan's crossover items "
                                          "bare for one of them (decision 25)")
    cx.add_argument("batch")
    cx.add_argument("plan")
    cx.add_argument("--annotators", nargs=2, default=["a1", "a2"], metavar=("FIRST", "SECOND"))
    cl = sub.add_parser("check-labels", help="check an exported labels file against its batch and hidden meta")
    cl.add_argument("batch")
    cl.add_argument("labels")
    cl.add_argument("--offline", action="store_true",
                    help="do not resolve added works and check no quote verbatim (no network, no cached texts)")
    nt = sub.add_parser("notes", help="rewrite every candidate note of a draft packet from facts (neutral_note)")
    nt.add_argument("packet")
    mg = sub.add_parser("merge", help="check like check-labels, then write the done items as SCHEMA.md items (JSONL); "
                                      "bare items awaiting a claim check go to <out stem>.pending.jsonl")
    mg.add_argument("batch")
    mg.add_argument("labels")
    mg.add_argument("out")
    args = parser.parse_args(argv)
    if args.command == "crossover":
        batch_path = Path(args.batch)
        try:
            copies = crossover(_read_json(batch_path), _read_json(Path(args.plan)), tuple(args.annotators))
        except ValueError as err:
            print(f"nothing written: {err}")
            return 1
        report = {}
        for annotator, data in copies.items():
            path = batch_path.with_name(f"{batch_path.stem}-{annotator}.json")
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            report[path.name] = {"bare": [p["id"] for p in data["items"] if is_bare(p)], "problems": check_batch(path)}
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 1 if any(r["problems"] for r in report.values()) else 0
    if args.command == "check-labels":
        notes: list[str] = []
        problems = check_labels(Path(args.batch), Path(args.labels), offline=args.offline, notes=notes)
        for problem in problems:
            print(problem)
        for note in notes:
            print(f"note: {note}")
        labels = _read_json(Path(args.labels))
        n_done = sum(1 for i in labels.get("items") or [] if isinstance(i, Mapping) and i.get("status") == "done")
        print(f"{n_done} done items, {len(problems)} problems")
        return 1 if problems else 0
    if args.command == "merge":
        notes = []
        problems, items = _check_labels(Path(args.batch), Path(args.labels), notes=notes)
        for problem in problems:
            print(problem)
        if problems:
            print(f"nothing written: {len(problems)} problems, the same as check-labels reports; fix them first")
            return 1
        out = Path(args.out)
        _write_jsonl(out, items)
        for note in notes:
            print(f"note: {note}")
        print(f"wrote {len(items)} items to {out}; each passes items.validate")
        return 0
    if args.command == "notes":
        path = Path(args.packet)
        packet = json.loads(path.read_text(encoding="utf-8"))
        for cand in packet.get("candidates") or []:
            cand["note"] = neutral_note(cand)
        path.write_text(json.dumps(packet, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"id": packet.get("id"), "notes": [c["note"] for c in packet.get("candidates") or []]},
                         ensure_ascii=False))
        return 0
    if args.command == "check-packet":
        packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8")) if args.meta else None
        problems = packet_problems(packet, meta)
        print(json.dumps({"id": packet.get("id"), "ok": not problems, "problems": problems}, indent=1,
                         ensure_ascii=False))
        return 0 if not problems else 1
    report = assemble(args.batch, Path(args.drafts)) if args.command == "assemble" else check_batch(Path(args.path))
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if not report else 1


if __name__ == "__main__":
    raise SystemExit(main())
