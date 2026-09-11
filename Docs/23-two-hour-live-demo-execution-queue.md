# Two-hour live demo execution queue

Started: 2026-09-11T17:20:13+05:30

Hard stop target: 2026-09-11T19:20:13+05:30

## Objective

Prioritize a working live-integrated Salesforce demo MVP where Neo uses real LLM specialist calls to
reason over verified Salesforce change context and produces demo-useful output. The target is
good-enough capability delivery, not foundation perfection.

## Operating rules

- Work in bounded queues; do not expand scope when a foundation gap is discovered.
- Retry any failing subtask at most three times, then record the blocker and move on.
- If a retry exposes a design-level or complex debugging problem, invoke Astra for review/debug,
  but keep the same subtask clock and retry limit.
- Mark a subtask done when it delivers the demo output safely enough, even if production hardening
  remains.
- Patch foundation/architecture only when it blocks the live demo path or creates a false claim.
- Preserve the truth boundary: fixture, diagnostic live read, accepted live receipt and release
  authority must stay separately labeled.
- Select work one demo capability at a time: make it run end-to-end with live Salesforce and a
  real LLM, add only the foundation required for truthful/safe operation, test it, document the
  pass/fail result, then move to the next capability.
- Current-scope prioritization overrides earlier holistic design pressure. Older roadmap, memory,
  governance and architecture instructions remain binding only for safety, credential protection,
  non-production/live-mutation authority, and truthful claims. If an older instruction would pull
  the work into broad hardening, extra architecture, or production completeness beyond the selected
  demo slice, record it as deferred work instead of coding it now.
- For every selected task: plan, challenge the plan, correct it, implement the minimum complete
  path, integrate, run focused tests, fix failures one by one up to three retries, then mark
  `DONE`, `PARTIAL`, `BLOCKED`, or `DEFERRED`. Do not chase perfection after the demo output is
  truthful and safe enough.
- Keep a visible wall-clock. Each two-hour run records start time, hard stop, selected capability,
  elapsed-window status, completed items and deferred bias items.

## Time allocation

| Window | Subtask | Completion bar |
|---:|---|---|
| 0-10m | Freeze queue and task history | This file records plan, retry policy, done/deferred states |
| 10-45m | Public candidate LLM advisory | Candidate path invokes real provider and at least one stage yields verifier-accepted advisory output, or a precise verifier blocker is recorded |
| 45-75m | Live Salesforce read proof | Machine can reach authenticated non-production org through safe CLI/API read and produce sanitized diagnostic/demo evidence |
| 75-100m | Browser/Playwright demo proof | Minimal browser automation path runs or records exact dependency/login/blocker |
| 100-115m | Integration tests | Focused unit/functional tests for completed items pass |
| 115-120m | Master history and commit | Docs/index/history updated; protected commit attempted |

## Task history

| Item | Status | Attempts | Notes |
|---|---|---:|---|
| Queue freeze | DONE | 1 | Scope, retry policy and timing recorded here |
| Public candidate LLM advisory | DONE | 2 | First retry exposed relation/prompt mismatch; Astra found canonical edge orientation issue. Patched exact relation edge hints, canonical edge-source/target bindings, and safe unordered-list normalization. Final real-provider fixture run returned 6/6 `SUCCESS`, each with one accepted proposal |
| Live Salesforce read proof | DONE | 2 | `sf org display`, standard Opportunity query and app custom REST policy endpoint all succeeded read-only. First custom REST attempt used wrong CLI flag; second returned HTTP 200 with active policy, opportunity policy status and history |
| Browser/Playwright demo proof | DEFERRED | 2 | Build ran; live smoke profile read succeeded only with absolute path, then blocked on `ENROLLMENT_EXPIRED`. No fake profile refresh performed; live browser proof not claimed |
| Integration tests | DONE | 1 | Focused Python regression passed 10/10 across specialist, candidate assurance and Azure strict-schema config; `npm run build:live` passed for the Playwright/browser package; `ruff check src tests` and `git diff --check` passed |
| Master history/commit | DONE | 1 | Scope-review manifest updated, knowledge graph/index regenerated, governance checks passed and protected commit prepared |

