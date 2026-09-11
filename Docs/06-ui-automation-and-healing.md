# UI automation and healing

> **Development stopped 2026-09-11.** See
> [the current checkpoint](21-development-stop-checkpoint-2026-09-11.md) before using this design.
> The browser probe has an unresolved closed-schema P1, the host factory is interrupted, and no live
> healing mutation or acceptance receipt exists.

## Worker boundary

The TypeScript Playwright package now separates five offline-tested boundaries:

1. a small metadata-aware locator-candidate selector;
2. a typed worker that owns a nonpersistent browser context, one-shot session handoff, bounded
   DOM capture or non-mutating candidate readback, and cleanup reporting;
3. a fixed-alias Salesforce CLI session broker that checks the enrolled org and actor before it
   converts a short-lived frontdoor URL into an opaque worker handle; and
4. a digest-pinned coordinator plus diagnostic-only live-smoke command; and
5. an offline reversible-recovery executor that accepts only an independently signed, expiring
permit bound to the complete campaign/source/candidate/org/actor roots and to one exact,
   previously discovered worker candidate. A durable atomic one-use claim prevents permit replay.

The fifth boundary proves the action contract against isolated pages: it re-resolves one visible,
enabled candidate, checks the exact precondition, requires a changed exact after-state, restores the
pre-state, closes the ephemeral session and signs only digest-safe evidence. Unchanged readback,
ambiguity, stale/cross-request authority, restore failure and cleanup failure cannot report a heal.
The executor snapshots the entire validated request before its first asynchronous boundary and
revalidates permit expiry plus origin/org/actor/scope binding immediately before the action, so
caller mutation and time-of-check/time-of-use races fail closed.
It is not wired into the production coordinator and has not acted on Salesforce. The live-smoke
command still produces a sanitized diagnostic projection, not a signed campaign receipt. Python
owns product intent, governance and persisted gate evidence. The host-side source-to-healing
translation now exists; authenticated candidate execution binding and concrete stateful
Python-to-browser dispatch remain to be built.

## Target healing algorithm

1. Capture current visible candidates inside the scoped page/record context.
2. Match exact Salesforce metadata and stable semantic attributes.
3. Score label, role, control type, section context, visibility and enabled state.
4. Require uniqueness and negative-state checks.
5. Attempt a non-destructive probe where possible.
6. Accept only above policy threshold; otherwise abstain.
7. Persist only governed digest-safe evidence; keep raw candidates and the temporary locator map
   private to the bounded ephemeral worker session.

The locator library accepts generic `LocatorIntent` and contains no page-specific business branch.
Its fixed values are strategy priorities, not measured confidence. It returns the first visible
unique candidate and abstains on ambiguity. The worker independently uses typed accessibility
selectors for bounded discovery and can compare a permitted attribute with an expected value. The
separate recovery executor can apply and undo one permitted reversible toggle only after verifying
independent authority and that exact current candidate receipt. Source component/record target-plan
binding is compiled; production coordinator wiring, Salesforce execution and durable accepted
healing evidence remain absent.

For an explicit object/field intent, metadata discovery now resolves either co-located identity
attributes or a field descendant of the matching object. Playwright CSS locators cross open
component shadow roots; a bounded composed-ancestor walk independently checks the nearest object
and field identities. The library returns one visible editable native or semantic control within
the field boundary, not the wrapper. Repeated scopes, multiple controls, another nested object or
field, excessive ancestry, disabled/read-only controls and absent controls abstain. Explicit
metadata mismatches cannot fall through to an unscoped matching label. This is candidate discovery
only; it does not perform a form edit, save, layout change or production healing operation.

The lifecycle names are distinct and must not be collapsed:

1. `CANDIDATE_DISCOVERED` — a bounded locator candidate was found.
2. `PROPOSAL_APPROVED` — policy/operator approval permits a non-destructive attempt.
3. `ACTION_APPLIED` — the browser worker attempted the action.
4. `OUTCOME_VERIFIED` — current-page readback and evidence prove the intended outcome.

