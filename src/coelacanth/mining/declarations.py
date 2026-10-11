"""Equivalence declarations in OpenAlex: letters, comments and other works whose title or abstract says that a
result is equivalent to, a special case of, or a rediscovery of earlier work (template: Monaco and Anderson 1994,
"Tai's formula is the trapezoidal rule"; PLAN.md, source 2).

Each query is a phrase filter; the first page (up to 200 works) of each is kept (round 1, 2026-09-24). Round 2
(2026-10-06, Damir) fetches every page of the PAGED queries, the ones with at least one usable cross-field declaration
in the round-1 hand check and more hits than their first page held, with cursor paging from page 1 so each query is
one consistent snapshot; new works get `round: 2`, works already mined get the query added. OpenAlex drops stop words
inside phrases, so phrases made mostly of them ("is not new" matched about 5 million works) are not used.
Titles, identifiers, dates, fields and reference lists are OpenAlex metadata (CC0); abstracts stay in the cache
and are used only for the flags.
Writes
    data/candidates/openalex-declarations.jsonl          one row per work, with the queries that found it
    data/candidates/openalex-declarations-queries.json   per query: filter, OpenAlex count, fetched

    PYTHONPATH=src python -m coelacanth.mining.declarations           # round 1: first pages
    PYTHONPATH=src python -m coelacanth.mining.declarations --page    # round 2: every page of the PAGED queries
"""

from __future__ import annotations

import argparse
import json
import math
import re

from ..data.openalex import PRICE_USD, OpenAlexClient, abstract_text
from . import CANDIDATES_DIR, write_jsonl

QUERIES = {
    # letters that state an equivalence in the title
    "letter_title_equivalent": 'title.search:"is equivalent to",type:letter',
    "letter_title_identical": 'title.search:"is identical to",type:letter',
    "letter_title_special_case": 'title.search:"special case",type:letter',
    "letter_title_already_known": 'title.search:"already known",type:letter',
    "letter_title_rediscovery": "title.search:rediscovery,type:letter",
    "letter_abstract_mathematically_equivalent": 'abstract.search:"mathematically equivalent",type:letter',
    # comments on a paper whose abstract names the equivalence
    "comment_equivalent": 'title.search:"comment on",abstract.search:"equivalent to"',
    "comment_identical": 'title.search:"comment on",abstract.search:"identical to"',
    "comment_special_case": 'title.search:"comment on",abstract.search:"special case of"',
    "comment_rediscovered": 'title.search:"comment on",abstract.search:rediscovered',
    "comment_previously_published": 'title.search:"comment on",abstract.search:"previously published"',
    # titles of any type
    "title_rediscovery_of": 'title.search:"rediscovery of"',
    "title_reinventing_the_wheel": 'title.search:"reinventing the wheel"',
    "title_reinvention_of": 'title.search:"reinvention of"',
    "title_old_wine_new_bottles": 'title.search:"old wine in new bottles"',
    "title_new_name_for": 'title.search:"new name for"',
    "title_by_another_name": 'title.search:"by another name"',
    "title_already_known": 'title.search:"already known"',
    # abstracts of any type
    "abstract_independently_rediscovered": 'abstract.search:"independently rediscovered"',
    "abstract_has_been_rediscovered": 'abstract.search:"has been rediscovered"',
    "abstract_were_unaware": 'abstract.search:"were unaware of"',
    "abstract_under_a_different_name": 'abstract.search:"under a different name"',
}
# Paged in round 2: at least one usable cross-field declaration in the 2026-09-24 hand check of 40 positives, and
# more hits than the first page held (abstract_independently_rediscovered had 169 hits, all on its first page).
PAGED = ("abstract_has_been_rediscovered", "abstract_under_a_different_name", "title_by_another_name",
         "comment_special_case", "comment_equivalent")
