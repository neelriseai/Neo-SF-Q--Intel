# Demonstration scenarios

> **Development stopped 2026-09-11.** This runbook remains non-authorizing and `NOT_READY`.
> [The development stop checkpoint](21-development-stop-checkpoint-2026-09-11.md) records the open
> browser-schema, Apex-test-inventory, host-factory and production-dispatch blockers.

## Execution status

All scenarios below are specifications until one current source-bound receipt proves the complete
path. Offline unit/contract tests and mocked dashboard browser tests exist. Neo has not yet produced
a live Salesforce, live provider, live PostgreSQL, trusted test-runner or real Salesforce browser
receipt. Historical AUT deployment/API evidence is useful input but is not Neo runtime evidence.

All live scenario claims must satisfy the corresponding gates in
`config/live-salesforce-acceptance-profile.json`. Fixtures, mocks and injected outages test the
contract but never satisfy a positive live gate. Every live assertion must say whether it concerns
`LIVE_BASELINE`, `CANDIDATE_CHECK_ONLY`, `DEPLOYED_CANDIDATE` or `RESTORED_BASELINE`. If local
candidate analysis is paired only with baseline evidence, the dashboard must say **candidate not
deployed or validated live**.

## Master unified demo

Use [Strategic Deal Change Assurance Demo](24-strategic-deal-change-assurance-demo.md) as the
current master narrative when one scenario should showcase graph impact, evidence graph,
graph-grounded advisory, candidate assurance, test selection, governance, live Salesforce evidence,
browser worker, locator healing, ChromaDB roadmap posture, candidate deployment boundaries and live
test-execution gaps together. The master demo does not upgrade any scenario below from
specification to accepted live evidence; it only composes them into one truthful presentation flow.

## Scenario A — business-rule change

Input a source/requirement change to a policy threshold. The current foundation can show pinned,
normalized contract/analysis-graph ingestion, graph-supported impact, selected validations,
evidence gaps and a deterministic posture. It does not yet consume release-trusted current path
receipts. The completed scenario requires live source-contract custom API assertions, scoped
Metadata API retrieval, selected live Apex tests and post-deploy repeats bound to the exact
candidate. Policy values must come from the selected source contract, never platform literals.

## Scenario B — permission change (target vertical)

The current foundation can graph a declared permission-related source change and preserve mandatory
test obligations when those facts exist in the source contract. The complete target adds live
positive/negative persona authorization evidence, transport-versus-business-authority policy and a tested
identity rule preventing administrator activity from being represented as VP approval.

## Scenario C — UI presentation change

Analyze a presentation change and show an evidence-bound healing strategy. The complete demo
requires a classified live org, ephemeral in-memory Playwright session handoff, verified Lightning
origin/persona, current DOM capture, real candidate ranking, ambiguity abstention, explicit approval,
permitted application and live readback with before/after evidence. Only the final verified outcome
may be called healed; until then it is a strategy proposal.

## Scenario D — governed degradation (partial now)

The current workflow demonstrates `INCOMPLETE` for missing, stale, expired, contradictory or
unverified in-run evidence, and an explicit `INCOMPLETE` decision plus sanitized trace when a stage
fails. Loading an untrusted source fails closed before a run. Model-provider and live-Salesforce
outage receipts become part of the scorecard only after those dependencies participate in the
assurance workflow; the demo must not simulate that evidence meanwhile.
Injected failures and real dependency failures are reported separately. An unknown external job
state, failed restoration or unreconciled residue blocks candidate-campaign acceptance.

## Current two-hour operator demo slice — ready as a truthful sequence

This is the current demo slice selected on 2026-09-11. It is intentionally narrower than the full
live Salesforce campaign. It proves that Neo can use a real LLM over a real local Salesforce-app
candidate, and it separately proves live Salesforce connectivity plus a headless browser diagnostic.
It does **not** claim that the local candidate was deployed to Salesforce, restored, reconciled, or
accepted by all live campaign gates.

Run and present the slice in this order:

