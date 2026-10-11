"""Benchmark items (eval/benchmark/SCHEMA.md): load, derive the item verdict, validate, build the views systems see.

    PYTHONPATH=src python -m coelacanth.benchmark.items eval/benchmark/v0/items.jsonl

validate() returns violations as strings that start with the number of the SCHEMA.md validation rule they break
("rule 0" is field structure). An empty list means the item passes the script checks; rules 5 and 6 are also
checked by a person (SCHEMA.md). `check_quotes=False` skips the verbatim part of rule 4, for checks run without the
cached texts; `skip_quotes` names evidence entries (evidence_key) whose rule 4 checks run elsewhere
(annotation.check_labels checks the quotes annotators add, with clearer locations).
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from ..env import REPO_ROOT
from ..quotes import QuoteChecker, normalize

RELATIONS = ("equivalent", "specialization", "generalization", "partial", "similar_mechanism", "different")
PRECEDENCE = RELATIONS[:5]  # most to least prior art; `different` never sets the verdict
VERDICTS = PRECEDENCE + ("nothing_close",)
NEGATIVE_KINDS = ("contrast", "natural_distractor")
ALREADY_DONE = ("equivalent", "specialization")
STATUSES = ("candidate", "pooled", "labelled", "adjudicated", "released", "excluded")
LABELLED = ("labelled", "adjudicated", "released")
FINAL = ("adjudicated", "released")
TEXT_LEVELS = ("full_text", "abstract")
ATTRIBUTIONS = ("cited", "not_cited", "unknown")
WORK_IDS = ("openalex", "doi", "arxiv", "pmid")
SOURCE_IDS = WORK_IDS + ("ai_origin",)  # an AI-origin claim's source is the generated method itself
MAX_CLAIM_WORDS = 120
MAX_QUOTE_WORDS = 40
LABELLED_CLAIM_FIELDS = ("text", "skeleton", "coined_names", "written_by", "checked_by", "source_work", "date",
                         "field", "domain", "computational", "chart")
ID_RE = re.compile(r"cf-\d{4}")
FOUND_BY_RE = re.compile(r"sys:[\w.\-]+@[1-9]\d*|annotator:[\w\-]+|exposure|mining:[\w.:\-]+")
GUIDE_DOI_RE = re.compile(r"\bdoi (10\.\d{4,9}/[^\s,;)]+)")
STOPWORDS = frozenset("a an and are as at be by for from in into is it its of on or that the their this to "
                      "under was were with".split())
CLAIM_CHECK_MISSING = "claim.written_by is required, and claim.checked_by from adjudication on"
YEAR_CUE_RE = re.compile(r"\b(?:in|since|from|until|till|before|after|by|circa|year|years|early|late|mid)\s+"
                         r"((?:1[6-9]|20)\d\d)\b|\b((?:1[6-9]|20)\d\d)s\b|\(((?:1[6-9]|20)\d\d)\)", re.IGNORECASE)


def load_items(path: str | Path) -> list[dict]:
    """Read a JSONL file of items; blank lines are skipped."""
    items = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as err:
                raise ValueError(f"{path}:{n}: {err}") from err
    return items


def derive_verdict(item: Mapping) -> str:
    """The strongest judged relation by precedence, or `nothing_close`."""
    judged = {c.get("relation") for c in item.get("candidates") or []}
    return next((relation for relation in PRECEDENCE if relation in judged), "nothing_close")


def system_view(item: Mapping) -> dict:
    """What a system under test sees (rule 11): the field-native claim text and the claim date, nothing else."""
    return {"text": item["claim"]["text"], "date": item["claim"]["date"]}


def skeleton_view(item: Mapping) -> dict:
    """The vocabulary-barrier ablation's view (eval/protocol.md section 13): the skeleton in place of the text."""
    return {"text": item["claim"]["skeleton"], "date": item["claim"]["date"]}


