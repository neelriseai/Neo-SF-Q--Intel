import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  BrowserWorker,
  type BrowserWorkerReceipt,
  type CandidateIntent,
} from "./browser-worker.js";
import {
  BrowserCoordinatorError,
  PlaywrightChromiumBrowserFactory,
  loadTrustedLiveBrowserProfile,
} from "./salesforce-browser-coordinator.js";
import { SalesforceCliSessionBroker } from "./salesforce-cli-session.js";

type Environment = Readonly<Record<string, string | undefined>>;
type OutputWriter = (value: string) => void;

type CandidateReadbackRequest = {
  readonly startPath: string;
  readonly candidate: CandidateIntent;
  readonly maximumAttempts: number;
  readonly retryDelayMs: number;
};

type CandidateReadbackStepContext = {
  readonly schemaVersion: "1.0.0";
  readonly intentKind: "HOST_ATTRIBUTE_READBACK";
  readonly strategy: "BOUNDED_HOST_ATTRIBUTE_READBACK";
  readonly retryPolicy: "MAX_THREE_ATTEMPTS_SAME_INTENT";
  readonly startPathDigest: string;
  readonly hostTag: "button" | "lightning-button";
  readonly hostAttribute: "data-action";
  readonly expectedValueDigest: string;
  readonly expectedPostcondition: "EXACTLY_ONE_HOST_ATTRIBUTE_MATCH";
  readonly maximumAttempts: number;
};

type CandidateReadbackAttempt = {
  readonly attempt: number;
  readonly strategy: "BOUNDED_HOST_ATTRIBUTE_READBACK";
  readonly status: BrowserWorkerReceipt["status"];
  readonly lifecycle: BrowserWorkerReceipt["lifecycle"];
  readonly candidateCount: number;
  readonly readbackMatched?: boolean;
  readonly cleanup: BrowserWorkerReceipt["cleanup"];
  readonly errorCode?: string;
  readonly executionIdDigest: string;
  readonly inputDigest: string;
};

export async function runLiveCandidateReadbackCli(
  arguments_: readonly string[],
  environment: Environment,
  write: OutputWriter,
): Promise<number> {
  try {
    if (arguments_.length !== 0) {
      throw new BrowserCoordinatorError("CLI_ARGUMENTS_INVALID");
    }
    const raw = await loadProfileBytes(environment);
    const expectedDigest = environment.NEO_BROWSER_PROFILE_SHA256;
    if (!expectedDigest) throw new BrowserCoordinatorError("PROFILE_NOT_TRUSTED");
    const profile = loadTrustedLiveBrowserProfile(raw, expectedDigest);
    // Operator-only visible-browser mode. The trusted profile still pins headless: true;
    // this opt-in changes only the local launch surface and is reported in the projection.
    const headedMode = environment.NEO_BROWSER_HEADED === "true";
    const request = candidateReadbackRequest(environment);
    const expectedEnrollment = canonicalJson(profile.enrollment);
    const worker = new BrowserWorker({
      verifyEnrollment: (assertion) => constantTimeEqual(canonicalJson(assertion), expectedEnrollment),
      allowedFrontdoorHostnameSuffixes:
        profile.sessionBroker.allowedFrontdoorHostnameSuffixes,
      allowedLightningHostnameSuffixes:
        profile.sessionBroker.allowedLightningHostnameSuffixes,
      navigationTimeoutMs: profile.browser.navigationTimeoutMs,
      operationTimeoutMs: profile.browser.operationTimeoutMs,
      launchBrowser: () =>
        new PlaywrightChromiumBrowserFactory({
          headless: !headedMode,
          launchTimeoutMs: profile.browser.launchTimeoutMs,
        }).launch(),
    });
    const broker = new SalesforceCliSessionBroker(profile.sessionBroker, worker);
    const enrollment = await worker.enroll(profile.enrollment);
    const receipts: BrowserWorkerReceipt[] = [];
    for (let attempt = 1; attempt <= request.maximumAttempts; attempt += 1) {
      const handoff = await broker.acquire(enrollment);
      const receipt = await worker.execute({
        handoff,
        mode: "CANDIDATE_READBACK",
        startPath: request.startPath,
        candidate: request.candidate,
        captureLimit: profile.execution.captureLimit,
      });
      receipts.push(receipt);
      if (receipt.status === "PASSED" || !isRetryableReadbackFailure(receipt)) {
        break;
      }
      if (attempt < request.maximumAttempts) {
        await delay(request.retryDelayMs);
      }
    }
    const projection = liveCandidateReadbackProjection(
      receipts,
      candidateStepContext(request),
    );
    write(`${JSON.stringify({ ...projection, headedMode })}\n`);
    return projection.status === "PASSED" ? 0 : 2;
  } catch (error) {
    const code = safeErrorCode(error);
    write(
      `${JSON.stringify({
        schemaVersion: "1.0.0",
        status: "BLOCKED",
        errorCode: code,
        diagnosticOnly: true,
        releaseEligible: false,
      })}\n`,
    );
    return 2;
  }
}

