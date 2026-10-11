"""Works the models named closed-book outside the gold lists, for the items in H (eval/protocol.md section 3,
decision 1): every named work that resolves in OpenAlex and predates the claim date joins the item's candidates and is
judged like any candidate, before H is final; the number left unjudged is reported.

    PYTHONPATH=src python eval/results/closed-book-v0/named_works.py extract
        named-works.json: per H item (results.json, summary.low.H), the distinct answer lines that cite a dated work
        and match neither a candidate nor the claim's source (closedbook.match_gold, strict or loose)
    PYTHONPATH=src python eval/results/closed-book-v0/named_works.py resolve [--max 120]
        resolves each line in OpenAlex (one search per line, within the free daily allowance; cached) and keeps the
        best hit when its title overlaps the line and its first author's surname or the year agrees; marks whether it
        predates the claim date
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from coelacanth.benchmark.items import _surname
from coelacanth.closedbook import _content_words, _fold, match_gold
from coelacanth.data.openalex import OpenAlexClient
from coelacanth.llm import read_jsonl

HERE = Path(__file__).resolve().parent
YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|20[0-2]\d)(?!\d)")
REFUSAL = re.compile(r"can't|cannot|can not|unknown|not aware|no specific|i'm not|i am not|not able", re.I)
SELECT = ["id", "doi", "title", "publication_date", "authorships", "primary_topic", "primary_location"]


def clean(line: str) -> str:
    line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line)
    return re.sub(r"[*_`]+", "", line).strip()


def cited_lines(answer: str | None) -> list[str]:
    out = []
    for line in (answer or "").splitlines():
        c = clean(line)
        if YEAR.search(c) and len(_content_words(c)) >= 3:
            out.append(c)
    return out


def query_text(line: str) -> tuple[str, int | None]:
    quoted = re.findall(r"[\"“”']([^\"“”']{12,})[\"“”']", line)
    year = int(YEAR.search(line).group(1)) if YEAR.search(line) else None
    text = quoted[0] if quoted else YEAR.sub(" ", line)
    words = _content_words(text)[:12]
    return " ".join(words), year


def cmd_extract() -> int:
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    h_items = results["summary"]["low"]["H"]
    items = {i["pid"]: i for i in read_jsonl(HERE / "items.jsonl")}
    out = {}
    for pid in h_items:
        item = items[pid]
        known = list(item["candidates"]) + [item["claim_source"]]
        seen, rows = set(), []
        for a in read_jsonl(HERE / "answers.jsonl"):
            if a["pid"] != pid or a.get("is_error"):
                continue
            for line in cited_lines(a.get("answer")):
                if any(match_gold(line, k) for k in known):
                    continue
                years = [int(y) for y in YEAR.findall(line)]
                if REFUSAL.search(line) or not years or min(years) >= int(item["date"][:4]):
                    continue  # a refusal or remark, or no year before the claim's
                q, year = query_text(line)
                key = (q, year)
                if not q or key in seen:
                    continue
                seen.add(key)
                rows.append({"line": line, "query": q, "year": year})
        out[pid] = {"claim_date": item["date"], "lines": rows}
        print(pid, len(rows), "distinct named lines outside the candidates")
    (HERE / "named-works.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def best_hit(row: dict, results: list[dict]) -> dict | None:
    words = set(re.findall(r"[a-z0-9]+", _fold(row["line"])))
    best, score = None, 0.0
    for w in results:
        title = _content_words(w.get("title") or "")
        if not title:
            continue
        overlap = sum(t in words for t in title) / len(title)
        authors = w.get("authorships") or []
        first = (authors[0].get("author") or {}).get("display_name") if authors else ""
        surname = re.sub(r"[^a-z0-9]", "", _fold(_surname(first or "") or ""))
        name_ok = bool(surname) and surname in words
        year = int((w.get("publication_date") or "0")[:4] or 0)
        year_ok = row["year"] is not None and abs(year - row["year"]) <= 1
        s = overlap + (0.3 if name_ok else 0) + (0.2 if year_ok else 0)
        if overlap >= 0.6 and (name_ok or year_ok) and s > score:
            best, score = w, s
    return best


def cmd_resolve(max_searches: int) -> int:
    data = json.loads((HERE / "named-works.json").read_text(encoding="utf-8"))
    client = OpenAlexClient()
    done = 0
    for pid, entry in data.items():
        for row in entry["lines"]:
            if "resolved" in row:
                continue
            if done >= max_searches:
                break
            flt = f"publication_year:{row['year'] - 1}-{row['year'] + 1}" if row["year"] else None
            try:
                res = client.works(search=row["query"], filter=flt, select=SELECT, per_page=5).get("results") or []
            except Exception as err:  # the daily budget guard or a network error: stop, resume tomorrow
                print(f"stopped: {type(err).__name__}: {err}")
                done = max_searches
                break
            done += 1
            w = best_hit(row, res)
            if w:
                authors = w.get("authorships") or []
                row["resolved"] = {"openalex": w["id"].rsplit("/", 1)[-1], "doi": w.get("doi"), "title": w.get("title"),
                                   "publication_date": w.get("publication_date"),
                                   "first_author": (authors[0].get("author") or {}).get("display_name") if authors else None,
                                   "field": ((w.get("primary_topic") or {}).get("field") or {}).get("display_name")}
                row["before_claim"] = (w.get("publication_date") or "9999") < entry["claim_date"]
            else:
                row["resolved"] = None
    for entry in data.values():  # an unresolved line that names a work already resolved for the item is that work
        found = {r["resolved"]["openalex"]: r["resolved"] for r in entry["lines"] if r.get("resolved")}
        for r in entry["lines"]:
            if "resolved" in r and r["resolved"] is None:
                for w in found.values():
                    if match_gold(r["line"], {"title": w["title"], "first_author": w["first_author"],
                                              "year": int((w["publication_date"] or "0")[:4] or 0)}) == "strict":
                        r["resolved"], r["before_claim"], r["same_as_line_resolved"] = w, \
                            (w["publication_date"] or "9999") < entry["claim_date"], True
                        break
    (HERE / "named-works.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for pid, entry in data.items():
        rs = [r for r in entry["lines"] if r.get("resolved")]
        print(pid, f"lines {len(entry['lines'])}, resolved {len(rs)}, before the claim {sum(r['before_claim'] for r in rs)}, "
                   f"unresolved {sum(1 for r in entry['lines'] if r.get('resolved') is None and 'resolved' in r)}, "
                   f"not yet searched {sum(1 for r in entry['lines'] if 'resolved' not in r)}")
    print(f"OpenAlex spend today: ${client.spent_today():.4f} of ${client.daily_budget_usd:.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract")
    r = sub.add_parser("resolve")
    r.add_argument("--max", type=int, default=80)
    args = parser.parse_args()
    return cmd_extract() if args.cmd == "extract" else cmd_resolve(args.max)


if __name__ == "__main__":
    raise SystemExit(main())
