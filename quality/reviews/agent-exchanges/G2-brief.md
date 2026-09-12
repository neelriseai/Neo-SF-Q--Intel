# G2 brief (verbatim)

```
[REPO] C:\Users\neela\Documents\ChatGPT\Neo SF Q- Intel  [CHUNK] G2  [TIMEBOX] 25m
[ROLE] opus developer. TEST CODE ONLY. Do NOT change src/. If a test proves a real defect, STOP and
       report it in the return JSON — do not fix it silently.
[RULES] CRLF forbidden, normalise \r\n -> \n on every write. No git add/commit/push.
[FILES_OWNED] packages/browser/tests/browser-worker.spec.ts (extend, append only)
       Do NOT touch healing-probe.spec.ts, live-healing-cli.spec.ts, or any src/ file.
       Another agent owns tests/**.py — stay out of the Python tree entirely.

[WHY] REQ-HEAL-14 (llm tier gated: llm fires ONLY on deterministic abstention) is currently proven
 only at the CLI projection level with stubbed reports. Prove it against a REAL Playwright page.

[EXISTING_HARNESS] packages/browser/tests/browser-worker.spec.ts already contains, near the end:
  makeWorker(html, overrides)  · handoff(worker, canary, enrollmentOverrides) · expectNoLeak(receipt, canary)
  PROBE_HTML · probeTarget() · runProbe(canary, captureCandidates?)
  const RECORD_ID / PERSON_NAME / RECORD_ID_DIGEST
 REUSE these. Do not duplicate them.
 Runner: @playwright/test.  Verify with:
   cd packages/browser; npx playwright test tests/browser-worker.spec.ts --config=playwright.worker.config.ts
 Typecheck with: npm run lint

[CONTRACT_FACTS] (do not re-derive)
 healing-probe.ts:144  capture happens only when: captureCandidates === true AND deterministicTierAbstained(outcome)
 deterministicTierAbstained(outcome) is true for: LOCATOR_NOT_FOUND, CANDIDATE_AMBIGUOUS
 probeTarget() obligations MUST carry assertions exactly ["EDITABLE","ENABLED","VISIBLE"] for a FIELD
   identity — a shorter set throws PROBE_OBLIGATION_INVALID and the receipt becomes
   {class:"CAPTURE_FAILED", code:"BROWSER_OPERATION_FAILED"} with probeReport undefined.

[STATE_TRANSITIONS] write one test per row.
 1 State: page WITHOUT the declared element · captureCandidates=true
   -> Expected: outcome LOCATOR_NOT_FOUND · domEvidence PRESENT
 2 State: page WHERE the deterministic tier RESOLVES the element (element carries
     data-object-api="Opportunity" + data-field-api matching the obligation so metadata identity
     discovery succeeds) · captureCandidates=true
   -> Expected: outcome PASSED · domEvidence ABSENT
   THIS IS THE CORE OF REQ-HEAL-14: the llm tier must not fire when the deterministic tier succeeded.
 3 State: same resolvable page · captureCandidates=false
   -> Expected: outcome PASSED · domEvidence ABSENT
 4 State: page with TWO elements matching the same semantic identity · captureCandidates=true
   -> Expected: outcome CANDIDATE_AMBIGUOUS · domEvidence PRESENT

[ANTI_VACUOUS] MANDATORY. Before asserting domEvidence is absent, FIRST assert the probe actually
 ran: receipt.error must be undefined AND receipt.probeReport must be defined AND
 probeReport.observations.length === 1. A previous version of these tests passed vacuously because
 probeReport was undefined, so "withheld evidence" and "never ran" were indistinguishable. Do not
 repeat that. Every negative assertion must be preceded by a positive liveness assertion.

[NO_OVERBUILD] no new abstractions, no page-object layer. Plain HTML strings + the existing helpers.
[SANITY] keep expectNoLeak(receipt, canary) on at least one test.

[RETENTION] write quality/reviews/agent-exchanges/G2-brief.md (this brief verbatim, fenced) and
 quality/reviews/agent-exchanges/G2-return.md (your return JSON, fenced). Create dir if absent. LF only.

[RETURN] minified JSON only:
 {"files":[{"path","action","lines"}],"tests":{"added":N,"passed":N,"failed":N},
  "redRunConfirmed":bool,"livenessAssertionsAdded":bool,
  "deterministicResolveProven":bool,
  "defectsFound":[{"sev","desc","evidence"}],"deviations":[{"from","why"}]}
```
