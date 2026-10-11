// Tests for tools/annotate.html, no dependencies: node tools/test_annotate.mjs (from the repo root).
//
// 1. The pure core (between CORE-BEGIN and CORE-END) runs in a vm context: v1 compatibility, the v2 rules, an
//    import and export round trip.
// 2. The whole page script runs against a minimal fake DOM (below): a pre-filled item and a bare item are labelled
//    through the page's own controls (add and remove chart elements, add quote pairs, add a candidate, mark done),
//    and window.coelacanthExport() must pass the core's labelsProblems. Also: labels of one copy of a batch never
//    load into the other (browser storage and import), shortcuts stay off while a select has focus, where focus
//    goes after a removal, restore at six elements, element ids never reused, hints for texts nobody could cache.
// 3. Parity with Python, when the project venv is there (or PYTHON points to a Python with the repo's src on
//    PYTHONPATH): the exports pass `check-labels --offline`, and a set of broken labels gets the same problem
//    messages from labels_problems() as from the core. Skipped with a message otherwise.
//
// Not covered: layout and styles, scrolling, real keyboard input (native select type-ahead, focus rings), file
// pickers, drag and drop, downloads, the timer. Those were checked by hand in a browser (tools/README.md).

import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {homedir, tmpdir} from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.dirname(HERE);
const html = readFileSync(path.join(HERE, "annotate.html"), "utf8");
const script = html.slice(html.indexOf("<script>") + "<script>".length, html.lastIndexOf("</script>"));
const coreSource = script.slice(script.indexOf("// CORE-BEGIN"), script.indexOf("// CORE-END"));

let passed = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const clone = x => JSON.parse(JSON.stringify(x));
// Values made inside a vm context have that context's Array and Object prototypes; compare plain copies.
const eq = (actual, expected, message) => assert.deepEqual(clone(actual), clone(expected), message);

// ---- fixtures: the packet of tests/test_annotation.py with a third element, a bare copy and the added work ----
const PACKET = {
  id: "cf-0001",
  prefill: {by: "claude-opus-5-5", date: "2026-10-06", status: "draft",
            fields: ["claim.text", "claim.skeleton", "claim.chart", "candidates[].evidence"]},
  claim: {text: "Estimate the population distribution of model parameters without a parametric form.",
          skeleton: "Fit an unknown mixing distribution by maximum likelihood over a grid of support points.",
          coined_names: ["NPAG"], date: "2013-03-26", field: "Pharmacology", domain: "Life Sciences", computational: true,
          source_work: {arxiv: "1", title: "Two methods", first_author: "Tatiana Tatarinova",
                        publication_date: "2013-03-26", year: 2013, cache_key: "arxiv-1"},
          chart: [{id: "e1", text: "unknown mixing distribution", key: null},
                  {id: "e2", text: "nonparametric maximum likelihood on a grid", key: null},
                  {id: "e3", text: "dosing from the estimate", key: null}]},
  candidates: [{cid: "c1", work: {openalex: "W2", title: "EM", first_author: "A. P. Dempster",
                                  publication_date: "1977-09-01", year: 1977, cache_key: "oa-W2"},
                evidence: [{element: "e2", quote_candidate: "computing maximum likelihood estimates",
                            quote_claim: "nonparametric maximum likelihood on a grid", location_candidate: "abstract",
                            location_claim: "abstract", text_level: "abstract", general_form: false},
                           {element: "e3", quote_candidate: "incomplete data", quote_claim: "on a grid",
                            location_candidate: "abstract", location_claim: "abstract", text_level: "abstract",
                            general_form: false},
                           {element: "e1", quote_candidate: "maximum likelihood estimates from incomplete data",
                            quote_claim: "We estimate the mixing distribution", location_candidate: "abstract",
                            location_claim: "abstract", text_level: "abstract", general_form: false}],
                attribution_hint: "unknown", note: "Field not in OpenAlex; 1977. Text: abstract only cached (oa-W2). 3 pre-filled quote pairs."}],
  notes: "Read the abstract of c1 first."};
const BARE_NOTES = "Nothing is pre-filled for this item. Read the source work, then write the claim text, the skeleton " +
  "and the claim chart yourself. For each candidate, add the quote pairs your labels rest on, and add any earlier work you find.";
const BARE = {id: "cf-0002", prefill: {status: "none", by: null, date: null, fields: []},
              claim: Object.assign(clone(PACKET.claim), {text: "", skeleton: "", coined_names: [], chart: []}),
              candidates: [Object.assign(clone(PACKET.candidates[0]), {evidence: [], attribution_hint: "unknown",
                note: "Field not in OpenAlex; 1977. Text: abstract only cached (oa-W2). No pre-filled quote pairs."})],
              notes: BARE_NOTES};
const RELATIONS = ["equivalent", "specialization", "generalization", "partial", "similar_mechanism", "different"];
const BATCH = {format: "coelacanth-batch/v1", batch: "b00", created: "2026-10-07", labels: RELATIONS, items: [PACKET]};
// b00's crossover copy for a1 in which cf-0001 is bare (annotation.crossover)
const BARE_COPY = {format: "coelacanth-batch/v1", batch: "b00", annotator: "a1", created: "2026-10-07", labels: RELATIONS,
                   items: [Object.assign(clone(BARE), {id: "cf-0001"})]};