PAGED_ROUND = 2
# Round 3 (2026-10-10, Damir: a further search with a one-day yield check): new phrases modelled on the query with the
# best hand-check yield (abstract_independently_rediscovered, 4 usable of 48 positives), first page each. Counts read
# the same day: 1140, 48, 203, 597, 2481, 871, 180, 6, 1617, 4528. Two words in one phrase lose their stop words, so
# rediscovery or reinvention plus "field" are two filters, which OpenAlex joins with AND.
ROUND3 = {
    "abstract_independently_discovered": 'abstract.search:"independently discovered"',
    "abstract_rediscovered_independently": 'abstract.search:"rediscovered independently"',
    "abstract_multiple_discovery": 'abstract.search:"multiple discovery"',
    "abstract_mathematically_identical": 'abstract.search:"mathematically identical"',
    "abstract_rediscovered_and_field": "abstract.search:rediscovered,abstract.search:field",
    "abstract_reinvented_and_field": "abstract.search:reinvented,abstract.search:field",
    "abstract_unaware_and_earlier_work": 'abstract.search:unaware,abstract.search:"earlier work"',
    "abstract_equivalent_different_fields": 'abstract.search:"mathematically equivalent",abstract.search:"different fields"',
    "abstract_formally_equivalent": 'abstract.search:"formally equivalent"',
    "abstract_independently_derived": 'abstract.search:"independently derived"',
}
ROUND3_ROUND = 3
PER_PAGE = 200
SELECT = ["id", "doi", "title", "publication_date", "type", "primary_topic", "authorships", "primary_location",
          "referenced_works", "abstract_inverted_index"]
FLAG_RE = re.compile(r"\b(equivalent|identical|special case|rediscover\w*|reinvent\w*|already known|not new|"
                     r"old wine|another name|different name|same as|unaware)\b", re.IGNORECASE)
SPECIES_RE = re.compile(r"\b(species|genus|specimen|population|fossil|bird|frog|plant|orchid|beetle|fish|"
                        r"herbarium|sp\.|nov\.)\b", re.IGNORECASE)


def _row(work: dict) -> dict:
    topic = work.get("primary_topic") or {}
    authors = work.get("authorships") or []
    source = ((work.get("primary_location") or {}).get("source") or {})
    title = work.get("title") or ""
    abstract = abstract_text(work) or ""
    return {"openalex": (work.get("id") or "").rsplit("/", 1)[-1], "doi": work.get("doi"), "title": title,
            "type": work.get("type"), "publication_date": work.get("publication_date"),
            "venue": source.get("display_name"),
            "first_author": (authors[0].get("author") or {}).get("display_name") if authors else None,
            "field": (topic.get("field") or {}).get("display_name"),
            "domain": (topic.get("domain") or {}).get("display_name"),
            "referenced_works": [r.rsplit("/", 1)[-1] for r in work.get("referenced_works") or []],
            "flag_phrase": sorted({m.lower() for m in FLAG_RE.findall(title + " " + abstract)}),
            "flag_species": bool(SPECIES_RE.search(title + " " + abstract)),
            "has_abstract": bool(abstract)}


def mine(client: OpenAlexClient, per_page: int = 200) -> tuple[list[dict], list[dict]]:
    planned = len(QUERIES) * 0.001
    left = client.daily_budget_usd - client.spent_today()
    if planned > left + 1e-9:
        raise SystemExit(f"planned ${planned:.3f} of searches, ${left:.4f} left in today's OpenAlex budget")
    rows: dict[str, dict] = {}
    stats = []
    for name, flt in QUERIES.items():
        data = client.works(filter=flt, select=SELECT, per_page=per_page)
        results = data.get("results") or []
        stats.append({"query": name, "filter": flt, "openalex_count": (data.get("meta") or {}).get("count"),
                      "fetched": len(results)})
        for work in results:
            row = _row(work)
            if not row["openalex"]:
                continue
            kept = rows.setdefault(row["openalex"], {**row, "queries": []})
            kept["queries"].append(name)
    for row in rows.values():
        row["source"] = "openalex_declarations"
        row["likely_declaration"] = bool(row["flag_phrase"]) and not row["flag_species"]
    return stats, sorted(rows.values(), key=lambda r: r["openalex"])


