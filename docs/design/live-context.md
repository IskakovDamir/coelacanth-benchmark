# Live shared context for field specialists (design for protocol v2)

Status: design only, 2026-10-06. Nothing is built. AgentMash was read from `origin/main` at commit `dfdf5a0` (2026-10-06), read-only, and is cited as (AgentMash <file>, dfdf5a0). No AgentMash code is copied: this document describes interfaces and behaviour.

## 1. Purpose and status

This is the design for arm (3) of the ACL 2027 comparison: field specialists that share a live room, so that each one sees what the others have searched, found and taken while it works. It feeds `eval/protocol.md` v2 (section 10 is the placeholder), which is frozen before any ACL run. It is November work (PLAN.md, Phase 3). The ECIR scope does not change (benchmark v0, measurement A, the single-agent pipeline, protocol v1), and nothing here runs before 2 Nov.

## 2. AgentMash as read

**Room**: a shared stream of events about one repository; a reader sees the union of every client's events. The room id (`mash_` plus 16 to 64 URL-safe characters) is the credential and travels in the `x-agentmash-room` header. `AGENTMASH_DISABLE=1` is a kill switch (AgentMash PROTOCOL.md §1, §3, dfdf5a0).

**Event**: one JSON object sent to `POST /events` (protocol 1, frozen). Three fields are required: `developer`, `session_id` and `event_type`. Optional fields include `file_path`, `task_hint` (500 characters) and `change_summary` (300). The types are `edit`, `prompt` and `session_end`. The server stores unknown types but leaves them out of aggregation, and it discards undocumented fields. Delivery is unordered and at most once, and events are kept for 7 days by default. **Claims** are advisory statements of intent: a task, paths and a TTL of 5 to 480 minutes (default 120). Two claims overlap when one path contains the other or when they share a symbol (AgentMash PROTOCOL.md §4, §5, §11, dfdf5a0).

**Injection**: a hook's `additionalContext` enters the agent's context. SessionStart gives a heads-up on teammates. A prompt hook repeats it at most once in 10 minutes, and only if it is news. PreToolUse on edit tools adds an advisory and lets the edit proceed, and it does not repeat that advisory for the same file within 10 minutes. Stop tells the person once per collision (`systemMessage`) and never blocks. A heads-up is at most 500 characters, names at most 3 teammates and quotes at most 120 characters of each prompt (AgentMash hooks/session_start.mjs, prompt_note.mjs, pre_tool_use.mjs, stop.mjs, lib.mjs `NOTE_*`, dfdf5a0).

**MCP tool**: a stdio server over the same HTTP API, for asking while planning (`who_is_working_on_what`, `claim_work`, `list_claims`, `get_collisions` and four more; AgentMash hooks/mcp_server.mjs, dfdf5a0).

**Fail-open**: a client never blocks its host. It always exits 0, prints nothing on the happy path, never retries or spools, and arms an exit timer (AgentMash PROTOCOL.md §6, dfdf5a0). MCP tools do the opposite on purpose: they name every failure (`unreachable`, `timeout`), because an empty answer would read as "nobody is working" (AgentMash hooks/mcp_coordination.mjs, dfdf5a0).

**Time budgets** (dfdf5a0):

| Path | Budget | Source |
|---|---|---|
| PreToolUse advisory | 1500 ms, exit guard 250 ms later; configurable up to 4000 ms | hooks/lib.mjs `ADVISORY_TIMEOUT_MS`, `MAX_ADVISORY_TIMEOUT_MS`; pre_tool_use.mjs |
| Reporting (PostToolUse, prompt) | 2500 ms, async | hooks/lib.mjs `REPORT_TIMEOUT_MS` |
| SessionStart | advisory budget, ended 500 ms before a 4500 ms deadline | hooks/session_start.mjs; lib.mjs `HOOK_DEADLINE_MS` |
| Prompt heads-up | 1500 ms inside a 2500 ms guard; hook timeout 3 s | hooks/prompt_note.mjs |
| Stop; SessionEnd | advisory budget plus a 250 ms guard; a 2500 ms guard and a 4 s timeout | hooks/stop.mjs; session_end.mjs |
| MCP call | 4000 ms | hooks/mcp_coordination.mjs |
| Any hook entry | timeout 5 s | cli/agentmash.mjs |

A cold check on loopback costs about 160 ms (AgentMash README.md, dfdf5a0). AgentMash's hooks fire on edit tools and reason about files, so none fires on a search, and a stock server drops our fields. Coelacanth reuses the room model, the protocol and the behavioural contract, and writes its own clients.

## 3. The mapping

Each benchmark item gets one fresh **room** per arm and repeat, so nothing crosses items. Each agent is a `developer` (`spec:<field>` or `single`).

**Chart**: step 1 (skeletonize) runs once per item, and every arm reuses its output: the system's own chart (element id, text, key flag) and the target fields. "Element" means an element of this system chart. Systems never see the annotators' `claim.chart` (SCHEMA.md rule 11).

**Events**: Coelacanth's fields are optional additions, which protocol 1 allows (AgentMash PROTOCOL.md §8, dfdf5a0).