Only the fourth state may be presented as healed.

## Target framework constraints

- Playwright TypeScript.
- Chromium or installed Edge only.
- One Salesforce session URL is consumed through an opaque one-shot in-memory handoff and is never
  intentionally persisted or logged.
- Assisted Salesforce metadata/DOM healing.
- No universal cross-framework repair claim.

## Current verification truth

The current offline Playwright inventory adds 7 reversible-recovery contract tests to the locator,
worker, CLI-session and coordinator suites. They cover positive action/restore/signing, unchanged
readback, ambiguity, precondition mismatch, restore and cleanup failure, expiry, tamper,
cross-request binding, non-unique prior evidence and secret/path canaries. These are source-level
tests, not durable acceptance receipts. Dashboard tests still mock the API and are not Salesforce
or frontend-to-backend evidence.

`packages/browser/src/healing-probe.ts` adds the complete-set, headless read-only browser probe used
by the live bridge. It proves every source locator at baseline, proves each old hook is absent after
the declared drift, discovers exactly one metadata/action-identity candidate, and reruns the
declared visible/enabled/editable assertions. Its four focused tests cover the successful baseline
and drift paths plus ordering, ambiguity, disabled-state and selector-injection refusals. It never
clicks, fills, submits, saves or persists raw locator/DOM material, and its report explicitly earns
no acceptance credit until a trusted live dispatcher signs and stores the observation.

On 2026-09-10 the production diagnostic command completed against the real `caip-dev` Developer
Edition org after the earlier enrollment-window and Windows CLI-wrapper defects were fixed. Its
sanitized projection reported `status=PASSED`, `diagnosticOnly=true`, `releaseEligible=false`,
`evidencePhase=null`, `contextClosed=true` and `browserClosed=true`. This proves only that the
fixed-alias session, read-only browser path and cleanup completed for that diagnostic invocation.
It produced no signed campaign receipt, performed no browser action and earns zero acceptance
credit. `SF-L06`, `SF-L07`, `SF-L09`, the browser portion of `SF-C05`, live applied healing and the six
legacy AUT browser-healing specifications therefore remain `NOT_RUN` for acceptance. The legacy
labels are historical input, not Neo requirement IDs.

## Source-bound healing sequence and report contract

`src/neo_sf_q_intel/live_healing.py` adds an offline-tested Python orchestration boundary for
`automation.applied-healing`. It is disabled by default and contains no Salesforce call, browser
launch, session material or production authority implementation. A host-only authority port must
independently verify the complete source-derived browser target and assertion set, exact plan and
execution contract, current classification/actor binding, deployment and restoration prerequisites,
then durably claim one-use authority. The request contains source-owned browser intent plus exact
assertion, locator, semantic-identity and expected-value roots; missing bindings are not inferred
from intent prose, record names or a demo scenario.

For every target the sequence requires headless baseline capture with every independent assertion
passing; a fresh failed old locator (not a business-assertion failure); exactly one visible, enabled,
new locator with matching semantic identity per stale obligation; a temporary locator rebind; and a
complete rerun of both affected and unaffected assertions. Optional temporary page-metadata changes
require a separate host enable flag, candidate-phase authority and an exact expected metadata state
root. A changed but unrelated post-state fails. Baseline-phase requests cannot authorize metadata
mutation. Every command is time-bounded and authority-revalidated immediately before dispatch;
authenticated durable observations bind the command, source/campaign scope and producer.

Recovery checks two independent state domains: exact Salesforce pre-state **and** residue/deletion
reconciliation, and exact test-side locator-map reset with zero pending rebinds. Original assertions
must pass again after restoration. A rejected action before dispatch does not trigger a compensating
write; a dispatched action whose result times out does require recovery. Failure of one restoration
branch cannot skip the other. Local browser/context/rebind-store closure has a separate local-only
port and is always attempted after a session dispatch, including after authority revocation or
expiry. It cannot navigate or call Salesforce. Forward authority reserves bounded recovery time;
expired recovery authority never permits an org write and leaves an explicit reconciliation gap.

