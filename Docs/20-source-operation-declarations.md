# Source-operation declarations

The source contract must explicitly reference a source-owned operation document:

```json
"sourceOperations": {
  "schemaVersion": "1.0.0",
  "locator": "contracts/source-operations.json"
}
```

The locator is relative to the configured local Git source root. Both files and every selected
Apex source file must occur at the same exact relative locator, byte length and SHA-256 in the
current candidate graph's manifest-bound file dispositions. A self-consistent digest supplied
alongside different bytes is insufficient. Capture checks file bounds before reading, rejects
symlinks, junctions/reparse points and special files, verifies opened-file and path identities
before/after reading, and rejects expired or future captures.

`config/source-profiles/source-operation-declarations.schema.json` defines the closed version 1.0.0
schema. Every object rejects unknown fields. The document declares all six arrays explicitly:
`standardRest`, `customRest`, `syntheticDatasets`, `browserIntents`, `nonRuntimeFiles` and
`localTestObligations`. REST declarations specify
an existing graph node, exact GET template, every variable and its type/source, dataset ID, closed
response field types/requiredness, cardinality and response byte bound. Dataset declarations specify
an existing object node and exact object identity, projected fields, explicit ownership predicate
and marker, and exact cardinality. Browser declarations specify an existing component node and
explicit application, action and surface intent. Source declarations supply facts, never authority.
The compiler derives current changed-entity bindings through the source evidence graph and host
policy must permit the complete resulting target set.

## Closed locator-rebind source binding (FOUNDATION)

`LOCATOR_REBIND_PREVIEW` additionally requires `locatorRebind`; other actions reject that field.
Its `navigation` is `DATASET_RECORD_PAGE`, resolved as exactly one authorized dataset member by
the declared dataset ID, object, projected identity field and exact ownership-marker member.
No URL, record ID, query, alias or source path is accepted. The dataset must still be independently
authorized and resolved at execution; source declarations never establish live ownership.

`metadataDrift` declares exactly one `FlexiPage` member, component name and instance identifier,
one exported String property, `operation: SET_SCALAR`, exact `baselineValue`, one bounded
`alternateValue`, file/byte limits, and `restoration: PREIMAGE_EXACT`. `allowedPreStates` is an
explicit closed set: `{ "state": "ABSENT" }` and/or `{ "state": "PRESENT", "value": <baseline> }`.
Entries are unique and sorted ABSENT before PRESENT; a present value must equal the declared
baseline, and the alternate must differ. Values are bounded identifier-like String tokens,
not XML, selectors, expressions, arrays or arbitrary objects. Omitting ABSENT forbids insertion;
a caller flag or inherited default cannot supply that missing source-declared precondition.

The compiler captures the exact candidate page and component metadata
files through the existing manifest-verified source port. It checks a unique matching RecordPage
instance and object, the exported property and explicit allowed values, and requires the LWC
default to equal `baselineValue`. A current page property must be one exact scalar baseline value
with PRESENT admitted; an absent property requires ABSENT explicitly admitted. Duplicate or
structured values and any unlisted prestate fail closed. The complete operation and prestate set
remain in the browser target hash; widening the set changes the host-reviewed target identity.
Those XML byte hashes enter the derivation proof.
Unsupported, missing, mismatched or ambiguous source facts block the browser derivation.

`obligationPolicy: COMPLETE_DECLARED_SET` binds all sorted unique obligations. Each obligation
contains a bounded `ATTRIBUTE_EQUALS` original locator identity (no raw CSS/XPath), an exact
object/field or object/action semantic identity, and the complete sorted read-only assertions:
`EDITABLE`, `ENABLED`, `VISIBLE` for a field; `ENABLED`, `VISIBLE` for an action. Shared original
or semantic identities, partial metadata identities, mixed field/action identities, duplicate or
unsorted obligations and write assertions are rejected. Removing even one obligation changes the
browser target hash and cannot inherit the host policy pin for the complete set.

