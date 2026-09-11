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
          headless: true,
          launchTimeoutMs: profile.browser.launchTimeoutMs,
        }).launch(),
    });
    const broker = new SalesforceCliSessionBroker(profile.sessionBroker, worker);
    const enrollment = await worker.enroll(profile.enrollment);
    const handoff = await broker.acquire(enrollment);
    const receipt = await worker.execute({
      handoff,
      mode: "CANDIDATE_READBACK",
      startPath: request.startPath,
      candidate: request.candidate,
      captureLimit: profile.execution.captureLimit,
    });
    write(`${JSON.stringify(liveCandidateReadbackProjection(receipt))}\n`);
    return receipt.status === "PASSED" ? 0 : 2;
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

function candidateReadbackRequest(environment: Environment): {
  startPath: string;
  candidate: CandidateIntent;
} {
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
  return {
    startPath,
    candidate: {
      hostAttribute: {
        tag,
        attribute,
        expectedValue,
      },
    },
  };
}

export function liveCandidateReadbackProjection(
  receipt: BrowserWorkerReceipt,
): Record<string, unknown> {
  return Object.freeze({
    schemaVersion: "1.0.0",
    diagnosticOnly: true,
    releaseEligible: false,
    evidencePhase: "DEPLOYED_CANDIDATE_READBACK",
    capabilityId: receipt.capabilityId,
    status: receipt.status,
    mode: receipt.mode,
    executionIdDigest: digest(receipt.executionId),
    inputDigest: receipt.inputDigest,
    policyDigest: receipt.enrollment.policyDigest,
    lifecycle: receipt.lifecycle,
    candidateCount: receipt.candidateCount,
    readbackMatched: receipt.readbackMatched,
    cleanup: receipt.cleanup,
    errorCode: receipt.error?.code,
  });
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
