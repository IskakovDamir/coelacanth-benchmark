"""Analysis of the v0 closed-book probes (eval/protocol.md section 3; README.md in this directory).

Gold: items.jsonl, from the adjudicated quiz labels (`build_items.py --labels`). Every answer in answers.jsonl counts,
the carried-over declarations-pilot answers included; their pilot borderline decisions were made by Claude readers,
so they are read again here by a person.

    PYTHONPATH=src python eval/results/closed-book-v0/analyse.py borderline
        writes borderline-queue.json (what the person sees: claim, its date, one gold work, the matched answer lines;
        no model, no prompt, no sample), borderline-keys.json (which answers each entry comes from) and
        borderline.html, the page Damir decides on (it saves borderline-decisions.json)
    PYTHONPATH=src python eval/results/closed-book-v0/analyse.py results [--decisions borderline-decisions.json]
        writes results.json: per item, the models and prompts whose samples name cross-field gold and any gold, and
        R's recall split, H, H_A and H-any; without decisions, borderline matches are counted both ways (low and
        high) and nothing is final

A match is clear when it is strict with a gold-title overlap of 0.95 or more; every other match (every loose match,
every strict match below 0.95) is borderline and goes to the person (protocol section 3). Works named outside the
gold lists are not judged here yet (decision 1), so H stays provisional.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from coelacanth.benchmark.items import _surname
from coelacanth.closedbook import MODELS, _content_words, _fold, match_gold, wilson
from coelacanth.llm import read_jsonl

HERE = Path(__file__).resolve().parent
CLEAR = 0.95
PROMPTS = ("frozen", "cross_field")


def answers() -> list[dict]:
    return [{**a, "prompt": a.get("prompt", "frozen")} for a in read_jsonl(HERE / "answers.jsonl")
            if not a.get("is_error")]


def items() -> dict[str, dict]:
    return {i["pid"]: i for i in read_jsonl(HERE / "items.jsonl")}


def windows(answer: str | None) -> list[str]:
    lines = [line.strip() for line in (answer or "").splitlines() if line.strip()]
    return lines + [a + " " + b for a, b in zip(lines, lines[1:])]


def overlap(line: str, gold: dict) -> float:
    words = set(re.findall(r"[a-z0-9]+", _fold(line)))
    title = _content_words(gold.get("title") or "")
    return sum(w in words for w in title) / len(title) if title else 0.0


def matches(answer: dict, item: dict) -> list[dict]:
    """Every gold work the answer matches: level, whether it is clear, and the windows that matched."""
    out = []
    for gi, g in enumerate(item.get("gold") or []):
        hit = [(w, match_gold(w, g)) for w in windows(answer.get("answer"))]
        hit = [(w, lv) for w, lv in hit if lv]
        if not hit:
            continue
        level = "strict" if any(lv == "strict" for _, lv in hit) else "loose"
        clear = any(lv == "strict" and overlap(w, g) >= CLEAR for w, lv in hit)
        out.append({"gold": gi, "level": level, "clear": clear, "lines": sorted({w for w, _ in hit})})
    return out


def entry_id(pid: str, gi: int, lines: list[str]) -> str:
    return hashlib.sha256(json.dumps([pid, gi, lines], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def cmd_borderline() -> int:
    its, queue, keys = items(), {}, defaultdict(list)
    for a in answers():
        item = its.get(a["pid"])
        if not item:
            continue
        for m in matches(a, item):
            if m["clear"]:
                continue
            eid = entry_id(a["pid"], m["gold"], m["lines"])
            g = item["gold"][m["gold"]]
            queue[eid] = {"id": eid, "claim": item["text"], "claim_date": item["date"],
                          "gold_work": {k: g.get(k) for k in ("first_author", "year", "title", "doi", "openalex")},
                          "answer_lines": m["lines"]}
            keys[eid].append({"pid": a["pid"], "gold": m["gold"], "cid": g["cid"], "model": a["model"],
                              "prompt": a["prompt"], "sample": a["sample"], "level": m["level"]})
    # entries of provisional R items first (they decide H), then the rest; by ID inside each group
    first = {eid for eid, ks in keys.items() if its[ks[0]["pid"]].get("in_R_provisional")}
    entries = sorted(queue.values(), key=lambda e: (e["id"] not in first, e["id"]))
    (HERE / "borderline-queue.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    (HERE / "borderline-keys.json").write_text(json.dumps(keys, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (HERE / "borderline.html").write_text(sheet(entries), encoding="utf-8")
    print(len(entries), "borderline entries from", sum(len(v) for v in keys.values()), "matches")
    return 0


RUBRIC = ("Да, если строка даёт название (мелкие ошибки допустимы) вместе с первым автором или годом, либо первого "
          "автора с узнаваемым куском названия, журналом или книгой. Автор и год без названия считаются, только если "
          "у этого автора нет другой работы на ту же тему в пределах года (проверь по ссылке на OpenAlex). Год "
          "засчитывается с ошибкой до одного. Другая версия той же работы считается. Не считается: сама работа "
          "утверждения, другая работа того же автора, понятие или эпоним без года.")


def sheet(entries: list[dict], out_name: str = "borderline-decisions.json",
          heading: str = "Пограничные совпадения: проверка по памяти") -> str:
    """One self-contained page, Russian UI, no network requests; decisions autosave in the browser and download
    as borderline-decisions.json."""
    data = json.dumps({"entries": entries, "out": out_name,
                       "hash": hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]},
                      ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Пограничные совпадения</title>
<style>
:root {{ --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --card:#fafafa; --yes:#1d7a3a; --no:#a3271f; --acc:#f97316; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#999; --line:#333; --card:#1a1a1a; --yes:#4cc274; --no:#ff7b70; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:860px; margin:0 auto; }}
.e {{ border:1px solid var(--line); background:var(--card); border-radius:6px; padding:12px 14px; margin:14px 0; }}
.e.done {{ opacity:.6; }}
.muted {{ color:var(--muted); font-size:13px; }}
.lines {{ font-family: ui-monospace, monospace; font-size:13px; white-space:pre-wrap; border-left:3px solid var(--acc); padding-left:8px; margin:8px 0; }}
button {{ font:inherit; padding:6px 14px; margin-right:8px; border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:4px; cursor:pointer; }}
button.on.y {{ border-color:var(--yes); color:var(--yes); font-weight:600; }}
button.on.n {{ border-color:var(--no); color:var(--no); font-weight:600; }}
input {{ font:inherit; width:100%; box-sizing:border-box; margin-top:8px; padding:5px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; }}
#bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); }}
</style></head><body><main>
<div id="bar"><b>{html.escape(heading)}</b> · <span id="count"></span>
<input id="who" placeholder="Кто решал: имя или ID (a1, a2, a3, damir)" style="width:auto;min-width:280px;margin:0 8px">
<button id="dl">Скачать {html.escape(out_name)}</button></div>
<p>Модели без интернета спрашивали, публиковалось ли это раньше. Здесь строки их ответов, которые похожи на известную раннюю работу, но не совпали точно. Модель, вопрос и номер попытки скрыты. Вопрос один: <b>называет ли строка именно эту работу?</b></p>
<p class="muted">{html.escape(RUBRIC)}</p>
<div id="list"></div>
<script id="data" type="application/json">{data}</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const KEY = 'cb-v0-' + D.out + '-' + D.hash;
let S = {{}};
try {{ S = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(S)); }} catch (e) {{}} count(); }}
function count() {{ const n = D.entries.filter(e => S[e.id] && S[e.id].names_gold !== undefined).length;
  document.getElementById('count').textContent = n + ' из ' + D.entries.length; }}
function esc(s) {{ return String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
const list = document.getElementById('list');
D.entries.forEach((e, i) => {{
  const g = e.gold_work, d = S[e.id] || {{}};
  const link = g.openalex ? 'https://openalex.org/' + g.openalex : (g.doi || '');
  const div = document.createElement('div'); div.className = 'e' + (d.names_gold !== undefined ? ' done' : '');
  div.innerHTML = `<div class="muted">${{i + 1}}. Утверждение (дата ${{esc(e.claim_date)}})</div><div>${{esc(e.claim)}}</div>
    <div class="muted" style="margin-top:8px">Ранняя работа</div>
    <div><b>${{esc(g.first_author)}} ${{esc(g.year)}}</b>. ${{esc(g.title)}} ${{link ? `<a href="${{esc(link)}}" target="_blank" rel="noopener">ссылка</a>` : ''}}</div>
    <div class="muted" style="margin-top:8px">Строки ответа</div><div class="lines">${{esc(e.answer_lines.join('\\n'))}}</div>
    <button class="y">Да, называет</button><button class="n">Нет</button>
    <input placeholder="Заметка (по желанию)" value="${{esc(d.note || '')}}">`;
  const [by, bn] = div.querySelectorAll('button'), inp = div.querySelector('input');
  function paint() {{ const v = (S[e.id] || {{}}).names_gold; by.classList.toggle('on', v === true); bn.classList.toggle('on', v === false);
    div.classList.toggle('done', v !== undefined); }}
  by.onclick = () => {{ S[e.id] = {{...(S[e.id] || {{}}), names_gold: true}}; save(); paint(); }};
  bn.onclick = () => {{ S[e.id] = {{...(S[e.id] || {{}}), names_gold: false}}; save(); paint(); }};
  inp.oninput = () => {{ S[e.id] = {{...(S[e.id] || {{}}), note: inp.value}}; save(); }};
  paint(); list.appendChild(div);
}});
document.getElementById('dl').onclick = () => {{
  const who = document.getElementById('who').value.trim();
  if (!who) {{ alert('Впиши, кто решал.'); return; }}
  const out = {{format: 'coelacanth-closedbook-borderline/v1', decided_by: who, queue_hash: D.hash,
    exported_at: new Date().toISOString(), decisions: S}};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {{type: 'application/json'}}));
  a.download = D.out; a.click();
}};
const whoEl = document.getElementById('who');
try {{ whoEl.value = localStorage.getItem(KEY + '-who') || ''; }} catch (e) {{}}
whoEl.oninput = () => {{ try {{ localStorage.setItem(KEY + '-who', whoEl.value); }} catch (e) {{}} }};
count();
</script></main></body></html>
"""


