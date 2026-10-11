"""Damir's page for what the annotators' checks left to the adjudicator (Russian UI, self-contained):

- attribution pairs without an agreed reading (eval/benchmark/v0/checks/attribution.json `pending`): cited or not
  cited, with a reason;
- claim texts an annotator fixed (eval/benchmark/v0/checks/claimcheck.json `fixed`): the old text, the fixed text in
  an editable field with a word counter, facts the script found (length over SCHEMA.md's 120 words, words new to the
  text that appear in a candidate's title, phrases that point to earlier work), and accept (the field's text) or keep
  the old text, with a reason.

    PYTHONPATH=src python tools/checks/make_decisions.py

writes eval/benchmark/v0/checks/decide-damir.html; it downloads checks-decisions.json.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V0 = REPO / "eval" / "benchmark" / "v0"
CHECKS = V0 / "checks"
BATCHES = [V0 / "batches" / "b01.json", V0 / "batches" / "b02.json"]
MAX_WORDS = 120
CUES = re.compile(r"previous\w*|earlier|already|correct\w* previously|rather than the introduction|main contribution|"
                  r"corresponds molecularly|known as|rediscover\w*|originally", re.I)


def words(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text))


def entries() -> dict:
    pk = {p["id"]: p for b in BATCHES for p in json.loads(b.read_text(encoding="utf-8"))["items"]}
    att = json.loads((CHECKS / "attribution.json").read_text(encoding="utf-8"))
    cc = json.loads((CHECKS / "claimcheck.json").read_text(encoding="utf-8"))
    attribution = []
    for eid in att["pending"]:
        item, cid = eid.split("/")
        p = pk[item]
        cand = next(c for c in p["candidates"] if c["cid"] == cid)
        sw, cw = p["claim"]["source_work"], cand["work"]
        attribution.append({"id": eid, "readings": att["pairs"][eid]["readings"],
                            "source": {k: sw.get(k) for k in ("title", "first_author", "year", "doi", "arxiv", "openalex")},
                            "candidate": {k: cw.get(k) for k in ("title", "first_author", "year", "doi", "arxiv", "openalex")}})
    claims = []
    for pid in cc["fixed"]:
        p = pk[pid]
        old, new = p["claim"]["text"], cc["items"][pid]["fixed_text"]
        title_words = set().union(*(words(c["work"].get("title") or "") for c in p["candidates"])) if p["candidates"] else set()
        added = sorted(w for w in words(new) - words(old) if w in title_words and w[0].isupper() and w.lower() != "the")
        cues = sorted({m.group(0) for m in CUES.finditer(new)} - {m.group(0) for m in CUES.finditer(old)})
        flags = []
        if len(new.split()) > MAX_WORDS:
            flags.append(f"{len(new.split())} слов, больше {MAX_WORDS} (SCHEMA.md)")
        if added:
            flags.append("новые слова из названий ранних работ: " + ", ".join(added))
        if cues:
            flags.append("новые фразы, указывающие на раннюю работу: " + ", ".join(cues))
        claims.append({"id": pid, "old": old, "new": new, "note": cc["items"][pid]["note"],
                       "coined_names": p["claim"].get("coined_names") or [], "flags": flags,
                       "source": {k: p["claim"]["source_work"].get(k) for k in ("title", "first_author", "year", "doi", "arxiv", "openalex")}})
    return {"attribution": attribution, "claims": claims}


def page(data: dict) -> str:
    data = {**data, "hash": hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()[:16],
            "max": MAX_WORDS}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Решения арбитра</title>
<style>
:root {{ --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --card:#fafafa; --on:#1d4ed8; --warn:#b45309; --acc:#f97316; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#999; --line:#333; --card:#1a1a1a; --on:#7aa2ff; --warn:#f59e0b; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:900px; margin:0 auto; }}
.e {{ border:1px solid var(--line); background:var(--card); border-radius:6px; padding:12px 14px; margin:14px 0; }}
.e.done {{ opacity:.65; }}
.muted {{ color:var(--muted); font-size:13px; }}
.warn {{ color:var(--warn); font-size:13px; }}
.old {{ border-left:3px solid var(--line); padding-left:8px; font-size:14px; }}
button {{ font:inherit; padding:6px 12px; margin:6px 8px 0 0; border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:4px; cursor:pointer; }}
button.on {{ border-color:var(--on); color:var(--on); font-weight:600; }}
input, textarea {{ font:inherit; width:100%; box-sizing:border-box; margin-top:8px; padding:5px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; }}
textarea {{ min-height:150px; }}
#bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:1; }}
h2 {{ font-size:17px; margin-top:28px; }}
a {{ color:var(--on); }}
</style></head><body><main>
<div id="bar"><b>Решения арбитра</b> · <span id="count"></span> <button id="dl">Скачать checks-decisions.json</button></div>
<h2>1. Цитирование: пары, где аннотаторы не смогли проверить</h2>
<p class="muted">Цитирует ли поздняя работа раннюю? Нужен полный текст поздней работы (например, через библиотеку) и её список литературы.</p>
<div id="att"></div>
<h2>2. Правки текстов утверждений</h2>
<p class="muted">Слева старый текст (черновик Claude), в поле правка аннотатора. Правило: одно утверждение, не больше {MAX_WORDS} слов, так, как его формулирует источник, без фамилий, слов названия подряд, годов и придуманных названий, и без подсказок о ранней работе. Можно поправить текст в поле, затем «Принять текст из поля» или «Оставить старый».</p>
<div id="cl"></div>
<script id="data" type="application/json">{blob}</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const KEY = 'decide-damir-' + D.hash;
let S = {{attribution: {{}}, claims: {{}}}};
try {{ S = JSON.parse(localStorage.getItem(KEY)) || S; }} catch (e) {{}}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(S)); }} catch (e) {{}} count(); }}
function count() {{ const a = D.attribution.filter(e => (S.attribution[e.id] || {{}}).value).length;
  const c = D.claims.filter(e => (S.claims[e.id] || {{}}).decision).length;
  document.getElementById('count').textContent = `цитирование ${{a}} из ${{D.attribution.length}}, тексты ${{c}} из ${{D.claims.length}}`; }}
function esc(s) {{ return String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function link(w) {{ const u = w.doi ? (w.doi.startsWith('http') ? w.doi : 'https://doi.org/' + w.doi) : (w.arxiv ? 'https://arxiv.org/abs/' + w.arxiv : (w.openalex ? 'https://openalex.org/' + w.openalex : ''));
  return `<b>${{esc(w.first_author)}} ${{esc(w.year)}}</b>. ${{esc(w.title)}} ${{u ? `<a href="${{esc(u)}}" target="_blank" rel="noopener">ссылка</a>` : ''}}`; }}
function nwords(t) {{ return (t.trim().match(/\\S+/g) || []).length; }}
D.attribution.forEach(e => {{
  const d = S.attribution[e.id] || {{}};
  const div = document.createElement('div'); div.className = 'e';
  div.innerHTML = `<div class="muted">${{esc(e.id)}}</div><div>Поздняя: ${{link(e.source)}}</div><div>Ранняя: ${{link(e.candidate)}}</div>
    <div class="muted">Ответы аннотаторов: ${{e.readings.map(r => esc(r.annotator + ': ' + r.value)).join('; ')}}</div>
    <button data-v="cited">Цитирует</button><button data-v="not_cited">Не цитирует</button>
    <input placeholder="Причина (что проверил)" value="${{esc(d.reason || '')}}">`;
  const bs = div.querySelectorAll('button'), inp = div.querySelector('input');
  const paint = () => {{ const v = (S.attribution[e.id] || {{}}).value; bs.forEach(b => b.classList.toggle('on', b.dataset.v === v)); div.classList.toggle('done', !!v); }};
  bs.forEach(b => b.onclick = () => {{ S.attribution[e.id] = {{...(S.attribution[e.id] || {{}}), value: b.dataset.v}}; save(); paint(); }});
  inp.oninput = () => {{ S.attribution[e.id] = {{...(S.attribution[e.id] || {{}}), reason: inp.value}}; save(); }};
  paint(); document.getElementById('att').appendChild(div);
}});
D.claims.forEach(e => {{
  const d = S.claims[e.id] || {{}};
  const div = document.createElement('div'); div.className = 'e';
  div.innerHTML = `<div class="muted">${{esc(e.id)}} · источник: ${{link(e.source)}}</div>
    ${{e.flags.map(f => `<div class="warn">${{esc(f)}}</div>`).join('')}}
    <div class="muted" style="margin-top:6px">Старый текст</div><div class="old">${{esc(e.old)}}</div>
    <div class="muted" style="margin-top:6px">Заметка аннотатора: ${{esc(e.note)}}</div>
    <div class="muted" style="margin-top:6px">Нельзя: ${{e.coined_names.length ? esc(e.coined_names.join('; ')) : 'придуманных названий нет'}} · <span class="wc"></span></div>
    <textarea>${{esc(d.text ?? e.new)}}</textarea>
    <button data-v="accept">Принять текст из поля</button><button data-v="keep_old">Оставить старый</button>
    <input placeholder="Причина" value="${{esc(d.reason || '')}}">`;
  const ta = div.querySelector('textarea'), wc = div.querySelector('.wc'), bs = div.querySelectorAll('button'), inp = div.querySelector('input');
  const upd = () => {{ const n = nwords(ta.value); wc.textContent = n + ' слов из ' + D.max; wc.style.color = n > D.max ? 'var(--warn)' : ''; }};
  const paint = () => {{ const v = (S.claims[e.id] || {{}}).decision; bs.forEach(b => b.classList.toggle('on', b.dataset.v === v)); div.classList.toggle('done', !!v); }};
  ta.oninput = () => {{ S.claims[e.id] = {{...(S.claims[e.id] || {{}}), text: ta.value}}; save(); upd(); }};
  bs.forEach(b => b.onclick = () => {{
    if (b.dataset.v === 'accept' && nwords(ta.value) > D.max) {{ alert('Больше ' + D.max + ' слов: сократи текст в поле.'); return; }}
    S.claims[e.id] = {{...(S.claims[e.id] || {{}}), decision: b.dataset.v, text: ta.value}}; save(); paint(); }});
  inp.oninput = () => {{ S.claims[e.id] = {{...(S.claims[e.id] || {{}}), reason: inp.value}}; save(); }};
  upd(); paint(); document.getElementById('cl').appendChild(div);
}});
document.getElementById('dl').onclick = () => {{
  const out = {{format: 'coelacanth-checks-decisions/v1', decided_by: 'damir', page_hash: D.hash, exported_at: new Date().toISOString(),
    attribution: S.attribution, claims: S.claims}};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {{type: 'application/json'}}));
  a.download = 'checks-decisions.json'; a.click();
}};
count();
</script></main></body></html>
"""


def main() -> int:
    data = entries()
    (CHECKS / "decide-damir.html").write_text(page(data), encoding="utf-8")
    (CHECKS / "decide-damir-entries.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                                                      encoding="utf-8")
    print(f"{len(data['attribution'])} attribution pairs, {len(data['claims'])} claim texts -> "
          f"{(CHECKS / 'decide-damir.html').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