The full nested declaration survives compilation and plan sealing. Host policy must also permit
the declared metadata type and bounded file/byte scope. `dataMutation: FORBIDDEN` excludes fill,
click, save, submit or record creation. The action probe locates and inspects; it never invokes.
`PREIMAGE_EXACT` requires a future authorized executor to capture and restore the exact live
preimage, including an originally absent property; the candidate source bytes are not a runtime
prestate or restore receipt. This source/compiler vertical remains **FOUNDATION**, non-authorizing
and without live acceptance credit: deployment/check-only authority, live preimage preservation,
all-obligation browser receipts and verified restoration remain separate execution work.

The host-only `live_healing_bridge` consumes the configured frozen compilation/composition and
derives every `SourceHealingTarget`, with exact original/semantic locator hashes and the complete
read-only assertion conjunction. Its pure metadata preview reuses the shared recovery XML editor;
it accepts only exact host preimage bytes/roots, not a caller XPath, route, alias or file path.
The declaration root is `contract_sha256(drift.model_dump(by_alias=True, mode="json"))`, including
all allowed prestates. The final recovery intent root instead uses canonical runtime snake-case
model JSON. Candidate-phase evidence must be independently authenticated; baseline expectations
cannot be relabelled. The existing production browser coordinator has no stateful all-obligation
healing dispatch, so preparation explicitly remains `NOT_READY`, non-authorizing and without
acceptance credit. See [the host bridge boundary](06-ui-automation-and-healing.md).

Generate/check the source schema with `python scripts/catalog/build_source_operation_schema.py`
and its `--check` option. The generated JSON schema is paired with deterministic model validators
for cross-field completeness and dataset/identity relationships.

The compiler does not interpret legacy capability prose, route strings with ranges, variable-name
suffixes, locator-demo notes or dataset plan narratives. It does not invent `Id`, `Name`, ownership
markers, `schemaVersion` response fields, browser actions or record counts. Legacy source contracts
without explicit declarations remain blocked until the source owner provides and reviews them.

Every independently selected test remains a mandatory obligation, including unsupported tests.
Current candidate Apex bytes resolve the exact supported `@isTest`/`testMethod` method declarations;
unknown signatures, ambiguous classes, duplicate methods and non-Apex obligations produce typed
blocking derivations. An Apex target for the same entity cannot hide a Jest/browser obligation.
Any unsupported derivation blocks plan composition and expected execution contract production;
there is no class-wide Apex or partially authorized subset fallback.

Expected execution contracts retain source-declared projection/cardinality and exact dataset
identity. Host-owned runner provenance includes `runnerKeyId`, binding the expected signing key
alongside producer, runner and tool versions. These artifacts remain derivation/expectation
foundations and do not satisfy a live Salesforce acceptance gate by themselves.

## Complete dataset fanout and parent-bound history

Every response field declares both `required` and `nullable`. A required nullable field must
exist; an explicit null is not an absent field. No default nullability or invented non-null
business value can remove boundary cases.

An optional `expectedLiteral` is a closed `{ "value": <JSON primitive> }` wrapper. Its absence
means no constant constraint; `{ "value": null }` explicitly requires a present null when the
field is required. The value must match the declared primitive type/nullability. A source-owned
schema version or object name can therefore be asserted exactly without inventing application
constants in Neo. Expected projection hashes bind this literal constraint independently of type.

Each dataset declares its projected `identityField`, source-owned exact marker set and an
independent `CURRENT_ENROLLED_ACTOR` ownership predicate. `SOURCE_LITERAL_SET` supports `IN_SET`
with explicit sorted unique values, or `EQUALS` with exactly one value. Current actor predicates
use `EQUALS` and an empty literal array: the independently enrolled actor supplies the value at
execution, never a source credential, record ID or actor alias. Exact marker count equals dataset
cardinality. Omitted, duplicate or additional members cannot be silently sampled away.