function candidateReadbackRequest(environment: Environment): CandidateReadbackRequest {
  const startPath = environment.NEO_BROWSER_CANDIDATE_START_PATH;
  if (!startPath || !isSafeStartPath(startPath)) {
    throw new BrowserCoordinatorError("CANDIDATE_START_PATH_INVALID");
  }
  const tag = environment.NEO_BROWSER_CANDIDATE_HOST_TAG;
  const attribute = environment.NEO_BROWSER_CANDIDATE_HOST_ATTRIBUTE;
  const expectedValue = environment.NEO_BROWSER_CANDIDATE_HOST_VALUE;
  if (
    (tag !== "button" && tag !== "lightning-button") ||
    attribute !== "data-action" ||
    !expectedValue ||
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/.test(expectedValue)
  ) {
    throw new BrowserCoordinatorError("CANDIDATE_HOST_READBACK_INVALID");
  }
  const maximumAttempts = boundedInteger(
    environment.NEO_BROWSER_CANDIDATE_MAX_ATTEMPTS,
    3,
    1,
    3,
    "CANDIDATE_MAX_ATTEMPTS_INVALID",
  );
  const retryDelayMs = boundedInteger(
    environment.NEO_BROWSER_CANDIDATE_RETRY_DELAY_MS,
    1_000,
    0,
    5_000,
    "CANDIDATE_RETRY_DELAY_INVALID",
  );
  return {
    startPath,
    candidate: {
      hostAttribute: {
        tag,
        attribute,
        expectedValue,
      },
    },
    maximumAttempts,
    retryDelayMs,
  };
}

export function liveCandidateReadbackProjection(
  receipts: readonly BrowserWorkerReceipt[],
  stepContext: CandidateReadbackStepContext,
): Record<string, unknown> {
  const finalReceipt = receipts.at(-1);
  if (!finalReceipt) {
    throw new BrowserCoordinatorError("READBACK_RECEIPT_MISSING");
  }
  return Object.freeze({
    schemaVersion: "1.0.0",
    diagnosticOnly: true,
    releaseEligible: false,
    evidencePhase: "DEPLOYED_CANDIDATE_READBACK",
    capabilityId: finalReceipt.capabilityId,
    status: finalReceipt.status,
    mode: finalReceipt.mode,
    attemptCount: receipts.length,
    stepContext,
    attempts: receipts.map((receipt, index): CandidateReadbackAttempt => ({
      attempt: index + 1,
      strategy: "BOUNDED_HOST_ATTRIBUTE_READBACK",
      status: receipt.status,
      lifecycle: receipt.lifecycle,
      candidateCount: receipt.candidateCount,
      readbackMatched: receipt.readbackMatched,
      cleanup: receipt.cleanup,
      errorCode: receipt.error?.code,
      executionIdDigest: digest(receipt.executionId),
      inputDigest: receipt.inputDigest,
    })),
    policyDigest: finalReceipt.enrollment.policyDigest,
    lifecycle: finalReceipt.lifecycle,
    candidateCount: finalReceipt.candidateCount,
    readbackMatched: finalReceipt.readbackMatched,
    cleanup: finalReceipt.cleanup,
    errorCode: finalReceipt.error?.code,
  });
}

