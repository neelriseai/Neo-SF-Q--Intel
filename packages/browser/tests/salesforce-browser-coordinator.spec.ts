import { createHash, randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import type {
  BrowserWorkerReceipt,
  EphemeralSessionHandoff,
  TrustedEnrollmentHandle,
} from "../src/browser-worker.js";
import {
  BrowserCoordinatorError,
  ReadOnlyBrowserSessionCoordinator,
  loadTrustedLiveBrowserProfile,
  type TrustedLiveBrowserProfile,
} from "../src/salesforce-browser-coordinator.js";
import { liveSmokeProjection, runLiveSmokeCli } from "../src/live-smoke-cli.js";

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

test("loads only an exactly digest-pinned, read-only host profile", () => {
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
    expect.objectContaining({ code: "PROFILE_INVALID" }),
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
