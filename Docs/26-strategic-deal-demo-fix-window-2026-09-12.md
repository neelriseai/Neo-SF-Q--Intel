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