REST declarations require `requestExpansion: EACH_DATASET_RECORD`, an explicit `datasetField`
for each dataset variable (null for API version), `responseRecordPath` (empty string for the root),
and `datasetFieldPaths` mapping declared dataset fields to exact dotted response paths. A standard
record read projects its exact fields; its Salesforce `attributes` transport structure must be
declared and independently validated when present. The custom API can return only parent identity
while independent authorized standard reads establish ownership before and after the custom read.

`parentBindings` is an explicit array, empty when no nested collection is authorized. A child
binding declares its exact `collectionPath`, item-relative `parentIdPath`, dataset identity field,
and `maximumCardinality`. Every child must bind the independently resolved requested parent;
an authorized parent does not authorize unrelated private child records. The complete child
projection and its nullability remain mandatory. Paths use the same dotted/`[]` grammar as the
response projection; no second path language or prose inference is accepted.

The host stores API versions as the bare number (`67.0`, not `v67.0`). Accepted legacy host inputs
are normalized once; the declared standard route owns the literal `v` in `/services/data/v{apiVersion}`.
The fixed classification adapter adds that prefix only at its own transport boundary. Ambiguous
or injected versions are rejected, not repaired from route prose.

`sf api request rest --json` returns a transport envelope: `status`, `warnings`, and a `result`
containing exactly `statusCode`, `headers`, and `body`. The live executor requires an integer 2xx
HTTP status and validates the source projection against the bounded JSON-object **body**, never
against the CLI wrapper. Bounded headers and warnings remain transient and never enter receipts.
Redirects, non-JSON/string bodies, extra envelope fields, malformed headers and overflow fail closed.
Offline tests mirror the installed CLI contract; opt-in checks inspect installed package source
and current local AUT declarations without contacting Salesforce.

Expected execution assertions bind invocation count, expansion, aggregate cardinality, per-response
cardinality, nullable projection and the full response/parent mappings. All dataset members must be
accounted for. The source-owned AUT response 1.1.0 adds parent identity and child parent bindings;
that local API candidate needs independent Apex execution, check-only/deployment authorization and
fresh live evidence. Historical 1.0.0 smoke results cannot satisfy the new contract.

## Every changed file remains accounted

The compiler partitions every verified changed-file binding, preserving exact path, operation and
both side-file evidence records. Salesforce runtime/metadata changes require semantic targets and
independent test obligations. The exact consumed agent and operation JSON contracts are recorded
as `CONSUMED_SOURCE_CONTRACT`; they are compiler inputs, not invented live metadata targets.

An exact `nonRuntimeFiles` declaration can classify only a graph-confirmed nonsemantic file with
a supported category/suffix: documentation, generated JSON index, local catalog script or local
JSON test specification. It cannot override Salesforce semantics or consumed contracts. Unknown
changed files remain `UNKNOWN_BLOCKING`, including unrelated executable code. A declared file
rename does not automatically inherit classification or proof.

Each nonruntime file cites explicit `localTestObligations`. The closed command contract declares
the Node test or deterministic generated-check runner, exact working directory and script/test
locators, bounded timeout and `COMPLETE_PASS_NO_SKIP`. Arbitrary command arguments and test-name
selectors are not accepted. A generated index must bind an exact generator check and output path;
its currentness cannot be asserted merely from file existence.

The compiler binds the **complete candidate file manifest**, including unchanged transitive inputs,
to each local obligation, its candidate-tree root, command contract and relevant changed-side
evidence. A host runner must stage only these bounded, no-follow, hash-verified files; running a
script against an unbounded mutable source checkout is insufficient.

`LocalValidationArtifact` is immutable evidence, not execution authority. It records the exact
candidate/tree/command roots, input paths and bytes' digests, working directory, every script
invocation, host-pinned runner/implementation/Node versions, measured complete/pass/fail/skip/cancel/
todo counts, bounded times, sanitized result root and exact regenerated output bytes. The closed
host configuration supplies runner pins; API/MCP callers cannot nominate an alternative runner.

