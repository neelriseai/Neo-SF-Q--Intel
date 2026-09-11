# Eight-hour live-integration sprint

> **Resumed under a narrower timebox, not a narrower capability.** The authoritative current
> execution boundary is [the frozen three-hour agentic live vertical](22-frozen-three-hour-agentic-live-vertical.md).
> The [development stop checkpoint](21-development-stop-checkpoint-2026-09-11.md) remains historical
> handoff evidence. Neither document grants Salesforce action authority by itself.

## Objective

Complete one reusable, production-behavior vertical in which Neo evaluates the configured local
Salesforce DX candidate against a classified live non-production Salesforce org. The vertical must
connect verified local change evidence, graph-grounded impact and test obligations, exact live
targets, product-owned live reads, runnable Playwright automation, PostgreSQL persistence and
truthful governance.

This sprint does not redefine full production readiness. A fixture, diagnostic observation or
console result cannot satisfy a live gate, and the candidate deployment campaign remains conditional
on independent recovery readiness and exact current-task authority.

## Non-negotiable boundaries

- Local Git is sufficient. No remote, GitHub, branch-service or network Git dependency is allowed.
- Runtime callers cannot supply an org alias, repository path, REST route, metadata manifest, test
  name, browser selector, record identifier or action scope.
- The source contract and model may propose targets but cannot authorize execution.
- Salesforce session material and raw org results never enter prompts, logs, persisted artifacts or
  API/MCP responses.
- Live evidence remains phase-, org-, actor-, source-, policy- and campaign-bound.
- The model may explain or propose; deterministic producers and validators own facts, permissions,
  test obligations, terminal states and release eligibility.
- The current candidate's independent test expectations must not be rewritten or omitted to make the
  candidate pass. A correctly detected failing or blocked change is a successful assurance outcome.

## Parallel lanes

1. **Candidate reasoning** (`reasoning.graph-impact`, `quality.test-selection`): bridge the current
   verified graph into trusted source-hash and freshness envelopes; derive impacts and complete
   test obligations from both code and supported metadata/configuration changes.
2. **Live evidence** (`runtime.salesforce-live-evidence`, `runtime.salesforce-target-planning`,
   `quality.live-test-execution`): compile typed source operations, intersect them with host policy,
   and execute only bounded, exact, read-only API/metadata/browser operations plus separately
   authorized selected tests.
3. **Receipt and browser recovery** (`governance.live-receipt-assurance`,
   `automation.locator-healing`, `automation.applied-healing`): persist replayable assertion
   artifacts before signing receipts and support one explicitly approved, reversible,
   non-destructive browser recovery with before/after/restoration evidence.
4. **Integration and review**: connect FastAPI, MCP/service, PostgreSQL and Next.js; run the live
   journey, fault tests and independent Astra review; update the requirement registry and generated
   project knowledge without overstating acceptance.

## Hour-four mandatory operator checkpoint

By hour four, the Playwright TypeScript automation framework and runnable test suite are mandatory,
not a later stretch item. The checkpoint requires:

- a product-owned fixed-alias live-org launch path with exact org, actor and origin checks;
- an isolated nonpersistent context with one-shot in-memory session handoff;
- bounded sanitized DOM capture, metadata/accessibility candidate discovery and ambiguity
  abstention;
- at least one source-derived live application assertion and one metadata-aware locator-recovery
  scenario;
- positive, negative, timeout, secret-canary, cleanup and readback-mismatch tests;
- structured sanitized logging and machine-readable Playwright reporting; and
- an explicit label separating fixture/browser-contract tests, diagnostic live execution and an
  accepted live receipt.

The offline hour-four contract gate is runnable as `npm run test:hour4` from `packages/browser`.
It writes the Playwright JSON report to `packages/browser/test-results/browser-contract-results.json`
in addition to the console list reporter. This generated local report is test evidence only and is
not a signed live receipt. The production `live:smoke` command emits one bounded JSON projection and
remains diagnostic-only until it consumes the verified source-derived browser target and the exact
durable enrollment, target-plan and dataset receipt roots.

At that checkpoint the product may request only these operator-owned inputs:

- confirmation of the intended synthetic Salesforce page/record and observable expected behavior;
- current-task authority for the complete, source-derived selected Apex method set, if it is safe to
  run on the classified org; and
- approval or rejection of one exact non-destructive browser-recovery proposal from the policy
  allowlist.

Neo must show the local candidate identity, derived impacts and obligations, exact live target plan,
classified org/actor binding, completed read-only receipts and every remaining blocked or not-run
gate. The operator is never asked to paste a session URL, token, selector, route, manifest or broad
suite name.

## Hour-eight definition of done

The bounded integrated demo is complete only when all applicable statements are true:

1. A real local code or supported metadata/configuration change produces a replay-validated graph,
   non-empty justified impacts and complete test obligations, or a precise typed abstention.
2. The production target compiler derives every API, metadata, test, browser and synthetic-data
   obligation from current verified evidence and reports denied, unsupported or unmapped targets.
3. A product-owned bounded executor performs the permitted live baseline operations through the
   shared service boundary; the legacy broad CLI adapter is not exposed as authority.
4. Sanitized typed execution artifacts are durably stored and independently replayed before any
   signed receipt can report `PASSED`.
5. One real Next.js-to-FastAPI-to-service-to-Salesforce journey is demonstrated without fixture
   substitution, with exact live and offline evidence labels.
