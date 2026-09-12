import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { BrowserWorker, type BrowserWorkerReceipt } from "./browser-worker.js";
import type { ProbeStage } from "./healing-probe.js";
import {
  BrowserCoordinatorError,
  PlaywrightChromiumBrowserFactory,
  loadTrustedLiveBrowserProfile,
} from "./salesforce-browser-coordinator.js";
import {
  SalesforceCliSessionBroker,
  parseStrictBoundedJson,
} from "./salesforce-cli-session.js";

type Environment = Readonly<Record<string, string | undefined>>;
type OutputWriter = (value: string) => void;

const STAGES: readonly string[] = ["BASELINE", "STALE_AND_DISCOVER", "RERUN"];
const TIERS: readonly string[] = ["metadata", "llm"];
const UNSAFE_PATH = /[\u0000-\u001f\u007f\\]/;

/**
 * Host-owned staged locator-healing probe against the enrolled org. The probe is read-only: it
 * never clicks, fills, submits or saves, and only digests leave this process.
 */
export async function runLiveHealingCli(
  arguments_: readonly string[],
  environment: Environment,
  write: OutputWriter,
): Promise<number> {
  try {
    if (arguments_.length !== 0) throw new BrowserCoordinatorError("CLI_ARGUMENTS_INVALID");
    const raw = await loadProfileBytes(environment);
    const expectedDigest = environment.NEO_BROWSER_PROFILE_SHA256;
    if (!expectedDigest) throw new BrowserCoordinatorError("PROFILE_NOT_TRUSTED");
    const profile = loadTrustedLiveBrowserProfile(raw, expectedDigest);
    if (!profile.enrollment.permittedModes.includes("LOCATOR_PROBE")) {
      throw new BrowserCoordinatorError("LOCATOR_PROBE_NOT_AUTHORIZED");
    }

    // Operator-only presentation controls; they change only the local launch surface.
    const headedMode = environment.NEO_BROWSER_HEADED === "true";
    const slowMoMs = Math.min(
      Math.max(Number.parseInt(environment.NEO_BROWSER_SLOW_MO_MS ?? "0", 10) || 0, 0),
      2000,
    );

    // Ordered discovery tiers. Switching tiers never disables deterministic verification: the
    // probe still requires a unique, visible, enabled, metadata-scoped candidate.
    const tiers = parseTiers(environment.NEO_HEAL_TIERS);
    const stage = environment.NEO_BROWSER_PROBE_STAGE;
    if (!stage || !STAGES.includes(stage)) {
      throw new BrowserCoordinatorError("PROBE_STAGE_INVALID");
    }
    const startPath = environment.NEO_BROWSER_PROBE_START_PATH;
    if (!startPath || !isSafeStartPath(startPath)) {
      throw new BrowserCoordinatorError("PROBE_START_PATH_INVALID");
    }
    const target = parseTarget(environment.NEO_BROWSER_PROBE_TARGET_JSON);

    const expectedEnrollment = canonicalJson(profile.enrollment);
    const worker = new BrowserWorker({
      verifyEnrollment: (assertion) =>
        constantTimeEqual(canonicalJson(assertion), expectedEnrollment),
      allowedFrontdoorHostnameSuffixes: profile.sessionBroker.allowedFrontdoorHostnameSuffixes,
      allowedLightningHostnameSuffixes: profile.sessionBroker.allowedLightningHostnameSuffixes,
      navigationTimeoutMs: profile.browser.navigationTimeoutMs,
      operationTimeoutMs: profile.browser.operationTimeoutMs,
      launchBrowser: () =>
        new PlaywrightChromiumBrowserFactory({
          headless: !headedMode,
          launchTimeoutMs: profile.browser.launchTimeoutMs,
          slowMoMs,
        }).launch(),
    });
    const broker = new SalesforceCliSessionBroker(profile.sessionBroker, worker);
    const enrollment = await worker.enroll(profile.enrollment);
    const handoff = await broker.acquire(enrollment);
    // The model tier only ever receives a sanitized, digested candidate list, and only when the
    // deterministic tier abstains. Requesting it never relaxes deterministic verification.
    const probe = {
      target,
      stage: stage as ProbeStage,
      captureCandidates: tiers.includes("llm"),
    };
    const receipt = await worker.execute({
      handoff,
      mode: "LOCATOR_PROBE",
      startPath,
      probe,
      captureLimit: profile.execution.captureLimit,
    });
    const projection = healingProjection(receipt, stage as ProbeStage, tiers, headedMode);
    write(`${JSON.stringify(projection)}\n`);
    return receipt.status === "PASSED" ? 0 : 2;
  } catch (error) {
    write(
      `${JSON.stringify({
        schemaVersion: "1.0.0",
        status: "BLOCKED",
        errorCode: safeErrorCode(error),
        acceptanceCredit: false,
        releaseEligible: false,
      })}\n`,
    );
    return 2;
  }
}