const COPY = {format: "coelacanth-batch/v1", batch: "b00", annotator: "a1", created: "2026-10-07", labels: RELATIONS,
              items: [PACKET, BARE]};
const META_ITEM = {seed: "p01", found_by: {c1: ["mining:kulikowski"]}, exposure: {kind: "benchmark_import"},
                   origin: {kind: "human", system: null, mode: null}, negative: null,
                   provenance: {source: "kulikowski", mined_by: "kulikowski_package", license: "CC-BY-4.0"}, exclusions: []};
const ADDED_WORK = {doi: "10.1214/mix.1995", title: "Mixture models: theory, geometry and applications", year: 1995,
                    first_author: "Bruce G. Lindsay", field: "Statistics"};

// ---- 1. the core ----
const Core = vm.runInNewContext(`(function () { "use strict";\n${coreSource}\nreturn Core; })()`, {});

/** A done item in format v2 built by hand: chart e1, e2 and an added e7, c1 with an added quote pair, a1 added. */
function handLabels() {
  return {format: Core.LABELS_FORMAT, batch: "b00", annotator: "a1", exported_at: "2026-10-07T10:00:00Z",
    items: [{id: "cf-0001", minutes: 12, text_edited: false, status: "done", notes: "", prefill_status: "draft",
      claim: {text: PACKET.claim.text, skeleton: PACKET.claim.skeleton,
              chart: [{id: "e1", text: "unknown mixing distribution", key: true},
                      {id: "e2", text: "nonparametric maximum likelihood on a grid", key: true},
                      {id: "e7", text: "finitely many support points", key: true}]},
      candidates: [{cid: "c1", relation: "similar_mechanism", covered_elements: ["e1"], analogous_elements: ["e2", "e7"],
                    restricted_elements: [], attribution: "unknown", cross_field: true, confidence: 2,
                    rejected_evidence: [1], note: "",
                    added_evidence: [{element: "e7", quote_candidate: "maximum likelihood estimates",
                                      quote_claim: "on a grid", location_candidate: "abstract", location_claim: "abstract",
                                      text_level: "abstract", general_form: false}]}],
      added_candidates: [{cid: "a1", work: clone(ADDED_WORK), relation: "partial", covered_elements: ["e1", "e2"],
                          analogous_elements: [], restricted_elements: [], attribution: "not_cited", cross_field: true,
                          confidence: 3, note: "",
                          evidence: [{element: "e1", quote_candidate: "a mixing distribution", quote_claim: "mixing distribution",
                                      location_candidate: "section 1", location_claim: "abstract", text_level: "full_text",
                                      general_form: false},
                                     {element: "e2", quote_candidate: "maximum likelihood estimate", quote_claim: "maximum likelihood",
                                      location_candidate: "section 2", location_claim: "abstract", text_level: "full_text",
                                      general_form: false}]}],
      verdict: "partial"}]};
}

test("core: a v1 file still passes, and may not add anything", () => {
  const v1 = handLabels();
  v1.format = "coelacanth-labels/v1";
  const item = v1.items[0];
  item.claim.chart = item.claim.chart.slice(0, 2).concat([{id: "e3", text: "dosing", key: false}]);
  item.candidates[0].analogous_elements = ["e2"];          // e7 is not in a v1 chart
  delete item.added_candidates;
  delete item.candidates[0].added_evidence;
  item.verdict = "similar_mechanism";
  eq(Core.labelsProblems(BATCH, v1), []);
  item.added_candidates = [];
  item.candidates[0].added_evidence = [];
  item.claim.chart[2].id = "e7";
  const out = Core.labelsProblems(BATCH, v1).join("\n");
  for (const needle of ["added_evidence needs labels format", "added_candidates needs labels format", "claim.chart ids"]) {
    assert.ok(out.includes(needle), needle);
  }
});

test("core: a v2 item with chart edits, added evidence and an added candidate passes", () => {
  eq(Core.labelsProblems(BATCH, handLabels()), []);
});

test("core: the v2 rules", () => {
  const cases = [
    [l => { l.items[0].claim.chart[2].id = "e4"; }, "are neither packet elements nor new ids (added elements take e7 onwards)"],
    [l => { l.items[0].candidates[0].rejected_evidence = []; }, "pre-filled evidence #2 is for e3, which is not in the chart (reject it)"],
    [l => { l.items[0].candidates[0].added_evidence[0].text_level = "full_text"; }, "text_level must be 'abstract' when a quote comes from an abstract-only text"],
    [l => { l.items[0].candidates[0].added_evidence[0].quote_claim = "w ".repeat(41); }, "quote_claim has 41 words; the limit is 40"],
    [l => { l.items[0].added_candidates[0].work.doi = "mix"; }, "doi 'mix' does not look like a DOI (10.xxxx/...)"],
    [l => { l.items[0].added_candidates[0].work.year = 2014; }, "year 2014 is after the claim date 2013-03-26 (rule 6)"],
    [l => { l.items[0].added_candidates[0].work = {openalex: "W2", title: "EM", year: 1977, first_author: "Dempster", field: "Statistics"}; },
     "candidate a1: the same work as candidate c1"],
    [l => { l.items[0].added_candidates[0].covered_elements = ["e1"]; }, "candidate a1 (partial): needs at least two covered key elements"],
    [l => { l.items[0].verdict = "similar_mechanism"; }, "differs from the derived verdict 'partial'"],
  ];
  for (const [change, needle] of cases) {
    const labels = handLabels();
    change(labels);
    const out = Core.labelsProblems(BATCH, labels);
    assert.ok(out.some(x => x.includes(needle)), `${needle}\n${out.join("\n")}`);
  }
});