| `query_issued` | Meaning |
|---|---|
| `agent`, `field` | who searched, and the target field the query is written for |
| `source`, `before` | `openalex`, `s2`, `arxiv` or `pubmed`; the date filter |
| `query` | the text as sent, at most 300 characters |
| `n_results`, `top_works` | number of results; the first 10 work ids in rank order |
| `dup_of` | the earlier query it near-duplicates, or null |

| `candidate_found` | Meaning |
|---|---|
| `work` | OpenAlex W id, else DOI, arXiv id or PMID |
| `field`, `query_id` | the finder's field; the query that surfaced it |
| `covered` | element ids the agent says the work covers |
| `quotes` | `{element, quote, location, text_level}`, each at most 40 words and passing the verbatim check |
| `quotes_dropped` | the number of quotes that failed the check |
| `reason` | one line, at most 200 characters |

| `claim`, `claim_release` | Meaning |
|---|---|
| `scope` | `field:<name>` or `field:<name>/element:<id>`; a field scope contains its element scopes, as a path contains its files in AgentMash |
| `reason`, `ttl_s` | one line; by default, the agent's remaining wall time |

Quotes go through the quote-hook check before posting, so no unverified quote reaches another agent. Coverage is derived from the events and never posted: in a field, an element is *covered* when a candidate from that field has a checked quote for it, *tentative* when it is only listed in `covered`, and *open* otherwise.

**Injection**:
- SessionStart: the coverage map (fields by key elements) and the live claims, at most 1,200 characters.
- PreToolUse on the search tool: up to 3 earlier queries in the room, from any agent, that near-duplicate the one about to run. Each comes with its agent, field and top 5 works (titles cut to 80 characters), at most 700 characters in all. It is advisory, as in AgentMash, so the search runs anyway. No duplicate is shown twice to the same agent.
- Stop: the key elements still open in every field, at most 400 characters, once per agent and never when `stop_hook_active` is set. This is a **departure**: AgentMash's Stop hook only tells a person, and no person is present here. If at least 20% of the agent's share is left, the hook returns a one-time continuation with the list. Otherwise it only logs the list, for the judge's coverage statement.
- Cap: once an agent's injected tokens reach 5% of its share, its hooks fall silent (logged). Text over a cap loses whole lines from the end, then gets an ellipsis.

**Near-duplicate rule** (a proposal, fixed in v2): a query's word set is built after NFKC, lowercasing, removing punctuation and removing a versioned English stopword list. Two queries to the same source are near-duplicates when their word sets have a Jaccard similarity of at least 0.6. Before the freeze, 100 pilot pairs near the threshold are judged by hand, and the threshold is adjusted at most once.

**MCP tools**: `get_coverage`, `list_claims`, `claim_scope`, `release_scope`, `list_candidates` (at most 2 quotes each) and `similar_queries`, each answer at most 2,000 characters, failures named as in AgentMash.

## 4. Three arms for protocol v2

All arms share the models (one step-1 output; every search agent on Haiku 4.5 or Sonnet 5, fixed in v2; Sonnet 5 verifies, Opus 5.5 judges), the search tool, date filter and exclusions, central steps 4 to 6, and the prompts apart from the field assignment.

1. **Single agent**: one search agent covers every target field.
2. **Specialists, no shared context**: one agent per target field, in parallel.
3. **Specialists, shared room**: arm (2), with all of the item's specialists on one room.

Proposal: arms (1) and (2) run the same hooks and tools against a private room per agent, which holds only that agent's events. Then arm (3) differs from arm (2) only in whether the agents can see each other.

**Budget unit**: the sum, over every model call for the item, of input, cache-creation, cache-read and output tokens, taken from the API usage fields. Injected text and MCP answers are input, so the total includes them and every later re-read. Injected tokens at entry are also logged per call.

**Matching** (protocol section 10: within 5% per item): a fixed per-item budget B, set from the pilot and frozen in v2. B splits into step 1, a search share S and a reserve R for steps 4 to 6, the same split in every arm. The single agent gets S, and each of k specialists gets S/k. A governor refuses any call once a share is spent and caps `max_tokens` on the last call. An agent that stops below 96% of its share gets one fixed continuation prompt, the same in every arm, at most twice. Afterwards each item is checked for |T_arm − T_single| ≤ 0.05 × T_single. Items outside that band stay in the analysis and are counted, and a sensitivity analysis drops them.

**Metrics**: primary, recall@10 and verdict macro-F1 on H (H-post alongside), paired bootstrap (B = 2000), Holm over (3) vs (2) and (2) vs (1). Secondary: duplicate queries per item (same rule, every arm), tokens and dollars per item (billed and undiscounted), key elements open at finish, leakage, wall time.

**Order**: an item's arms run on one day, one at a time, in a seeded random order. The search cache is shared, so a repeated query gets the same records in every arm; prompt caching changes dollars, not tokens. One run per arm and item, plus three repeats on 10 H items for variance. Each arm is pooled as its own system and judged blind (protocol section 12).

## 5. Fail-open, local server, logging

