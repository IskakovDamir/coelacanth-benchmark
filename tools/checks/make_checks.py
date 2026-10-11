"""Pages for the annotators' remaining checks on b01 and b02 (Russian UI, self-contained, no network requests):

- attribution: for every cross-field gold pair of an item that could be in R (human origin, not an attributed
  control, not a built negative), does the source work cite the candidate? Two annotators per pair. The quiz did not
  ask this, and R stays provisional until a person confirms it (labels.json notes).
- claimcheck: is each probed item's claim text faithful to its source, free of names, title words, years and coined
  names, and in the source field's vocabulary? One annotator per item (SCHEMA.md rule 5: `claim.checked_by` is a
  person other than the writer, here Claude). A corrected text is probed again closed-book.

    PYTHONPATH=src python tools/checks/make_checks.py --annotators a1 a2 a3

writes eval/benchmark/v0/checks/send/<kind>-<annotator>.html and eval/benchmark/v0/checks/assignment.json. Each
page asks who answered and downloads <kind>-<annotator>.json.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V0 = REPO / "eval" / "benchmark" / "v0"
BATCHES = [V0 / "batches" / "b01.json", V0 / "batches" / "b02.json"]
LABELS = V0 / "quiz" / "labels.json"
CLOSED_BOOK_ITEMS = REPO / "eval" / "results" / "closed-book-v0" / "items.jsonl"
FULLTEXT = REPO / "cache" / "fulltext"
OUT = V0 / "checks"
SEED = 20261010


def links(work: dict) -> list[tuple[str, str]]:
    out = []
    if work.get("doi"):
        doi = work["doi"]
        out.append(("DOI", doi if doi.startswith("http") else f"https://doi.org/{doi}"))
    if work.get("arxiv"):
        out.append(("arXiv", f"https://arxiv.org/abs/{work['arxiv']}"))
    if work.get("pmid"):
        out.append(("PubMed", f"https://pubmed.ncbi.nlm.nih.gov/{work['pmid']}/"))
    if work.get("openalex"):
        out.append(("OpenAlex", f"https://openalex.org/{work['openalex']}"))
    return out


def work_view(work: dict) -> dict:
    return {"title": work.get("title"), "first_author": work.get("first_author"), "year": work.get("year"),
            "venue": work.get("venue"), "field": work.get("field"), "links": links(work)}


def packets() -> dict[str, dict]:
    out = {}
    for path in BATCHES:
        for p in json.loads(path.read_text(encoding="utf-8"))["items"]:
            out[p["id"]] = p
    return out


def attribution_entries(labels: dict, pk: dict[str, dict]) -> list[dict]:
    entries = []
    for item, v in sorted(labels["items"].items()):
        if v["origin_kind"] != "human" or v["role"] == "attributed_control" or v["negative"]:
            continue
        cands = {c["cid"]: c for c in pk[item]["candidates"]}
        for cid in v["cross_field_gold_candidates"]:
            entries.append({"id": f"{item}/{cid}", "item": item, "claim_date": pk[item]["claim"]["date"],
                            "source": work_view(pk[item]["claim"]["source_work"]),
                            "candidate": work_view(cands[cid]["work"])})
    return entries


def abstract(work: dict) -> str | None:
    """The text shown beside the claim: an AI-origin method's own record (`aio-`), else the OpenAlex abstract."""
    key = work.get("cache_key") if (work.get("cache_key") or "").startswith("aio-") else \
        (f"oa-{work.get('openalex')}" if work.get("openalex") else None)
    path = FULLTEXT / f"{key}.txt" if key else None
    if path and path.exists():
        text = path.read_text(encoding="utf-8").strip()
        return text[:3000]
    return None


def claimcheck_entries(pk: dict[str, dict]) -> list[dict]:
    probed = [json.loads(line)["pid"] for line in CLOSED_BOOK_ITEMS.open(encoding="utf-8")]
    entries = []
    for item in probed:
        if item not in pk:  # items of later batches get their own page
            continue
        claim = pk[item]["claim"]
        entries.append({"id": item, "item": item, "claim_date": claim["date"], "claim_text": claim["text"],
                        "coined_names": claim.get("coined_names") or [], "field": claim.get("field"),
                        "source": work_view(claim["source_work"]), "abstract": abstract(claim["source_work"])})
    return entries


def assign(ids: list[str], annotators: list[str], per_item: int, salt: str) -> dict[str, list[str]]:
    """Each id to `per_item` annotators, rotating so loads stay within one of each other, in a seeded order."""
    order = sorted(ids)
    random.Random(f"{SEED}:{salt}").shuffle(order)
    loads = {a: [] for a in annotators}
    k = 0
    for i in order:
        chosen = [annotators[(k + j) % len(annotators)] for j in range(per_item)]
        k += per_item
        for a in chosen:
            loads[a].append(i)
    return {a: sorted(v) for a, v in loads.items()}


