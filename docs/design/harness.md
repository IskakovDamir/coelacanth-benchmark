# Measurement A harness: final specification

Status: design, 2026-10-06, written by a design panel (three independent designs, protocol, minimal and scoring angles, then a judge who checked them against the protocol and the code and synthesized this spec). Nothing here has been run, and no paid call was made to write it. Section 3.1 is implemented (`src/coelacanth/llm.py`, tests in `tests/test_llm.py`). Repository: `/Users/damir/Desktop/coelacanth`. Commands run from the repo root as `PYTHONPATH=src ~/.venvs/rediscovery-layer/bin/python -m ...`.

## 0. Basis

**Base design: the minimal design.** Every system is one adapter with one contract. Each adapter returns a dict shaped like the CLI's JSON output, so `pricing.cli_cost` prices it and `llm.run_jobs` books it, with the ledger, projected-spend stop and low-credit stop it already has. Retrieval for the systems we run is one OpenAlex search, bound per item to the date filter and the exclusions. A separate free step, `resolve`, handles resolution, dating and leakage after a run.

**Grafted from the protocol design:**
- the verdict is derived after leakage cleaning, so a leaked or excluded work can never produce "already done";
- canonical config files, whose SHA-256 is the plain file hash;
- a fixed status taxonomy;
- a sentinel test proving that nothing but `{text, date}` reaches any prompt;
- exclusion matching in a canonical form;
- resolution cost is booked to the harness, never to a system's cap;
- the Hunch brief keeps its headings but has a neutral Study line;
- pilot items are LABELING.md worked examples, picked by a rule fixed in advance.

**Grafted from the scoring design:**
- an `outcome` hook in `run_jobs`, so a cap stop is a counted item failure and does not abort the run;
- `tokens_all` summed over `modelUsage`;
- declared auxiliary models;
- the `eval/results/measurement-a/` layout that `paper/ecir2027/OUTLINE.md` already names;
- `rank_raw` / `rank_clean` and per-evidence dates in the records;
- free gold-hiding and resolver measurements before any run;
- model free text (answers, "why" sentences) stays out of git.

**Kept from the minimal design itself:**
- resume never re-runs a system failure;
- the single agent's skeleton condition is flagged as a protocol inconsistency (point 8 in section 13);
- the 8-word overlap test guards the clean-room prompts;
- Theorizer's knowledge-cutoff setting is tried as a date filter.

**Dropped:**

| Dropped | From | Why |
|---|---|---|
| hash-chained freeze log | protocol design | the repo is private until 30 Oct; an append-only file committed and pushed gives the same evidence |
| sealed runs | protocol and scoring designs | decision 3 permits them but does not require them. H and P40 exist only after 19 Oct. The harness instead refuses every benchmark item before the items freeze, which is stricter than the rule |
| a separate OpenAlex `on_charge` hook | protocol design | per-item OpenAlex cost is counted at list price inside the bound search (an upper bound, independent of cache state); real charges stay in the SQLite ledger |
| cross-system identity clustering and the metric scorer | scoring design | outside two days. The metric code (`score.py`) is separate work due before the 12 Oct rules freeze |
| six-class labels for coarse systems | minimal design | they fabricate labels; coarse systems enter only the three-class metrics |

## 1. Scope and estimate

**In the two-day build** (one engineer, standard library only, no new dependency):
1. Surgical changes to `src/coelacanth/llm.py` (section 3).
2. A new package `src/coelacanth/harness/` (section 4).
3. The closed-pool export `eval/results/measurement-a/scripts/kulikowski_rank.py`.
4. Offline tests (section 11).

**Not in the two days:**

| Work | Why it is outside | When |
|---|---|---|
| INC-style, OpenNovelty-style and Idea-Catalyst-style step prompts | clean-room prompt writing in a separate session from the papers only (decision 11). The harness ships the framework they plug into | 9 to 14 Oct |
| Theorizer and Owl adapters | new dependencies (asta CLI, edison-client) and logins need Damir's approval | after approval |
| reference pipeline | not built; its adapter interface is fixed here | readiness on 19 Oct (decision 13) |
| `score.py` (section 7 metrics) | method-prereg item 3 puts metric code into the 12 Oct rules freeze | before 12 Oct |
| drawing H, P40, H_half, P20 | the 19 Oct freeze tool; the harness only reads its output (section 8.3) | 19 Oct |

**Cut order if the two days run short:** `pool.to_batch` first, then the tier-order check, then the AP print in the Kulikowski script.

## 2. Facts in the existing code this spec relies on

| # | Fact | Where | Consequence |
|---|---|---|---|
| F1 | `run_jobs` books `input_tokens` and `output_tokens` from the top-level `usage` block, which covers the main loop only. One probe on 6 Oct: 5,387 vs 15,762 input tokens | `llm.py` ledger row; `pricing.cli_cost` docstring; `docs/decisions.md` 2026-10-06 | the cost table would undercount every agent |
| F2 | a `cli_call` timeout returns no `modelUsage`, and `run_jobs` then books `total_cost_usd or 0.0`, i.e. $0 | `llm.py` `cli_call` TimeoutExpired branch; `run_jobs` KeyError branch | money spent is booked as zero |
| F3 | a `--max-budget-usd` stop returns `is_error: true` with no answer (6 Oct: single $0.406934, hunch $0.4121755 against a $0.40 cap). It counts toward `MAX_CONSECUTIVE_ERRORS = 3` | `eval/results/ai-origin-prior-art/ledger.jsonl`; `run_jobs` | three capped items in a row abort a run; a capped item must be a counted failure instead |
| F4 | `expected_model` requires `sorted(modelUsage) == [model]` | `run_jobs` | an auxiliary model used by a web tool under Sonnet 5.5 would fail every agent call. Not observed yet (the pilot was Haiku only) |
| F5 | `priorart.run` and `closedbook.run` re-run every `is_error` row on resume | their `done` sets | copied as is, system failures would be resampled until success |
| F6 | `priorart.user_message` sends `method_text` (first line `Field: ...`). Hunch's brief says "a benchmark of cross-field rediscoveries" and "proposed as new by an AI system". `CONTRACT` speaks of "the method" | `priorart.py` | not reusable for benchmark items (SCHEMA rule 11, league equality). `SINGLE_SYSTEM`, `hunch_system()`, `prior_art_system()`, `_git_show`'s SHA-256 check and `parse_candidates` are reusable |
| F7 | `cli_call` has no `--max-turns` and always uses `--output-format json` | `cli_call` | no turn limit, and no record of fetched URLs (evidence) |
| F8 | `b01-meta.json` exclusions come in five forms: W IDs, DOI URLs in mixed case, bare DOIs, a handle.net URL and another web URL. `normalize_work_id` raises `ValueError` on non-DOI URLs | `eval/benchmark/v0/batches/b01-meta.json`; `openalex.normalize_work_id` | exclusion matching needs its own canonical form |
| F9 | `TestRule11SystemView` checks the view dict only, never a prompt | `tests/test_items.py` | no test yet proves that nothing else reaches a model |
| F10 | Kulikowski, two TF-IDF settings. `held_out_family.py` fits a plain `TfidfVectorizer()` on `R.faceted_text("skeletons_faceted_haiku", i)`, giving P@1 0.826 and MRR 0.870. `R.vectors(texts, "tfidf")` (`embed.embed_texts`: stop words, sublinear tf, unigrams and bigrams), used by `p1mrr_boot.perq` and our `abstract_drift_diagnostic.per_query`, gives P@1 0.870 and MRR 0.912 on the whole fingerprint | `data/raw/kulikowski-spdf/src/`; baseline README | the export must implement both settings |
| F11 | `OpenAlexClient` raises `BudgetExceeded` past `daily_budget_usd` ($1 with a key) | `openalex.py` `_get` | three re-implementations at about 30 searches per item pass the free allowance within a day |
| F12 | `llm.key_problem` refuses `sk-ant-oat` subscription tokens; runs use API-key credit only | `llm.py` | every harness dollar is Anthropic API credit |

## 3. Changes to existing code

### 3.1 `src/coelacanth/llm.py`

Every change keeps today's behaviour when the new arguments are left at their defaults, so `closedbook` and `priorart` are unaffected.