test("core: a similar_mechanism candidate needs a kept quote pair per covered key element (SCHEMA.md rule 2)", () => {
  const item = handLabels().items[0];
  eq(Core.loaderProblems(PACKET, item), []);
  item.candidates[0].rejected_evidence = [1, 2];           // the pre-filled pair for e1 rejected too
  eq(Core.loaderProblems(PACKET, item),
     ["rule 2: candidate c1 (similar_mechanism): covered key elements without evidence: ['e1']"]);
});

test("core: a similar_mechanism candidate needs an analogous key element (LABELING.md clarification 1)", () => {
  const labels = handLabels();
  const item = labels.items[0];
  item.added_candidates = [];
  item.verdict = "similar_mechanism";
  eq(Core.labelsProblems(BATCH, labels), []);
  item.claim.chart[1].key = false;                         // e2 and e7, the analogous elements, not key: e1 covered
  item.claim.chart[2].key = false;                         // is every key element, so the pair would be equivalent
  eq(Core.labelsProblems(BATCH, labels), ["cf-0001: candidate c1 (similar_mechanism): no analogous key element"]);
});

test("core: export and import round trip keeps chart edits, added evidence and candidates", () => {
  const labels = handLabels();
  const st = Core.stateFromLabelItem(PACKET, labels.items[0]);
  eq(st.order, ["e1", "e2", "e7"]);
  const again = Core.exportItem(PACKET, st);
  eq(again, labels.items[0]);
});

// ---- 2. the page in a fake DOM ----
class FakeNode {}
class FakeText extends FakeNode {
  constructor(data) { super(); this.data = String(data); this.parentNode = null; }
  get textContent() { return this.data; }
}
class FakeElement extends FakeNode {
  constructor(tag) {
    super();
    Object.assign(this, {tagName: tag.toUpperCase(), children: [], parentNode: null, attrs: {}, listeners: {}, dataset: {},
                         style: {}, className: "", value: "", checked: false, disabled: false, indeterminate: false,
                         scrollTop: 0, id: ""});
  }
  get classList() {
    const names = () => this.className.split(/\s+/).filter(Boolean);
    return {
      add: c => { if (!names().includes(c)) this.className = [...names(), c].join(" "); },
      remove: c => { this.className = names().filter(x => x !== c).join(" "); },
      contains: c => names().includes(c),
      toggle: (c, force) => {
        const on = force === undefined ? !names().includes(c) : !!force;
        this.className = (on ? [...names().filter(x => x !== c), c] : names().filter(x => x !== c)).join(" ");
        return on;
      },
    };
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === "id" || k === "type") this[k] = String(v); // reflected as properties, as in a browser
    if (k === "disabled" || k === "selected") this[k] = true; // boolean attributes set their property, as in a browser
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type) { for (const fn of this.listeners[type] || []) fn({type, target: this, preventDefault() {}}); }
  append(...kids) {
    for (const kid of kids) {
      const node = kid instanceof FakeNode ? kid : new FakeText(kid);
      if (node.parentNode) node.parentNode.children = node.parentNode.children.filter(x => x !== node);
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...kids) { for (const c of this.children) c.parentNode = null; this.children = []; this.append(...kids); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(x => x !== this); this.parentNode = null; }
  get textContent() { return this.children.map(c => c.textContent).join(""); }
  set textContent(t) { this.replaceChildren(String(t)); }
  focus() { focused = this; }
  scrollIntoView() {}
  click() { this.dispatch("click"); }
}
function* walk(el) { yield el; for (const c of el.children) if (c instanceof FakeElement) yield* walk(c); }
let focused = null; // the element the page focused last

