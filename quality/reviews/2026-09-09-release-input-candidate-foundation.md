# R0.3 immutable release-input candidate-composition review

Date: 2026-09-09

Capability: `governance.release-input-integrity`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may prove canonical composition, current replay and tamper detection for a complete
candidate input surface. It must not claim that candidate obligation, execution, conflict or human
populations are producer-complete, that a declared change/build is attested, or that the manifest
can authorize release. The global `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock remains mandatory.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — APPROVED, no remaining P0/P1 findings.
- Governance/evidence/safety: `r01_governance_review` — APPROVED, no remaining P0/P1 findings.

The review cycle rejected early digest-only producer bindings, fabricated policy pins, incomplete
runtime-policy identities, unreachable expiry validation, unsafe path validation, risk results that
could be rehashed without replay, fake path substitution, and swaps of valid path identities between
different risk contributions. Each was corrected before approval.

## Verified behavior

- The compiler derives the request, evidence, impact, claim, analysis-gap and selected-test
  partitions from one complete v2 `AssuranceRun`; empty or duplicate required identities fail closed.
- Requirement and source-reference content are bound by digest and size without copying narrative or
  secret-shaped content into the manifest. Changed paths and artifact locators must be canonical,
  safe, repository-relative paths.
- Exact candidate change/build bytes, graph/ontology/profile roots and the complete historical R0.2
  artifact are bound. R0.2 is replayed against current graph, propagation, R0.1 and path policies.
- Analysis risk is recomputed under the current pinned policy. Historical and refreshed path hashes
  map through a stable identity containing seed, target and full ordered hop provenance, so fake
  hashes and valid-path score/component swaps fail closed.
- Active reasoning, evaluation-set and governance identities are loaded and compared with the pinned
  policy manifest; placeholder or caller-invented producer roots are not accepted.
- Candidate downstream receipts bind full canonical payload digests, exact candidate/build/path
  scope and validity windows, but remain advisory because their producer roles are explicitly
  missing and permanent `RELEASE_ONLY` gaps cannot be removed.
- Current UTC is sampled internally. Historical candidates refresh only after full current replay
  and static semantic equality; future, expired, cross-root, omitted, duplicated, oversized,
  malformed and rehashed inputs fail closed.
- The strongest governance test proves that a syntactically complete candidate manifest still
  produces `INCOMPLETE / RELEASE_EVIDENCE_MODEL_INCOMPLETE`.

## Retained foundation gaps

- `VERIFIED_CHANGE_SET_MISSING` and `CANDIDATE_BUILD_NOT_VERIFIED`.
- `UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED` and `CHANGE_SEED_SCOPE_NOT_ATTESTED`.
- `RISK_FACTORS_NOT_ATTESTED`.
- `TEST_OBLIGATION_SCOPE_NOT_ATTESTED` and `TEST_EXECUTION_SCOPE_NOT_ATTESTED`.
- `CONFLICT_SCOPE_NOT_ATTESTED` and `HUMAN_APPROVAL_SCOPE_NOT_ATTESTED`.
- Durable independent anchoring plus R0.4–R0.6 producer/revalidation work.

Every artifact remains `ANALYSIS_ONLY`, `release_eligible=false`, with the global release interlock.
Full R0.3 authority is not claimed until the later producer scopes are non-caller-narrowable.
