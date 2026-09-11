# Live Salesforce integrated-demo execution contract

## Purpose

The 24-hour demo is not complete from fixtures, mocks, local source analysis or an already-open
Salesforce tab. A `100% live Salesforce campaign` means Neo itself completes every required gate in
`config/live-salesforce-acceptance-profile.json` on the configured org machine and retains a
sanitized, source-bound receipt for each gate.

This contract is implementation authority for live Salesforce acceptance. It does not authorize a
deployment, data mutation, approval action or release decision.

## Evidence identities that must remain separate

Neo must label and bind these identities independently:

- `LOCAL_CANDIDATE`: current Git candidate bytes and build identity;
- `LIVE_BASELINE`: the metadata, API, browser and test evidence observed before deployment;
- `CANDIDATE_CHECK_ONLY`: validation of candidate bytes without proving those bytes are deployed;
- `DEPLOYED_CANDIDATE`: post-deploy evidence proven to run against the exact candidate;
- `RESTORED_BASELINE`: post-restore evidence plus residue/deletion reconciliation.

No identity can substitute for another. In particular, a connected CLI, successful REST request,
baseline Apex run or check-only deployment cannot prove that the changed candidate works live.

Every `dependsOn` edge means the dependency's current `PASSED` receipt is an implicit, exact,
same-campaign input root; it is not only scheduling metadata. Stale or cross-org dependency receipts
fail each downstream gate that consumes them.

## Required live gates

All nine live gates are mandatory for the integrated demo claim. A missing or unsafe gate is
`BLOCKED` or `NOT_RUN`; the workflow continues only on independent work that does not depend on it.

Before `SF-L03` onward can run, a new strict versioned source-operation profile and host-owned
`LiveTargetPlan` producer must exist. They bind exact captured contract bytes/digest, reject unknown
or duplicate keys, and expose typed REST variables/schema, exact metadata members, mandatory test
obligation IDs and browser intents. The producer derives the complete target partition from the
current verified change and semantic graph, then intersects it with host policy. Policy may allow
all or block with explicit denied/unmapped targets; it may not silently select a passing subset.
Candidate target derivation additionally requires an independently supplied exact SHA-256 pin for
the obligation catalog and exact byte pins for the verified-change, operation-seed, release-input,
live-target, browser and test policies. Each target partition must cover every entity independently,
and every target must have a one-to-one proof binding its exact target digest to the complete set of
current operation-seed receipts and the mandatory policy roles for that partition. A catalog's own
`complete` flag, union coverage across partitions, or a rehashed/rebound proof is never completeness
evidence. `knowledge/project-index.json` and `knowledge/application-graph.json` remain integrity and
developer-trace inputs only; their requirement edges cannot grant, select or block product targets.
The existing free-text contract cannot be heuristically parsed to satisfy this gate.
All plan and gate receipts bind the canonical acceptance-profile digest, so a prior receipt cannot
survive profile or policy rotation without current replay.

By default, compiler-required local validations must complete and undergo exact aggregate replay of signatures,
artifact bytes, durable ledger entries, complete obligation membership, runner pins and expiry before
any Salesforce subprocess, including `sf --version`. Composition repeats that verification, and a
cached positive must recover and verify the same local proof roots. All obligations are sealed only
after the whole batch passes, with one common expiry capped by the existing host campaign and source
deadlines; this does not reset or extend task authority. If that budget expires, no Salesforce command
may follow. Source-owned `NODE_TEST` currently remains blocked in the production subprocess adapter
because candidate-controlled stdout can forge Node's internal test-event framing; an outer host HMAC
does not repair that trust boundary. Offline fake-runner checks are not production execution proof.

A source obligation may explicitly request baseline-only deferral using `requiredEvidencePhases`;
omission means all four evidence phases remain mandatory. A source request alone never waives work.
Only a separate, externally SHA-pinned host phase policy can permit the exact declared nonruntime
catalog/documentation obligation to remain `UNRESOLVED` and `not_required_for_phase: LIVE_BASELINE`.
The policy binds project, source-contract bytes, full candidate-tree/input roots, exact obligation,
changed relative locators/categories/bytes, validator implementation and bounded validity. Unknown,
runtime and consumed-contract changes cannot be reclassified or exempted. Their complete semantic
targets and independent obligations remain required.

The baseline service may omit local execution only when every unresolved local obligation has that
exact typed deferral. Otherwise the full local validation set is still required before Salesforce.
Composition, the plan, expected execution contract and durable cached replay preserve every deferred
identity and the phase-policy hash; no local receipt or passing test is invented. The only executable
target gates for a deferral-bearing plan are the fixed read-only `SF-L03`, `SF-L04` and `SF-L05` set.
`SF-L01`/`SF-L02` are still mandatory identity prerequisites. Apex, browser, mutation and release
obligations stay visible, non-exempt and non-passing; this policy cannot execute or promote them.
API/MCP callers cannot select the phase, policy or exemption. Candidate check-only, deployed candidate,
restored baseline and release readiness remain blocked until their full required evidence exists.

