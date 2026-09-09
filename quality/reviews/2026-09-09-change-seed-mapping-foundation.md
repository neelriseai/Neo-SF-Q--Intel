# R0.4b local change-to-graph mapping review

Date: 2026-09-09

Capability: `source.change-seed-mapping`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may prove complete local structural alignment between every supported non-delete entry
in a currently replayed `VerifiedChangeSet`, unique policy-defined graph artifact anchors, declared
entities and the affected stable path identities from current R0.2/R0.1 replay. It must not call the
result change-seed attestation, verified impact completeness, a trusted build or release evidence.
The Git candidate tree and graph snapshot still lack a shared graph-producer receipt, and deletes
lack a base-graph/tombstone receipt. Every result remains `ANALYSIS_ONLY`,
`release_eligible=false` and retains the global interlock and all unresolved producer gaps.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — APPROVED after remediation.
- Governance/evidence/safety: `r01_governance_review` — APPROVED after remediation.

The review cycle required exact producer-type and repository-byte replay, current R0.2/R0.1 replay,
externally pinned policy/implementation roots, exact changed-artifact partitioning, aggregate
capacity enforcement, final-clock chronology, safe historical receipt refresh and protection of
historical audit roots that are excluded from stable refresh comparison. All P0/P1 findings were
corrected before approval.

## Verified behavior

- Callers cannot submit changed paths, mappings, seed IDs, graph-path subsets, ignore rules or a
  successful change producer. The current concrete local-Git producer replays repository bytes.
- The mapping policy pins the verified-change, ontology, source-profile, propagation, complete-path,
  trusted-edge and extractor-registry contracts plus the mapper implementation.
- Each supported change maps by exact canonical repository locator to one unique normalized
  `source-artifact` anchor. Declared entities are derived through the canonical `declared-in`
  relation; no label, suffix, basename or business literal participates.
- Artifact anchors are the propagation seeds, and every current R0.2 path for those seeds is
  projected. Stable ordered-topology identities support later-time refresh without trusting stale
  path receipts.
- Historical Git manifest, R0.2 artifact and raw path receipt roots must match the supplied
  historical inputs before a current semantic refresh. Fully rehashed audit-root mutations fail.
- Unsafe, case/Unicode-alias, duplicate, ambiguous, unmapped, zero-path, stale, cross-project,
  over-capacity or tampered inputs fail closed. Deletes explicitly require base graph/tombstones.
- No source bytes, secrets, host paths, Salesforce aliases, object names or demo routes are emitted.

## Retained foundation gaps

- `GRAPH_INPUT_TREE_NOT_ATTESTED` and `CHANGE_SEED_SCOPE_NOT_ATTESTED`.
- `CANDIDATE_BUILD_NOT_VERIFIED` and `DEPLOYMENT_NOT_ATTESTED`.
- `RISK_FACTORS_NOT_ATTESTED`.
- `TEST_OBLIGATION_SCOPE_NOT_ATTESTED` and `TEST_EXECUTION_SCOPE_NOT_ATTESTED`.
- `CONFLICT_SCOPE_NOT_ATTESTED` and `HUMAN_APPROVAL_SCOPE_NOT_ATTESTED`.
- `UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED`, `REPOSITORY_ORIGIN_NOT_ATTESTED` and
  `GIT_COMMIT_SIGNATURE_NOT_ATTESTED`.
- The global `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock.

## Nonblocking P2 hardening

- Add a genuinely rebuilt graph permutation fixture and broader multi-file/multi-entity
  diamond/cycle scenarios.
- Add direct boundary regressions for locator bytes, graph nodes and aggregate seed/path capacities.
- Add a graph-producer receipt, candidate/base graph linkage and deleted-artifact tombstones before
  promoting this mapping foundation to attestation.
