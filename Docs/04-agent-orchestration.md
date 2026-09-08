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

## Current specialist-stage responsibilities

| Stage | Current behavior | Target boundary not yet claimed |
|---|---|---|
| Change Analyst | Deterministic lexical/path seed matching plus trusted graph traversal; abstains on unsupported semantics | Provider-backed normalization and semantic proposals fused into assurance evidence |
| Test Intelligence | Deterministically separates graph-connected mandatory and recommended selections | Trusted execution planning/results and provider explanations |
| UI Healing | Converts eligible impacts into approval-gated metadata/accessibility strategy proposals | Current-DOM capture, candidate ranking, apply and before/after browser evidence |
| Governance Review | Revalidates evidence, exact metrics, guardrails and deterministic decision truth | Durable tool/model audit and human approval workflow |

These nodes are deterministic specialist stages, not yet independent model-backed agents. Each consumes typed state and emits a bounded activity event plus a typed deliverable containing conclusions, evidence IDs, gaps, measurements and the next permitted action. Events contain identifiers and measurements, never raw prompts, credentials, session URLs or chain-of-thought.

The multi-agent roadmap capability becomes complete only when provider-backed specialist ports consume bounded context packs, return schema-validated proposals, degrade deterministically when a provider is unavailable, and have independent negative/failure tests. Tool use and state transitions will continue to pass through the orchestrator.

## Graph-grounded specialist contract

Every provider-backed specialist will receive a deterministic `GraphContextPack` containing the
project and source snapshot, request/input digest, canonical ontology version, seed entities,
confirmed paths with evidence receipts, relevant source fragments, selected prior outcomes,
blocking/nonblocking gaps and explicit item/token limits. It returns a `GraphReasoningProposal`
containing only proposed relations/conclusions, cited pack evidence IDs, assumptions and unresolved
conflicts. The verifier rejects any endpoint or citation outside the pack.

This pattern applies to impact, security, test intelligence, healing and RCA. The agent helps
interpret ambiguity; deterministic graph traversal, authorization, evidence promotion, test truth
and release decisions remain ordinary application logic.

## Provider profiles

- Local development: `AI_PROVIDER=openai`.
- Org runtime: `AI_PROVIDER=azure_openai`.

Both providers implement the same JSON reasoning and embedding interfaces. Provider-specific deployment names, endpoints and keys stay in environment configuration.
