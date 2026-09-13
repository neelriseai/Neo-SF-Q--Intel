import { expect, test } from "@playwright/test";
import type { BrowserWorkerReceipt } from "../src/browser-worker.js";
import type { DomEvidence, LocatorProbeObservation } from "../src/healing-probe.js";
import { healingProjection } from "../src/live-healing-cli.js";

const evidence: DomEvidence = {
  capturedForOutcome: "LOCATOR_NOT_FOUND",
  candidateCount: 2,
  truncated: false,
  candidates: [
    {
      ordinal: 0,
      tag: "input",
      role: "combobox",
      structure: "lightning-input-field>input[role=combobox]",
      attrNames: ["data-record-id", "role"],
      attrHashes: { "data-record-id": "0123456789abcdef", role: "fedcba9876543210" },
      nameDigest: "00112233445566aa",
      nearby: ["lightning-input-field", "button"],
      visible: true,
      enabled: true,
    },
  ],
};

const observation = (extra: Partial<LocatorProbeObservation>): LocatorProbeObservation => ({
  obligationIdDigest: "a".repeat(64),
  originalLocatorDigest: "b".repeat(64),
  semanticIdentityDigest: "c".repeat(64),
  outcome: "LOCATOR_NOT_FOUND",
  staleOriginal: true,
  candidateCount: 0,
  ...extra,
});

const receipt = (observations: readonly LocatorProbeObservation[]): BrowserWorkerReceipt =>
  ({
    status: "FAILED",
    executionId: "execution-1",
    inputDigest: "d".repeat(64),
    cleanup: { contextClosed: true, browserClosed: true },
    probeReport: {
      schemaVersion: "1.0.0",
      capabilityId: "automation.locator-healing-probe",
      evidenceClass: "HEADLESS_READ_ONLY_PROBE_NOT_ACCEPTANCE_RECEIPT",
      acceptanceCredit: false,
      releaseEligible: false,
      stage: "STALE_AND_DISCOVER",
      targetDigest: "e".repeat(64),
      status: "FAILED",
      obligationCount: observations.length,
      observations,
    },
  }) as unknown as BrowserWorkerReceipt;

test("projects dom evidence through when the probe captured it", () => {
  const projection = healingProjection(
    receipt([observation({ domEvidence: evidence })]),
    "STALE_AND_DISCOVER",
    ["metadata", "llm"],
    false,
  );
  const projected = (projection.observations as readonly Record<string, unknown>[])[0];
  expect(projected.domEvidence).toEqual(evidence);
  expect(projection.acceptanceCredit).toBe(false);
  expect(projection.releaseEligible).toBe(false);
  expect(projection.diagnosticOnly).toBe(true);
  expect(projection.modelDiscoveryRequested).toBe(true);
});

test("projects model locator proposal when the llm bridge returned one", () => {
  const proposal = {
    schemaVersion: "1.0.0",
    accepted: true,
    proposal: {
      candidateOrdinal: 0,
      confidenceMilli: 820,
      rationale: "Digest overlap selected candidate zero.",
      citedRefs: ["cand:0"],
    },
    receipt: {
      status: "SUCCESS",
      providerProfileSha256: "f".repeat(64),
      promptSha256: "0".repeat(64),
    },
  };
  const projection = healingProjection(
    receipt([observation({ domEvidence: evidence })]),
    "STALE_AND_DISCOVER",
    ["metadata", "llm"],
    true,
    [proposal],
  );
  expect(projection.modelDiscoveryAvailable).toBe(true);
  expect(projection.headedMode).toBe(true);
  const projected = (projection.observations as readonly Record<string, unknown>[])[0];
  expect(projected.modelProposal).toEqual(proposal);
});

test("projects model locator proposal for ambiguous deterministic abstention", () => {
  const proposal = {
    schemaVersion: "1.0.0",
    accepted: true,
    proposal: {
      candidateOrdinal: 1,
      confidenceMilli: 760,
      rationale: "Ambiguous deterministic candidates required the model proposal.",
      citedRefs: ["cand:1"],
    },
  };
  const projection = healingProjection(
    receipt([
      observation({
        outcome: "CANDIDATE_AMBIGUOUS",
        candidateCount: 2,
        domEvidence: { ...evidence, capturedForOutcome: "CANDIDATE_AMBIGUOUS" },
      }),
    ]),
    "STALE_AND_DISCOVER",
    ["metadata", "llm"],
    false,
    [proposal],
  );
  expect(projection.modelDiscoveryAvailable).toBe(true);
  const projected = (projection.observations as readonly Record<string, unknown>[])[0];
  expect(projected.modelProposal).toEqual(proposal);
});

test("does not attach model proposal to deterministic metadata recovery", () => {
  const proposal = {
    schemaVersion: "1.0.0",
    accepted: true,
    proposal: {
      candidateOrdinal: 0,
      confidenceMilli: 950,
      rationale: "Would be unsafe if attached to a deterministic pass.",
      citedRefs: ["cand:0"],
    },
  };
  const projection = healingProjection(
    receipt([
      observation({
        outcome: "PASSED",
        staleOriginal: true,
        candidateCount: 1,
        visible: true,
        enabled: true,
        editable: true,
      }),
    ]),
    "STALE_AND_DISCOVER",
    ["metadata", "llm"],
    false,
    [proposal],
  );
  expect(projection.modelDiscoveryAvailable).toBe(false);
  const projected = (projection.observations as readonly Record<string, unknown>[])[0];
  expect(projected.resolvedByTier).toBe("metadata");
  expect(projected.modelProposal).toBeUndefined();
  expect(projected.domEvidence).toBeUndefined();
});

test("does not attach model proposal when non-abstention observation carries dom evidence", () => {
  const proposal = {
    schemaVersion: "1.0.0",
    accepted: true,
    proposal: {
      candidateOrdinal: 0,
      confidenceMilli: 900,
      rationale: "A non-abstention outcome must not consume this.",
      citedRefs: ["cand:0"],
    },
  };
  const projection = healingProjection(
    receipt([
      observation({
        outcome: "ASSERTION_MISMATCH",
        candidateCount: 1,
        domEvidence: { ...evidence, capturedForOutcome: "ASSERTION_MISMATCH" },
      }),
    ]),
    "STALE_AND_DISCOVER",
    ["metadata", "llm"],
    false,
    [proposal],
  );
  expect(projection.modelDiscoveryAvailable).toBe(false);
  const projected = (projection.observations as readonly Record<string, unknown>[])[0];
  expect(projected.domEvidence).toBeDefined();
  expect(projected.modelProposal).toBeUndefined();
});

test("leaves the metadata-tier projection shape unchanged", () => {
  const projection = healingProjection(
    receipt([observation({})]),
    "STALE_AND_DISCOVER",
    ["metadata"],
    false,
  );
  const projected = (projection.observations as readonly Record<string, unknown>[])[0];
  expect(Object.keys(projected)).toEqual([
    "obligationIdDigest",
    "outcome",
    "staleOriginal",
    "candidateCount",
    "resolvedByTier",
    "visible",
    "enabled",
    "editable",
  ]);
  expect(projection.acceptanceCredit).toBe(false);
  expect(projection.releaseEligible).toBe(false);
  expect(projection.diagnosticOnly).toBe(true);
  expect(projection.modelDiscoveryRequested).toBe(false);
});