## Master history

- 17:20:13+05:30: baseline clock captured.
- 17:27:21+05:30: public candidate LLM advisory complete with 6 accepted model-backed proposals in fixture-backed candidate path.
- 17:28:31+05:30: live Salesforce read proof started after connected CLI and standard Opportunity reads.
- Browser diagnostic live smoke deferred because the existing private enrollment expired at 2026-09-11T06:14:30Z.
- 17:32:47+05:30: focused regression and live browser build sweep started after the model-provider and live-read fixes.
- 17:33:14+05:30: 10 focused Python checks passed, `npm run build:live` passed, `ruff` passed and whitespace diff checks passed. The quality gate required the expected scope-review manifest and knowledge regeneration updates before commit.
- 17:36:xx+05:30: knowledge graph/index regenerated and freshness/genericity checks passed.

## Continuation: selected demo capability

Selected capability: `automation.browser-worker` live Salesforce browser smoke.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Make it live end-to-end | DONE | 2 | Added generic `npm run live:profile` host-local profile refresh, generated a fresh ignored runtime profile from current `sf` CLI identity and ran `npm run live:smoke` headless against live Salesforce |
| Minimal truthful foundation | DONE | 2 | The tool derives hashed org/actor bindings and pinned origins, writes only `.runtime/live-browser-profile.json`, prints only path/digest/expiry, and never prints/stores the frontdoor URL |
| Test it | DONE | 2 | `npm run build:live` passed; `tests/live-profile-cli.spec.ts` passed 4/4; live smoke returned `status=PASSED`, `diagnosticOnly=true`, `releaseEligible=false`, and cleanup closed both browser context and browser |
| Document pass/fail | DONE | 1 | This continuation records the live result as diagnostic proof only; it still does not satisfy signed live acceptance receipt gates |
| Move to next capability | READY | 0 | Next slice should combine live Salesforce read evidence plus real LLM advisory into one operator-visible demo flow |

## Continuation: live operator advisory slice

Selected capability: `demo.live-operator-advisory` as an operator-visible foundation slice. This
slice composes a governed live Salesforce diagnostic projection with the real candidate LLM
advisory path. It is intentionally diagnostic/advisory-only and cannot approve release, satisfy
live campaign gates or substitute fixture output for live evidence.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Make it run end-to-end | PARTIAL | 2 | Added `POST /api/v1/demo/live-operator-advisory`. Focused tests prove the endpoint combines a live diagnostic projection and model-backed candidate advisory when verified candidate changes exist. The real current run remains blocked because the configured Salesforce app repository has no candidate changes, so Neo returns `CANDIDATE_ADVISORY_UNAVAILABLE` rather than fabricating advisory output |
| Minimal truthful foundation | DONE | 2 | The endpoint accepts no body, query, org alias, authorization header, `x-*` override or caller-selected scope. After Astra review, the implementation no longer creates a direct `sf org display` shortcut from only an alias; the real service projects the existing governed live-baseline boundary when available or blocks as `LIVE_DIAGNOSTIC_NOT_CONFIGURED` |
| Test it | DONE | 1 | Focused Python tests passed for positive in-process composition, independent blocker reporting, API caller-scope rejection and the bounded Salesforce CLI helper |
| Document pass/fail | DONE | 1 | This section records that live Salesforce connectivity evidence and candidate LLM evidence are still separate. The current public slice is not the final evidence-bound live reasoning vertical because live Salesforce read payload is not yet part of the specialist context pack |
| Move to next capability | READY | 0 | Next slice should bind source-derived live Salesforce read evidence into the same graph context/LLM advisory pack, or first introduce an authorized local candidate change so the real candidate advisory path has non-empty input |

### Additional task history

- The Windows Salesforce CLI adapter now invokes the platform executable through a bounded timeout
  and sanitized subprocess environment.
- `demo.live-operator-advisory` returns HTTP 200 only when both the live diagnostic passes and at
  least one model-backed advisory capture is available. Empty candidates, disabled providers,
  unavailable live diagnostics and caller-supplied scope return bounded non-release responses.
