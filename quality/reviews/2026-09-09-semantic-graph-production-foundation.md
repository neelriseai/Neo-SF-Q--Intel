# R0.4c verified semantic graph-production review

Date: 2026-09-09

Capability: `source.tree-graph-production`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may prove complete exact-byte file accounting plus deterministic Salesforce semantic
extraction for the explicitly supported, `sfdx-project.json`-scoped source families. It may derive
typed node/edge changes and deletion tombstones from materialized base and candidate graph sides.
It must not claim complete Salesforce-family coverage, upstream live-org capture, complete impact
seeds, a trusted build, exact-build test execution or release authority. Every artifact remains
`ANALYSIS_ONLY`, `release_eligible=false`, and retains the global interlock.

The product Change Evidence Graph is distinct from the generated developer navigation artifacts in
`knowledge/`. Those indexes cannot supply runtime facts, permissions or release evidence.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — APPROVED after remediation.
- Governance/evidence/safety: `r01_governance_review` — APPROVED after remediation.

The review rejected the first file-only topology design. Remediation added a generic Salesforce DX
semantic profile and pinned adapter, descriptor-derived package roots, exact contract/normalizer
pins, fail-closed unsupported families, bounded no-follow candidate reads, conservative inferred
relations, false-grant suppression, raw-value digestion, independent delta/tombstone replay and
separate semantic-versus-byte provenance digests.

## Verified behavior

- The caller cannot submit an adapter, graph, file subset, ignore list, semantic seed or tombstone.
- The complete base and candidate manifests are replayed. Immutable base blobs and bounded current
  candidate handles are matched to the verified sizes and hashes under a final repository replay.
- The exact ontology, generic source profile, producer, adapter, adapter contract and normalizer are
  content-pinned and verified again at runtime.
- Salesforce package roots are derived independently on each side. Lookalike files outside package
  roots are nonsemantic; unrecognized files inside package source roots block the artifact.
- Supported metadata, Apex, trigger, LWC, flow, permission, approval, report, tab and configuration
  families emit typed product nodes and edges. Reference-app replay produced 165 nodes and 366 edges
  from 117 parsed files with no application-specific literals in runtime policy.
- Lexical or structurally ambiguous relations remain `INFERRED`. False permission declarations do
  not become authorization edges. Configuration literal values are represented only by digest and
  byte length.
- Exact byte ownership remains in receipts while semantic hashes omit owner-byte changes. Formatting
  edits therefore update file evidence without fabricating a semantic entity modification.
- Node/edge add, modify and delete partitions and all tombstones are independently recomputed from
  both materialized graph sides. Rehashed nested tampering fails closed.

## Retained foundation gaps

- `SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE` and `UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED`.
- `CHANGE_SEED_SCOPE_NOT_ATTESTED`, `RISK_FACTORS_NOT_ATTESTED` and conflict/approval scope gaps.
- `CANDIDATE_BUILD_NOT_VERIFIED`, `DEPLOYMENT_NOT_ATTESTED`, test-obligation and test-execution gaps.
- Repository-origin and Git-signature gaps.
- The global `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock.

## Nonblocking P2 hardening

- Replace full XML materialization with a streaming pre-parse limit for lower peak memory.
- Add reviewed adapters and ontology mappings for additional Salesforce source families.
- Make R0.4d consume both product graph sides with operation-aware ADD/MODIFY/DELETE seed semantics.

## Verification

- Focused graph-production and repository-knowledge suite: 23 passed.
- Independent reference-project adapter replay: 216 files, 117 parsed, 99 nonsemantic,
  0 unsupported, 165 nodes, 366 edges; reversed input produced an equal graph.
- Ruff, generated-knowledge check and `git diff --check`: passed.