Composition accepts one typed artifact plus signed `LOCAL_SOURCE_VALIDATION_RECEIPT` pair per
required obligation. The verifier checks the entire current scope, authorized `PRODUCT_EXECUTION`
issuer/signature, current capture lifetime, exact command/inputs/output currentness and timeout.
It independently replays both canonical artifact bytes and signed receipt bytes from host-owned
immutable artifact storage and the receipt ledger. Missing, duplicate, extra, stale, tampered,
failed, empty or skipped results block rather than turn into a success flag.

Only complete verified pairs satisfy local validations; verified artifact/document/receipt
roots are sealed into the composition and expected execution contract.
The expected execution lifetime is capped by every required local receipt's expiry; verification
at composition time cannot extend an older local result through a longer-lived target plan.
Source compilation remains unchanged with `satisfied: false`: facts and externally verified
evidence are separate. Unknown
files, unsupported mandatory tests and live authorization restrictions still block. Local proof
does not satisfy a Salesforce gate, authorize deployment or substitute for live tests. Host runner
execution and wiring are a separate integration slice; absent evidence remains honestly blocked.

## Phase applicability without false local passes

`requiredEvidencePhases` defaults to all four evidence phases. Its only supported alternative omits
`LIVE_BASELINE` while retaining `CANDIDATE_CHECK_ONLY`, `DEPLOYED_CANDIDATE` and `RESTORED_BASELINE`.
This is a source request, not permission. `HostLocalValidationPhasePolicy`, inside independently
SHA-pinned host configuration, must approve the exact obligation declaration, full input manifest,
candidate tree, changed nonruntime relative paths/categories/content and source-contract identity.
Its implementation digest covers the phase validator, composer and target-plan model. A renamed,
stale, mismatched or independently rehashed declaration does not inherit an earlier exemption.

The deterministic classifier returns `DeferredLocalValidation`, not `VerifiedLocalValidation`:
`status: UNRESOLVED`, `satisfied: false`, and `not_required_for_phase: LIVE_BASELINE`. The current
command root and remaining mandatory phases are preserved. The compiler itself remains immutable.
Absent policy, absent source request or an unknown/runtime/consumed-contract classification stays
blocking. The composer retains unresolved local support requirements and every source-derived
Salesforce target. Policy-scoped plans and expected contracts seal the same deferrals, phase-policy
digest and fixed read-only gate set (`SF-L03`, `SF-L04`, `SF-L05`); executors enforce that intersection.
No browser/Apex execution, candidate acceptance, deployment or release is authorized by deferral.

Source-owned `NODE_TEST` execution currently fails closed with
`LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED`. Candidate stdout is not a trustworthy event source;
authenticating a summary parsed from it does not establish that tests ran. The actual production
dispatcher refuses such execution before process creation and denies child-process permission.
Same-process lexical secrets are not an isolation boundary either. The offline fake runner validates
receipt mechanics only. A future trusted host validator registry must preserve the independent
domain and generated-currentness obligations, not replace them with a weaker manifest-only check.

### Offline proposal and independent installation

This feature is disabled by default. It is not configured through a request body, MCP argument,
source contract, generated graph or the general baseline configuration's embedded fields.

1. Finish source edits and regenerate/check the source-owned catalogs. With the configured local
   baseline service, call `propose_local_validation_phase_policy(service.capture_compilation,
   observed_at=datetime.now(UTC))` from `neo_sf_q_intel.phase_policy_proposal`. Do **not** call
   `service.run()`. The fixed capture callback uses configured local Git/source; the helper accepts
   no alternate repository, API route or executable and performs no Salesforce/LLM operation.
2. Treat the returned `REVIEW_PROPOSAL_ONLY` wrapper as review material, not an enabled policy.
   Independently review the complete unresolved obligation list, exact source/tree/file/category
   roots, remaining candidate/restore requirements, host implementation hash and short expiry.
   Confirm that no unknown/runtime/consumed-contract file is being waived. A nonlocal blocker
   causes the helper to refuse a proposal. The helper never saves files or changes `.env`.
3. Only after that independent review, extract the exact `draft_policy` JSON into the machine-local
   `.runtime/host-local-validation-phase-policy.json`. Preserve canonical model values, including
   UTC timestamps. Compute the SHA-256 of the actual saved UTF-8 bytes; the policy's internal
   `policy_sha256` is a different canonical-content identity and is not this external file pin.