function loadPage(store = new Map()) {
  focused = null;
  const docListeners = {};
  const root = new FakeElement("html");
  const body = new FakeElement("body");
  root.append(body);
  for (const [tag, id] of [["span", "batchName"], ["span", "progress"], ["span", "saveStatus"], ["span", "annotatorBox"],
                           ["button", "btnLoad"], ["button", "btnImport"], ["button", "btnExport"], ["button", "btnTheme"],
                           ["input", "importFile"], ["nav", "sidebar"], ["main", "main"]]) {
    const el = new FakeElement(tag);
    el.setAttribute("id", id);
    body.append(el);
  }
  const document = {
    documentElement: root, body, visibilityState: "visible", hasFocus: () => true,
    createElement: tag => new FakeElement(tag),
    querySelector: sel => [...walk(root)].find(el => sel.startsWith("#") && el.id === sel.slice(1)) || null,
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  };
  const confirms = [];
  const window = {
    localStorage: {getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v))},
    location: {protocol: "file:", search: ""},
    confirm: message => { confirms.push(message); return true; },
    addEventListener() {},
  };
  const context = vm.createContext({window, document, Node: FakeNode, URLSearchParams, performance, console,
                                    setInterval: () => 0, setTimeout: () => 0, clearTimeout() {}});
  vm.runInContext(script, context);
  const all = pred => [...walk(root)].filter(pred);
  const one = (pred, what) => {
    const found = all(pred);
    assert.equal(found.length, 1, `expected one ${what}, found ${found.length}`);
    return found[0];
  };
  const page = {
    window, confirms, store,
    focused: () => focused,
    /** A key pressed while `target` has focus, as the browser delivers it to the page's document listener. */
    key(target, key) {
      const event = {key, target, metaKey: false, ctrlKey: false, altKey: false, defaultPrevented: false,
                     preventDefault() { event.defaultPrevented = true; }};
      for (const fn of docListeners.keydown || []) fn(event);
      return event;
    },
    label: name => one(el => el.getAttribute("aria-label") === name, `control labelled ${JSON.stringify(name)}`),
    button: text => one(el => el.tagName === "BUTTON" && el.textContent === text, `button ${JSON.stringify(text)}`),
    type(name, value) { const el = page.label(name); el.value = value; el.dispatch("input"); },
    choose(name, value) { const el = page.label(name); el.value = value; el.dispatch("change"); },
    tick(name, on = true) { const el = page.label(name); el.checked = on; el.dispatch("change"); },
    click(nameOrText) {
      const byLabel = all(el => el.getAttribute("aria-label") === nameOrText);
      (byLabel.length ? one(el => el.getAttribute("aria-label") === nameOrText, nameOrText) : page.button(nameOrText)).dispatch("click");
    },
    text: () => root.textContent,
    draftTitles: () => [...new Set(all(el => el.className.split(/\s+/).includes("draft")).map(el => el.getAttribute("title")))],
  };
  return page;
}

/** Label c1 and add a1 on the pre-filled item through the page's controls. */
function labelPrefilled(page) {
  page.click("remove element e3");                       // c1 marks nothing on e3: no question asked
  page.click("Add element");
  page.type("e7 text", "finitely many support points");
  for (const id of ["e1", "e2", "e7"]) page.tick(`${id} is a key element`);
  page.tick("c1 relation similar_mechanism");
  page.tick("c1 e1 covered");                            // every key element gets a counterpart (clarification 1)
  page.tick("c1 e2 analogous");
  page.tick("c1 e7 analogous");
  page.tick("c1 cross-field: yes, a different field");
  page.tick("c1 attribution: unknown");
  page.tick("c1 confidence: 2");
  page.click("add a quote pair to c1");
  page.choose("c1 quote pair 1: chart element", "e7");
  page.type("c1 quote pair 1: quote from the candidate", "maximum likelihood estimates");
  page.type("c1 quote pair 1: location in the candidate", "abstract");
  page.type("c1 quote pair 1: quote from the claim source", "on a grid");
  page.type("c1 quote pair 1: location in the claim source", "abstract");
  page.tick("c1 quote pair 1: abstract only");
  page.click("Add a candidate you found");
  page.choose("a1 identifier type", "doi");
  page.type("a1 identifier", "https://doi.org/10.1214/MIX.1995");
  page.type("a1 title", ADDED_WORK.title);
  page.type("a1 year", "1995");
  page.type("a1 first author", ADDED_WORK.first_author);
  page.type("a1 field", ADDED_WORK.field);
  page.tick("a1 relation partial");
  page.tick("a1 e1 covered");
  page.tick("a1 e2 covered");
  page.tick("a1 cross-field: yes, a different field");
  page.tick("a1 attribution: not cited");
  page.tick("a1 confidence: 3 certain");
  for (const [n, element, quote] of [[1, "e1", "a mixing distribution"], [2, "e2", "maximum likelihood estimate"]]) {
    page.click("add a quote pair to a1");
    const pair = `a1 quote pair ${n}`;
    page.choose(`${pair}: chart element`, element);
    page.type(`${pair}: quote from the candidate`, quote);
    page.type(`${pair}: location in the candidate`, `section ${n}`);
    page.type(`${pair}: quote from the claim source`, element === "e1" ? "mixing distribution" : "maximum likelihood");
    page.type(`${pair}: location in the claim source`, "abstract");
    page.tick(`${pair}: full text`);
  }
  page.type("Minutes on this item", "12");
}

const exports = {};