- Current real-machine blocker: no verified local candidate changes are present in the configured
  Salesforce app repository. This is a data/setup precondition, not a reason to weaken candidate
  validation or substitute fixture output.

## Continuation: real AUT candidate LLM advisory

Selected capability: `assurance.candidate-comparison` plus `reasoning.graph-grounded-agent` over
the actual configured Salesforce app repository.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Make it run end-to-end | DONE | 3 | Created a local-only AUT candidate locator drift in `strategicDealWorkbench.html`, refreshed the AUT catalog, and ran the current Neo candidate path. The real OpenAI run produced two side analyses and six specialist captures over the verified local-Git candidate |
| Minimal truthful foundation | DONE | 3 | Fixed the specialist context allowlist for current source-foundation gaps, raised bounded specialist artifact output capacity to handle real graph-backed captures, and deduplicated workflow gaps before enforcing finite result limits |
| Test it | DONE | 2 | Focused tests passed for specialist gap allowlist, candidate advisory composition, workflow result bounds and Salesforce CLI timeout/failure behavior |
| Document pass/fail | DONE | 1 | This is real local candidate + real LLM advisory evidence. It is not a deployed-candidate or live Salesforce acceptance receipt, and the AUT change remains local/uncommitted unless a separate deployment/mutation milestone is authorized |
| Move to next capability | READY | 0 | Next slice should either run the live-operator advisory endpoint with an accepted governed live diagnostic, or bind source-derived live read payload into the same specialist context pack |

## Current two-hour execution run - 2026-09-11

Started: 2026-09-11T20:49:02+05:30

Hard stop target: 2026-09-11T22:49:02+05:30

### Current scope

Complete the most valuable remaining live-demo path without broadening scope:

1. Preserve the latest time-boxed execution method as the active project instruction for this
   sprint.
2. Make the operator demo story coherent from current evidence: real local AUT candidate + real LLM
   advisory, live Salesforce read proof, and live headless browser diagnostic proof.
3. If the single public endpoint cannot truthfully combine those inside the timebox because a
   governed live-baseline receipt is missing, do not weaken the boundary. Record the missing gate
   and provide the runnable demo sequence from existing safe commands/evidence.

### Time allocation

| Window | Subtask | Completion bar |
|---:|---|---|
| 0-10m | Save latest approach and bias deferrals | This file plus deferred queue updated; no older doc can silently broaden current scope |
| 10-35m | Operator demo coherence check | Verify current candidate advisory, live read and browser diagnostic can be demonstrated from safe commands/evidence |
| 35-70m | Patch only demo-blocking gaps | Fix at most the minimum gap that prevents a truthful operator demo; otherwise defer |
| 70-100m | Focused tests and runbook | Run focused checks and update demo scenario/how-to docs |
| 100-120m | Commit/checkpoint | Regenerate knowledge, run genericity/secret checks and commit completed current-scope changes |

### Deferred bias rules for this run

| Bias source | Current handling |
|---|---|
| Production-grade live campaign acceptance | Defer unless needed to avoid a false demo claim |
| Full metadata deploy/restore/reconciliation | Defer; local candidate evidence remains local unless separately authorized |
| Complete GraphRAG/vector/Chroma implementation | Defer; graph-grounded candidate context already proves the selected advisory path |
| UI polish outside the selected demo path | Defer |
| Broad foundation refactors discovered during testing | Fix only if they block current demo output or create a safety/false-claim issue |

### Task history

