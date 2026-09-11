# Development stop checkpoint — 2026-09-11

> **Resumed by explicit user instruction.** Preserve this file as the immutable interruption
> record. Execute only the corrected, non-deterministic-only boundary in
> [the frozen three-hour agentic live vertical](22-frozen-three-hour-agentic-live-vertical.md).

## Purpose and authority

The user stopped all feature development and live-execution work on 2026-09-11 and requested a
complete documentation checkpoint. This document records the working-tree state at that stop. It
does not authorize Salesforce access, deployment, mutation, recovery, release or capability
promotion. Resume only through a new explicit user instruction and a fresh review of the files and
machine-local authority described below.

No commit or push was performed at this checkpoint. Local Git is the product source boundary; a
remote repository is optional and its absence must not block analysis. Secret-bearing `.env` and
`.runtime` content remains ignored and must never be committed, copied into reports or printed.
The machine-readable scope review is intentionally `IN_PROGRESS`, not `APPROVED`, while the open
P1 findings and interrupted host/browser integrations remain.

## Executive status

| Lane | Current truth | Acceptance status |
|---|---|---|
| Dashboard | API and Next.js processes were restarted; the page rendered visibly at the configured local URL and all 21 dashboard Chromium tests passed | Locally usable; browser tests use mocked API responses and are not live Salesforce acceptance |
| Model provider | API health reported OpenAI ready after the child process was started with one coherent `.env` credential source; no key value was displayed or copied | Configuration ready; no model invocation was required for this repair |
| PostgreSQL | API health reported ready using Neo's dedicated schema; unrelated public/pgvector tables are ignored | Runtime foundation verified previously; not a live Salesforce result |
| Source compiler | Closed `source-operations` contract derives exact targets, one locator-rebind target and nine read-only obligations | Offline source/compiler foundation verified; no live execution credit |
| Locator discovery | Metadata/action identity candidate selection is source-neutral, shadow-aware and ambiguity-abstaining | Offline foundation |
| Browser healing probe | Baseline, stale/discovery and rerun stages exist and currently pass eight focused headless tests | **Not production-ready:** one open closed-schema validation P1 is recorded below |
| Metadata transaction | Exact one-member `SET_SCALAR`, check-only/deploy/restore journal, concurrency quarantine and crash-recovery foundation implemented | Offline foundation; no live check-only, deploy or restore was run |
| Source-to-healing bridge | Complete source target and metadata-preimage translation implemented | Offline foundation; production browser dispatch and accepted receipt bridge absent |
| Host recovery factory | Partially implemented when development was stopped | Interrupted/unreviewed; do not activate |
| Live Salesforce | Earlier read-only identity/API/browser diagnostics reached the enrolled non-production org | Zero live acceptance receipts; no metadata or business-data mutation occurred |

## Verified evidence retained

The following results were observed before the stop. They are test evidence for the exact bytes at
their run time, not a claim that every later concurrent edit was included:

- Full Python regression before the latest healing additions: **1,547 passed, 3 explicitly skipped**.
- Source/compiler/target-plan integration: **161 passed**; final closed contract slice **62 passed**;
  independent actual-AUT source/locator capture **63 passed**; AUT catalog **8 passed**.
- Metadata recovery, live-healing sequence and timezone slice: **150 passed, 1 skipped**. The skip is
  the Windows account's inability to create a symlink; hardlink, path and ZIP defenses were tested.
- Source-to-healing bridge plus metadata/healing integration: **174 passed, 1 skipped**; isolated
  bridge suite **55 passed**.
- Browser healing probe after four Astra corrections and the composed disabled-state correction:
  **8 passed**; TypeScript compilation passed. The open extra-key finding below was discovered after
  that run and remains unfixed by user instruction.
- Dashboard: **21 passed** in Chromium after both local services were restarted. Direct API health
  returned `status=ok`, persistence ready and provider ready. The in-app browser visibly rendered
  the complete dashboard.