test("page: a pre-filled item labelled through the controls exports labels that pass the rules", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  labelPrefilled(page);
  page.click("Mark done");
  const labels = page.window.coelacanthExport();
  assert.equal(labels.format, "coelacanth-labels/v2");
  const [item] = labels.items;
  assert.equal(item.status, "done");
  eq(item.claim.chart.map(e => [e.id, e.key]), [["e1", true], ["e2", true], ["e7", true]]);
  eq(item.candidates[0].rejected_evidence, [1], "the quote pair of the removed element is rejected");
  eq(item.candidates[0].added_evidence.map(e => [e.element, e.text_level]), [["e7", "abstract"]]);
  const [a1] = item.added_candidates;
  eq(a1.work, Object.assign({}, ADDED_WORK, {doi: "https://doi.org/10.1214/MIX.1995"}), "the work as typed");
  eq([a1.cid, a1.relation, a1.covered_elements, a1.evidence.length], ["a1", "partial", ["e1", "e2"], 2]);
  assert.equal(item.verdict, "partial");
  eq(Core.labelsProblems(BATCH, labels), []);
  assert.ok(page.text().includes("No problems."), "the live warnings agree once done");
  exports.prefilled = labels;
});

test("page: the draft badges name who pre-filled the item", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  eq(page.draftTitles(), ["Pre-filled by claude-opus-5-5. A draft: check it against the source."]);
  const contrast = clone(BATCH);
  contrast.items[0].prefill.by = "annotator:a2";           // a contrast item another annotator wrote
  const other = loadPage();
  other.window.coelacanthLoadBatch(contrast, "a1");
  eq(other.draftTitles(), ["Pre-filled by annotator:a2. A draft: check it against the source."]);
  assert.ok(other.text().includes("Pre-filled by annotator:a2 on 2026-10-06"), "the visible line agrees");
  assert.ok(!other.text().includes("Claude"), "nothing on the item says Claude wrote it");
});

test("page: live warnings, removing and restoring elements, removing an added candidate", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.click("Add a candidate you found");
  assert.ok(page.text().includes("candidate a1: work needs one of the identifiers openalex, doi, arxiv, pmid"));
  page.type("a1 identifier", "not-a-doi");
  assert.ok(page.text().includes("This does not look like a DOI (10.xxxx/...)."));
  page.tick("c1 e3 covered");
  page.click("remove element e3");
  assert.equal(page.confirms.length, 1, "removing a marked element asks first");
  assert.ok(page.text().includes("Removed from the pre-fill: e3"));
  page.click("restore element e3");
  let labels = page.window.coelacanthExport();
  eq(labels.items[0].claim.chart.map(e => e.id), ["e1", "e2", "e3"], "restored in its packet position");
  eq(labels.items[0].candidates[0].covered_elements, [], "its marks went with it");
  eq(labels.items[0].candidates[0].rejected_evidence, [1], "its quote pair stays rejected");
  page.click("remove candidate a1");
  labels = page.window.coelacanthExport();
  eq(labels.items[0].added_candidates, []);
  for (let i = 0; i < 3; i++) page.click("Add element");
  assert.equal(page.button("Add element").disabled, true, "six elements at most");
  eq(page.window.coelacanthExport().items[0].claim.chart.map(e => e.id), ["e1", "e2", "e3", "e7", "e8", "e9"]);
});

test("page: a bare item is written from empty, and a copy refuses the other annotator", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(COPY), "a1");
  page.click("Next");
  assert.ok(page.text().includes("Nothing is pre-filled for this item"));
  page.type("Claim text", "Estimate how model parameters are distributed in a population without assuming a parametric shape.");
  page.type("Skeleton", "Fit an unknown mixing distribution by maximum likelihood over candidate support points.");
  page.type("Coined names of the source work", "NPAG, nonparametric adaptive grid");
  for (const [id, text] of [["e1", "unknown mixing distribution"], ["e2", "maximum likelihood over a grid"]]) {
    page.click("Add element");
    page.type(`${id} text`, text);
    page.tick(`${id} is a key element`);
  }
  page.tick("c1 relation different");
  page.tick("c1 cross-field: no, same field");
  page.tick("c1 attribution: unknown");
  page.tick("c1 confidence: 2");
  page.click("Add a candidate you found");
  page.type("a1 identifier", "10.1214/mix.1995");
  page.type("a1 title", ADDED_WORK.title);
  page.type("a1 year", "1995");
  page.type("a1 first author", ADDED_WORK.first_author);
  page.type("a1 field", ADDED_WORK.field);
  page.tick("a1 relation equivalent");
  page.tick("a1 e1 covered");
  page.tick("a1 e2 covered");
  page.tick("a1 cross-field: yes, a different field");
  page.tick("a1 attribution: not cited");
  page.tick("a1 confidence: 3 certain");
  for (const [n, element, quote, claimQuote] of [[1, "e1", "a mixing distribution", "mixing distribution"],
                                                 [2, "e2", "maximum likelihood estimate", "maximum likelihood"]]) {
    page.click("add a quote pair to a1");
    page.choose(`a1 quote pair ${n}: chart element`, element);
    page.type(`a1 quote pair ${n}: quote from the candidate`, quote);
    page.type(`a1 quote pair ${n}: location in the candidate`, "section 2");
    page.type(`a1 quote pair ${n}: quote from the claim source`, claimQuote);
    page.type(`a1 quote pair ${n}: location in the claim source`, "abstract");
    page.tick(`a1 quote pair ${n}: full text`);
  }
  page.type("Minutes on this item", "20");
  page.click("Mark done");
  const labels = page.window.coelacanthExport();
  const item = labels.items[1];
  eq(item.claim.coined_names, ["NPAG", "nonparametric adaptive grid"]);
  eq(item.claim.chart.map(e => e.id), ["e1", "e2"], "a bare packet's elements start at e1");
  assert.equal(item.text_edited, true);
  assert.equal(item.verdict, "equivalent");
  eq(Core.labelsProblems(COPY, labels), []);
  exports.bare = labels;
  const other = page.window.coelacanthImport(Object.assign(clone(labels), {annotator: "a2"}));
  assert.equal(other.annotator, "a2");
  assert.ok(page.text().includes("is for annotator a1, and you are a2"));
  assert.ok(Core.labelsProblems(COPY, page.window.coelacanthExport()).some(x => x.includes("copy for annotator a1")));
});

