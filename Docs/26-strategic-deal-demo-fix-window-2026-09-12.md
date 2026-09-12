# Strategic Deal Demo — two-hour fix window, 2026-09-12

Started: 2026-09-12T05:32:26+05:30. Hard stop: 2026-09-12T07:32:26+05:30.

Scenario source: [Strategic Deal Change Assurance Demo](24-strategic-deal-change-assurance-demo.md).
Test pass and original bug map: [test pass](25-strategic-deal-demo-test-pass-2026-09-12.md) and
`quality/reviews/strategic-deal-demo-bug-map-2026-09-12.json`.
Evidence directory: `.runtime/demo-evidence/fixwindow-20260912-053226`.

This document records a timeboxed fix pass. It is development evidence only. It does not authorize
release, and it does not claim any signed live Salesforce campaign gate.

## Result summary

| Bug | Severity | Status | Verification |
|---|---|---|---|
| `DEMO-BOOT-001` | P2 | FIXED | Launcher starts API and dashboard; health reports ready |
| `DEMO-API-001` | P1 | FIXED | Candidate analysis returns HTTP 200 with real model-backed captures |
| `DEMO-BROWSER-001` | P1 | FIXED | 159/159 browser contract tests pass |
| `DEMO-BROWSER-UI-001` | P2 | FIXED | Live headed run with `headedMode=true` and clean closure |
| `DEMO-LIVE-BA-001` | P1 | FIXED | Live run `PASSED`: success text matched and record persisted |
| `DEMO-LIVE-BASELINE-001` | P1 | PARTIAL | Three expired/stale authority layers fixed; one product defect remains |
| `DEMO-TRUTH-001` | P2 | OPEN (new) | Reported `submitted` can be true when no submission occurred |
| `DEMO-APEX-001` | P1 | OPEN (new) | `APEX_TEST` derivations `UNSUPPORTED` block baseline plan composition |

## Corrected root causes

The original bug map's hypotheses were wrong or incomplete in three places. The corrections matter
more than the fixes.

### `DEMO-API-001` was two independent faults

The provider conflict and the candidate pipeline failure were unrelated. Clearing the conflict did
not make the candidate API work. `PROVIDER_CREDENTIAL_SOURCE_CONFLICT` came from an `OPENAI_API_KEY`
exported in the parent shell that differed from `.env`; the launcher now removes provider variables
from the child environment rather than weakening the guard. `FOUNDATION_PIPELINE_UNAVAILABLE` was a
setup precondition: `VERIFIED_CHANGE_CAPTURE` failed with `NO_CANDIDATE_CHANGES` because the
configured Salesforce app repository was clean. The demo candidate was reintroduced as a local,
uncommitted presentation change and the app catalog was rebuilt.

### `DEMO-LIVE-BASELINE-001` is four layers, not one

Each fix exposed the next blocker:

1. `LOCAL_VALIDATION_PHASE_POLICY_INVALID` — the phase policy had expired. Its validity is fifteen
   minutes. Regenerated from the current compilation and re-pinned; the only exemptions are
   `GENERATED_INDEX` catalog files, and no runtime or Apex file is waived.
2. `CLASSIFICATION_AUTHORITY_INVALID` — the baseline enrollment had expired the previous day. Its
   validity is about twenty-one minutes. Regenerated from a fresh live six-step identity
   classification (`caip-dev`, `DEVELOPER_EDITION`, API v67.0).
3. `LIVE_BASELINE_TARGET_POLICY_DENIED` — authorized target digests are candidate-specific, so they
   must be recomputed whenever the candidate changes.
4. `LIVE_BASELINE_PLAN_BLOCKED` — the remaining blocker is a product defect, not stale
   configuration. Three `APEX_TEST` derivations are `UNSUPPORTED`, and any unsupported derivation
   blocks plan composition with no partial-subset fallback. Tracked as `DEMO-APEX-001`.

**Operational consequence for any demo:** the phase policy and enrollment must be regenerated
immediately before a live run. A demo prepared more than roughly fifteen minutes ahead fails exactly
as the 2026-09-12 test pass recorded.

### `DEMO-LIVE-BA-001` was not a Salesforce save-contract problem

The original hypothesis was disproved directly: the identical record saved successfully through the
Salesforce API, so the platform accepted the data. Four separate defects were found by bisecting a
visible browser run.

1. **Date format.** `CloseDate` was supplied as ISO `2026-12-15`, while the org's UI requires
   `MMM d, yyyy`. `lightning-record-edit-form` rejected the entry client-side and never dispatched
   `onsubmit`, so no page-level success, error or busy state appeared. This was found only because a
   new digest-only post-submit signal reported `alertPresent=false` and `statusPresent=false`,
   proving the handler never ran.
2. **Checkbox field.** `Strategic_Deal__c` is a Boolean rendered as a checkbox. Every fill strategy
   explicitly excluded checkboxes and the component fallback assigned the string `"true"`, so the
   save failed. A dedicated checkbox strategy now sets the checked state from the declared value.
3. **Persistence verification could never pass on Windows.** The CLI launches through `cmd.exe`,
   where `resolveLaunch` requires every argument to match `^[A-Za-z0-9._-]+$` to prevent interpreter
   injection. A SOQL string can never satisfy that. The query now travels in a file with an
   allowlist-safe bare name, so the guard is preserved rather than relaxed.
