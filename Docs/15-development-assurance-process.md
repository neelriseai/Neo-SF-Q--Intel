# Development assurance process

## Purpose

Keep the platform generic, evidence-grounded and architecturally complete while development
moves quickly. Automated checks handle mechanical regressions; two independent read-only reviewer
agents handle genericity and governance precision in parallel.

## Per-slice loop

1. Route the task through `knowledge/project-index.json` and
   `knowledge/application-graph.json`; select the affected capability IDs, files, imports and tests
   before opening broader docs. Do not redefine scope in code.
2. Implement through domain, service, adapter and verification layers as applicable.
3. Continue the next independent task while a reviewer agent inspects the coherent diff.
4. Reconcile every P0/P1 finding at the next natural boundary and record its disposition in the
   structured scope-review manifest.
5. Rebuild repository knowledge and run the fast genericity gate.
6. Run the full gate before a milestone commit and record unresolved findings honestly.

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
approved only when `quality/reviews/current-scope-review.json` names every changed controlled path,
contains two distinct reviewers and has status `APPROVED`. The pre-push hook and `-Full` command add
lint and automated tests.