function candidateStepContext(
  request: CandidateReadbackRequest,
): CandidateReadbackStepContext {
  const host = request.candidate.hostAttribute;
  if (!host) throw new BrowserCoordinatorError("CANDIDATE_HOST_READBACK_INVALID");
  return Object.freeze({
    schemaVersion: "1.0.0",
    intentKind: "HOST_ATTRIBUTE_READBACK",
    strategy: "BOUNDED_HOST_ATTRIBUTE_READBACK",
    retryPolicy: "MAX_THREE_ATTEMPTS_SAME_INTENT",
    startPathDigest: digest(request.startPath),
    hostTag: host.tag,
    hostAttribute: host.attribute,
    expectedValueDigest: digest(host.expectedValue),
    expectedPostcondition: "EXACTLY_ONE_HOST_ATTRIBUTE_MATCH",
    maximumAttempts: request.maximumAttempts,
  });
}

function isRetryableReadbackFailure(receipt: BrowserWorkerReceipt): boolean {
  return (
    receipt.status !== "PASSED" &&
    receipt.cleanup.contextClosed &&
    receipt.cleanup.browserClosed &&
    (receipt.error?.code === "CANDIDATE_NOT_FOUND" ||
      receipt.error?.code === "READBACK_MISMATCH")
  );
}

async function loadProfileBytes(environment: Environment): Promise<Uint8Array> {
  const inline = environment.NEO_BROWSER_LIVE_PROFILE_JSON;
  const environmentPath = environment.NEO_BROWSER_LIVE_PROFILE_PATH;
  const sources = [inline, environmentPath].filter((value): value is string => Boolean(value));
  if (sources.length !== 1) throw new BrowserCoordinatorError("PROFILE_SOURCE_INVALID");
  if (inline) return Buffer.from(inline, "utf8");
  try {
    return await readFile(environmentPath!);
  } catch {
    throw new BrowserCoordinatorError("PROFILE_READ_FAILED");
  }
}

function safeErrorCode(error: unknown): string {
  if (
    error instanceof BrowserCoordinatorError ||
    (error instanceof Error && /^[A-Z][A-Z0-9_]{2,100}$/.test(error.message))
  ) {
    return error instanceof BrowserCoordinatorError ? error.code : error.message;
  }
  return "LIVE_CANDIDATE_READBACK_FAILED";
}

function boundedInteger(
  value: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
  code: string,
): number {
  const parsed = value === undefined || value === "" ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new BrowserCoordinatorError(code);
  }
  return parsed;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => {
    setTimeout(resolveDelay, milliseconds);
  });
}

function isSafeStartPath(value: string): boolean {
  if (
    value.length < 1 ||
    value.length > 256 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /[\u0000-\u001f\u007f\\]/.test(value)
  ) {
    return false;
  }
  let parsed: URL;
  try {
    parsed = new URL(value, "https://example.lightning.force.com");
  } catch {
    return false;
  }
  return (
    parsed.origin === "https://example.lightning.force.com" &&
    parsed.pathname === value &&
    !parsed.search &&
    !parsed.hash
  );
}

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left, "utf8");
  const rightBytes = Buffer.from(right, "utf8");
  if (leftBytes.byteLength !== rightBytes.byteLength) return false;
  return createHash("sha256").update(leftBytes).digest("hex") ===
    createHash("sha256").update(rightBytes).digest("hex");
}

const invokedPath = process.argv[1];
if (invokedPath && fileURLToPath(import.meta.url) === resolve(invokedPath)) {
  const exitCode = await runLiveCandidateReadbackCli(process.argv.slice(2), process.env, (value) => {
    process.stdout.write(value);
  });
  process.exitCode = exitCode;
}
