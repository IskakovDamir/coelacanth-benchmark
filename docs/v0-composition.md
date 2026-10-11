# Benchmark v0: composition, size and H target (decided 2026-10-06)

Updated 2026-10-09 (Damir's decision on `docs/design/h-decision.md`, option A amended; `eval/protocol.md` section 14): the `H` target of 25 and floor of 20 are retired. The two closed-book probes of 7 Oct project `H` at about 5 to 7 (about 17 under the most generous reading), so `H` is reported first and descriptively (k of n), and the confirmatory tests run on `R`, the adjudicated rediscovery positives with person-confirmed cross-field gold, split by recall. The target is now at least 30 adjudicated `R` items, labelled before `A` items and negatives. The projection below is kept as the 6 Oct record; the measured rates are in `docs/design/h-decision.md` ("Why H cannot reach the floor").

Decided under Damir's delegation of 6 Oct (GitHub issue #5; `eval/protocol.md` decisions 2, 14, 15 and 16; reasons in `docs/decisions.md`). It replaces the proposal of the same morning (80 items, `H` target 35 with controls inside `H`). The hand-check standard stays as it is: two blind readings of each sampled declaration, the lower call kept (`data/candidates/declarations-triage/hand-check-summary.json`). "Expected" numbers are projections, not counts; the paper reports the number adjudicated, never a target.

## Decision

**v0 = about 80 items: about 42 rediscovery positives, 8 attributed Kulikowski controls (subset `C`, at most 10), 10 AI-origin items (stratum `A`, 10 to 12) and about 20 negatives. `H` target: 25 rediscovery positives, floor 20** (retired 2026-10-09, see the update above).

| Stratum | Planned | In b01 | Where the rest comes from | Basis |
|---|---|---|---|---|
| Equivalence declarations | 20 | 11 (7 both readers rate usable; 4 the stricter reading marked down, one of them same-field) | the 10 usable titles of the screening of every remaining title (two blind readings, the lower call kept: 10 of 79, `data/candidates/declarations-triage/screening.jsonl`); the other 2 usable sampled titles were excluded from b01 (cf-0032, cf-0033) | hand-check-summary.json; screening.jsonl; queue.md |
| Kulikowski, method named partly or not at all | 14 | 10 | the 4 ready in the queue | queue.jsonl (`method_named`) |
| Gradient Coupling catalogue | 3 | 2 | 1 more if instance 9, 10 or 13 passes the checks | data/candidates/README.md |
| Post-cutoff sweep leads | 3 | 1 | about 2 of the 5 other leads | research/novelty/threats.md section 7.7 |
| Non-computational, known unification | 2 | 2 (RANKL, cross-field; ILC2, same-field) | none | PLAN.md source 5 |
| **Rediscovery positives** | **42** | **26** | | |
| Attributed Kulikowski controls (`C`) | 8 | 4 | 4 drawn with a seed from the 50 others, at most 2 per family | queue.jsonl; protocol decision 16 |
| AI-origin methods (`A`) | 10 | 0 | drawn with a fixed seed from the 54, stratified by field and generator, in fields an annotator can read; never selected by what any system found | protocol decision 15 |
| Contrast negatives | 10 | 0 | one key element of a randomly drawn adjudicated positive edited by an annotator, no model in the loop, verified blind by the other annotator | protocol decision 14 |
| Natural near-negatives | 10 | 0 | drawn with a seed from Kulikowski's 17 topical distractors and the catalogue's failed unification (Arrhenius metastability); their verdicts come from labelling | protocol decision 14 |
| **Total** | **about 80** | **30** | | |

The draws are fixed in `data/candidates/draws-v0.json` (`python -m coelacanth.mining.draw`, seed 20261006, with the SHA-256 of every input): AI-origin ai-opus-4-6, ai-sonnet-4-4, ai-sonnet-3-2, ai-opus-1-2, ai-opus-2-3, ai-sonnet-1-4, ai-opus-3-2, ai-sonnet-2-5, ai-sonnet-5-5, ai-opus-4-3 (one per field and generator cell, then the next in seeded cell order; cells whose field nobody can read are skipped by the same order); controls gp-ml-001, pca-recsys-001, em-physics-002, kalman-neuro-001; natural near-negatives gci#18 and the first 9 eligible distractors in seeded order.

`N` is the set of items whose adjudicated verdict is `nothing_close`, whatever their construction; a natural distractor that labelling finds gold for stays in v0 as a positive (SCHEMA.md rule 7). Same-field items (ILC2, the Alonso declaration and any declaration labelling finds same-field) have no cross-field gold, so they are scored in `ALL` on any-gold metrics and never enter `H`.

## Projected H

`H` counts rediscovery positives with at least one person-confirmed cross-field gold work that no closed-book sample names. About 40 of the 42 planned positives are expected to have cross-field gold. Failure shares from the settled closed-book pilot (`eval/results/closed-book-pilot/adjudicated.json` with `borderline-decisions.json`; Wilson 95% intervals), applied per source; sources the pilot did not cover take the pooled share:

| Source | Items | Failed share in the pilot | Projected in `H` |
|---|---|---|---|
| Declarations | 20 | pooled, 14 of 20 (0.70, 0.48 to 0.86) | 14.0 (9.6 to 17.1) |
| Kulikowski, method named partly or not at all | 14 | 4 of 8 (0.50, 0.22 to 0.79) | 7.0 (3.0 to 11.0) |
| Catalogue and sweep | 5 | 4 of 4 (1.00, 0.51 to 1.00) | 5.0 (2.6 to 5.0) |
| Non-computational, cross-field | 1 | pooled | 0.7 (0.5 to 0.9) |
| **Total** | **40** | | **26.7 (15.7 to 33.9)** |

The interval sums the per-source bounds, so it is wide on purpose. Three changes since the pilot all push `H` down, and none is in the projection: five samples per model instead of three, Sonnet 5.5 in place of Sonnet 5 (which answered "unknown" in 21 of 60 pilot samples), and works named in closed-book answers joining the gold before `H` is computed (decision 1). The target of 25 therefore sits under the central projection, and the floor of 20 is the protocol's limit for confirmatory tests (decision 23).

Attributed controls, reported as `C` and in the secondary `H+C` line: 8 at the pilot's 6 of 8 (0.75, 0.41 to 0.93) give about 6 (3.3 to 7.4) closed-book-failed controls. AI-origin items are post-cutoff for every probed model by construction (generated in September 2026, after the June 2026 cutoffs), so they are reported as their own descriptive stratum, never added to `H`.

## Why this composition

- **Controls out of `H`.** An attributed import cites its source, so it is not a rediscovery, and the ease of finding a cited source says little about cross-field search. Keeping them as `C` preserves their purpose (does a system flag honest, cited reuse?) without diluting the headline (Jacovi et al. 2023; Kandpal et al. 2023; `reference/research-notes/method-contamination.md`). With them out, the old target of 45, or 35, cannot be met from rediscoveries alone without loosening the hand check.
- **Size from supply, not from a target.** About 44 rediscoveries are expected under the strict two-reading standard (the stratified projection over all declaration positives is 20 usable, 95% interval 8.5 to 43). Planning 42 leaves room for the screening to come in low. Grow toward 100 only if at least 50 rediscoveries pass the same standard by Wed 14 Oct.
- **Power.** At 25 paired items an exact McNemar test detects only large differences in success@10 (the realised minimum detectable difference is reported; `reference/research-notes/method-power.md`), so the confirmatory family is three tests and every other comparison is exploratory (decision 23). More items of lower quality would not fix this: label errors in test sets can reorder the systems being compared (Northcutt et al. 2021).
- **Negatives that test something.** Contrast items differ from a real positive in one key element, so a system that matches on topic rather than mechanism flags them (Gardner et al. 2020; Kaushik et al. 2020); natural distractors were chosen by someone outside the project (Kulikowski), which guards against our own construction artefacts (Gururangan et al. 2018). 20 negatives give the false already-done rate a usable denominator together with every item weaker than `specialization`.
- **AI-origin items by seed.** Selecting them by what a pre-search found would select them by the behaviour of the systems under test; a seeded, stratified draw keeps the stratum unbiased, and the pre-search only adds to their pools (Gupta and Pruthi 2025; Reuel et al. 2024; `reference/research-notes/method-validity.md`).
- **The 60% rule** stays: if fewer than 60% of the first 30 labelled rediscovery positives fail closed-book for every model, further supply shifts away from well-known Kulikowski families.

## What follows from it

- Queue targets in `src/coelacanth/mining/queue.py` (`TARGET`) and `data/candidates/queue.md`.
- Annotator hours for about 80 items (`docs/annotator-brief.md`).
- Outline numbers (`paper/ecir2027/OUTLINE.md`).
- Batch b02 (next): the 4 ready Kulikowski positives, 4 controls, the 10 AI-origin items, the 10 natural near-negatives and 2 of the screened declarations, with 12 crossover items for the pre-fill check (decision 25); b03 takes the other 8 screened declarations, the remaining catalogue and sweep items and the contrast negatives.
