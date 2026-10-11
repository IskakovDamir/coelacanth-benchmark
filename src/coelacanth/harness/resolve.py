"""After a run: resolve each candidate to a work, date it by its earliest version, apply the leakage rule, clean the
ranked list and map the verdict (docs/design/harness.md section 7; eval/protocol.md sections 4, 7 and 12).

Resolution takes the first route that answers: the candidate's OpenAlex ID, its DOI, its arXiv ID (an OpenAlex lookup,
else the arXiv API), its PMID, then one title search accepted only when exactly one result matches strictly
(`closedbook.match_gold`). A work is dated by its OpenAlex `publication_date`, where a January-first date counts as its
year only (OpenAlex stores a year-only date that way); only when that date is a potential leak (on or after the claim
date, or missing) is it moved back to the arXiv v1 date or to an earlier record with the same title and first author
(surname, and first initial when both give one), whose W IDs then count as versions of the same work. An unresolved
candidate citing a web page is dated by the version read. Leaks are removed (an unresolved citation that strictly
matches an excluded work's title is an excluded work), duplicates collapse to their first rank, unresolved candidates
with an earlier model-given year stay as placeholders (never gold, never pooled), and the verdict is mapped on what
remains, so a leaked or excluded work can never yield "already done".

A lookup the services fail to answer (OpenAlex or the arXiv API down after the retries, OpenAlex's daily budget spent)
says nothing about the work, so it never becomes a placeholder or a leak: the row is written with status
`infra_error` and its `resolution_error`, and running `resolve_run` again resolves it once the services answer.

Search-result lists in an agent's trace are not evidence (decision 28); their scholarly entries are resolved by the
free ID lookups and dated, and the counts are reported as the row's `exposure`, descriptively.

`resolve_run` writes `resolved.jsonl` and logs every OpenAlex call of the resolver in `resolve-calls.jsonl`, booked to
the harness at list price, never to a system's cap.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..benchmark import texts
from ..benchmark.items import _surname, derive_verdict, load_items
from ..closedbook import _content_words, match_gold
from ..data.openalex import PRICE_USD, BudgetExceeded, OpenAlexClient, OpenAlexError
from ..env import REPO_ROOT
from ..llm import append_jsonl, read_jsonl, utc_stamp
from .retrieval import LOOKUP, canonical_id, exclusion_keys, work_keys

MEASUREMENT_A = REPO_ROOT / "eval" / "results" / "measurement-a"
CONFIGS = MEASUREMENT_A / "configs"
ITEM_FILES = (REPO_ROOT / "eval" / "benchmark" / "v0" / "items.jsonl", MEASUREMENT_A / "pilot-items.jsonl")
ID_ROUTES = (("openalex_id", "openalex"), ("doi", "doi"), ("arxiv", "arxiv"), ("pmid", "pmid"))
SEARCH_LISTS = ("search_results",)  # stored, not counted as evidence (section 13, point 1); their entries give exposure
MapVerdict = Callable[[Mapping, Any, list, Any], Mapping]
# The services' answers about an identifier or a query ("HTTP 404 for <url>", from openalex._get and texts._get).
# Any other failure (5xx or 429 after the retries, 401 or 403, a dropped connection) is the service failing.
NOT_FOUND_RE = re.compile(r"HTTP (400|404|410) for ")
HTTP_RE = re.compile(r"HTTP \d{3} for ")


class ResolutionUnavailable(RuntimeError):
    """A lookup the resolver needs went unanswered: OpenAlex or the arXiv API failing, or OpenAlex's daily budget
    spent. It says nothing about the work (decision 30 removes only citations that resolve to no work), so the row is
    marked for another resolution instead of getting placeholders or leaks."""


class _Booked:
    """The client as the resolver uses it: each OpenAlex call is counted at list price, whether or not the cache
    answered it (an upper bound), and written through `log` as one resolve-calls.jsonl row. A failure that is not an
    answer about the work is raised as `ResolutionUnavailable`, which no lookup in this module or in
    `retrieval.exclusion_keys` reads as "not found"."""

    def __init__(self, client: Any, log: Callable[[dict], None] | None = None, tags: Mapping | None = None) -> None:
        self.client, self.cache, self.log, self.tags = client, client.cache, log, dict(tags or {})
        self.searches, self.usd = 0, 0.0

    def work(self, work_id: str, select: Iterable[str] | None = None) -> dict:
        return self._book("entity", lambda: self.client.work(work_id, select=select))

    def works(self, **query) -> dict:
        searching = bool(query.get("search")) or ".search" in str(query.get("filter") or "")
        return self._book("search" if searching else "list", lambda: self.client.works(**query))

    def _book(self, kind: str, call: Callable[[], dict]) -> dict:
        error = "failed"
        try:
            data = call()
            error = None
            return data
        except (OpenAlexError, BudgetExceeded, json.JSONDecodeError) as err:
            if isinstance(err, OpenAlexError) and NOT_FOUND_RE.match(str(err)):
                error = "not_found"
                raise
            error = "unavailable"
            raise ResolutionUnavailable(f"OpenAlex: {err}"[:300]) from err
        except ValueError:  # an identifier normalize_work_id does not accept: nothing was sent
            error = "not_found"
            raise
        finally:
            ok = error is None
            cost = PRICE_USD[kind] if ok else 0.0
            if ok and kind == "search":
                self.searches += 1
            self.usd += cost
            if self.log:
                self.log({"at": utc_stamp(), **self.tags, "step": "resolve", "kind": f"openalex_{kind}", "model": None,
                          "cost_usd": cost, "cost_source": "list_price", "tokens_all": None, "budget_left_usd": None,
                          "is_error": not ok, "error": error})


def _booked(client: Any) -> _Booked:
    return client if isinstance(client, _Booked) else _Booked(client)


class _Recorder:
    """The client as `exclusion_keys` uses it, keeping every work it looked up (their titles and first authors match
    unresolved citations of an excluded work)."""

    def __init__(self, client: Any) -> None:
        self.client, self.cache, self.seen = client, client.cache, []

    def work(self, work_id: str, select: Iterable[str] | None = None) -> dict:
        work = self.client.work(work_id, select=select)
        self.seen.append(work)
        return work

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


# ---------- dates ----------

def _from_openalex(work: Mapping) -> bool:
    return str(work.get("id") or "").startswith("https://openalex.org/")


def _brief(work: Mapping) -> dict:
    """SCHEMA work-object fields of an OpenAlex work, or the work object `texts.arxiv_work` returned."""
    return texts.work_object(work, None) if _from_openalex(work) else dict(work)


def _year(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and re.fullmatch(r"\d{4}", value.strip()) else None


def _year_only(d: str) -> bool:
    return len(d) == 4 and d.isdigit()


def _sortable(d: str) -> str:
    return d + "-12-31" if _year_only(d) else d  # a bare year counts as its last day: a full date in it wins


def _earlier(a: str | None, b: str | None) -> bool:
    return a is not None and (b is None or _sortable(a) < _sortable(b))


def _before(d: str, claim_date: str) -> bool:
    return int(d) < int(claim_date[:4]) if _year_only(d) else d < claim_date


def _fold(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _openalex_date(d: str) -> str:
    """An OpenAlex date, with YYYY-01-01 read as the year alone: OpenAlex stores a year-only date that way (14 of the
    114 dated works in batches/b01.json fall on 1 January, measured 2026-10-07), so the same-year rule applies to it
    (decision 29, SCHEMA rule 6). A real 1 January date is read as its year too, which errs toward a leak."""
    return d[:4] if d.endswith("-01-01") else d


def _recorded_date(work: Mapping) -> tuple[str | None, str]:
    obj = _brief(work)
    d = obj.get("publication_date")
    if d and _from_openalex(work):
        d = _openalex_date(d)
        return d, "year_only" if _year_only(d) else "openalex"
    if d:
        return d, "arxiv_v1"
    if _year(obj.get("year")):
        return str(obj["year"]), "year_only"
    return None, "none"


def _arxiv_record(aid: str, client: Any) -> dict | None:
    """The arXiv API's record of a preprint, or None when the API has no entry for it. A failing API raises
    `ResolutionUnavailable`."""
    try:
        return texts.arxiv_work(aid, client.cache)[0]
    except texts.TextError as err:
        if HTTP_RE.match(str(err)) and not NOT_FOUND_RE.match(str(err)):
            raise ResolutionUnavailable(f"arXiv API: {err}"[:300]) from err
        return None
    except (OSError, ET.ParseError) as err:
        raise ResolutionUnavailable(f"arXiv API: {err}"[:300]) from err


def _arxiv_v1(aid: str, client: Any) -> str | None:
    record = _arxiv_record(aid, client)
    return record.get("publication_date") if record else None


def _author_key(name: str) -> tuple[str, str]:
    """(surname, first initial) of an author name, folded; the initial is "" when the name gives none. 'Ann Smith',
    'A. Smith', 'Smith, Ann' and 'Smith A' all give ('smith', 'a')."""
    surname = _surname(name or "") or ""
    if not surname:
        return "", ""
    if "," in name:
        given = [name.split(",", 1)[1]]
    else:
        before, _, after = name.rpartition(surname)
        given = [before, after]
    initial = next((w[0] for part in given for w in re.findall(r"[^\W\d_]+", part)), "")
    return _fold(surname), _fold(initial)


def _earlier_record(obj: Mapping, client: Any) -> tuple[str, list[str]] | None:
    """(the earliest date, the W IDs of the matching records, the one that gives that date first) among other OpenAlex
    records with the same title (case, accents and punctuation aside) and the same first author: the same surname
    and, when both names give one, the same first initial. One search, made only when the work names a first author,
    because a title alone does not tell a version from a homonym."""
    surname, initial = _author_key(obj.get("first_author") or "")
    if not obj.get("title") or not surname:
        return None
    title = _fold(obj["title"])
    try:
        results = client.works(search=obj["title"], select=texts.WORK_SELECT, per_page=10).get("results") or []
    except OpenAlexError:
        return None
    found = []
    for w in results:
        other = texts.work_object(w, None)
        if (not other["publication_date"] or other["openalex"] == obj.get("openalex")
                or _fold(other["title"] or "") != title):
            continue
        other_surname, other_initial = _author_key(other["first_author"] or "")
        if other_surname != surname or (initial and other_initial and other_initial != initial):
            continue
        found.append((_openalex_date(other["publication_date"]), other["openalex"]))
    found.sort(key=lambda f: _sortable(f[0]))
    return (found[0][0], [wid for _, wid in found]) if found else None


def earliest_date(work: Mapping, client: Any, linked: list[str] | None = None, *,
                  search: bool = True) -> tuple[str | None, str]:
    """(date, date_source) of a work's earliest version: the recorded date (`openalex`, or `year_only` when only a
    year is known), moved back to the arXiv v1 date (`arxiv_v1`) or to an earlier record with the same title and
    first author (`earlier_record`) when either is earlier; (None, `none`) when nothing dates it. When an earlier
    record sets the date, the W IDs of the matching records are appended to `linked`: they are versions of the same
    work. `search=False` keeps to the free lookups (no earlier-record search)."""
    client = _booked(client)
    best, source = _recorded_date(work)
    obj = _brief(work)
    if obj.get("arxiv") and source != "arxiv_v1":
        v1 = _arxiv_v1(obj["arxiv"], client)
        if _earlier(v1, best):
            best, source = v1, "arxiv_v1"
    found = _earlier_record(obj, client) if search else None
    if found and _earlier(found[0], best):
        best, source = found[0], "earlier_record"
        if linked is not None:
            linked.extend(found[1])
    return best, source


def _dated(work: Mapping, client: Any, claim_date: str | None, linked: list[str] | None = None, *,
           search: bool = True) -> tuple[str | None, str]:
    """The recorded date when it is already before the claim date; otherwise (a potential leak) the earliest one."""
    recorded = _recorded_date(work)
    if claim_date and recorded[0] and _before(recorded[0], claim_date):
        return recorded
    return earliest_date(work, client, linked, search=search)


# ---------- resolution ----------

def _lookup(kind: str, ident: str, client: Any) -> dict | None:
    try:
        return client.work(LOOKUP[kind].format(ident), select=texts.WORK_SELECT)
    except (OpenAlexError, ValueError):
        if kind != "arxiv":
            return None
    return _arxiv_record(ident, client)  # a preprint OpenAlex no longer resolves (decisions.md, 2026-10-06)


def _title_search(c: Mapping, client: Any) -> dict:
    title = str(c.get("title") or "").strip()
    if not title:
        return {"resolved_by": "none", "work": None, "ambiguous": False}
    year = _year(c.get("year"))
    query = {"search": title, "select": texts.WORK_SELECT, "per_page": 5}
    if year:
        query["filter"] = f"publication_year:{year - 1}-{year + 1}"
    authors = c.get("authors")
    if isinstance(authors, list):
        authors = ", ".join(str(a) for a in authors)
    line = f"{title} {authors or ''} {year or ''}"
    try:
        results = client.works(**query).get("results") or []
    except OpenAlexError:  # logged as an error call; the candidate stays unresolved
        results = []
    strict = [w for w in results if match_gold(line, texts.work_object(w, None)) == "strict"]
    best = _best_title(title, strict)
    if best is not None:
        return {"resolved_by": "title_search", "work": best, "ambiguous": False}
    return {"resolved_by": "none", "work": None, "ambiguous": len(strict) > 1}


def _best_title(title: str, strict: list[Mapping]) -> Mapping | None:
    """The one strict match, or among several the one whose title is clearly the asked title: equal after folding,
    else the only one whose content words agree in both directions (Jaccard at least 0.9, every other below 0.7).
    A first author's other work with a longer title also matches strictly on author and year, so the strict rule
    alone left the obvious work unresolved (Kirkpatrick 1983 and 1984, measured 2026-10-07)."""
    if len(strict) <= 1:
        return strict[0] if strict else None
    want = _fold(title)
    exact = [w for w in strict if _fold(w.get("display_name") or w.get("title") or "") == want]
    if len(exact) == 1:
        return exact[0]
    words = set(_content_words(title))

    def jaccard(w: Mapping) -> float:
        other = set(_content_words(w.get("display_name") or w.get("title") or ""))
        return len(words & other) / len(words | other) if words | other else 0.0
    scored = sorted(((jaccard(w), n) for n, w in enumerate(strict)), reverse=True)
    if scored[0][0] >= 0.9 and all(score < 0.7 for score, _ in scored[1:]):
        return strict[scored[0][1]]
    return None


def resolve_candidate(c: Mapping, client: Any) -> dict:
    """{resolved_by, work, ambiguous} for one candidate. Routes in order: OpenAlex ID, DOI, arXiv ID (field, arXiv
    DOI or URL), PMID, each a free lookup; then a title search ($0.001, filtered to the year plus or minus one when
    the candidate gives a year), accepted only when exactly one result matches strictly. Two or more strict matches
    leave it unresolved and `ambiguous`, for a person to settle. A failing service raises `ResolutionUnavailable`."""
    client = _booked(client)
    found = _by_ids(c, client)
    if found is not None:
        return {"resolved_by": found[0], "work": found[1], "ambiguous": False}
    return _title_search(c, client)


def _by_ids(c: Mapping, client: Any) -> tuple[str, dict] | None:
    """(route, work) from the free ID lookups alone, or None."""
    keys = [canonical_id(c[f]) for f in ("openalex", "doi", "arxiv", "pmid", "url") if str(c.get(f) or "").strip()]
    for route, kind in ID_ROUTES:
        for key in dict.fromkeys(k for k in keys if k.startswith(kind + ":")):
            work = _lookup(kind, key.partition(":")[2], client)
            if work is not None:
                return route, work
    return None


# ---------- leakage and cleaning ----------

def leak(c: Mapping, claim_date: str, keys: frozenset[str] | set[str]) -> str | None:
    """The first leakage reason that applies to a candidate or evidence record (its identifiers and `date`), else
    None: `excluded_work`, `on_or_after_claim_date`, `year_only_not_before` (SCHEMA rule 6), `undated`."""
    if work_keys(c) & keys:
        return "excluded_work"
    d = c.get("date")
    if d and not _year_only(d):
        return "on_or_after_claim_date" if d >= claim_date else None
    if d:
        return "year_only_not_before" if int(d) >= int(claim_date[:4]) else None
    return "undated"


def _wid(value: Any) -> str | None:
    key = canonical_id(value) if str(value or "").strip() else ""
    return key.partition(":")[2] if key.startswith("openalex:") else None


def _date_evidence(e: Mapping, client: Any, claim_date: str | None = None) -> tuple[dict, Mapping | None]:
    client = _booked(client)
    out = {"kind": e.get("kind"), "url": e.get("url"), "openalex": _wid(e.get("openalex")), "date": None,
           "date_source": "none"}
    if e.get("kind") in SEARCH_LISTS:
        out["date_source"] = "not_counted"
        return out, None
    ids = {f: e[f] for f in ("openalex", "doi", "url") if str(e.get(f) or "").strip()}
    if any(canonical_id(v).partition(":")[0] in LOOKUP for v in ids.values()):
        found = _by_ids(ids, client)
        if found is not None:
            work = found[1]
            out["openalex"] = _brief(work).get("openalex") or out["openalex"]
            out["date"], out["date_source"] = _dated(work, client, claim_date)
            return out, work
    if e.get("kind") == "fetched_page" and e.get("url"):
        out["date"] = str(e.get("fetched_at") or e.get("at") or datetime.now(timezone.utc).isoformat())[:10]
        out["date_source"] = "version_read"
    return out, None


def date_evidence(e: Mapping, client: Any, claim_date: str | None = None) -> dict:
    """Date one evidence entry. A scholarly reference (OpenAlex ID, or a doi.org, arxiv.org, openalex.org or PubMed
    URL) is dated by its work's earliest version; any other fetched page by the version read, which is the fetch time
    (`fetched_at`, else `at`); a search-result list is not counted. With `claim_date`, the earliest-version lookups
    run only when the recorded date is a potential leak."""
    return _date_evidence(e, client, claim_date)[0]


def _cites_excluded(c: Mapping, excluded: Iterable[Mapping]) -> bool:
    """Whether an unresolved candidate strictly matches an excluded work (`closedbook.match_gold` on its title, authors
    and year): a citation of the source work that the title search, filtered to the stated year, could not settle."""
    title = str(c.get("title") or "").strip()
    if not title:
        return False
    authors = c.get("authors")
    if isinstance(authors, list):
        authors = ", ".join(str(a) for a in authors)
    line = f"{title} {authors or ''} {c.get('year') or ''}"
    return any(match_gold(line, w) == "strict" for w in excluded)


def _record(c: Mapping, rank_raw: int, claim_date: str, keys: frozenset[str], client: Any,
            excluded: Iterable[Mapping] = (), at: str | None = None) -> dict:
    res = resolve_candidate(c, client)
    work = res["work"]
    linked: list[str] = []
    if work is not None:
        obj = _brief(work)
        date, date_source = _dated(work, client, claim_date, linked)
    else:
        obj, year = {}, _year(c.get("year"))
        url = str(c.get("url") or "").strip()
        if url and canonical_id(url).startswith("url:"):  # a cited web page: the version read (decision 29)
            date = str(c.get("fetched_at") or at or datetime.now(timezone.utc).isoformat())[:10]
            date_source = "version_read"
        else:
            date, date_source = (str(year), "year_only") if year else (None, "none")
    rec = {"rank_raw": rank_raw, "openalex": obj.get("openalex") or _wid(c.get("openalex")),
           "doi": obj.get("doi") or c.get("doi"), "arxiv": obj.get("arxiv") or c.get("arxiv"),
           "pmid": obj.get("pmid") or c.get("pmid"), "url": c.get("url"), "resolved_by": res["resolved_by"],
           "ambiguous": res["ambiguous"], "date": date, "date_source": date_source, "same_work": linked,
           "relation_native": c.get("relation"), "leak": None, "duplicate_of": None, "placeholder": False,
           "rank_clean": None}
    if work is not None and work_keys(work) & keys:  # any identifier or location of the resolved work
        rec["leak"] = "excluded_work"
    elif work is None and _cites_excluded(c, excluded):
        rec["leak"] = "excluded_work"
    else:
        rec["leak"] = leak(rec, claim_date, keys)
    rec["placeholder"] = work is None and rec["leak"] is None
    return rec


# ---------- exposure to search results (decision 28) ----------

def _structured_links(result: Any) -> list[str]:
    if not isinstance(result, Mapping):
        return []
    return [str(link["url"]) for r in result.get("results") or [] if isinstance(r, Mapping)
            for link in r.get("content") or [] if isinstance(link, Mapping) and link.get("url")]


def _text_links(content: Any) -> list[str]:
    text = content if isinstance(content, str) else "".join(
        str(b.get("text") or "") for b in content or [] if isinstance(b, Mapping))
    out = []
    for m in re.finditer(r"Links: (?=\[)", text):
        try:
            links, _ = json.JSONDecoder().raw_decode(text, m.end())
        except json.JSONDecodeError:
            continue
        if isinstance(links, list):
            out += [str(x["url"]) for x in links if isinstance(x, Mapping) and x.get("url")]
    return out


def search_result_urls(trace_path: Path | str | None) -> list[str]:
    """The distinct URLs listed in the WebSearch results of one stream-json agent trace, in the order first listed.
    A result is the `tool_result` that answers a WebSearch `tool_use`; its links are read from the event's
    structured `tool_use_result` when it has one, else from the `Links: [...]` JSON at the head of the result text
    (the format of cache/runs/cli-probes/, CLI 2.1.286). The model's own text, the summary after the links and
    fetched pages are never read."""
    path = Path(trace_path) if trace_path else None
    if path is None or not path.exists():
        return []
    searches, answered, urls = set(), set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (event.get("message") or {}).get("content") if isinstance(event, dict) else None
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if event.get("type") == "assistant":
            searches |= {b["id"] for b in blocks
                         if b.get("type") == "tool_use" and b.get("name") == "WebSearch" and b.get("id")}
        elif event.get("type") == "user":
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for b in results:
                tid = b.get("tool_use_id")
                if tid not in searches or tid in answered:
                    continue
                answered.add(tid)
                listed = _structured_links(event.get("tool_use_result")) if len(results) == 1 else []
                urls += listed or _text_links(b.get("content"))
    return list(dict.fromkeys(urls))


def search_exposure(urls: Iterable[str], client: Any, claim_date: str) -> dict:
    """Decision 28, descriptive: of the distinct listed `urls` (`search_results`), how many are scholarly (doi.org,
    arxiv.org, openalex.org or PubMed) and resolve to a dated work through the free ID lookups (`resolved`), and how
    many of those are dated on or after the claim date, a year-only date counting when its year is not before the
    claim's (`on_or_after_claim`). Dating moves a work back to its arXiv v1 date but makes no paid earlier-record
    search, so `on_or_after_claim` is an upper bound. A listed result is not evidence and never a leakage hit."""
    client = _booked(client)
    urls = list(dict.fromkeys(urls))
    resolved = later = 0
    for url in urls:
        if canonical_id(url).partition(":")[0] not in LOOKUP:
            continue
        found = _by_ids({"url": url}, client)
        date = _dated(found[1], client, claim_date, search=False)[0] if found is not None else None
        if date:
            resolved += 1
            later += leak({"date": date}, claim_date, frozenset()) is not None
    return {"search_results": len(urls), "resolved": resolved, "on_or_after_claim": later}


def _trace(row: Mapping) -> Path | None:
    """The row's agent trace (gitignored), when it is on this machine and unchanged since the run hashed it."""
    raw = row.get("raw") or {}
    if not raw.get("trace_path"):
        return None
    path = REPO_ROOT / raw["trace_path"]
    if not path.exists():
        return None
    if raw.get("trace_sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != raw["trace_sha256"]:
        return None
    return path


def load_config(system: str) -> dict:
    return json.loads((CONFIGS / f"{system}.json").read_text(encoding="utf-8"))


def clean(row: Mapping, item: Mapping, client: Any, map_verdict: MapVerdict | None = None,
          cfg: Mapping | None = None) -> dict:
    """The resolved.jsonl record (section 8.8) of one outputs.jsonl row: every candidate resolved, dated and checked
    for leakage; leaks and duplicate versions removed; `rank_clean` over what remains; `top10` with placeholders as
    null; leakage counts over candidates and evidence; `exposure` over the search results of the row's trace (None
    when the trace is not here); and the verdict from `map_verdict(cfg, native_label, cleaned, status)` (default
    `labels.map_item`) on the cleaned list only. The item's exclusions plus its source work are the excluded works.
    `cfg` defaults to the system's file in `eval/results/measurement-a/configs/`. When a service fails to answer a
    lookup, the record has status `infra_error`, its `resolution_error` and no candidates, labels or counts."""
    if map_verdict is None:
        from .labels import map_item as map_verdict  # the label rules belong to the 12 Oct freeze
    booked = _booked(client)
    cfg = cfg if cfg is not None else load_config(row["system"])
    native = row.get("native") or {}
    head = {"format": "coelacanth-resolved/v1", "run_id": row.get("run_id"), "system": row.get("system"),
            "condition": row.get("condition"), "item": row.get("item"), "claim_date": item["claim"]["date"],
            "config_sha256": row.get("config_sha256")}
    try:
        body = _clean(row, item, booked, map_verdict, cfg)
    except ResolutionUnavailable as err:
        body = {"status": "infra_error", "failure": True, "resolution_error": str(err), "candidates": None,
                "top10": None, "evidence": None, "leakage": None, "exposure": None,
                "verdict": {"rule": (cfg.get("label_map") or {}).get("rule"), "native": native.get("label"),
                            "six": None, "class3": None, "mapped": None, "six_before_cleaning": None}}
    return {**head, **body, "probability": native.get("probability"),
            "resolver": {"title_searches": booked.searches, "usd_nominal": round(booked.usd, 6)}}


def _clean(row: Mapping, item: Mapping, booked: _Booked, map_verdict: MapVerdict, cfg: Mapping) -> dict:
    claim = item["claim"]
    claim_date = claim["date"]
    source = claim.get("source_work") or {}
    looked_up = _Recorder(booked)
    keys = exclusion_keys([*(item.get("exclusions") or []),
                           *(source[f] for f in ("openalex", "doi", "arxiv", "pmid") if source.get(f))], looked_up)
    excluded = ([source] if source.get("title") else []) + [texts.work_object(w, None) for w in looked_up.seen]
    native = row.get("native") or {}
    raw = list(native.get("candidates") or [])
    records = [_record(c, n, claim_date, keys, booked, excluded, row.get("at")) for n, c in enumerate(raw, 1)]
    seen: dict[str, int] = {}
    kept = []
    for rec in records:
        if rec["leak"]:
            continue
        ids = sorted(canonical_id(rec[f]) for f in ("openalex", "doi", "arxiv") if rec.get(f))
        ids += [canonical_id(w) for w in rec["same_work"]]  # versions the earlier-record dating matched
        first = next((seen[k] for k in ids if k in seen), None)
        if first is not None:
            rec["duplicate_of"] = first
            continue
        seen.update({k: rec["rank_raw"] for k in ids})
        kept.append(rec)
        rec["rank_clean"] = len(kept)
    evidence = []
    for e in native.get("evidence") or []:
        rec, work = _date_evidence({"at": row.get("at"), **e}, booked, claim_date=claim_date)
        if rec["date_source"] == "not_counted":
            rec["leak"] = None
        elif work is not None and work_keys(work) & keys:  # any identifier or location of the resolved work
            rec["leak"] = "excluded_work"
        else:
            rec["leak"] = leak(rec, claim_date, keys)
        evidence.append(rec)
    trace = _trace(row)
    listed = [u for e in native.get("evidence") or [] if e.get("kind") in SEARCH_LISTS
              for u in [e.get("url"), *(e.get("urls") or [])] if u]
    exposure = (search_exposure(search_result_urls(trace) + listed, booked, claim_date)
                if trace is not None or listed else None)
    hits = [r for r in records + evidence if r["leak"]]
    leakage = {"hit_top10": any(r["leak"] and r["rank_raw"] <= 10 for r in records),
               "hit_evidence": any(e["leak"] for e in evidence), "n_hits": len(hits),
               "n_undated": sum(r["leak"] == "undated" for r in hits),
               "n_excluded": sum(r["leak"] == "excluded_work" for r in hits)}
    cleaned = [{"rank": r["rank_clean"], "relation": r["relation_native"], "openalex": r["openalex"],
                "placeholder": r["placeholder"]} for r in kept]
    verdict = dict(map_verdict(cfg, native.get("label"), cleaned, row.get("status")))
    if verdict.get("rule") == "precedence_over_candidates" and not row.get("failure"):
        if verdict.get("six_before_cleaning") is None:
            verdict["six_before_cleaning"] = derive_verdict({"candidates": raw})
    verdict.setdefault("six_before_cleaning", None)
    top10 = [None if r["placeholder"] else r["openalex"] or (f"arXiv:{r['arxiv']}" if r["arxiv"] else None)
             for r in kept[:10]]
    return {"status": row.get("status"), "failure": bool(row.get("failure")), "resolution_error": None,
            "candidates": records, "top10": top10, "evidence": evidence, "leakage": leakage, "exposure": exposure,
            "verdict": verdict}


def _items(items: Mapping | Iterable[Mapping] | None) -> dict[str, Mapping]:
    if items is None:
        return {i["id"]: i for path in ITEM_FILES if path.exists() for i in load_items(path)}
    if isinstance(items, Mapping):
        return dict(items)
    return {i["id"]: i for i in items}


def resolve_run(run_dir: Path | str, client: OpenAlexClient | None = None, items: Mapping | Iterable | None = None,
                configs: Mapping[str, Mapping] | None = None, map_verdict: MapVerdict | None = None) -> dict:
    """Resolve a run directory: read `outputs.jsonl` (the last row per item, since a re-run infrastructure error
    appends a new row), write `resolved.jsonl` and append the resolver's OpenAlex calls to `resolve-calls.jsonl`.
    Items default to benchmark v0 plus the pilot items; configs to the files in `configs/`. A row whose lookups a
    service failed to answer is written with status `infra_error` and its `resolution_error` (pooling refuses it);
    running this again rewrites `resolved.jsonl` from the cached answers plus the lookups that failed."""
    run_dir = Path(run_dir)
    last: dict[str, Mapping] = {}
    for row in read_jsonl(run_dir / "outputs.jsonl"):
        last[row["item"]] = row
    if not last:
        raise FileNotFoundError(f"no output rows in {run_dir / 'outputs.jsonl'}")
    client = OpenAlexClient() if client is None else client
    by_id, cfgs = _items(items), dict(configs or {})
    calls_path = run_dir / "resolve-calls.jsonl"
    resolved = []
    for row in last.values():
        if row["item"] not in by_id:
            raise KeyError(f"item {row['item']} of {run_dir} is in no item file")
        if row["system"] not in cfgs:
            cfgs[row["system"]] = load_config(row["system"])
        booked = _Booked(client, log=lambda call: append_jsonl(calls_path, call),
                         tags={"run_id": row.get("run_id"), "item": row["item"]})
        resolved.append(clean(row, by_id[row["item"]], booked, map_verdict, cfgs[row["system"]]))
    tmp = run_dir / "resolved.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in resolved), encoding="utf-8")
    tmp.replace(run_dir / "resolved.jsonl")
    candidates = [c for r in resolved for c in r["candidates"] or []]
    errors = [r["item"] for r in resolved if r["resolution_error"]]
    return {"rows": len(resolved), "resolution_errors": len(errors), "resolution_error_items": errors,
            "items_with_hits": sum((r["leakage"] or {}).get("n_hits", 0) > 0 for r in resolved),
            "unresolved": sum(c["resolved_by"] == "none" for c in candidates),
            "ambiguous": sum(c["ambiguous"] for c in candidates),
            "placeholders": sum(c["placeholder"] for c in candidates),
            "title_searches": sum(r["resolver"]["title_searches"] for r in resolved),
            "usd_nominal": round(sum(r["resolver"]["usd_nominal"] for r in resolved), 6)}
