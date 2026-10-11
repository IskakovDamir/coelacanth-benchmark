"""Compare a practice labels file with the reference key and print, in Russian, where they differ and why.

    PYTHONPATH=src ~/.venvs/rediscovery-layer/bin/python docs/annotators/practice/compare.py <your-labels.json>
    python3 compare.py practice-a1-labels.json          # from this folder, without the repository
    python3 compare.py --all practice-a1-labels.json    # also explain the fields that agree

Reads practice.json, key.json and key-explanations.json from this folder. Standard library only, Python 3.8 or
later. When the coelacanth package can be imported (PYTHONPATH=src, or this file inside the repository), the labels
file is first checked with annotation.labels_problems, the network-free part of check-labels, and its messages are
printed as they are (in English).

For each item and candidate it compares the item verdict, the key flags of the chart, the relation, the covered,
analogous and restricted elements, which pre-filled quote pairs were kept or rejected, which elements have evidence
(and general-form evidence), the cross-field flag, attribution and confidence, and prints the key's reasoning from
key-explanations.json for each difference, then the key's candidate note. An empty candidate note, and an item whose
verdict is nothing_close with empty item notes, get a reminder that does not count as a difference. At the end it counts agreement on the four distinctions of LABELING.md
section 5 and lists what to discuss with Damir.

Exit status: 0 when the labels agree with the key, 1 when there are differences, 2 when a file cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRECEDENCE = ("equivalent", "specialization", "generalization", "partial", "similar_mechanism")
ALREADY_DONE = ("equivalent", "specialization")
ELEMENT_LISTS = (("covered_elements", "Покрытые элементы (covered)"),
                 ("analogous_elements", "Аналогичные элементы (analogous)"),
                 ("restricted_elements", "Ограниченные элементы (restricted)"))
KEY_WORDS = {True: "ключевой", False: "не ключевой", None: "не решено"}
ATTRIBUTION_WORDS = {"cited": "cited (цитирует)", "not_cited": "not_cited (не цитирует)",
                     "unknown": "unknown (неизвестно)"}


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def derive_verdict(candidates) -> str:
    """items.derive_verdict: the strongest judged relation by precedence, or nothing_close."""
    judged = {c.get("relation") for c in candidates if isinstance(c, dict)}
    return next((r for r in PRECEDENCE if r in judged), "nothing_close")


def labels_checker():
    """annotation.labels_problems when the coelacanth package can be imported, else None."""
    parents = HERE.parents
    src = parents[2] / "src" if len(parents) > 2 else None  # docs/annotators/practice inside the repository
    if src is not None and (src / "coelacanth").is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from coelacanth.benchmark.annotation import labels_problems
    except Exception:  # outside the repository: the comparison still runs
        return None
    return labels_problems


def listing(values) -> str:
    values = [str(v) for v in values or []]
    return ", ".join(values) if values else "нет"


def word(value, words=None) -> str:
    if value is None:
        return "не указано"
    if words and value in words:
        return words[value]
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def evidence_sets(packet_cand: dict, cand: dict) -> tuple[set, set, set]:
    """Elements with evidence, elements with general-form evidence (kept pre-filled pairs plus added ones) and
    elements with added evidence."""
    rejected = set(cand.get("rejected_evidence") or [])
    added = [ev for ev in cand.get("added_evidence") or [] if isinstance(ev, dict)]
    entries = [ev for n, ev in enumerate(packet_cand.get("evidence") or []) if n not in rejected] + added
    return ({ev.get("element") for ev in entries}, {ev.get("element") for ev in entries if ev.get("general_form")},
            {ev.get("element") for ev in added})


class Report:
    """Collects the printed lines and the number of differences."""

    def __init__(self, show_all: bool) -> None:
        self.lines: list[str] = []
        self.differences = 0
        self.show_all = show_all

    def add(self, text: str = "", indent: int = 0) -> None:
        self.lines.append(" " * indent + text if text else "")

    def why(self, text: str | None, indent: int, label: str = "Почему") -> None:
        if text:
            self.add(f"{label}: {text}", indent)

    def field(self, name: str, mine, key, explanation: str | None, indent: int, agreed: list[str],
              words=None, extra: list[tuple[str, str]] | None = None, why_label: str = "Почему") -> None:
        """One compared field: a difference with the key's reasoning, or a match (explained with --all)."""
        if mine == key:
            agreed.append(name)
            if self.show_all:
                self.add(f"{name}: {word(key, words)} (совпало).", indent)
                self.why(explanation, indent + 4, why_label)
            return
        self.differences += 1
        self.add(f"{name}: у вас {word(mine, words)}, в ключе {word(key, words)}.", indent)
        self.why(explanation, indent + 4, why_label)
        for label, text in extra or []:
            self.why(text, indent + 4, label)