test("page: an exported file imports back to the same labels", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.window.coelacanthImport(clone(exports.prefilled));
  const again = page.window.coelacanthExport();
  eq(again.items, exports.prefilled.items);
});

const STORE_LABELS = name => `coelacanth.annotate.labels.${name}.a1`;

test("page: work on a pre-filled batch never fills its bare copy in this browser", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.type("Claim text", "An edited pre-filled claim text.");
  page.tick("e1 is a key element");
  page.window.coelacanthLoadBatch(clone(BARE_COPY), "a1");     // saves b00, then opens the copy b00-a1
  const item = page.window.coelacanthExport().items[0];
  eq([item.claim.text, item.claim.skeleton, item.claim.chart, item.prefill_status], ["", "", [], "none"]);
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");         // saves b00-a1, then opens b00 again
  assert.ok(page.store.has(STORE_LABELS("b00")) && page.store.has(STORE_LABELS("b00-a1")), "one key per copy");
  assert.equal(page.window.coelacanthExport().items[0].claim.text, "An edited pre-filled claim text.", "kept apart");

  // work saved under the copy's key from the pre-filled version (as before copies had their own keys)
  const saved = page.store.get(STORE_LABELS("b00"));
  for (const [state, why] of [[saved, "the labels were made on a pre-filled copy, and this copy has the item bare."],
                              [JSON.stringify(Object.assign(JSON.parse(saved), {items: JSON.parse(saved).items.map(i => {
                                delete i.prefill_status; return i; })})),
                               "the labels do not say which copy they were made on, and this copy has the item bare."]]) {
    const other = loadPage(new Map([[STORE_LABELS("b00-a1"), state]]));
    other.window.coelacanthLoadBatch(clone(BARE_COPY), "a1");
    const restored = other.window.coelacanthExport().items[0];
    eq([restored.claim.text, restored.claim.chart, restored.text_edited], ["", [], false]);
    assert.ok(other.text().includes(`Not restored from this browser: cf-0001: ${why}`), why);
    eq(Core.labelsProblems(BARE_COPY, other.window.coelacanthExport()), []);
    other.click("Dismiss");
    assert.ok(!other.text().includes("Not restored"), "dismissed");
  }
});

test("page: only formats older than bare packets may omit the copy, and only on pre-filled items", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.type("Claim text", "Edited before bare copies existed.");
  page.window.coelacanthLoadBatch(clone(BARE_COPY), "a1");
  const state = JSON.parse(page.store.get(STORE_LABELS("b00")));
  for (const i of state.items) delete i.prefill_status;
  for (const [format, restored] of [["coelacanth-annotate-state/v1", true], [Core.STATE_FORMAT, false]]) {
    const other = loadPage(new Map([[STORE_LABELS("b00"), JSON.stringify(Object.assign(clone(state), {format}))]]));
    other.window.coelacanthLoadBatch(clone(BATCH), "a1");
    assert.equal(other.window.coelacanthExport().items[0].claim.text === "Edited before bare copies existed.", restored, format);
    assert.equal(other.text().includes("cf-0001: the labels do not say which copy they were made on."), !restored, format);
  }
  const v1 = handLabels();
  v1.format = "coelacanth-labels/v1";
  delete v1.items[0].prefill_status;
  for (const [batch, imported] of [[BATCH, 1], [BARE_COPY, 0]]) {
    const other = loadPage();
    other.window.coelacanthLoadBatch(clone(batch), "a1");
    assert.equal(other.window.coelacanthImport(clone(v1)).imported, imported, batch.annotator || "b00");
  }
});