4. Set `LIVE_LOCAL_VALIDATION_PHASE_POLICY_ENABLED=true`, the relative
   `LIVE_LOCAL_VALIDATION_PHASE_POLICY_PATH=.runtime/host-local-validation-phase-policy.json`, and
   `LIVE_LOCAL_VALIDATION_PHASE_POLICY_SHA256` to that exact file-byte digest in private local
   configuration. Paths outside `.runtime`, absolute/traversal paths and missing pins are rejected.
   The factory loads only this separate pinned file; it rejects an embedded policy supplied in
   the general baseline configuration. The broker rechecks exact bytes and validity before each
   operation. Restart/reload through the normal host lifecycle; never patch a running broker.
5. If source bytes, catalogs, policy/implementation, identity or expiry change, stop and repeat
   capture plus independent review. Do not edit roots to make a stale policy pass. Keep policy
   enablement false when no reviewed policy is available. Read-only baseline observations cannot
   repair the unresolved NODE_TEST runner or satisfy any candidate/deploy/restore/release gate.

### Host-only baseline enrollment/configuration proposal

The disabled baseline service still supplies `capture_compilation`, so preparation does not require
enabling baseline first. The composition root can construct the following host-only helper using
the same Settings and repository root already used to construct that service:

```python
from datetime import UTC, datetime

from neo_sf_q_intel.baseline_enrollment_proposal import (
    OPERATOR_CONFIRMATION,
    HostBaselineEnrollmentProposalService,
    serialize_baseline_enrollment_proposal,
)

preparation = HostBaselineEnrollmentProposalService(
    settings,
    repository_root,
    capture_compilation=service.capture_compilation,
)
# Only after the operator explicitly authorizes the fixed system-classification reads:
confirmation = preparation.confirm_classification_only(
    operator_confirmation=OPERATOR_CONFIRMATION,
    task_authority_sha256=current_task_authority_sha256,
)
proposal = preparation.propose(confirmation)
review_bytes = serialize_baseline_enrollment_proposal(proposal, observed_at=datetime.now(UTC))
```

This example is not a command to run automatically. `propose` is the only subprocess boundary:
it performs a bounded local CLI version read and the fixed six-step system identity classification.
It accepts no source path, alias, route, query, test, manifest or caller identity. The confirmation
is a one-use, five-minute, in-process object; repeating an attempt requires a new explicit operator
confirmation. It never runs `service.run()` or writes the resulting bytes. The proposal wrapper
contains `authorizesExecution: false` and cannot itself be loaded as `HostBaselineConfiguration`.

Review the complete wrapper and checklist before independently extracting `draft_configuration`.
Its canonical-content proposal digest is distinct from the eventual saved configuration file's
SHA-256. After separate task authorization, a host operator can serialize that exact model as UTF-8
JSON into the configured private `LIVE_BASELINE_CONFIG_PATH` and set `LIVE_BASELINE_CONFIG_SHA256`
to the actual file bytes' SHA-256. Independently review and pin target policy bytes using
`LIVE_TARGET_POLICY_SHA256`; a missing pin remains `TARGET_POLICY_EXTERNAL_PIN_REQUIRED` in the
proposal and the default policy grants no target scope. Keep enablement false until this separate
review/authorization is complete. Distinct issuer identities and private keys, phase applicability
policy and unresolved local obligations require their own checks. The helper generates no key,
signs no receipt, edits no configuration and grants no live gate, candidate or release acceptance.

If the proposal, classification, source, implementation or policy expires or changes, discard the
draft and repeat preparation under a fresh confirmation. Installation never extends the embedded
expiry. The baseline broker independently reclassifies current org/actor identity before dependent
work; proposal observations cannot replace those execution-time checks. `.env.example` already
contains all baseline configuration, target policy, issuer and phase-policy variables; enrollment
preparation introduces no enablement environment variable.
