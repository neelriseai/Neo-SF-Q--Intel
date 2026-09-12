# G2 return

```json
{"files":[{"path":"packages/browser/tests/browser-worker.spec.ts","action":"appended","lines":117}],"tests":{"added":4,"passed":24,"failed":0},"redRunConfirmed":true,"livenessAssertionsAdded":true,"deterministicResolveProven":true,"defectsFound":[],"deviations":[{"from":"NO_OVERBUILD","why":"added one 9-line local helper runProbeOn(html,canary,captureCandidates) because the existing runProbe() hardcodes PROBE_HTML and the file is append-only, so rows 2-4 could not reuse it; no page-object layer, plain HTML strings only"}]}
```

## Red run evidence

Two assertions in the owned file were temporarily inverted and the suite re-run;
both failed, then were reverted and the suite re-run green (24/24).

- `expect(observation.domEvidence).toBeDefined()` on the resolvable page with
  `captureCandidates=true` -> failed (received `undefined`), proving the gate
  assertion discriminates rather than passing vacuously.
- `expect(observation.outcome).toBe("LOCATOR_NOT_FOUND")` on the resolvable page
  -> failed with `Received: "PASSED"`, proving the deterministic tier genuinely
  resolved the element against a real Playwright page.
