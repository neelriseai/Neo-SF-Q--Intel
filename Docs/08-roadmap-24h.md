# Twenty-four-hour implementation roadmap

## Runtime authority

Build and final verification target the org machine: Python 3.14.3, Node.js 26.5.x, Salesforce CLI 2.148.3 and local PostgreSQL. The development machine may use newer compatible patch releases, but the compatibility gate is the org machine.

## Schedule

| Hours | Outcome |
|---:|---|
| 0–2 | Repository, canonical docs, contracts, provider profiles and environment preflight |
| 2–6 | Auto-created PostgreSQL run/checkpoint memory, SQLite/JSON/cache fallback, hybrid retrieval ports and snapshot checks |
| 6–10 | LangGraph workflow and four schema-bounded specialist agents |
| 10–13 | Salesforce services and five MCP adapters |
| 13–16 | Playwright worker and generic locator-ranking/healing path |
| 16–20 | Next.js dashboard: command center, run timeline, impact/tests, healing and governance |
| 20–24 | Unit/contract/golden tests, live integration correction and rehearsal |

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

The repository now contains the typed LangGraph workflow, four specialist nodes, deterministic
governance, source graph traversal, OpenAI/Azure provider adapters, an extension-free embedding
similarity index, PostgreSQL run/checkpoint wiring and relational foundation schema, three MCP tools, a Salesforce read adapter,
metadata-aware Playwright healing and the first dashboard. Live PostgreSQL, Azure/OpenAI, and
Salesforce org verification remain environment gates and must not be represented as completed
until configured and run on the org machine.

## Post-demo vector milestone

Add ChromaDB behind `SemanticEvidenceIndex` only after the governed ingestion and retrieval contract
is complete. Acceptance requires snapshot-isolated collections, idempotent upsert/delete, embedding
version migration, project isolation, provenance returned with every hit, corrupted-index rebuild,
and contract parity with the in-process adapter. ChromaDB remains derived/rebuildable; PostgreSQL
retains authoritative run, evidence, chunk metadata/content, graph, audit and checkpoint records,
with SQLite, defined JSON and process cache providing progressively reduced fallback modes.

Before presenting PostgreSQL as complete agent memory, add application ports and tests for governed
chunk/metadata ingestion, graph edges, tool audit, test/healing outcomes, evaluations and approvals.
