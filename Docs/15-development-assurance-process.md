# Development assurance process

## Purpose

Keep the platform generic, evidence-grounded and architecturally complete while development
moves quickly. Automated checks handle mechanical regressions; one read-only reviewer agent
handles design judgment in parallel.

## Per-slice loop

1. Select capability IDs from `config/capability-scope.json`; do not redefine scope in code.
2. Implement through domain, service, adapter and verification layers as applicable.
3. Continue the next independent task while a reviewer agent inspects the coherent diff.
4. Reconcile P0/P1 findings at the next natural boundary, not after every file write.
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

The hook is intentionally fast: knowledge freshness, forbidden hardcoding, layer boundaries and
scope-manifest integrity. The full command adds lint and automated tests.
