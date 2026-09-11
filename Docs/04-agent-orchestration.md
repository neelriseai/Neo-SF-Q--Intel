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
| Change Analyst | Deterministic lexical/path seed matching over a pinned, normalized analysis graph; the host-owned candidate path now invokes configured provider-backed advisory specialists and persists private replay captures | Durable restart replay, hard cancellation, retry-safe checkpoint/CAS integration and quality-corpus evidence |
| Test Intelligence | Projects selections already made by deterministic analysis into a typed stage result; it does not independently select tests today | Independent evidence-bound obligation planning, trusted execution results and provider explanations |
| UI Healing | Converts eligible impacts into approval-gated metadata/accessibility strategy proposals | Current-DOM capture, candidate ranking, apply and before/after browser evidence |
| Governance Review | Revalidates evidence, exact metrics, guardrails and deterministic decision truth | Durable tool/model audit and human approval workflow |

These nodes remain governed advisory stages, not release-authorizing model agents. The current
activity event is useful diagnostic structure but does not yet contain the complete replayable
measurement and exact-policy receipt required by `AGENTS.md`; durable outbox capture is also
pending. Events must never contain raw prompts, credentials, session URLs or chain-of-thought.

The verified candidate path now wires provider-backed specialist ports into the public service when
LLM dispatch is enabled. Each configured stage consumes replay-verified context packs, returns a
schema-validated advisory artifact or deterministic degradation, and stores private replay capture
material; the public view exposes hashes/counts only. The capability remains `FOUNDATION` pending
durable restart replay, hard cancellation, retry-safe checkpoint/CAS integration, service outbox
guarantees and adjudicated quality evidence. Tool use and state transitions continue to pass
through the orchestrator.

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

This remains `FOUNDATION`, not durable workflow completion. The production composition now has a
real OpenAI/Azure structured-output adapter, capture-returning specialist execution and
candidate-path service wiring with private replay captures. It still needs independent durable
restart replay, hard cancellation, host-owned consumption-time receipts, idempotent
CAS/checkpoint resume, an event outbox and quality-corpus evidence. Until those exist the dashboard
must not imply that configured profiles are accepted live agents or that advisory results affected
the deterministic assurance run.

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

Both providers implement the same JSON reasoning and embedding interfaces. Provider-specific
deployment names, endpoints and keys stay in environment configuration. Azure OpenAI specialist
dispatch fails closed before client construction when the configured API version is a placeholder,
malformed or older than the structured-output boundary.
