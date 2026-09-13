# Strategic Deal Change Assurance Demo

## Purpose

This is the master demo scenario for Neo SF Q-Intel. It combines the previously separate
business-rule, UI locator-healing, persona-governance and live-Salesforce evidence stories into one
coherent Salesforce change-assurance flow.

The scenario is intentionally one realistic change, not twelve disconnected feature demos.

## Scenario narrative

A Salesforce team changes the Strategic Deal approval behavior and updates the Strategic Deal
Workbench UI. Neo must inspect the candidate change, trace downstream impact through the evidence
graph, ask graph-grounded agents for advisory findings, select impacted validations, handle UI
locator drift safely, preserve persona/security boundaries, collect live Salesforce evidence where
authorized, and produce a governed release posture without overclaiming.

Short demo name:

`Strategic Deal Change Assurance Demo`

Expanded demo name:

`Agentic Salesforce Change Assurance: Policy + UI Drift + Persona Governance`

## Candidate change bundle

The demo change can include these source-level themes:

| Change theme | Demo purpose |
|---|---|
| Strategic discount policy threshold or approval behavior | Drives graph impact, test selection, LLM advisory and release governance |
| Strategic Deal Workbench presentation/locator drift | Drives browser-worker and locator-healing evidence |
| Persona/permission-sensitive approval behavior | Drives governance, security and “admin is not VP” boundaries |
| Live Salesforce evidence collection | Proves real integration while preserving incomplete-gate honesty |

The candidate must remain synthetic and non-production. Local Git is sufficient; a remote Git
repository is not a runtime dependency.

## Unified demo flow

### 1. Capture candidate and compare to baseline

Neo captures the local candidate from Git and compares it to the baseline without trusting
caller-selected changed paths.

Capabilities highlighted:

- `source.verified-change-set`
- `source.tree-graph-production`
- `source.change-seed-mapping`
- `assurance.candidate-comparison`

Expected message:

> Neo captures the complete local candidate, derives a source graph and operation seeds, and keeps
> the result analysis-only until downstream evidence exists.

### 2. Build graph impact and evidence paths

Neo traces the policy/UI/persona change through source-owned graph relationships, canonical
ontology mappings and replayable evidence paths.

Capabilities highlighted:

- `reasoning.graph-impact`
- `reasoning.directional-propagation`
- `knowledge.evidence-graph`
- `knowledge.trusted-edge-envelope`
- `knowledge.complete-path-replay`
- `knowledge.canonical-ontology`

Expected message:

> The change is not treated as prose. Neo traces typed graph paths across Opportunity fields,
> Apex/Flow policy logic, approval behavior, LWC UI surface and validation obligations.

### 3. Run graph-grounded LLM advisory

Neo compiles a bounded graph/source context pack and asks model-backed specialists for advisory
findings. The LLM may propose; deterministic verification decides what can be accepted.

Capabilities highlighted:

- `reasoning.graph-context-compiler`
- `reasoning.hybrid-candidate-fusion`
- `reasoning.graph-grounded-agent`
- `reasoning.semantic-retrieval`

Expected message:

> LLM agents reason over bounded evidence packs with cited graph/source evidence. They do not grant
> release, invent graph facts or satisfy live gates.

### 4. Select impacted tests and obligations

Neo selects validations connected to the impacted graph paths.

Capabilities highlighted:

- `quality.test-selection`
- `runtime.salesforce-target-planning`
- `governance.release-input-integrity`

Expected validation families:

- policy boundary tests;
- approval/persona positive and negative tests;
- browser locator/UI tests;
- live Salesforce read/API/metadata evidence;
- live campaign gates when authority exists.

Expected message:

> Neo selects tests by graph-connected impact and obligation policies, not by a hardcoded demo list.

### 5. Demonstrate UI locator drift and safe healing

The Strategic Deal Workbench layout changes from baseline to reordered/regrouped. Old locators may
break or become unsafe. Neo/browser automation must validate identity before writing.

Capabilities highlighted:

- `automation.browser-worker`
- `automation.locator-healing`
- `automation.applied-healing`

AUT scenario source:

- `SalesForceAgentApp/strategic-deal-assurance/docs/locator-healing-demo.md`
- `SalesForceAgentApp/strategic-deal-assurance/data/locator-healing-suite.json`

Expected message:

> The automation does not force-click or choose the first candidate. It uses Salesforce object/field
> identity plus DOM/accessibility evidence, and abstains on ambiguity.

### 6. Demonstrate persona and permission governance

Neo separates administrator test/configuration authority from Regional VP business approval
authority. A denial for the VP persona is a security result, not a locator-healing failure.

