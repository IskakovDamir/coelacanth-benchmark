"""Pages on which annotators judge the works the models named closed-book for the items in H (eval/protocol.md
section 3, decision 1, and decision 17: judged like any candidate, by the item's annotator, blind to which model named
the work and how often; Damir adjudicates every work rated `similar_mechanism` or stronger).

    PYTHONPATH=src python tools/checks/make_named.py cf-0013 cf-0036 cf-0046

reads eval/results/closed-book-v0/named-works.json (resolved works dated before the claim) and writes
eval/benchmark/v0/checks/send/named-<annotator>.html, one page per annotator (the first annotator the quiz gave the
item), and eval/benchmark/v0/checks/named-assignment.json.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V0 = REPO / "eval" / "benchmark" / "v0"
CHECKS = V0 / "checks"
NAMED = REPO / "eval" / "results" / "closed-book-v0" / "named-works.json"
TEXTS = V0 / "checks" / "claim-texts.json"
FULLTEXT = REPO / "cache" / "fulltext"
# names and definitions as in the quiz (tools/quiz/quiz-template.html)
RELATIONS = [
    ("equivalent", "То же самое", "Ранняя работа говорит то же, что утверждение, при тех же условиях, только другими словами или обозначениями."),
    ("specialization", "Утверждение: частный случай ранней работы", "Зафиксируйте параметр или добавьте условие в ранней работе, и с тем же смыслом получится утверждение."),
    ("generalization", "Утверждение: обобщение ранней работы", "Наоборот: ранняя работа это частный случай утверждения, она получается из него ограничением."),
    ("partial", "Частичное совпадение", "Хотя бы два ключевых элемента есть в ранней работе с тем же смыслом, но хотя бы одного ключевого нет."),
    ("similar_mechanism", "Похожий механизм", "У каждого ключевого элемента есть соответствие (хотя бы по роли), но система или смысл переменных другие."),
    ("different", "Разное", "Общие слова, тема или отдельные элементы, но меньше двух ключевых совпадают и не у каждого есть соответствие."),
]


def packets() -> dict[str, dict]:
    return {p["id"]: p for b in ("b01", "b02", "b03")
            for p in json.loads((V0 / "batches" / f"{b}.json").read_text(encoding="utf-8"))["items"]}


def abstract(openalex: str) -> str | None:
    path = FULLTEXT / f"oa-{openalex}.txt"
    return path.read_text(encoding="utf-8").strip()[:2500] if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("items", nargs="+")
    args = parser.parse_args()
    named = json.loads(NAMED.read_text(encoding="utf-8"))
    texts = json.loads(TEXTS.read_text(encoding="utf-8"))["items"]
    quiz = json.loads((V0 / "quiz" / "assignment.json").read_text(encoding="utf-8"))["questions"]
    pk = packets()
    pages: dict[str, list[dict]] = {}
    for item in args.items:
        p = pk[item]
        annotator = quiz[f"{item}/c1"]["annotators"][0]
        works = {}
        for r in named[item]["lines"]:
            w = r.get("resolved")
            if w and r.get("before_claim"):
                works.setdefault(w["openalex"], w)
        claim_text = (texts.get(item) or {}).get("text") or p["claim"]["text"]
        for oid, w in sorted(works.items()):
            pages.setdefault(annotator, []).append({
                "id": f"{item}/{oid}", "item": item, "claim_date": p["claim"]["date"], "claim_text": claim_text,
                "chart": [e["text"] for e in p["claim"]["chart"]], "claim_field": p["claim"]["field"],
                "work": {"title": w["title"], "first_author": w["first_author"],
                         "year": (w["publication_date"] or "")[:4], "field": w.get("field"), "openalex": oid,
                         "doi": w.get("doi"), "abstract": abstract(oid)}})
    (CHECKS / "send").mkdir(parents=True, exist_ok=True)
    record = {}
    for annotator, entries in pages.items():
        data = {"annotator": annotator, "entries": entries, "relations": RELATIONS,
                "hash": hashlib.sha256(json.dumps([annotator, entries], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]}
        text = page(data)
        path = CHECKS / "send" / f"named-{annotator}.html"
        path.write_text(text, encoding="utf-8")
        record[annotator] = {"ids": [e["id"] for e in entries], "data_hash": data["hash"],
                             "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        print(f"{path.relative_to(REPO)}: {len(entries)} works")
    (CHECKS / "named-assignment.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def page(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ранние работы</title>
<style>
:root {{ --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --card:#fafafa; --on:#1d4ed8; --acc:#f97316; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#999; --line:#333; --card:#1a1a1a; --on:#7aa2ff; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:880px; margin:0 auto; }}
.e {{ border:1px solid var(--line); background:var(--card); border-radius:6px; padding:12px 14px; margin:14px 0; }}
.e.done {{ opacity:.65; }}
.muted {{ color:var(--muted); font-size:13px; }}
.claim {{ border-left:3px solid var(--acc); padding-left:8px; margin:6px 0; }}
.abs {{ font-size:13px; white-space:pre-wrap; max-height:180px; overflow:auto; border:1px solid var(--line); padding:6px; }}
ol {{ margin:4px 0 4px 18px; padding:0; font-size:14px; }}
button {{ font:inherit; padding:5px 10px; margin:5px 6px 0 0; border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:4px; cursor:pointer; font-size:14px; }}
button.on {{ border-color:var(--on); color:var(--on); font-weight:600; }}
input {{ font:inherit; width:100%; box-sizing:border-box; margin-top:8px; padding:5px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; }}
#bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:1; }}
a {{ color:var(--on); }}
</style></head><body><main>
<div id="bar"><b>Ранние работы</b> · <span id="count"></span>
<input id="who" placeholder="Кто отвечает (имя или ID)" style="width:auto;min-width:240px;margin:0 8px">
<button id="dl">Скачать named-{html.escape(data['annotator'])}.json</button></div>
<p>Для каждого утверждения ниже несколько ранних работ. Оцени каждую как обычного кандидата по правилам разметки: <b>как ранняя работа относится к утверждению</b> (по ключевым элементам утверждения: какие из них работа покрывает с тем же смыслом; определения ниже) и <b>из другой ли она области</b>. Если аннотации нет, открой работу по ссылке; если понять нельзя, напиши это в заметке и выбери «Разное».</p>
<details><summary class="muted">Определения отношений</summary><ul class="muted">{''.join(f"<li><b>{html.escape(n)}</b>: {html.escape(d)}</li>" for _, n, d in RELATIONS)}</ul></details>
<div id="list"></div>
<script id="data" type="application/json">{blob}</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const KEY = 'named-' + D.annotator + '-' + D.hash;
let S = {{}};
try {{ S = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(S)); }} catch (e) {{}} count(); }}
function count() {{ const n = D.entries.filter(e => S[e.id] && S[e.id].relation && S[e.id].cross_field).length;
  document.getElementById('count').textContent = n + ' из ' + D.entries.length; }}
function esc(s) {{ return String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
const list = document.getElementById('list');
let last = null;
D.entries.forEach((e, i) => {{
  if (e.item !== last) {{
    const h = document.createElement('div'); h.className = 'e';
    h.innerHTML = `<div class="muted">${{esc(e.item)}} · утверждение (дата ${{esc(e.claim_date)}}, область: ${{esc(e.claim_field)}})</div><div class="claim">${{esc(e.claim_text)}}</div>
      <div class="muted">Элементы утверждения</div><ol>${{e.chart.map(c => `<li>${{esc(c)}}</li>`).join('')}}</ol>`;
    list.appendChild(h); last = e.item;
  }}
  const w = e.work, d = S[e.id] || {{}};
  const url = w.doi ? (w.doi.startsWith('http') ? w.doi : 'https://doi.org/' + w.doi) : 'https://openalex.org/' + w.openalex;
  const div = document.createElement('div'); div.className = 'e';
  div.innerHTML = `<div class="muted">${{i + 1}}. ${{esc(e.item)}} · ранняя работа</div>
    <div><b>${{esc(w.first_author)}} ${{esc(w.year)}}</b>. ${{esc(w.title)}} <span class="muted">${{esc(w.field || '')}}</span> <a href="${{esc(url)}}" target="_blank" rel="noopener">ссылка</a></div>
    ${{w.abstract ? `<div class="abs">${{esc(w.abstract)}}</div>` : '<div class="muted">Аннотации нет: открой по ссылке.</div>'}}
    <div class="muted" style="margin-top:6px">Отношение к утверждению</div>
    ${{D.relations.map(([v, l, _d]) => `<button data-k="relation" data-v="${{v}}">${{esc(l)}}</button>`).join('')}}
    <div class="muted" style="margin-top:6px">Из другой области?</div>
    <button data-k="cross_field" data-v="yes">Да</button><button data-k="cross_field" data-v="no">Нет</button>
    <input placeholder="Заметка: какие элементы покрыты, почему" value="${{esc(d.note || '')}}">`;
  const bs = div.querySelectorAll('button'), inp = div.querySelector('input');
  const paint = () => {{ const s = S[e.id] || {{}}; bs.forEach(b => b.classList.toggle('on', s[b.dataset.k] === b.dataset.v)); div.classList.toggle('done', !!(s.relation && s.cross_field)); }};
  bs.forEach(b => b.onclick = () => {{ S[e.id] = {{...(S[e.id] || {{}}), [b.dataset.k]: b.dataset.v}}; save(); paint(); }});
  inp.oninput = () => {{ S[e.id] = {{...(S[e.id] || {{}}), note: inp.value}}; save(); }};
  paint(); list.appendChild(div);
}});
const whoEl = document.getElementById('who');
try {{ whoEl.value = localStorage.getItem(KEY + '-who') || ''; }} catch (e) {{}}
whoEl.oninput = () => {{ try {{ localStorage.setItem(KEY + '-who', whoEl.value); }} catch (e) {{}} }};
document.getElementById('dl').onclick = () => {{
  const who = whoEl.value.trim(); if (!who) {{ alert('Впиши, кто отвечал.'); return; }}
  const out = {{format: 'coelacanth-named-works/v1', annotator: D.annotator, answered_by: who, page_hash: D.hash,
    exported_at: new Date().toISOString(), answers: S}};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {{type: 'application/json'}}));
  a.download = 'named-' + D.annotator + '.json'; a.click();
}};
count();
</script></main></body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