- AUT graph at the last regeneration: **1,014 nodes / 3,700 edges**.

Run a fresh full regression after any resume. Interrupted agent messages, earlier passing counts and
generated reports cannot substitute for a clean post-resume run.

## Resolved findings

### Metadata recovery safety

Independent adversarial review reproduced and the implementation corrected these P1 defects:

1. `recover()` could race an active candidate window and restore prematurely.
2. Exact restoration could overwrite an unrelated concurrent same-page edit.
3. Restart recovery could issue new transport after an unproven non-quiescent process.
4. A dispatch fence could cross authority expiry immediately before the external effect.
5. Metadata API test selection used `Class.method` where deployment accepts class names.
6. The editor initially required an existing property and could not safely preserve an explicitly
   authorized absent pre-state.

The frozen offline adapter now uses exclusive execution ownership, independent durable dispatch and
process fences, authenticated journal replay, bounded exact package retrieval, exact source-compiled
`ABSENT`/`PRESENT`-baseline scalar semantics, candidate/preimage readback, concurrent-drift
quarantine and exact original-byte restoration. Astra reported no remaining P0/P1 in that frozen
offline slice. Salesforce does not expose an atomic metadata compare-and-swap; a live run still
requires an exclusive demo change window and must not claim stronger concurrency protection.

### Locator/healing probe corrections completed

Before the stop, Astra found and the probe corrected:

- a decoy original hook could pass while semantic assertions ran against an unrelated control;
- action identity was not scoped to the declared Salesforce object;
- partial field/action assertion sets and duplicate locator/identity sets were admitted;
- unchanged but valid obligations failed the complete rerun;
- custom-element or composed-ancestor disabled state could appear enabled.

The corrected probe binds baseline candidates through composed ancestry, scopes actions by object,
requires exact field/action assertion sets, distinguishes affected from unaffected obligations and
reuses the composed interaction-state guard. It never clicks, fills, submits, saves or persists raw
DOM/locator material.

### Dashboard outage

The reported blank/unavailable dashboard was caused by stopped local API and Next.js processes, not
a rendering regression. Both services were restarted and visually verified. API startup also
reported `PROVIDER_CREDENTIAL_SOURCE_CONFLICT` because the host process and `.env` supplied different
OpenAI values. The restarted API used a single coherent `.env` source without logging the value and
reported the provider ready. Preserve the conflict guard: future launch tooling should sanitize the
child environment rather than weaken provider validation.

## Open P0/P1 issues and interrupted work

### P1 — browser-probe closed-schema bypass

`packages/browser/src/healing-probe.ts` reconstructs most validated fields but does not yet reject
unknown keys on target, obligation, original-locator and semantic-identity objects. Two duplicate
obligations can add different unknown nonce properties, receive different canonical uniqueness
hashes and still address the same control. On resume, enforce exact object keys before uniqueness,
align the obligation maximum and token patterns with the Python source model, add adversarial tests,
and require a fresh Astra review. Do not use this probe live before that correction.

### P1 — deployment Apex class inventory is not source-closed

The bridge originally produced a metadata intent with `NoTestRun`, which conflicts with the AUT rule
requiring named tests. Metadata API accepts test class names and executes the entire selected class,
while `source-operations` currently records method obligations. Selecting a class may therefore run
undeclared methods and break exact result accounting. On resume, compile and authorize the complete
method inventory of each selected source-captured class before check-only. Extra methods must block
before Salesforce execution, not merely be detected after they have run.

### P1 — host production factory was interrupted

`src/neo_sf_q_intel/metadata_recovery_host.py` and
`tests/test_metadata_recovery_host.py` were being developed when the stop arrived. The agent reported
focused tests in progress, but the final bytes did not receive a completed independent review or a
fresh root test run. Treat both files as unreviewed working code. A correct resume must verify:

- digest-pinned real `sf` executable/version, alias and API binding at the actual spawn boundary;
- signed current classification, source intent, check-only, restore-rehearsal, mutation and recovery
  authorities;
