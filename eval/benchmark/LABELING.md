# Annotation guideline (benchmark v0)

Status: draft v0, written 2026-09-24 and revised the same day after Damir's review (field-native claims, stricter `partial`, restricted elements for `specialization`, blind pooled judging); section 5 revised on 2026-10-06 for protocol v1.0-rc2 (second readings of critical fields, Krippendorff's alpha, one revision at most). Read together with SCHEMA.md, which defines the fields and the six pair labels. Every quote below is verified against a cached source (registry: `research/quotes/benchmark-docs.json`). Bibliographic metadata marked [V] was checked on Crossref or PubMed on 2026-09-24; relation statements marked [S] come from secondary sources (Wikipedia revisions cached in `cache/fulltext/wiki-*.txt`); section 3 clarifications 1 to 7 and section 5's full double labelling added on 2026-10-07; the key guidance in section 2 step 4, the last sentences of clarifications 4 and 5 and clarification 8 added on 2026-10-09; the key-flag sentences of clarification 7 and clarification 9 (a quote pair for part of an element, Damir's decision) added on 2026-10-09; the contrast-item procedure at the end of section 5 added on 2026-10-09 (procedure only, no decision rule changed).

## 1. What you label

For each item you see one claim from a newer source work and the earlier candidate works that searches returned. You label the relation of the claim to each candidate; the item verdict follows from the labels (SCHEMA.md, precedence rule).

Work blind: do not look at system outputs, closed-book results or the other annotator's labels before adjudication. Candidates pooled from systems (the top 10 of every system per item, SCHEMA.md "Pooling") reach you without any mark of which system found them; judge every unjudged one the same way as your own.

Claude may pre-fill the claim chart, candidate evidence and quotes. Check every pre-filled element and quote against the source, correct what is wrong, and choose every label yourself; the pre-fill is recorded in `annotation.prefill` and disclosed in the datasheet.

## 2. Procedure for one item

1. **Read the source work**, in full text when available.
2. **Write `claim.text` in the source field's own words**: one claim, at most 120 words, stated the way the source work states it, with its terms and notation. Remove only author names, titles, years, venues and the coined method name; eponyms count as author names, so a method named after a person is described instead of named (the loader checks the first authors of the source and of every candidate). Do not neutralise, generalise or translate the wording: crossing vocabularies is the task we measure, so you must not do it for the systems. Your co-annotator checks the text against the source.
3. **Write `claim.skeleton`**: the same claim in neutral, mechanism-level words (no field jargon). Systems never see it; it anchors your chart and the vocabulary-barrier ablation.
4. **Write the claim chart**: two to six elements. Mark an element `key` if, without it, a reader would no longer call it the same claim. What is usually key: the method's steps, the conditions and assumptions the claim needs, a property the claim proves or states (a guarantee, a bound), and the purpose the claim serves. Usually not key: a check or demonstration of the result (a comparison, an example run), the setting where it was observed, and an element that follows from another element of the chart, unless it is the result the claim states (then it is key).
5. **For each candidate**, decide element by element: covered with the same meaning, corresponding by role only (analogous), or not covered. For every covered or analogous element, attach a verbatim quote of at most 40 words from the candidate and from the source work, with their locations; an element may need more than one quote pair (section 3, clarification 9). For `specialization`, also list the `restricted_elements` (the claim elements obtained by fixing a parameter or adding a hypothesis to the candidate) and quote the candidate's general form for each (`general_form: true`). Quotes must come from the cached texts; the loader rejects quotes that do not match.
6. **Pick the relation** with the decision rules in section 3.
7. **Record** attribution (does the source work cite the candidate?), confidence from 1 to 3, minutes spent, the exposing document, and the exclusion list (the source work, the exposing document, later works that state the relation).

## 3. Decision rules

Ask in this order and stop at the first yes:

