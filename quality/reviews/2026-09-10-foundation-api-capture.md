# R0.4 host-owned foundation API capture review

Date: 2026-09-10

Capabilities: `source.verified-change-set`, `source.tree-graph-production`,
`source.change-seed-mapping`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may claim a fresh application-service and HTTP capture of the current R0.4a/R0.4c/R0.4d
foundation. The service and POST accept no caller scope, current-verify every successful capture,
return only bounded `CandidateFoundationEvidence`, preserve expected abstentions, and never persist
or merge the projection into `AssuranceRun`. The unique candidate-side DX project must equal the
configured nested Salesforce app root inside the configured Git repository.

The slice must not claim live-org capture, deployment/build/test evidence, complete impact analysis,
trusted per-side path replay or release authority. Evidence remains `INCOMPLETE`, authority remains
`ANALYSIS_ONLY`, release eligibility remains false and the global release interlock remains
mandatory.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — ACCEPT after request-boundary and
  event-loop remediation.
- Governance/evidence/safety: `r01_governance_review` — ACCEPT after bounded-body and health
  truthfulness remediation.

## Findings resolved

- The endpoint rejects every nonempty body, all query parameters and normalized custom
  scope-bearing headers before service invocation. Nonzero `Content-Length` is rejected without a
  read; absent/zero length streams only until the first nonempty chunk and never buffers the body.
- Blocking Git/tree capture and replay executes in a worker thread rather than the ASGI event loop.
- A successful capture is immediately passed through `verify_current`; expected abstentions return
  their safe typed projection and verification failures never leak the first capture.
- Full verified-change, graph and operation-seed artifacts remain request-local. The projection is
  capped at 32 KiB at the API boundary and contains no paths, bytes, aliases, URLs or exception text.
- The loaded source project ID and configured Git/app roots are the only host inputs. Candidate DX
  root mismatch fails before operation-seed execution and suppresses partial runtime artifacts.
- No GET/list/cache was added and no run or outcome repository receives foundation evidence.
- Health reports construction state as `foundation_capture_configured`, not runtime availability,
  and exposes only sanitized `NOT_CONFIGURED`/`CONFIGURATION_INVALID` categories. Generic 503s are
  conservatively non-retryable.

## Retained foundation gaps

- A separately runtime-decoded dashboard panel for this projection.
- Live Salesforce source/deployment/retrieval receipts and side-specific trusted path replay.
- Complete semantic-family, build, test-execution, obligation, conflict, approval and release
  evidence.

## Verification

- Focused backend gate: 54 foundation-pipeline, service and API tests passed.
- Ruff formatting/lint and `git diff --check` passed.
- Full repository gate: 717 Python tests, 2 Playwright locator-healing tests, 7 dashboard E2E
  tests, TypeScript lint, governance/genericity checks and the Next.js production build passed.