| Item | Status | Attempts | Notes |
|---|---|---:|---|
| Clock started | DONE | 1 | Start 2026-09-11T20:49:02+05:30; hard stop 2026-09-11T22:49:02+05:30 |
| Latest approach saved | DONE | 1 | This section records the merged latest approach and current-scope override |
| Operator demo coherence check | DONE | 1 | Current demo is a truthful three-part sequence: real local AUT candidate + real LLM advisory, live Salesforce read proof, and live headless browser diagnostic proof. It is not the full live deployed-candidate campaign |
| Demo-blocking patch decision | DONE | 1 | No code guardrail was weakened. The only immediate patch is documentation: make the runnable sequence explicit and defer production campaign closure rather than forcing a misleading 200 response |
| Real LLM candidate advisory recheck | DONE | 1 | Current OpenAI-backed run passed with 2 analyses, 6 specialist captures, 15 blocking gaps, `releaseEligible=false`, view digest `d1cf1e6b93c6f4d6ff6981a3ed48b618b66ab84ec759f6e5b32ca8cc3d307fba` |
| Live Salesforce read recheck | DONE | 1 | Current CLI/API read-only proof passed against `caip-dev`: org display succeeded, one Opportunity was queried, custom REST returned a bounded body digest `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`; no raw org payload was printed |
| Live headless browser diagnostic recheck | DONE | 2 | First smoke retry lacked the required profile env binding and blocked as `PROFILE_SOURCE_INVALID`; second run passed with `diagnosticOnly=true`, `releaseEligible=false`, cleanup closed context/browser, execution digest `a24f5ca68185ea4fc31cdcf705084fa15a782ea19d09e0d9f43706b5fd72a661` |

### Current runnable demo sequence

Use this sequence for the next operator walkthrough:

1. Real LLM candidate advisory over the configured Salesforce app local Git state. Expected
   evidence: candidate available, analysis count greater than zero, successful specialist captures
   greater than zero, bounded view digest, and explicit release-blocking gaps. This proves the
   agentic reasoning path with the configured model provider.
2. Live Salesforce read proof through the host-owned Salesforce CLI/API/custom REST read path.
   Evidence must remain sanitized and read-only.
3. Live headless browser diagnostic from `packages/browser`: `npm run live:profile` and
   `npm run live:smoke`. Evidence remains diagnostic-only and non-release-eligible.
4. Optional `POST /api/v1/demo/live-operator-advisory` composition. Show it only as a truthful
   composition/gap projection; do not relax the endpoint if a governed live diagnostic or candidate
   advisory precondition is missing.

Deferred from this two-hour run: deployed-candidate mutation, restoration/reconciliation, full
live campaign gate closure, Chroma/GraphRAG breadth, UI polish outside the selected route, and broad
architecture refactors.

## Continuation: live read bound into LLM advisory context

Selected capability: `demo.live-operator-advisory` context binding for the current live-integrated
vertical. This closes the immediate foundation defect that live Salesforce read evidence and LLM
candidate advisory were computed beside each other instead of inside the same specialist context.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Make it run end-to-end | DONE | 2 | `run_live_operator_advisory_demo()` now passes the governed live diagnostic projection into `analyze_current_candidate()`, and the specialist context compiler receives a non-authorizing unresolved fragment derived from the live read summary when the live baseline is completed |
| Minimal truthful foundation | DONE | 2 | The live fragment is digest-addressed, marks `READ_ONLY_DIAGNOSTIC_CONTEXT`, cites only sanitized counts/digests/status, binds to an existing verified graph seed, and explicitly says it cannot satisfy deploy, browser, restore, release or mutation evidence |
| Test it | DONE | 2 | Focused regression passed 28/28, proving positive live-read context inclusion, abstention does not count as LLM advisory, independent blocker reporting and API caller-scope rejection |
| Document pass/fail | DONE | 1 | This section records that gap #1 is addressed at the foundation/operator-composition layer. The full live vertical is still incomplete until candidate deploy, deployed-candidate browser acceptance, restore/reconciliation and live acceptance gates pass |
| Move to next capability | BLOCKED | 1 | Astra review blocks deployment until the restore/reconciliation mechanism covers the exact current LWC HTML candidate mutation, not only the older metadata property case |

Current remaining live-vertical gaps at this point in the chronology, before the scoped LWC
deploy/readback/restore continuation below:

1. Local AUT candidate change is not deployed to Salesforce.
2. No live deployed-candidate browser acceptance has run.
3. No metadata restore/reconciliation campaign has run for the exact current LWC candidate change.
4. Full live Salesforce acceptance gates/receipts are incomplete.
5. The Salesforce app repo intentionally retains uncommitted demo candidate changes until a protected
   mutation/deploy milestone is explicitly authorized.

## Continuation: scoped LWC deploy, live readback and restore

