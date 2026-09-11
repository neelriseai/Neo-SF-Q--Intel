# Development assurance process

## Purpose

Keep the platform generic, evidence-grounded and architecturally complete while development
moves quickly. Automated checks handle mechanical regressions; two independent read-only reviewer
agents handle genericity and governance precision in parallel.

## Per-slice loop

1. Route the task through `knowledge/project-index.json` and
   `knowledge/application-graph.json`; select the affected capability IDs, files, imports and tests
   before opening broader docs. Assign stable requirement IDs and acceptance classes. Do not
   redefine scope in code.
2. Implement through domain, service, adapter and verification layers as applicable.
3. Continue the next independent task while a reviewer agent inspects the coherent diff.
4. Reconcile every P0/P1 finding at the next natural boundary and record its disposition in the
   structured scope-review manifest.
5. Update exact requirement-to-test node mappings and rebuild repository knowledge.
6. Run the fast genericity gate.
7. Run the full gate before a milestone commit and retain a sanitized, source-bound execution
   receipt. Record unresolved findings honestly.

## Verification ledger rules

- Test files are inventory only. Acceptance maps to exact test node IDs, level (`UC`, `F`, `B`,
  `L`, or `G`), environment, expected result and most recent source-bound execution receipt.
- `FOUNDATION` and `IMPLEMENTED` capabilities require positive, negative, failure, degradation and
  scenario-independence cases where applicable. A category may be `NOT_APPLICABLE` only with a
  reviewed rationale.
- A passing mocked browser test cannot satisfy a live API/browser requirement. A historical result
  cannot satisfy the current worktree. Configuration presence cannot satisfy a provider, database
  or Salesforce runtime requirement.
- Ontology, profile and policy identities migrate atomically across all consumers. Focused load and
  replay tests must pass before another development slice begins.
- Every live mutation requires a non-production classification receipt, current source/org
  binding, check-only result, pre-state, tested restore and post-restore verification. The versioned
  mutation-authorization receipt must bind current-task authority; fixed host-owned alias and
  non-secret org fingerprint/class; exact source/build/manifest and operation set; check-only
  receipt; pre-state digest; bounded expiry; tested metadata-and-data restore artifacts/receipts;
  and post-restore reconciliation including residue/deletes. Unknown/production org, mismatched or
  dirty source, partial validation, stale restore proof, or caller alias/scope override yields
  `POLICY_BLOCKED`/`NOT_RUN` before any subprocess or browser action.

## Verification receipt target

The versioned receipt schema and validator must reject duplicates, partial promotion, stale data
and any project/source/build/policy mismatch. A receipt contains:

- project plus repository/source snapshot and exact build identity;
- requirement ID, capability ID, exact test node ID and acceptance level;
- runner, adapter and CLI identities;
- non-secret environment identity and, for live Salesforce, org fingerprint/classification;
- policy IDs, versions and hashes;
- start, end and expiry timestamps;
- per-test outcome and process exit status;
- bounded artifact digest, byte size and index;
- typed gaps and one terminal status: `PASSED`, `FAILED`, `BLOCKED`, `NOT_RUN`, `TIMED_OUT` or
  `CANCELLED`.

Acceptance levels are: `UC` (unit/contract), `F` (in-process functional), `B` (mocked browser
contract), `L` (live dependency), and `G` (independently adjudicated golden scenario). Until this
schema and validator are implemented, this remains an acceptance target and no new capability is
promoted from an ad hoc report.

## Reviewer questions

- Would renamed objects, fields, users and business values follow the same control flow?
- Does any core module know the current demo application, alias, route or record identifier?
- Does each agent own real typed behavior, or is it only a label over a pass-through function?
- Can every material claim be proven from the cited edge/evidence state?
- Does vague, conflicting, stale or missing evidence cause abstention?
- Are implementation-status claims supported by positive, negative and failure-path tests?
- Did the change remove or silently narrow an agreed capability?
- Is persistence/retrieval real, or is schema/documentation being mistaken for behavior?
- Does each metric define its exact population, formula, comparator, threshold, sample rule and
  zero-denominator behavior?
- Does a guardrail protect a real trust/action boundary without duplicating another control or
  blocking benign context?

## Commands

```powershell
python scripts/catalog/build_project_index.py
.\scripts\quality\check.ps1
.\scripts\quality\check.ps1 -Full
```

Enable the repository hook once per checkout:

```powershell
git config core.hooksPath .githooks
```

The pre-commit hook is intentionally fast: knowledge freshness, forbidden hardcoding, layer
boundaries, exact structured review coverage and governance-policy validation. A policy change is
recorded as reviewed only when `quality/reviews/current-scope-review.json` names every changed
controlled path, contains two distinct reviewer IDs and has status `APPROVED`. That manifest is an
auditable development record, not authenticated reviewer identity, live-action authority or release
approval. Merge approval remains external; signed or host-produced reviewer receipts are required
if machine-enforced independence is introduced. The pre-push hook and `-Full` command add lint and
automated tests.