```python
def cli_call(model: str, message: str, *, system: str, effort: str | None = None, cwd: str | None = None,
             timeout: float = 900, max_budget_usd: float = 1.0, tools: tuple[str, ...] = (),
             max_turns: int | None = None, trace: Path | None = None) -> dict
```

- `max_turns` adds `--max-turns N`.
- `trace` replaces `--output-format json` with `--output-format stream-json --verbose`. Every stdout line is written to `trace`, and the last event with `"type": "result"` is returned with today's extra fields (`returncode`, `stderr`, `wall_seconds`). With no result event, it returns `{"is_error": True, "result": None}`.
- On `TimeoutExpired` it adds `"timed_out": True`.

```python
def usage_totals(out: Mapping) -> dict[str, int]
# {"input", "output", "cache_read", "cache_creation"}, summed over modelUsage; falls back to `usage`.

def run_jobs(jobs, call, save, *, ledger_path, cap_usd, workers=3, describe=lambda job: {},
             expected_model: Callable[[Any], str | tuple[str, ...]] | None = None,
             outcome: Callable[[Any, dict], str] | None = None) -> dict
```

- **`expected_model` may return a tuple `(main, *declared_aux)`.** A call is a mismatch when `main` is missing from `modelUsage` or any model outside the tuple appears. A plain string keeps today's equality check.
- **`outcome(job, out)` returns `"ok"`, `"system_failure"` or `"infra_error"`.** Only `"infra_error"` counts toward `MAX_CONSECUTIVE_ERRORS`. Without `outcome`, today's rule applies.
- **Cost when `modelUsage` is missing:** if `out["booked_cost"] = {"usd": x, "source": "upper_bound" | "service_reported"}` is present, it is booked before the `total_cost_usd` fallback.
- **New ledger fields:** `tokens_all` (from `usage_totals`), `web_searches` (`pricing.web_searches`), `num_turns`, `subtype`, `wall_seconds`, `timed_out`. Existing fields are unchanged; `input_tokens` and the other top-level fields are documented as main loop only.

### 3.2 No other module changes

`openalex.py`, `priorart.py`, `items.py`, `pricing.py`, `quotes.py` and `texts.py` are imported, not edited.

## 4. New package `src/coelacanth/harness/`

| Module | Purpose | Key functions | Approx. lines |
|---|---|---|---|
| `config.py` | Loads canonical `config.json` files. Recomputes prompt and code hashes, checks leagues and label maps, and appends to the hash log | `canonical_bytes(cfg) -> bytes`; `load(system) -> tuple[dict, str]`; `prompt_hashes(cfg) -> dict[str, str]`; `code_hashes(cfg) -> dict[str, str]`; `problems(cfg) -> list[str]`; `league_problems(cfgs) -> list[str]`; `log(system) -> dict`; `frozen(system) -> tuple[dict, str]` | 130 |
| `run.py` | Builds the view, runs the preflight guards and the call wrapper (decline retry, status), and drives `llm.run_jobs`. Also handles pending items, `save` (no network) and the run manifest | `view(item, condition) -> dict`; `view_sha256(view) -> str`; `preflight(spec) -> list[str]`; `classify(out, cfg) -> str`; `pending(run_dir, item_ids) -> list[str]`; `run(spec) -> dict` | 230 |
| `agents.py` | The Claude Code agent league: briefs, one tool-enabled call, trace parsing, candidate parsing | `brief(cfg, view) -> str`; `run_agent(view, cfg, ctx) -> dict`; `trace_events(path) -> dict` | 120 |
| `retrieval.py` | Exclusion canonical form, and the bound OpenAlex search shared by the re-implementations and the pipeline | `canonical_id(value) -> str`; `exclusion_keys(exclusions, client) -> frozenset[str]`; `bound_search(client, *, claim_date, exclusions, counts) -> Search` | 90 |
| `reimpl.py` | Framework for the three re-implementations: the item budget, one tool-free LLM step, merging sub-calls into one CLI-shaped dict, JSON parsing | `class ItemBudget`; `llm_step(cfg, step, fields, budget, ctx) -> tuple[Any, dict]`; `merge(outs) -> dict`; `parse_json(text) -> Any` | 100 |
| `resolve.py` | After a run: resolution, earliest-version dating, the leakage rule, cleaning, label mapping, `resolved.jsonl` | `resolve_candidate(c, client) -> dict`; `earliest_date(work, client) -> tuple[str, str]`; `leak(c, claim_date, keys) -> str`; `date_evidence(e, client) -> dict`; `clean(row, item, client) -> dict`; `resolve_run(run_dir) -> dict` | 220 |
| `labels.py` | Label mapping fixed in the config; three-class map of protocol section 7 | `THREE_CLASS`; `map_item(cfg, native_label, cleaned) -> dict` | 60 |
| `pool.py` | Pooling round export, blind to system and rank; `found_by` tags; seeded sample of automatic exclusions | `found_by(system, condition, rank) -> str`; `build_pool(round, run_dirs, items, *, depth, skeleton_depth, closed_book_works) -> dict`; `exclusion_sample(pool, n=20, seed=20261006) -> list`; `to_batch(pool) -> tuple[dict, dict]` (optional) | 150 |
| `budget.py` | Line caps, the $20 approval rule, tier order, skeleton funding, pilot projections | `line_spent(line) -> float`; `check(spec, cfg, budget) -> list[str]`; `project(pilot_dirs, plan) -> dict` | 110 |
| `refpipe.py` | Interface only: `run(view, cfg, ctx)` raises `NotReady` until the pipeline exists; `rank_closed_pool(query_text, candidates)` signature fixed | | 20 |
| `__main__.py` | CLI: `pilot`, `hash`, `run`, `resolve`, `pool`, `project`, `status` | `main(argv) -> int` | 80 |

## 5. Adapter contract

```python
View = dict  # exactly {"text": str, "date": "YYYY-MM-DD"}, from items.system_view or items.skeleton_view

@dataclass(frozen=True)
class ItemCtx:
    task: str                                   # "job-<n>", run-local; never the item ID
    cwd: str                                    # fresh temporary directory per item
    cap_usd: float                              # the config's per-item dollar cap
    search: Callable[..., list[dict]] | None    # retrieval.bound_search for systems we run; None otherwise
    log_call: Callable[[dict], None]            # appends one sub-call row to calls.jsonl; the harness adds the item ID
    trace_path: Path | None                     # cache/runs/<run_id>/<task>.jsonl (gitignored)

Adapter = Callable[[View, Mapping, ItemCtx], dict]
ADAPTERS = {"agent": agents.run_agent, "inc_style": ..., "opennovelty_style": ...,
            "ideacatalyst_style": ..., "refpipe": refpipe.run}
```

What an adapter returns (CLI-shaped, so `cli_cost` and `run_jobs` need no special case):

```json
{"is_error": false, "result": "<final answer text or null>", "subtype": "<CLI subtype or null>", "num_turns": 9,
 "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
                                       "cacheCreationInputTokens": 0, "webSearchRequests": 3}},
 "usage": {}, "total_cost_usd": 0.31, "wall_seconds": 74.2, "timed_out": false, "booked_cost": null,
 "native": {"label": null, "probability": null,
            "candidates": [{"rank": 1, "title": "...", "authors": "...", "year": 1963, "venue": null, "doi": null,
                            "url": null, "openalex": null, "publication_date": null, "field": null,
                            "relation": "specialization", "score": null}],
            "evidence": [{"kind": "fetched_page", "url": "https://...", "openalex": null, "for_rank": null}],
            "searches": ["<query>"], "fetches": 3, "llm_calls": 1,
            "openalex": {"search": 0, "list": 0, "entity": 0}}}
```

Adapter rules:
- Adapters are module-level functions of `(view, cfg, ctx)`.
- Their only outside calls are `llm.cli_call` (through `agents.run_agent` or `reimpl.llm_step`) and `ctx.search`.
- They never read item files.
- Answers and "why" sentences go to `cache/runs/` only.

## 6. Run flow and statuses

```bash
python -m coelacanth.harness pilot   --system single-agent --cap 6
python -m coelacanth.harness hash    --system single-agent
python -m coelacanth.harness run     --system single-agent --subset H --tier T1 --condition text --cap 14
python -m coelacanth.harness resolve eval/results/measurement-a/runs/single-agent/text/<run_id>
python -m coelacanth.harness pool    --round 1
python -m coelacanth.harness project
python -m coelacanth.harness status  eval/results/measurement-a/runs/single-agent/text/<run_id>
```