def guide_dois(labeling_md: str) -> frozenset[str]:
    """DOIs cited in LABELING.md's worked examples, lowercased; items built on them must be excluded (rule 8)."""
    return frozenset(m.group(1).rstrip(".").lower() for m in GUIDE_DOI_RE.finditer(labeling_md))


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _surname(author: str) -> str | None:
    """'Hoerl', 'Hoerl, Arthur E.', 'Arthur E. Hoerl' and 'Hoerl AE' all give 'Hoerl'."""
    if not author:
        return None
    if "," in author:
        return author.split(",", 1)[0].strip() or None
    tokens = [t for t in author.split() if not (t.isupper() and len(t.rstrip(".")) <= 3) and not t.endswith(".")]
    return tokens[-1] if tokens else None


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", normalize(text).casefold())


def _title_trigrams(title: str) -> set[tuple[str, ...]]:
    words = _words(title)
    grams = (tuple(words[i:i + 3]) for i in range(len(words) - 2))
    return {g for g in grams if any(w not in STOPWORDS for w in g)}


def _work_years(item: Mapping) -> set[str]:
    works = [item.get("claim", {}).get("source_work") or {}]
    works += [c.get("work") or {} for c in item.get("candidates") or []]
    years = {str(w.get("year")) for w in works if isinstance(w.get("year"), int)}
    years |= {w["publication_date"][:4] for w in works if _date(w.get("publication_date"))}
    return years


def claim_text_problems(item: Mapping) -> list[str]:
    """Rule 5, script part: surnames of the source's and the candidates' first authors (eponyms such as a named
    filter leak the answer), title words, years and coined names in claim.text; skeleton present and different."""
    claim = item["claim"]
    text = claim.get("text") or ""
    source = claim.get("source_work") or {}
    problems = []
    authors = [(source.get("first_author") or "", "the source's first author")]
    authors += [((c.get("work") or {}).get("first_author") or "", f"candidate {i}'s first author")
                for i, c in enumerate(item.get("candidates") or [])]
    for author, whose in authors:
        surname = _surname(author)
        if surname and re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", text):
            problems.append(f"claim.text contains {whose} surname {surname!r}")
    words = _words(text)
    text_grams = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
    shared = sorted(" ".join(g) for g in _title_trigrams(source.get("title") or "") & text_grams)
    if shared:
        problems.append(f"claim.text repeats three consecutive title words: {shared}")
    years = {y for y in _work_years(item) if re.search(rf"(?<![\d.,]){y}(?![\d.,]*\d)", text)}
    years |= {next(g for g in m.groups() if g) for m in YEAR_CUE_RE.finditer(text)}
    if years:
        problems.append(f"claim.text contains a year: {sorted(years)}")
    for name in claim.get("coined_names") or []:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            problems.append(f"claim.text contains the coined name {name!r}")
    skeleton = claim.get("skeleton")
    if item.get("status") in LABELLED or skeleton is not None:
        if not skeleton:
            problems.append("claim.skeleton is missing")
        elif normalize(skeleton).casefold() == normalize(text).casefold():
            problems.append("claim.skeleton repeats claim.text")
    if item.get("status") in LABELLED:
        if not claim.get("written_by") or (item.get("status") in FINAL and not claim.get("checked_by")):
            problems.append(CLAIM_CHECK_MISSING)
        elif claim.get("checked_by") and claim["written_by"] == claim["checked_by"]:
            problems.append("claim.checked_by must be a different annotator from claim.written_by")
    return problems


def evidence_key(i: int, entry: Mapping) -> tuple:
    """How validate's `skip_quotes` names an evidence entry: (candidate index, element, quote_candidate, quote_claim),
    any value that is not text or None as its repr."""
    return (i,) + tuple(v if v is None or isinstance(v, str) else repr(v)
                        for v in (entry.get("element"), entry.get("quote_candidate"), entry.get("quote_claim")))