Selected capability: deployed-candidate readback for the current AUT locator-drift candidate.
The user authorized the lightweight archive/manifest approach for this demo milestone.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Preimage archive | DONE | 2 | Created operation `restore-20260911T155750Z-1e31703f`, retrieved the live `strategicDealWorkbench` bundle into ignored runtime artifacts, and wrote a sanitized manifest with preimage/candidate hashes. Live preimage differed from Git HEAD only by EOL normalization |
| Check-only | DONE | 1 | Scoped `LightningComponentBundle:strategicDealWorkbench` dry-run/check-only deployment succeeded with explicit `NoTestRun` |
| Candidate deploy | DONE | 1 | Scoped LWC bundle deployment succeeded. No Apex, data, permission, destructive or broad package mutation was dispatched |
| Live browser readback | DONE | 3 | Added a generic read-only candidate readback CLI using the existing browser worker and fixed host-owned Workbench path. First two attempts proved browser cleanup but missed the host marker; final run passed with one `lightning-button[data-action="save-evaluate-live"]`, `readbackMatched=true`, and closed browser/context cleanup |
| Restore/reconcile | DONE | 1 | Deployed the archived preimage bundle back to Salesforce, retrieved it again, and verified restored HTML SHA-256 equals archived preimage SHA-256 |
| Full live campaign gates | PARTIAL | 1 | This proves the scoped LWC deployed-candidate browser readback and restoration for the candidate marker. It is not yet the complete signed live campaign receipt set across all Salesforce acceptance gates |

Sanitized receipt digests:

- Candidate live readback execution digest: `dd6d236e5ec754c229b52d2d42559fb9d4f096f7b526874b92c9b9f500ab466b`.
- Browser profile digest: `665ff873a5a90ea6f646ca1833566e2340eefbc221c8d3946e71166a072d96b8`.
- Archived/restored preimage SHA-256: `7777e3ef35d0709e091acf6b076d7dd62767ab44f0382eee476d78d4bcdbd5a3`.

Remaining boundary after this continuation:

- The local Salesforce app repo still intentionally contains the candidate LWC/knowledge changes for
  analysis replay; the live org has been restored to the archived baseline.
- The full acceptance profile remains incomplete because this milestone covers only the scoped LWC
  deploy/readback/restore path, not every API, metadata, browser, test and campaign gate.

## Continuation: runtime step-context and bounded retry envelope

Selected improvement: use the user's blueprint as a bug-fix aid without widening into a new
planner/vector/vision architecture. The implemented slice adds a small runtime step-context
envelope to the live candidate readback CLI.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Runtime context envelope | DONE | 1 | `live:candidate-readback` now emits sanitized intent kind, strategy, retry policy, start-path digest, host attribute name, expected-value digest and expected postcondition |
| Bounded retry loop | DONE | 1 | Readback attempts are capped at three, retry only sanitized readback failures with closed browser/context cleanup, and record per-attempt status, lifecycle, candidate count, cleanup, error code and execution/input digests |
| Scope control | DONE | 1 | The CLI remains generic and host-configured through bounded environment values; it stores no raw path, marker value, selector, session URL, username, org payload or credential |
| Test it | DONE | 1 | Browser build passed and 23 focused Playwright worker/profile/coordinator tests passed, including projection no-leak coverage |

Deferred from this blueprint-inspired slice: broad LangGraph rewrite, vector example retrieval,
vision/bounding boxes, full layout/FLS extraction and forced JavaScript-click fallback.

## Continuation: full live Salesforce campaign acceptance status

