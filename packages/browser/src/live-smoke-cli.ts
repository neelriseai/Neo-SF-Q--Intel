import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  BrowserCoordinatorError,
  SalesforceBrowserCoordinator,
  loadTrustedLiveBrowserProfile,
} from "./salesforce-browser-coordinator.js";
import type { BrowserWorkerReceipt } from "./browser-worker.js";

type Environment = Readonly<Record<string, string | undefined>>;
type OutputWriter = (value: string) => void;

export async function runLiveSmokeCli(
  arguments_: readonly string[],
  environment: Environment,
  write: OutputWriter,
): Promise<number> {
  try {
    const raw = await loadProfileBytes(arguments_, environment);
    const expectedDigest = environment.NEO_BROWSER_PROFILE_SHA256;
    if (!expectedDigest) throw new BrowserCoordinatorError("PROFILE_NOT_TRUSTED");
    const profile = loadTrustedLiveBrowserProfile(raw, expectedDigest);
    const receipt = await new SalesforceBrowserCoordinator(profile).runReadOnlySmoke();
    write(
      `${JSON.stringify(liveSmokeProjection(receipt))}\n`,
    );
    return receipt.status === "PASSED" ? 0 : 2;
  } catch (error) {
    const code = safeErrorCode(error);
    write(`${JSON.stringify({ schemaVersion: "1.0.0", status: "BLOCKED", errorCode: code })}\n`);
    return 2;
  }
}

export function liveSmokeProjection(receipt: BrowserWorkerReceipt): Record<string, unknown> {
  return Object.freeze({
    schemaVersion: "1.0.0",
    diagnosticOnly: true,
    releaseEligible: false,
    evidencePhase: null,
    capabilityId: receipt.capabilityId,
    status: receipt.status,
    executionIdDigest: digest(receipt.executionId),
    inputDigest: receipt.inputDigest,
    policyDigest: receipt.enrollment.policyDigest,
    cleanup: receipt.cleanup,
    errorCode: receipt.error?.code,
  });
}

async function loadProfileBytes(
  arguments_: readonly string[],
  environment: Environment,
): Promise<Uint8Array> {
  const inline = environment.NEO_BROWSER_LIVE_PROFILE_JSON;
  const environmentPath = environment.NEO_BROWSER_LIVE_PROFILE_PATH;
  let argumentPath: string | undefined;
  if (arguments_.length === 2 && arguments_[0] === "--profile") {
    argumentPath = arguments_[1];
  } else if (arguments_.length !== 0) {
    throw new BrowserCoordinatorError("CLI_ARGUMENTS_INVALID");
  }
  const sources = [inline, environmentPath, argumentPath].filter(
    (value): value is string => Boolean(value),
  );
  if (sources.length !== 1) throw new BrowserCoordinatorError("PROFILE_SOURCE_INVALID");
  if (inline) return Buffer.from(inline, "utf8");
  try {
    return await readFile(environmentPath ?? argumentPath!);
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
  return "LIVE_SMOKE_FAILED";
}

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

const invokedPath = process.argv[1];
if (invokedPath && fileURLToPath(import.meta.url) === resolve(invokedPath)) {
  const exitCode = await runLiveSmokeCli(process.argv.slice(2), process.env, (value) => {
    process.stdout.write(value);
  });
  process.exitCode = exitCode;
}