TEXTS = {
    "attribution": {
        "title": "Цитирование",
        "intro": ("Для каждой пары: поздняя работа (источник утверждения) и ранняя работа из другой области. "
                  "Вопрос один: <b>цитирует ли поздняя работа раннюю?</b> Открой полный текст поздней работы по "
                  "ссылке (DOI, arXiv или PubMed) и найди раннюю работу в списке литературы: по фамилии первого "
                  "автора, году и названию. Если полного текста нет в открытом доступе, попробуй через библиотеку; "
                  "если не получилось, отвечай «Не удалось проверить» и напиши почему."),
        "options": [("cited", "Да, цитирует"), ("not_cited", "Нет, не цитирует"),
                    ("cannot_check", "Не удалось проверить")],
    },
    "claimcheck": {
        "title": "Проверка текстов утверждений",
        "intro": ("Каждый текст ниже написал Claude по работе-источнику; именно его увидят системы. Сверь его с "
                  "источником (ссылка и аннотация ниже; при сомнении открой полный текст) и ответь: <b>можно "
                  "оставить как есть?</b> «Нужна правка», если текст говорит то, чего в источнике нет, теряет "
                  "главное, называет авторов, слова из названия подряд, год или придуманное название метода "
                  "(список ниже), или пишет не языком области источника. Тогда впиши исправленный текст целиком."),
        "options": [("ok", "Можно оставить"), ("fix", "Нужна правка")],
    },
}


def data_hash(kind: str, annotator: str, entries: list[dict]) -> str:
    return hashlib.sha256(json.dumps([kind, annotator, entries], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def page(kind: str, annotator: str, entries: list[dict]) -> str:
    t = TEXTS[kind]
    data = {"kind": kind, "annotator": annotator, "entries": entries, "options": t["options"],
            "hash": data_hash(kind, annotator, entries)}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(t['title'])}</title>
<style>
:root {{ --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --card:#fafafa; --on:#1d4ed8; --acc:#f97316; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#999; --line:#333; --card:#1a1a1a; --on:#7aa2ff; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; padding:16px; }}
main {{ max-width:860px; margin:0 auto; }}
.e {{ border:1px solid var(--line); background:var(--card); border-radius:6px; padding:12px 14px; margin:14px 0; }}
.e.done {{ opacity:.65; }}
.muted {{ color:var(--muted); font-size:13px; }}
.claim {{ border-left:3px solid var(--acc); padding-left:8px; margin:6px 0; }}
.abs {{ font-size:13px; white-space:pre-wrap; max-height:180px; overflow:auto; border:1px solid var(--line); padding:6px; }}
button {{ font:inherit; padding:6px 12px; margin:6px 8px 0 0; border:1px solid var(--line); background:var(--bg); color:var(--fg); border-radius:4px; cursor:pointer; }}
button.on {{ border-color:var(--on); color:var(--on); font-weight:600; }}
input, textarea {{ font:inherit; width:100%; box-sizing:border-box; margin-top:8px; padding:5px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; }}
textarea {{ min-height:90px; }}
#bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); z-index:1; }}
a {{ color:var(--on); }}
</style></head><body><main>
<div id="bar"><b>{html.escape(t['title'])}</b> · <span id="count"></span>
<input id="who" placeholder="Кто отвечает (имя или ID)" style="width:auto;min-width:240px;margin:0 8px">
<button id="dl">Скачать {html.escape(kind)}-{html.escape(annotator)}.json</button></div>
<p>{t['intro']}</p>
<p class="muted">Ответы сохраняются в этом браузере. В конце впиши, кто отвечал, и нажми «Скачать».</p>
<div id="list"></div>
<script id="data" type="application/json">{blob}</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const KEY = 'checks-' + D.kind + '-' + D.annotator + '-' + D.hash;
let S = {{}};
try {{ S = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(S)); }} catch (e) {{}} count(); }}
function count() {{ const n = D.entries.filter(e => S[e.id] && S[e.id].answer).length;
  document.getElementById('count').textContent = n + ' из ' + D.entries.length; }}
