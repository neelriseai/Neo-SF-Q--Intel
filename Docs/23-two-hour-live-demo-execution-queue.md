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
