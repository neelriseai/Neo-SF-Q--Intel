import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { chromium } from "playwright";
import type { Page } from "playwright";
import type { BrowserWorkerReceipt } from "../src/browser-worker.js";
import {
  BrowserRecoveryError,
  BrowserRecoveryExecutor,
  FileBrowserRecoveryPermitLedger,
  type BrowserRecoveryPermitPayload,
  type BrowserRecoveryScope,
  signBrowserRecoveryPermit,
  verifyBrowserRecoveryEvidence,
} from "../src/browser-recovery.js";

const NOW = Date.parse("2030-01-01T00:00:00.000Z");
const AUTHORITY_KEY = Buffer.from("independent-browser-authority-key-material-001");
const EVIDENCE_KEY = Buffer.from("product-browser-evidence-key-material-00001");
const ORIGIN = "https://recovery-fixture.example.lightning.force.com";

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function scope(overrides: Partial<BrowserRecoveryScope> = {}): BrowserRecoveryScope {
  return {
    campaignId: "campaign-001",
    projectId: "project-generic",
    sourceContractSha256: "1".repeat(64),
    candidateSha256: "2".repeat(64),
    buildSha256: "3".repeat(64),
    operationPlanSha256: "4".repeat(64),
    restoreScopeSha256: "5".repeat(64),
    policySha256: "6".repeat(64),
    profileSha256: "7".repeat(64),
    orgFingerprintSha256: "8".repeat(64),
    actorFingerprintSha256: "9".repeat(64),
    lightningOriginSha256: digest(ORIGIN),
    expectedExecutionContractSha256: "a".repeat(64),
    ...overrides,
  };
}

function candidateReceipt(overrides: Partial<BrowserWorkerReceipt> = {}): BrowserWorkerReceipt {
  return {
    schemaVersion: "1.0.0",
    executionId: "candidate-execution-001",
    inputDigest: "a".repeat(64),
    capabilityId: "automation.browser-worker",
    status: "PASSED",
    mode: "CANDIDATE_READBACK",
    enrollment: {
      enrollmentIdDigest: "b".repeat(64),
      issuerDigest: "c".repeat(64),
      originDigest: scope().lightningOriginSha256,
      orgBindingDigest: scope().orgFingerprintSha256,
      actorBindingDigest: scope().actorFingerprintSha256,
      policyDigest: "0".repeat(64),
    },
    lifecycle: ["CAPTURED", "CANDIDATE_DISCOVERED"],
    capture: [],
    candidateCount: 1,
    cleanup: { contextClosed: true, browserClosed: true },
    ...overrides,
  };
}

function permit(
  receipt: BrowserWorkerReceipt,
  overrides: Partial<BrowserRecoveryPermitPayload> = {},
) {
  const payload: BrowserRecoveryPermitPayload = {
    schemaVersion: "1.0.0",
    permitId: "recovery-permit-001",
    authorityClass: "INDEPENDENT_BROWSER_RECOVERY_AUTHORITY",
    scope: scope(),
    candidateBinding: {
      workerExecutionId: receipt.executionId,
      workerInputDigest: receipt.inputDigest,
      candidateCount: 1,
      role: "button",
      accessibleNameSha256: digest("Enable feature"),
      stateAttribute: "aria-pressed",
      preconditionSha256: digest("false"),
      expectedAfterSha256: digest("true"),
      enrollmentOriginDigest: receipt.enrollment.originDigest,
      enrollmentOrgBindingDigest: receipt.enrollment.orgBindingDigest,
      enrollmentActorBindingDigest: receipt.enrollment.actorBindingDigest,
    },
    permittedAction: "CLICK_REVERSIBLE_TOGGLE",
    beforeAssertion: "EXACT_PRECONDITION",
    afterAssertion: "EXACT_EXPECTED_AND_CHANGED",
    restoration: "REQUIRED_EXACT_PRECONDITION",
    cleanup: "REQUIRED_EPHEMERAL_SESSION_CLOSE",
    issuedAt: new Date(NOW - 1_000).toISOString(),
    expiresAt: new Date(NOW + 60_000).toISOString(),
    ...overrides,
  };
  return signBrowserRecoveryPermit(payload, "independent-authority", AUTHORITY_KEY);
}

