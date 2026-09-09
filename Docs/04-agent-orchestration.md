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

## Isolated advisory consumer foundation

`reasoning_workflow.py` now provides the first A6 consumption boundary without changing the four
authoritative LangGraph nodes above. It accepts only configured, bound specialist stages and a
module-pinned source-neutral workflow policy. Before any live port call it replay-validates the A3
context, binds the complete change request plus graph/ontology/profile/policy identities, requires
at least one graph seed and passes those roots in the immutable invocation input. A returned bundle
must preserve that exact preflight context. Captured bundles are replayed through A3, A4 and A5
again at consumption.

The consumer returns a separate immutable `ReasoningWorkflowResult`. Its proposals are permanently
`ANALYSIS_ONLY`, `CANDIDATE`, `INFERRED`, non-authorizing and ineligible as release evidence. It
hashes the complete `AssuranceRun` before and after every specialist interaction, replays the
current governance assessment and effective decision, and cannot write to evidence, impacts,
selected or executed tests, healing proposals, claims, analysis gaps, governance or the release
decision. Provider failures are isolated; exact duplicate advisory facts merge their evidence and
provenance, while distinct multi-target facts remain distinct. A4/A5 explicit conflict sets still
force abstention, and A6 does not invent conflict semantics from model prose.

This is deliberately an isolated `FOUNDATION`, not durable workflow completion. The production
composition still needs a real OpenAI/Azure structured-output adapter with hard cancellation, an
independent durable invocation/capture registry, host-owned consumption-time receipts, idempotent
CAS/checkpoint resume, an event outbox and service/API persistence. Until those exist the dashboard
must not imply that configured profiles are running agents or that advisory results affected the
deterministic assurance run.

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