6. One approved locator-recovery action is uniquely scoped, applied, read back, restored and
   persisted, or it truthfully abstains/blocks. An unchanged attribute is not a healed outcome.
7. PostgreSQL save/restart/replay is exercised; degradation cannot claim campaign recovery.
8. Positive, negative, failure, timeout, cleanup, tamper, scenario-independence and secret-canary
   tests pass for every new execution boundary.
9. Astra reports no unresolved P0 or scope-contradicting P1, generated knowledge is current, and the
   structured review ledger covers the final controlled diff.

## Conditional candidate deployment

`SF-C01` through `SF-C06` are not forced into an eight-hour passing demonstration. They may run only
after a current metadata-and-data restore rehearsal, durable dispatch journal, preissued exact
recovery permit, successful complete check-only validation and post-dispatch recovery path are
independently proven. If the current candidate conflicts with mandatory tests, Neo must stop at
check-only or selected-test failure and retain that result. It must not remove an obligation, weaken
an oracle or infer deployment authority.

## Product-owned live-read executor checkpoint

The read-only Salesforce boundary is implemented in `src/neo_sf_q_intel/live_read_evidence.py`.
It consumes one complete `LiveTargetPlan` through a host-owned input port; API and MCP callers do
not supply an alias, route, metadata type/member or record value. Before contacting Salesforce it
replays the exact current enrollment, CLI authentication, target-plan, dataset-scope and expected-
execution-contract receipts, requires their canonical bytes to already exist in the same durable
ledger, verifies target-plan and dataset-variable digests, and validates every planned target. The
exact expected contract additionally binds the plan/request/candidate/scope roots, execution ID,
runner key identity and pinned runner/adapter/Salesforce CLI versions before any CLI command runs.

The executor currently supports the baseline partitions `STANDARD_REST`, `CUSTOM_REST` and
`METADATA`. REST operations are fixed `GET` requests with exact route templates, response schemas
and byte caps. Metadata retrieval is an exact type/member request into a fresh child of the ignored
`.runtime/` directory; wildcards, secret-bearing families, links/reparse points and file/byte/count
overflow are rejected, and cleanup is verified on every outcome. The Salesforce child process has
fixed arguments, a minimal secret-denying environment, bounded stdout/stderr and time, and
sanitized error codes. Its observed Salesforce CLI version must equal the host pin before org
access. Windows `.cmd` launch uses a resolved absolute CLI path without `shell=True`.

Each successful partition is converted into a typed `ExecutionAssertionArtifact` containing exact
sanitized result bytes. Byte size/hash, artifact index and assertion-to-result bindings are
recomputed, the runner authenticates the artifact with its configured key, and replay compares the
observed target/predicate/cardinality/projection/member/dataset bindings with the independent
expected contract. Only then may the producer sign SF-L03, SF-L04 or SF-L05. Callers cannot assert
`PASSED` or supply receipt digests. The returned projection contains only digests, counts, durations
and receipt IDs and always reports `releaseEligible=false` for the overall campaign; campaign
acceptance remains the independent validator's decision.

The production service, zero-input `POST /api/v1/live-salesforce/baseline` API route and bounded MCP
tool now issue the host-owned enrollment, CLI, target-plan, dataset-scope and expected-contract
receipts and instantiate this executor. Real read-only campaigns against the enrolled non-production
org have completed the six-step identity classification and source-derived dataset resolution. The
latest governed campaign is correctly `BLOCKED`: all seven custom REST invocations return HTTP 200
with deployed schema `1.0.0`, while the current source contract requires `1.1.0` and an explicit
parent identity on every history item. No SF-L03/SF-L04/SF-L05 passing receipt or partial observation
was accepted. Resolving this is a candidate check-only/deploy/restore operation requiring separate
current-task mutation authority; the read-only baseline must not silently weaken the source oracle.
Selected Apex tests, browser acceptance and every candidate/deployment gate remain separate later
partitions.

All authoritative instants are timezone-aware and normalized to UTC before chronology, expiry or
digest validation. Naive timestamps and RFC 3339's unknown `-00:00` offset are rejected. PostgreSQL
connections set their session timezone to UTC independently of the server default; Git author-local
display time is not authority; Salesforce timestamps retain explicit offsets at ingress. Existing
canonical UTC signed documents retain byte-for-byte compatibility, so timezone portability does not
weaken signatures, freshness windows, ordering or replay.

## Feasibility verdict

- **Completed prerequisite:** isolated real OpenAI specialist invocation through the strict
  provider-neutral contract, including typed verification and sanitized capture roots. This remains
  advisory-only and does not satisfy live Salesforce gates.
- **Feasible in eight hours:** a live baseline assurance vertical, real candidate impact/test
  reasoning, product-owned scoped live reads, selected live tests when independently authorized,
  PostgreSQL-backed evidence, one reversible browser recovery, a real dashboard journey and final
  independent review.
- **Mandatory by hour four:** the runnable Playwright TypeScript automation suite and live-org
  diagnostic/assertion path described above.
- **Conditional:** the complete candidate deployment/restore campaign; it depends on recovery
  rehearsal and the candidate passing independently derived check-only obligations.
- **Not an eight-hour claim:** broad production readiness across all Salesforce metadata families,
  multi-tenant isolation, distributed leases, universal locator repair, complete GraphRAG/Chroma
  evaluation or unrestricted autonomous mutation.
