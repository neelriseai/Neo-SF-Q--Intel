# Agent orchestration

## Orchestrator

LangGraph coordinates a typed workflow and, when PostgreSQL is reachable, persists checkpoints
there. Routing and release policy remain deterministic. If PostgreSQL is absent, complete run
documents persist in auto-created SQLite while the graph runs without durable checkpoints; if
SQLite also fails, the process cache is explicitly labeled non-durable.

```text
START
  -> change_analyst
  -> test_intelligence
  -> ui_healing
  -> governance_review
  -> END
```

Preflight, live evidence collection, and selected-test execution are application-service gates
around this initial graph and remain scheduled work for the 24-hour implementation window.

## Specialist responsibilities

| Agent | May do | Must not do |
|---|---|---|
| Change Analyst | Normalize requirements; propose semantic impacts | Confirm source facts or authorize tools |
| Test Intelligence | Explain coverage; propose non-mandatory tests | Remove mandatory obligations |
| UI Healing | Rank evidence-backed locator candidates | Guess on ambiguity or bypass UI permissions |
| Governance Review | Identify unsupported claims and missing controls | Alter evidence or select release outcome |

Agents do not converse freely. Each receives a bounded context pack and returns one schema-validated result. Independent analysis may run in parallel, but tool use and state transitions pass through the orchestrator.

## Provider profiles

- Local development: `AI_PROVIDER=openai`.
- Org runtime: `AI_PROVIDER=azure_openai`.

Both providers implement the same JSON reasoning and embedding interfaces. Provider-specific deployment names, endpoints and keys stay in environment configuration.
