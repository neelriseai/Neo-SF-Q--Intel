# AI communication approach — compliance and savings log

Maintained per timebox chunk. Records whether the shared design was followed, where the
`Development_AI_communication_approch.md` techniques were applied, and what they measurably saved.

## Measurement method (declared before any figure)

- **Population:** files and payloads actually handled in this project during this session.
- **Token proxy:** `chars / 4`. A proxy, not a tokenizer count.
- **`MEAS`** = measured from real byte counts. **`EST`** = counterfactual; the wasteful path was
  not executed, so the saving is modelled, not observed. Never report `EST` as `MEAS`.
- Percentages are given only against a stated denominator, per verifier rule `[V3]`.

## Technique compliance

| # | Technique | Status | Where | Evidence |
|---|---|---|---|---|
| 1 | Diff-only contract | PARTIAL | All edits to existing files used targeted replace/sed mutations | 5 lookup-strategy iterations averaged ~0.6KB each against a 52.6KB file |
| 1a | — exception | ACCEPTED | New files written whole (`live-healing-cli.ts` 8.4KB) | A new file has no diff; exception is legitimate and bounded |
| 2 | AST context feeding | PLANNED | Not used pre-C0; bounded reads (`sed` ranges, scoped `grep`) used instead of file dumps | Signature block for `browser-worker.ts` = 617 chars vs 52,642 chars full file |
| 3 | State-transition prompting | NOT YET | Applies to generated test scripts; none generated yet | — |
| 4 | Code-generation DSL / dense handles | APPLIED | C0 contract uses `[T1..T7]`, `[V1..V7]`, `[SIDE_EFFECT]`, `[CALLER_SCOPE]` | `Docs/27` |
| 5 | Compressed markdown matrices | APPLIED | C0 contract and the 6h chunk plan are key-value matrices, not prose | `Docs/27` = 4,433 chars for the full design |
| 6 | Minified JSON agent-to-agent | NOT YET | No subagent spawned yet; mandated in C1 prompt contract | `Docs/27` §4 |
| 7 | Graph as edge lists | NOT YET | Designed into the context pack, not yet built | `Docs/27` §5 |

## Measured and modelled savings

| Item | Baseline | Applied | Saving | Class |
|---|---:|---:|---:|---|
| Context for `browser-worker.ts` | 52,642 ch (~13,160 tok) | 617 ch (~154 tok) | 98.8% | MEAS (both sides measured) |
| Three-file subagent context | ~118,000 ch (~29,500 tok) | ~1,800 ch (~450 tok) | ~98% | EST (subagent not yet run) |
| 5 edit iterations on the worker | 263,210 ch if rewritten whole | ~4,000 ch of mutations | ~98% | EST (whole-file path not executed) |
| C0 design as matrices | prose equivalent ~13,000 ch | 4,433 ch | ~66% | EST (prose version never written) |

## Honest non-compliance

- The approach document arrived late in the session. Everything before C0 — analysis, live
  debugging, reporting — was prose-heavy and is **not** claimed as compliant.
- Techniques 3, 6 and 7 have zero applied evidence so far. They are designed into the C1-C4
  contracts and must be evidenced when those chunks run, not before.
- No tokenizer-accurate measurement exists. Every figure here is a `chars/4` proxy and three of
  the four savings rows are counterfactual.

## Design compliance (timeboxed loop)

| Rule | Status |
|---|---|
| Size work and split the window before coding | FOLLOWED — 6h split into C0-C7 with stated rationale |
| Test first, within each chunk | PENDING — C1 onward |
| Collect all issues before fixing any | PENDING — C5/C6 |
| Severity then priority, fix in order | PENDING — C6/C7 |
| Skip overruns with recorded next action | PENDING |
| Delegate: sonnet deterministic/docs, opus coding, Opus 5 design and review | PARTIAL — C0 designed at Opus 5; no delegation yet |
| Model-version caveat | RECORDED — subagent enum is sonnet/opus/haiku/fable; 4.6/4.7/4.8 not selectable |

## Chunk entries

### C0 — design and contracts (0-30m, 16:42)
- Applied: techniques 4 and 5. Contract written as matrices and handles throughout.
- Output: `Docs/27` 4,433 ch covering 7 tool signatures, report schema, 7 verifier rules,
  agent prompt contract, orchestrator flow.