- an independent SQLite dispatch/process fence and one-use transaction claim;
- an authenticated, fsynced local snapshot of all prevalidated recovery evidence before effects;
- recovery independence from a remote ledger or subsequently changed source checkout;
- no candidate retry or authority widening after an uncertain/unknown job.

### P1 — production browser dispatch and receipt bridge absent

No production TypeScript healing dispatcher was completed. The intended minimum design was a
digest-pinned machine-local profile, fixed CLI session broker, exact source-resolved Lightning
record path, headless-only `BASELINE`/`STALE_AND_DISCOVER`/`RERUN` commands, the complete obligation
set, digest-only observations and unconditional browser/context cleanup. It must accept no caller
alias, path, record ID, selector or target subset. Existing `runReadOnlySmoke()` remains diagnostic
only and cannot be relabelled as healing evidence.

### P1 — source/org custom REST schema drift

Earlier bounded live reads reached all seven custom endpoints with HTTP 200, but the deployed org
returned contract schema `1.0.0` while the current source contract requires `1.1.0` history parent
binding. Neo correctly reported `LIVE_READ_RESPONSE_INVALID` and accepted zero partial observations.
The dirty AUT tree also contains unrelated API 1.1 work and a discount threshold change. Never
deploy the AUT root. Any future schema deployment needs separate exact authority, an isolated
manifest, named tests, restoration/reconciliation and post-deploy live receipts.

### Stale machine-local proposals and policy pins

Compiler/source changes invalidate previously generated phase, enrollment and target proposals.
Machine-local reviewed policies must be regenerated from current code and source rather than
manually rehashed. The last reported producer implementation pin remained unchanged, but transitive
phase/enrollment hashes changed. Do not enable live execution with stale `.runtime` files.

## Live Salesforce security boundary at stop

The only user-authorized mutation discussed was a temporary presentation-only property on
`FlexiPage:Strategic_Deal_Record_Page`, component `c:strategicDealWorkbench`, identifier
`dealWorkbench`, property `locatorVariant`, with an exact admitted baseline (`ABSENT` or
`PRESENT=baseline`) and alternate `reordered`. The intended assertions were eight field identities
plus one save-action visibility/enabled probe. Business-data mutation was forbidden.

That mutation was **not dispatched**. No check-only, candidate deploy, browser-healing acceptance or
restore operation was performed. No permissions, users, authentication settings, Connected Apps,
certificates, custom policy values, Apex/API source or business records were changed. A future run
must retrieve and authenticate the live preimage, validate an isolated one-member package, prove the
complete selected Apex class inventory, obtain signed one-use mutation/recovery authority, run the
headless probe, restore exact bytes and reconcile zero scoped residue. Unknown job state or
concurrent drift requires quarantine, not blind retry or overwrite.

## Exact resume priority

This historical order is superseded where incomplete by the frozen three-hour plan linked above.
In particular, completion now requires a real graph-grounded provider invocation joined to the
same accepted live Salesforce and Playwright trace. Do not treat this list as permission to deliver
a deterministic-only result. Preserve the following unfinished-work order within that boundary:

1. Fix and re-review the browser-probe closed-schema P1.
2. Close source-derived Apex class/method inventory and remove the `NoTestRun` mismatch.
3. Review or discard the interrupted host factory; run its complete focused and integration tests.
4. Implement the minimal source-bound browser dispatcher and signed observation bridge.
5. Regenerate machine-local proposals and execute read-only classification/preflight.
6. Only with fresh explicit authority, run isolated check-only, candidate, headless assertions and
   exact restore/reconciliation against the enrolled non-production org.
7. Run the complete Python, TypeScript, Playwright, catalog, genericity, secret and absolute-path
   gates; update the review ledger and attach only sanitized receipts.

Optional UI polish, new connectors, vector work, extra agents and broader Salesforce scenarios stay
outside this resume line until the one live vertical is accepted.