1. **Real LLM candidate advisory.** Use the no-scope candidate advisory endpoint or service call
   against the configured local Salesforce app repository. The expected demo evidence is:
   `candidate_available=true`, at least one analysis, at least one successful specialist capture,
   a bounded `view_sha256`, and explicit release-blocking gap codes. This is the agentic reasoning
   proof: the advisory is produced by the configured model provider over graph/source-derived
   candidate context, not by a canned fixture.
2. **Live Salesforce read proof.** Use the host-owned Salesforce CLI/API read path only. Evidence
   must be sanitized: connected/non-connected status, target classification, API/custom REST status,
   digest and gap code are acceptable; aliases, usernames, record IDs, raw org payloads, paths,
   session URLs and tokens are not.
3. **Live headless browser diagnostic.** From `packages/browser`, run the profile refresh and live
   smoke only when the machine-local Salesforce CLI identity is already authorized:
   `npm run live:profile` then `npm run live:smoke`. The expected result remains diagnostic-only:
   `status=PASSED`, `diagnosticOnly=true`, `releaseEligible=false`, cleanup closed. It is not a
   healed locator or deployed-candidate acceptance receipt.
4. **Optional composition view.** `POST /api/v1/demo/live-operator-advisory` may be shown only if
   it returns both live diagnostic pass and model-backed candidate advisory. If it returns
   `CANDIDATE_ADVISORY_UNAVAILABLE`, `LIVE_DIAGNOSTIC_NOT_CONFIGURED`, or another gap, present that
   as a truthful blocker instead of downgrading the guardrail.

2026-09-11 update: the optional composition path now binds the sanitized live Salesforce read
summary into the same specialist `GraphContextPack` as the candidate advisory when a governed
completed live baseline is available. This is still diagnostic/advisory evidence only. The live
fragment is non-authorizing, digest-addressed, source-bound and explicitly blocked from satisfying
deploy, browser, restore, mutation or release gates.

Current non-negotiable claim boundary:

- Local candidate + real LLM = demoable agentic advisory.
- Live Salesforce read/browser diagnostic = demoable live connectivity.
- Local candidate + live deployed Salesforce behavior = not yet claimed.
- Full live campaign acceptance = deferred until explicit mutation authority, deploy/check-only,
  restored baseline and signed receipt gates exist.

Current 2026-09-11 recheck receipts for this slice:

- Real LLM candidate advisory passed with two analyses, six specialist captures, 15 blocking gaps
  and `releaseEligible=false`.
- Live Salesforce read-only proof passed through the configured CLI/API route and printed only
  bounded status/digest evidence.
- Live headless browser diagnostic passed after binding the generated profile path and digest
  through environment variables; it remained `diagnosticOnly=true` and `releaseEligible=false`.
- The first browser-smoke attempt blocked as `PROFILE_SOURCE_INVALID` because the generated profile
  path and digest were not exported. This is an operator sequencing issue, not a product claim.
- The scoped LWC candidate was later deployed under operation `restore-20260911T155750Z-1e31703f`,
  read back live through headless Playwright as one matching `save-evaluate-live` host action marker,
  then restored and reconciled to the archived preimage SHA-256. This is deployed-candidate readback
  evidence for the marker only, not complete full-campaign acceptance.
- The live campaign status endpoint now shows the complete 15-gate matrix with gate kind, receipt
  type, accepted evidence phase, locally valid count and missing gate IDs. Use it to explain which
  API, metadata, browser, test, candidate-deploy and restore gates are still missing. Do not present
  locally replay-valid gates as accepted completion; `releaseEligible=false` remains the boundary
  until every required signed receipt validates from the durable ledger.

## Operator runbook — live locator-healing demo (NOT_READY)

This is the safe operator sequence for a future source-bound demo. It documents the existing
interfaces and evidence boundary; it is not an instruction to mutate an org. The production
authority, browser dispatch/observation producer, and durable acceptance-receipt bridge are not
implemented, so the run must stop at the first unavailable step and remains `FOUNDATION` with zero
acceptance credit. Never substitute a fixture, historical screenshot, open browser tab, or the
diagnostic smoke for a live receipt.

