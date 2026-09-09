# Nested Salesforce DX discovery review

Date: 2026-09-10

Capability: `source.tree-graph-production`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

The pinned Salesforce adapter may support one DX project descriptor at any repository depth. It
must discover the descriptor independently from each complete verified BASE/CANDIDATE tree, resolve
declared package roots relative to the descriptor, and preserve whole-repository accounting. It
must not accept a caller-selected project, infer one from the working directory, or claim automatic
multi-project monorepo selection.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — ACCEPT after remediation.
- Governance/evidence/safety: `r01_governance_review` — ACCEPT after remediation.

## Findings resolved

- Root-only descriptor lookup was replaced with deterministic exactly-one discovery on each side.
- Every adapter input path, declared package path and combined descriptor-relative root is checked
  for canonical repository-relative form and the pinned byte limit.
- Exact-plus-case-alias descriptors, unsafe Windows-portable names, traversal, duplicate/casefold
  roots and bidirectional overlaps fail closed.
- Nested package lookalikes remain explicitly `NOT_APPLICABLE`; they are not omitted from the
  complete file disposition receipt.
- BASE and CANDIDATE locate independently. Descriptor relocation preserves semantic identities
  while owner provenance and side receipts change.
- Runtime code contains no repository name, DX subdirectory, package name, org alias, object name
  or absolute path.

## Retained foundation gaps

- Exactly one descriptor is supported; multi-project selection requires a future independently
  pinned partition/selection policy.
- Source-family, upstream org, build, deployment, test and release evidence gaps remain unchanged.
- The output remains `ANALYSIS_ONLY`, `release_eligible=false`.

## Verification

- Focused change, graph-production and operation-seed gate: 78 tests passed.
- The real reference repository adapter replay accounted for 221 tracked files, parsed 117 files,
  and produced 165 semantic nodes plus 366 edges without a layout literal in runtime code.
- Full repository gate: 667 Python tests, 2 Playwright locator tests, 7 dashboard E2E tests,
  TypeScript checks and the Next.js production build passed.