test("page: an imported labels file of the other copy is not loaded, and check-labels would refuse it", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BARE_COPY), "a1");
  page.type("Claim text", "My own claim text.");
  const result = page.window.coelacanthImport(clone(exports.prefilled)); // made on b00, where cf-0001 is pre-filled
  eq([result.imported, result.not_imported], [0, 1]);
  const item = page.window.coelacanthExport().items[0];
  eq([item.claim.text, item.claim.chart, item.prefill_status], ["My own claim text.", [], "none"], "what the copy held");
  assert.ok(page.text().includes("Not imported: cf-0001: the labels were made on a pre-filled copy, and this copy has the item bare."));
  assert.ok(Core.labelsProblems(BARE_COPY, exports.prefilled).includes(
    "cf-0001: prefill_status 'draft' but the packet is bare (labels made on another copy of the batch)"));
});

test("page: shortcuts stay off while a select has focus, and work on radio buttons and checkboxes", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.click("add a quote pair to c1");
  const select = page.label("c1 quote pair 1: chart element");
  assert.equal(page.focused(), select, "the new quote pair's element select has focus");
  for (const key of ["e", "2", "j", "]"]) assert.equal(page.key(select, key).defaultPrevented, false, key);
  assert.equal(page.window.coelacanthExport().items[0].candidates[0].relation, null, "typing e2 chose no relation");
  assert.equal(page.key(page.label("c1 relation different"), "2").defaultPrevented, true);
  assert.equal(page.window.coelacanthExport().items[0].candidates[0].relation, "specialization");
  page.key(page.label("c1 e1 covered"), "6");
  assert.equal(page.window.coelacanthExport().items[0].candidates[0].relation, "different");
});

test("page: after a removal, focus goes to the next row or to the add button", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.click("remove element e2");
  assert.equal(page.focused(), page.label("e3 text"));
  page.click("remove element e3");
  assert.equal(page.focused(), page.button("Add element"));
  page.click("add a quote pair to c1");
  page.click("add a quote pair to c1");
  page.click("remove c1 quote pair 1");
  assert.equal(page.focused(), page.label("c1 quote pair 1: chart element"), "the pair that moved up");
  page.click("remove c1 quote pair 1");
  assert.equal(page.focused(), page.label("add a quote pair to c1"));
  page.click("Add a candidate you found");
  page.click("remove candidate a1");
  assert.equal(page.focused(), page.button("Add a candidate you found"));
});

test("page: restore is disabled at six elements, and element ids are never given twice", () => {
  const page = loadPage();
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");
  page.click("remove element e3");
  for (let i = 0; i < 4; i++) page.click("Add element");
  assert.equal(page.label("restore element e3").disabled, true);
  assert.ok(page.text().includes("Removed from the pre-fill: e3 restore. Six elements at most: remove one first."));
  page.click("remove element e10");
  assert.equal(page.label("restore element e3").disabled, false);
  page.click("Add element");
  eq(page.window.coelacanthExport().items[0].claim.chart.map(e => e.id), ["e1", "e2", "e7", "e8", "e9", "e11"]);

  page.window.coelacanthLoadBatch(clone(BARE_COPY), "a1");
  assert.ok(!page.text().includes("pre-fill from OpenAlex references"), "nothing pre-filled on a bare item");
  for (let i = 0; i < 3; i++) page.click("Add element");
  page.click("remove element e3");
  page.click("Add element");
  page.click("remove element e4");
  eq(page.window.coelacanthExport().items[0].claim.chart.map(e => e.id), ["e1", "e2"]);
  page.window.coelacanthLoadBatch(clone(BATCH), "a1");         // saved, then restored from this browser
  page.window.coelacanthLoadBatch(clone(BARE_COPY), "a1");
  page.click("Add element");
  eq(page.window.coelacanthExport().items[0].claim.chart.map(e => e.id), ["e1", "e2", "e5"]);
});

test("page: quotes against a text nobody could cache are flagged before check-labels rejects them", () => {
  const packet = clone(PACKET);
  packet.candidates[0].work.cache_key = null;
  packet.claim.source_work.cache_key = null;
  const page = loadPage();
  page.window.coelacanthLoadBatch(Object.assign(clone(BATCH), {items: [packet]}), "a1");
  assert.ok(page.text().includes("No text of this candidate or of the claim source is cached, so check-labels cannot verify quotes from either."));
  page.click("add a quote pair to c1");
  page.type("c1 quote pair 1: quote from the candidate", "computing maximum likelihood estimates");
  page.type("c1 quote pair 1: quote from the claim source", "on a grid");
  const lines = Core.loaderProblems(packet, page.window.coelacanthExport().items[0]);
  for (const needle of ["rule 4: candidate c1: no text of this candidate is cached, so check-labels cannot verify the candidate quotes of your 1 quote pair",
                        "rule 4: no text of the claim source is cached, so check-labels cannot verify the claim quotes of your 1 quote pair"]) {
    assert.ok(lines.some(x => x.startsWith(needle)), `${needle}\n${lines.join("\n")}`);
    assert.ok(page.text().includes(needle), "shown under the item's warnings");
  }
});

// ---- 3. parity with Python ----
function python() {
  const candidates = [process.env.PYTHON, path.join(homedir(), ".venvs", "rediscovery-layer", "bin", "python")].filter(Boolean);
  return candidates.find(p => existsSync(p)) || null;
}
const PY = python();
function runPython(args) {
  const out = spawnSync(PY, args, {cwd: REPO, encoding: "utf8", env: Object.assign({}, process.env, {PYTHONPATH: path.join(REPO, "src")})});
  return {code: out.status, stdout: out.stdout || "", stderr: out.stderr || ""};
}

