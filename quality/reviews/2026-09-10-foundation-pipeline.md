# R0.4 host-owned foundation-pipeline review

Date: 2026-09-10

Capabilities: `source.verified-change-set`, `source.tree-graph-production`,
`source.change-seed-mapping`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may claim a host-owned, zero-scope-argument composition of the current R0.4a verified
local-Git capture, R0.4c semantic graph production and R0.4d operation-aware seed mapping. The
composer must replay current host policy and source state, preserve exact cross-stage lineage,
sanitize failures, stop downstream execution after failure, suppress partial runtime artifacts and
return only a bounded non-authoritative projection. `EXECUTED` describes local stage execution; it
does not mean evidence completeness or release readiness.

The slice must not claim upstream live-org capture, trusted per-side path replay, complete
Salesforce-family coverage, verified build/deployment/test evidence, durable workflow integration or
release authority. Evidence remains `INCOMPLETE`, authority remains `ANALYSIS_ONLY`, release
eligibility remains false and `RELEASE_EVIDENCE_MODEL_INCOMPLETE` remains mandatory.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — ACCEPT after maintenance and P2
  remediation.
- Governance/evidence/safety: `r01_governance_review` — ACCEPT after lineage remediation.

## Findings resolved

- The stage-3 `verified-change` input is now value-bound to the exact stage-1 output, as well as the
  stage-3 graph input being bound to stage 2. A fully rehashed forged lineage is rejected.
- The pipeline policy and implementation are independently pinned along with each upstream policy;
  runtime policy rotation and source rotation fail closed.
- `verify_current` replays retained R0.4a/R0.4c/R0.4d artifacts, binds exact historical output and
  time receipts, then returns only a fresh matching host capture.
- Public evidence is bounded and excludes repository paths, source bytes and unsanitized exception
  content. Every stage failure has a stable code, later stages become `NOT_RUN` and full artifacts
  are not promoted.
- `capture_current()` accepts no caller paths, changed-file subset, graph, adapter or seed. Renaming
  the host directory, business entity and package preserves control flow and measurements.
- The composition-root parameter is named `implementation_root` so it cannot be confused with the
  configured Salesforce application root.
- All three capability claims remain `FOUNDATION`; execution state cannot remove permanent gaps or
  imply a `GO` result.

## Retained foundation gaps

- Current-verified pipeline exposure through the application service, HTTP/API and dashboard.
- Trusted BASE/CANDIDATE R0.2/R0.1 path replay from the side-qualified seeds.
- Upstream live Salesforce capture receipts, complete semantic-family adapters, verified build and
  exact-build execution, obligations, conflicts and approvals.
- Durable workflow/checkpoint persistence and release-authorizing evidence remain outside this
  slice.

## Verification

- Focused gate: 41 pipeline, operation-seed and configuration tests passed before final reviewer
  remediation.
- Reviewer regression: exact forged-lineage validation passed.
- Genericity follow-up: business-entity and package rename metamorphic test passed.
- Ruff formatting/lint and `git diff --check` passed.
- Full repository gate: 693 Python tests, 2 Playwright locator-healing tests, 7 dashboard E2E
  tests, TypeScript lint, governance/genericity checks and the Next.js production build passed.