4. **Submit click target.** The click addressed the `lightning-button` host instead of its
   interactive descendant; it now prefers the real control when one is present.

A fifth issue was self-inflicted during the window and reverted: requested subprocess environment
variables are restricted to an allowlist of boolean flags, so injecting `PATH` made the runner throw.

## Verified evidence

- Live business action, headed and paced at 600 ms: `status=PASSED`, success text matched, record
  persisted in `caip-dev`, `StageName` and `Discount__c` asserted true, context and browser closed.
- Candidate analysis through the running API: HTTP 200, two side analyses, six model-backed
  specialist captures, `release_eligible=false`, evidence `INCOMPLETE`.
- Browser contract suite: 159 passed. TypeScript compiles clean.
- Focused Python regression: 48 passed, including generated-knowledge freshness.
- `ruff check` clean; project index and application graph regenerated.

## What must not be claimed

- No signed live Salesforce campaign gate passed. The read-only baseline remains `BLOCKED`.
- A passing business action is browser and persistence evidence, not a campaign receipt, and
  `releaseEligible` stays false.
- The headed mode changes only the local launch surface; the trusted profile still pins
  `headless: true`.

## Remaining work in priority order

1. `DEMO-APEX-001` — close the source-derived Apex method inventory so every obligation derives a
   supported target, then recompute authorized digests and rerun the read-only baseline.
2. `DEMO-TRUTH-001` — derive `submitted` from an observed post-submit transition, or rename it.
3. Add a regression test for the checkbox fill strategy and the file-based persistence query.
4. Consider a host-owned demo runbook that regenerates both short-lived authorities immediately
   before a live run.

## Second fix window — 45 minutes, 2026-09-12T06:33:35+05:30

Scope: `DEMO-TRUTH-001`, `DEMO-APEX-001`, `DEMO-LIVE-BASELINE-001`.

| Bug | Status | Outcome |
|---|---|---|
| `DEMO-TRUTH-001` | FIXED | `submitted` now derives from a completed click dispatch, not from locating a candidate. 159/159 browser contract tests pass |
| `DEMO-APEX-001` | OPEN | Root cause proven and fix designed; implementation deliberately stopped inside the timebox |
| `DEMO-LIVE-BASELINE-001` | PARTIAL | Still `LIVE_BASELINE_PLAN_BLOCKED`, now proven to be the required-partition rule rather than expired authority |

### `DEMO-APEX-001` is a missing partition, not a broken inventory

The earlier description was wrong. Measured behaviour:

- With the LWC presentation candidate, the three selected tests are LWC Jest tests. The compiler
  has only an Apex partition for selected tests, so all three derive as
  `NON_APEX_TEST_OBLIGATION` and `UNSUPPORTED`.
- With an Apex-only candidate, no `APEX_TEST` targets derive at all.

Host policy lists `APEX_TEST` in `requiredPartitions`, so `LiveTargetPlanProducer._validate` raises
`MISSING_PARTITION` when the partition is empty and `UNSUPPORTED_TARGET` when it is non-Apex. Either
way the read-only baseline is blocked by a partition whose gate, `SF-L08`, is not in the fixed
read-only gate set `SF-L03`, `SF-L04`, `SF-L05`.

Designed fix, not yet implemented: `LiveTargetPlan` already carries `phase_read_only_gate_ids` when a
phase policy applies. Thread that gate set into `_validate`
(`src/neo_sf_q_intel/live_target_plan.py`, around the required-partition loop) and require only
partitions whose mapped gate is executable in the plan phase. Targets that do derive must still be
validated and authorized exactly as today, and `SF-L06` through `SF-L09` must remain non-passing.
This needs positive, negative and phase scenario-independence tests before acceptance.

It was stopped deliberately: two plan producers exist, the gate set is not currently threaded into
the validator, and a half-finished change to a governance boundary is worse than an open bug with a
precise design.

### Candidate state

The working candidate in the Salesforce app repository is now the discount policy boundary change
(`>` to `>=` in `StrategicDiscountPolicy.cls`, Doc 24 theme one), not the LWC presentation change.
The machine-local phase policy was regenerated for that candidate. Both remain uncommitted in the
app repository by design.

### Rejected shortcut

Removing `APEX_TEST` from `requiredPartitions` in the machine-local reviewed target policy would
unblock the baseline in one line, but it hides the missing partition behind operator configuration
instead of fixing the producer. The product-code fix above was chosen instead and remains open.

### Second window addendum — partition fix landed

`DEMO-APEX-001` moved from OPEN to PARTIAL. `HostOwnedLiveTargetPlanProducer` now accepts an
explicit executable gate set, and `compose_candidate_live_plan` passes `READ_ONLY_BASELINE_GATES`
when a phase policy applies, so a read-only baseline is no longer blocked by a partition whose gate
it cannot execute. `MISSING_PARTITION` for `APEX_TEST` is gone and the repository default-deny
policy's producer pin was refreshed.

The remaining single gap is `UNSUPPORTED_TARGET` on `BROWSER_INTENT`: its action class or
application intent is not in the reviewed host policy allowlists, and `SF-L07` is also outside the
read-only gate set. Whether an unauthorized out-of-phase target should block a phase that cannot
execute it is a governance semantics decision; it was left open rather than decided unilaterally.