- Delegation: none (design is the Opus 5 slot by policy).
- Savings: see table; the `browser-worker.ts` row is the only fully measured figure.

### K — context feed tools (75m box, used ~50m, 17:00-17:50)

Built: `context_feeds.py` (F0 index / F1 section / F2 neighborhood) + MCP exposure of all three.
Result: 40 tests green, ruff clean, 0 CRLF bytes.

**Techniques applied, with evidence**

| # | Technique | Evidence this chunk |
|---|---|---|
| 1 | Diff-only | All `mcp_server.py` changes were targeted replaces; file never rewritten |
| 2 | AST signature feeding | Both subagent prompts carried signature blocks, not source. `mcp_server.py` ~3.6KB and `context_feeds.py` ~8KB were represented by ~0.4KB and ~0.5KB of signatures |
| 6 | Minified JSON agent-to-agent | Both subagents returned JSON only, 451 ch and 522 ch. No prose exchanged in either direction |
| 7 | Graph as edge list | `MEAS` full graph 228,591 ch (~57,147 tok) vs 1-hop 946 ch (~236 tok) — **99.6% reduction, both sides measured** |
| — | Scoped knowledge retrieval | `MEAS` one heading block 517 ch vs whole page file 5,824 ch — 91% reduction |

**Delegation — honest accounting**

Two `opus` subagents consumed 80,338 and 82,767 tokens (163,105 total). Delegation did **not**
reduce total tokens spent; it moved consumption off the main thread. The saving is context
preservation on a long-running session, not raw spend. Reporting it as a token saving would be
false.

**Review slot earned its place.** The subagent wired `Path.cwd()` inline at each call site. The
repo convention (`api.py`) is an injectable `repository_root` with cwd only as fallback. Caught in
review, corrected to resolve once and thread through `run()`. Tests stayed green throughout, so no
test would have caught it — it needed a human-level convention check.

**Design compliance:** size-and-split before coding FOLLOWED · test-first FOLLOWED (subagent wrote
tests alongside, 27 then 13 more) · delegation per policy FOLLOWED (opus coded, Opus 5 designed and
reviewed) · one deviation: this log entry written directly rather than by a `sonnet` subagent,
because the numbers were already in the main context and a cold start would have cost more.

### Consolidation checkpoint — Docs/28 rev 2

Swept the conversation for anything discussed and not captured. Now recorded in `Docs/28`:
tier switch, probe mode, healing CLI, T-GRAPH, T-INTENT, knowledge repo, deterministic heal proof,
T-SIG/T-META/T-DOM/prompt as remaining, agent-evidence fields including **rejected proposals**,
deferred items (screenshots, dashboard panel, report agent), Playwright MCP as dev-time only,
and the two decisions blocking D1.

**Timebox compliance to date**

| Rule | Status | Evidence |
|---|---|---|
| Size and split before coding | FOLLOWED | C0-C7 and D0-D7 both sized before any code |
| Test first | FOLLOWED | K-chunk: 27 then 13 tests written with the code, all green before commit |
| Collect all issues before fixing | NOT YET EXERCISED | K-chunk produced one review finding, not a batch; D6 is the first real triage |
| Severity then priority | NOT YET EXERCISED | D6 |
| Skip overruns, record next action | FOLLOWED | lookup work stopped after 4 hypotheses; recorded, not extended |
| Delegate by model | FOLLOWED | 2 opus subagents coded; Opus 5 designed and reviewed |
| Chunk came in under box | FOLLOWED | K: ~50m used of 75m |

**Benefit to date**

| Measure | Value | Class |
|---|---:|---|
| Graph edge list vs full graph | 99.6% smaller | MEAS |
| Knowledge heading vs whole page | 91% smaller | MEAS |
| Signature block vs source file | 98.8% smaller | MEAS |
| Subagent replies, prose vs JSON | 451 / 522 ch, no prose | MEAS |
| Delegation token cost | 163,105 subagent tokens | MEAS — context relief, NOT a token saving |
| Defects caught by review that tests missed | 1 (`Path.cwd()` wiring) | MEAS |

**Honest gaps unchanged:** techniques 3 (state-transition prompting) and the full batch-triage loop
still have no applied evidence. Both land in D3-D6.
