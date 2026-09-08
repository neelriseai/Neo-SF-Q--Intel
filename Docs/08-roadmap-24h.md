# Twenty-four-hour implementation roadmap

## Runtime authority

Build and final verification target the org machine: Python 3.14.3, Node.js 26.5.x, Salesforce CLI 2.148.3 and local PostgreSQL. The development machine may use newer compatible patch releases, but the compatibility gate is the org machine.

## Planned 24-hour schedule and current truth

| Hours | Planned outcome | Current status |
|---:|---|---|
| 0–2 | Repository, canonical docs, contracts, provider profiles and environment preflight | Foundation implemented; org-machine provider verification pending |
| 2–6 | Auto-created PostgreSQL run/checkpoint memory, SQLite/JSON/cache fallback, hybrid retrieval ports and snapshot checks | Run/checkpoint and fallback foundation implemented; governed metadata/audit ports and semantic fusion pending |
| 6–10 | LangGraph workflow and four schema-bounded specialist agents | Deterministic typed stages implemented; independent provider-backed agents pending |
| 10–13 | Salesforce services and five MCP adapters | Three foundation tools exist; governed live reads, policy/audit envelope and remaining adapters pending |
| 13–16 | Playwright worker and generic locator-ranking/healing path | Locator ranking library implemented; browser capture/apply worker pending |
| 16–20 | Next.js dashboard: command center, run timeline, impact/tests, healing and governance | Polished command center and governance view implemented; complete timeline/test/healing/degradation views pending |
| 20–24 | Unit/contract/golden tests, live integration correction and rehearsal | Unit, contract and mocked dashboard E2E exist; real vertical E2E and three golden scenarios pending |

## Gates

- Python imports and provider configuration pass without exposing keys.
- PostgreSQL checkpoint save/resume passes.
- Missing PostgreSQL falls back to an auto-created SQLite schema; missing SQLite falls back to a
  declared non-durable process cache without blocking JSON/graph analysis.
- Salesforce contract and graph load through relative/configured paths.
- Salesforce CLI read-only smoke passes on the org machine.
- Next.js production build passes under Node 26.5.
- Playwright launches the selected local browser and returns a structured snapshot.
- Three golden scenarios produce evidence-backed results.
- Governance scorecard uses measured results, never constants presented as execution.

## Contingencies

- If LangGraph dependency installation fails under Python 3.14, stop and resolve that dependency; do not install an unapproved Python runtime or silently replace durable orchestration.
- Keep the current bounded in-process cosine index for the 24-hour demo. When persistent vector
  retrieval is added, implement only the ChromaDB adapter and preserve deterministic exact/graph
  and PostgreSQL full-text fallback. Do not add a PostgreSQL vector extension.
- If Playwright cannot download Chromium, use an installed Edge channel.
- If the runtime LLM endpoint is unavailable, demonstrate deterministic degradation and label semantic agents unavailable rather than simulating them.

## Current implementation checkpoint

The repository now contains a typed LangGraph workflow with four deterministic specialist stages,
deterministic governance, source graph traversal, OpenAI/Azure provider adapters, a standalone
extension-free semantic index, PostgreSQL run/checkpoint wiring and relational foundation schema,
three MCP tools, a bounded Salesforce CLI adapter, locator ranking and the first dashboard.
Semantic results are not yet fused into assurance decisions. Live PostgreSQL, model-provider,
Salesforce evidence, trusted test execution and browser-worker verification remain environment or
implementation gates and must not be represented as completed until they are exercised end to end.

## Post-demo vector milestone

Add ChromaDB behind `SemanticEvidenceIndex` only after the governed ingestion and retrieval contract
is complete. Acceptance requires snapshot-isolated collections, idempotent upsert/delete, embedding
version migration, project isolation, provenance returned with every hit, corrupted-index rebuild,
and contract parity with the in-process adapter. ChromaDB remains derived/rebuildable; PostgreSQL
retains authoritative run, evidence, chunk metadata/content, graph, audit and checkpoint records,
with SQLite, defined JSON and process cache providing progressively reduced fallback modes.

Before presenting PostgreSQL as complete agent memory, add application ports and tests for governed
chunk/metadata ingestion, graph edges, tool audit, test/healing outcomes, evaluations and approvals.