1. Are all key elements covered with the same meaning, under the same hypotheses? → `equivalent`
2. Is the claim what you get from the candidate by restricting it (fixing a parameter, adding a hypothesis), with the same meaning, while the candidate is strictly more general? → `specialization` (record the restricted elements with a quote of the candidate's general form)
3. Is the candidate what you get from the claim by restricting it, with the same meaning, while the claim is strictly more general (weaker hypotheses or a stronger conclusion)? → `generalization` (a candidate that only lacks an element of the claim is not a restriction of it: clarification 8)
4. Are at least two key elements covered with the same meaning, and at least one key element not covered? → `partial`. If only one key element is covered, the pair is not `partial`: go on to rule 5, and if that fails, it is `different`.
5. Does every key element have a counterpart in the candidate (covered with the same meaning, or corresponding by role or functional form), at least one by role only, in a different system or with a different meaning of the variables, so that neither statement implies the other? → `similar_mechanism`
6. Otherwise → `different`

Rules that settle common disputes:
- **When unsure between two labels, take the one lower in precedence** (it claims less prior art), set confidence to 1 and flag the pair for adjudication. A false "already done" is the most expensive error.
- **Names are not evidence.** A shared name can hide a different mechanism (example D1); different names can hide the same one (E2, S1).
- **Authors' own words are evidence, not labels.** "Equivalent", "a generalization of" or "the same as" in a paper or survey is checked against the definitions (G2, M2).
- **A transformation counts only if the variables keep their meaning.** Two equations that map onto each other after a change of variables are not `equivalent` when the change alters what a variable stands for (G1).
- **The label belongs to the pair.** The same claim can be `equivalent` to one candidate and `specialization` of another (S2); label each candidate separately.
- **Attributed imports** (the source work cites the candidate) get the same relation labels; attribution is recorded separately and marks them as controls, not rediscoveries (P1).
- **Non-computational claims**: the elements are entities, their interactions and the conditions under which they hold. `equivalent` needs the same entities and the same relations under the same conditions, for example one molecule described under several names.
- **Abstract-only evidence**: labelling is allowed with `text_level: abstract` and confidence at most 2.
- **`nothing_close` is a statement about the search, not about the idea.** Write the coverage: fields, sources, date range, and what could not be read.

Clarifications, added on 2026-10-07 before the protocol freeze after the practice round and a fidelity check of the Russian translation (`docs/annotators/pravila-razmetki.md`, section 3, uses the same numbers). Clarification 8 and the last sentences of clarifications 4 and 5 were added on 2026-10-09 after a dry run of the practice batch, and the key-flag sentences of clarification 7 and clarification 9 the same day, all before the freeze and before any human label:

1. **similar_mechanism threshold.** Rule 5 requires every key element to have a counterpart in the candidate: covered with the same meaning, or corresponding by role or functional form, at least one of them by role only. If any key element has no counterpart, the pair is `different`, even when one element corresponds by role.
2. **A named standard method.** "Names are not evidence" concerns a name the claim and the candidate share. When the candidate names a standard method with an unambiguous textbook definition (Simpson's rule, least squares, the Fourier transform), the elements in that definition count as covered; the annotator names the definition in the candidate note and sets confidence at most 2 when the candidate's text gives no formula. A condition of a key element the text is silent on but the standard definition allows (for example unequal intervals) counts as covered, with the doubt in the note.
3. **A general class and the claim's own case.** When a candidate states both a general form and the claim's special case, rule 1 is checked first: if the special case covers every key element with the same meaning, the pair is `equivalent`.
4. **One textbook step.** A quote may support an element through one standard reasoning step from a textbook of the annotator's field; the annotator keeps the pair, writes the step in one English sentence in the candidate note and sets confidence at most 2. A quote that needs more than one step, or a step the annotator is unsure of, is rejected. The step adds no premise the candidate's text does not state (for example that the errors are normal). When the label needs such a premise, the annotator is unsure between two labels (clarification 7): the lower one, confidence 1, the premise named in the note.
5. **Meaning of the variables.** For a claim about a method (an estimator, an algorithm, a computing formula), the variables' meaning is their role in the method: data, unknowns, error, penalty. A candidate's method that applies to the claim's objects with these roles unchanged has the same meaning even if the candidate applied it to another system (E2, S1). For a claim about nature (a law, a property of a substance, a relation in a cell), the meaning is the physical quantity: covered fraction and reaction rate differ, so M1 is `similar_mechanism`. A claim is about a method when it says how to compute or estimate something from data; it is about nature when it says how a system behaves (a law, a property, a relation). The item's `computational` flag does not decide this: the Langmuir isotherm is a formula and a claim about nature.
6. **Marks for specialization.** Every key element is marked covered; restricted elements are also marked restricted (covered then means present in the candidate's general form). The claim-side quote of each general-form pair shows the special case the claim takes; when the cached claim text lacks it, the nearest quote is used, confidence at most 2, and the note says where the element comes from. A key element absent from the candidate, not given in a general form, rules out `specialization`.
7. **Doubt and confidence.** "Unsure between two labels" means hesitating between two specific labels: take the lower of the two, confidence 1, both named in the note, never lower than the second label considered. Doubt about one quote or one element without a second label in mind does not change the label: confidence 2. The same holds for the key flag: doubt about whether one element is key, when the other choice would not change the candidate's label, is doubt about one element: confidence 2. When the other choice would change the label, the annotator has two labels in mind: the lower one, confidence 1, both labels and the element named in the candidate note. The key flag is one per item, the same for every candidate: it is decided from the claim alone (section 2 step 4: without the element it would no longer be the same claim, and the list of what is usually key), never to reach a candidate's label; each candidate's label then follows from the rules above. Confidence 3: the label is clear and no kept quote pair is abstract-only (a `different` pair without quote pairs may get 3 when the candidate's abstract shows the key elements are absent). Confidence 2: a kept pair is abstract-only, or doubt about one element or quote, or clarification 2 or 4 was used. Confidence 1: two labels considered. The abstract-only cap applies to the kept quote pairs, not to what was read; a correct pair is never rejected to reach confidence 3.
8. **Generalization and partial.** Rule 3 needs the candidate to be a special case of the claim: fixing a parameter of the claim or adding a hypothesis to it gives the candidate (G1: p = 0; G2: stationary signals only). A claim element that this restriction removes does not rule out `generalization`. A candidate that simply lacks an element of the claim (a step, or a property the claim proves, such as a convergence guarantee) is a fragment of the claim, not a special case: rule 4 applies. Marks for `generalization`: key elements present in the candidate with the same meaning are covered; an element the restriction removes is left unmarked, and the note names the restriction. A stronger conclusion (rule 3) counts only when the candidate's conclusion is what the claim gives under such a restriction: a claim that proves convergence at a rate and a candidate that proves only convergence is not `generalization`.
9. **A quote pair for part of an element.** A quote pair that supports only part of its element is kept, and another pair is added for the rest; the element is covered only when its kept pairs together support all of it (a named standard method: clarification 2).

## 4. Worked examples

All fourteen are excluded from the benchmark (`provenance.source: guide`). Formulas are written in plain text.

### equivalent

**E1. Tai's model and the trapezoidal rule**
- Claim: Tai MM, "A mathematical model for the determination of total area under glucose tolerance and other metabolic curves", *Diabetes Care* 17(2):152-154, 1994, doi 10.2337/diacare.17.2.152, PMID 8137688 [V]. Field: diabetes and clinical nutrition.
- Candidate: the trapezoidal rule of numerical integration (numerical analysis; centuries older) [S].
- Chart (all key): e1 area under a curve known only at sampled points; e2 straight-line interpolation between consecutive samples; e3 total area = sum over k of (x_k - x_(k-1)) * (y_(k-1) + y_k) / 2.
- Evidence: the rule's formula covers e1 to e3 with the same meaning. Exposed by Monaco JH, Anderson RL, "Tai's formula is the trapezoidal rule", *Diabetes Care* 17(10):1224-1225, 1994, doi 10.2337/diacare.17.10.1224, PMID 7677819 [V]. Wikipedia's article on the case says "her method is in fact the trapezoidal rule" [S].
- Label: `equivalent`. Not `specialization`: the claim adds no restriction. Not `partial`: no element is uncovered.
- Attribution: not cited. Exposure: letter.
- Field-native text vs skeleton, as an annotator would write them: `claim.text` keeps the clinical wording, for example *the total area under a glucose tolerance curve is obtained by dividing the region under the plotted glucose values into simple polygons between consecutive measurement times and adding up their areas*, and drops the coined name "Tai's model". `claim.skeleton` is neutral: *approximate the integral of a function known only at sampled points by joining neighbouring samples with straight lines and summing the resulting areas*. Systems see only the first.

**E2. The lasso and basis pursuit denoising**
- Claim: Tibshirani R, "Regression Shrinkage and Selection via the Lasso", *J R Stat Soc B* 58(1):267-288, 1996, doi 10.1111/j.2517-6161.1996.tb02080.x [V]. Field: statistics.
- Candidate: basis pursuit denoising, Chen, Donoho and Saunders; journal version "Atomic Decomposition by Basis Pursuit", *SIAM J Sci Comput* 20(1):33-61, 1998, doi 10.1137/S1064827596304010 [V]. Field: signal processing.
- Chart (all key): e1 least-squares fit of a linear model; e2 an l1 penalty or l1 constraint on the coefficients; e3 sparse solutions as the aim.
- Forms: lasso, minimise ||y - X b||^2 subject to ||b||_1 <= t; basis pursuit denoising, minimise (1/2)||y - F c||^2 + lambda ||c||_1. For every t there is a lambda with the same solution (Lagrangian duality): a routine reformulation.
- Evidence [S]: "Basis pursuit denoising was introduced by Chen and Donoho in 1994, in the field of signal processing. In statistics, it is well known under the name LASSO, after being introduced by Tibshirani in 1996."
- Label: `equivalent`. Dates: record the earliest public version of each; the candidate's 1994 report predates the claim, its 1998 journal version does not.
- Attribution: unknown (check the reference list during annotation).

### specialization

**S1. Ridge regression and Tikhonov regularization**
- Claim: Hoerl AE, Kennard RW, "Ridge Regression: Biased Estimation for Nonorthogonal Problems", *Technometrics* 12(1):55-67, 1970, doi 10.1080/00401706.1970.10488634 [V]. Field: statistics.
- Candidate: Tikhonov regularization of ill-posed problems (Tikhonov 1963; numerical analysis and inverse problems) [S].
- Forms: candidate, minimise ||A x - b||^2 + ||G x||^2 for a chosen matrix G; claim, minimise ||y - X b||^2 + lambda ||b||^2, that is G = sqrt(lambda) * I.
- Chart (all key): e1 least-squares fit; e2 a quadratic penalty on the coefficients; e3 the penalty matrix is a multiple of the identity.
- Evidence [S]: the general form uses "for some suitably chosen Tikhonov matrix" (`general_form: true`, for restricted element e3), and "In many cases, this matrix is chosen as a scalar multiple of the identity matrix", the ridge case.
- Label: `specialization`, `restricted_elements: [e3]`: the claim is the candidate with G restricted to a multiple of the identity. Not `equivalent`: the candidate also allows other G (for example difference operators that enforce smoothness), so it is strictly more general.
- Trap: the same article opens with "Ridge regression (also known as Tikhonov regularization, named for Andrey Tikhonov)". A shared name does not decide the label.

**S2. Add-one smoothing and additive smoothing**
- Claim: add-one smoothing of n-gram counts in statistical language modelling (natural language processing) [S].
- Candidate: additive smoothing with a general pseudocount a, theta_i = (x_i + a) / (N + a d) (Lidstone 1920, actuarial statistics) [S].
- Chart (all key): e1 add a pseudocount to every observed count before normalising; e2 the pseudocount equals 1.
- Evidence [S]: "additive smoothing, also called Laplace smoothing or Lidstone smoothing"; the general form's pseudocount "is a smoothing parameter, with α = 0 corresponding to no smoothing" (`general_form: true`, for restricted element e2); common choices include "or 1 (Laplace's rule of succession)".
- Label: `specialization`, `restricted_elements: [e2]`: the claim fixes a = 1.
- Contrast: against Laplace's rule of succession itself (a = 1), the same claim is `equivalent`. Record the two candidates as two pairs.

### generalization

**G1. The Bass diffusion model and logistic growth**
- Claim: Bass FM, "A New Product Growth for Model Consumer Durables", *Management Science* 15(5):215-227, 1969, doi 10.1287/mnsc.15.5.215 [V]. Field: marketing.
- Candidate: logistic growth (Verhulst 1838; population biology) [S].
- Forms: claim, dF/dt = (p + q F)(1 - F), with p the adoption independent of earlier adopters and q the imitation; candidate, dF/dt = r F (1 - F).
- Chart (all key): e1 cumulative share of adopters F(t); e2 growth through contact with earlier adopters, q F (1 - F); e3 adoption independent of earlier adopters, p (1 - F).
- Evidence [S]: "The second special case reduces to the logistic distribution, when p=0." The candidate covers e1 and e2; the claim with p = 0 gives the candidate with the same meaning.
- Label: `generalization`. Not `partial`: the candidate is exactly a special case of the claim, not a fragment of it (section 3, clarification 8).
- Trap: the same article says "the basic Bass diffusion is a Riccati equation with constant coefficients equivalent to Verhulst-Pearl logistic growth". That equivalence needs an affine change of variable after which F no longer means the share of adopters, so by section 3 it does not make the claims `equivalent`.

**G2. Detrended fluctuation analysis and the Hurst exponent**
- Claim: Peng et al., "Mosaic organization of DNA nucleotides", *Phys Rev E* 49(2):1685-1689, 1994, doi 10.1103/PhysRevE.49.1685 [V], which introduced detrended fluctuation analysis (DFA). Field: biophysics.
- Candidate: Hurst HE, "Long-Term Storage Capacity of Reservoirs", *Transactions of the ASCE* 116(1):770-799, 1951, doi 10.1061/TACEAT.0006518 [V]. Field: hydrology.
- Chart (all key): e1 a scaling exponent of fluctuations against window size; e2 long-range correlation read from that exponent; e3 local trends removed in each window, so that non-stationary signals are allowed.
- Evidence [S]: the DFA exponent "is a generalization of the Hurst exponent", and "DFA may also be applied to signals whose underlying statistics (such as mean and variance) or dynamics are non-stationary".
- Label: `generalization` (weaker hypotheses: non-stationary signals are allowed). Not `equivalent`: element e3 has no counterpart in the candidate. Not `partial`: e3 is the element that the restriction to stationary signals removes (section 3, clarification 8).
- Note: a survey across fields calls these "functionally corresponding measures of correlation scaling" (Stephenson and Macomber 2026, arXiv 2601.22389) [V]. That wording is evidence, not the label.

### similar_mechanism

**M1. The Langmuir isotherm and Michaelis-Menten kinetics**
- Claim: Langmuir I, "The adsorption of gases on plane surfaces of glass, mica and platinum", *J Am Chem Soc* 40(9):1361-1403, 1918, doi 10.1021/ja02242a004 [V]. Field: surface chemistry.
- Candidate: Michaelis-Menten enzyme kinetics (Michaelis and Menten 1913; biochemistry) [S].
- Forms: claim, theta = K p / (1 + K p), the fraction of surface covered at equilibrium; candidate, v = Vmax S / (Km + S), the initial rate of an enzyme reaction.
- Evidence [S]: the Langmuir article lists "Michaelis-Menten kinetics (equation with the same mathematical form)"; the Michaelis-Menten article says the equation can describe a generic reaction "in the same way that the Langmuir equation can be used to model generic adsorption of biomolecular species".
- Label: `similar_mechanism`: the same saturating form comes from an analogous mechanism (a finite number of sites that fill up), but the variables mean different things (covered fraction vs reaction rate) and neither statement implies the other. The former label "same functional form" maps here.

**M2. Echo state networks and the correlation length**
- Claim: the spectral-radius criterion for echo state networks, which keeps the recurrent weight matrix near the edge of stability (Jaeger 2001, machine learning; technical report without DOI) [S, via Stephenson and Macomber].
- Candidate: the correlation length that diverges at a critical point (statistical physics) [S].
- Evidence [V]: Stephenson and Macomber list the correlation length, the DFA exponent and the Hurst exponent "and the machine learning engineer's spectral radius χ all detect critical signatures under different notation".
- Label: `similar_mechanism`: both are read as distance to a critical transition, but one is the largest eigenvalue modulus of a weight matrix and the other a spatial decay length, and neither statement implies the other. The same survey says practitioners "are performing functionally equivalent calculations under different notation"; under SCHEMA.md that is not `equivalent`.

### partial

**P1. Simulated annealing and the Metropolis algorithm**
- Claim: Kirkpatrick S, Gelatt CD, Vecchi MP, "Optimization by Simulated Annealing", *Science* 220(4598):671-680, 1983, doi 10.1126/science.220.4598.671 [V]. Field: combinatorial optimization.
- Candidate: Metropolis et al., "Equation of State Calculations by Fast Computing Machines", *J Chem Phys* 21(6):1087-1092, 1953, doi 10.1063/1.1699114 [V]. Field: statistical physics.
- Chart (all key): e1 random local moves accepted with probability min(1, exp(-dE / T)); e2 at a fixed T the visited configurations follow the Boltzmann distribution of the energy (the cost); e3 lower the temperature T gradually during the run; e4 use the procedure to find low-cost solutions of combinatorial problems.
- Evidence [S]: "The method is an adaptation of the Metropolis-Hastings algorithm, a Monte Carlo method to generate sample states of a thermodynamic system, published by N. Metropolis et al. in 1953."
- Label: `partial`: the candidate covers two key elements, e1 and e2, with the same meaning; e3 and e4 are not in it (it samples at a fixed temperature to compute equilibrium properties). Had the chart merged e1 and e2 into one element, the candidate would cover a single key element, and the pair would not be `partial` (section 3, rule 4); write charts at the grain of the claim's distinct steps. Not `generalization`: holding T fixed removes e3 but not e4 (the candidate computes equilibrium properties, not low-cost solutions), so the candidate lacks e4 and is a fragment of the claim (clarification 8).
- Attribution: cited. This is an attributed import, so it is a control, not a rediscovery.

**P2. A population pharmacokinetic dosing engine and nonparametric mixture estimation**
- Claim: the nonparametric population pharmacokinetic dosing engine described by Kulikowski 2026 (arXiv 2609.07595; clinical pharmacology) [V].
- Candidate: nonparametric maximum-likelihood estimation of a mixing distribution (statistics; for example Schumitzky 1991, "Nonparametric EM Algorithms for Estimating Prior Distributions", *Applied Mathematics and Computation* 45(2):143-157, as cited by Kulikowski) [S].
- Chart (all key): e1 per-patient parameters treated as draws from an unknown population (mixing) distribution; e2 that distribution estimated by nonparametric maximum likelihood, concentrated on a sparse set of support points; e3 individual doses chosen by feedback control from the estimated distribution and new measurements.
- Evidence [V]: the dosing algorithm "estimates a population parameter distribution as a constrained optimization over a sparse set of support points: the nonparametric maximum-likelihood estimate of a mixing distribution"; in its dosing-control form it resembles LQG, "a Kalman filter plus a linear-quadratic regulator".
- Label: `partial`: the candidate covers two key elements, e1 and e2; e3 is not in it. A control-theory candidate (LQG) would be a separate pair.

### different

**D1. The Boltzmann machine and the Boltzmann transport equation**
- Claim: Ackley DH, Hinton GE, Sejnowski TJ, "A Learning Algorithm for Boltzmann Machines", *Cognitive Science* 9(1):147-169, 1985, doi 10.1207/s15516709cog0901_7 [V]. Field: machine learning.
- Candidate, retrieved by name: the Boltzmann transport equation (Boltzmann 1872; kinetic theory), which "describes the statistical behaviour of a thermodynamic system not in a state of equilibrium" [S].
- Label: `different`: the machines "are named after the Boltzmann distribution in statistical mechanics, which is used in their sampling function" [S]; the transport equation shares only the name.
- Contrast: a real twin exists: "A Boltzmann machine (also called Sherrington-Kirkpatrick model with external field or stochastic Ising model)" [S]. Against Sherrington D, Kirkpatrick S, "Solvable Model of a Spin-Glass", *Phys Rev Lett* 35(26):1792-1796, 1975, doi 10.1103/PhysRevLett.35.1792 [V], the model element is covered with the same meaning. If the claim is only the model, that pair is `equivalent`; if the learning rule is also a key element, the pair is `partial` only when at least two key elements are covered (for example the energy function and its Boltzmann distribution), and otherwise falls to rule 5.

**D2. The percolation threshold and Darcy's law**
- Claim: Broadbent SR, Hammersley JM, "Percolation processes", *Mathematical Proceedings of the Cambridge Philosophical Society* 53(3):629-641, 1957, doi 10.1017/S0305004100032680 [V]: whether a random medium contains a connected path, and the threshold at which one appears.
- Candidate, retrieved by shared setting and vocabulary: Darcy's law (Darcy 1856; hydrology), "an equation that describes the flow of a fluid through a porous medium" [S].
- Why it is a trap: percolation asks about connectivity, "Will the liquid be able to make its way from hole to hole and reach the bottom?" [S], while Darcy's law gives the flow rate through an already connected medium; texts that use Darcy's law also say water "percolates", for example "how the hot water percolates through the coffee grinds under pressure" [S].
- Label: `different`: the same setting and words, but the key element (a connectivity threshold of a random network) is absent from the candidate.

### nothing_close (item verdict)

These two are hypothetical walk-throughs of the procedure, not real searches.

**N1. A computational claim with only false friends.** Claim H1 proposes an estimator whose name reuses a well-known method's vocabulary. The annotator searches three fields (for example statistics, signal processing, econometrics) in OpenAlex and arXiv, restricted to works before the claim date, screens 40 candidates and reads five in full. Two use the same name for a different quantity (`different`); three share only the data type (`different`). Item verdict: `nothing_close`. The report reads: *Closest prior work found: none at the similar-mechanism level or above. Searched: statistics, signal processing, econometrics; OpenAlex and arXiv; works before [claim date]. Not searched: patents, non-English literature; two candidates were paywalled and read at abstract level.* It never says "novel".

**N2. A non-computational claim.** Claim H2 states that entity A regulates entity B under condition K. Candidates mention A and B together but report no regulation, or report regulation of B by a different entity; none covers the key element *A regulates B under K* with the same meaning, and none gives the same relation between analogous entities. All candidates are `different`; item verdict `nothing_close`, with the same kind of coverage statement. If the chart were e1 *A acts on B*, e2 *through step M*, e3 *under condition K*, a candidate reporting that A acts on B through M under a different condition would cover two key elements and miss one: `partial`, and the verdict would change. A candidate reporting only that A acts on B would cover one key element and would not be `partial`.

## 5. Agreement and adjudication

- Every item is labelled in full by two annotators, independently (`python -m coelacanth.benchmark.assign` writes each annotator's copy; pairs rotate when there are more than two annotators). The 25 first items of b01 (the reliability measurement) and b02's 12 crossover items (the pre-fill anchor) go to the same two annotators, a1 and a2 (eval/protocol.md decisions 4, 18 and 25).
- Agreement is measured on independent first readings only, never after discussion: Krippendorff's alpha (nominal) with bootstrap intervals, Cohen's kappa and raw agreement alongside, on four distinctions: the six-way item verdict, already done (`equivalent` or `specialization`) or not, gold or not per candidate, and the cross-field flag on gold. The work proceeds if alpha is at least 0.80 on already done and on gold, and at least 0.667 on the six-way verdict. If a distinction falls short, this guide is revised once (logged in eval/protocol.md section 14) and agreement is measured again on 12 fresh shared items from b03 (annotators receive b01 and b02 together); the discussed items are not relabelled to raise the number (decision 18).
- Damir settles every disagreement from the evidence and records the adjudicated label with a one-line reason; he also checks the evidence of every `equivalent` and `specialization` label and decides every AI-origin item. If he cannot settle an item, it is excluded with `exclusion_reason: annotators_disagree`.
- Pre-filled notes state facts only (field, year, venue, which text is cached, how many quote pairs are pre-filled). A note that suggests how a candidate relates to the claim is an error; report it, and judge from the texts (decision 25).
- Minutes per item are recorded, for the cost section of the paper.

### Contrast items (eval/protocol.md decision 14)

Procedure only; the decision rules of section 3 apply to contrast items unchanged. The annotators' version, in Russian, is `docs/annotators/kontrastnye-primery.md`; the tool is `python -m coelacanth.benchmark.contrast`.

- **Pivots.** Adjudicated rediscovery positives (a gold candidate the source does not cite; human origin; not built as a negative), in a seeded order stratified by `provenance.source` and `claim.computational` (`contrast pivots`). Damir hands them out in that order, alternating between annotators, and logs every pivot skipped because no acceptable edit exists.
- **The edit.** The writer changes exactly one key chart element that a gold candidate of the pivot covers or matches by role, so that it no longer has the same meaning in those candidates, and rewrites the claim text and the skeleton only where that element appears. The claim text keeps the source field's vocabulary and section 2's rules (one claim, at most 120 words, no names, years, titles or coined names) and still states every chart element. No plain negation; the edited claim must not become another known method or result, which the writer checks with a short search. The writer works alone, with no model in the loop. The contrast file (`contrast template`, then `contrast check`) records the edit and, for the adjudicator only, what changed and the labels the writer expects.
- **Two readings.** Both annotators label the contrast item in full in the contrast batch (`contrast packet`): the writer, and the other annotator blind to which element changed and to the writer's expectations, with their own search. Claim-side quotes come from the claim text, cached as the source's text (`ctr-<id>`), since no source states the edited element. Readers do not edit the claim text, skeleton or chart; they note problems instead.
- **Adjudication** as above, with one addition: when the two readings disagree on the item verdict, the item is excluded (`annotators_disagree`), never brought into line with the writer's intent; other disagreements are settled from the evidence as usual. Merge records the writer (from `prefill.by`) as `claim.written_by` and, on the other annotator's reading, that annotator as `claim.checked_by`; when the kept reading lacks the check, the adjudicator names the checker in `claim_checked_by`. `contrast finish` then checks both against the contrast file. Whether the item is in `N` follows from its adjudicated verdict. Agreement between writer and checker is reported descriptively, outside the reliability measurement (decision 18).

## 6. What never appears in a label or a report

- Words that accuse: "plagiarism", "not novel", "stolen" (CLAUDE.md output rule).
- A label decided from names, from the model's memory, or from an abstract without `text_level: abstract`.