def compare_candidate(rep: Report, packet_cand: dict, mine: dict | None, key: dict, expl: dict) -> dict:
    """Print the differences for one packet candidate; returns what the summary counts."""
    cid = key["cid"]
    work = packet_cand.get("work") or {}
    rep.add(f"Кандидат {cid}: {work.get('title')} ({work.get('year')}, {work.get('venue')})", 2)
    if mine is None:
        rep.differences += 1
        rep.add("В вашем файле нет меток этого кандидата.", 4)
        mine = {}
    agreed: list[str] = []
    relation, key_relation = mine.get("relation"), key.get("relation")
    not_this = (expl.get("not") or {}).get(relation) if relation else None
    rep.field("Отношение", relation, key_relation, expl.get("relation"), 4, agreed,
              extra=[(f"Почему не {relation}", not_this)] if not_this else None,
              why_label=f"Почему в ключе {key_relation}")
    for name, title in ELEMENT_LISTS:
        mine_set = sorted(set(mine.get(name) or []))
        key_set = sorted(set(key.get(name) or []))
        if mine_set == key_set:
            agreed.append(title)
            if rep.show_all:
                rep.add(f"{title}: {listing(key_set)} (совпало).", 4)
                rep.why(expl.get(name), 8)
            continue
        rep.differences += 1
        rep.add(f"{title}: у вас {listing(mine_set)}; в ключе {listing(key_set)}.", 4)
        rep.why(expl.get(name), 8)
    pairs = packet_cand.get("evidence") or []
    mine_rejected, key_rejected = set(mine.get("rejected_evidence") or []), set(key.get("rejected_evidence") or [])
    for n, ev in enumerate(pairs):
        label = f"Пара цитат #{n + 1} ({ev.get('element')})"
        reason = (expl.get("evidence") or {}).get(str(n))
        if (n in mine_rejected) == (n in key_rejected):
            agreed.append(label)
            if rep.show_all:
                rep.add(f"{label}: {'отклонена' if n in key_rejected else 'оставлена'} (совпало).", 4)
                rep.why(reason, 8)
            continue
        rep.differences += 1
        if n in mine_rejected:
            rep.add(f"{label}: вы её отклонили, ключ её оставляет.", 4)
        else:
            rep.add(f"{label}: вы её оставили, ключ её отклоняет.", 4)
        rep.why(reason, 8)
    # Pre-filled pairs are compared above; here only the pairs added in the tool and the general-form pairs.
    mine_ev, mine_general, mine_added = evidence_sets(packet_cand, mine)
    key_ev, key_general, key_added = evidence_sets(packet_cand, key)
    missing, extra = sorted(key_added - mine_ev, key=str), sorted(mine_added - key_ev, key=str)
    missing_general = sorted(key_general - mine_general, key=str)
    if missing or missing_general:
        rep.differences += 1
        if missing:
            rep.add(f"Доказательства: в ключе есть пары цитат для {listing(missing)}, у вас нет.", 4)
        if missing_general:
            rep.add(f"Общая форма (general form): в ключе есть такая пара для {listing(missing_general)}, у вас нет.",
                    4)
        rep.why(expl.get("added_evidence"), 8)
    elif rep.show_all:
        rep.add("Доказательства: совпало.", 4)
        rep.why(expl.get("added_evidence"), 8)
    if extra:
        rep.add(f"Для сведения: у вас есть пары цитат для {listing(extra)}, в ключе нет. Это не ошибка, если цитаты "
                "дословные и подтверждают элемент; покажите их Дамиру.", 4)
    rep.field("Из другой области (cross-field)", mine.get("cross_field"), key.get("cross_field"),
              expl.get("cross_field"), 4, agreed)
    rep.field("Атрибуция", mine.get("attribution"), key.get("attribution"), expl.get("attribution"), 4, agreed,
              ATTRIBUTION_WORDS)
    rep.field("Уверенность", mine.get("confidence"), key.get("confidence"), expl.get("confidence"), 4, agreed)
    if agreed and not rep.show_all:
        rep.add(f"Совпало: {', '.join(agreed)}.", 4)
    if (key.get("note") or "").strip():
        rep.add(f"Заметка ключа: {key['note'].strip()}", 4)
    if not (mine.get("note") or "").strip():
        rep.add("Ваша заметка к кандидату (Note) пустая. В настоящем батче коротко напишите в ней по-английски, "
                "почему такая метка (инструкция, шаг 10).", 4)
    return {"mine_gold": relation in PRECEDENCE, "key_gold": key_relation in PRECEDENCE,
            "mine_cross": mine.get("cross_field"), "key_cross": key.get("cross_field")}