test("python: check-labels --offline passes on both exports", () => {
  if (!PY) return "skipped: no Python with the project's packages (set PYTHON)";
  const dir = mkdtempSync(path.join(tmpdir(), "annotate-test-"));
  try {
    for (const [batch, labels, name] of [[BATCH, exports.prefilled, "b00"], [COPY, exports.bare, "b00"]]) {
      writeFileSync(path.join(dir, `${name}.json`), JSON.stringify(batch));
      writeFileSync(path.join(dir, `${name}-meta.json`), JSON.stringify({format: "coelacanth-batch-meta/v1", batch: "b00",
        items: {"cf-0001": META_ITEM, "cf-0002": META_ITEM}}));
      writeFileSync(path.join(dir, "labels.json"), JSON.stringify(labels));
      const out = runPython(["-m", "coelacanth.benchmark.annotation", "check-labels", "--offline",
                             path.join(dir, `${name}.json`), path.join(dir, "labels.json")]);
      assert.equal(out.code, 0, out.stdout + out.stderr);
      assert.match(out.stdout, /1 done items, 0 problems|2 done items, 0 problems/);
      assert.match(out.stdout, /note: offline: 1 added works not resolved/);
      if (batch === COPY) assert.match(out.stdout, /claim written by a1 on a bare packet/);
    }
  } finally {
    rmSync(dir, {recursive: true, force: true});
  }
});

test("python: labels_problems and the core report the same messages for broken labels", () => {
  if (!PY) return "skipped: no Python with the project's packages (set PYTHON)";
  const mutations = [
    l => { l.items[0].claim.chart[2].id = "e4"; },
    l => { l.items[0].claim.chart.push({id: "e7", text: "", key: "yes"}); },
    l => { l.items[0].candidates[0].rejected_evidence = []; },
    l => { l.items[0].candidates[0].covered_elements = []; },
    l => { Object.assign(l.items[0].candidates[0].added_evidence[0], {element: "e9", quote_candidate: "", text_level: "full_text", general_form: null}); },
    l => { l.items[0].candidates[0].added_evidence[0].quote_claim = "w ".repeat(41); },
    l => { Object.assign(l.items[0].added_candidates[0].work, {doi: "mix", title: "", year: "1995", first_author: null}); },
    l => { l.items[0].added_candidates[0].work = {pmid: "pmid:x", arxiv: "hep-th/9901001v2", title: "T", year: 2014, first_author: "A", field: "F"}; },
    l => { l.items[0].added_candidates.push(Object.assign(clone(l.items[0].added_candidates[0]), {cid: "b2"})); },
    l => { Object.assign(l.items[0].added_candidates[0], {relation: "specialization", confidence: 9, cross_field: "yes", evidence: "none"}); },
    l => { l.items[0].added_candidates = [7]; },
    l => { l.items[0].claim.coined_names = ["X"]; l.items[0].verdict = "equivalent"; },
    l => { l.format = "coelacanth-labels/v1"; l.annotator = "a 1"; },
    l => { l.items[0].prefill_status = "none"; },
    l => { delete l.items[0].prefill_status; },
    l => { l.items[0].claim.chart[1].key = false; l.items[0].claim.chart[2].key = false; },
    l => { l.items[0].status = "skipped"; l.items[0].added_candidates[0].relation = null; },
  ];
  const cases = mutations.map(change => { const l = handLabels(); change(l); return l; });
  const dir = mkdtempSync(path.join(tmpdir(), "annotate-test-"));
  try {
    writeFileSync(path.join(dir, "batch.json"), JSON.stringify(COPY));
    writeFileSync(path.join(dir, "cases.json"), JSON.stringify(cases));
    const out = runPython(["-c", "import json, sys\nfrom coelacanth.benchmark.annotation import labels_problems\n" +
      "batch = json.load(open(sys.argv[1]))\nprint(json.dumps([labels_problems(batch, c) for c in json.load(open(sys.argv[2]))]))",
      path.join(dir, "batch.json"), path.join(dir, "cases.json")]);
    assert.equal(out.code, 0, out.stderr);
    const fromPython = JSON.parse(out.stdout);
    cases.forEach((labels, n) => {
      const fromCore = Core.labelsProblems(COPY, labels);
      eq(fromCore, fromPython[n], `case ${n + 1}`);
      if (n < cases.length - 1) assert.ok(fromCore.length > 0, `case ${n + 1} should have problems`);
    });
  } finally {
    rmSync(dir, {recursive: true, force: true});
  }
});

for (const [name, fn] of tests) {
  try {
    const note = fn();
    passed += 1;
    console.log(`ok   ${name}${note ? ` (${note})` : ""}`);
  } catch (err) {
    console.log(`FAIL ${name}\n${err.stack}`);
    process.exitCode = 1;
  }
}
console.log(`${passed} of ${tests.length} tests passed`);