### Host-only enrollment review before baseline enablement

`HostBaselineEnrollmentProposalService` in `baseline_enrollment_proposal.py` provides the host
operator's enrollment/configuration preparation boundary. Construction takes Settings, the trusted
repository root, the configured baseline service's zero-argument `capture_compilation` callback,
and the existing bounded CLI runner. It does not duplicate source selection or expose an API/MCP
operation. Neither construction nor explicit confirmation performs a subprocess.

The operator must call `confirm_classification_only` with the literal `OPERATOR_CONFIRMATION` and
the current task's SHA-256 authority identity. The returned opaque object is valid only in that
service instance and process, cannot be serialized as authority, and is consumed on the first
`propose` attempt including failures. Its five-minute maximum lifetime, fixed Settings alias,
classification operation-plan digest, fixed task purpose and no-write/no-application-access
semantics are rechecked around every command. Changing relevant Settings invalidates it. This
confirmation permits only local `sf --version` and the existing six system-classification reads;
it grants no baseline, dataset, metadata, browser, test, mutation or receipt authority.

The version observation is separate from the exact classification operation plan. Display supplies
the bounded API version; the shared classifier reconciles Organization, the real userinfo HTTP
envelope, active User, repeated subject and repeated display. It derives only hashed identity pins.
Production/unknown stops after Organization, before userinfo or dependent reads. Responses, org
IDs, usernames, hostnames, tokens and OIDC claims remain transient. No proposal creates a receipt,
writes `.env`/`.runtime`, enables execution or calls the live baseline service.

Successful output remains `REVIEW_PROPOSAL_ONLY`, `authorizesExecution: false`, with the proposed
`HostBaselineConfiguration`, observed `ClassificationPins`, exact compilation/candidate/source/
graph/tree identities, canonical and file-byte policy/profile identities, installed CLI version,
implementation digest and operator checklist. Expiry is capped by confirmation, source and policy
validity; commands recheck policy bytes and implementation currentness. A missing external target
policy pin is an explicit review gap; a configured mismatched pin blocks. The default-deny target
policy remains default-deny. Unresolved local-validation obligations remain separately identified.

`serialize_baseline_enrollment_proposal(proposal, observed_at=...)` checks the complete proposal and
expiry and returns only sanitized review-wrapper bytes. The wrapper cannot be loaded as baseline
configuration. Independent review, exact draft-file byte hashing, separate policy and issuer/key
provisioning, explicit task authorization and host lifecycle reload remain necessary before
enablement. Even an independently installed draft must pass the normal current enrollment and
scope gates on actual execution. Fake-invoker tests establish these boundaries only; they are not
Salesforce acceptance evidence. See Docs/20 for the host-only preparation example.

1. `SF-L01` — require a trusted host-owned expiring enrollment that binds the alias reference,
   expected non-secret org fingerprint/instance, non-production class and permitted persona. The
   issuer is independent of the caller, model and source contract.
   Allowed classes are sandbox, scratch org and Developer Edition; production and unknown are
   blocked. Alias names and `isSandbox` alone are never classification proof.
   The current fixed bootstrap proves sandbox or Developer Edition only; a scratch-org claim
   still blocks without independent lifecycle proof, even though the profile reserves that class.
2. `SF-L02` — prove machine-local Salesforce CLI authentication through the product-owned
   adapter. The separately pinned classification policy v1.1.0 permits only the fixed sequence
   `org display` → exact `Organization` query → `GET /services/oauth2/userinfo` → exact active
   `User` query by observed user ID → repeated userinfo → repeated display. Production or unknown
   classification stops before userinfo/User/application reads. Userinfo must be the bounded CLI
   HTTP envelope with status 200 and a JSON body, never a redirect; its `user_id`, `organization_id`
   and `preferred_username` must reconcile with enrollment, display and the exact active User.
   Neither a cached display identity nor its preferred alias proves the current server subject.
   The invocation alias remains host-fixed, while another displayed alias for that same resolved
   identity is informational. No username is interpolated into SOQL. The resulting org and actor
   must exactly match enrollment before any later command.
3. `SF-L03` — perform one bounded standard Salesforce REST read through Neo's MCP adapter, which
   wraps the shared service and consumes the underlying live-adapter receipt, validating the route
   and response shape under policy.
