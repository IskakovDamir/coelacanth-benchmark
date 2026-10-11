# Benchmark schema (v0)

Status: draft for benchmark v0 (ECIR 2027 resource paper), written 2026-09-24 and revised the same day after Damir's review (change log at the end). Field names are stable once the first item is labelled.

## Units

- **Item**: one claim taken from a "new" source work, plus the earlier works that annotators and pooled systems surfaced, each judged with evidence. A system under test sees only `claim.text` and `claim.date`, never `claim.skeleton`.
- **Pair**: the claim and one earlier candidate work. Each pair gets one relation label.
- **Item verdict**: derived from the pair labels by the precedence rule below.

Cross-field status is decided by a person: each gold candidate records whether it comes from a different field than the claim (`candidates[].cross_field`) and who confirmed it (`candidates[].field_confirmed_by`). OpenAlex fields (topic taxonomy: domain > field > subfield > topic) are only a suggestion; they are unreliable for old papers (Fisher 1937 is filed under Arts and Humanities). Same-field items are allowed as controls and are flagged.

**Field-native claims.** `claim.text` states the claim the way the source work states it, in the source field's own vocabulary. Annotators remove author names, titles, years, venues and the coined method name, and nothing else: they do not neutralise or abstract the wording, because translating across vocabularies is exactly what the benchmark measures. The neutral, mechanism-level version goes to `claim.skeleton`, which systems never see; it serves labelling and the vocabulary-barrier ablation (eval/protocol.md).

## Labels

### Pair-level relation: how the new claim relates to an earlier candidate

The four AViD types keep AViD's direction: the label describes the new claim relative to the earlier work. AViD's judge prompt (arXiv 2608.14669; github.com/ayrtonporto/avid-journal, `src/novelty/llm_judge.py`, MIT, commit 6379383) defines them as: "up to renaming and routine reformulation, both statements assert the same mathematical fact under the same hypotheses" (equivalent); "statement A logically implies statement B and is strictly stronger (weaker hypotheses or stronger conclusion)" (generalization, A being the new statement); "statement A is a strict particular case of statement B" (specialization). We extend the four types to non-mathematical claims and add two.

| Label | Definition (claim C, candidate P) | AViD type | "Already done"? |
|---|---|---|---|
| `equivalent` | Up to renaming, notation and routine reformulation, P states the same mechanism or result as C under the same hypotheses. Every key element of C's claim chart is covered by P with the same meaning. | equivalent | yes |
| `specialization` | C is a strict particular case of P: restricting P (fixing a parameter, adding a hypothesis) yields C with the same meaning. The restricted elements are recorded with evidence quoting P's general form. | specialization | yes |
| `generalization` | C implies P and is strictly stronger (weaker hypotheses or a stronger conclusion); P is a special case of C, obtained from C by fixing a parameter or adding a hypothesis; a P that lacks an element of C without such a restriction (a step, or a property C proves) is a fragment of C, `partial` or lower (LABELING.md clarification 8). | generalization | no: P is prior work that C extends |
| `partial` | P covers at least two key elements of C with the same meaning, and at least one key element of C is not covered. A candidate that covers only one key element is `similar_mechanism` (if the remaining key elements correspond by role) or `different`. | (none; AViD would say different or gray zone) | no |
| `similar_mechanism` | P uses the same functional mechanism or the same functional form as C, but in a different system or with a different meaning of the variables, and neither statement implies the other. | different | no |
| `different` | P does not reach `similar_mechanism`: fewer than two key elements of C covered with the same meaning, and not every key element of C has a counterpart (covered or corresponding by role); often a false friend that shares vocabulary, setting or topic. | different | no |

*With the same meaning* is the test that separates `equivalent` and `specialization` from `similar_mechanism`: a formula that can be transformed into another is not equivalent if the transformation changes what the variables stand for (worked examples in LABELING.md).

### Item-level verdict

Precedence, from most to least prior art: `equivalent` > `specialization` > `generalization` > `partial` > `similar_mechanism`. The item verdict is the highest relation among the gold candidates. If no candidate reaches `similar_mechanism`, the verdict is **`nothing_close`**.

