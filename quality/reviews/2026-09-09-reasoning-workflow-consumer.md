# A6 reasoning-workflow consumer review

## Scope and claims

- Capability IDs: `reasoning.graph-grounded-agent`, `orchestration.multi-agent`,
  `reasoning.graph-impact`, `quality.test-selection`, `automation.locator-healing`.
- Status: `FOUNDATION`.
- Implemented claim: a separate immutable consumer can merge configured, replay-verified A3/A4/A5
  specialist artifacts without changing an authoritative `AssuranceRun` or its effective
  `INCOMPLETE` decision.
- Explicitly unclaimed: durable live-provider invocation, hard network cancellation, independent
  capture and consumption receipts, CAS/checkpoint resume, event outbox, service/LangGraph/API
  persistence, provider quality and release authority.

## Independent review record

Two read-only lanes reviewed the design before implementation and challenged the completed diff.

### Genericity, architecture and scope lane

The first completed-diff review blocked integration for request/context substitution, caller-paired
workflow policy, configurable seed bypass, cosmetic unbound stages, incomplete identifier limits,
invented cross-specialist conflict semantics, missing renamed-topology coverage and stale capability
documentation. The implementation was revised to:

- bind the canonical complete change request and every graph/ontology/profile/reasoning root;
- replay the live preflight context before a call and require the returned context to be identical;
- load one module-pinned, duplicate-key-safe, source-neutral workflow policy;
- make graph seeds mandatory and reject an unbound configured stage;
- merge only exact logical duplicates while preserving distinct multi-target facts;
- enforce bounds over every emitted proposal identifier; and
- cover a genuinely renamed topology with the same behavior and decision class.

### Governance, evidence and safety lane

The first completed-diff review blocked integration for cross-request replay and false
multi-specialist conflicts. It also required whole-run mutation detection, A4 expiry checks, current
governance replay, full provider-capture references and blocking-gap degradation. The implementation
now hashes the complete run before and after every specialist interaction, replays the module-pinned
governance policy and effective decision, checks capture and fusion receipt freshness, retains the
full capture digest, isolates failures and keeps every proposal non-authorizing and ineligible as
release evidence.

## Verification

- Focused A6 tests: 22 passed.
- Combined A3-A6 tests: 117 passed.
- Ruff: passed for the A6 source and tests.
- Scenarios cover positive, negative, degradation, tamper, outage, malicious authority narrative,
  complete-request mutation, preflight/root substitution, expiry, duplicate identities, seedless
  input, renamed topology, bounds and secret-safe failure output.

## Accepted FOUNDATION gaps

| Priority | Owner | Gap | Acceptance boundary |
|---|---|---|---|
| P1 | Orchestrator | Request-specific provider capture roots arrive in the trusted replay bundle; there is no independent durable capture registry | Persist invocation intent and full capture root before consumption; verify by request/run/stage idempotency key and reject paired substitution |
| P1 | Orchestrator | Live calls are not retry-safe across process failure or checkpoint resume | Add CAS state version, atomic capture/result/checkpoint/outbox commit and crash/race tests proving one call and one terminal event |
| P1 | Orchestrator | Consumption time is an explicit replay input, not a host-owned persisted receipt | Persist `consumed_at` and effective `valid_until`, cap it by source/channel validity and replay the receipt |
| P1 | Provider adapters | OpenAI/Azure structured-output adapters and hard network cancellation are not implemented | Add provider contract suites for success, refusal, malformed output, timeout and cancellation without logging prompts or raw responses |
| P1 | Quality | Specialist and integrated advisory quality are not established | Adjudicate at least 20 representative cases under a versioned evaluation contract before making quality claims |
| P2 | Observability | Advisory activities are embedded in the result rather than emitted through a durable deduplicated outbox | Emit one bounded event per logical attempt with run/trace/root identity and secret canary tests |

These gaps do not permit the isolated consumer to be presented as completed multi-agent workflow
integration. They remain scheduled work and the release-authority interlock stays fail-closed.

After the blocker remediations, both independent lanes returned `APPROVE` with no P0 or P1 finding
that contradicts the isolated `FOUNDATION` claim. Generated knowledge and the full repository gate
remain mandatory before integration.
