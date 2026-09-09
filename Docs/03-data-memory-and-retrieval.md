# Data, memory and retrieval

## Memory ownership and fallback

PostgreSQL is the target primary durable memory for all non-vector agent state: run documents,
LangGraph checkpoints, evidence and knowledge-chunk content/metadata, graph edges, tool audit,
test/healing outcomes, evaluations and approvals. It does not store unrestricted chat transcripts,
credentials or vector indexes.

The current implemented checkpoint wires complete run documents, LangGraph checkpoints and the A7
outcome-memory foundation. Outcome memory has append-only PostgreSQL, SQLite, immutable-JSON and
process-cache adapters behind one project-scoped port. Every append replays the complete originating
run, module-pinned governance/outcome/evaluation contracts and any exact persisted incident chain
before a storage or idempotency effect. Service reads replay again and fail closed on missing,
corrupt, cross-project or inconsistent history. PostgreSQL and SQLite prevent row mutation;
PostgreSQL also blocks table truncation. The remaining chunk, graph, audit, approval and evaluation
ports stay `FOUNDATION`; DDL alone is never reported as completed runtime behavior.

If PostgreSQL is missing or unreachable, run storage automatically uses the configured SQLite
database. Outcome storage independently falls back through auto-created SQLite, immutable JSON and
a declared process cache. Health separates liveness from persistence readiness and reports
`run_persistence`, `outcome_persistence`, `outcome_durable`, sanitized degradation codes and gap
codes. An unknown/custom adapter is conservatively non-durable. Fallback occurs only during
classified backend unavailability; schema defects, corruption, policy/replay failures, idempotency
conflicts and ambiguous writes fail closed without switching stores. The versioned contract,
project index and evidence graph remain available from defined JSON artifacts.

This is an A7 `FOUNDATION`, not completed historical intelligence. PostgreSQL behavior still needs a
live transaction/concurrency verification on the target machine. JSON publication is atomic per
receipt and record but not a multi-file transaction; an identical retry repairs a receipt left by a
crash, while a conflicting retry remains refused. HTTP/MCP exposure, signed or independently
anchored tamper evidence, historical-policy replay, remaining outcome kinds and an adjudicated
quality corpus remain planned.

## Vector-store decision

ChromaDB is the only supported vector database for later semantic retrieval on the org machine.
Its persistent directory and collection names must be environment-configured, project-scoped and
excluded from Git. Stored records must carry source snapshot, chunk hash, embedding provider/model,
ingestion version and evidence ID. PostgreSQL remains the system of record for runs, provenance,
governance and audit; ChromaDB is a rebuildable retrieval index, never independent evidence authority.
If ChromaDB is unavailable, persistent semantic history is unavailable; the current bounded
ephemeral index may rank the active source snapshot, while exact, graph and lexical reasoning remain.

## Ontology-guided hybrid retrieval

1. Resolve exact contract/entity identifiers.
2. Traverse confirmed graph relationships.
3. Retrieve lexical candidates through PostgreSQL full-text search when available, otherwise from
   the bounded JSON/source corpus.
4. Retrieve semantic candidates from ChromaDB when enabled; otherwise use the current bounded
   in-process cosine index for the small approved corpus.
5. Build an evidence pack with token budget, provenance and freshness.
6. Fuse typed exact, lexical, confirmed-graph and semantic candidate receipts using the pinned
   weighted reciprocal-rank policy. Exact and confirmed-graph bundles are protected from semantic
   displacement, while every fused item remains a non-authoritative candidate.
7. Validate every agent-returned evidence ID against the pack.

This sequence is the retrieval half of graph-grounded agent reasoning. It is not represented as a
generic GraphRAG product or an LLM-generated graph. The isolated deterministic context-compiler
foundation owns canonical structure selection, authority separation and atomic budgets. The
isolated fusion foundation owns bounded request- and channel-rooted candidate ranking, conflict
preservation, explicit degradation and trusted replay. It propagates upstream context
incompleteness and cannot satisfy a release gate. Governed source resolution, real channel
adapters, trusted A3 verification receipts, semantic-index/outcome receipts and workflow delivery
remain pending;
once integrated, an output verifier will accept only typed proposals whose endpoints and evidence
IDs are present in the hash-valid context pack.

No PostgreSQL vector extension is permitted or required. The `SemanticEvidenceIndex` port must keep
the current in-process implementation and the future ChromaDB adapter interchangeable.

## Graph rules

- Confirmed source/runtime edges and inferred semantic edges use separate states.
- The LLM may propose `SEMANTICALLY_RELATED`, `SUPPORTS` or `CONTRADICTS` edges.
- The LLM cannot create confirmed `READS`, `WRITES`, `CALLS`, `GRANTS_ACCESS_TO` or `TESTS` edges.
- Snapshot mismatch invalidates earlier decisions.
- The supplied Salesforce graph is ingested from the configured sibling repository and is never silently replaced by an LLM-extracted graph.
- Canonical node/relation classes and legal endpoint signatures are implemented in the source-
  independent ontology. The Salesforce source profile exhaustively maps its current vocabulary;
  changing a source vocabulary requires a new profile rather than editing core policy. Both
  contracts are self-hashed and pinned into the run identity.
- Missing record-level state, source artifact, extractor, snapshot or hash remains `UNVERIFIED`.
  A whole-graph digest does not silently promote incomplete records, and raw
  `HUMAN_CONFIRMED` text cannot promote itself.
- The R0.1 trusted-edge foundation preserves a separate release-only readiness channel. Missing or
  invalid source-artifact hash, extractor version or extractor-implementation digest blocks release
  evidence but does not remove a legacy edge from analysis. A current consumer must replay raw
  source-graph bytes, normalized graph, ontology/profile, parser registry, policy, edge artifact and
  validity window; a stored verification digest alone is never authority.
- Accepted local envelopes bind legal direction and endpoint signature, canonical relation,
  project/snapshot, raw and normalized graph roots, repository-relative artifact locator and digest,
  pinned parser implementation and freshness. They remain `ANALYSIS_ONLY` because upstream source
  capture is not yet attested; PostgreSQL persistence or a signature would not repair that missing
  fact by itself.
- An unmapped relation touching a material endpoint or an illegal signature is blocking. A truly
  supporting-to-supporting omission remains visible but nonblocking.
- Graph expansion follows a reviewed propagation matrix keyed by canonical relation, changed
  endpoint and direction. The current bidirectional allowlist traversal is only a foundation and
  must not be described as causal reasoning. Every accepted impact path carries an ordered path
  receipt; cycles and diamonds are deduplicated without reversing causality.
- Release/outcome memory is append-only governed evidence. Historical similarity may rank a
  candidate or flag a prior incident, but cannot prove that the current change is safe.