`equivalent` and `specialization` are the "already done" verdicts. Their precision is reported separately, because a false "already done" is the most expensive error (CLAUDE.md).

Mapping from the earlier CLAUDE.md types: identity → `equivalent`; special case → `specialization`; same functional form → `similar_mechanism`, or `equivalent` when the meaning also matches; similar mechanism → `similar_mechanism`.

## Pooling

Annotators will not find every earlier work, so candidates are pooled before scoring, TREC-style: the top 10 candidates of every system under test, per item, join the annotators' own candidates, and every unjudged pooled candidate is labelled by annotators who do not see which system found it. `candidates[].found_by` records the sources; `pool` records the judged depth. A candidate outside the judged pool counts as not gold.

## File layout

- `eval/benchmark/v0/items.jsonl`: one JSON object per line, UTF-8, sorted by `id`. Created when the first item is labelled.
- `eval/benchmark/v0/README.md`: datasheet at release (sources, licenses, annotation procedure including the Claude pre-fill, agreement, known limits).
- Item IDs are never reused. Excluded items keep their ID with `status: "excluded"` and a reason.
- Released metadata comes from OpenAlex (CC0) and our own labels, released under CC-BY-4.0 (`eval/benchmark/LICENSE`). Semantic Scholar and Asta records and full texts are never shipped (docs/api-keys.md). Evidence quotes are short (at most 40 words) with their locations.