RECHECK_SEED = 20261010
RECHECK_N = 30


def cmd_recheck() -> int:
    """Protocol section 3: a second person re-decides a random 30 of the borderline entries, blind to the first
    decisions; the first decision stands, and agreement and the number of H flips are reported (`results`)."""
    queue = json.loads((HERE / "borderline-queue.json").read_text(encoding="utf-8"))
    picked = sorted(random.Random(RECHECK_SEED).sample(queue, RECHECK_N), key=lambda e: e["id"])
    (HERE / "borderline-recheck-queue.json").write_text(json.dumps(picked, indent=1, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")
    (HERE / "borderline-recheck.html").write_text(
        sheet(picked, "borderline-recheck.json", "Повторная проверка: 30 случайных пограничных совпадений"),
        encoding="utf-8")
    # a fresh copy for a second person: its own storage key and file name, so no earlier answers show in a shared browser
    (HERE / "borderline-recheck-student.html").write_text(
        sheet(picked, "borderline-recheck-student.json", "Повторная проверка: 30 случайных пограничных совпадений"),
        encoding="utf-8")
    print(f"{len(picked)} entries (seed {RECHECK_SEED}) in borderline-recheck.html and borderline-recheck-student.html")
    return 0


def hit_table(decisions: dict | None) -> dict[tuple, dict]:
    """(pid, model, prompt) -> {"cross": {"low", "high"}, "any": {...}}: whether some sample names cross-field gold or
    any gold. Clear matches count; a borderline match counts by the person's decision, or, without one, not at all
    (low) and fully (high)."""
    its = items()
    keys = json.loads((HERE / "borderline-keys.json").read_text(encoding="utf-8")) if decisions is not None or \
        (HERE / "borderline-keys.json").exists() else {}
    decided = {(k["pid"], k["model"], k["prompt"], k["sample"], k["gold"]): (decisions or {}).get(eid, {}).get("names_gold")
               for eid, ks in keys.items() for k in ks}
    out: dict[tuple, dict] = defaultdict(lambda: {"cross": {"low": False, "high": False},
                                                   "any": {"low": False, "high": False}})
    for a in answers():
        item = its.get(a["pid"])
        if not item:
            continue
        row = out[(a["pid"], a["model"], a["prompt"])]
        for m in matches(a, item):
            g = item["gold"][m["gold"]]
            if m["clear"]:
                low = high = True
            else:
                d = decided.get((a["pid"], a["model"], a["prompt"], a["sample"], m["gold"]))
                low, high = (bool(d), bool(d)) if d is not None else (False, True)
            for gs in ("any", "cross") if g["cross_field"] else ("any",):
                row[gs]["low"] |= low
                row[gs]["high"] |= high
    return out


def cmd_results(decisions_path: Path | None, recheck_path: Path | None = None) -> int:
    its = items()
    decisions = None
    if decisions_path:
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))["decisions"]
    table = hit_table(decisions)
    valid = defaultdict(int)
    for a in answers():
        valid[(a["pid"], a["model"])] += 1
    per_item = []
    for pid, item in its.items():
        row = {"pid": pid, "source": item["source"], "origin": item["origin"], "role": item["role"],
               # decision 6: a model with no valid sample under either prompt, after the retry, takes the item out of H
               "models_without_valid_sample": [m for m in MODELS if not valid[(pid, m)]],
               "in_R_provisional": item.get("in_R_provisional", False),
               "has_cross_gold": any(g["cross_field"] for g in item.get("gold") or []),
               "has_gold": bool(item.get("gold"))}
        for gs in ("cross", "any"):
            for bound in ("low", "high"):
                row[f"named_{gs}_{bound}"] = sorted(f"{m}:{p}" for (q, m, p), v in table.items()
                                                    if q == pid and v[gs][bound])
        per_item.append(row)
    R = [r for r in per_item if r["in_R_provisional"]]

    def split(rows, gs, bound, prompts=PROMPTS):
        named = [r for r in rows if any(x.split(":")[1] in prompts for x in r[f"named_{gs}_{bound}"])]
        return named, [r for r in rows if r not in named and not r["models_without_valid_sample"]]

    summary = {"R_provisional": len(R),
               "R_out_of_H_by_declines": [r["pid"] for r in R if r["models_without_valid_sample"]]}
    for bound in ("low", "high"):
        recalled, h = split(R, "cross", bound)
        _, h_a = split(R, "cross", bound, ("frozen",))
        _, h_any = split(R, "any", bound)
        summary[bound] = {"recalled": len(recalled), "H": [r["pid"] for r in h], "H_A": [r["pid"] for r in h_a],
                          "H_any": [r["pid"] for r in h_any],
                          "H_wilson": [round(x, 3) for x in wilson(len(h), len(R))] if R else None}
    per_model = {}
    for model in MODELS:
        for prompt in PROMPTS:
            for bound in ("low", "high"):
                k = sum(1 for r in R if f"{model}:{prompt}" in r[f"named_cross_{bound}"])
                per_model[f"{model}|{prompt}|{bound}"] = k
    if recheck_path and decisions:
        second = json.loads(recheck_path.read_text(encoding="utf-8"))
        picked = {e["id"] for e in json.loads((HERE / "borderline-recheck-queue.json").read_text(encoding="utf-8"))}
        both = [eid for eid in picked if eid in second["decisions"] and eid in decisions]
        agree = sum(second["decisions"][e]["names_gold"] == decisions[e]["names_gold"] for e in both)
        swapped = {**decisions, **{e: second["decisions"][e] for e in both}}
        t2 = hit_table(swapped)
        h2 = {pid for pid in (r["pid"] for r in R)
              if not any(v["cross"]["low"] for (q, m, pr), v in t2.items() if q == pid)
              and not its[pid] is None and not next(r for r in R if r["pid"] == pid)["models_without_valid_sample"]}
        h1 = set(summary["low"]["H"])
        summary["recheck"] = {"decided_by": second.get("decided_by"), "entries": len(both), "agree": agree,
                              "H_flips_if_second_decisions_stood": sorted(h1 ^ h2)}
    out = {"decisions": str(decisions_path) if decisions_path else None,
           "note": ("Provisional: R rests on model attribution hints; works the models named outside the gold lists "
                    "are not judged yet (protocol decision 1)" + ("" if decisions else "; borderline matches are "
                    "undecided, so low counts none of them and high counts all")),
           "summary": summary, "per_model_R_items_named_cross": per_model, "per_item": per_item}
    (HERE / "results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("borderline")
    sub.add_parser("recheck")
    r = sub.add_parser("results")
    r.add_argument("--decisions")
    r.add_argument("--recheck", help="borderline-recheck.json: the second person's decisions on the random 30")
    args = parser.parse_args()
    if args.cmd == "borderline":
        return cmd_borderline()
    if args.cmd == "recheck":
        return cmd_recheck()
    return cmd_results(Path(args.decisions) if args.decisions else None,
                       Path(args.recheck) if args.recheck else None)


if __name__ == "__main__":
    raise SystemExit(main())