def paged_works(client: OpenAlexClient, name: str):
    """Every work of one query, by cursor paging from page 1 (replayed from the cache once fetched)."""
    return client.iter_works(filter=QUERIES[name], select=SELECT, per_page=PER_PAGE)


def page(client: OpenAlexClient, rows: list[dict], stats: list[dict],
         names: tuple[str, ...] = PAGED) -> tuple[list[dict], list[dict]]:
    """Round 2: merge every page of the named queries into the mined rows. Refuses to start when the planned
    searches (from the counts stored in round 1, plus 10%) pass what is left of today's OpenAlex budget."""
    counts = {s["query"]: s.get("openalex_count") or 0 for s in stats}
    planned = sum(math.ceil(counts.get(n, 0) * 1.1 / PER_PAGE) + 1 for n in names) * PRICE_USD["search"]
    left = client.daily_budget_usd - client.spent_today()
    if planned > left + 1e-9:
        raise SystemExit(f"planned ${planned:.3f} of searches, ${left:.4f} left in today's OpenAlex budget")
    by_id = {r["openalex"]: r for r in rows}
    stats = [dict(s) for s in stats]
    for name in names:
        fetched = 0
        for work in paged_works(client, name):
            row = _row(work)
            if not row["openalex"]:
                continue
            fetched += 1
            if row["openalex"] in by_id:
                kept = by_id[row["openalex"]]
                if name not in kept["queries"]:
                    kept["queries"].append(name)
                continue
            by_id[row["openalex"]] = {**row, "queries": [name], "source": "openalex_declarations",
                                      "likely_declaration": bool(row["flag_phrase"]) and not row["flag_species"],
                                      "round": PAGED_ROUND}
        for s in stats:
            if s["query"] == name:
                s["fetched_all_pages"] = fetched
                s["paged_on"] = "2026-10-06"
    return stats, sorted(by_id.values(), key=lambda r: r["openalex"])


def round3(client: OpenAlexClient, rows: list[dict], stats: list[dict]) -> tuple[list[dict], list[dict]]:
    """Round 3: merge the first page of each ROUND3 query into the mined rows; new works get `round: 3`."""
    planned = len(ROUND3) * PRICE_USD["search"]
    left = client.daily_budget_usd - client.spent_today()
    if planned > left + 1e-9:
        raise SystemExit(f"planned ${planned:.3f} of searches, ${left:.4f} left in today's OpenAlex budget")
    by_id = {r["openalex"]: r for r in rows}
    stats = [dict(s) for s in stats if s["query"] not in ROUND3]
    for name, flt in ROUND3.items():
        data = client.works(filter=flt, select=SELECT, per_page=PER_PAGE)
        results = data.get("results") or []
        stats.append({"query": name, "filter": flt, "openalex_count": (data.get("meta") or {}).get("count"),
                      "fetched": len(results), "round": ROUND3_ROUND, "mined_on": "2026-10-10"})
        for work in results:
            row = _row(work)
            if not row["openalex"]:
                continue
            if row["openalex"] in by_id:
                kept = by_id[row["openalex"]]
                if name not in kept["queries"]:
                    kept["queries"].append(name)
                continue
            by_id[row["openalex"]] = {**row, "queries": [name], "source": "openalex_declarations",
                                      "likely_declaration": bool(row["flag_phrase"]) and not row["flag_species"],
                                      "round": ROUND3_ROUND}
    return stats, sorted(by_id.values(), key=lambda r: r["openalex"])