## Item fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | `cf-` plus four digits, for example `cf-0042` |
| `version` | string | yes | `v0` |
| `status` | enum | yes | `candidate`, `pooled` (system candidates added, some unjudged), `labelled`, `adjudicated`, `released`, `excluded` |
| `exclusion_reason` | string | if excluded | for example `guide_example`, `no_full_text`, `annotators_disagree` |
| `claim.text` | string | yes | Field-native statement given to systems: the claim as the source work states it, in the source field's own vocabulary, minus author names, titles, years, venues and the coined method name. One claim, at most 120 words |
| `claim.skeleton` | string | yes | Neutral, mechanism-level version written by the annotator. Never shown to systems |
| `claim.coined_names` | list | yes (may be empty) | Coined names of the source work (method name, acronym, named model) that `claim.text` must not contain; used by the rule 5 script check |
| `claim.written_by` / `claim.checked_by` | string | yes | Annotator IDs; the checker compares `claim.text` with the source's own wording |
| `claim.source_work` | work | yes | The new work the claim comes from |
| `claim.date` | date | yes | Earliest public version of the source work (preprint if any); systems must not use anything dated on or after it (eval/protocol.md, leakage) |
| `claim.field` / `claim.domain` | string | yes | OpenAlex field and domain of the source work |
| `claim.computational` | bool | yes | True if the claim states a computable method or formula; false for mechanistic, qualitative or empirical claims |
| `claim.chart` | list | yes | Two to six elements `{id, text, key}`; `key: true` marks the elements the claim cannot lose and still be the same claim |
| `candidates` | list | yes | Earlier works (may be empty for negatives) |
| `candidates[].work` | work | yes | The earlier work |
| `candidates[].found_by` | list | yes | Who surfaced it: `sys:<system>@<rank>` (pooled run, rank in that system's list, for example `sys:refpipe-v0@3`), `annotator:<id>`, `exposure`, `mining:<source>`. Hidden from annotators while they label |
| `candidates[].relation` | enum or null | yes | One of the six pair labels; `null` only while the pooled candidate is unjudged |
| `candidates[].covered_elements` | list | yes | Chart element IDs covered with the same meaning |
| `candidates[].analogous_elements` | list | for `similar_mechanism` | Chart element IDs that correspond by role only |
| `candidates[].restricted_elements` | list | for `specialization` | Chart element IDs the claim obtains by fixing a parameter or adding a hypothesis to the candidate |
| `candidates[].evidence` | list | yes, unless `different` | `{element, quote_candidate, quote_claim, location_candidate, location_claim, text_level, general_form}`; `text_level` is `full_text` or `abstract`; `general_form: true` marks a quote of the candidate's general form (required for each restricted element). An element may have several entries, each supporting part of it; it counts as covered only when its entries together support all of it (LABELING.md clarification 9; a person checks this, the loader checks only that an entry exists, rule 2) |
| `candidates[].attribution` | enum | yes | `cited`, `not_cited`, `unknown`: does the source work cite this candidate? |
| `candidates[].cross_field` | bool | for gold | True when the candidate comes from a different field than the claim, as judged by a person; OpenAlex fields are only a suggestion |
| `candidates[].field_confirmed_by` | string | for gold | Annotator ID of the person who confirmed `cross_field` |
| `candidates[].gold` | bool | yes | True when the adjudicated relation is `similar_mechanism` or stronger; retrieval metrics count gold candidates |
| `candidates[].confidence` | int | yes | 1 (unsure) to 3 (certain) |
| `verdict` | enum | yes | Derived by precedence, or `nothing_close`; the loader recomputes and checks it |
| `pool` | object | yes once pooled | `{depth, systems, n_pooled, n_unjudged}`: judged-pool depth (10 by default), the systems pooled, and counts |
| `exposure` | object | yes | `{kind, work, date}`: what exposed the relation (`letter`, `comment`, `erratum`, `withdrawal`, `review`, `catalogue`, `benchmark_import`, `our_search`). The exposing work is excluded from retrieval |
| `origin` | object | yes | `{kind: human or ai, system, mode}`; `mode` for AI items: `reproduced_learned`, `independent`, `unknown` |
| `pair_stats` | object | derived | Per gold candidate: `field_distance` (`same_subfield`, `same_field`, `same_domain`, `cross_domain`), `year_gap`, `shared_key_terms` (count of the source work's OpenAlex keywords found in the candidate's title or abstract) |
| `splits` | object | derived | `post_cutoff: {model: bool}`, `closed_book: {model: {runs, hits_cross_field, hits_any, failed}}`, `headline` (H), `headline_any` (H-any), `headline_post` (H-post), `p40`, `closed_pool` (`null` or `kulikowski`) |
| `negative` | object or null | yes | How a negative was built, null otherwise: `{kind: contrast, from_item, edited_element}` (one key element of an adjudicated positive edited by an annotator, no model in the loop; `from_item` is that positive, `edited_element` the id of the key chart element changed; such items have `provenance.source: negative` and `mined_by: manual`, LABELING.md section 5, "Contrast items") or `{kind: natural_distractor}` (Kulikowski's topical distractors, the catalogue's failed unification). Whether an item is a negative follows from its adjudicated verdict (`nothing_close`), not from this field |
| `provenance` | object | yes | `{source: kulikowski, declaration, post_cutoff, catalogue, control, negative or guide; mined_by; dataset; license; attribution}`. `mined_by` is `manual`, `openalex_query:<name>`, `catalogue:<name>`, `kulikowski_package` or `our_pipeline:<version>`; results are reported with and without `our_pipeline` items |
| `annotation` | object | yes | `{annotators: [{id, relations, verdict, minutes}], prefill: {by, fields}, batch, adjudicated_by, notes}`. Claude may pre-fill the chart, candidate evidence and quotes; humans choose every label. On a contrast item `prefill.by` is `annotator:<writer>`: the writer's claim text, skeleton and chart, recorded as `claim.written_by` |
| `exclusions` | list | yes | Work IDs systems must not retrieve: the source work, the exposing work, and later works that state the relation |

**Work object**: `openalex` (W ID), `doi`, `arxiv`, `pmid`, `title`, `first_author`, `n_authors`, `year`, `publication_date`, `venue`, `field`, `domain`, `oa_url`, `cache_key` (stem of the cached full text used for quote checks). At least one of `openalex`, `doi`, `arxiv` or `pmid` is required. An AI-origin item's source work is the generated method itself: it is identified by `ai_origin` (its ID in `data/candidates/ai-origin/methods.jsonl`), dated by its generation, and its text is cached under `aio-<id>`; candidates always need one of the four bibliographic identifiers. A contrast item's source work is its positive's, with `cache_key` `ctr-<id>`: that cached text is the item's own claim text, so its claim-side quotes come from the claim text, since no source states the edited element.

**Headline subsets** (eval/protocol.md): `headline` (H) is true when no closed-book sample of any model we can query names a cross-field gold candidate, whatever the date; `headline_any` (H-any) is the stricter variant in which no sample names any gold candidate, reported alongside; `headline_post` (H-post) is H restricted to items post-cutoff for every such model; `p40` is drawn from H.

## Validation rules (enforced by the loader)

1. Every judged relation is one of the six pair labels; the stored verdict equals the one derived by precedence.
2. `equivalent`, `generalization` and `partial` candidates have at least one evidence entry per covered key element. `specialization` candidates have that too, plus a non-empty `restricted_elements` with at least one `general_form: true` evidence entry for each restricted element. `similar_mechanism` candidates have at least one evidence entry per covered key element and per analogous element.
3. `partial` candidates cover at least two key elements and leave at least one key element uncovered; `equivalent` candidates cover every key element.
4. Every quote passes the verbatim check against the cached text (Unicode NFKC, quote and dash unification, whitespace collapsing, line-break hyphenation removal).
5. `claim.text` contains no author surname (of the source work or of a candidate, so eponyms such as a named filter are rewritten), no three consecutive words of the source title, no year and no coined name of the source work (`claim.coined_names`) (script check, then a person), and keeps the source field's vocabulary (checked against the source by `claim.checked_by`, a person other than `claim.written_by`, required from status `adjudicated` on: a claim an annotator wrote on a bare crossover packet is checked at adjudication). `claim.skeleton` is present and differs from `claim.text`.
6. Gold candidates are dated before `claim.date` (earliest public versions).
7. `negative.kind` is `contrast` or `natural_distractor`; a contrast item names the positive it was edited from. A built negative that labelling finds gold for keeps its `negative` record and is not in `N` (protocol decision 14).
8. Worked examples from LABELING.md carry `provenance.source: "guide"` and `status: "excluded"`.
9. Items with `origin.kind: "ai"` name the system.
10. Every candidate has `found_by`. Items with status `adjudicated` or `released` have no unjudged candidate within the recorded pool depth.
11. The view given to systems contains `claim.text` and `claim.date` only.
12. Once an item is labelled, every gold candidate has `cross_field` set and `field_confirmed_by` naming the annotator who confirmed it.

## Format example

For format only: this is worked example S1 from LABELING.md, which is excluded from the benchmark. Angle brackets stand for content taken from the cached texts during annotation.

```json
{
  "id": "cf-0000",
  "version": "v0",
  "status": "excluded",
  "exclusion_reason": "guide_example",
  "claim": {
    "text": "When the columns of the design matrix are far from orthogonal, estimate the regression coefficients as (X'X + kI)^(-1) X'Y with a small positive constant k added to the diagonal of X'X. The estimator is biased, but its mean square error can be smaller than that of least squares; plotting the estimates against k shows where they stabilise.",
    "skeleton": "Fit a linear model by least squares plus a quadratic penalty on the coefficient vector, weighted by a multiple of the identity, accepting some bias for lower variance when the inputs are strongly correlated.",
    "coined_names": ["ridge regression", "ridge trace"],
    "written_by": "a1",
    "checked_by": "a2",
    "source_work": {"doi": "10.1080/00401706.1970.10488634", "title": "Ridge Regression: Biased Estimation for Nonorthogonal Problems", "first_author": "Hoerl", "n_authors": 2, "year": 1970, "venue": "Technometrics", "field": "<OpenAlex field>", "domain": "<OpenAlex domain>"},
    "date": "<earliest public version, YYYY-MM-DD>",
    "field": "<OpenAlex field>",
    "domain": "<OpenAlex domain>",
    "computational": true,
    "chart": [
      {"id": "e1", "text": "least-squares data fit", "key": true},
      {"id": "e2", "text": "quadratic penalty on the coefficients", "key": true},
      {"id": "e3", "text": "penalty weighted by a multiple of the identity", "key": true}
    ]
  },
  "candidates": [
    {
      "work": {"title": "<Tikhonov 1963, regularization of ill-posed problems>", "year": 1963, "field": "<OpenAlex field>"},
      "found_by": ["annotator:a1"],
      "relation": "specialization",
      "covered_elements": ["e1", "e2", "e3"],
      "restricted_elements": ["e3"],
      "evidence": [
        {"element": "e1", "quote_candidate": "<verbatim, at most 40 words>", "quote_claim": "<verbatim>", "location_candidate": "<section or equation>", "location_claim": "<section or equation>", "text_level": "full_text", "general_form": false},
        {"element": "e2", "quote_candidate": "<verbatim>", "quote_claim": "<verbatim>", "location_candidate": "<section or equation>", "location_claim": "<section or equation>", "text_level": "full_text", "general_form": false},
        {"element": "e3", "quote_candidate": "<verbatim: the penalty with a general matrix>", "quote_claim": "<verbatim: kI>", "location_candidate": "<equation>", "location_claim": "<equation>", "text_level": "full_text", "general_form": true}
      ],
      "attribution": "unknown",
      "cross_field": true,
      "field_confirmed_by": "a1",
      "gold": true,
      "confidence": 3
    }
  ],
  "verdict": "specialization",
  "pool": null,
  "exposure": {"kind": "review", "work": null, "date": null},
  "origin": {"kind": "human", "system": null, "mode": null},
  "negative": null,
  "provenance": {"source": "guide", "mined_by": "manual", "dataset": "LABELING.md", "license": "CC-BY-4.0", "attribution": "Coelacanth"},
  "annotation": {"annotators": [], "prefill": null, "batch": null, "adjudicated_by": null, "notes": "format example"},
  "exclusions": []
}
```

## Change log

- 2026-09-24: v0 draft. Labels aligned with AViD's four types plus `partial`, `similar_mechanism` and the item verdict `nothing_close`.
- 2026-09-24, after Damir's review: `claim.text` is field-native (the source's own vocabulary) and the new `claim.skeleton` holds the neutral version, never shown to systems; `partial` needs at least two covered key elements; `restricted_elements` and `general_form` evidence for `specialization`; TREC-style pooling with `found_by` and `pool`; `provenance.mined_by`; `headline` redefined as closed-book-failed at any date, `headline_post` added, `p40` drawn from H; `splits.pool` renamed `closed_pool`; Claude pre-fill recorded in `annotation.prefill`.
- 2026-09-24, for the loader: `claim.coined_names` added for rule 5's script check; system entries in `found_by` carry their rank (`sys:<system>@<rank>`) so rule 10 can check the judged depth.
- 2026-10-06: `ai_origin` identifies the source work of AI-origin items, which have no bibliographic identifier.
- 2026-09-24, after Damir's decisions on the Phase 1 report: cross-field status is confirmed by a person (`candidates[].cross_field`, `candidates[].field_confirmed_by`, rule 12); H is defined on cross-field gold and `headline_any` (H-any) is added.
- 2026-10-09, before any human label: the `generalization` row says how P is obtained from C (fixing a parameter or adding a hypothesis) and that a P lacking an element of C without such a restriction is `partial` or lower (LABELING.md clarification 8).
- 2026-10-09, before any contrast item exists: the `negative` row names what `from_item` and `edited_element` hold and the provenance of contrast items; a contrast item's source work caches its own claim text (`ctr-<id>`) for claim-side quotes, and its `annotation.prefill.by` names the writer (LABELING.md section 5, "Contrast items"; `coelacanth.benchmark.contrast`).
- 2026-10-09, before any human label (Damir's decision): the `candidates[].evidence` row says that one element may have several evidence entries and counts as covered only when they together support all of it (LABELING.md clarification 9). No field or loader rule changed.