function executor(
  html: string,
  overrides: Partial<ConstructorParameters<typeof BrowserRecoveryExecutor>[0]> = {},
) {
  return new BrowserRecoveryExecutor({
    expectedScope: scope(),
    trustedAuthorityKeys: new Map([["independent-authority", AUTHORITY_KEY]]),
    evidenceIssuer: { issuerId: "product-browser-evidence", hmacKey: EVIDENCE_KEY },
    now: () => NOW,
    permitLedger: new FileBrowserRecoveryPermitLedger(
      mkdtempSync(join(tmpdir(), "neo-browser-recovery-")),
    ),
    openSession: async () => {
      const browser = await chromium.launch({ headless: true });
      const context = await browser.newContext({
        acceptDownloads: false,
        serviceWorkers: "block",
      });
      const page = await context.newPage();
      await fixturePage(page, html);
      return {
        page,
        observeIdentity: async () => observedIdentity(),
        close: async () => {
          await context.close();
          await browser.close();
        },
      };
    },
    ...overrides,
  });
}

async function fixturePage(page: Page, html: string) {
  await page.route("**/*", (route) => route.fulfill({ status: 200, contentType: "text/html", body: html }));
  await page.goto(`${ORIGIN}/fixture`);
}

function observedIdentity() {
  return {
    lightningOriginSha256: scope().lightningOriginSha256,
    orgFingerprintSha256: scope().orgFingerprintSha256,
    actorFingerprintSha256: scope().actorFingerprintSha256,
    observedAt: new Date(NOW).toISOString(),
    expiresAt: new Date(NOW + 60_000).toISOString(),
  };
}

function request(receipt = candidateReceipt()) {
  return {
    permit: permit(receipt),
    candidateReceipt: receipt,
    candidate: { role: "button" as const, accessibleName: "Enable feature" },
    preconditionValue: "false",
    expectedAfterValue: "true",
  };
}

test("executes an independently authorized reversible action, restores, closes, and signs", async () => {
  const recovery = executor(`
    <button aria-label="Enable feature" aria-pressed="false"
      onclick="this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')">
      Enable
    </button>
  `);

  const evidence = await recovery.execute(request());

  expect(evidence.payload.status).toBe("PASSED");
  expect(evidence.payload.lifecycle).toEqual([
    "CANDIDATE_DISCOVERED",
    "PROPOSAL_APPROVED",
    "ACTION_APPLIED",
    "OUTCOME_VERIFIED",
  ]);
  expect(evidence.payload.assertions).toEqual({
    identityBeforeActionMatched: true,
    identityBeforeRestoreMatched: true,
    uniqueCurrentCandidate: true,
    preconditionMatched: true,
    changedAsExpected: true,
    restoredExactly: true,
    sessionClosed: true,
  });
  expect(evidence.payload.beforeSha256).toBe(digest("false"));
  expect(evidence.payload.afterSha256).toBe(digest("true"));
  expect(evidence.payload.restoredSha256).toBe(digest("false"));
  expect(verifyBrowserRecoveryEvidence(evidence, "product-browser-evidence", EVIDENCE_KEY)).toBe(
    true,
  );
});

test("rejects unchanged readback as healing and still restores and cleans up", async () => {
  const recovery = executor(
    `<button aria-label="Enable feature" aria-pressed="false">Enable</button>`,
  );

  const evidence = await recovery.execute(request());

  expect(evidence.payload.status).toBe("FAILED");
  expect(evidence.payload.errorCode).toBe("RECOVERY_UNCHANGED_READBACK");
  expect(evidence.payload.lifecycle).toEqual([
    "CANDIDATE_DISCOVERED",
    "PROPOSAL_APPROVED",
    "ACTION_APPLIED",
  ]);
  expect(evidence.payload.assertions.changedAsExpected).toBe(false);
  expect(evidence.payload.assertions.restoredExactly).toBe(true);
  expect(evidence.payload.assertions.sessionClosed).toBe(true);
});

test("blocks ambiguous candidate and precondition mismatch before action", async () => {
  const ambiguous = executor(`
    <button aria-label="Enable feature" aria-pressed="false">One</button>
    <button aria-label="Enable feature" aria-pressed="false">Two</button>
  `);
  const ambiguousEvidence = await ambiguous.execute(request());
  expect(ambiguousEvidence.payload.errorCode).toBe("RECOVERY_CANDIDATE_AMBIGUOUS");
  expect(ambiguousEvidence.payload.assertions.preconditionMatched).toBe(false);

  const mismatch = executor(
    `<button aria-label="Enable feature" aria-pressed="true">Enable</button>`,
  );
  const mismatchEvidence = await mismatch.execute(request());
  expect(mismatchEvidence.payload.errorCode).toBe("RECOVERY_PRECONDITION_MISMATCH");
  expect(mismatchEvidence.payload.assertions.changedAsExpected).toBe(false);
});

