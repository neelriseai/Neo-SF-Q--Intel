import { createHash, randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import { chromium, type Browser, type BrowserContextOptions } from "playwright";
import {
  BrowserWorker,
  type BrowserWorkerReceipt,
  type TrustedEnrollmentAssertion,
} from "../src/browser-worker.js";

const NOW = Date.parse("2030-01-01T00:00:00.000Z");
const ORIGIN = "https://example--qa.lightning.force.com";
const POLICY_DIGEST = createHash("sha256").update("browser-policy").digest("hex");

function enrollment(
  overrides: Partial<TrustedEnrollmentAssertion> = {},
): TrustedEnrollmentAssertion {
  return {
    enrollmentId: "enrollment-001",
    issuer: "host-broker",
    classification: "NON_PRODUCTION",
    frontdoorOrigin: ORIGIN,
    lightningOrigin: ORIGIN,
    navigationBridgeOrigins: [],
    resourceOrigins: [],
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
    policyDigest: POLICY_DIGEST,
    issuedAt: new Date(NOW - 1_000).toISOString(),
    expiresAt: new Date(NOW + 60_000).toISOString(),
    permittedModes: ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK"],
    ...overrides,
  };
}

function makeWorker(html: string, overrides: Partial<ConstructorParameters<typeof BrowserWorker>[0]> = {}) {
  return new BrowserWorker({
    verifyEnrollment: async (assertion) => assertion.issuer === "host-broker",
    now: () => NOW,
    offlineDocumentForPath: () => ({ body: html }),
    ...overrides,
  });
}

async function handoff(
  worker: BrowserWorker,
  canary: string,
  overrides: Partial<TrustedEnrollmentAssertion> = {},
) {
  const handle = await worker.enroll(enrollment(overrides));
  const identity = worker.verifySessionIdentity(handle, {
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
  });
  return worker.issueSession(handle, identity, {
    entryUrl: `${ORIGIN}/secur/frontdoor.jsp?sid=${encodeURIComponent(canary)}`,
    frontdoorOrigin: ORIGIN,
    lightningOrigin: ORIGIN,
    navigationBridgeOrigins: [],
    resourceOrigins: [],
  });
}

function expectNoLeak(receipt: BrowserWorkerReceipt, canary: string) {
  const projection = JSON.stringify(receipt);
  expect(projection).not.toContain(canary);
  expect(projection).not.toContain("frontdoor.jsp");
  expect(projection).not.toContain("sid=");
  expect(Object.keys(receipt.enrollment)).not.toContain("lightningOrigin");
}

test("captures bounded, sanitized DOM through a one-shot private handoff", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <button aria-label="Save ${canary}">Save</button>
    <input aria-label="Reference" />
  `);
  const session = await handoff(worker, canary);
  expect(JSON.stringify(session)).toBe("{}");

  const receipt = await worker.execute({
    handoff: session,
    mode: "READ_ONLY_DOM_CAPTURE",
    captureLimit: 10,
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.lifecycle).toEqual(["CAPTURED"]);
  expect(receipt.capture).toHaveLength(2);
  expect(receipt.capture[0].accessibleName).toContain("[REDACTED]");
  expect(receipt.cleanup).toEqual({ contextClosed: true, browserClosed: true });
  expectNoLeak(receipt, canary);

  const replay = await worker.execute({ handoff: session, mode: "READ_ONLY_DOM_CAPTURE" });
  expect(replay.status).toBe("BLOCKED");
  expect(replay.error?.code).toBe("INVALID_OR_CONSUMED_HANDOFF");
});

test("discovers exactly one candidate and verifies readback without applying an action", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <button aria-label="Review" aria-pressed="true">Review</button>
  `);
  const session = await handoff(worker, canary);

  const receipt = await worker.execute({
    handoff: session,
    mode: "CANDIDATE_READBACK",
    candidate: {
      role: "button",
      accessibleName: "Review",
      readback: { attribute: "aria-pressed", expectedValue: "true" },
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.lifecycle).toEqual([
    "CAPTURED",
    "CANDIDATE_DISCOVERED",
    "READBACK_VERIFIED",
  ]);
  expect(receipt.readbackMatched).toBe(true);
  expectNoLeak(receipt, canary);
});

test("verifies deployed candidate action marker as read-only readback", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <button aria-label="Save proposal and check policy" data-action="save-evaluate-live">
      Save proposal and check policy
    </button>
  `);
  const session = await handoff(worker, canary);

  const receipt = await worker.execute({
    handoff: session,
    mode: "CANDIDATE_READBACK",
    candidate: {
      hostAttribute: {
        tag: "button",
        attribute: "data-action",
        expectedValue: "save-evaluate-live",
      },
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.lifecycle).toEqual([
    "CAPTURED",
    "CANDIDATE_DISCOVERED",
    "READBACK_VERIFIED",
  ]);
  expect(receipt.readbackMatched).toBe(true);
  expectNoLeak(receipt, canary);
});

test("executes an explicitly authorized business action and verifies success readback", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <form onsubmit="event.preventDefault(); document.querySelector('[role=status]').textContent='Saved successfully. The policy results are shown below.'">
      <div data-field-api="Name"><input aria-label="Opportunity Name" /></div>
      <div data-field-api="Amount"><input aria-label="Amount" /></div>
      <button data-action="save-evaluate-live">Save and Evaluate</button>
      <p role="status"></p>
    </form>
  `);
  const session = await handoff(worker, canary, {
    permittedModes: ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK", "BUSINESS_ACTION"],
  });

  const receipt = await worker.execute({
    handoff: session,
    mode: "BUSINESS_ACTION",
    businessAction: {
      objectApiName: "Opportunity",
      fields: [
        { fieldApiName: "Name", value: "SYN-Widget Renewal" },
        { fieldApiName: "Amount", value: "50000" },
      ],
      submit: {
        tag: "button",
        attribute: "data-action",
        expectedValue: "save-evaluate-live",
      },
      successText: "Saved successfully. The policy results are shown below.",
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.lifecycle).toEqual(["CAPTURED", "CANDIDATE_DISCOVERED", "READBACK_VERIFIED"]);
  expect(receipt.businessAction).toEqual({
    fieldCount: 2,
    submitted: true,
    successTextMatched: true,
    healedFieldCount: 0,
    abstainedFieldCount: 0,
    modelProposalCount: 0,
    modelAppliedFieldCount: 0,
    modelRejectionCodes: [],
    modelCandidateOrdinals: [],
    modelDomCandidateCounts: [],
    modelAttemptFields: [],
    modelContextPlanCounts: [],
    modelIntentCitedFields: [],
    signatureLookupFoundFields: [],
    signatureSavedFields: [],
    signatureSaveErrorCodes: [],
    strategies: ["direct-data-field-api", "direct-data-field-api"],
  });
  expect(JSON.stringify(receipt)).not.toContain("SYN-Widget Renewal");
  expect(JSON.stringify(receipt)).not.toContain("50000");
  expectNoLeak(receipt, canary);
});

test("blocks business action when the live profile does not explicitly authorize mutation", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <div data-field-api="Name"><input aria-label="Opportunity Name" /></div>
    <button data-action="save-evaluate-live">Save and Evaluate</button>
    <p role="status">Saved successfully. The policy results are shown below.</p>
  `);
  const session = await handoff(worker, canary);

  const receipt = await worker.execute({
    handoff: session,
    mode: "BUSINESS_ACTION",
    businessAction: {
      objectApiName: "Opportunity",
      fields: [{ fieldApiName: "Name", value: "SYN-Unauthorized" }],
      submit: { tag: "button", attribute: "data-action", expectedValue: "save-evaluate-live" },
      successText: "Saved successfully. The policy results are shown below.",
    },
  });

  expect(receipt.status).toBe("BLOCKED");
  expect(receipt.error?.code).toBe("MODE_NOT_AUTHORIZED");
  expect(JSON.stringify(receipt)).not.toContain("SYN-Unauthorized");
  expectNoLeak(receipt, canary);
});

test("self-heals business submit locators through stable action identity before evaluation", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <form data-object-api="Opportunity"
      onsubmit="event.preventDefault(); document.querySelector('[role=status]').textContent='Saved successfully. The policy results are shown below.'">
      <section data-field-api="Name"><label>Opportunity Name<input /></label></section>
      <button data-action="save-evaluate-live">Save and Evaluate</button>
      <p role="status"></p>
    </form>
  `);
  const session = await handoff(worker, canary, {
    permittedModes: ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK", "BUSINESS_ACTION"],
  });

  const receipt = await worker.execute({
    handoff: session,
    mode: "BUSINESS_ACTION",
    businessAction: {
      objectApiName: "Opportunity",
      fields: [{ fieldApiName: "Name", value: "SYN-Healed Renewal" }],
      submit: { tag: "lightning-button", attribute: "data-action", expectedValue: "save-evaluate-live" },
      successText: "Saved successfully. The policy results are shown below.",
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.businessAction).toMatchObject({
    fieldCount: 1,
    submitted: true,
    successTextMatched: true,
    healedFieldCount: 1,
    abstainedFieldCount: 0,
    strategies: ["direct-data-field-api", "stable-action-identity"],
  });
  expect(JSON.stringify(receipt)).not.toContain("SYN-Healed Renewal");
  expectNoLeak(receipt, canary);
});

test("uses a host-owned LLM ordinal proposal when business field locators are stale", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const proposals: unknown[] = [];
  const savedSignatures: unknown[] = [];
  const worker = makeWorker(`
    <form data-object-api="Opportunity"
      onsubmit="event.preventDefault();
        document.querySelector('[role=status]').textContent =
          document.querySelector('input[aria-label=&quot;Opportunity Name&quot;]').value === 'SYN-LLM-Healed'
            ? 'Saved successfully. The policy results are shown below.' : 'Unexpected value';">
      <section data-field-api="Name">
        <label>Opportunity Name<input aria-label="Opportunity Name" /></label>
      </section>
      <button data-action="save-evaluate-live">Save and Evaluate</button>
      <p role="status"></p>
    </form>
  `, {
    forceModelHealingFields: ["Name"],
    signatureScope: { projectId: "neo-sf-q-intel", pageKey: "strategic-deal-workbench" },
    knowledgeIntentFallback: {
      page: "strategic-deal-workbench",
      section: "Field behavior in plain English",
    },
    proposeBusinessLocator: async (payload) => {
      proposals.push(payload);
      return {
        schemaVersion: "1.0.0",
        signatureLookup: { found: true },
        contextPlans: [{ toolCalls: [] }],
        accepted: false,
        rejectionCode: "PROPOSAL_CONFIDENCE_BELOW_FLOOR",
        proposal: {
          candidateOrdinal: 0,
          confidenceMilli: 820,
        },
      };
    },
    recordBusinessSignature: async (payload) => {
      savedSignatures.push(payload);
      return { schemaVersion: "1.0.0", saved: true, status: "SAVED" };
    },
  });
  const session = await handoff(worker, canary, {
    permittedModes: ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK", "BUSINESS_ACTION"],
  });

  const receipt = await worker.execute({
    handoff: session,
    mode: "BUSINESS_ACTION",
    businessAction: {
      objectApiName: "Opportunity",
      fields: [{ fieldApiName: "Name", value: "SYN-LLM-Healed" }],
      submit: { tag: "button", attribute: "data-action", expectedValue: "save-evaluate-live" },
      successText: "Saved successfully. The policy results are shown below.",
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(proposals).toHaveLength(1);
  expect(proposals[0]).toMatchObject({
    schemaVersion: "1.0.0",
    objectApiName: "Opportunity",
    fieldApiName: "Name",
    contextPlanning: true,
    fallbackIntentLookup: {
      page: "strategic-deal-workbench",
      section: "Field behavior in plain English",
    },
    signatureLookup: {
      projectId: "neo-sf-q-intel",
      pageKey: "strategic-deal-workbench",
    },
  });
  expect(JSON.stringify(proposals[0])).not.toContain("intentSection");
  expect(savedSignatures).toHaveLength(1);
  expect(savedSignatures[0]).toMatchObject({
    schemaVersion: "1.0.0",
    operation: "SAVE_SIGNATURE",
    projectId: "neo-sf-q-intel",
    pageKey: "strategic-deal-workbench",
    objectApiName: "Opportunity",
    fieldApiName: "Name",
    capturedAtUtc: "2030-01-01T00:00:00Z",
    candidate: {
      structure: expect.stringContaining("input"),
      attrNames: ["aria-label"],
      nearby: expect.any(Array),
    },
  });
  expect((savedSignatures[0] as { candidate: { attrHashes: Record<string, string> } }).candidate.attrHashes["aria-label"])
    .toMatch(/^[a-f0-9]{16}$/);
  expect(receipt.businessAction).toMatchObject({
    fieldCount: 1,
    submitted: true,
    successTextMatched: true,
    healedFieldCount: 1,
    abstainedFieldCount: 0,
    modelProposalCount: 1,
    modelAppliedFieldCount: 1,
    modelContextPlanCounts: [1],
    signatureLookupFoundFields: ["Name"],
    signatureSavedFields: ["Name"],
  });
  expect(receipt.businessAction?.strategies?.[0]).toContain("llm-ordinal-0");
  expect(JSON.stringify(receipt)).not.toContain("SYN-LLM-Healed");
  expectNoLeak(receipt, canary);
});

test("writes Salesforce lightning-input-field values before business submit", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <form data-object-api="Opportunity"
      onsubmit="event.preventDefault();
        const value = document.querySelector('lightning-input-field').value;
        document.querySelector('[role=status]').textContent =
          value === 'SYN-Lightning Field' ? 'Saved successfully. The policy results are shown below.' : 'Unexpected value';">
      <section data-field-api="Name">
        <lightning-input-field field-name="Name"></lightning-input-field>
      </section>
      <button data-action="save-evaluate-live">Save and Evaluate</button>
      <p role="status"></p>
    </form>
  `);
  const session = await handoff(worker, canary, {
    permittedModes: ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK", "BUSINESS_ACTION"],
  });

  const receipt = await worker.execute({
    handoff: session,
    mode: "BUSINESS_ACTION",
    businessAction: {
      objectApiName: "Opportunity",
      fields: [{ fieldApiName: "Name", value: "SYN-Lightning Field" }],
      submit: { tag: "button", attribute: "data-action", expectedValue: "save-evaluate-live" },
      successText: "Saved successfully. The policy results are shown below.",
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.businessAction).toMatchObject({
    fieldCount: 1,
    submitted: true,
    successTextMatched: true,
    strategies: ["salesforce-lightning-input-field"],
  });
  expect(JSON.stringify(receipt)).not.toContain("SYN-Lightning Field");
  expectNoLeak(receipt, canary);
});

test("navigates to a bounded same-origin start path before candidate readback", async () => {
  const worker = makeWorker(`<button aria-label="Wrong page">Wrong page</button>`, {
    offlineDocumentForPath: (pathname) => ({
      body:
        pathname === "/lightning/n/Strategic_Deal_Workbench"
          ? `<button aria-label="Save proposal and check policy" data-action="save-evaluate-live">
              Save proposal and check policy
            </button>`
          : `<button aria-label="Wrong page">Wrong page</button>`,
    }),
  });
  const session = await handoff(worker, "bounded-start-path");

  const receipt = await worker.execute({
    handoff: session,
    mode: "CANDIDATE_READBACK",
    startPath: "/lightning/n/Strategic_Deal_Workbench",
    candidate: {
      role: "button",
      accessibleName: "Save proposal and check policy",
      readback: { attribute: "data-action", expectedValue: "save-evaluate-live" },
    },
  });

  expect(receipt.status).toBe("PASSED");
  expect(receipt.readbackMatched).toBe(true);
});

test("rejects unsafe start paths before browser navigation", async () => {
  const worker = makeWorker(`<button aria-label="Review">Review</button>`);
  const session = await handoff(worker, "unsafe-start-path");

  const receipt = await worker.execute({
    handoff: session,
    mode: "CANDIDATE_READBACK",
    startPath: "//evil.invalid/path",
    candidate: { role: "button", accessibleName: "Review" },
  });

  expect(receipt.status).toBe("BLOCKED");
  expect(receipt.error?.code).toBe("START_PATH_INVALID");
  expect(receipt.cleanup).toEqual({ contextClosed: true, browserClosed: true });
});

test("blocks ambiguous candidates and still closes the ephemeral browser", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`
    <button aria-label="Continue">First</button>
    <button aria-label="Continue">Second</button>
  `);
  const session = await handoff(worker, canary);

  const receipt = await worker.execute({
    handoff: session,
    mode: "CANDIDATE_READBACK",
    candidate: { role: "button", accessibleName: "Continue" },
  });

  expect(receipt.status).toBe("BLOCKED");
  expect(receipt.error?.code).toBe("CANDIDATE_AMBIGUOUS");
  expect(receipt.lifecycle).toContain("CANDIDATE_AMBIGUOUS");
  expect(receipt.cleanup).toEqual({ contextClosed: true, browserClosed: true });
  expectNoLeak(receipt, canary);
});

test("fails closed before launch for untrusted, expired, wrong-origin, and mismatched bindings", async () => {
  const rejectingWorker = makeWorker("", { verifyEnrollment: async () => false });
  await expect(rejectingWorker.enroll(enrollment())).rejects.toMatchObject({
    code: "ENROLLMENT_NOT_TRUSTED",
  });

  const worker = makeWorker("");
  await expect(
    worker.enroll(
      enrollment({
        issuedAt: new Date(NOW - 120_000).toISOString(),
        expiresAt: new Date(NOW - 60_000).toISOString(),
      }),
    ),
  ).rejects.toMatchObject({ code: "ENROLLMENT_EXPIRED" });
  await expect(
    worker.enroll(enrollment({ lightningOrigin: "https://example.invalid" })),
  ).rejects.toMatchObject({ code: "LIGHTNING_ORIGIN_NOT_ALLOWED" });
  await expect(
    worker.enroll(enrollment({ frontdoorOrigin: "https://example.invalid" })),
  ).rejects.toMatchObject({ code: "FRONTDOOR_ORIGIN_NOT_ALLOWED" });
  await expect(
    worker.enroll(enrollment({ navigationBridgeOrigins: ["https://attacker.invalid"] })),
  ).rejects.toMatchObject({ code: "NAVIGATION_BRIDGE_ORIGINS_INVALID" });
  await expect(
    worker.enroll(enrollment({ resourceOrigins: ["https://attacker.invalid"] })),
  ).rejects.toMatchObject({ code: "RESOURCE_ORIGINS_INVALID" });

  const handle = await worker.enroll(enrollment());
  expect(() =>
    worker.verifySessionIdentity(handle, {
      orgBinding: "wrong-org",
      actorBinding: "actor-fingerprint-001",
    }),
  ).toThrowError("SESSION_BINDING_MISMATCH");
  const identityForOrigin = worker.verifySessionIdentity(handle, {
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
  });
  expect(() =>
    worker.issueSession(handle, identityForOrigin, {
      entryUrl: `${ORIGIN}/secur/frontdoor.jsp?sid=opaque`,
      frontdoorOrigin: "https://different--qa.lightning.force.com",
      lightningOrigin: ORIGIN,
    }),
  ).toThrowError("SESSION_BINDING_MISMATCH");
  const identityForEntry = worker.verifySessionIdentity(handle, {
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
  });
  expect(() =>
    worker.issueSession(handle, identityForEntry, {
      entryUrl: "https://different--qa.lightning.force.com/secur/frontdoor.jsp?sid=opaque",
      frontdoorOrigin: ORIGIN,
      lightningOrigin: ORIGIN,
    }),
  ).toThrowError("SESSION_ORIGIN_MISMATCH");
  const identityForPath = worker.verifySessionIdentity(handle, {
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
  });
  expect(() =>
    worker.issueSession(handle, identityForPath, {
      entryUrl: `${ORIGIN}/other/path?sid=opaque`,
      frontdoorOrigin: ORIGIN,
      lightningOrigin: ORIGIN,
    }),
  ).toThrowError("SESSION_HANDOFF_INVALID");
});

test("sanitizes navigation failures and closes every created resource", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker("", { offlineDocumentForPath: () => undefined });
  const session = await handoff(worker, canary);
  const receipt = await worker.execute({ handoff: session, mode: "READ_ONLY_DOM_CAPTURE" });

  expect(receipt.status).toBe("FAILED");
  expect(receipt.error).toEqual({ class: "NAVIGATION_FAILED", code: "NAVIGATION_FAILED" });
  expect(receipt.cleanup).toEqual({ contextClosed: true, browserClosed: true });
  expectNoLeak(receipt, canary);
});

test("waits for a bounded asynchronous redirect to the exact enrolled Lightning origin", async () => {
  const frontdoor = "https://example--qa.my.salesforce.com";
  const bridge = "https://example--qa.develop.file.force.com";
  const worker = new BrowserWorker({
    verifyEnrollment: async () => true,
    now: () => NOW,
    navigationTimeoutMs: 5_000,
    offlineDocumentForPath: (pathname) => {
      if (pathname === "/secur/frontdoor.jsp") {
        return { body: `<script>setTimeout(() => location.href = '${bridge}/bridge', 25)</script>` };
      }
      if (pathname === "/bridge") {
        return { body: `<script>setTimeout(() => location.href = '${ORIGIN}/lightning/page/home', 25)</script>` };
      }
      return { body: `<button aria-label="Loaded">Loaded</button>` };
    },
  });
  const enrolled = await worker.enroll(enrollment({
    frontdoorOrigin: frontdoor,
    navigationBridgeOrigins: [bridge],
  }));
  const identity = worker.verifySessionIdentity(enrolled, {
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
  });
  const session = worker.issueSession(enrolled, identity, {
    entryUrl: `${frontdoor}/secur/frontdoor.jsp?sid=opaque`,
    frontdoorOrigin: frontdoor,
    lightningOrigin: ORIGIN,
    navigationBridgeOrigins: [bridge],
    resourceOrigins: [],
  });
  const receipt = await worker.execute({ handoff: session, mode: "READ_ONLY_DOM_CAPTURE" });
  expect(receipt.status).toBe("PASSED");
  expect(receipt.capture).toHaveLength(1);
  expect(receipt.capture[0].accessibleName).toBe("Loaded");
});

test("verified identity handles are opaque, enrollment-bound, and one-shot", async () => {
  const worker = makeWorker("");
  const first = await worker.enroll(enrollment());
  const second = await worker.enroll(enrollment({ enrollmentId: "enrollment-002" }));
  const identity = worker.verifySessionIdentity(first, {
    orgBinding: "org-fingerprint-001",
    actorBinding: "actor-fingerprint-001",
  });
  expect(JSON.stringify(identity)).toBe("{}");
  expect(() => worker.issueSession(second, identity, {
    entryUrl: `${ORIGIN}/secur/frontdoor.jsp?sid=opaque`,
    frontdoorOrigin: ORIGIN,
    lightningOrigin: ORIGIN,
  })).toThrowError("SESSION_IDENTITY_HANDLE_INVALID");
  const handoff = worker.issueSession(first, identity, {
    entryUrl: `${ORIGIN}/secur/frontdoor.jsp?sid=opaque`,
    frontdoorOrigin: ORIGIN,
    lightningOrigin: ORIGIN,
  });
  expect(JSON.stringify(handoff)).toBe("{}");
  expect(() => worker.issueSession(first, identity, {
    entryUrl: `${ORIGIN}/secur/frontdoor.jsp?sid=opaque`,
    frontdoorOrigin: ORIGIN,
    lightningOrigin: ORIGIN,
  })).toThrowError("SESSION_IDENTITY_HANDLE_INVALID");
});

test("accepts exact otp+cshc handoff and rejects mixed or extra query keys", async () => {
  const worker = makeWorker("");
  const makeIdentity = async () => {
    const enrolled = await worker.enroll(enrollment());
    return { enrolled, identity: worker.verifySessionIdentity(enrolled, { orgBinding: "org-fingerprint-001", actorBinding: "actor-fingerprint-001" }) };
  };
  const accepted = await makeIdentity();
  expect(() => worker.issueSession(accepted.enrolled, accepted.identity, {
    entryUrl: `${ORIGIN}/secur/frontdoor.jsp?otp=one&cshc=two`, frontdoorOrigin: ORIGIN, lightningOrigin: ORIGIN,
  })).not.toThrow();
  for (const query of ["otp=one", "otp=one&cshc=two&extra=three", "sid=x&otp=one&cshc=two", "otp=one&otp=two&cshc=three"]) {
    const current = await makeIdentity();
    expect(() => worker.issueSession(current.enrolled, current.identity, {
      entryUrl: `${ORIGIN}/secur/frontdoor.jsp?${query}`, frontdoorOrigin: ORIGIN, lightningOrigin: ORIGIN,
    })).toThrowError("SESSION_HANDOFF_INVALID");
  }
});

test("reports a readback mismatch without returning actual or expected values", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  const expectedCanary = `expected-${randomBytes(12).toString("hex")}`;
  const worker = makeWorker(`<button aria-label="Review" data-state="closed">Review</button>`);
  const session = await handoff(worker, canary);
  const receipt = await worker.execute({
    handoff: session,
    mode: "CANDIDATE_READBACK",
    candidate: {
      role: "button",
      accessibleName: "Review",
      readback: { attribute: "data-state", expectedValue: expectedCanary },
    },
  });

  expect(receipt.status).toBe("FAILED");
  expect(receipt.error?.code).toBe("READBACK_MISMATCH");
  expect(JSON.stringify(receipt)).not.toContain(expectedCanary);
  expectNoLeak(receipt, canary);
});

test("uses a nonpersistent context and reports cleanup failure without leaking the session", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;
  let contextOptions: BrowserContextOptions | undefined;
  let closeAttempted = false;
  const launchBrowser = async (): Promise<Browser> => {
    const browser = await chromium.launch({ headless: true });
    return new Proxy(browser, {
      get(target, property, receiver) {
        if (property === "newContext") {
          return async (options: BrowserContextOptions) => {
            contextOptions = options;
            const context = await target.newContext(options);
            return new Proxy(context, {
              get(contextTarget, contextProperty, contextReceiver) {
                if (contextProperty === "close") {
                  return async () => {
                    closeAttempted = true;
                    await contextTarget.close();
                    throw new Error(`untrusted-close-error-${canary}`);
                  };
                }
                const value = Reflect.get(contextTarget, contextProperty, contextReceiver);
                return typeof value === "function" ? value.bind(contextTarget) : value;
              },
            });
          };
        }
        const value = Reflect.get(target, property, receiver);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
  };
  const worker = makeWorker(`<button aria-label="Inspect">Inspect</button>`, { launchBrowser });
  const session = await handoff(worker, canary);
  const receipt = await worker.execute({ handoff: session, mode: "READ_ONLY_DOM_CAPTURE" });

  expect(closeAttempted).toBe(true);
  expect(contextOptions).toEqual({ acceptDownloads: false, serviceWorkers: "block" });
  expect(contextOptions).not.toHaveProperty("recordHar");
  expect(contextOptions).not.toHaveProperty("recordVideo");
  expect(contextOptions).not.toHaveProperty("storageState");
  expect(receipt.status).toBe("FAILED");
  expect(receipt.error).toEqual({
    class: "CLEANUP_FAILED",
    code: "EPHEMERAL_BROWSER_CLEANUP_FAILED",
  });
  expect(receipt.cleanup).toEqual({ contextClosed: false, browserClosed: true });
  expectNoLeak(receipt, canary);
});

// Regression: captureCandidates reached the CLI but was dropped by the worker, so every probe
// test passed while the live path could never produce DOM evidence. These exercise the worker.
const RECORD_ID = "005fj00000N5t8IAAR";
const PERSON_NAME = "Synthetic Regional VP";
const RECORD_ID_DIGEST = "b167df2522f2e65b";

const PROBE_HTML = `
  <div data-object-api="Opportunity">
    <input role="combobox" title="${PERSON_NAME}" data-value="${RECORD_ID}" />
    <button aria-label="Save">Save</button>
  </div>`;

function probeTarget() {
  return {
    obligationPolicy: "COMPLETE_DECLARED_SET",
    dataMutation: "FORBIDDEN",
    obligations: [
      {
        obligationId: "ob-amount",
        originalLocator: {
          kind: "ATTRIBUTE_EQUALS",
          attribute: "data-testid",
          value: "deal-amount",
        },
        semanticIdentity: {
          kind: "FIELD",
          objectApiName: "Opportunity",
          fieldApiName: "Amount",
        },
        assertions: ["EDITABLE", "ENABLED", "VISIBLE"],
      },
    ],
  };
}

async function runProbe(canary: string, captureCandidates?: boolean) {
  const worker = makeWorker(PROBE_HTML);
  const session = await handoff(worker, canary, { permittedModes: ["LOCATOR_PROBE"] });
  return worker.execute({
    handoff: session,
    mode: "LOCATOR_PROBE",
    startPath: "/lightning/o/Opportunity/list",
    probe: { target: probeTarget(), stage: "BASELINE", captureCandidates },
  });
}

test("locator probe pushes DOM evidence when the worker is asked to capture candidates", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  const receipt = await runProbe(canary, true);

  expect({ status: receipt.status, error: receipt.error }).toEqual({
    status: "FAILED",
    error: undefined,
  });
  const observation = receipt.probeReport?.observations[0];
  expect(observation?.outcome).toBe("LOCATOR_NOT_FOUND");
  expect(observation?.domEvidence).toBeDefined();
  expect(observation?.domEvidence?.candidates.length).toBeGreaterThan(0);
  expectNoLeak(receipt, canary);
});

test("locator probe withholds DOM evidence when candidate capture is not requested", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  const receipt = await runProbe(canary);

  expect(receipt.probeReport?.observations[0]?.domEvidence).toBeUndefined();
});

test("pushed DOM evidence carries digests, never the record value or the person name", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  const receipt = await runProbe(canary, true);

  const projection = JSON.stringify(receipt);
  expect(projection).not.toContain(RECORD_ID);
  expect(projection).not.toContain(PERSON_NAME);
  // Pins the cross-language digest contract: the Python signature store must agree byte for byte,
  // otherwise stored signatures and pushed candidates can never be compared.
  expect(projection).toContain(RECORD_ID_DIGEST);
});

// REQ-HEAL-14: the llm tier is gated — candidate capture fires ONLY when the deterministic tier
// abstains. Previously proven only at the CLI projection level against stubbed reports. These four
// drive a real Playwright page through both sides of the gate.
//
// ANTI-VACUOUS: an earlier version of the evidence tests passed while probeReport was undefined,
// which makes "evidence withheld" and "the probe never ran" indistinguishable. Every assertion that
// evidence is ABSENT below is preceded by a liveness assertion that the probe actually ran.

// The declared original locator resolves to exactly one element, and that same element carries the
// metadata identity from probeTarget(), so the deterministic tier discovers it without the llm tier.
const RESOLVABLE_PROBE_HTML = `
  <div data-object-api="Opportunity">
    <input data-field-api="Amount" data-testid="deal-amount" />
    <button aria-label="Save">Save</button>
  </div>`;

// Two controls carry the same Opportunity.Amount identity, so metadata discovery cannot pick one.
const AMBIGUOUS_PROBE_HTML = `
  <div data-object-api="Opportunity">
    <input data-field-api="Amount" data-testid="deal-amount" />
    <input data-field-api="Amount" />
    <button aria-label="Save">Save</button>
  </div>`;

async function runProbeOn(html: string, canary: string, captureCandidates?: boolean) {
  const worker = makeWorker(html);
  const session = await handoff(worker, canary, { permittedModes: ["LOCATOR_PROBE"] });
  return worker.execute({
    handoff: session,
    mode: "LOCATOR_PROBE",
    startPath: "/lightning/o/Opportunity/list",
    probe: { target: probeTarget(), stage: "BASELINE", captureCandidates },
  });
}

test("gate open: deterministic abstention on a missing element pushes DOM evidence", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  // PROBE_HTML has no [data-testid="deal-amount"], so the declared locator cannot resolve.
  const receipt = await runProbe(canary, true);

  // Liveness first: the probe ran and produced exactly one observation.
  expect(receipt.error).toBeUndefined();
  expect(receipt.probeReport).toBeDefined();
  expect(receipt.probeReport?.observations.length).toBe(1);

  const observation = receipt.probeReport!.observations[0]!;
  expect(observation.outcome).toBe("LOCATOR_NOT_FOUND");
  expect(observation.domEvidence).toBeDefined();
  expect(observation.domEvidence?.capturedForOutcome).toBe("LOCATOR_NOT_FOUND");
  expect(observation.domEvidence!.candidates.length).toBeGreaterThan(0);
});

test("gate shut: the deterministic tier resolving the element withholds DOM evidence", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  const receipt = await runProbeOn(RESOLVABLE_PROBE_HTML, canary, true);

  // Liveness first. Without this, a probe that never ran would satisfy every assertion below.
  expect(receipt.error).toBeUndefined();
  expect(receipt.probeReport).toBeDefined();
  expect(receipt.probeReport?.observations.length).toBe(1);

  const observation = receipt.probeReport!.observations[0]!;
  // The deterministic tier genuinely resolved the element: it asserted against a real control.
  expect(observation.outcome).toBe("PASSED");
  expect({
    visible: observation.visible,
    enabled: observation.enabled,
    editable: observation.editable,
  }).toEqual({ visible: true, enabled: true, editable: true });
  expect(observation.candidateEvidenceDigest).toBeDefined();
  expect(receipt.status).toBe("PASSED");
  expect(receipt.probeReport?.status).toBe("PASSED");

  // REQ-HEAL-14 core: capture was requested, the deterministic tier succeeded, so the llm tier
  // must not fire and no DOM must be pushed.
  expect(observation.domEvidence).toBeUndefined();
  expectNoLeak(receipt, canary);
});

test("gate shut: a resolved element with capture disabled still withholds DOM evidence", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  const receipt = await runProbeOn(RESOLVABLE_PROBE_HTML, canary, false);

  expect(receipt.error).toBeUndefined();
  expect(receipt.probeReport).toBeDefined();
  expect(receipt.probeReport?.observations.length).toBe(1);

  const observation = receipt.probeReport!.observations[0]!;
  expect(observation.outcome).toBe("PASSED");
  expect(receipt.status).toBe("PASSED");
  expect(observation.domEvidence).toBeUndefined();
});

test("gate open: an ambiguous semantic identity abstains and pushes DOM evidence", async () => {
  const canary = `session-${randomBytes(12).toString("hex")}`;

  const receipt = await runProbeOn(AMBIGUOUS_PROBE_HTML, canary, true);

  expect(receipt.error).toBeUndefined();
  expect(receipt.probeReport).toBeDefined();
  expect(receipt.probeReport?.observations.length).toBe(1);

  const observation = receipt.probeReport!.observations[0]!;
  // The declared locator still resolves to one element, so this is the semantic tier abstaining,
  // not a missing-element path.
  expect(observation.outcome).toBe("CANDIDATE_AMBIGUOUS");
  expect(observation.staleOriginal).toBe(false);
  expect(observation.candidateCount).toBeGreaterThan(1);
  expect(observation.domEvidence).toBeDefined();
  expect(observation.domEvidence?.capturedForOutcome).toBe("CANDIDATE_AMBIGUOUS");
  expect(observation.domEvidence!.candidates.length).toBeGreaterThan(0);
  expect(receipt.status).toBe("FAILED");
});
