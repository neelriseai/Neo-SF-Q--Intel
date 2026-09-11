import { createHash, randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import type {
  BrowserWorkerReceipt,
  CandidateLifecycleState,
  EphemeralSessionHandoff,
  TrustedEnrollmentHandle,
} from "../src/browser-worker.js";
import {
  liveCandidateReadbackProjection,
} from "../src/live-candidate-readback-cli.js";
import {
  BrowserCoordinatorError,
  ReadOnlyBrowserSessionCoordinator,
  loadTrustedLiveBrowserProfile,
  type TrustedLiveBrowserProfile,
} from "../src/salesforce-browser-coordinator.js";
import { liveSmokeProjection, runLiveSmokeCli } from "../src/live-smoke-cli.js";
import { liveBusinessActionProjection } from "../src/live-business-action-cli.js";

const FRONTDOOR_ORIGIN = "https://example--qa.my.salesforce.com";
const LIGHTNING_ORIGIN = "https://example--qa.lightning.force.com";

function digest(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function profileDocument(): Record<string, unknown> {
  const orgBinding = digest("salesforce-org:00D000000000001AAA");
  const actorBinding = digest("salesforce-username:admin@example.invalid");
  const enrollment = {
    enrollmentId: "fixture-enrollment",
    issuer: "fixture-host-issuer",
    classification: "NON_PRODUCTION",
    frontdoorOrigin: FRONTDOOR_ORIGIN,
    lightningOrigin: LIGHTNING_ORIGIN,
    navigationBridgeOrigins: [],
    resourceOrigins: [],
    orgBinding,
    actorBinding,
    policyDigest: digest("fixture-browser-policy"),
    issuedAt: new Date(Date.now() - 1_000).toISOString(),
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
    permittedModes: ["READ_ONLY_DOM_CAPTURE"],
  };
  return {
    schemaVersion: "1.0.0",
    profileId: "fixture-live-browser",
    profileVersion: "1.0.0",
    enrollment,
    sessionBroker: {
      targetOrgAlias: "host-alias",
      frontdoorOrigin: FRONTDOOR_ORIGIN,
      lightningOrigin: LIGHTNING_ORIGIN,
      navigationBridgeOrigins: [],
      resourceOrigins: [],
      orgBinding,
      actorBinding,
      actorBindingSource: "USERNAME",
      salesforceExecutable: "sf-fixture",
      allowedFrontdoorHostnameSuffixes: [".my.salesforce.com"],
      allowedLightningHostnameSuffixes: [".lightning.force.com"],
      maximumOutputBytes: 4096,
      timeoutMs: 5000,
    },
    browser: {
      headless: true,
      launchTimeoutMs: 10000,
      navigationTimeoutMs: 10000,
      operationTimeoutMs: 5000,
    },
    execution: {
      mode: "READ_ONLY_DOM_CAPTURE",
      captureLimit: 20,
      mutationActionsEnabled: false,
    },
  };
}

function encodedProfile(document = profileDocument()): Uint8Array {
  return Buffer.from(JSON.stringify(document), "utf8");
}

function profile(): TrustedLiveBrowserProfile {
  const raw = encodedProfile();
  return loadTrustedLiveBrowserProfile(raw, digest(raw));
}

function privateHandle(kind: "TRUSTED_ENROLLMENT_HANDLE" | "EPHEMERAL_SESSION_HANDOFF") {
  return Object.freeze(
    Object.defineProperty({}, "kind", { value: kind, enumerable: false }),
  );
}

function receipt(canary = ""): BrowserWorkerReceipt {
  return {
    schemaVersion: "1.0.0",
    executionId: "execution-001",
    inputDigest: digest("input"),
    capabilityId: "automation.browser-worker",
    status: "PASSED",
    mode: "READ_ONLY_DOM_CAPTURE",
    enrollment: {
      enrollmentIdDigest: digest("enrollment"),
      issuerDigest: digest("issuer"),
      originDigest: digest("origins"),
      orgBindingDigest: digest("org"),
      actorBindingDigest: digest("actor"),
      policyDigest: digest("policy"),
    },
    lifecycle: ["CAPTURED"],
    capture: [
      {
        ordinal: 0,
        tag: "button",
        role: "button",
        accessibleName: `private-dom-${canary}`,
        visible: true,
        enabled: true,
      },
    ],
    candidateCount: 1,
    cleanup: { contextClosed: true, browserClosed: true },
  };
}

test("loads only an exactly digest-pinned host profile with coherent authority", () => {
  const raw = encodedProfile();
  const loaded = loadTrustedLiveBrowserProfile(raw, digest(raw));

  expect(loaded.execution).toEqual({
    mode: "READ_ONLY_DOM_CAPTURE",
    captureLimit: 20,
    mutationActionsEnabled: false,
  });
  expect(loaded.enrollment.frontdoorOrigin).toBe(FRONTDOOR_ORIGIN);
  expect(loaded.enrollment.lightningOrigin).toBe(LIGHTNING_ORIGIN);
  expect(() => loadTrustedLiveBrowserProfile(raw, "0".repeat(64))).toThrow(
    expect.objectContaining({ code: "PROFILE_NOT_TRUSTED" }),
  );

  const mutable = profileDocument();
  (mutable.execution as Record<string, unknown>).mutationActionsEnabled = true;
  const mutationRaw = encodedProfile(mutable);
  expect(() => loadTrustedLiveBrowserProfile(mutationRaw, digest(mutationRaw))).toThrow(
    expect.objectContaining({ code: "PROFILE_BINDING_MISMATCH" }),
  );
});

test("rejects duplicate and unknown profile fields", () => {
  const duplicate = Buffer.from(
    '{"schemaVersion":"1.0.0","schemaVersion":"1.0.0"}',
  );
  expect(() => loadTrustedLiveBrowserProfile(duplicate, digest(duplicate))).toThrow(
    expect.objectContaining({ code: "PROFILE_INVALID" }),
  );
  const document = profileDocument();
  document.callerAlias = "not-allowed";
  const unknown = encodedProfile(document);
  expect(() => loadTrustedLiveBrowserProfile(unknown, digest(unknown))).toThrow(
    expect.objectContaining({ code: "PROFILE_INVALID" }),
  );
});

test("coordinator passes only opaque handles and a fixed read-only request", async () => {
  const loaded = profile();
  const enrollment = privateHandle(
    "TRUSTED_ENROLLMENT_HANDLE",
  ) as TrustedEnrollmentHandle;
  const handoff = privateHandle("EPHEMERAL_SESSION_HANDOFF") as EphemeralSessionHandoff;
  const calls: string[] = [];
  const expected = receipt();
  const coordinator = new ReadOnlyBrowserSessionCoordinator(
    loaded,
    {
      enroll: async (assertion) => {
        calls.push(`enroll:${assertion.enrollmentId}`);
        return enrollment;
      },
      execute: async (request) => {
        calls.push(`execute:${request.mode}:${request.captureLimit}`);
        expect(request.handoff).toBe(handoff);
        expect(request.candidate).toBeUndefined();
        return expected;
      },
    },
    {
      acquire: async (value) => {
        calls.push("acquire");
        expect(value).toBe(enrollment);
        return handoff;
      },
    },
  );

  expect(await coordinator.run()).toBe(expected);
  expect(calls).toEqual(["enroll:fixture-enrollment", "acquire", "execute:READ_ONLY_DOM_CAPTURE:20"]);
  expect(JSON.stringify(enrollment)).toBe("{}");
  expect(JSON.stringify(handoff)).toBe("{}");
});

test("live smoke projection omits DOM, session material, and raw execution identity", () => {
  const canary = randomBytes(12).toString("hex");
  const projection = liveSmokeProjection(receipt(canary));
  const serialized = JSON.stringify(projection);

  expect(serialized).not.toContain(canary);
  expect(serialized).not.toContain("private-dom");
  expect(serialized).not.toContain("execution-001");
  expect(projection).toMatchObject({
    diagnosticOnly: true,
    releaseEligible: false,
    evidencePhase: null,
    status: "PASSED",
    capabilityId: "automation.browser-worker",
    cleanup: { contextClosed: true, browserClosed: true },
  });
});

test("candidate readback projection carries retry context without raw path or marker", () => {
  const canary = randomBytes(12).toString("hex");
  const path = "/lightning/app/Private_App/n/Private_Tab";
  const marker = `candidate-${canary}`;
  const first = {
    ...receipt(canary),
    status: "BLOCKED" as const,
    mode: "CANDIDATE_READBACK" as const,
    lifecycle: ["CAPTURED", "CANDIDATE_NOT_FOUND"] satisfies CandidateLifecycleState[],
    candidateCount: 0,
    error: { class: "POLICY_BLOCKED" as const, code: "CANDIDATE_NOT_FOUND" },
  };
  const second = {
    ...receipt(canary),
    mode: "CANDIDATE_READBACK" as const,
    lifecycle: [
      "CAPTURED",
      "CANDIDATE_DISCOVERED",
      "READBACK_VERIFIED",
    ] satisfies CandidateLifecycleState[],
    readbackMatched: true,
  };

  const projection = liveCandidateReadbackProjection([first, second], {
    schemaVersion: "1.0.0",
    intentKind: "HOST_ATTRIBUTE_READBACK",
    strategy: "BOUNDED_HOST_ATTRIBUTE_READBACK",
    retryPolicy: "MAX_THREE_ATTEMPTS_SAME_INTENT",
    startPathDigest: digest(path),
    hostTag: "lightning-button",
    hostAttribute: "data-action",
    expectedValueDigest: digest(marker),
    expectedPostcondition: "EXACTLY_ONE_HOST_ATTRIBUTE_MATCH",
    maximumAttempts: 3,
  });
  const serialized = JSON.stringify(projection);

  expect(projection).toMatchObject({
    status: "PASSED",
    attemptCount: 2,
    readbackMatched: true,
    stepContext: {
      intentKind: "HOST_ATTRIBUTE_READBACK",
      maximumAttempts: 3,
    },
  });
  expect(serialized).not.toContain(path);
  expect(serialized).not.toContain(marker);
  expect(serialized).not.toContain(canary);
});

test("business-action projection binds values by digest without leaking form input", () => {
  const canary = randomBytes(12).toString("hex");
  const actionReceipt = {
    ...receipt(canary),
    status: "PASSED" as const,
    mode: "BUSINESS_ACTION" as const,
    lifecycle: [
      "CAPTURED",
      "CANDIDATE_DISCOVERED",
      "READBACK_VERIFIED",
    ] satisfies CandidateLifecycleState[],
    candidateCount: 1,
    businessAction: {
      fieldCount: 1,
      submitted: true,
      successTextMatched: true,
    },
  };
  const fieldValue = `SYN-${canary}`;
  const successText = `Saved successfully ${canary}`;

  const request = {
    startPath: "/lightning/n/Strategic_Deal_Workbench",
    businessAction: {
      fields: [{ fieldApiName: "Name", value: fieldValue }],
      submit: {
        tag: "lightning-button" as const,
        attribute: "data-action" as const,
        expectedValue: "save-evaluate-live",
      },
      successText,
    },
    persistence: {
      objectApiName: "Opportunity",
      matchField: "Name",
      matchValue: fieldValue,
      assertions: [{ fieldApiName: "Name", value: fieldValue }],
    },
  };
  const projection = liveBusinessActionProjection(actionReceipt, request, {
    matched: true,
    objectApiName: "Opportunity",
    matchField: "Name",
    matchValueDigest: digest(fieldValue),
    assertedFields: [{ fieldApiName: "Name", valueDigest: digest(fieldValue), matched: true }],
  });
  const serialized = JSON.stringify(projection);

  expect(projection).toMatchObject({
    status: "PASSED",
    evidencePhase: "LIVE_BUSINESS_ACTION_BROWSER_ACCEPTANCE",
    acceptanceCredit: false,
    releaseEligible: false,
    browserStatus: "PASSED",
    fieldCount: 1,
    businessAction: {
      fieldCount: 1,
      submitted: true,
      successTextMatched: true,
    },
    persistence: {
      matched: true,
      objectApiName: "Opportunity",
      matchField: "Name",
      matchValueDigest: digest(fieldValue),
    },
  });
  expect(serialized).toContain(digest(fieldValue));
  expect(serialized).toContain(digest(successText));
  expect(serialized).not.toContain(fieldValue);
  expect(serialized).not.toContain(successText);
  expect(serialized).not.toContain(canary);
});

test("CLI refuses caller alias or selector arguments without starting live work", async () => {
  const outputs: string[] = [];
  const exitCode = await runLiveSmokeCli(
    ["--target-org", "caller-alias", "--selector", "button"],
    {},
    (value) => outputs.push(value),
  );

  expect(exitCode).toBe(2);
  expect(outputs).toHaveLength(1);
  expect(outputs[0]).toContain("CLI_ARGUMENTS_INVALID");
  expect(outputs[0]).not.toContain("caller-alias");
  expect(outputs[0]).not.toContain("button");
});