`run(spec)` follows these steps:
1. `preflight(spec)`; any problem exits non-zero.
2. Jobs are the pending items.
3. `call(job)` builds the view and the `ItemCtx`, then calls the adapter. On a decline (`llm.DECLINED`) it retries once with the identical input and merges both attempts (`attempts: 2`). It sets the status with `classify`. On a timeout without `modelUsage` it sets `booked_cost = {"usd": cap, "source": "upper_bound"}`.
4. `llm.run_jobs` runs with `expected_model = (cfg.model, *cfg.aux_models)`, `outcome` from the table below, `workers` of at most 4 (Damir's limit), and `cap_usd` equal to the approved run cap.
5. `save` appends to `outputs.jsonl` and writes the raw answer to `cache/runs/`. It makes no network call, because `run_jobs` holds its lock while `save` runs.
6. `run.json` records the counts per status.

**Statuses:**

| Status | When | `outcome` | Failure in scoring | Re-run on resume |
|---|---|---|---|---|
| `ok` | not `is_error`, and JSON parsed (an empty list is valid) | ok | no | no |
| `unparsed` | not `is_error`, but `parse_candidates` returns `None` or the adapter's parse fails | system_failure | yes | no |
| `capped` | CLI budget stop, or `ItemBudget` exhausted before a needed step. The stop subtype is pinned in `cfg.cli.stop_subtypes` after the first pilot call; until then: `is_error`, empty result and cost at least 0.95 of the call cap | system_failure | yes | no |
| `turn_limit` | the turn-limit subtype pinned in config | system_failure | yes | no |
| `timeout` | `timed_out`; booked at the call cap as `upper_bound` | system_failure | yes | no |
| `declined` | `DECLINED` matches on the first attempt and on the identical retry | system_failure | yes | no |
| `error` | any other `is_error` with an answer or spend | system_failure | yes | no |
| `model_mismatch` | a model outside `(main, *aux_models)` answered | infra_error | yes until re-run | yes |
| `infra_error` | any of: `LOW_CREDIT`; CLI exit without `modelUsage` and with $0 spend; OpenAlex `BudgetExceeded` or `OpenAlexError`; a service 5xx before the task ran | infra_error | yes until re-run | yes |

`pending(run_dir, ids)` returns the items that have no row, or whose last row has status `infra_error` or `model_mismatch`. A system failure is never resampled.

**Preflight guards** (`run.preflight`, plus `budget.check` and `config.frozen`):
- **Kind `pilot`.** Items come only from `eval/results/measurement-a/pilot-items.jsonl`. Each has `provenance.source == "guide"` and `status == "excluded"`. No ID or source DOI of theirs appears in `eval/benchmark/v0/items.jsonl` or `eval/benchmark/v0/batches/*.json`. The config need not be logged yet, because pilots come before the hash.
- **Kind `scored`**, every condition must hold:
  - `eval/results/splits/splits-v1.json` exists and passes `SHA256SUMS`;
  - its `items_sha256` equals the SHA-256 of `eval/benchmark/v0/items.jsonl`;
  - it records `refpipe_readiness`;
  - the subset is in `cfg.allowed_subsets` and in the splits file;
  - the config is logged and unchanged (`config.frozen`);
  - `cfg.models_called` is a subset of `splits.models_for_H`, unless the models are `undisclosed`;
  - `llm.cli_version()` equals `cfg.cli.version`;
  - `budget.check` passes.
- **Every kind:** `llm.auth_problem()` is None.

## 7. Resolution, leakage and labels (`resolve.py`, `labels.py`)

`resolve_run(run_dir)` runs after a run, with a cached `OpenAlexClient`. Its OpenAlex calls go to `resolve-calls.jsonl` and are booked to the harness, never to a system's cap.

**1. Resolve each candidate.** The first route that applies wins:

| Order | Route | Cost |
|---|---|---|
| 1 | `openalex` W ID: `client.work` | free |
| 2 | DOI in any form, including a doi.org URL: `client.work(normalize_work_id(...))` | free |
| 3 | arXiv ID (from the field, a `10.48550/arxiv.` DOI, or the URL via `texts.ARXIV_RE`): `client.work("arXiv:<id>")`; on `OpenAlexError`, `texts.arxiv_work` | free |
| 4 | `pmid:N`: `client.work` | free |
| 5 | title search: `client.works(search=title, filter="publication_year:<y-1>-<y+1>", per_page=5)` | $0.001 |

A title search is accepted only if exactly one result scores `strict` under `closedbook.match_gold(f"{title} {authors} {year}", brief)`. No strict result means `unresolved`; two or more mean `ambiguous`, which is also unresolved and sent to a person.

**2. Date each resolved work.** Start from OpenAlex `publication_date`. Only when that date is on or after `claim.date` (a potential leak), take the minimum with the arXiv v1 date (`texts.arxiv_id(work)`, then `texts.arxiv_work`) and with one exact-title search for an earlier record. The `date_source` is one of `openalex`, `arxiv_v1`, `earlier_record`, `year_only` or `none`.

**3. Classify leakage** (`leak` returns the first reason that applies, else `None`):

| Reason | Condition |
|---|---|
| `excluded_work` | the canonical key is in the item's exclusions |
| `on_or_after_claim_date` | date on or after `claim.date` |
| `year_only_not_before` | only a year is known, and it is at least the claim year (as SCHEMA rule 6) |
| `undated` | no date |

An unresolved candidate whose model-given year is before the claim year stays as a **placeholder**: it keeps its rank, can never be gold and is never pooled. Any other unresolved candidate is a leak (`undated` or `year_only_not_before`).

**4. Clean the list.**
- Leaks are removed. So are duplicates (same W ID, DOI or arXiv ID; the first rank is kept).
- `rank_clean` runs 1, 2, ... over what remains.
- `top10` is the first 10 by `rank_clean`, with placeholders as `null`.

**5. Date the evidence** (`date_evidence`):
- A scholarly URL (doi.org, arxiv.org, openalex.org) is dated by its work's earliest version.
- Any other fetched page is dated by the version read, which is the fetch time, so it is a hit.
- A cited work or a retrieved abstract is dated by its work.
- Search-result lists are stored and not counted (section 13, point 1).

**6. Map the label** (`labels.map_item`):
- `precedence_over_candidates` (agents, pipeline): `items.derive_verdict({"candidates": cleaned})` gives `six`, and `THREE_CLASS[six]` gives `class3`. `six_before_cleaning` is recorded too.
- `native_item_label` (coarse systems): `cfg.label_map.map[native]` gives `{"six": ... or null, "class3": ...}`. An unknown native label gives `mapped: false`; the row is kept and counted.
- `none` (Kulikowski): the system gives no verdict by design. This is not counted as unmappable.
- A failure status gives null labels.

```python
THREE_CLASS = {"equivalent": "already_done", "specialization": "already_done", "generalization": "related",
               "partial": "related", "similar_mechanism": "related", "nothing_close": "nothing_close"}
```

## 8. Records

All paths are under `eval/results/measurement-a/` unless stated.

### 8.1 `configs/<system>.json`

The file must equal `json.dumps(cfg, sort_keys=True, indent=1, ensure_ascii=False) + "\n"` in UTF-8, so `shasum -a 256` gives the logged hash. Zeros are placeholders, set by the pilots before the hash.

```json
{"schema": "coelacanth-system-config/v1", "system": "single-agent",
 "row_label": "Single Claude agent with web search", "disclosure": null,
 "adapter": "agent", "variant": "single", "league": "claude-code-agents",
 "allowed_subsets": ["H", "H_half", "ALL_minus_H"], "conditions": ["text", "skeleton"],
 "model": "claude-sonnet-5-5", "effort": "high", "models_called": ["claude-sonnet-5-5"], "aux_models": [],
 "temperature": "CLI default, not settable",
 "cli": {"version": "2.1.286 (Claude Code)", "stop_subtypes": {"capped": null, "turn_limit": null}},
 "tools": ["WebSearch", "WebFetch"], "max_turns": 0, "search_cap": 0, "fetch_cap": 0,
 "per_item_cap_usd": 0.0, "timeout_s": 1200, "top_k": 10,
 "prompts": {"system": {"source": "coelacanth.priorart.SINGLE_SYSTEM", "sha256": "<hex>"},
             "brief": {"path": "eval/results/measurement-a/prompts/agents/brief-single.txt", "sha256": "<hex>"},
             "contract": {"path": "eval/results/measurement-a/prompts/agents/contract.txt", "sha256": "<hex>"}},
 "answer_after_n_searches": "first sentence of contract.txt",
 "retrieval": {"backend": "claude_code_web", "date_filter": "contract sentence; post hoc in resolve",
               "exclusions": "post hoc in resolve"},
 "native": {"kind": "per_candidate_relation",
            "labels": ["equivalent", "specialization", "generalization", "partial", "similar_mechanism", "different"]},
 "label_map": {"rule": "precedence_over_candidates", "empty_list": "nothing_close", "no_json": "unparsed"},
 "probability": null, "line": "llm_baselines", "price_date": "2026-10-06",
 "tuning": {"items": ["guide-S1", "guide-E1"],
            "iterations": [{"n": 1, "date": "YYYY-MM-DD", "change": "...", "reason": "cost only"}],
            "complete": false}}
```

Variants:

| System or kind | Changes from the example |
|---|---|
| Hunch | `"allowed_subsets": ["P40", "P20"]`; `"pinned": {"repo": "github.com/agentmash-dev/agentmash-crew", "commit": "2ae56e145be0e2fa2eba744296edbe8bcd5342a3", "files": {"lab/agents/hunch-hypotheses.md": "711b3dec...", "lab/skills/lab-protocol/SKILL.md": "70f71a94..."}, "archive": "<DOI>"}`; `"disclosure": "<protocol section 6 text>"`; `prompts.system.source` is `coelacanth.priorart.hunch_system` |
| Prior-art skill | `"pinned": {"source": "github.com/rianczerwinski/prior-art-skill", "cache": "cache/fulltext/prior-art-skill-czerwinski-2026.txt", "sha256": "<hex>"}`; `prompts.system.source` is `coelacanth.priorart.prior_art_system` |
| Budget-matched single agent | `"system": "single-agent-matched"`, `"league": null`, `"line": "refpipe"`, `per_item_cap_usd` = the pipeline's pilot mean cost per item |
| Re-implementations | `"adapter": "inc_style"` (or the other two); `"league": "reimpl"`; `"tools": []`; `"retrieval": {"backend": "openalex", "filter": "to_publication_date: claim.date minus 1 day", "per_page": 25, "max_searches": 0, "exclusions": "dropped in code before any LLM step"}`; `"steps": [{"name": "...", "prompt": {"path": "...", "sha256": "..."}}]`; `"clean_room": {"paper": "arXiv 2506.22026, CC BY 4.0", "sections": [], "repo_used": false, "note": "prompts/CLEAN-ROOM.md"}`; `"primad": ["I'", "D' Sonnet 5.5", "P' OpenAlex"]`; `"native": {"kind": "item_label", "labels": [...]}`; `"label_map": {"rule": "native_item_label", "map": {...}}` |
| Services | `"external": {"service": "...", "client_version": "...", "payload_template": {"path": "...", "sha256": "..."}}`; `"models_called": "undisclosed"`; `"post_cutoff_assumed": "2026-08-01"` |
| Kulikowski | `"adapter": "closed_pool"`; `"allowed_subsets": ["K"]`; `"package": {"tag": "v1.0.0", "commit": "ef1ebe7fe69f2550bc246d19a7728452a04ad302"}`; `"tfidf": "unigram or headline, fixed before P2"`; `"label_map": {"rule": "none"}`; `"line": "free"` |

**League checks** (`config.league_problems`):
- **Agents** must be equal on: `model`, `effort`, `aux_models`, `tools`, `max_turns`, `search_cap`, `fetch_cap`, `per_item_cap_usd`, `timeout_s`, `top_k`, `prompts.contract.sha256` and `cli.version`.
- **Re-implementations** must be equal on: `model`, `effort`, the whole `retrieval` block and `cli.version`.

### 8.2 `config-hashes.jsonl`

Append-only, committed and pushed before the first scored run.

```json
{"at": "2026-10-18T09:00:00+00:00", "system": "single-agent", "config_sha256": "<hex>",
 "prompts": {"system": "<hex>", "brief": "<hex>", "contract": "<hex>"},
 "code": {"src/coelacanth/llm.py": "<hex>", "src/coelacanth/pricing.py": "<hex>", "src/coelacanth/priorart.py": "<hex>",
          "src/coelacanth/harness/run.py": "<hex>", "src/coelacanth/harness/agents.py": "<hex>"},
 "git_commit": "<sha>", "cli_version": "2.1.286 (Claude Code)"}
```

**What `code` covers:** the modules that shape a system's inputs and outputs. For agents that is `llm.py`, `pricing.py`, `priorart.py`, `harness/run.py` and `harness/agents.py`. Re-implementations add `harness/retrieval.py`, `harness/reimpl.py` and their own module. `resolve.py`, `labels.py` and `pool.py` belong to the 12 Oct rules freeze instead.

**When `config.log` refuses:**
- `config.problems` or `league_problems` is non-empty;
- a hashed file has uncommitted changes (`git status --porcelain`);
- scored outputs already exist for the system under another hash.

### 8.3 Splits file the harness reads

Written by the 19 Oct freeze at `eval/results/splits/splits-v1.json`, listed in `eval/results/splits/SHA256SUMS`.

```json
{"format": "coelacanth-splits/v1", "frozen_at": "2026-10-19T00:00:00+00:00",
 "items_path": "eval/benchmark/v0/items.jsonl", "items_sha256": "<hex>", "seed": 0,
 "refpipe_readiness": "ready", "models_for_H": ["claude-sonnet-5-5"],
 "subsets": {"ALL": [], "H": [], "H_any": [], "H_post": [], "H_half": [], "P40": [], "P20": [],
             "C": [], "A": [], "N": [], "K": []}}
```

`refpipe_readiness` is either `ready` or `not_ready`. The harness derives `ALL_minus_H` itself.

### 8.4 `pilot-items.jsonl`

Two LABELING.md worked examples, in item format. The rule is fixed before any pilot: take the two examples whose claim source work has the fewest OpenAlex citations (free entity lookups), so that famous examples do not understate cost. Tuning also happens only on these two items.

```json
{"id": "guide-S1", "status": "excluded", "exclusion_reason": "guide_example", "provenance": {"source": "guide"},
 "claim": {"text": "<field-native claim, drafted>", "date": "YYYY-MM-DD", "skeleton": "<neutral>"},
 "exclusions": ["<source work ID>"]}
```

### 8.5 `budget.json`

Committed. Damir's words are recorded in `approvals`.

```json
{"format": "coelacanth-budget/v1", "price_date": "2026-10-06",
 "lines": {"pilots": 40, "refpipe": 150, "theorizer": 200, "owl": 60, "llm_baselines": 30, "reserve": 20},
 "systems": {"single-agent": "llm_baselines", "prior-art-skill": "llm_baselines", "hunch": "llm_baselines",
             "inc-style": "llm_baselines", "opennovelty-style": "llm_baselines", "ideacatalyst-style": "llm_baselines",
             "single-agent-matched": "refpipe", "refpipe-v0": "refpipe", "theorizer": "theorizer", "owl": "owl",
             "kulikowski-fp": "free"},
 "plan": [{"tier": "T1", "system": "single-agent", "subset": "H", "condition": "text", "items": 25,
           "pilot_mean_usd": 0.0, "projected_usd": 0.0}],
 "approvals": [{"at": "...", "kind": "run_over_20", "scope": "single-agent:T1:H:text", "usd": 0.0,
                "from_line": null, "to_line": null, "damir_words": "<quoted from chat>", "recorded_by": "claude"}]}
```

Approval `kind` is one of `run_over_20`, `line_move`, `theorizer_past_200` or `dependency`.

**`budget.check` refuses a run when:**
- its cap is above $20 and no `run_over_20` approval matches its scope;
- its cap is above the line cap minus `line_spent`;
- its tier is above a tier whose planned runs lack a finished `run.json`;
- it uses the `skeleton` condition and that `(system, tier, condition)` is not in `plan`.

`project` reads ledgers only. For each planned row it reports the pilot mean and maximum dollars per item, `tokens_all`, web searches, seconds, and whether each pilot item ended `ok` within its cap. It computes `projected = mean x 1.5 x items` and writes `projection.json`.

### 8.6 `outputs.jsonl`

One row per item and run, failures included, committed. It holds no model free text.

```json
{"format": "coelacanth-system-output/v1", "run_id": "single-agent.text.T1.20261020T081200Z",
 "system": "single-agent", "condition": "text", "tier": "T1", "subset": "H", "kind": "scored",
 "item": "cf-0007", "task": "job-7", "claim_date": "2019-03-14", "config_sha256": "<hex>", "view_sha256": "<hex>",
 "at": "...", "cli": "2.1.286 (Claude Code)",
 "status": "ok", "failure": false, "attempts": 1,
 "native": {"label": null, "probability": null,
            "candidates": [{"rank": 1, "title": "...", "authors": "...", "year": 1963, "venue": null, "doi": null,
                            "url": null, "openalex": null, "publication_date": null, "field": null,
                            "relation": "specialization"}],
            "evidence": [{"kind": "fetched_page", "url": "https://...", "openalex": null, "for_rank": null}],
            "searches": 3, "fetches": 3},
 "caps": {"usd_item": 0.4, "max_turns": 20, "search_cap": 3, "fetch_cap": 3,
          "searches_exceeded": false, "fetches_exceeded": false},
 "cost": {"usd": 0.3121, "cost_source": "usage_at_official_prices",
          "tokens_all": {"input": 15762, "output": 1349, "cache_read": 7808, "cache_creation": 12552},
          "by_model": {"claude-sonnet-5-5": {"usd": 0.2821, "web_searches": 3}},
          "web_searches": 3, "llm_calls": 1, "openalex_nominal": {"search": 0, "list": 0, "usd": 0.0},
          "num_turns": 9, "seconds": 74.2},
 "raw": {"answer_sha256": "<hex>", "trace_path": "cache/runs/<run_id>/job-7.jsonl", "trace_sha256": "<hex>"}}
```

### 8.7 `ledger.jsonl` and `calls.jsonl`

`ledger.jsonl` has one row per item job, written by `run_jobs`. It keeps today's fields and adds:

```json
{"run_id": "...", "system": "...", "condition": "text", "tier": "T1", "subset": "H", "item": "cf-0007",
 "kind": "scored", "config_sha256": "<hex>", "model": "claude-sonnet-5-5",
 "tokens_all": {"input": 15762, "output": 1349, "cache_read": 7808, "cache_creation": 12552},
 "web_searches": 3, "num_turns": 9, "subtype": "<raw>", "wall_seconds": 74.2, "timed_out": false}
```

`calls.jsonl` has one row per sub-call of a multi-step system, an OpenAlex search, or a resolver call (`resolve-calls.jsonl`). Together with the ledger, every call is logged with tokens and dollars:

```json
{"at": "...", "run_id": "...", "item": "cf-0007", "step": "inc.rerank", "kind": "llm", "model": "claude-sonnet-5-5",
 "cost_usd": 0.012, "tokens_all": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
 "budget_left_usd": 0.31, "is_error": false}
```

### 8.8 `resolved.jsonl`

Written by `resolve_run`. Scoring and pooling read it.

```json
{"format": "coelacanth-resolved/v1", "run_id": "...", "system": "...", "condition": "text", "item": "cf-0007",
 "claim_date": "2019-03-14", "config_sha256": "<hex>", "status": "ok", "failure": false,
 "candidates": [{"rank_raw": 1, "openalex": "W123", "doi": null, "arxiv": null,
                 "resolved_by": "doi", "date": "1963-05-01", "date_source": "openalex",
                 "relation_native": "specialization", "leak": null, "duplicate_of": null,
                 "placeholder": false, "rank_clean": 1}],
 "top10": ["W123", null],
 "evidence": [{"kind": "fetched_page", "url": "https://en.wikipedia.org/...", "openalex": null,
               "date": "2026-10-20", "date_source": "version_read", "leak": "on_or_after_claim_date"}],
 "leakage": {"hit_top10": false, "hit_evidence": true, "n_hits": 1, "n_undated": 0, "n_excluded": 0},
 "verdict": {"rule": "precedence_over_candidates", "native": null, "six": "specialization",
             "class3": "already_done", "mapped": true, "six_before_cleaning": "specialization"},
 "probability": null, "resolver": {"title_searches": 0, "usd_nominal": 0.0}}
```

Allowed values:
- `resolved_by`: `openalex_id`, `doi`, `arxiv`, `pmid`, `title_search` or `none`;
- `date_source` (candidates): `openalex`, `arxiv_v1`, `earlier_record`, `year_only` or `none`;
- `leak`: null, `on_or_after_claim_date`, `year_only_not_before`, `undated` or `excluded_work`;
- `verdict.rule`: `precedence_over_candidates`, `native_item_label` or `none`.

### 8.9 `run.json`

```json
{"format": "coelacanth-run/v1", "run_id": "...", "system": "...", "condition": "text", "tier": "T1", "subset": "H",
 "kind": "scored", "item_ids": [], "config_sha256": "<hex>", "splits_sha256": "<hex>", "line": "llm_baselines",
 "cap_usd": 14.0, "cli": "...", "git_commit": "<sha>", "started": "...", "finished": "...",
 "summary": {"items": 25, "by_status": {"ok": 22, "capped": 2, "unparsed": 1}, "spent_usd": 9.1, "aborted": null}}
```

### 8.10 Pool round

- **`pool/round<k>/pool.jsonl`**, what judges get. Each row is `{"item": "cf-0007", "work": <SCHEMA work object from texts.work_object, cache_key from texts.cache_abstract>}`. Rows are sorted by OpenAlex ID within each item. There is no system, rank, count, relation, rationale or H membership.
- **`pool-meta.jsonl`**, hidden from judges: `{"item": "cf-0007", "openalex": "W1", "found_by": ["sys:single-agent@2", "sys:inc-style.skeleton@7", "mining:closedbook"], "matched_existing_candidate": null}`.
- **`exclusions.jsonl`**: `{"item", "system", "condition", "rank_raw", "reason": "leakage|excluded_work|duplicate|unresolved"}`. **`exclusion-sample.json`** holds 20 of them, drawn with `random.Random("20261006:auto_exclusions")`, for a person to check.
- **`pool-manifest.json`**: `{"depth": 10, "skeleton_depth": 10, "runs": [{"run_id", "config_sha256"}], "per_item": {"cf-0007": {"n_pooled": 23, "auto_removed": {"leakage": 4, "duplicate": 2, "unresolved": 1}}}}`.
- **Optional `to_batch`:** the same content in `annotation.py`'s `coelacanth-batch/v1` and `coelacanth-batch-meta/v1` formats, checked by `annotation.check_batch`.

The rank in `found_by` is `rank_clean`; tags match `items.FOUND_BY_RE`. Kulikowski closed-pool rankings are not pooled (section 13, point 9).

### 8.11 Closed-pool output

`closed-pool/kulikowski-fp/<setting>/rankings.jsonl`:

```json
{"format": "coelacanth-closed-pool/v1", "setting": "unigram", "query": "kalman-control-001", "query_openalex": "W3112152739",
 "family": "recursive_bayes_kalman", "field": "control_power_systems",
 "ranking": [{"id": "<package id>", "openalex": "W...", "score": 0.41, "rank": 1}]}
```

The ranking lists every cross-field candidate. OpenAlex IDs come from `data/candidates/kulikowski-papers.jsonl` (`kid` to `openalex`).

### 8.12 Layout

```
eval/results/measurement-a/
  README.md, budget.json, projection.json, config-hashes.jsonl, pilot-items.jsonl
  configs/<system>.json
  prompts/agents/{brief-single,brief-prior-art-skill,brief-hunch,contract}.txt
  prompts/<reimpl>/*.txt, prompts/CLEAN-ROOM.md
  pilots/<system>/<run_id>/{run.json, outputs.jsonl, ledger.jsonl, calls.jsonl}
  runs/<system>/<condition>/<run_id>/{run.json, outputs.jsonl, ledger.jsonl, calls.jsonl, resolved.jsonl, resolve-calls.jsonl}
  closed-pool/kulikowski-fp/<setting>/rankings.jsonl
  scripts/kulikowski_rank.py
  pool/round<k>/{pool.jsonl, pool-meta.jsonl, exclusions.jsonl, exclusion-sample.json, pool-manifest.json}
eval/results/splits/{splits-v1.json, SHA256SUMS}
cache/runs/<run_id>/<task>.{jsonl,answer.txt}     (gitignored: traces, fetched page text, answers, service JSON)
```

## 9. Systems

| System (id) | Adapter; built in the two days? | Native output | Label rule (fixed in config before the hash) | Leakage handling |
|---|---|---|---|---|
| Reference pipeline (`refpipe-v0`), the authors' own tool | `harness/refpipe.py`, interface only | six relations per candidate, claim chart, probability | `precedence_over_candidates`; probability goes to calibration | `ctx.search` (date filter, exclusions), then `resolve` post hoc. A hit is reported as a harness or dating bug, never hidden |
| Single agent, budget-matched (`single-agent-matched`) | `agent` (variant single); config only | as single agent | as single agent | post hoc |
| INC-style (`inc-style`) | `reimpl` framework now; steps from arXiv 2506.22026 later: keyword queries, `ctx.search`, lexical prefilter (stdlib, replacing the embedding filter: a PRIMAD change), facet re-rank whose top 10 is the list, binary judgment | `novel` or `not novel` | `not novel`: class3 `already_done`, six null. `novel`: `nothing_close`. Anything else is unmappable | `ctx.search` plus post hoc |
| OpenNovelty-style (`opennovelty-style`) | `reimpl` framework now; Apache-2.0 prompts reused with attribution at a pinned commit, retrieval replaced by `ctx.search`, quotes checked by `quotes.QuoteChecker` against `oa-<W>` | `can_refute`, `cannot_refute` or `unclear` per candidate | proposed: any verified `can_refute` gives `already_done` (six null); else any `unclear` gives `related`; else `nothing_close`. An unverified `can_refute` is downgraded to `cannot_refute`, as the paper does. Decide before the hash | `ctx.search` plus post hoc |
| Idea-Catalyst-style (`ideacatalyst-style`) | `reimpl` framework now; steps and native labels from arXiv 2603.12226 only (Appendix E evaluation prompts may be reused with attribution) | per-field judgment of whether the problem is addressed | proposed by strength: fully addressed gives `already_done`, partly gives `related`, not at all gives `nothing_close`; six null; native label names taken from the paper text. Decide before the hash | `ctx.search` (with `field_id`) plus post hoc |
| Kulikowski fingerprint + TF-IDF (`kulikowski-fp`) | `scripts/kulikowski_rank.py`; built | full cross-field ranking with scores | `none` | not applied: section 5's closed pool is date-agnostic |
| Prior-art skill (`prior-art-skill`) | `agent` (variant prior_art_skill); built | six relations per candidate (contract) | `precedence_over_candidates`. No JSON block gives `unparsed`; relations outside the six are null (`parse_candidates`) | post hoc; never told the exclusions |
| Single agent (`single-agent`) | `agent` (variant single); built | as above | as above | post hoc |
| Hunch (`hunch`), the author's own tool | `agent` (variant hunch); built | as above, one-to-one with SCHEMA (disclosed) | as above | post hoc |
| Ai2 Theorizer `evaluate-novelty` (`theorizer`) | after approval of the asta CLI: `asta generate-theories evaluate-novelty @payload.json`, final Task JSON from stdout, raw output in `cache/runs` | `already established`, `derivable from known work` or `genuinely new` per statement | proposed: established gives `already_done`; derivable gives `related` (conservative, because a false already-done is the costliest error; decide before the hash); new gives `nothing_close`. The strongest statement wins | the Knowledge Cutoff (Year/Month) setting set to the claim's month if `evaluate-novelty` accepts it, plus post hoc |
| Owl / Precedent (`owl`) | after approval of edison-client: `TaskRequest(name=JobNames.PRECEDENT, query=template)` with `verbose=True` | answer, `has_successful_answer`, references | `has_successful_answer` false gives unmappable (it flags answer quality, per the 0.16.1 README). Otherwise a regex table on the answer's opening sentence, frozen after the 2 pilot answers. No LLM mapper, because it would add a model to H's union | "before <date>" in the query, plus post hoc |

**Agent briefs** (`agents.brief`) are each a header line from `brief-<variant>.txt`, then the shared block `Date: $date\n\nClaim: $text`, then `contract.txt`. The contract is `priorart.CONTRACT` reworded from "method" to "claim", with `$searches`, `$fetches`, `$k` and `$date` filled from config and view.

| Variant | Header |
|---|---|
| single | "Find the earliest published prior work for this claim." |
| prior_art_skill | "Search prior art for this claim and deliver the novelty boundary." |
| hunch | Goal, Study, Acceptance and Ownership headings. The Study line is neutral ("prior-art check of one claim; this brief is the whole context; you cannot read or write files"), with no cross-field, benchmark, rediscovery or AI-origin wording. Acceptance is steps 1 to 3 only, then the JSON block |

**Agent calls:**
- `cli_call(cfg.model, brief, system=<pinned system prompt>, effort=cfg.effort, cwd=ctx.cwd, tools=("WebSearch", "WebFetch"), max_budget_usd=cfg.per_item_cap_usd, max_turns=cfg.max_turns, timeout=cfg.timeout_s, trace=ctx.trace_path)`.
- Fetched URLs and search queries come from the `tool_use` events in the trace.

## 10. Protocol rules: where enforced, which test proves it

| Rule (source) | Enforced in | Test |
|---|---|---|
| Systems see `claim.text` and `claim.date` only (SCHEMA rule 11; protocol 4) | `run.view` (`items.system_view` / `skeleton_view`); adapters get `(view, cfg, ctx)` only; `ctx` holds no item field | `test_harness_run.TestViewBoundary.test_sentinels_never_reach_any_prompt`: sentinel strings in the skeleton, field, domain, source title, coined names, chart, candidates, exclusions, exposure and ID; agent and fake re-implementation adapters run with `cli_call` and the OpenAlex fetch patched; no sentinel appears in any message, system prompt, query URL or `repr(ctx)` |
| Skeleton only in the ablation, only where a tier funds it (protocol 13; decision 22) | `run.view(condition)`; `budget.check` | `test_harness_budget.test_skeleton_run_refused_without_plan_row`; `test_harness_run.test_text_condition_never_reads_the_skeleton` |
| Exclusions never shown to a model; applied by our code (protocol 4) | `retrieval.exclusion_keys`, `bound_search`; `resolve.leak` | `test_harness_retrieval.test_b01_exclusion_forms_are_all_matched` (W ID, mixed-case DOI URL, bare DOI, handle URL, other URL); covered by the sentinel test |
| Date filter before `claim.date` for systems we run (protocol 4) | `bound_search` adds `to_publication_date:<claim.date minus 1 day>` and drops later results | `test_harness_retrieval.test_filter_is_the_day_before`; `test_same_day_result_dropped` |
| Leakage rule: on or after `claim.date`, undated, year-only, live page by version read, earliest version first (protocol 4) | `resolve.earliest_date`, `leak`, `date_evidence` | `test_harness_resolve`: `test_on_claim_date_is_a_hit`, `test_undated_is_a_hit`, `test_year_only_same_year_is_a_hit`, `test_earlier_arxiv_v1_rescues_a_later_journal_date`, `test_live_page_is_an_evidence_hit` |
| Leakage hits removed before scoring, never finds, counted per system (protocol 7) | `resolve.clean` | `test_hits_removed_and_ranks_reassigned`; `test_leakage_counts_per_row` |
| A leaked or excluded work cannot yield an already-done verdict | `labels.map_item` on the cleaned list | `test_harness_resolve.test_source_work_labelled_equivalent_gives_no_already_done` |
| Duplicates collapse to the first rank; unresolved works are non-gold placeholders, never pooled (protocol 12) | `resolve.clean`; `pool.build_pool` | `test_duplicate_versions_collapse`; `test_unresolved_with_earlier_year_is_placeholder`; `test_harness_pool.test_placeholders_not_pooled_but_counted` |
| Per-item cap; a capped item is a counted failure; the run continues (protocol 6) | `cli_call(max_budget_usd)`; `reimpl.ItemBudget`; `run.classify`; `run_jobs(outcome=...)` | `test_llm.test_system_failures_do_not_stop_the_run`; `test_harness_run.test_budget_stop_is_capped_failure` (fixture from the 6 Oct row); `test_reimpl_steps_share_one_item_cap` |
| Turn limit, timeout and decline handled the same for every system (decisions 6, 12) | `cli_call(max_turns)`; `run.classify`; retry in `run.call` | `test_llm.test_max_turns_flag`; `test_llm.test_timeout_booked_at_upper_bound`; `test_harness_run.test_decline_retried_once_identically_then_failure` |
| No resampling: only infrastructure errors are re-run | `run.pending` | `test_harness_run.test_only_infra_errors_are_pending` |
| Every call logged with dollars and `modelUsage` tokens, including tool sub-calls, plus web searches at $10 per 1,000 (protocol 7, 9) | `llm.usage_totals` and the ledger row; `calls.jsonl`; `pricing.cli_cost` | `test_llm.test_ledger_tokens_come_from_model_usage` (top-level 5,387 vs `modelUsage` 15,762); `test_llm.test_web_searches_booked`; `test_harness_run.test_every_subcall_in_calls_jsonl` |
| Answers from the configured model; auxiliary models only if declared (protocol 3; decision 8) | `run_jobs(expected_model=(main, *aux))` | `test_llm.test_undeclared_aux_model_is_mismatch`; `test_llm.test_declared_aux_model_accepted` |
| `config.json` SHA-256 logged before the first scored run; no silent change (decisions 3, 12) | `config.load`, `log`, `frozen`; `run.preflight` | `test_harness_config.test_hash_is_file_sha256`; `test_non_canonical_file_refused`; `test_prompt_or_code_change_after_log_refused`; `test_log_refused_with_existing_outputs` |
| Pinned third-party prompts byte-identical (protocol 6) | `config.prompt_hashes` through `priorart._git_show` and the skill cache SHA-256 | `test_harness_config.test_pinned_hash_mismatch_refused` (`_git_show` patched) |
| Leagues share everything but the method (protocol 6; decision 10) | `config.league_problems` in `log` and `preflight` | `test_harness_config.test_agent_league_mismatch_refused`; `test_reimpl_league_needs_same_retrieval_and_model`; `test_harness_agents.test_briefs_differ_only_in_header_and_carry_no_cue` |
| Label mapping fixed before runs; unmappable outputs counted (protocol 6) | `cfg.label_map` (hashed); `labels.map_item`; rows kept | `test_harness_resolve.test_unknown_native_label_unmappable_and_kept`; `test_three_class_matches_protocol_7`; `test_harness_config.test_label_map_covers_native_labels` |
| No system under test on a benchmark item before 19 Oct (decision 3; protocol 9) | `run.preflight` requires the splits file and matching hashes; no sealed mode | `test_harness_run.test_scored_run_refused_without_splits`; `test_items_hash_mismatch_refused` |
| Pilots on 2 items outside v0; only cost read (decision 9) | `run.preflight(kind="pilot")`; `budget.project` reads ledgers and statuses only | `test_harness_run.test_pilot_refuses_non_guide_item`; `test_harness_budget.test_projection_reads_no_candidates` |
| Projected cost = pilot mean x 1.5 x items; tiers in order (decision 9) | `budget.project`, `budget.check` | `test_harness_budget.test_projection_formula`; `test_next_tier_refused_before_current_complete` |
| $20 single-run approval; line caps; low credit stops every run (protocol 9) | `budget.check`; `run_jobs` `LOW_CREDIT` | `test_harness_budget.test_run_over_20_needs_approval`; `test_line_remainder_enforced`; existing `test_llm.test_stops_at_once_on_a_low_credit_balance` |
| Theorizer, Owl and Hunch only on P40 or P20; Kulikowski only on K (decision 2; protocol 5) | `cfg.allowed_subsets` in `run.preflight` | `test_harness_run.test_subset_not_allowed_refused` |
| H uses the models in frozen configs (decision 8) | `run.preflight`: `models_called` within `splits.models_for_H` | `test_harness_run.test_model_outside_models_for_H_refused` |
| Pipeline readiness before checker results (decision 13) | `run.preflight` requires `refpipe_readiness` in the splits file | `test_harness_run.test_scored_run_refused_without_readiness` |
| Tuning outside v0, iterations recorded (decision 12) | `config.problems`: tuning items must be pilot items; `tuning.complete` true before `log` | `test_harness_config.test_tuning_on_non_pilot_item_refused` |
| Pool: top 10 cleaned per system and condition, `found_by` format, blind, OpenAlex-ID order, automatic exclusions counted, 20 sampled (protocol 12; decision 17) | `pool.build_pool`, `found_by`, `exclusion_sample` | `test_harness_pool`: `test_top10_of_each_run_and_condition`, `test_found_by_matches_FOUND_BY_RE`, `test_judge_file_has_no_system_rank_or_rationale`, `test_sorted_by_openalex_id`, `test_pilot_and_unlogged_runs_refused`, `test_merged_item_passes_validate_rule_10` |
| Every item has an output row, failures included (protocol 6) | `run.save`; `run.json` summary | `test_harness_run.test_failed_items_keep_rows_and_counts` |
| Clean-room prompts for INC-style and Idea-Catalyst-style (decision 11) | `prompts/CLEAN-ROOM.md`; prompt files | `test_harness_cleanroom.test_no_8_word_span_shared_with_cached_repo_prompts` (the test reads `cache/fulltext/ai2-inc-gh-utils-prompts.txt` and `ideacatalyst-code-prompts.txt`; the prompt writer never does; skipped while no prompt exists) |
| Third-party raw content and model free text never committed (SCHEMA licences; decision 26) | `run.save` writes raw content to `cache/runs/`; outputs hold hashes | `test_harness_run.test_outputs_hold_no_answer_why_or_page_text` |
| CLI version fixed (decision 12) | `run.preflight` | `test_harness_run.test_cli_version_mismatch_refused` |
| Kulikowski reproduction (protocol 5) | `kulikowski_rank.py` asserts P@1 and MRR: unigram 0.826 and 0.870, headline 0.870 and 0.912, at three decimals | script self-check run on the package; not a unittest, because `data/raw/` is gitignored |

## 11. Tests

- Style: `unittest` with `mock.patch`, in the manner of `tests/test_llm.py` (`fake_call`) and `tests/test_openalex.py` (`FakeFetch`, temporary `ResponseCache`). No model or network is called.
- Files: `tests/test_llm.py` (extended), `tests/test_harness_config.py`, `test_harness_run.py`, `test_harness_agents.py`, `test_harness_retrieval.py`, `test_harness_resolve.py`, `test_harness_pool.py`, `test_harness_budget.py` and `test_harness_cleanroom.py`.
- Fixtures: a sentinel item; a synthetic stream-json trace with two `WebSearch` and one `WebFetch` `tool_use` (including an encyclopedia URL); CLI outputs copied from the 6 Oct ledger shapes; a fake OpenAlex fetch returning a later journal record with an earlier arXiv v1.
- Run all: `PYTHONPATH=src ~/.venvs/rediscovery-layer/bin/python -m unittest discover -s tests -v`.

## 12. Order of work

**Day 1 (Wed 7 Oct), no spend:**
- **Morning:** the `llm.py` changes with their tests; `harness/config.py` with tests.
- **Afternoon:** `harness/run.py` (view, preflight, call wrapper, classify, pending, save, manifest) and `harness/budget.py`, tested with fake adapters. `pilot-items.jsonl`: pick the two worked examples by the citation rule (free lookups), then draft their claim texts and dates.

**Day 2 (Thu 8 Oct), no spend:**
- **Morning:** `harness/agents.py`, the brief and contract files, trace parsing and tests; `harness/retrieval.py` and `harness/reimpl.py` with tests.
- **Afternoon:** `harness/resolve.py` and `harness/labels.py` with tests; `harness/pool.py` with tests; `kulikowski_rank.py`, run once (free, no network, about 40 s).

**After the two days:**

| Date | Work | Spend |
|---|---|---|
| Fri 9 Oct | **Measure first, both free:** (a) the gold-hiding check: for the 110 gold works of `closed-book-second-probe/items.jsonl` and the 87 b01 candidates, count how many have an OpenAlex `publication_date` later than their arXiv v1, so that `bound_search` would hide them; (b) resolver accuracy: resolve those gold works from title and year alone, then report precision, recall and the ambiguous count (about 110 searches, $0.11, inside the free allowance). **Then one paid call** (at most $1) on one pilot item to pin CLI behaviour: is `--max-turns` accepted with `-p`, does the stream-json result carry `modelUsage`, which subtypes mark budget and turn stops, which models appear under Sonnet 5.5 | at most $1.11 |
| 9 to 10 Oct | agent league cap ladder on the 2 pilot items; each run at most $20 unless approved | at most $20 |
| 9 to 11 Oct | `score.py` (separate task); the protocol points of section 13 go to Damir | none |
| Mon 12 Oct | rules freeze: the protocol tag also covers `harness/resolve.py`, `labels.py`, `pool.py` and `score.py` | none |
| 13 to 16 Oct | clean-room step prompts and adapters for the three re-implementations, written from the papers in a separate session; their 2-item pilots. Theorizer and Owl adapters and pilots after Damir approves the dependencies and logs in | pilots line |
| 17 to 18 Oct | `project` produces the table; Damir approves `budget.json`; configs are completed; `hash` logs each one; push | none |
| Mon 19 Oct | readiness, the items and splits freeze, then preflight for T1. First scored runs on 20 Oct, `resolve`, then `pool --round 1` for judging on 20 to 23 Oct | T1 |

## 13. Points Damir must settle in the protocol text before the 12 Oct freeze

1. **Evidence for web agents.** Proposal: fetched pages and cited works count; search-result lists are stored and not counted. Otherwise every web agent leaks on nearly every item.
2. **Dating.** Proposal: a scholarly URL is dated by its work's earliest version; any other live page by the fetch time, so it is a hit.
3. **Unresolved candidates.** Proposal: a placeholder if the model-given year is before the claim year, a leakage hit otherwise; never gold, never pooled.
4. **Year-only dates.** Proposal: a hit when the year is at least the claim year (SCHEMA rule 6 logic).
5. **Verdicts.** Proposal: for per-candidate systems the verdict is derived after cleaning. Item-label systems keep their native label, with a sensitivity line that drops items whose evidence had a leakage or exclusion hit.
6. **Coarse systems.** Proposal: INC-style, OpenNovelty-style, Idea-Catalyst-style, Theorizer and Owl enter the three-class metrics only; the six-class confusion matrix covers six-way systems only.
7. **Failures and declines.** Proposal: system failures (cap, turns, timeout, unparsed, second decline) are final. Only infrastructure errors are re-run. A decline is retried once with the identical prompt (decision 6 extended to systems).
8. **Inconsistency: the single agent's skeleton condition.** Confirmatory test T1 (section 8) averages the vocabulary gap over the re-implemented baselines and the single agent on H. But section 9 and decision 22 fund the single agent's skeleton condition only in T4. Either move it into T3, or drop the single agent from T1.
9. **Kulikowski.** Which TF-IDF setting is P2's comparator (unigram, P@1 0.826, or headline, 0.870)? Closed-book-failed K has no input yet: the 92 queries are papers, not field-native claims. Proposal: Kulikowski rankings are not pooled.
10. **P40 strata** for the 19 Oct draw (proposal: `provenance.source`).
11. **OpenAlex.** A line or an approval for paid searches above the free $1 per day (retrieval by three re-implementations, plus resolution).
12. **Release.** Whether `outputs.jsonl` and `resolved.jsonl` (item IDs with candidate works) ship inside the password-protected archive (decision 26).
13. **Label maps.** OpenNovelty `unclear` and `cannot_refute`; Theorizer `derivable from known work`; Idea-Catalyst's three strengths.
14. **Pool depth.** Whether `found_by` ranks in pooling are cleaned ranks (proposal: yes, because depth applies after leakage removal).

## 14. Risks

- **Money.** The $30 LLM-baselines line does not cover T1 (method-prereg estimates about $80 for the agent league at a $0.40 cap).
  - The API balance was $30 on 6 Oct. Damir's $250 of Claude Code cloud session credits pay for cloud sessions (development work), not for API calls: the harness spends only through `ANTHROPIC_API_KEY`, and `llm.key_problem` refuses `sk-ant-oat` subscription tokens.
  - Theorizer at about $5 to $10 per run puts P40 at or above its $200 line.
- **Unverified CLI behaviour** in 2.1.286: `--max-turns` with `-p`, `modelUsage` on the stream-json result event, the stop subtypes, and whether `--max-budget-usd` uses the CLI's own estimate (one probe: CLI $0.0297 vs ours $0.0167). All are pinned by the first pilot call. Until then, `classify` uses the heuristic above.
- **Auxiliary model under Sonnet 5.5.** If a web tool uses a second model, it must be declared in `aux_models`, and it then joins H's model union. Declare it before 19 Oct, never after.
- **Search caps are instructions only.** The CLI has no hard search cap, and `--safe-mode` disables hooks. Compliance is measured (`searches_exceeded`), and the dollar cap is the only hard stop.
- **Gold hidden by the date filter.** OpenAlex `publication_date` can be a later journal date. The 9 Oct measurement sizes this before any run. Mitigation, if it is large: filter one year later at retrieval and leave exact dating to `resolve` (a pre-hash choice, logged).
- **Clean-room contamination.** `cache/fulltext/` holds repository prompt files, and `research/novelty/threats.md` quotes them. Prompt writers work from the papers in a fresh session; the overlap test is the mechanical guard.
- **Pilot representativeness.** Worked examples are famous and may be answered from memory. The citation rule and the 1.5 margin are the only controls.
- **OpenAlex limits.** `BudgetExceeded` mid-item is an infrastructure error. The item is re-run, but its LLM sub-calls are already paid. Run re-implementations one line at a time, spread over days or with an approved daily budget.
- **Throughput.** Owl was reported at 10 queries per 24 hours (May 2025), and Theorizer takes 30 to 60 minutes per item. P40 runs may slip past judging round 1.
- **Licences.** Asta and Semantic Scholar records, Edison contexts and fetched pages stay in `cache/runs/` (gitignored); only hashes are committed.
- **Effort.** Section 4 adds about 1,300 lines plus about 700 test lines: two full days. The cut order in section 1 applies.

## 15. Amendments after the adversarial review (2026-10-07)

An adversarial review (three lenses, each finding re-checked by a skeptic) confirmed 38 findings, 2 refuted; fixers repaired them in file-disjoint groups, and a second skeptic re-checked every blocker and major. Where this spec and the code now differ, the code and these notes win:
- Statuses: a model mismatch flagged by `run_jobs` is saved as `model_mismatch` (re-run on resume, counts toward the three-in-a-row stop); an exception inside an adapter (OpenAlex `BudgetExceeded` or `OpenAlexError`, anything else) is an `infra_error` row booked at the paid sub-calls in calls.jsonl, and run.json is always written.
- Resume: `pilot` and `run` take `--resume <run_dir>`; a second scored run of the same (system, condition, tier, subset) is refused without it; `status` lists items with no row; a run with infrastructure errors pending does not complete its tier (`budget._finished`).
- The run cap is hard: calls in flight count at their per-call cap (`llm.run_jobs(call_cap_usd=...)`), from the first call; a decline's identical retry gets what the first attempt left of the item cap.
- Items freeze (8.3): the splits file also holds `views_sha256` (each item's text, skeleton and date), and preflight checks the requested items' views, so judgments merged after 19 Oct do not block late runs; `items_sha256` stays as a recorded fact.
- Resolution (section 7): an OpenAlex outage makes the row's resolution an `infra_error` (`resolution_error`), never placeholders or leaks; an unresolved citation of the excluded source work cannot set the verdict; an earlier record re-dates a work only when the first author and initial agree; a non-scholarly page cited as a candidate is dated by the version read; two OpenAlex records linked by dating are collapsed (`same_work`); an OpenAlex `YYYY-01-01` date is year-only, also in `bound_search`; resolved rows carry decision 28's `exposure` from the agent trace.
- Pooling (8.10): rows with a failure status are never pooled; one preprint found by two runs is pooled once.
- Configs: `models_called` must equal the model plus the auxiliary models; the league check also runs at preflight against the logged configs.
- Budget: `budget.json` keeps the approved caps and a `spent_outside_harness` block (closed-book runs, CLI probes, triage, generation), which `budget.check` subtracts; every scored run needs its exact plan row.
- Closed pool: `kulikowski_rank.py` also writes the 5,812 pair scores and their AP (0.557 in the headline setting, as reproduced).