Toward the agent, the clients keep AgentMash's contract: they never block, always exit 0, print only context and never retry. Sync hooks get 1500 ms plus a 250 ms guard, MCP calls 4000 ms, and hook entries a 5 s timeout. One **departure**: reports are synchronous, with 2500 ms, so that an event is in the room before the agent's next call. On loopback this costs wall time, not tokens.

A silent failure would turn arm (3) into arm (2) without anyone noticing, so every hook and MCP call logs its outcome (`ok`, `timeout`, `refused`, `error`, `capped`) and its latency. An arm-(3) run with a failed sync hook is marked degraded and kept, and a sensitivity analysis drops it.

Every run uses a fresh, version-pinned server on 127.0.0.1, and clients refuse any URL that is not loopback. AgentMash's own hooks can be installed machine-wide by a login (AgentMash README.md, hooks/session_start.mjs `--global`, dfdf5a0). They would send eval prompts, and with them benchmark claims, to AgentMash's shared server before release. So runs set `AGENTMASH_DISABLE=1`, load only Coelacanth's settings, and fail a preflight check if any AgentMash hook is registered.

Each run logs, in `eval/results/<run>/`: the server version, commit and protocol; the raw room export; every injected text verbatim with its characters, tokens, latency and outcome; every MCP call and answer; the usage of every model call; seeds and arm order; the stopword list and threshold version. A replay script rebuilds every injected text from the event log, without a server.

## 6. Reproducibility

The AgentMash server code is in a private repository. **(A) Minimal room inside coelacanth (MIT).** A standard-library Python server (`http.server`, `sqlite3`) that implements a subset compatible with protocol 1: `POST /events`, a paged `GET /events`, the three claims endpoints, `POST /rooms` and `GET /healthz`. It is tested against AgentMash's conformance checklist, a closed port and a hung server (AgentMash PROTOCOL.md §9, dfdf5a0), and it adds no dependencies.
- Effort: 4 to 6 days (server 1.5, clients and MCP 2, tests 1, replay 0.5 to 1).
- Risks: it tests a protocol-1 room, not AgentMash itself, and the paper must say so; it may drift from AgentMash; its bugs are ours.
- What reviewers can rerun: everything, from the public repository.

**(B) Pinned public AgentMash release.** The co-founders publish a tagged release that includes `server/`, which is `private` today; only `cli/` is published (AgentMash README.md, dfdf5a0). The MIT LICENSE file at the repository root does not make private code reachable. Coelacanth pins the tag and lockfile and runs the server on loopback. This brings new dependencies, which need approval: Node, fastify, better-sqlite3 (a native build) and tsx (AgentMash server/package.json, dfdf5a0). A stock server drops undocumented fields. So either the events are packed lossily into documented fields (`change_summary` 300 characters, at most 60 symbols per server/src/db.ts) and the coverage logic moves to the client, or AgentMash adds the fields upstream first. `AGENTMASH_ROOMS_PER_HOUR` (default 20, server/src/config.ts) must also be raised.
- Effort: 2 to 3 days in Coelacanth, plus the co-founders' release work (their estimate).
- Risks: publishing is a company decision; the release may change; native builds may fail for reviewers; the conflict-of-interest optics are stronger.
- What reviewers can rerun: everything, including the product, as long as the release stays public.

The choice is Damir's decision with his co-founders. Under either option, the replay logs let anyone re-check the injections.

## 7. Conflict of interest

Damir co-founded AgentMash, and its site has a science section for an `agentmash-lab` plugin (AgentMash landing/scripts/science-pages.mjs, dfdf5a0), so a favourable result is worth something commercially. The paper and the ARR conflict fields disclose AgentMash, Damir's role and the option used. Arm (3) is called "shared room (AgentMash protocol 1)", and the paper makes no product claims.

Guards: v2 (arms, B, caps, duplicate rule, analysis code) is committed before any run; all arms get the same budget, with injected text inside it; arm (2) is the direct control; tuning uses only pilot items outside H; candidates are judged blind; the result is reported whatever it is (PLAN.md, Phase 3).

Shared-memory and blackboard designs for LLM agents already exist. They are surveyed with verified sources before writing, and the paper claims only the measured comparison.

## 8. Open questions for Damir

1. Option A or B (with the co-founders).
2. Should arms (1) and (2) get private rooms, as proposed, or no room at all? No room is cheaper but mixes the effect of the hooks with the effect of sharing.
3. At Stop: a one-time continuation (the proposed departure), or only a log entry?
4. Should duplicate searches stay advisory, or be denied and answered with the earlier results, as in strict mode?
5. Specialists would return covered elements and checked quotes, more than the "candidate IDs with a one-line reason" in PLAN.md. Is that change acceptable?
6. What are B and the Phase 3 ceiling? Estimate, not measured: about 45 H items (the target set on 6 Oct) × 3 arms × $2 to $4, plus three repeats of every arm on 10 items, is roughly $450 to $900 before the new pooled candidates are annotated.
7. Should (3) vs (1) also be in the primary family?
8. Should a co-author with no AgentMash ties rerun the analysis, and may AgentMash cite the result before the paper is public?