def validate(item: Mapping, quotes: QuoteChecker | None = None,
             guide: frozenset[str] = frozenset(), check_quotes: bool = True,
             skip_quotes: frozenset[tuple] = frozenset()) -> list[str]:
    """Check one item against SCHEMA.md's validation rules; returns the violations."""
    out: list[str] = []

    def bad(rule: int, message: str) -> None:
        out.append(f"rule {rule}: {message}")

    status = item.get("status")
    if not isinstance(item.get("id"), str) or not ID_RE.fullmatch(item["id"]):
        bad(0, f"id must look like cf-0042, got {item.get('id')!r}")
    if item.get("version") != "v0":
        bad(0, "version must be 'v0'")
    if status not in STATUSES:
        bad(0, f"status must be one of {STATUSES}, got {status!r}")
    if status == "excluded" and not item.get("exclusion_reason"):
        bad(0, "excluded items need an exclusion_reason")
    claim = item.get("claim")
    if not isinstance(claim, Mapping):
        bad(0, "claim is missing")
        return out
    # claim.checked_by is required from adjudication on: a claim an annotator wrote on a bare packet is checked by a
    # second person when the item is adjudicated (eval/protocol.md decision 4), so a labelled item may lack it.
    required = (tuple(f for f in LABELLED_CLAIM_FIELDS if f != "checked_by" or status in FINAL)
                if status in LABELLED else ("source_work",))
    for field in required:
        if claim.get(field) is None:
            bad(0, f"claim.{field} is missing")
    text = claim.get("text")
    if text is not None and len(text.split()) > MAX_CLAIM_WORDS:
        bad(0, f"claim.text has {len(text.split())} words; the limit is {MAX_CLAIM_WORDS}")
    claim_date = _date(claim.get("date"))
    if claim.get("date") is not None and claim_date is None:
        bad(0, f"claim.date must be YYYY-MM-DD, got {claim.get('date')!r}")
    source = claim.get("source_work") or {}
    if source and not any(source.get(k) for k in SOURCE_IDS):
        bad(0, "claim.source_work needs an openalex, doi, arxiv, pmid or ai_origin identifier")
    chart = claim.get("chart") or []
    chart_ids = [e.get("id") for e in chart]
    key_ids = {e.get("id") for e in chart if e.get("key") is True}
    if chart or status in LABELLED:
        if not 2 <= len(chart) <= 6:
            bad(0, f"claim.chart needs two to six elements, has {len(chart)}")
        if len(set(chart_ids)) != len(chart_ids):
            bad(0, "claim.chart element ids repeat")
        if not key_ids:
            bad(0, "claim.chart has no key element")

    # Rule 1: labels and verdict.
    candidates = item.get("candidates") or []
    for i, cand in enumerate(candidates):
        relation = cand.get("relation")
        if relation is not None and relation not in RELATIONS:
            bad(1, f"candidate {i}: relation {relation!r} is not one of the six labels")
        if relation is not None and cand.get("gold") is not (relation in PRECEDENCE):
            bad(1, f"candidate {i}: gold must be {relation in PRECEDENCE} for relation {relation!r}")
        if relation is None and cand.get("gold"):
            bad(1, f"candidate {i}: an unjudged candidate cannot be gold")
    verdict = item.get("verdict")
    if verdict is not None and verdict not in VERDICTS:
        bad(1, f"verdict {verdict!r} is not a valid item verdict")
    elif verdict is not None and verdict != derive_verdict(item):
        bad(1, f"stored verdict {verdict!r} differs from the derived verdict {derive_verdict(item)!r}")
    elif verdict is None and status in LABELLED:
        bad(1, f"a {status} item needs a verdict")

    checker = quotes if quotes is not None else QuoteChecker()
    for i, cand in enumerate(candidates):
        relation = cand.get("relation")
        work = cand.get("work") or {}
        if not any(work.get(k) for k in WORK_IDS):
            bad(0, f"candidate {i}: work needs an openalex, doi, arxiv or pmid identifier")
        covered = set(cand.get("covered_elements") or [])
        analogous = set(cand.get("analogous_elements") or [])
        restricted = set(cand.get("restricted_elements") or [])
        evidence = cand.get("evidence") or []
        for name, ids in (("covered", covered), ("analogous", analogous), ("restricted", restricted),
                          ("evidence", {e.get("element") for e in evidence})):
            unknown = ids - set(chart_ids)
            if unknown:
                bad(0, f"candidate {i}: {name} elements {sorted(unknown)} are not in the chart")
        if relation is not None:
            if cand.get("attribution") not in ATTRIBUTIONS:
                bad(0, f"candidate {i}: attribution must be one of {ATTRIBUTIONS}")
            if cand.get("confidence") not in (1, 2, 3):
                bad(0, f"candidate {i}: confidence must be 1, 2 or 3")
        for entry in evidence:
            if entry.get("text_level") not in TEXT_LEVELS:
                bad(0, f"candidate {i}: evidence text_level must be one of {TEXT_LEVELS}")
            elif entry["text_level"] == "abstract" and (cand.get("confidence") or 0) > 2:
                bad(0, f"candidate {i}: abstract-only evidence allows confidence at most 2 (LABELING.md)")

        # Rule 2: evidence per element.
        with_evidence = {e.get("element") for e in evidence}
        if relation in ("equivalent", "specialization", "generalization", "partial", "similar_mechanism"):
            missing = (covered & key_ids) - with_evidence
            if missing:
                bad(2, f"candidate {i} ({relation}): covered key elements without evidence: {sorted(missing)}")
        if relation == "specialization":
            if not restricted:
                bad(2, f"candidate {i} (specialization): restricted_elements is empty")
            general = {e.get("element") for e in evidence if e.get("general_form") is True}
            if restricted - general:
                bad(2, f"candidate {i} (specialization): no general-form quote for {sorted(restricted - general)}")
        if relation == "similar_mechanism":
            # LABELING.md clarification 1: at least one key element corresponds by role only (with every key
            # element covered and only a non-key element analogous, the pair is equivalent)
            if not analogous & key_ids:
                bad(3, f"candidate {i} (similar_mechanism): no analogous key element")
            if key_ids - covered - analogous:
                bad(3, f"candidate {i} (similar_mechanism): key elements without a counterpart: "
                       f"{sorted(key_ids - covered - analogous)}")
            if analogous - with_evidence:
                bad(2, f"candidate {i} (similar_mechanism): analogous elements without evidence: "
                       f"{sorted(analogous - with_evidence)}")

        # Rule 3: coverage that the label requires.
        if relation == "partial" and not (len(covered & key_ids) >= 2 and key_ids - covered):
            bad(3, f"candidate {i} (partial): needs at least two covered key elements and one uncovered, "
                   f"covers {sorted(covered & key_ids)} of {sorted(key_ids)}")
        if relation == "equivalent" and key_ids - covered:
            bad(3, f"candidate {i} (equivalent): key elements not covered: {sorted(key_ids - covered)}")

        # Rule 4: every quote verbatim in its cached text, at most 40 words.
        for entry in evidence:
            if skip_quotes and evidence_key(i, entry) in skip_quotes:
                continue
            for field, key in (("quote_candidate", work.get("cache_key")), ("quote_claim", source.get("cache_key"))):
                quote = entry.get(field)
                if not quote:
                    bad(4, f"candidate {i}: evidence for {entry.get('element')} lacks {field}")
                    continue
                if len(quote.split()) > MAX_QUOTE_WORDS:
                    bad(4, f"candidate {i}: {field} for {entry.get('element')} is longer than {MAX_QUOTE_WORDS} words")
                if not check_quotes:
                    continue
                if not key:
                    bad(4, f"candidate {i}: no cache_key to check {field} for {entry.get('element')}")
                    continue
                try:
                    if checker.level(quote, key) is None:
                        bad(4, f"candidate {i}: {field} for {entry.get('element')} not found in {key}: {quote[:80]!r}")
                except FileNotFoundError:
                    bad(4, f"candidate {i}: no cached text {key!r}")

        # Rule 6: gold candidates are earlier than the claim.
        if relation in PRECEDENCE and claim_date is not None:
            cand_date = _date(work.get("publication_date"))
            year = work.get("year")
            if cand_date is not None:
                if cand_date >= claim_date:
                    bad(6, f"candidate {i}: dated {cand_date} is not before the claim date {claim_date}")
            elif isinstance(year, int):
                if year >= claim_date.year:
                    bad(6, f"candidate {i}: year {year} is not clearly before the claim date {claim_date}")
            else:
                bad(6, f"candidate {i}: gold candidate has no date")

        # Rule 10: provenance of every candidate.
        found_by = cand.get("found_by")
        if not found_by:
            bad(10, f"candidate {i}: found_by is empty")
        else:
            for entry in found_by:
                if not isinstance(entry, str) or not FOUND_BY_RE.fullmatch(entry):
                    bad(10, f"candidate {i}: found_by entry {entry!r} is not in a recognised form")

    # Rule 5: the claim text leaks nothing and a separate skeleton exists.
    if text is not None:
        for problem in claim_text_problems(item):
            bad(5, problem)

    # Rule 7: `negative` records how an item was built; whether it is a negative follows from its verdict.
    negative = item.get("negative")
    if negative is not None:
        if not isinstance(negative, Mapping) or negative.get("kind") not in NEGATIVE_KINDS:
            bad(7, f"negative.kind must be one of {', '.join(NEGATIVE_KINDS)}")
        elif negative["kind"] == "contrast" and not negative.get("from_item"):
            bad(7, "a contrast item names the positive it was edited from (negative.from_item)")

    # Rule 8: worked examples from the guide never enter the benchmark.
    provenance = item.get("provenance") or {}
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", (source.get("doi") or "").strip().lower())
    if provenance.get("source") == "guide" or (doi and doi in guide):
        if provenance.get("source") != "guide" or status != "excluded":
            bad(8, "a worked example from LABELING.md must have provenance.source 'guide' and status 'excluded'")

    # Rule 9: AI-generated items name the system.
    origin = item.get("origin") or {}
    if origin.get("kind") == "ai" and not origin.get("system"):
        bad(9, "origin.kind is 'ai' but origin.system is empty")

    # Rule 10: no unjudged candidate within the judged depth once adjudicated.
    if status in FINAL:
        depth = (item.get("pool") or {}).get("depth") or 0
        for i, cand in enumerate(candidates):
            if cand.get("relation") is not None:
                continue
            entries = cand.get("found_by") or []
            ranks = [int(e.rsplit("@", 1)[1]) for e in entries if isinstance(e, str) and e.startswith("sys:")]
            if len(ranks) < len(entries) or (ranks and min(ranks) <= depth):
                bad(10, f"candidate {i}: unjudged, but inside the judged pool of an item with status {status}")

    # Rule 11: the system view is the claim text and date only.
    if text is not None and claim.get("date") is not None and set(system_view(item)) != {"text", "date"}:
        bad(11, "the system view must hold claim.text and claim.date only")

    # Rule 12: a person confirmed each gold candidate's cross-field status (OpenAlex fields are only a suggestion).
    if status in LABELLED:
        for i, cand in enumerate(candidates):
            if cand.get("relation") in PRECEDENCE:
                if not isinstance(cand.get("cross_field"), bool):
                    bad(12, f"candidate {i}: gold candidate has no cross_field flag")
                if not cand.get("field_confirmed_by"):
                    bad(12, f"candidate {i}: no annotator confirmed the gold candidate's cross_field flag")
    return out