def compare(batch: dict, key: dict, explanations: dict, labels: dict, show_all: bool = False,
            problems: list[str] | None = None) -> Report:
    """The comparison of one labels file with the key, as printed lines and a count of differences."""
    rep = Report(show_all)
    general = explanations.get("general") or {}
    rep.add("Учебный батч: ваши метки и ключ")
    rep.add(f"Разметчик: {labels.get('annotator')}; батч: {labels.get('batch')}.")
    if problems is None:
        rep.add("Проверка формата пропущена: пакет coelacanth не найден. Сравнение ниже от этого не зависит.")
    elif problems:
        rep.add(f"Проверка формата (те же правила, что у check-labels; сообщения по-английски), найдено проблем: "
                f"{len(problems)}. Их видно и в инструменте, в рамке над пунктом.")
        for problem in problems:
            rep.add(f"- {problem}", 2)
    else:
        rep.add("Проверка формата: проблем нет.")
    mine_items = {i.get("id"): i for i in labels.get("items") or [] if isinstance(i, dict)}
    key_items = {i["id"]: i for i in key["items"]}
    counts = {"verdict": [0, 0], "done": [0, 0], "gold": [0, 0], "cross": [0, 0]}
    discuss: list[tuple[str, list[str]]] = []
    for packet in batch["items"]:
        pid = packet["id"]
        expl = (explanations.get("items") or {}).get(pid) or {}
        key_item = key_items[pid]
        mine = mine_items.get(pid)
        rep.add()
        rep.add(f"==== {pid}: {expl.get('title', '')} ====")
        before = rep.differences
        if mine is None:
            rep.differences += 1
            rep.add(general.get("missing", ""), 2)
            discuss.append((pid, expl.get("discuss") or []))
            continue
        if mine.get("status") != "done":
            rep.differences += 1
            rep.add(general.get("skipped", ""), 2)
        mine_cands = [c for c in mine.get("candidates") or [] if isinstance(c, dict)]
        added = [c for c in mine.get("added_candidates") or [] if isinstance(c, dict)]
        mine_verdict = derive_verdict(mine_cands + added)
        key_verdict = key_item["verdict"]
        counts["verdict"][1] += 1
        counts["verdict"][0] += mine_verdict == key_verdict
        counts["done"][1] += 1
        counts["done"][0] += (mine_verdict in ALREADY_DONE) == (key_verdict in ALREADY_DONE)
        rep.field("Вердикт пункта", mine_verdict, key_verdict, expl.get("verdict"), 2, [])
        if mine_verdict == key_verdict and not show_all:
            rep.add(f"Вердикт пункта: {key_verdict}, совпало.", 2)
        claim, key_claim = mine.get("claim") or {}, key_item["claim"]
        if claim.get("text") != key_claim["text"]:
            rep.add(general.get("text_edited", ""), 2)
        if claim.get("skeleton") != key_claim["skeleton"]:
            rep.add(general.get("skeleton_edited", ""), 2)
        chart = {e.get("id"): e for e in claim.get("chart") or [] if isinstance(e, dict)}
        key_chart = {e["id"]: e for e in key_claim["chart"]}
        chart_agreed: list[str] = []
        rep.add("Карта утверждения:", 2)
        for eid, element in key_chart.items():
            reason = (expl.get("chart") or {}).get(eid)
            if eid not in chart:
                rep.differences += 1
                rep.add(f"{eid}: вы удалили этот элемент. {general.get('chart_removed', '')}", 4)
                rep.why(reason, 8, "Ключ")
                continue
            rep.field(f"{eid}", chart[eid].get("key"), element["key"], reason, 4, chart_agreed, KEY_WORDS)
        for eid, element in chart.items():
            if eid not in key_chart:
                rep.add(f"{eid} («{element.get('text')}», {word(element.get('key'), KEY_WORDS)}): "
                        f"{general.get('chart_added', '')}", 4)
        if chart_agreed and not show_all:
            rep.add(f"Ключевые флажки совпали: {', '.join(chart_agreed)}.", 4)
        mine_by_cid = {c.get("cid"): c for c in mine_cands}
        packet_cands = {c["cid"]: c for c in packet.get("candidates") or []}
        for key_cand in key_item["candidates"]:
            cid = key_cand["cid"]
            got = compare_candidate(rep, packet_cands[cid], mine_by_cid.get(cid), key_cand,
                                    (expl.get("candidates") or {}).get(cid) or {})
            counts["gold"][1] += 1
            counts["gold"][0] += got["mine_gold"] == got["key_gold"]
            if got["key_gold"]:
                counts["cross"][1] += 1
                counts["cross"][0] += got["mine_gold"] and got["mine_cross"] == got["key_cross"]
        if added:
            rep.add(f"Ваши добавленные кандидаты: {listing(c.get('cid') for c in added)}. "
                    f"{general.get('added_candidates', '')}", 2)
        if mine_verdict == "nothing_close" and not (mine.get("notes") or "").strip():
            rep.add(general.get("search_notes", ""), 2)
        if rep.differences > before or show_all:
            discuss.append((pid, expl.get("discuss") or []))
    rep.add()
    rep.add("==== Итог ====")
    rep.add(general.get("critical", ""))
    for name, (agree, total) in (("вердикт пункта (шесть вариантов)", counts["verdict"]),
                                 ("«уже сделано» или нет", counts["done"]),
                                 ("gold или нет по кандидатам", counts["gold"]),
                                 ("флажок cross-field у gold-кандидатов", counts["cross"])):
        rep.add(f"- {name}: совпало {agree} из {total}", 2)
    rep.add(f"Всего расхождений: {rep.differences}.")
    rep.add()
    rep.add("Что обсудить с Дамиром:")
    for pid, questions in discuss:
        if questions:
            rep.add(f"{pid}:", 2)
            for question in questions:
                rep.add(f"- {question}", 4)
    rep.add("Общие вопросы:", 2)
    for question in general.get("discuss_always") or []:
        rep.add(f"- {question}", 4)
    return rep


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # Russian text on a Windows console
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Сравнить метки учебного батча с ключом.")
    parser.add_argument("labels", help="файл меток, экспортированный из annotate.html (practice-<ID>-labels.json)")
    parser.add_argument("--all", action="store_true", help="объяснять и совпавшие поля")
    args = parser.parse_args(argv)
    try:
        batch = read_json(HERE / "practice.json")
        key = read_json(HERE / "key.json")
        explanations = read_json(HERE / "key-explanations.json")
        labels = read_json(Path(args.labels))
    except (OSError, ValueError) as err:
        print(f"Не удалось прочитать файл: {err}")
        return 2
    if not isinstance(labels, dict) or labels.get("batch") != batch["batch"]:
        found = labels.get("batch") if isinstance(labels, dict) else None
        print(f"Это не метки учебного батча: в файле batch {found!r}, нужен {batch['batch']!r}. Экспортируйте метки "
              "из инструмента, в который загружен practice.json.")
        return 2
    checker = labels_checker()
    problems = checker(batch, labels) if checker else None
    rep = compare(batch, key, explanations, labels, args.all, problems)
    print("\n".join(rep.lines))
    return 1 if rep.differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