Selected item: full live Salesforce campaign acceptance across API, metadata, browser, tests,
candidate deployment, reconciliation and release policy.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Full gate-set visibility | DONE | 1 | `GET /api/v1/live-campaigns/{campaign_id}/status` now exposes all required gate IDs with kind, receipt type, accepted evidence phases, required-for-completion flag, locally valid count and missing required gate IDs |
| Truthful acceptance boundary | DONE | 1 | `accepted_completion_numerator`, `requirements_satisfied` and `release_eligible` remain false/zero unless the product-owned validator can accept the complete signed campaign; local replay-valid gates are reported separately and cannot become release authority |
| API/metadata/browser/test/reconciliation coverage | PARTIAL | 1 | The acceptance profile enumerates those gates (`SF-L03` REST, `SF-L04` custom REST, `SF-L05` metadata, `SF-L06`-`SF-L09` browser/recovery, `SF-L08` tests, `SF-C01`-`SF-C06` candidate/check/deploy/assert/restore). The status API can now show which are missing, but no new signed receipts were minted in this pass |
| Test it | DONE | 1 | `tests/test_live_campaign_status.py` passed 10/10, including empty-campaign missing-gate projection, full local replay-valid projection and API serialization/no-secret checks |
| Commit boundary | READY | 0 | This is a status/readiness enhancement and documentation update. It does not deploy, mutate Salesforce, restore metadata, or satisfy the full signed campaign |

Current result: the system can now present the full 15-gate campaign matrix truthfully. The scoped
LWC deploy/readback/restore evidence remains useful demo evidence, but full live Salesforce
campaign acceptance still requires trusted current receipts for every required gate and successful
validator replay over the durable ledger.

## Continuation: business-action browser acceptance

Selected item: move beyond deployed-marker readback toward a real business browser flow.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Browser action primitive | DONE | 1 | Added `BUSINESS_ACTION` worker mode that fills configured `data-field-api` fields, clicks the configured `data-action` submit control, waits for configured success status text, and reports only counts/booleans/digests |
| Mutation authority boundary | DONE | 1 | Default live profiles remain read-only/readback-only. Business actions require `NEO_BROWSER_ENABLE_BUSINESS_ACTION=true`, `mutationActionsEnabled=true`, and `BUSINESS_ACTION` in permitted modes |
| Runnable CLI | DONE | 1 | Added `npm run live:business-action`, requiring a trusted live profile plus env-supplied start path, fields, submit action, success text and Salesforce persistence assertion JSON |
| Persisted outcome assertion | DONE | 1 | After browser success, the CLI runs a bounded Salesforce CLI `data query` against the configured object/match field and verifies expected persisted fields; output contains only digests and match booleans |
| Live execution | NOT_RUN | 0 | No live mutation was dispatched in this pass. A safe synthetic action recipe and current business-action profile should be generated immediately before live testing |
| Test it | DONE | 1 | Browser lint, live build and 27 focused Playwright worker/profile/coordinator tests passed |

Boundary: this is now a runnable acceptance mechanism for fill/edit/save/evaluate plus persistence
verification. It is still not a signed Salesforce campaign gate receipt and remains
`releaseEligible=false` until the live receipt producer/ledger issues and validates the corresponding
campaign receipt.

## Continuation: general self-healing loop for business actions

Selected item: extend browser business actions beyond fixed locators into a bounded
observe → choose alternate locator → execute → evaluate loop.

| Step | Status | Attempts | Notes |
|---|---|---:|---|
| Observe | DONE | 1 | Business actions still begin with a sanitized DOM capture and exact object/field/action intent; raw field values are represented only by digests |
| Choose alternate locator | DONE | 1 | Field fills and submit actions now fall back to the existing metadata-aware locator healer when the direct `data-field-api` or host-tag action locator fails |
| Execute | DONE | 1 | The worker fills the healed field/action locator only when the candidate is unique, visible, enabled, metadata/action scoped and non-readonly |
| Evaluate | DONE | 1 | The worker requires configured success status text and the live CLI still performs the post-action Salesforce persistence assertion before returning `PASSED` |
| Heal evidence | DONE | 1 | Receipts include healed/abstained counts and strategy names, without exposing raw selectors, form values, session URLs, record IDs or org payloads |
| Live execution | NOT_RUN | 0 | No new live Salesforce mutation was dispatched in this pass |
| Test it | DONE | 1 | Browser lint, live build and 23 focused Playwright worker/coordinator tests passed, including stable-action submit healing |

Boundary: this is a generalized self-healing execution loop for browser business actions, not a
signed live-campaign receipt producer. Ambiguous or unsafe candidates still abstain rather than
forcing an action.
