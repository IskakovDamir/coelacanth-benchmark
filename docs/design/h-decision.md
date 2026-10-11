# H after the two closed-book probes (decision memo, 7 Oct)

**Decided by Damir on 2026-10-09: option A, amended.** Applied to `eval/protocol.md` the same day, before the rules freeze (section 14).

Status: proposal for Damir, 2026-10-07; nothing below was applied until he decided. Written by a panel (three independent analyses: contamination methodology, an ECIR resource-track reviewer, statistics and power; then a judge who checked them against the result files and listed their errors). Evidence: `eval/results/closed-book-second-probe/`, `eval/results/closed-book-declarations-pilot/`, `data/candidates/declarations-post-cutoff-count.json`.

## Path

**Adopt A, amended (all three analyses converge here):**
- Keep the union rule as pre-registered, and report H first, descriptively.
- Move the unchanged confirmatory family to R.
- Reject B and C.
- Use D only as planned supply, with the 2026-08-01 boundary.
- Report E as a descriptive result, not the headline.

This changes how CLAUDE.md's "Headline numbers are reported on H" applies, so it needs your explicit yes.

**Not pivot hunting:** no system has run on a benchmark item, the trigger is closed-book data, and hypotheses, metric, tests, alpha and Holm are unchanged. Logged before 12 Oct, it is an update under section 14.

**Why H cannot reach the floor.**
- p17, the only pilot item that fails both probes, is excluded from v0 (q0215). The 11 pilot-derived v0 positives score 0 of 11, or 4 if a person reverses every reader-accepted work.
- Declarations: 3 of 11 under H-any, at most 5 under the cross-field proxy.
- Projected H is about 5 to 7, and about 17 under the most generous reading.

Every reading is under 20; the decision need not wait for human judgments.

## Changes

Each change gets a section 14 row: type update, dated before the freeze and any system run, reason "the 7 Oct probes project H under 20".

1. **Decision 8 and section 3.** Record that the rule triggered. H means failing both probes, and probe B runs on every positive at 3 samples. Define H over all three probed models, as CLAUDE.md and both pilots do (on the pilot this equals "models in the frozen configs").
2. **Section 1.** Add R: adjudicated rediscovery positives (not C, not A) with person-confirmed cross-field gold. Add the stratifier "recalled": any probed model, either prompt, decided by a person before any run.
   - H is R minus the recalled items: k of n, Wilson interval, first row of every headline table.
   - H_A only as a labelled sensitivity line.
3. **Section 8 and decision 23.**
   - T1 runs on R. T2 and T3 run on a P40 drawn from R.
   - A test with fewer than 20 items on 19 Oct leaves the family, and Holm runs over the tests that remain.
   - Design MDE at |R| = 40 under Holm: about 0.27 to 0.36. The Kulikowski analog puts T1's power near 0.1.
   - P1 runs on R. P2 runs on all 92 K queries, with closed-book-failed K descriptive.
4. **Decisions 2 and 36.** P40 is drawn from R, stratified by source and by recall, with the seed recorded on 19 Oct.
5. **Sections 6 and 7.** Add a Tier 2 memory reference: Sonnet 5.5, frozen prompt, sample 1. That is the single agent's model and framing, without tools.
   - Scored on R only, pooled like any system, its own group in the leave-own-group-out check.
   - Named works dated on or after claim.date: parametric leakage.
6. **Decision 1.** Named works are judged blind only for items not already out of H by drafted gold. Report how many stay unjudged.
7. **docs/v0-composition.md.** Replace the 26.7 projection with the measured rates and retire "target 25, floor 20". Set a target of at least 30 adjudicated R items, labelled before A items and negatives.
8. **PLAN.md.** The ECIR question becomes: on documented cross-field rediscoveries, how often do checkers find the earlier work, split by closed-book recall? The Feb 2027 gate and the ACL question get restated in protocol v2, not now.
9. **CLAUDE.md, your call.** H's k of n leads every headline table, and inferential results are on R with the recall split. "Memorization control" becomes "closed-book recall measured per item and model".
10. **docs/rejected.md.**
    - B redefines gold after seeing what the models named, and the declared works it would keep are equivalent by construction.
    - C reverses decision 8 after it fired: 8 of 9 H_A items were caught.

    Plus decisions.md and session-log entries.

## Measure next

- **$0, before 12 Oct:** you judge, blind, the 9 reader-accepted works that alone decide p02, p05, p07 and p18.
- **$0, declarations:**
  - Read the lines outside the gold list for cf-0004, cf-0016 and cf-0034.
  - Confirm whether McKean 1962 (cf-0011) and Kuramoto 1975 (cf-0024) are cross-field.
- **About $17: the closed-book probes on about 47 positives**, each once its claim text has been checked.
  - The 15 pilot-derived v0 items count as unprobed: their b01 texts differ from the pilot texts.
  - Re-probes cost about $0.37 each.
  - The log implies about $19.5 of credit, not $25, so check the balance first.
- **$0:** adjudicated |R| on 19 Oct; judging hours per item on 5 items.

## Paper

**Can claim**, after measuring and the weekly re-check:
- documented cross-field rediscoveries with typed labels and closed-book recall per item, model and prompt;
- checker miss rates on R split by recall (CLAUDE.md's "first measurement", qualified "on documented rediscoveries");
- H as "no closed-book recall detected under two probes", given as k of n;
- recall rates by source, without "first";
- search versus memory, as exploratory.

**Cannot claim:**
- any test on H;
- "contamination-free" or "unseen";
- strata differences as a memorization effect (confounded with popularity);
- results for undocumented rediscoveries;
- Theorizer or Owl misses as search failures rather than memory;
- pilot numbers as final (Claude's claims, gold and judgments).