Prerequisites are: the configured non-production alias and actor are enrolled by the host; the
exact local Git candidate tree (including intended uncommitted changes) is captured and pinned—no
remote repository is required; the live acceptance profile and execution contract are current;
the metadata/data restore rehearsal is current; a private task-scoped authority and recovery lease
exist; and no other campaign owns the change window. Do not paste an alias, path, route, locator,
session URL, record ID, or raw org payload into a caller request.

Follow this exact sequence, recording only sanitized, digest-bound evidence:

1. Run the repository preflight and stop on any policy, source, environment, or secret-safety
   failure.
2. Use the host-only `confirm_classification_only` flow with its literal operator confirmation.
   Reconcile the fixed display, Organization, userinfo subject, and active User twice. This grants
   no metadata, browser, test, or mutation authority.
3. Run the no-argument host source/compiler composition. It must derive the complete target and
   obligation inventory from the pinned source/graph/policy roots; caller-selected subsets are
   invalid.
4. Capture and authenticate the exact metadata preimage and residue/recovery roots into ignored
   runtime storage. Preserve original property absence when the declaration permits `ABSENT`.
5. Run complete candidate check-only validation for the exact isolated package and test inventory.
   A partial check-only result is a stop condition.
6. **Deploy is currently a placeholder.** Do not invoke a Salesforce deploy: the independently
   verified production mutation authority, durable dispatch fencing and live transport identity
   are not available in this workspace.
7. After a future authorized deployment, run the target's exact nine read-only UI obligations
   (eight field identities plus the save-action visibility/enabled probe) and record the applicable
   `SF-L01`–`SF-L09` and `SF-C01`–`SF-C06` campaign gates separately. Campaign gates cover
   classification/identity, source/API contract, metadata, browser session/origin, selected tests,
   candidate reconciliation, UI assertions, healing readback and restoration/reconciliation; they
   are not interchangeable with the nine UI obligations. The exact operation plan and acceptance
   profile determine the concrete requests; no ad-hoc command is defined here.
8. **Heal is currently a placeholder.** The browser worker's `runReadOnlySmoke()` is diagnostic
   capture only and cannot stand in for retained-session all-obligation healing. A future run must
   prove a genuine stale old-locator failure, exactly one fresh semantic candidate, complete
   assertion rerun, changed readback, and independent locator/session cleanup.
9. Restore the journaled exact preimage (including `ABSENT` where applicable), never a guessed
   reverse edit. Then reconcile metadata bytes, component semantics, changed/added/deleted residue,
   locator-map reset, pending rebinds, browser/context closure, and all required post-restore
   assertions. Any unknown job, concurrent change, expiry, non-quiescent process, or failed branch
   leaves the campaign quarantined for manual intervention.

Expected sanitized evidence contains run/trace IDs, source/campaign/plan/policy roots, phase,
operation counts, command/receipt/observation digests, timestamps, durations, typed statuses and
cleanup/reconciliation outcomes. It must contain no DOM, accessible names, selectors, session URLs,
aliases, record IDs, XML, job IDs, paths, credentials, or exception payloads. Interpret
`CANDIDATE_DISCOVERED`, `PROPOSAL_APPROVED`, `ACTION_APPLIED`, and `OUTCOME_VERIFIED` separately;
only the last is a healed outcome, and even that remains non-acceptance evidence until the signed
live receipt bridge exists. On any abort, do not retry an unknown remote job or overwrite unrelated
state; quarantine, preserve the journal, and require the host recovery procedure and fresh
classification before another attempt.

## Viewer narrative

The current dashboard makes the implemented boundary visually explicit: it renders only specialist
activities recorded on the run; source-confirmed, human-recorded, inferred, contradictory and
unresolved evidence remain separate; selected validations remain distinct from execution receipts;
healing stays a strategy proposal unless a browser receipt exists; guardrails and violations remain
distinct from the current effective decision; and historical decisions are audit-only. A completed
run without an effective decision fails closed in the view. The run API currently exposes evidence
citations, not replayable ordered path receipts, and does not yet expose A6 advisory payloads or live
dependency availability. The dashboard therefore labels those boundaries instead of simulating
them. It also makes no persistence-durability claim until `/health` is integrated. ChromaDB is the
sole persistent vector backend in the roadmap.