The per-target report includes complete obligation roots, stale obligation roots, attempted stages,
verified command/receipt/observation digests, UTC timestamps, measured stage duration and separate
restoration/closure results. It contains no raw DOM, accessible names, selectors, session URLs,
record IDs, org aliases or exception payloads. Explicit timezone offsets normalize to UTC; naive
and unknown offsets are rejected. Malformed adapter output is rejected without serializer-warning
payload leakage. A failed target leaves every later target visible as `NOT_RUN`, not silently omitted.

This is a **FOUNDATION**, not a live-healing completion claim. `OUTCOME_VERIFIED` means only that the
injected observations satisfy this sequence contract. The report is explicitly not an acceptance
receipt, has `acceptance_credit=false` and `release_eligible=false`. The current `BrowserTarget`
carries the complete source-bound locator, assertion and restoration contract, and the host bridge
derives that exact set. Metadata recovery is limited to an exact `SET_SCALAR` edit with declared
`ABSENT`/`PRESENT`-baseline prestates inside the existing component instance; arbitrary XPath and
existing-leaf-only assumptions are not part of the contract. Independently reviewed host authority/
dispatch adapters, a durable accepted
receipt producer and actual Salesforce execution remain required. No API/MCP
endpoint enables this orchestrator. Existing read-only diagnostics or fixture tests cannot satisfy
`SF-L09`, candidate UI gates or restoration acceptance.

### Frozen source-to-healing host bridge

`src/neo_sf_q_intel/live_healing_bridge.py` is the non-authorizing translation for
`automation.applied-healing`. `HostSourceHealingBridge.prepare()` takes no caller parameters: its
zero-argument capture callback must be wired to the configured host composition service, not JSON,
paths, source refs, aliases or a caller-selected target subset. It revalidates the frozen compiler,
plan and expectation models and checks their source/tree/graph/profile/policy roots, freshness,
exact full target inventory and declared dataset. It derives every rebind target and obligation.
Each obligation's expected value is the canonical hash of **all** its declared read-only assertions
set to true (visible/enabled, plus editable for fields); no fill, click or save action is added.

The private metadata preview consumes only an exact host-observed package manifest and member
bytes. It validates the isolated member, API version, byte bounds, source/target/preimage roots and
freshness, then reuses `edit_compiled_property` and `metadata_state_sha256` from the recovery adapter.
It never writes the candidate or preimage. The declaration hash uses the shared drift model's
alias-key JSON; the final intent hash uses the recovery adapter's canonical snake-case model JSON.
The preview preserves the full source-planned Apex method inventory, derives exact sorted class
names for `RunSpecifiedTests`, and binds the independent expected method count. `NoTestRun` is used
only when that source inventory is genuinely empty; it cannot silently drop declared tests.
Neither selection is permission to deploy or a test gate result.

An injected host evidence port must independently authenticate the exact preimage, classification,
session, candidate execution contract, scope, expiry and residue roots. Merely supplying matching
hashes is not proof. The existing compiler expectation is `LIVE_BASELINE` only: the bridge refuses
to relabel it and produces no `HealingRequest` unless the host independently verifies a distinct
`DEPLOYED_CANDIDATE` binding. That verifier has no production implementation yet. A prepared request
still requires separate one-use mutation/recovery authority from the orchestration port.