def validate_items(items: Iterable[Mapping], quotes: QuoteChecker | None = None,
                   guide: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Validate a whole file: per-item violations plus file-level ones (unique, sorted IDs) under '<file>'."""
    items = list(items)
    checker = quotes if quotes is not None else QuoteChecker()
    report = {}
    for n, item in enumerate(items):
        problems = validate(item, checker, guide)
        if problems:
            report[str(item.get("id", f"line {n + 1}"))] = problems
    ids = [str(item.get("id")) for item in items]
    file_problems = []
    if len(set(ids)) != len(ids):
        file_problems.append("rule 0: item ids repeat")
    if ids != sorted(ids):
        file_problems.append("rule 0: items are not sorted by id")
    if file_problems:
        report["<file>"] = file_problems
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.benchmark.items")
    parser.add_argument("path", help="items.jsonl")
    parser.add_argument("--labeling", default=str(REPO_ROOT / "eval" / "benchmark" / "LABELING.md"),
                        help="guide whose worked-example DOIs must stay excluded")
    args = parser.parse_args(argv)
    labeling = Path(args.labeling)
    guide = guide_dois(labeling.read_text(encoding="utf-8")) if labeling.exists() else frozenset()
    items = load_items(args.path)
    report = validate_items(items, guide=guide)
    for item_id, problems in report.items():
        for problem in problems:
            print(f"{item_id}: {problem}")
    print(f"{len(items)} items, {sum(1 for k in report if k != '<file>')} with violations")
    return 1 if report else 0


if __name__ == "__main__":
    raise SystemExit(main())
