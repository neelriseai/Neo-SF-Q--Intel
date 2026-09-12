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

## D-series audit (D1-D6) — 2026-09-12

Written by a `sonnet` measurement pass. No design or code review performed. Every figure below is
tagged `MEAS` (counted this pass from a real artifact, or reported to this pass by a tool run
already executed) or `EST` (reasoned, not counted). No `EST` is presented as `MEAS`.

### Measurement basis

Commands actually run this pass (raw output kept in session, restated here as evidence):

| Cmd | Command | Result |
|---|---|---|
| M1 | `git status --short` | 11 modified, 8 untracked (listed below) |
| M2 | `git diff --numstat` | see per-file table below |
| M3 | `wc -l` per new file | see per-file table below |
| M4 | `grep -c "^def test_\|^async def test_"` (5 python test files) | see table below |
| M5 | `grep -c "^test("` (3 TS spec files) | see table below |
| M6 | `wc -c knowledge/*.json` | application-graph.json 276,471 B · project-index.json 129,055 B |
| M7 | `wc -l` (3 python source files) | context_feeds.py 404 · locator_proposal.py 446 · element_signature.py 388 |

`GIVEN_MEAS` (reported by tool runs already executed this session, not recomputed by this pass —
still `MEAS`, source: prior tool output, not this pass's own command):

| Item | Value |
|---|---|
| D3 opus subagent usage | 118,587 tokens · 37 tool uses · 584,878 ms |
| Graph feed cut | full 228,591 ch vs 1-hop 946 ch, 99.6% |
| Knowledge feed cut | whole page 5,824 ch vs heading block 517 ch, 91.1% |
| Cross-language digest check | PY `b167df2522f2e65b` == NODE `b167df2522f2e65b` (data-value) · PY `c5ecd76d4d625d98` == NODE `c5ecd76d4d625d98` (title) |
| Full python suite, before D6 fixes | 1,910 passed · 1 failed · 8 skipped · 779.73s |
| Browser specs, after D6 fixes | 51 passed (31.1s) |

M1 detail:

```
 M knowledge/application-graph.json
 M knowledge/project-index.json
 M packages/browser/package.json
 M packages/browser/src/browser-worker.ts
 M packages/browser/src/healing-probe.ts
 M packages/browser/src/live-healing-cli.ts
 M packages/browser/tests/browser-worker.spec.ts
 M packages/browser/tests/healing-probe.spec.ts
 M src/neo_sf_q_intel/context_feeds.py
 M src/neo_sf_q_intel/mcp_server.py
 M tests/test_mcp_server.py
?? migrations/004_element_signature.sql
?? packages/browser/tests/live-healing-cli.spec.ts
?? src/neo_sf_q_intel/element_signature.py
?? src/neo_sf_q_intel/locator_proposal.py
?? tests/test_element_signature.py
?? tests/test_element_signature_functional.py
?? tests/test_locator_healing_bridge.py
?? tests/test_locator_proposal.py
?? tests/test_metadata_lookup.py
```

M2 detail (`git diff --numstat`, insertions/deletions):

| File | + | - |
|---|---:|---:|
| knowledge/application-graph.json | 153 | 1 |
| knowledge/project-index.json | 55 | 10 |
| packages/browser/package.json | 1 | 1 |
| packages/browser/src/browser-worker.ts | 4 | 2 |
| packages/browser/src/healing-probe.ts | 257 | 1 |
| packages/browser/src/live-healing-cli.ts | 10 | 1 |
| packages/browser/tests/browser-worker.spec.ts | 83 | 0 |
| packages/browser/tests/healing-probe.spec.ts | 204 | 0 |
| src/neo_sf_q_intel/context_feeds.py | 165 | 0 |
| src/neo_sf_q_intel/mcp_server.py | 15 | 0 |
| tests/test_mcp_server.py | 83 | 0 |

M3 detail (new files, `wc -l`):

| File | Lines |
|---|---:|
| migrations/004_element_signature.sql | 39 |
| packages/browser/tests/live-healing-cli.spec.ts | 93 |
| src/neo_sf_q_intel/element_signature.py | 388 |
| src/neo_sf_q_intel/locator_proposal.py | 446 |
| tests/test_element_signature.py | 289 |
| tests/test_element_signature_functional.py | 117 |
| tests/test_locator_healing_bridge.py | 175 |
| tests/test_locator_proposal.py | 318 |
| tests/test_metadata_lookup.py | 286 |

M4 detail (python `def test_` count per file):

| File | Tests |
|---|---:|
| tests/test_element_signature.py | 16 |
| tests/test_element_signature_functional.py | 4 |
| tests/test_metadata_lookup.py | 18 |
| tests/test_locator_proposal.py | 20 |
| tests/test_locator_healing_bridge.py | 10 |
| tests/test_mcp_server.py (modified, not new — supplementary count, not in the requested M4 file list) | 12 |

M5 detail (TS `^test(` count per file):

| File | Tests |
|---|---:|
| packages/browser/tests/healing-probe.spec.ts | 22 |
| packages/browser/tests/live-healing-cli.spec.ts | 2 |
| packages/browser/tests/browser-worker.spec.ts | 20 |

Caveat on attribution: `test_mcp_server.py`, `healing-probe.spec.ts`, and `browser-worker.spec.ts`
are **modified** files (M1), not new files. Their diffs are pure additions (M2: `+83/-0`,
`+204/-0`, `+83/-0`), so the added-line count is `MEAS`, but the per-file test totals above (12,
22, 20) are current **file totals**, not an isolated added-this-session delta — pre-existing tests
in those files are not subtracted out because no pre-D-series baseline count was captured. Flagged,
not estimated around.

### SECTION_1 — Chunk ledger

| Chunk | Scope | File(s) touched | Executing model | Timebox | Actual | Tests added | Outcome |
|---|---|---|---|---:|---|---:|---|
| D1 | T-SIG signature store | element_signature.py (388 ln, MEAS M7) · migrations/004_element_signature.sql (39 ln, MEAS M3) | opus-5 (VIOLATION) | 45m | NOT MEASURED (no timer artifact) | 20 (16+4, MEAS M4) | tests green (asserted, not re-run this pass) |
| D2 | T-META metadata lookup | context_feeds.py +165/-0 (MEAS M2), mcp_server.py +15/-0 (MEAS M2) | opus-5 (VIOLATION) | 15m | NOT MEASURED | 18 (MEAS M4) + 12 in modified test_mcp_server.py (ambiguous attribution, see caveat) | tests green (asserted) |
| D3 | T-DOM push payload | browser-worker.ts +4/-2, healing-probe.ts +257/-1 (MEAS M2) | opus-4.x subagent (COMPLIANT) | 30m | 584,878 ms, approx 9m45s (MEAS, GIVEN_MEAS) — under box | 20 + 22 file totals, modified files (ambiguous attribution, see caveat) | subagent: 118,587 tok · 37 tool uses (MEAS) |
| D4 | prompt+provider+proposal | locator_proposal.py (446 ln, new file, MEAS M7/M3) | opus-5 (VIOLATION) | 25m | NOT MEASURED | 20 (new file, fully attributable, MEAS M4) | tests green (asserted) |
| D4b | context bridge | test_locator_healing_bridge.py (new file); shares context_feeds.py +165/-0 diff with D2 (not separable) | opus-5 (VIOLATION) | included in D4 box | NOT MEASURED | 10 (new file, fully attributable, MEAS M4) | tests green (asserted) |
| D6 | triage + fixes | live-healing-cli.ts +10/-1, live-healing-cli.spec.ts new 93 ln/2 tests, browser-worker.ts (shared w/ D3), knowledge/*.json, package.json +1/-1 | opus-5 (VIOLATION) | 20m | NOT MEASURED | 2 (new file, fully attributable, MEAS M5) | browser specs after fixes: 51 passed / 31.1s (MEAS, GIVEN_MEAS); full python suite before fixes: 1910 passed, 1 failed, 8 skipped / 779.73s (MEAS, GIVEN_MEAS) |

"NOT MEASURED" is stated rather than estimated: no per-chunk timestamp log exists in the repo for
D1/D2/D4/D4b/D6, and inventing a duration would violate `[LABEL_RULE]`.

### SECTION_2 — Delegation compliance

**1 of 5 code chunks delegated** (D3 only, to an opus-4.x subagent). D1, D2, D4, D4b, D6 were coded
directly by opus-5, the model reserved by policy for design and review, not implementation.

| # | Violation |
|---|---|
| V1 | D2 coded by opus-5, not opus-4.x |
| V2 | D4 coded by opus-5, not opus-4.x |
| V3 | D4-bridge coded by opus-5, not opus-4.x |
| V4 | D6 fixes coded by opus-5, not opus-4.x |
| V5 | All pytest/ruff/build_project_index runs done by opus-5, not sonnet |
| V6 | Triage assigned severity+priority but no per-bug time slot by complexity |
| V7 | No requirement-to-test traceability matrix; coverage asserted by test count only |
| V8 | This log unmaintained from the K-chunk until now |

Corrective action already taken: memory instructions rewritten — `agent-delegation-policy.md`,
`timeboxed-test-driven-loop.md`, `compliance-log-discipline.md` (present this pass under this
project's memory directory; content not re-audited by this pass).

### SECTION_3 — Token technique usage

| # | Technique | Where | Benefit |
|---|---|---|---|
| T1 | Diff-only contract | Every edit to an existing file this D-series used anchored replacement, not full-file rewrite | MEAS: `context_feeds.py` diff `+165/-0` against a 404-line file (M2 vs M7) = 40.8% of the current file added as pure insertion, 0 lines rewritten. `browser-worker.ts` diff `+4/-2` (6 changed lines) against a 1,515-line / 51,230-byte file (measured this pass) = 0.4% of the file touched — a 51KB file was not re-sent or rewritten for a 6-line change. |
| T2 | AST signature feeding | Stated as used for the D3 brief (interface/type signature block, not source files) | NOT MEASURED THIS PASS. The runtime prompt sent to the D3 subagent was not retained as a repo artifact; only the design doc (`Docs/28-llm-locator-healing-plan.md`) survives, and its `[T-DOM]`/`[T-SIG]` lines are the contract shape, not the literal signature block sent. No real artifact to count against `healing-probe.ts` (641 ln / 24,319 B, measured this pass) plus `browser-worker.ts` (1,515 ln / 51,230 B). Gap, not estimated around. |
| T3 | State-transition prompting | Stated as used in the D3 brief (capture rule expressed as State to Action to State_Expected) | Qualitative only. No length or count artifact retained; not quantifiable this pass. |
| T4 | Minified JSON A2A | Stated as used for the D3 subagent return contract (JSON only, no prose) | NOT MEASURED THIS PASS. The actual D3 return payload was not saved to the repo; nothing to `wc -c`. Gap, not estimated around. |
| T5 | Graph/knowledge feeds | context feed tools (`F0`/`F1`/`F2` in `context_feeds.py`) | MEAS (GIVEN_MEAS): graph 228,591 ch full vs 946 ch 1-hop, 99.6% cut. Knowledge 5,824 ch whole page vs 517 ch heading block, 91.1% cut. |

T2 and T4 are asserted as applied in the chunk ledger's source instructions but have zero
measurable evidence retained in the repo as of this pass. This mirrors the existing log's own
"Honest non-compliance" pattern (see top section of this file) — the gap is recorded, not papered
over with an invented `EST` figure.

### SECTION_4 — Bug ledger (D6)

Time slots below are **RETRO-FIT**: no time slot was allocated in advance of triage (`V6`). Slots
are recorded now, after the fact, against the fix — not planned before the fix.

| # | Severity | Priority | Complexity | Time slot | Actual | Status |
|---|---|---|---|---|---|---|
| 2 | HIGH | — | — | RETRO-FIT, not pre-allocated | NOT MEASURED | FIXED — captureCandidates dropped by worker; llm tier inert on live path |
| 1 | MED | — | — | RETRO-FIT, not pre-allocated | NOT MEASURED | FIXED — knowledge index stale after D1 added a module |
| 3 | MED | — | — | RETRO-FIT, not pre-allocated | NOT MEASURED | FIXED — cross-language digest contract unpinned in CI |
| 4 | MED | — | — | RETRO-FIT, not pre-allocated | NOT MEASURED | FIXED — live-healing-cli.spec.ts absent from every aggregate npm script |
| 5 | LOW | — | — | RETRO-FIT, not pre-allocated | NOT MEASURED | ACCEPTED — raw values cross CDP into node before digesting (matches existing `sanitizeCandidates` precedent) |

Priority and complexity columns are blank: the source triage (per `[V6]`) recorded severity only,
no priority or complexity classification, and no per-bug time-by-complexity slot. Filling those in
now would be invention, not measurement.

Post-fix verification (MEAS, `GIVEN_MEAS`): browser specs 51 passed, 31.1s. The full python suite
figure supplied (1,910 passed · 1 failed · 8 skipped · 779.73s) is explicitly labeled **before D6
fixes** in the source instructions — it is not a post-fix confirmation for the python side, and no
post-fix python full-suite number was supplied to this pass. Gap noted, not filled.

## D-series/G-series audit, block 2 (R0, G1-G3, DOC-PASS) — 2026-09-12

Written by a `sonnet` documentation pass following the D-series audit above. Every figure below is
tagged `MEAS` (counted this pass from a real repo artifact) or `EST` (reasoned, not counted). No
`EST` is presented as `MEAS`.

### SECTION_5 — Chunk ledger, block 2

| Chunk | Scope | Executing model | Outcome |
|---|---|---|---|
| R0 | evidence-graph access-path spike | opus-4.x subagent | COMPLIANT · measured the real access path and refuted 2 opus-5 assumptions: (1) that a single entity could be looked up cheaply/directly — refuted, the adapter merges attributes and owner paths across the whole tree, so no per-entity shortcut is correct (q4, `singleEntityPossible: false`); (2) that the accessor chunk (R1) was a 30-minute unit of work — refuted, measured at 90m (range 75-110m), a 3x miss (see `Docs/28` §12 `[RESLOT]`) |
| G1 | postgres functional gaps (REQ-HEAL-02, 03, 13) | opus-4.x subagent | COMPLIANT · found a real `src/` defect (tamper raised the base `ElementSignatureError`, not a dedicated catchable type) and reported it rather than silently fixing it, per its brief's role boundary |
| G2 | tier-gating functional gap (REQ-HEAL-14) | opus-4.x subagent | COMPLIANT · no defects found; the gate was proven correct against a real Playwright page, including a red-run (temporarily inverted assertions, confirmed they fail, reverted) |
| G3 | `SignatureCorrupt` fix | opus-4.x subagent | COMPLIANT · fixed the defect G1 surfaced, and in doing so found a second, smaller defect inside G1's own output: a vacuous tamper test (`captured_at_utc` mutated with no read-back) |
| DOC-PASS | this pass — Docs/29, Docs/28 rev 3, this log entry, index rebuild | sonnet | COMPLIANT |

**Delegation, stated plainly:** after the user's correction recorded in `SECTION_2` above, 5 of 5
chunks in this block were delegated to the model the policy names for their role — opus-4.x
subagents for test/fix code (R0, G1, G2, G3), sonnet for documentation (DOC-PASS). **Opus-5 wrote
zero code in this block.** That is the direct reversal of the D-series block above it, where 4 of
5 code chunks (D2, D4, D4b, D6) were self-coded by opus-5 in violation of the same policy (`V1-V4`
in `SECTION_2`).

### SECTION_6 — Bug ledger additions (R0/G1-G3 block)

| Severity | Priority | Time slot | Actual | Finding |
|---|---|---|---|---|
| HIGH | — | 25m | NOT MEASURED (no timer artifact for the fix itself; G3 chunk box was 25m) | `SignatureCorrupt`: tamper (`SIGNATURE_CORRUPT`) was raised as the BASE `ElementSignatureError`, not as a type distinct from `SignatureStoreUnavailable`, so a caller catching the documented store-failure type never caught a tamper event. Fixed in G3 by adding a SIBLING `class SignatureCorrupt(ElementSignatureError)` (element_signature.py:101) — a sibling of `SignatureStoreUnavailable`, not a subclass of it. **REJECTED FIX, recorded so it is not tried again:** moving the row-decode inside `_session()` so corruption would raise `SignatureStoreUnavailable` was proposed and rejected by opus-5 — corruption is not transient, and filing a tamper signal under a retryable type is worse than the original bug. |
| MED | — | — (found during G3, no separate slot allocated) | NOT MEASURED | G1's functional test `test_a_tampered_row_is_also_refused_through_the_obligation_index` mutated `captured_at_utc` with no read-back, so it would have passed even if the `UPDATE` had silently no-opped. Found by G3 (its own `[ANTI_VACUOUS]` requirement). Fixed by adding a `_column()` read-back plus an inequality assertion against the original value, matching the pattern already used by the other three tamper tests in the same file. |
| LOW | — | — | NOT MEASURED | No production call site anywhere in the repo catches `SignatureStoreUnavailable` or `ElementSignatureError` today (repo-wide grep for `except (SignatureStoreUnavailable\|ElementSignatureError\|SignatureCorrupt)` returns exactly one hit, inside `element_signature.py` itself) — the class distinction G3 introduced is currently consumed only by tests, because T-SIG is not yet wired into MCP. Recorded as a pre-emptive fix, not a live bug closed: nothing in production could have hit it yet. |
| MED | — | — | NOT MEASURED | The knowledge index (`knowledge/project-index.json`) went stale twice this project as agents added files mid-stream; regenerating it mid-stream is futile while other agents are still writing. Rule adopted and enforced by this pass's own task order: rebuild the index LAST, once the file tree has settled, never before or between other writes. |

### SECTION_7 — Retention and T2/T4 measurement

`quality/reviews/agent-exchanges/{R0-spike,G1,G2,G3}-{brief,return}.md` are now persisted in the
repo, so techniques T2 (AST/interface signature feeding) and T4 (minified JSON agent-to-agent) are
measurable against real artifacts for the first time — the D-series block above marked both `NOT
MEASURED THIS PASS` because no brief/return survived as a repo file. They are measured now.

**T2 — brief size vs. the size of the source it stood in for.** Method as instructed: `wc -c` on
each brief, `wc -l` on the modules the brief names (current LOC, this pass; not LOC at the time the
brief was written — no earlier snapshot exists to count against). Bytes and lines are different
units and are NOT converted into a false "% reduction" figure — that would be inventing a number
this method cannot produce; the pairing below is descriptive only.

| Brief | Brief size (MEAS, `wc -c`) | Files named in the brief (MEAS, `wc -l` today) | Total LOC named |
|---|---:|---|---:|
| R0-spike-brief.md | 4,312 B | graph_production.py (1,712) | 1,712 |
| G1-brief.md | 5,709 B | context_feeds.py (404) + element_signature.py (435) + locator_proposal.py (446) + test_element_signature.py (356) + test_element_signature_functional.py (330) + test_locator_healing_bridge.py (175) + test_locator_healing_bridge_functional.py (234) + test_locator_proposal.py (318) + test_metadata_lookup.py (286) | 2,984 |
| G2-brief.md | 3,835 B | browser-worker.spec.ts (807) | 807 |
| G3-brief.md | 4,725 B | element_signature.py (435) + test_element_signature.py (356) + test_element_signature_functional.py (330) + test_locator_healing_bridge.py (175) + test_locator_proposal.py (318) + test_metadata_lookup.py (286) | 1,900 |
| **Total** | **18,581 B** | | **7,403** |

A brief is not a lossless replacement for the files it names — it is a task specification, not a
compressed re-encoding — so this table is evidence that briefs stayed far smaller than the surface
they pointed at (18,581 B of brief text directed work across 7,403 lines of named source/test
code, some files named by more than one brief), not a claim of a specific compression ratio.

**T4 — minified JSON agent-to-agent, confirmed applied.** Every return file's JSON payload is
exactly 1 line (MEAS, checked this pass: `sed` the fenced ```json block out of each return file
and count lines — R0-spike-return.md, G1-return.md, G2-return.md, G3-return.md each yield exactly
1 line of JSON). No pretty-printing, no prose inside the payload itself.

| Return | Byte size (MEAS, `wc -c`) | JSON lines (MEAS) |
|---|---:|---:|
| R0-spike-return.md | 12,955 B | 1 |
| G1-return.md | 2,026 B | 1 |
| G2-return.md | 1,155 B | 1 |
| G3-return.md | 2,669 B | 1 |
| **Total** | **18,805 B** | |

No prose-equivalent baseline was captured for any of these four returns (the wasteful path was
never executed), so no savings percentage is reported for T4 — reporting one would be an invented
`EST` dressed as `MEAS`. What is measured and reported is narrower and honest: the technique (JSON
only, one line, no prose) was actually applied on all 4 exchanges in this block, not merely
designed-in as the D-series block's `SECTION_3` recorded for T4 there.

## G4/DOC-PASS-2/VERIFY audit, block 3 — 2026-09-12

### SECTION_8 — Chunk ledger, block 3

| Chunk | Executing model | Outcome |
|---|---|---|
| G4 — REQ-HEAL-05 org-call assertion | opus-4.x subagent | COMPLIANT · 6 tests · no src defect found |
| DOC-PASS-2 | sonnet | COMPLIANT |
| VERIFY — full verification run | sonnet | COMPLIANT · see MEAS below |

**MEAS (VERIFY):**

- python suite: 1977 passed · 0 failed · 19 skipped · 1538.57 s
- functional opt-in (real PostgreSQL): 15 passed · same files 15 skipped without the env var
- ruff: pass · browser typecheck: pass · playwright: 138 passed · knowledge currency: pass
- skip-count reconciliation: 8 -> 19 because 4 of the original 8 were functional opt-in tests, and
  4 + 15 new opt-in functional tests = 19

### SECTION_9 — Bug/process ledger additions, block 3

| Severity | Finding |
|---|---|
| MED | Docs/29 marked REQ-HEAL-05 "COVERED (see note 1)" while note 1 admitted an unasserted clause. A verdict softened by a parenthetical hides a gap. Closed by G4. Slot 20m. RULE: a verdict is COVERED or it is not; qualifications belong in the gap list. |
| LOW | a subagent ended its turn while its own backgrounded 26-minute pytest was still running, losing the run. "Do not poll" applies to the main session, which receives completion notifications; a subagent must block within one turn until a completion condition is met. Brief-writing rule corrected. |
| LOW | three separate subagents spent tokens flagging the dirty working tree as suspicious because their session-start snapshot showed it clean. Brief template now states the tree is intentionally dirty. |
