import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
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
type ModelProposal = Readonly<Record<string, unknown>>;

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
    const modelProposals = tiers.includes("llm")
      ? await requestModelProposals(target, receipt, environment)
      : [];
    const projection = healingProjection(
      receipt,
      stage as ProbeStage,
      tiers,
      headedMode,
      modelProposals,
    );
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
  modelProposals: readonly (ModelProposal | null)[] = [],
): Record<string, unknown> {
  const report = receipt.probeReport;
  const observations = report?.observations ?? [];
  const projectedObservations = observations.map((item, index) => {
    const proposal = modelProposalForObservation(item, modelProposals[index] ?? null);
    return {
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
      ...(proposal ? { modelProposal: proposal } : {}),
    };
  });
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
    modelDiscoveryAvailable: projectedObservations.some((item) => "modelProposal" in item),
    headedMode,
    obligationCount: report?.obligationCount ?? 0,
    targetDigest: report?.targetDigest ?? null,
    observations: projectedObservations,
    cleanup: receipt.cleanup,
    executionIdDigest: digest(receipt.executionId),
    inputDigest: receipt.inputDigest,
  };
}

function modelProposalForObservation(
  observation: { outcome: string; domEvidence?: unknown },
  proposal: ModelProposal | null,
): ModelProposal | null {
  if (!proposal || !observation.domEvidence) return null;
  return observation.outcome === "LOCATOR_NOT_FOUND" || observation.outcome === "CANDIDATE_AMBIGUOUS"
    ? proposal
    : null;
}

async function requestModelProposals(
  target: unknown,
  receipt: BrowserWorkerReceipt,
  environment: Environment,
): Promise<readonly (ModelProposal | null)[]> {
  const obligations = targetObligations(target);
  const observations = receipt.probeReport?.observations ?? [];
  const results: (ModelProposal | null)[] = [];
  for (let index = 0; index < observations.length; index += 1) {
    const observation = observations[index];
    const obligation = obligations[index];
    if (!obligation || !observation?.domEvidence || obligation.semanticIdentity.kind !== "FIELD") {
      results.push(null);
      continue;
    }
    results.push(await requestModelProposal({
      schemaVersion: "1.0.0",
      obligationId: obligation.obligationId,
      objectApiName: obligation.semanticIdentity.objectApiName,
      fieldApiName: obligation.semanticIdentity.fieldApiName,
      domEvidence: observation.domEvidence,
    }, environment));
  }
  return Object.freeze(results);
}

export async function requestModelProposal(
  payload: Record<string, unknown>,
  environment: Environment,
): Promise<ModelProposal> {
  const python = environment.NEO_LOCATOR_HEALING_PYTHON || environment.PYTHON || "python";
  const timeout = Math.min(
    Math.max(Number.parseInt(environment.NEO_LOCATOR_HEALING_TIMEOUT_MS ?? "45000", 10) || 45000, 1000),
    120000,
  );
  try {
    const stdout = await runPythonBridge(
      python,
      ["-m", "neo_sf_q_intel.locator_healing_cli"],
      JSON.stringify(payload),
      {
        timeout,
        env: locatorBridgeEnvironment(environment),
        cwd: environment.NEO_LOCATOR_HEALING_CWD,
      },
    );
    return parseModelProposal(stdout);
  } catch {
    return Object.freeze({
      schemaVersion: "1.0.0",
      accepted: false,
      rejectionCode: "LOCATOR_HEALING_BRIDGE_FAILED",
    });
  }
}

export async function saveElementSignature(
  payload: Record<string, unknown>,
  environment: Environment,
): Promise<ModelProposal> {
  const python = environment.NEO_LOCATOR_HEALING_PYTHON || environment.PYTHON || "python";
  const timeout = Math.min(
    Math.max(Number.parseInt(environment.NEO_LOCATOR_HEALING_TIMEOUT_MS ?? "45000", 10) || 45000, 1000),
    120000,
  );
  try {
    const stdout = await runPythonBridge(
      python,
      ["-m", "neo_sf_q_intel.locator_healing_cli"],
      JSON.stringify(payload),
      {
        timeout,
        env: locatorBridgeEnvironment(environment),
        cwd: environment.NEO_LOCATOR_HEALING_CWD,
      },
    );
    return parseModelProposal(stdout);
  } catch {
    return Object.freeze({
      schemaVersion: "1.0.0",
      operation: "SAVE_SIGNATURE",
      saved: false,
      status: "BRIDGE_FAILED",
      errorCode: "SIGNATURE_SAVE_BRIDGE_FAILED",
    });
  }
}