The bridge keeps all targets visible but cannot map multiple independent metadata transitions into
the current request's single prestate/candidate root, so that case explicitly remains `NOT_READY`.
For the supported single-member intent, the AUT state root means exact isolated package/member
semantics, not the whole org; residue reconciliation is a separate authenticated input.
`UnavailableBrowserHealingDispatch` implements the existing `HealingCommand`/`HealingObservation`
port with a typed fail-closed refusal. It never substitutes `runReadOnlySmoke`, a worker diagnostic
capture, or one `CANDIDATE_READBACK` for a retained-session all-obligation healing run, and never
fabricates successful cleanup observations. The missing cross-language session/locator-rebind and
authenticated observation producer remain explicit. Preparation always returns a digest-only
`NOT_READY` report with `FOUNDATION`, no execution authority and no acceptance/release credit.
Raw XML and source locator intent stay in private memory; there is no raw DOM/locator persistence,
API/MCP exposure, session broker invocation, browser launch or Salesforce operation in this bridge.

### Metadata recovery adapter dependency

`src/neo_sf_q_intel/metadata_recovery.py` now supplies a concrete, disabled-by-default metadata-side
transaction service. It reuses the direct, `shell=False`, bounded Salesforce CLI runner, but the
current tests inject a fake transport and no Salesforce operation has been performed by this slice.
The host supplies independently verified non-production/actor authority, a one-use recovery lease
and a separate private journal-authentication key. There is no API/MCP mutation entry point.

Its source contract consumes the compiler's shared `BrowserMetadataDrift`: exactly one existing
FlexiPage member/component instance and one `SET_SCALAR` property. Source-owned component name,
instance identifier, property name, baseline, alternate and sorted `ABSENT`/`PRESENT` allowed
pre-states are mandatory. No caller XPath or file path is accepted. A present value must equal the
declared baseline; an absent property may be inserted only when explicitly allowed, within the
exact existing component instance. No missing component is created. The runtime independently
proves the complete semantic delta is only that scalar, preserves all unrelated original bytes,
and enforces source member byte limits before and after editing and during retrieval/replay.
Arbitrary additions/deletions, Apex code, contained-field packages, ambiguous identities and
non-round-trippable XML fail closed. These are typed adapter limits, not obligation exemptions.

The service retrieves a bounded ZIP without CLI extraction, validates exact file membership and
rejects traversal, links, duplicates, missing/extra members and expansion overflow. It authenticates
the complete preimage semantics against the source-compiled expectation, then durably writes exact
live preimage bytes and the isolated candidate before effects. HMAC-linked, fsynced journal events
bind the intent, lease, package roots and exact original byte hashes. A kernel-owned org-wide lock
excludes simultaneous run/recovery calls even within the same process, while a separate durable
active claim prevents subsequent transactions while recovery remains unresolved. Independently
durable host authority fences record each check/deploy/restore claim and local process quiescence;
they cannot be rolled back by deleting a journal suffix. Missing/unavailable containment proof
blocks every subsequent transport, including cancel/report and restart. On Windows,
native read handles deny writes/deletion of package files and their parent directories throughout
deployment submission. The production runner fails closed on platforms lacking that implemented
immutable-file guarantee; fake transport portability tests are not an equivalent live proof.

The exact isolated package receives check-only validation with complete component/test accounting.
`RunSpecifiedTests` passes explicit authorized class names to Metadata API, separately verifying
the full expected class/method result inventory; class selection alone does not satisfy tests.
The actual configured alias/API/CLI version and runner implementation root are bound into the
lease and passed to authority verification on every invocation. Deadline and transport binding
are rechecked after durable dispatch/process fences, immediately before calling the runner.
A claimed-but-not-sent request remains quarantined without triggering a compensating write.
Following check-only validation,
a second live preimage read rejects concurrent changes before one asynchronous candidate
deployment. Fixed job-ID report/cancel operations establish terminality; candidate retrieval must
match the entire expected component/package semantics. The optional host-only callback is the
candidate assertion window. Its failure cannot suppress the `finally` recovery path. Before
restoration, current member semantics must still equal the exact owned candidate or preimage;
an observed unrelated concurrent property change quarantines instead of being overwritten. An
exclusive live demo change window is required: read-before-write is not a remote compare-and-swap
and cannot prevent a concurrent org edit after the last observation. Restoration
deploys the exact journaled live preimage bytes (including original property absence), never a
Git approximation or guessed reverse edit, and retrieves it again to verify complete scoped file
inventory and all component properties.

