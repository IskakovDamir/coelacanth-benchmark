"""Damir's page for the works the models named closed-book (Russian UI, self-contained):

- every work the students rated gold (any relation but `different`) on the pages of make_named.py, 14 in all: the
  claim, its chart, the work with its abstract, the student's reading, and the relation and cross-field answer Damir
  decides (protocol decision 17: the author adjudicates every work rated `similar_mechanism` or stronger);
- optionally (UNJUDGED_ITEMS), every named work of an `H` item that never reached a judging page; empty now, because
  the only such item, cf-0044, left `H` when Opus 5.5 declined every sample on its checked text (decision 6).

    PYTHONPATH=src python tools/checks/make_named_decisions.py

reads eval/benchmark/v0/checks/named.json (written by import_named.py) and writes
eval/benchmark/v0/checks/decide-named.html; it downloads named-decisions.json, which
import_named_decisions.py checks and applies. The student's note is shown folded and marked: it was produced when
the sheets were transcribed (checks/returned/named-PROVENANCE.md) and is not a ground for a decision.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_named  # noqa: E402

REPO = make_named.REPO
CHECKS = make_named.CHECKS
UNJUDGED_ITEMS: tuple[str, ...] = ()  # cf-0044 left `H` by declines (decision 6), so its named works need no first judgment

TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Решения: названные работы</title>
<style>
:root { --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --card:#fafafa; --on:#1d4ed8; --warn:#b45309; }
@media (prefers-color-scheme: dark) { :root { --bg:#111; --fg:#eee; --muted:#999; --line:#333; --card:#1a1a1a; --on:#7aa2ff; --warn:#f59e0b; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; padding:16px; }
main { max-width:900px; margin:0 auto; }
.e { border:1px solid var(--line); background:var(--card); border-radius:6px; padding:12px 14px; margin:14px 0; }
.e.done { opacity:.6; }
.muted { color:var(--muted); font-size:13px; }
.claim { border-left:3px solid var(--line); padding-left:8px; margin:6px 0; }
.abs { font-size:14px; max-height:9em; overflow:auto; border:1px solid var(--line); padding:6px; margin:6px 0; background:var(--bg); }
.read { background:var(--bg); border:1px dashed var(--line); padding:6px 8px; margin:8px 0; font-size:14px; }
button { font:inherit; padding:5px 10px; margin:5px 6px 0 0; border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:4px; cursor:pointer; }
button.on { border-color:var(--on); color:var(--on); font-weight:600; }
input { font:inherit; width:100%; box-sizing:border-box; margin-top:8px; padding:5px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; }
#bar { position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:1; }
h2 { font-size:17px; margin-top:26px; }
a { color:var(--on); }
</style></head><body><main>
<div id="bar"><b>Решения арбитра: названные работы</b> · <span id="count"></span> <button id="dl">Скачать named-decisions.json</button></div>
<p class="muted">Вопрос к каждой работе: в каком отношении утверждение стоит к этой более ранней работе, и из другой ли она области. Правила: вкладка «Определения» внизу. Решение любое, причина одной строкой обязательна. Если согласен с чтением студента, нажми «Как у студента».</p>
<h2>1. Работы, которые студенты оценили как gold (<span id="n1"></span>)</h2>
<div id="g"></div>
<h2>2. Работы, которых никто не оценивал (<span id="n2"></span>)</h2>
<p class="muted">Названы моделями до даты утверждения, но не попали ни на одну страницу оценки. Читателя нет, оцениваешь с нуля.</p>
<div id="u"></div>
<details><summary class="muted">Определения отношений</summary><ul class="muted" id="defs"></ul></details>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const KEY = 'decide-named-' + D.hash;
let S = {};
try { S = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) {}
function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} count(); }
function ok(id) { const s = S[id] || {}; return !!(s.relation && s.cross_field && (s.reason || '').trim()); }
function count() { const n = D.entries.filter(e => ok(e.id)).length;
  document.getElementById('count').textContent = `решено ${n} из ${D.entries.length}`; }
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
const label = Object.fromEntries(D.relations.map(([v, l]) => [v, l]));
document.getElementById('defs').innerHTML = D.relations.map(([v, l, d]) => `<li><b>${esc(l)}</b> (${esc(v)}): ${esc(d)}</li>`).join('');
function render(e, host) {
  const w = e.work, d = S[e.id] || {};
  const url = w.doi ? (w.doi.startsWith('http') ? w.doi : 'https://doi.org/' + w.doi) : 'https://openalex.org/' + w.openalex;
  const div = document.createElement('div'); div.className = 'e';
  const r = e.reading;
  div.innerHTML = `<div class="muted">${esc(e.id)} · утверждение от ${esc(e.claim_date)}, область: ${esc(e.claim_field)}</div>
    <div class="claim">${esc(e.claim_text)}</div>
    <div class="muted">Элементы: ${e.chart.map((c, i) => `e${i + 1}. ${esc(c)}`).join(' · ')}</div>
    <div style="margin-top:8px"><b>${esc(w.first_author)} ${esc(w.year)}</b>. ${esc(w.title)} <span class="muted">${esc(w.field || '')}</span> <a href="${esc(url)}" target="_blank" rel="noopener">ссылка</a></div>
    ${w.abstract ? `<div class="abs">${esc(w.abstract)}</div>` : '<div class="muted">Аннотации нет: открой по ссылке.</div>'}
    ${r ? `<div class="read">Чтение студента (${esc(r.annotator)}): <b>${esc(label[r.relation] || r.relation)}</b>, из другой области: <b>${r.cross_field === 'yes' ? 'да' : 'нет'}</b>
      <details><summary class="muted">заметка из файла (не основание для решения)</summary><div class="muted">${esc(r.note)}</div></details></div>` : ''}
    <div>${D.relations.map(([v, l]) => `<button data-k="relation" data-v="${v}">${esc(l)}</button>`).join('')}</div>
    <div>Из другой области? <button data-k="cross_field" data-v="yes">Да</button><button data-k="cross_field" data-v="no">Нет</button>
      ${r ? '<button data-same="1">Как у студента</button>' : ''}</div>
    <input placeholder="Причина одной строкой (обязательно)" value="${esc(d.reason || '')}">`;
  const bs = div.querySelectorAll('button[data-k]'), inp = div.querySelector('input');
  const paint = () => { const s = S[e.id] || {}; bs.forEach(b => b.classList.toggle('on', s[b.dataset.k] === b.dataset.v)); div.classList.toggle('done', ok(e.id)); };
  bs.forEach(b => b.onclick = () => { S[e.id] = {...(S[e.id] || {}), [b.dataset.k]: b.dataset.v}; save(); paint(); });
  const same = div.querySelector('button[data-same]');
  if (same) same.onclick = () => { S[e.id] = {...(S[e.id] || {}), relation: r.relation, cross_field: r.cross_field, agrees_with_reading: true}; save(); paint(); };
  inp.oninput = () => { S[e.id] = {...(S[e.id] || {}), reason: inp.value}; save(); paint(); };
  paint(); host.appendChild(div);
}
const g = D.entries.filter(e => e.reading), u = D.entries.filter(e => !e.reading);
document.getElementById('n1').textContent = g.length; document.getElementById('n2').textContent = u.length;
g.forEach(e => render(e, document.getElementById('g'))); u.forEach(e => render(e, document.getElementById('u')));
document.getElementById('dl').onclick = () => {
  const missing = D.entries.filter(e => !ok(e.id)).length;
  if (missing && !confirm(`Не решено: ${missing}. Скачать как есть?`)) return;
  const decisions = {};
  D.entries.forEach(e => { const s = S[e.id]; if (s && s.relation && s.cross_field) decisions[e.id] = {relation: s.relation, cross_field: s.cross_field,
    reason: (s.reason || '').trim(), agrees_with_reading: !!(e.reading && s.relation === e.reading.relation && s.cross_field === e.reading.cross_field)}; });
  const out = {format: 'coelacanth-named-decisions/v1', decided_by: 'damir', page_hash: D.hash, exported_at: new Date().toISOString(), decisions};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {type: 'application/json'}));
  a.download = 'named-decisions.json'; a.click();
};
count();
</script></main></body></html>
"""


