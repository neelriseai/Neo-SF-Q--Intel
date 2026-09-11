# Whole-solution readiness and repair review

Review date: 2026-09-10  
Scope: current uncommitted Neo worktree  
Review lanes: `astra_solution_audit`, `r01_genericity_review`, `r01_governance_review`

## Verdict

Approved as a truthful foundation integration slice after the fixes below. This review does not
promote a capability, authorize a live Salesforce action, or provide release evidence. The 24-hour
demo vertical remains incomplete and release authority remains disabled.

## Resolved findings

| Priority | Finding | Resolution |
|---|---|---|
| P0 | MCP Salesforce inspection could invoke an arbitrary configured alias without independent non-production proof | Tool remains registered only as a typed `UNAVAILABLE`/`NOT_RUN` projection and never resolves an alias or invokes the CLI |
| P1 | Ontology, source-profile and dependent policy identities were inconsistent | All dependent pins/defaults were migrated as one fixed-point change; all ten default policy loaders compose |
| P1 | Developer graph lost capability-to-test edges and omitted canonical reasoning/TypeScript alias relationships | Capability verification edges restored and equality-tested; requirements, canonical read order, deduplication and alias imports are indexed |
| P1 | Browser foundation decoder accepted pipeline policy 1.0.0 while the backend emits 1.0.1 | Browser contract and mock now require 1.0.1; stale 1.0.0 is a negative case |
| P1 | Requirements and test acceptance were narrative/file-level only | A machine-readable seven-group requirement registry now maps every capability exactly once and records missing exact nodes/receipts as explicit gaps |
| P1 | Review-control policy could omit new runtime files or weaken itself | Boundary-safe controlled roots, HEAD/current union and hard-coded quality/hook bootstrap protection were added with tests |
| P1 | Machine-path scanning detected only user-home paths | Canonical/runtime scanning now covers generic drive, UNC and bounded POSIX machine paths with explicit parser-module exemptions and regression tests |
| P1 | Documentation overstated live evidence, agents, healing and completion | Canonical documents now distinguish foundations, proposals, mocked browser tests, manual observations and product-owned live receipts |

## Current verification observations

- Astra repair: 66 focused tests passed; 44 governance/workflow tests passed; all ten default
  runtime policy loaders passed; Ruff passed on its touched Python files.
- Frozen-tree Python run: 756 tests passed with zero failures.
- Before that final run: 94 focused integration tests passed, followed by 30 repository
  knowledge/requirement/path/scope tests.
- Next.js TypeScript check and production build passed.
- Playwright locator suite passed 2/2; the final mocked dashboard browser-contract suite passed
  18/18 after the browser policy-pin correction. Next.js TypeScript and production build passed.
- No Salesforce deployment, metadata write, Apex execution, browser action, live provider call or
  live PostgreSQL mutation was performed by this review.

## Accepted planned gaps

These are compatible with current `FOUNDATION`/`NEXT` labels and remain P1 roadmap work:

- exact test-node and versioned execution-receipt schema/validator plus durable reports;
- one connected source-change → graph/context → real specialist → obligations → live evidence →
  persistence → dashboard vertical;
- non-production Salesforce classification and bounded live adapter receipts;
- real provider, PostgreSQL, trusted test-runner and browser-worker execution;
- locator authorization/apply/readback and complete negative/failure coverage;
- durable audit/outbox and exact measurement/policy identities in every activity.

Any future architecture/design review, material refinement or whole-solution debug uses Astra when
available. Implementation remains with the primary agent or Astra unless the operator explicitly
authorizes another reviewed delegation policy.
