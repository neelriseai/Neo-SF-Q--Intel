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
