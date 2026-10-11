"""Screenshots of tools/annotate.html for docs/annotators/instrukciya.md, from a fictional demo batch.

    python3 docs/annotators/screens/make_screens.py [--chrome PATH] [--out DIR] [--only 05-labels ...]

Standard library only; it needs a local Google Chrome (headless). Run it again after tools/annotate.html changes.

The demo batch is made up for the instructions (works titled "not a real paper", DOIs under Crossref's test prefix
10.5555), so the pictures show no benchmark item and no answer of the practice batch. Each shot is the page with a
small script appended that loads the demo batch as annotator a1, imports demo labels and scrolls to one part of the
page; headless Chrome, with a throwaway profile, takes the picture. Each picture shows the page as it renders.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # docs/annotators/screens/make_screens.py
TOOL = REPO / "tools" / "annotate.html"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

FIELDS = ["claim.text", "claim.skeleton", "claim.coined_names", "claim.date", "claim.field", "claim.domain",
          "claim.computational", "claim.source_work", "claim.chart", "candidates[].work", "candidates[].evidence",
          "candidates[].attribution_hint", "candidates[].note", "notes"]


def work(n, title, author, year, date, venue, field, domain):
    return {"openalex": f"W00000000{n:02d}", "doi": f"10.5555/demo.{n}", "arxiv": None, "pmid": None,
            "title": title, "first_author": author, "n_authors": 1, "year": year, "publication_date": date,
            "venue": venue, "field": field, "domain": domain, "oa_url": None, "cache_key": f"oa-W00000000{n:02d}"}


def ev(element, cand, claim):
    return {"element": element, "quote_candidate": cand, "quote_claim": claim, "location_candidate": "abstract",
            "location_claim": "abstract", "text_level": "abstract", "general_form": False}


def packet(item_id, n):
    return {
        "id": item_id,
        "prefill": {"by": "claude-opus-5-5", "date": "2026-10-07", "status": "draft", "fields": FIELDS},
        "claim": {
            "text": "After a single intravenous dose, the plasma concentration of the drug falls by the same fraction "
                    "in every equal time interval, so the time needed for the concentration to halve does not depend "
                    "on the dose.",
            "skeleton": "A quantity that decreases at a rate proportional to its current value takes the same time to "
                        "halve from any starting value.",
            "coined_names": ["demo kinetics"],
            "date": "2001-05-14", "field": "Pharmacology, Toxicology and Pharmaceutics", "domain": "Life Sciences",
            "computational": True,
            "source_work": work(n, "Demo source work for the instructions (not a real paper)", "D. Example", 2001,
                                "2001-05-14", "Demo Journal of Pharmacology", "Pharmacology", "Life Sciences"),
            "chart": [
                {"id": "e1", "text": "the concentration falls at a rate proportional to the current concentration",
                 "key": None},
                {"id": "e2", "text": "the halving time is the same at every concentration", "key": None},
                {"id": "e3", "text": "measured in plasma after one intravenous dose", "key": None}]},
        "candidates": [
            {"cid": "c1",
             "work": work(n + 1, "Demo candidate: decay of a radioactive sample (not a real paper)", "E. Sample", 1950,
                          "1950-03-01", "Demo Physics Letters", "Physics and Astronomy", "Physical Sciences"),
             "evidence": [ev("e1", "the number of undecayed nuclei decreases at a rate proportional to the number "
                                   "present",
                             "the plasma concentration of the drug falls by the same fraction in every equal time "
                             "interval"),
                          ev("e2", "the half-life of the sample does not depend on its initial size",
                             "the time needed for the concentration to halve does not depend on the dose")],
             "attribution_hint": "not_cited",
             "note": "Physics and Astronomy; 1950, Demo Physics Letters. Text: abstract only cached "
                     f"(oa-W00000000{n + 1:02d}). 2 pre-filled quote pairs."},
            {"cid": "c2",
             "work": work(n + 2, "Demo candidate: savings that double at a fixed interest rate (not a real paper)",
                          "F. Ledger", 1962, "1962-06-01", "Demo Review of Finance",
                          "Economics, Econometrics and Finance", "Social Sciences"),
             "evidence": [],
             "attribution_hint": "unknown",
             "note": "Economics, Econometrics and Finance; 1962, Demo Review of Finance. Text: abstract only cached "
                     f"(oa-W00000000{n + 2:02d}). 0 pre-filled quote pairs."}],
        "notes": "Demo item for the instructions; it is not a benchmark item and its works are made up. Only "
                 "abstracts are cached, so every quote pair is at abstract level."}


BATCH = {"format": "coelacanth-batch/v1", "batch": "demo", "created": "2026-10-07",
         "labels": ["equivalent", "specialization", "generalization", "partial", "similar_mechanism", "different"],
         "items": [packet("cf-9997", 1), packet("cf-9998", 11), packet("cf-9999", 21)]}


def labels_for(packet, done, relation_c1=None, full=False, added=None, minutes=0, keys=(True, None, None)):
    chart = [{"id": e["id"], "text": e["text"], "key": k}
             for e, k in zip(packet["claim"]["chart"], ([True, True, False] if (full or done) else list(keys)))]

    def cand(cid, relation, analogous=(), cross=None, attribution=None, confidence=None, note=""):
        return {"cid": cid, "relation": relation, "covered_elements": [], "analogous_elements": list(analogous),
                "restricted_elements": [], "attribution": attribution, "cross_field": cross, "confidence": confidence,
                "rejected_evidence": [], "added_evidence": [], "note": note}

    if done or full:
        cands = [cand("c1", "similar_mechanism", ["e1", "e2"], True, "not_cited", 2,
                      "Same first-order decay form, but the variable is a count of nuclei, not a plasma "
                      "concentration (clarification 5). Abstract only. Checked OpenAlex references (12 works)."),
                 cand("c2", "different", (), True, "unknown", 3, "Growth, not decay; no halving time.")]
    else:
        cands = [cand("c1", relation_c1), cand("c2", None)]
    item = {"id": packet["id"], "minutes": minutes, "claim": {"text": packet["claim"]["text"],
                                                              "skeleton": packet["claim"]["skeleton"], "chart": chart},
            "text_edited": False, "candidates": cands, "added_candidates": added or [], "verdict": None,
            "status": "done" if done else "skipped", "prefill_status": "draft", "notes": ""}
    precedence = ["equivalent", "specialization", "generalization", "partial", "similar_mechanism"]
    rels = {c["relation"] for c in cands + (added or [])}
    item["verdict"] = next((r for r in precedence if r in rels), "nothing_close")
    return item


ADDED = [{"cid": "a1", "work": {"doi": "10.5555/demo.40", "title": "Demo work found by the annotator (not a real paper)",
                                "year": 1958, "first_author": "G. Finder", "field": "chemical kinetics"},
          "evidence": [], "relation": None, "covered_elements": [], "analogous_elements": [], "restricted_elements": [],
          "attribution": None, "cross_field": None, "confidence": None, "note": ""}]

LABELS = {"format": "coelacanth-labels/v2", "batch": "demo", "annotator": "a1", "exported_at": "2026-10-07T12:00:00Z",
          "items": [labels_for(BATCH["items"][0], True, minutes=34),
                    labels_for(BATCH["items"][1], False, relation_c1="similar_mechanism", added=ADDED, minutes=12),
                    labels_for(BATCH["items"][2], False, keys=(None, None, None))]}

# What each shot does after the demo batch is loaded and the labels imported. `item`: which item to open; `scroll`:
# text of a heading or button to scroll to the top; `act`: extra steps.
SHOTS = {
    "01-load": {"load": False},
    "02-items": {"item": "cf-9998"},
    "03-claim-chart": {"item": "cf-9997", "scroll": "h2:Claim", "height": 1060},
    "04-candidate-quotes": {"item": "cf-9997", "scroll": "card:c1", "offset": 10},
    "05-labels": {"item": "cf-9997", "scroll": "text:Relation of the claim to this candidate", "offset": 40},
    "06-add-quote": {"item": "cf-9998", "act": "addquote", "scroll": "button:add a quote pair to c2", "offset": 260},
    "07-warnings": {"item": "cf-9998", "scroll": "h2:Before you mark this item done", "offset": 140},
    "08-mark-done": {"item": "cf-9997"},
    "09-add-candidate": {"item": "cf-9998", "scroll": "text:found by you", "offset": 60},
    "10-export": {"item": "cf-9997", "act": "export", "width": 1440},
}

INJECT = r"""
<script>
(() => {
  const SHOT = __SHOT__;
  if (SHOT.load === false) return;
  const BATCH = __BATCH__;
  const LABELS = __LABELS__;
  window.confirm = () => true;
  window.alert = () => {};
  window.coelacanthLoadBatch(BATCH, "a1");
  window.coelacanthImport(LABELS);
  const byText = (sel, text) => [...document.querySelectorAll(sel)].find(e => e.textContent.trim().startsWith(text));
  const open = id => {
    const btn = [...document.querySelectorAll("button.item-link")].find(b => b.textContent.includes(id));
    if (btn) btn.click();
  };
  open(SHOT.item);
  const setValue = (el, value) => { el.value = value; el.dispatchEvent(new Event("input", {bubbles: true}));
                                    el.dispatchEvent(new Event("change", {bubbles: true})); };
  if (SHOT.act === "addquote") {
    const add = document.querySelector('button[aria-label="add a quote pair to c2"]');
    if (add) add.click();
    const q = sel => document.querySelector(sel);
    const L = "c2 quote pair 1: ";
    const el = q(`select[aria-label="${L}chart element"]`);
    if (el) setValue(el, "e1");
    const abs = q(`input[aria-label="${L}abstract only"]`);
    if (abs) abs.click();
    const fillIn = (prefix, value) => { const f = q(`[aria-label^="${L}${prefix}"]`); if (f) setValue(f, value); };
    fillIn("quote from the candidate", "the money in the account grows at a rate proportional to the amount already there");
    fillIn("location in the candidate", "abstract");
    fillIn("quote from the claim source", "the plasma concentration of the drug falls by the same fraction in every equal time interval");
    fillIn("location in the claim source", "abstract");
  }
  if (SHOT.act === "export") {
    const btn = [...document.querySelectorAll("button")].find(b => b.textContent.trim() === "Export labels");
    if (btn) btn.click();
  }
  let target = null;
  if (SHOT.scroll) {
    const [kind, text] = [SHOT.scroll.slice(0, SHOT.scroll.indexOf(":")), SHOT.scroll.slice(SHOT.scroll.indexOf(":") + 1)];
    if (kind === "h2") target = byText("h2", text);
    if (kind === "text") target = [...document.querySelectorAll("body *")].find(e => e.children.length === 0 && e.textContent.trim().startsWith(text));
    if (kind === "button") target = document.querySelector(`button[aria-label="${text}"]`);
    if (kind === "card") target = document.querySelector(`div.cand[data-cid="${text}"]`);
  }
  setTimeout(() => {
    if (target) {
      target.scrollIntoView({block: "start"});
      document.scrollingElement.scrollTop = 0;
      const pane = document.querySelector("main");
      if (pane) pane.scrollTop -= (SHOT.offset || 0);
    } else {
      window.scrollTo(0, 0);
    }
  }, 50);
})();
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", default=CHROME)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    page = TOOL.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        for name, shot in SHOTS.items():
            if args.only and name not in args.only:
                continue
            script = (INJECT.replace("__SHOT__", json.dumps(shot)).replace("__BATCH__", json.dumps(BATCH))
                      .replace("__LABELS__", json.dumps(LABELS)))
            html = page[:page.rindex("</body>")] + script + page[page.rindex("</body>"):]
            path = Path(tmp) / f"{name}.html"
            path.write_text(html, encoding="utf-8")
            profile = Path(tmp) / f"profile-{name}"
            target = out / f"{name}.png"
            if target.exists():
                target.unlink()
            # headless Chrome on macOS may keep running after it wrote the picture: wait for the file, then stop it
            proc = subprocess.Popen([args.chrome, "--headless=new", f"--user-data-dir={profile}", "--no-first-run",
                                     "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
                                     "--force-device-scale-factor=1", f"--window-size={shot.get('width', 1280)},{shot.get('height', 800)}",
                                     "--virtual-time-budget=4000", f"--screenshot={target}", path.as_uri()],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            size, stable = -1, 0
            for _ in range(600):
                time.sleep(0.1)
                now = target.stat().st_size if target.exists() else -1
                stable = stable + 1 if now == size and now > 0 else 0
                size = now
                if stable >= 10 or proc.poll() is not None:
                    break
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            if not target.exists():
                raise SystemExit(f"no screenshot for {name}")
            print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