test("fails when restoration or cleanup cannot be proven", async () => {
  const restoration = executor(`
    <button aria-label="Enable feature" aria-pressed="false"
      onclick="this.setAttribute('aria-pressed', 'true')">Enable</button>
  `);
  const restorationEvidence = await restoration.execute(request());
  expect(restorationEvidence.payload.status).toBe("FAILED");
  expect(restorationEvidence.payload.errorCode).toBe("RECOVERY_RESTORE_FAILED");

  let closeAttempted = false;
  const cleanup = executor(
    `<button aria-label="Enable feature" aria-pressed="false">Enable</button>`,
    {
      openSession: async () => {
        const browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        await fixturePage(page,
          `<button aria-label="Enable feature" aria-pressed="false">Enable</button>`,
        );
        return {
          page,
          observeIdentity: async () => observedIdentity(),
          close: async () => {
            closeAttempted = true;
            await browser.close();
            throw new Error("untrusted cleanup detail");
          },
        };
      },
    },
  );
  const cleanupEvidence = await cleanup.execute(request());
  expect(closeAttempted).toBe(true);
  expect(cleanupEvidence.payload.status).toBe("FAILED");
  expect(cleanupEvidence.payload.errorCode).toBe("RECOVERY_CLEANUP_FAILED");
  expect(JSON.stringify(cleanupEvidence)).not.toContain("untrusted cleanup detail");
});

test("rejects expired, tampered, cross-request, and non-unique candidate authority", async () => {
  const recovery = executor("");
  const receipt = candidateReceipt();

  const expired = request(receipt);
  expired.permit = permit(receipt, {
    issuedAt: new Date(NOW - 120_000).toISOString(),
    expiresAt: new Date(NOW - 60_000).toISOString(),
  });
  await expect(recovery.execute(expired)).rejects.toMatchObject({
    code: "RECOVERY_AUTHORITY_INVALID",
  });

  const tampered = request(receipt);
  tampered.permit = { ...tampered.permit, signatureSha256: "0".repeat(64) };
  await expect(recovery.execute(tampered)).rejects.toBeInstanceOf(BrowserRecoveryError);

  const crossRequest = request(receipt);
  crossRequest.candidateReceipt = candidateReceipt({ executionId: "another-execution" });
  await expect(recovery.execute(crossRequest)).rejects.toMatchObject({
    code: "RECOVERY_AUTHORITY_INVALID",
  });

  const ambiguousReceipt = candidateReceipt({ candidateCount: 2 });
  const nonUnique = request(ambiguousReceipt);
  await expect(recovery.execute(nonUnique)).rejects.toMatchObject({
    code: "RECOVERY_AUTHORITY_INVALID",
  });
});

test("rejects secret or absolute-path request values without echoing them", async () => {
  const recovery = executor("");
  for (const canary of [
    `sk-${randomBytes(12).toString("hex")}`,
    "C:/Users/person/private",
    "Bearer private-token-value",
  ]) {
    const unsafe = request();
    unsafe.candidate.accessibleName = canary;
    await expect(recovery.execute(unsafe)).rejects.toMatchObject({
      code: "RECOVERY_AUTHORITY_INVALID",
      message: "RECOVERY_AUTHORITY_INVALID",
    });
  }
});

test("atomically claims each permit once and blocks replay", async () => {
  const recovery = executor(`
    <button aria-label="Enable feature" aria-pressed="false"
      onclick="this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')">
      Enable
    </button>
  `);
  const boundedRequest = request();

  expect((await recovery.execute(boundedRequest)).payload.status).toBe("PASSED");
  await expect(recovery.execute(boundedRequest)).rejects.toMatchObject({
    code: "RECOVERY_PERMIT_ALREADY_CLAIMED",
  });
});

test("uses an immutable request snapshot and revalidates authority immediately before action", async () => {
  let mutableRequest = request();
  const recovery = executor("", {
    openSession: async () => {
      mutableRequest.candidate.accessibleName = "Attacker changed intent";
      (mutableRequest.permit.payload.scope as { campaignId: string }).campaignId =
        "attacker-campaign";
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage();
      await fixturePage(page, `
        <button aria-label="Enable feature" aria-pressed="false"
          onclick="this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')">
          Enable
        </button>
      `);
      return { page, observeIdentity: async () => observedIdentity(), close: () => browser.close() };
    },
  });
  const evidence = await recovery.execute(mutableRequest);
  expect(evidence.payload.status).toBe("PASSED");

  let current = NOW;
  const expiringRequest = request();
  const expiring = executor("", {
    now: () => current,
    openSession: async () => {
      current = NOW + 60_000;
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage();
      await fixturePage(page,
        `<button aria-label="Enable feature" aria-pressed="false">Enable</button>`,
      );
      return { page, observeIdentity: async () => observedIdentity(), close: () => browser.close() };
    },
  });
  const expiredEvidence = await expiring.execute(expiringRequest);
  expect(expiredEvidence.payload.status).toBe("FAILED");
  expect(expiredEvidence.payload.errorCode).toBe("RECOVERY_AUTHORITY_EXPIRED_OR_CHANGED");
  expect(expiredEvidence.payload.lifecycle).not.toContain("ACTION_APPLIED");
});