function locatorBridgeEnvironment(environment: Environment): NodeJS.ProcessEnv {
  const merged: NodeJS.ProcessEnv = { ...process.env, ...environment };
  if ((environment.NEO_LOCATOR_HEALING_PROVIDER_SOURCE ?? "dotenv").toLowerCase() === "process") {
    return merged;
  }
  for (const name of [
    "AI_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_CHAT_MODEL",
    "OPENAI_EMBEDDING_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
  ]) {
    delete merged[name];
  }
  return merged;
}

function runPythonBridge(
  command: string,
  args: readonly string[],
  input: string,
  options: { timeout: number; env: NodeJS.ProcessEnv; cwd?: string },
): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["pipe", "pipe", "ignore"],
      windowsHide: true,
    });
    const chunks: Buffer[] = [];
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error("LOCATOR_HEALING_BRIDGE_TIMEOUT"));
    }, options.timeout);
    child.stdout.on("data", (chunk: Buffer) => {
      if (chunks.reduce((total, item) => total + item.length, 0) + chunk.length <= 256 * 1024) {
        chunks.push(chunk);
      }
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error("LOCATOR_HEALING_BRIDGE_EXITED"));
        return;
      }
      resolve(Buffer.concat(chunks).toString("utf8"));
    });
    child.stdin.end(input);
  });
}

function parseModelProposal(stdout: string): ModelProposal {
  try {
    const body = JSON.parse(stdout.trim());
    if (body !== null && typeof body === "object" && !Array.isArray(body)) {
      return Object.freeze(body as Record<string, unknown>);
    }
  } catch {
    // Fall through to the safe blocked record.
  }
  return Object.freeze({
    schemaVersion: "1.0.0",
    accepted: false,
    rejectionCode: "LOCATOR_HEALING_BRIDGE_RESPONSE_INVALID",
  });
}

type TargetObligation = Readonly<{
  obligationId: string;
  semanticIdentity:
    | Readonly<{ kind: "FIELD"; objectApiName: string; fieldApiName: string }>
    | Readonly<{ kind: "ACTION"; objectApiName: string; action: string }>;
}>;

function targetObligations(value: unknown): readonly TargetObligation[] {
  if (value === null || typeof value !== "object") return [];
  const obligations = (value as { obligations?: unknown }).obligations;
  if (!Array.isArray(obligations)) return [];
  return obligations.flatMap((item): TargetObligation[] => {
    if (item === null || typeof item !== "object") return [];
    const body = item as {
      obligationId?: unknown;
      semanticIdentity?: unknown;
    };
    if (typeof body.obligationId !== "string" || !body.semanticIdentity) return [];
    const identity = body.semanticIdentity as {
      kind?: unknown;
      objectApiName?: unknown;
      fieldApiName?: unknown;
      action?: unknown;
    };
    if (identity.kind === "FIELD" &&
      typeof identity.objectApiName === "string" &&
      typeof identity.fieldApiName === "string") {
      return [{
        obligationId: body.obligationId,
        semanticIdentity: {
          kind: "FIELD",
          objectApiName: identity.objectApiName,
          fieldApiName: identity.fieldApiName,
        },
      }];
    }
    if (identity.kind === "ACTION" &&
      typeof identity.objectApiName === "string" &&
      typeof identity.action === "string") {
      return [{
        obligationId: body.obligationId,
        semanticIdentity: {
          kind: "ACTION",
          objectApiName: identity.objectApiName,
          action: identity.action,
        },
      }];
    }
    return [];
  });
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
