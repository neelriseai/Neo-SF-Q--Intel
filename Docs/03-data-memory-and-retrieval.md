# Data, memory and retrieval

## PostgreSQL ownership

The initial PostgreSQL schema stores run documents, LangGraph checkpoints, knowledge chunks,
embedding arrays, graph edges and tool audit. Test/healing outcomes, evaluations and approvals
remain inside the versioned run document until dedicated query tables are needed. PostgreSQL
does not store unrestricted chat transcripts or credentials.

## Ontology-guided hybrid retrieval

1. Resolve exact contract/entity identifiers.
2. Traverse confirmed graph relationships.
3. Retrieve lexical candidates through PostgreSQL full-text search.
4. Rank the bounded candidate set with embedding cosine similarity in Python.
5. Build an evidence pack with token budget, provenance and freshness.
6. Validate every agent-returned evidence ID against the pack.

No `pgvector` extension is required for the initial corpus. Embeddings are stored as numeric arrays behind a repository interface so a later vector extension or service is replaceable.

## Graph rules

- Confirmed source/runtime edges and inferred semantic edges use separate states.
- The LLM may propose `SEMANTICALLY_RELATED`, `SUPPORTS` or `CONTRADICTS` edges.
- The LLM cannot create confirmed `READS`, `WRITES`, `CALLS`, `GRANTS_ACCESS_TO` or `TESTS` edges.
- Snapshot mismatch invalidates earlier decisions.
- The supplied Salesforce graph is ingested from the configured sibling repository and is never silently replaced by an LLM-extracted graph.
