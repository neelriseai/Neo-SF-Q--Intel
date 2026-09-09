# R0.4a verified local change-set review

Date: 2026-09-09

Capability: `source.verified-change-set`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may prove a complete, bounded local-Git base/candidate input-tree capture and derived
byte/mode delta under a pinned policy and producer implementation. It must not claim a verified
build, Salesforce deployment, graph change-seed mapping, test-obligation completeness or exact-build
test execution. Every artifact remains `ANALYSIS_ONLY`, `release_eligible=false`, and retains the
global `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock plus every unresolved producer gap.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — APPROVED after remediation.
- Governance/evidence/safety: `r01_governance_review` — APPROVED after remediation.

The review cycle rejected staging-dependent semantic roots, incomplete root-level secret patterns,
caller-supplied snapshot/environment labels, injectable successful runners, incomplete permanent
gaps, post-allocation capacity checks, stale working-tree modes, status-only delta derivation,
serialized status partitions that could be rehashed, and state checks occurring before the final
candidate-byte read. All P0/P1 findings were corrected before approval.

## Verified behavior

- The local-Git producer derives the base from current `HEAD` and its tree and enumerates the full
  tracked plus nonignored-untracked candidate tree. Callers cannot supply changed paths.
- Base and candidate manifests bind canonical repository-relative paths, regular-file modes, sizes
  and SHA-256 hashes of actual bytes. ADD/MODIFY/DELETE is independently recomputed from the full
  manifest union; rename inference is deliberately represented as delete plus add.
- Staging state remains audit metadata and cannot change the semantic candidate-tree or build-input
  root. Clean/smudge line-ending differences are captured from actual working bytes.
- Project plus actual base history derives the privacy-safe repository identity. The source snapshot
  derives from base commit/tree and candidate-tree roots. Repository directory/origin authority
  remains explicitly unattested.
- Policy and producer implementation are self-hashed, externally pinned and replayed from current
  files. Time is sampled internally and successful historical artifacts require current recapture.
- Secret/auth locators are refused before any file/blob read. Unsafe, Unicode-ambiguous or Windows
  alias paths, sparse/special/index states, over-capacity data, malformed Git output, time failure,
  capture races and a commit during the second byte pass fail closed with typed release-only gaps.
- No raw source bytes, secret values or absolute host paths are serialized.

## Retained foundation gaps

- `CANDIDATE_BUILD_NOT_VERIFIED` and `DEPLOYMENT_NOT_ATTESTED`.
- `CHANGE_SEED_SCOPE_NOT_ATTESTED` and `UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED`.
- `RISK_FACTORS_NOT_ATTESTED`.
- `TEST_OBLIGATION_SCOPE_NOT_ATTESTED` and `TEST_EXECUTION_SCOPE_NOT_ATTESTED`.
- `CONFLICT_SCOPE_NOT_ATTESTED` and `HUMAN_APPROVAL_SCOPE_NOT_ATTESTED`.
- `REPOSITORY_ORIGIN_NOT_ATTESTED` and `GIT_COMMIT_SIGNATURE_NOT_ATTESTED`.
- The global `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock and R0.4b–R0.6 work.

The next R0.4 slice must map every verified changed artifact to graph seeds or an explicit blocking
unmapped-change gap, retain captured candidate bytes as the sole build input, and bind a trusted
build's configuration, toolchain, environment and output bytes before any build gap can be removed.