A dispatched request with an unknown remote job ID is not blindly retried or blindly compensated:
the service retains quarantine without further transport because readback alone cannot prove
that an unknown queued deployment will not apply later. A known nonterminal job is canceled and
confirmed terminal before restoration. Nonquiescent local processes, expired recovery authority,
failed restoration or residue mismatch also preserve the active org claim. The explicit host-only
restart recovery method authenticates journal and original byte hashes, and never resubmits an
already-dispatched candidate or restoration job. Reports expose only roots, typed states and gaps;
private XML, job IDs, aliases and paths stay in machine-local runtime storage.

This adapter remains `FOUNDATION`: pure source/bridge translation and the host factory below exist;
reviewed private authority issuance, composite browser dispatch/observation production, live
check-only/deploy/restore rehearsal and campaign acceptance integration remain outstanding.
Its zero-residue statement is limited to the exact
authorized metadata member/package, not an assertion about the entire Salesforce organization.

### Host metadata factory activation

`metadata_recovery_host.py` provides a host-only zero-argument `run()`/`recover()` service factory.
`MetadataHostSettings.enabled` defaults to false. There is no API/MCP request model, fake-runner
parameter or automatic `.env` enrollment. The host supplies a current source-intent compiler
callback, trusted issuer registry, durable receipt ledger and separately managed private keys.

The activation file is an independently SHA-pinned, repository-relative `.runtime` JSON document.
It binds the current-task authority, exclusive change window, exact source-compiled property intent,
org/actor/API pins, lease, installed CLI file hashes/version and host implementation digest. Both
the file and current source are rechecked during execution. The configured alias and source member
are data in this reviewed private document, never application literals in Neo runtime code.

Before serialization or activation, exact durable signed receipts must prove host nonproduction
enrollment, machine-local CLI authentication, independent mutation authorization, preissued recovery
permit, live check-only preflight and independent live restoration rehearsal. A product issuer must
attest the precise expected preflight/rehearsal artifact root, complete class/method inventory and
preimage/candidate scope. `metadata_preflight_artifact_sha256` computes an expected binding only;
it neither executes a check nor supplies a passing receipt. Missing evidence, stale scope, a wrong
issuer/signature, non-durable bytes, or a reissued one-use identity blocks activation. A fixture
signature remains test evidence only. No private activation file or live prerequisite receipt was
created by development of this factory.

Operator sequence: obtain the independent live classification/preflight/rehearsal evidence; review
and sign the exact task/recovery claim with host-owned keys; explicitly initialize/register its
separate `MetadataFenceStore`; serialize the reviewed enrollment; independently hash/pin its bytes
and task identity in `MetadataHostSettings`; then explicitly call the constructed host service.
`serialize_reviewed_metadata_enrollment` only returns validated bytes. It does not write files,
sign authority, enable execution, or manufacture a missing prerequisite.

The independent authority database is outside the transaction-journal directory. Atomic, HMAC-bound
SQLite state retains one-use operation claims and opaque process nonces. UNKNOWN/nonquiescent
process state blocks further transport and restart; missing databases/lease rows are never recreated
by the factory. This trusted store and its key must be protected independently from candidate and
journal data. Journal suffix removal cannot roll back its dispatch state. Whole-database rollback by
an administrator is outside that storage trust boundary and must not be presented as tamper-proof
hardware storage.

The factory installs only its closed classification wrapper and the real `SubprocessCliRunner`.
The direct Windows launch prefix, native executable, CLI entrypoint and package/version are checked
inside the shell-false spawn path, with no-write/no-delete native file guards held through process
collection. Each metadata invocation performs one fixed classification sequence within that same
invocation's total timeout. Any contained-process failure stops the remaining sequence. The factory
and its tests do not grant campaign acceptance or release eligibility; a live run and durable
accepted browser/mutation/restoration evidence remain separate requirements.