export function healingProjection(
  receipt: BrowserWorkerReceipt,
  stage: ProbeStage,
  tiers: readonly string[],
  headedMode: boolean,
): Record<string, unknown> {
  const report = receipt.probeReport;
  return {
    schemaVersion: "1.0.0",
    evidencePhase: "LIVE_LOCATOR_HEALING_PROBE",
    capabilityId: "automation.locator-healing",
    // Diagnostic only: no signed observation bridge exists, so this earns no gate credit.
    acceptanceCredit: false,
    releaseEligible: false,
    diagnosticOnly: true,
    stage,
    status: receipt.status,
    // Discovery tiers are configurable; deterministic verification always runs.
    healTiers: tiers,
    deterministicDiscoveryEnabled: tiers.includes("metadata"),
    modelDiscoveryRequested: tiers.includes("llm"),
    modelDiscoveryAvailable: false,
    headedMode,
    obligationCount: report?.obligationCount ?? 0,
    targetDigest: report?.targetDigest ?? null,
    observations: (report?.observations ?? []).map((item) => ({
      obligationIdDigest: item.obligationIdDigest,
      outcome: item.outcome,
      staleOriginal: item.staleOriginal,
      candidateCount: item.candidateCount,
      resolvedByTier: item.outcome === "PASSED" && stage !== "BASELINE" ? "metadata" : null,
      visible: item.visible ?? null,
      enabled: item.enabled ?? null,
      editable: item.editable ?? null,
      // Present only when the deterministic tier abstained and candidate capture was requested.
      ...(item.domEvidence ? { domEvidence: item.domEvidence } : {}),
    })),
    cleanup: receipt.cleanup,
    executionIdDigest: digest(receipt.executionId),
    inputDigest: receipt.inputDigest,
  };
}

function parseTiers(value: string | undefined): readonly string[] {
  const requested = (value ?? "metadata")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length > 0);
  if (requested.length === 0 || requested.some((item) => !TIERS.includes(item))) {
    throw new BrowserCoordinatorError("HEAL_TIERS_INVALID");
  }
  return Object.freeze([...new Set(requested)]);
}

function parseTarget(value: string | undefined): unknown {
  if (!value) throw new BrowserCoordinatorError("PROBE_TARGET_INVALID");
  try {
    return parseStrictBoundedJson(value);
  } catch {
    throw new BrowserCoordinatorError("PROBE_TARGET_INVALID");
  }
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

function isSafeStartPath(value: string): boolean {
  return (
    value.length >= 1 &&
    value.length <= 256 &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !UNSAFE_PATH.test(value)
  );
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(value, Object.keys(value as object).sort());
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let delta = 0;
  for (let index = 0; index < left.length; index += 1) {
    delta |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return delta === 0;
}

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function safeErrorCode(error: unknown): string {
  if (error instanceof BrowserCoordinatorError) return error.code;
  if (error instanceof Error && /^[A-Z][A-Z0-9_]{2,100}$/.test(error.message)) return error.message;
  return "LIVE_HEALING_FAILED";
}

const invokedPath = process.argv[1];
if (invokedPath && invokedPath.endsWith("live-healing-cli.js")) {
  const exitCode = await runLiveHealingCli(process.argv.slice(2), process.env, (value) => {
    process.stdout.write(value);
  });
  process.exitCode = exitCode;
}
