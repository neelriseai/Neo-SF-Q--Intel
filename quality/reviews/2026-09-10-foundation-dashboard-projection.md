# Local candidate foundation dashboard review

Date: 2026-09-10

Capability: `ui.assurance-dashboard`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

The panel requests a fresh host-owned local Git candidate projection and displays only a strictly
runtime-decoded, bounded view of its three foundation stages. It is separately owned from
`AssuranceRun`; it cannot persist evidence, affect the run decision or grant release authority.
Stage execution is capture-time local-foundation status, not evidence of a live Salesforce org,
deployment, build, tests, approval, complete impact or release readiness.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — ACCEPT, no P0/P1 findings.
- Governance/evidence/safety: `r01_governance_review` — ACCEPT, no P0/P1 findings.
- Browser/lifecycle edge cases: `r01_edge_builder` — ACCEPT, no P0/P1 findings.

Review challenges caused the implementation to add exact stage-to-capability and permanent-gap
contracts, exact upstream transition rules, a 32 KiB streaming response limit, canonical timestamp
and digest replay, expiry removal, a validated host-configured timeout and accessible ready-state
announcements. The generic timeout defaults to five minutes and accepts a validated 30-second to
15-minute host range instead of assuming a demo-scale repository.

## Verified behavior

- The request is a bodyless POST with no query or scope-bearing custom header.
- Unknown keys, authority changes, missing permanent gaps, impossible transitions, invalid receipt
  lineage, timestamps, digests, duplicate fields and oversized responses fail closed.
- Canonical stage and chain digests are independently replayed in the browser as contract integrity,
  never described as authority or proof.
- Executed, failed and not-run stages remain distinct. Valid abstention is distinct from malformed,
  unavailable, network-failed and timed-out capture.
- Refresh, error, timeout, supersession and expiry cannot leave an older projection visible.
- Missing measurements say not reported; an explicit zero remains zero.
- Every reported receipt role, measurement and blocking gap is reachable without rendering raw
  responses, server errors, paths or secrets.
- Natural keyboard navigation, Space activation, ready-state announcements and a 390 px viewport are
  covered. A production-layout screenshot was inspected after a successful local compile.
- Independently produced Python EXECUTED and ABSTAINED projections decoded successfully through the
  TypeScript contract, confirming cross-runtime canonicalization parity.

Focused verification passed TypeScript checks and all 17 dashboard Playwright cases. One earlier
trace-retention run encountered a Windows output-artifact cleanup collision after assertions; an
isolated full run and a trace-disabled independent run passed, so it was not treated as product
evidence failure.

## Retained foundation gaps

- The legacy assurance-run response still needs equivalent runtime decoding.
- Automated axe/contrast auditing and dedicated zero-sample presentation fixtures remain planned.
- Local candidate evidence remains `ANALYSIS_ONLY`, `INCOMPLETE`, non-authoritative and release
  ineligible until the roadmap's live-source, per-side path, build, obligation, exact-test, conflict
  and approval evidence is implemented and independently verified.