4. `SF-L04` — discover the custom REST route from the selected source contract, invoke it through
   the same governed MCP/service boundary, and enforce
   an exact `GET` route/field/schema/size host policy and capture a live response. The contract
   proposes a target but grants no authority; runtime code may not contain the demo route.
   Both API gates also require a current independent synthetic-dataset scope receipt that supplies
   permitted record values/ownership marker, object/field projection and predicates. Broad query,
   list, wildcard and non-synthetic results are blocked and redacted before evidence.
5. `SF-L05` — retrieve a bounded Metadata API manifest into ignored runtime storage,
   using an exact host type/member allowlist intersected with source-derived scope. Refuse wildcard,
   whole-org and secret-bearing families; cap component/file/byte/time scope, clean fresh no-follow
   staging and persist only canonical digests/counts/safe relative locators.
6. `SF-L06` — obtain an ephemeral frontdoor URL only after org classification and
   hand it directly, in memory, to an isolated Playwright context. Prove session values never enter
   logs, exceptions, traces, screenshots, artifact indexes, prompts or API/MCP responses and close
   the context on every outcome.
7. `SF-L07` — verify the live Lightning origin and selected application surface, capture
   a redacted DOM snapshot, rank locator candidates, abstain on ambiguity and retain readback.
8. `SF-L08` — select tests from source/impact evidence, execute them in the live non-production
   org under current-task test authority and bounded polling, then bind exact method results to the
   org fingerprint and effective `LIVE_BASELINE` metadata/source root. Candidate-phase Apex evidence
   is separately bound to the exact deployed-candidate root in `SF-C05`. Broad suites, caller test
   names, queued, partial, skipped or missing outcomes cannot pass.
   The exact complete executable method set comes from every mandatory obligation; host policy may
   allow all or block the gate, but may never choose a passing subset.
9. `SF-L09` — complete live capture, unique candidate selection, explicit approval, permitted
   non-destructive browser application and current-page readback with before/after evidence.

## Candidate campaign

The deployed-candidate campaign is also mandatory for `100%` Salesforce-campaign completion. It is one
ordered, fail-closed chain with six separately receipted gates:

1. `SF-C01` captures pre-state and validates an independently authorized current restore-rehearsal
   receipt for the same org, metadata families and synthetic-data scope; it is read-only and the
   profile itself grants no rehearsal authority;
2. `SF-C02` runs check-only validation for the exact candidate manifest and build;
3. `SF-C03` obtains a versioned, expiring current-task authorization binding the non-production
   fingerprint, exact source/build/manifest and bounded operation set, then deploys only that
   authorized candidate;
4. `SF-C04` retrieves and reconciles deployed metadata against the exact candidate;
5. `SF-C05` executes candidate-bound standard/custom API, live Lightning, browser-recovery and
   selected Apex checks. It consumes distinct current receipts for REST, custom API, metadata,
   browser session, Lightning assertions, Apex tests and browser recovery; every receipt shares the
   exact org/campaign/candidate/deployment root and none can be reused from baseline; and
6. `SF-C06` restores metadata and synthetic data, then reconciles changed, added and deleted residue.

Unknown or production classification, mismatched source, a stale restore proof, partial check-only
validation or absent authorization stops before mutation. Browser actions remain read-only unless
the same authorization explicitly names the action and its readback/restore proof.

Before deployment dispatch, a failed prerequisite stops and leaves later gates `NOT_RUN`. After any
deployment request is dispatched, failure, timeout, cancellation or unknown external state always
transitions to `SF-C06`; it may not terminate through the ordinary stop path. If automated recovery
cannot run, Neo records `RESTORE_REQUIRED_MANUAL_INTERVENTION`, quarantines that org campaign,
blocks all acceptance and prohibits another deployment attempt until reconciliation is proven.
`SF-C06` activates from the dispatch event, not successful completion of `SF-C03`; it performs a
current readback before any recovery action and explicitly inverts supported `ADD`, `MODIFY` and
`DELETE` operations. Unsupported or non-round-trippable families block the campaign before deploy.
On the normal-success branch it also requires the `SF-C05` candidate-assertion receipt. On a failed,
timed-out or unknown branch, missing `SF-C04`/`SF-C05` is expected and cannot suppress recovery;
unknown remote state is read back before restore or quarantine.
Before `SF-C03` dispatch, an independent authorization must also issue a campaign-scoped recovery
permit for the exact compensation manifest and synthetic-data scope, with remaining validity longer
than the bounded worst-case campaign plus restoration. Dispatch activates that permit irrevocably;
the recovery path does not depend on the issuer remaining available afterward. A missing, narrower,
tampered or short-lived permit blocks deployment.

## Receipt and terminal-state contract