function esc(s) {{ return String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function work(w) {{ const l = (w.links || []).map(([n, u]) => `<a href="${{esc(u)}}" target="_blank" rel="noopener">${{esc(n)}}</a>`).join(' · ');
  return `<b>${{esc(w.first_author)}} ${{esc(w.year)}}</b>. ${{esc(w.title)}}. <span class="muted">${{esc(w.venue || '')}}${{w.field ? ' · ' + esc(w.field) : ''}}</span><div>${{l}}</div>`; }}
const list = document.getElementById('list');
D.entries.forEach((e, i) => {{
  const d = S[e.id] || {{}};
  const div = document.createElement('div'); div.className = 'e';
  let body = `<div class="muted">${{i + 1}}. ${{esc(e.item)}} · дата утверждения ${{esc(e.claim_date)}}</div>`;
  if (D.kind === 'attribution') {{
    body += `<div class="muted" style="margin-top:6px">Поздняя работа (источник утверждения)</div><div>${{work(e.source)}}</div>
      <div class="muted" style="margin-top:6px">Ранняя работа</div><div>${{work(e.candidate)}}</div>`;
  }} else {{
    body += `<div class="muted" style="margin-top:6px">Текст утверждения (его видят системы)</div><div class="claim">${{esc(e.claim_text)}}</div>
      <div class="muted">Нельзя в тексте: ${{e.coined_names.length ? esc(e.coined_names.join('; ')) : 'придуманных названий нет'}}, а также фамилии, год, три слова названия подряд</div>
      <div class="muted" style="margin-top:6px">Источник</div><div>${{work(e.source)}}</div>
      ${{e.abstract ? `<div class="muted" style="margin-top:6px">Аннотация источника</div><div class="abs">${{esc(e.abstract)}}</div>` : '<div class="muted">Аннотации нет в кэше: открой источник по ссылке.</div>'}}`;
  }}
  body += D.options.map(([v, label]) => `<button data-v="${{v}}">${{esc(label)}}</button>`).join('');
  if (D.kind === 'claimcheck') body += `<textarea placeholder="Исправленный текст целиком (только если «Нужна правка»)">${{esc(d.fixed_text || '')}}</textarea>`;
  body += `<input placeholder="Заметка: что не так или что проверил" value="${{esc(d.note || '')}}">`;
  div.innerHTML = body;
  const btns = div.querySelectorAll('button');
  function paint() {{ const v = (S[e.id] || {{}}).answer; btns.forEach(b => b.classList.toggle('on', b.dataset.v === v)); div.classList.toggle('done', !!v); }}
  btns.forEach(b => b.onclick = () => {{ S[e.id] = {{...(S[e.id] || {{}}), answer: b.dataset.v}}; save(); paint(); }});
  const ta = div.querySelector('textarea'); if (ta) ta.oninput = () => {{ S[e.id] = {{...(S[e.id] || {{}}), fixed_text: ta.value}}; save(); }};
  const inp = div.querySelector('input'); inp.oninput = () => {{ S[e.id] = {{...(S[e.id] || {{}}), note: inp.value}}; save(); }};
  paint(); list.appendChild(div);
}});
const whoEl = document.getElementById('who');
try {{ whoEl.value = localStorage.getItem(KEY + '-who') || ''; }} catch (e) {{}}
whoEl.oninput = () => {{ try {{ localStorage.setItem(KEY + '-who', whoEl.value); }} catch (e) {{}} }};
document.getElementById('dl').onclick = () => {{
  const who = whoEl.value.trim(); if (!who) {{ alert('Впиши, кто отвечал.'); return; }}
  const out = {{format: 'coelacanth-checks/v1', kind: D.kind, annotator: D.annotator, answered_by: who,
    page_hash: D.hash, exported_at: new Date().toISOString(), answers: S}};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {{type: 'application/json'}}));
  a.download = D.kind + '-' + D.annotator + '.json'; a.click();
}};
count();
</script></main></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotators", nargs="+", required=True)
    args = parser.parse_args()
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    pk = packets()
    kinds = {"attribution": (attribution_entries(labels, pk), 2), "claimcheck": (claimcheck_entries(pk), 1)}
    (OUT / "send").mkdir(parents=True, exist_ok=True)
    record = {"seed": SEED, "annotators": args.annotators, "kinds": {}}
    for kind, (entries, per_item) in kinds.items():
        by_id = {e["id"]: e for e in entries}
        loads = assign(list(by_id), args.annotators, per_item, kind)
        record["kinds"][kind] = {"per_item": per_item, "entries": len(entries), "pages": {}}
        for a, ids in loads.items():
            text = page(kind, a, [by_id[i] for i in ids])
            path = OUT / "send" / f"{kind}-{a}.html"
            path.write_text(text, encoding="utf-8")
            record["kinds"][kind]["pages"][a] = {"ids": ids, "data_hash": data_hash(kind, a, [by_id[i] for i in ids]),
                                                 "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
            print(f"{path.relative_to(REPO)}: {len(ids)} entries")
    (OUT / "assignment.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