def abstracts(client: OpenAlexClient, per_page: int = PER_PAGE, paged: bool = False,
              with_round3: bool = False) -> dict[str, str]:
    """Abstracts of the mined works by OpenAlex ID, rebuilt from the same query pages (cache hits cost nothing),
    with every page of the PAGED queries when `paged` and the ROUND3 first pages when `with_round3`. They stay in
    memory and the cache; they are not written to data/candidates/."""
    out: dict[str, str] = {}
    works = [w for flt in QUERIES.values()
             for w in client.works(filter=flt, select=SELECT, per_page=per_page).get("results") or []]
    if paged:
        works += [w for name in PAGED for w in paged_works(client, name)]
    if with_round3:
        works += [w for flt in ROUND3.values()
                  for w in client.works(filter=flt, select=SELECT, per_page=per_page).get("results") or []]
    for work in works:
        wid = (work.get("id") or "").rsplit("/", 1)[-1]
        text = abstract_text(work)
        if wid and text:
            out.setdefault(wid, text)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coelacanth.mining.declarations")
    parser.add_argument("--page", action="store_true", help="round 2: fetch every page of the PAGED queries")
    parser.add_argument("--round3", action="store_true", help="round 3: first page of each ROUND3 query")
    args = parser.parse_args(argv)
    client = OpenAlexClient()
    if args.round3:
        rows_path = CANDIDATES_DIR / "openalex-declarations.jsonl"
        stats_path = CANDIDATES_DIR / "openalex-declarations-queries.json"
        with rows_path.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        before = len(rows)
        stats, rows = round3(client, rows, json.loads(stats_path.read_text(encoding="utf-8")))
        write_jsonl(rows_path, rows)
        stats_path.write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
        for s in stats:
            if s.get("round") == ROUND3_ROUND:
                print(f"{s['query']:45} count {s['openalex_count']:>7}  fetched {s['fetched']:>4}")
        new = [r for r in rows if r.get("round") == ROUND3_ROUND]
        print(f"works before {before}, after {len(rows)}; new in round {ROUND3_ROUND}: {len(new)} "
              f"(likely declarations {sum(r['likely_declaration'] for r in new)})")
        print(f"OpenAlex spend today: ${client.spent_today():.4f} of ${client.daily_budget_usd:.2f}")
        return 0
    if args.page:
        rows_path = CANDIDATES_DIR / "openalex-declarations.jsonl"
        stats_path = CANDIDATES_DIR / "openalex-declarations-queries.json"
        with rows_path.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        before = len(rows)
        stats, rows = page(client, rows, json.loads(stats_path.read_text(encoding="utf-8")))
        write_jsonl(rows_path, rows)
        stats_path.write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
        for s in stats:
            if "fetched_all_pages" in s:
                print(f"{s['query']:45} round-1 count {s['openalex_count']:>6}  fetched now {s['fetched_all_pages']:>5}")
        new = [r for r in rows if r.get("round") == PAGED_ROUND]
        print(f"works before {before}, after {len(rows)}; new in round {PAGED_ROUND}: {len(new)} "
              f"(likely declarations {sum(r['likely_declaration'] for r in new)})")
        print(f"OpenAlex spend today: ${client.spent_today():.4f} of ${client.daily_budget_usd:.2f}")
        return 0
    stats, rows = mine(client)
    write_jsonl(CANDIDATES_DIR / "openalex-declarations.jsonl", rows)
    (CANDIDATES_DIR / "openalex-declarations-queries.json").write_text(json.dumps(stats, indent=1) + "\n",
                                                                       encoding="utf-8")
    for s in stats:
        print(f"{s['query']:45} count {s['openalex_count']:>8}  fetched {s['fetched']:>4}")
    likely = [r for r in rows if r["likely_declaration"]]
    print(f"unique works {len(rows)}; flagged as likely declarations {len(likely)} "
          f"(letters {sum(r['type'] == 'letter' for r in likely)}); species rediscoveries set aside "
          f"{sum(r['flag_species'] for r in rows)}")
    print(f"OpenAlex spend today: ${client.spent_today():.4f} of ${client.daily_budget_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