Every accepted gate receipt binds the campaign/run/gate/requirement/capability IDs, evidence phase,
project and source-contract snapshot, assessed and validated candidate identities, build and
operation-plan digests, org/actor fingerprints, classification and task-authority receipt IDs,
adapter/CLI/runner versions, policy identities, input receipts/root, exact test IDs, bounded
timestamps/expiry, assertions and per-test outcomes, sanitized artifact indexes, gaps/error class
and its own digest. A field irrelevant to a phase is explicitly null under the versioned schema; it
is not silently omitted.

The receipt producer cannot accept a caller-selected terminal state or caller-selected artifact
digests. Before execution, the production compiler emits exact canonical
`ExpectedExecutionContract` bytes and an independent durable
`EXPECTED_EXECUTION_CONTRACT_RECEIPT`. That non-authorizing contract pins execution ID, plan and
scope, authenticated producer/runner/tool versions and the exact assertion predicates,
cardinalities, projections, metadata members and compatible dataset roots. The runner embeds each
sanitized result's exact bytes in a typed assertion artifact and authenticates the whole artifact
with a host-configured runner key. Replay rehashes the stored result bytes and index, verifies the
runner signature, resolves the independent contract receipt, and compares expected versus observed
assertions before any `PASSED` receipt can be signed. The host runner-key registry binds each key ID
to one exact producer ID, runner ID, allowed gate-ID set and allowed receipt-role set; overlapping
key claims are invalid, and the artifact key ID must equal the independently compiled
`runnerKeyId`. Merely configuring another trusted key never permits it to sign for that runner.
Missing, expired, corrupt, fabricated-version, fabricated-result, wrong-key, wrong-runner or
cross-root artifacts fail closed. The SQLite and private-schema PostgreSQL stores are append-only
evidence foundations; their offline tests do not satisfy a live gate.

Dependency and input receipts are replayed from exact durable bytes both when gate authority is
bound and immediately before append. Each must be signed, current, non-future, in the phase allowed
by its own gate or supporting-receipt contract, and causally prior to the execution start it
authorizes. A receipt that expires after binding but before append, or whose terminal timestamp is
later than the dependent execution start, invalidates the gate rather than being grandfathered by
the earlier binding.

Allowed terminal states are `PASSED`, `FAILED`, `BLOCKED`, `NOT_RUN`, `TIMED_OUT` and `CANCELLED`.
Only `PASSED` contributes to completion. A timeout or cancellation retains an unknown external-job
state when appropriate and cannot imply that a remote job stopped. Missing, stale, wrong-phase,
wrong-org or fixture-issued receipts fail validation and cannot be aggregated into a pass.
An execution receipt never authorizes itself: test execution, browser application, deployment and
recovery each require a distinct current independently issued authority input bound to the exact
org/campaign/source/build/manifest/operation scope, with no widening.

## What fixtures may prove

Fixtures, mock CLI runners and mocked browser tests remain required for fast positive, negative,
failure and degradation verification. They may prove that Neo refuses unsafe input. They may never
satisfy a positive live gate, increase live completion or be described as a live receipt.

An interactive Chrome login is helpful to the operator but is not an automation credential. The
accepted browser path starts from the classified org, requests a short-lived frontdoor session and
verifies the resulting origin in the isolated worker.

The required negative matrix includes production/unknown classification, alias remap, org/instance/
actor mismatch, stale enrollment, bootstrap-output leakage; zero/ambiguous/duplicate/unsupported,
renamed, permuted, omitted, extra and many-target plans; metadata wildcard/unknown/denied family,
path traversal, symlink, oversize, timeout, partial result and cleanup failure; broad/caller-selected/
queued/partial/skipped Apex tests; and frontdoor canary scans across all logs, errors, traces,
screenshots, artifact indexes, prompts and API/MCP projections with context closure on every branch.

## Completion calculation

Live Salesforce campaign completion is binary at the gate level:

- denominator: all fifteen required live and candidate gate IDs in the machine-readable profile;
- numerator: required gates with a current validated `PASSED` receipt for the same campaign root;
- completion: numerator divided by denominator;
- final state: `100%` only when all fifteen gates pass and the candidate campaign ends with successful
  restore and zero-residue reconciliation.

Planning estimates and local test counts are reported separately. They cannot change this
calculation or fill a missing live gate.

`15/15` proves the Salesforce campaign only. Whole-demo completion additionally requires the
source/graph pipeline, real model-provider specialists, live PostgreSQL save/resume, ordinary-run
orchestration, real FastAPI-to-dashboard execution, durable receipt replay and golden-scenario
gates in `Docs/08-roadmap-24h.md`; those dependencies cannot be inferred from this subprofile.

The machine-readable profile is definition-only, not a mutable pass ledger. Until the versioned
runtime receipt schema, trusted issuer registry, replay validator and completion calculator exist,
its accepted result is always `0/15`, `NOT_RUN`, regardless of edits to planning documents.