def entries() -> list[dict]:
    named = json.loads(make_named.NAMED.read_text(encoding="utf-8"))
    merged = json.loads((CHECKS / "named.json").read_text(encoding="utf-8"))
    texts = json.loads(make_named.TEXTS.read_text(encoding="utf-8"))["items"]
    pk = make_named.packets()
    out = []

    def entry(item: str, oid: str, w: dict, reading: dict | None) -> dict:
        p = pk[item]
        return {"id": f"{item}/{oid}", "item": item, "claim_date": p["claim"]["date"],
                "claim_text": (texts.get(item) or {}).get("text") or p["claim"]["text"],
                "chart": [e["text"] for e in p["claim"]["chart"]], "claim_field": p["claim"]["field"],
                "work": {"title": w["title"], "first_author": w["first_author"],
                         "year": (w["publication_date"] or "")[:4], "field": w.get("field"), "openalex": oid,
                         "doi": w.get("doi"), "abstract": make_named.abstract(oid)},
                "reading": reading}

    for wid in merged["for_damir"]:
        item, oid = wid.split("/")
        w = next(r["resolved"] for r in named[item]["lines"] if (r.get("resolved") or {}).get("openalex") == oid)
        m = merged["works"][wid]
        out.append(entry(item, oid, w, {"annotator": m["annotator"], "relation": m["relation"],
                                        "cross_field": m["cross_field"], "note": m["note"]}))
    for item in UNJUDGED_ITEMS:
        works = {}
        for r in named[item]["lines"]:
            w = r.get("resolved")
            if w and r.get("before_claim"):
                works.setdefault(w["openalex"], w)
        for oid, w in sorted(works.items()):
            out.append(entry(item, oid, w, None))
    return out


def page(data: dict) -> str:
    data = {**data, "hash": hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", blob)


def main() -> int:
    data = {"entries": entries(), "relations": make_named.RELATIONS}
    path = CHECKS / "decide-named.html"
    path.write_text(page(data), encoding="utf-8")
    g = sum(1 for e in data["entries"] if e["reading"])
    print(f"{path.relative_to(REPO)}: {g} gold-rated works with a student's reading, "
          f"{len(data['entries']) - g} works never judged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