test("production coordinator source remains read-only and cannot dispatch recovery", async () => {
  const source = await import("node:fs/promises").then((fs) =>
    fs.readFile(new URL("../src/salesforce-browser-coordinator.ts", import.meta.url), "utf8"),
  );
  expect(source).toContain('mode: "READ_ONLY_DOM_CAPTURE"');
  expect(source).toContain("mutationActionsEnabled: false");
  expect(source).not.toContain("BrowserRecoveryExecutor");
  expect(source).not.toContain("CLICK_REVERSIBLE_TOGGLE");
});

for (const mismatch of ["org", "actor", "observed-origin", "page-origin", "stale-observation"] as const) {
  test(`blocks current ${mismatch} mismatch before recovery action and closes the session`, async () => {
    let clicks = -1;
    const recovery = executor("", {
      openSession: async () => {
        const browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        await fixturePage(page, toggleHtml());
        if (mismatch === "page-origin") await page.goto("https://different.example.invalid/fixture");
        return {
          page,
          observeIdentity: async () => ({
            ...observedIdentity(),
            ...(mismatch === "org" ? { orgFingerprintSha256: "c".repeat(64) } : {}),
            ...(mismatch === "actor" ? { actorFingerprintSha256: "c".repeat(64) } : {}),
            ...(mismatch === "observed-origin" ? { lightningOriginSha256: "c".repeat(64) } : {}),
            ...(mismatch === "stale-observation" ? { observedAt: new Date(NOW - 1).toISOString() } : {}),
          }),
          close: async () => {
            clicks = await page.evaluate(() => Number(document.body.dataset.clicks ?? 0));
            await browser.close();
          },
        };
      },
    });
    const evidence = await recovery.execute(request());
    expect(evidence.payload.status).toBe("FAILED");
    expect(evidence.payload.errorCode).toBe("RECOVERY_CURRENT_IDENTITY_MISMATCH");
    expect(evidence.payload.assertions.sessionClosed).toBe(true);
    expect(evidence.payload.lifecycle).not.toContain("ACTION_APPLIED");
    expect(clicks).toBe(0);
  });
}

for (const phase of ["before-action", "before-restoration"] as const) {
  test(`blocks actor drift ${phase} instead of clicking in a different identity`, async () => {
    let clicks = -1;
    let observations = 0;
    const recovery = executor("", {
      openSession: async () => {
        const browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        await fixturePage(page, toggleHtml());
        return {
          page,
          observeIdentity: async () => {
            observations += 1;
            const drifted = observations >= (phase === "before-action" ? 2 : 3);
            return { ...observedIdentity(), ...(drifted ? { actorFingerprintSha256: "c".repeat(64) } : {}) };
          },
          close: async () => {
            clicks = await page.evaluate(() => Number(document.body.dataset.clicks ?? 0));
            await browser.close();
          },
        };
      },
    });
    const evidence = await recovery.execute(request());
    expect(evidence.payload.status).toBe("FAILED");
    expect(evidence.payload.errorCode).toBe("RECOVERY_CURRENT_IDENTITY_MISMATCH");
    expect(evidence.payload.assertions.restoredExactly).toBe(false);
    expect(evidence.payload.assertions.sessionClosed).toBe(true);
    expect(clicks).toBe(phase === "before-action" ? 0 : 1);
  });
}

test("expiry during durable permit claim blocks before openSession", async () => {
  let current = NOW;
  let sessions = 0;
  const recovery = executor("", {
    now: () => current,
    permitLedger: { claim: async () => { current = NOW + 60_000; return true; } },
    openSession: async () => { sessions += 1; throw new Error("must not be opened"); },
  });
  await expect(recovery.execute(request())).rejects.toMatchObject({
    code: "RECOVERY_AUTHORITY_EXPIRED_OR_CHANGED",
  });
  expect(sessions).toBe(0);
});

function toggleHtml() {
  return `<button aria-label="Enable feature" aria-pressed="false"
    onclick="document.body.dataset.clicks = String(Number(document.body.dataset.clicks || 0) + 1);
      this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')">
      Enable</button>`;
}