Capabilities highlighted:

- `governance.claim-grounding`
- `governance.release-decision`
- `governance.measurement-contract`
- `runtime.salesforce-live-evidence`

AUT scenario source:

- `SalesForceAgentApp/strategic-deal-assurance/docs/test-plan.md`
- Security and approval cases in the Salesforce manual suite.

Expected message:

> Admin actions cannot be represented as VP approval evidence. Persona permissions, transport
> access and business authority stay distinct.

### 7. Collect live Salesforce evidence

Neo uses the host-owned non-production Salesforce identity to collect sanitized live evidence.

Capabilities highlighted:

- `runtime.salesforce-live-evidence`
- `governance.live-acceptance-evidence`
- `tools.mcp`
- `memory.postgresql`
- `memory.resilient-fallback`
- `memory.release-outcomes`

Expected message:

> Live Salesforce evidence is separated by phase: baseline, check-only, deployed candidate and
> restored baseline. Passing one phase never proves another.

Current proven live sub-results:

- live Salesforce read-only proof has passed through the configured CLI/API route;
- live headless browser profile/diagnostic has passed;
- scoped Workbench marker deploy/readback passed for `save-evaluate-live`;
- live LLM browser healing passed for `Name`, `Strategic_Deal__c` and `StageName` in the Workbench
  business action, followed by submit, success readback and persisted Salesforce outcome proof.

### 8. Show governed release posture

Neo renders a truthful status instead of forcing a green demo.

Capabilities highlighted:

- `ui.assurance-dashboard`
- `governance.release-decision`
- `governance.live-acceptance-evidence`
- `observability.agent-trace`

Expected message:

> Neo can show useful advisory and evidence while still refusing to claim release readiness until
> required receipts pass.

## Capability coverage matrix

| Requested capability | Covered by this scenario | Current claim level |
|---|---|---|
| Graph impact | Policy/UI/persona source changes propagate through graph paths | `FOUNDATION` |
| Evidence graph | Source-bound paths, ontology mappings and evidence IDs | `FOUNDATION` |
| Graph-grounded agent | Real LLM advisory over bounded graph/source context | `FOUNDATION` |
| Candidate assurance | Local candidate comparison and analysis-only bundle | `FOUNDATION` |
| Test selection | Impact-connected validation obligations | `FOUNDATION` |
| Governance | Claim grounding, release posture, gate honesty | `FOUNDATION` |
| Live Salesforce evidence | CLI/API/read-only and browser diagnostic evidence | `FOUNDATION`, partial live proof |
| Browser worker | Live profile, readback, business-action runner | `FOUNDATION` |
| Locator healing | Metadata/accessibility-based candidate discovery, real LLM ordinal proposal and deterministic action verification | `FOUNDATION` plus live three-field proof |
| ChromaDB retrieval | Roadmap semantic index behind retrieval port | `NEXT`; do not demo as complete |
| Candidate deployment | Scoped marker deploy/readback exists; full campaign deployment pending | `NEXT` / partial demo evidence |
| Live test execution | Browser-worker regression and one live Workbench business-action proof passed; full campaign gate set still pending | partial live proof |

## What can be demoed now

Use this as the honest current demo path:

1. Show candidate advisory from a local Salesforce app candidate with real LLM specialist captures.
2. Show graph/evidence/test-selection reasoning and release-blocking gaps.
3. Show locator-healing tests and the Workbench UI-drift scenario.
4. Show live Salesforce read/browser diagnostic, deployed marker readback and live LLM-healed
   Workbench business action persistence.
5. Show the gate matrix proving why full live acceptance remains incomplete.

## What must not be claimed yet

Do not claim:

- full GraphRAG or ChromaDB-backed retrieval;
- full candidate deployment campaign acceptance;
- full live test execution;
- full campaign acceptance from one business action receipt;
- production release readiness.

## Current main blocker

The three-field LLM-healed Workbench business action is now proven live, but the full signed
campaign gate set is still incomplete. The latest proof simulates stale primary locators by
forcing the LLM path for selected fields; it does not yet prove a deployed Salesforce metadata/UI
mutation caused the locator drift.

Next focused work:

1. run the same business-action path after an intentional deployed UI/metadata mutation;
2. add the receipt to the live gate matrix;
3. run persona/governance evidence with the Regional VP path;
4. preserve `releaseEligible=false` until signed live receipts satisfy the configured gates.

## Demo positioning

The strongest message is not “everything is green.” The strongest message is:

> Neo turns a Salesforce change into graph-grounded impact, LLM advisory, test obligations, safe
> browser healing and live evidence, then refuses to overclaim when a required gate is missing.
