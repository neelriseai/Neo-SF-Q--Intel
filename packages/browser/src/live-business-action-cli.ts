import { createHash, randomBytes } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import {
  BrowserWorker,
  type BrowserWorkerReceipt,
  type BusinessActionIntent,
} from "./browser-worker.js";
import {
  BrowserCoordinatorError,
  PlaywrightChromiumBrowserFactory,
  loadTrustedLiveBrowserProfile,
} from "./salesforce-browser-coordinator.js";
import { parseStrictBoundedJson } from "./salesforce-cli-session.js";
import {
  NodeSalesforceCliProcessRunner,
  SalesforceCliSessionBroker,
} from "./salesforce-cli-session.js";

type Environment = Readonly<Record<string, string | undefined>>;
type OutputWriter = (value: string) => void;

type BusinessActionRequest = {
  readonly startPath: string;
  readonly businessAction: BusinessActionIntent;
  readonly persistence: PersistenceAssertionRequest;
};

type PersistenceAssertionRequest = {
  readonly objectApiName: string;
  readonly matchField: string;
  readonly matchValue: string;
  readonly assertions: readonly {
    readonly fieldApiName: string;
    readonly value: string;
  }[];
};

type PersistenceAssertionResult = {
  readonly matched: boolean;
  readonly objectApiName: string;
  readonly matchField: string;
  readonly matchValueDigest: string;
  readonly assertedFields: readonly {
    readonly fieldApiName: string;
    readonly valueDigest: string;
    readonly matched: boolean;
  }[];
  readonly errorCode?: string;
};

export async function runLiveBusinessActionCli(
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
    // Operator-only pacing for a watched demo run; bounded so it cannot stall an automated run.
    const slowMoMs = Math.min(
      Math.max(Number.parseInt(environment.NEO_BROWSER_SLOW_MO_MS ?? "0", 10) || 0, 0),
      2000,
    );
    if (!profile.execution.mutationActionsEnabled) {
      throw new BrowserCoordinatorError("BUSINESS_ACTION_NOT_AUTHORIZED");
    }
    const request = businessActionRequest(environment);
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
          slowMoMs: slowMoMs,
        }).launch(),
    });
    const broker = new SalesforceCliSessionBroker(profile.sessionBroker, worker);
    const enrollment = await worker.enroll(profile.enrollment);
    const handoff = await broker.acquire(enrollment);
    const receipt = await worker.execute({
      handoff,
      mode: "BUSINESS_ACTION",
      startPath: request.startPath,
      businessAction: request.businessAction,
      captureLimit: profile.execution.captureLimit,
    });
    const persistence =
      receipt.status === "PASSED"
        ? await verifyPersistence(request.persistence, profile.sessionBroker)
        : persistenceNotRun(request.persistence, "BROWSER_ACTION_NOT_PASSED");
    const projection = liveBusinessActionProjection(receipt, request, persistence);
    write(`${JSON.stringify({ ...projection, headedMode, postSubmit: receipt.postSubmit })}\n`);
    return projection.status === "PASSED" ? 0 : 2;
  } catch (error) {
    write(`${JSON.stringify({
      schemaVersion: "1.0.0",
      status: "BLOCKED",
      errorCode: safeErrorCode(error),
      acceptanceCredit: false,
      releaseEligible: false,
    })}\n`);
    return 2;
  }
}

export function liveBusinessActionProjection(
  receipt: BrowserWorkerReceipt,
  request: BusinessActionRequest,
  persistence: PersistenceAssertionResult = persistenceNotRun(
    request.persistence,
    "PERSISTENCE_NOT_RUN",
  ),
): Record<string, unknown> {
  const status = receipt.status === "PASSED" && persistence.matched ? "PASSED" : "FAILED";
  return Object.freeze({
    schemaVersion: "1.0.0",
    evidencePhase: "LIVE_BUSINESS_ACTION_BROWSER_ACCEPTANCE",
    acceptanceCredit: false,
    releaseEligible: false,
    capabilityId: receipt.capabilityId,
    status,
    browserStatus: receipt.status,
    mode: receipt.mode,
    startPathDigest: digest(request.startPath),
    fieldCount: request.businessAction.fields.length,
    fieldValueDigests: request.businessAction.fields.map((field) => ({
      fieldApiName: field.fieldApiName,
      valueDigest: digest(field.value),
    })),
    submitActionDigest: digest(request.businessAction.submit.expectedValue),
    successTextDigest: digest(request.businessAction.successText),
    lifecycle: receipt.lifecycle,
    candidateCount: receipt.candidateCount,
    businessAction: receipt.businessAction,
    persistence,
    cleanup: receipt.cleanup,
    errorCode: receipt.error?.code,
    inputDigest: receipt.inputDigest,
    executionIdDigest: digest(receipt.executionId),
  });
}

function businessActionRequest(environment: Environment): BusinessActionRequest {
  const startPath = environment.NEO_BROWSER_BUSINESS_START_PATH;
  if (!startPath || !isSafeStartPath(startPath)) {
    throw new BrowserCoordinatorError("BUSINESS_START_PATH_INVALID");
  }
  const fields = parseFields(environment.NEO_BROWSER_BUSINESS_FIELDS_JSON);
  const submitValue = environment.NEO_BROWSER_BUSINESS_SUBMIT_ACTION;
  const successText = environment.NEO_BROWSER_BUSINESS_SUCCESS_TEXT;
  if (
    !submitValue ||
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/.test(submitValue) ||
    !successText ||
    successText.length > 160
  ) {
    throw new BrowserCoordinatorError("BUSINESS_ACTION_REQUEST_INVALID");
  }
  const persistence = parsePersistence(environment.NEO_BROWSER_BUSINESS_PERSISTENCE_JSON);
  return {
    startPath,
    businessAction: {
      objectApiName: persistence.objectApiName,
      fields,
      submit: {
        tag: environment.NEO_BROWSER_BUSINESS_SUBMIT_TAG === "button" ? "button" : "lightning-button",
        attribute: "data-action",
        expectedValue: submitValue,
      },
      successText,
    },
    persistence,
  };
}

function parseFields(raw: string | undefined): BusinessActionIntent["fields"] {
  if (!raw) throw new BrowserCoordinatorError("BUSINESS_FIELDS_INVALID");
  let parsed: unknown;
  try {
    parsed = parseStrictBoundedJson(raw);
  } catch {
    throw new BrowserCoordinatorError("BUSINESS_FIELDS_INVALID");
  }
  if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 8) {
    throw new BrowserCoordinatorError("BUSINESS_FIELDS_INVALID");
  }
  return parsed.map((item) => {
    if (
      !item ||
      typeof item !== "object" ||
      Array.isArray(item) ||
      Object.keys(item).sort().join(",") !== "fieldApiName,value"
    ) {
      throw new BrowserCoordinatorError("BUSINESS_FIELDS_INVALID");
    }
    const value = item as Record<string, unknown>;
    if (typeof value.fieldApiName !== "string" || typeof value.value !== "string") {
      throw new BrowserCoordinatorError("BUSINESS_FIELDS_INVALID");
    }
    return { fieldApiName: value.fieldApiName, value: value.value };
  });
}

function parsePersistence(raw: string | undefined): PersistenceAssertionRequest {
  if (!raw) throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
  let parsed: unknown;
  try {
    parsed = parseStrictBoundedJson(raw);
  } catch {
    throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
  }
  if (
    !parsed ||
    typeof parsed !== "object" ||
    Array.isArray(parsed) ||
    Object.keys(parsed).sort().join(",") !== "assertions,matchField,matchValue,objectApiName"
  ) {
    throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
  }
  const body = parsed as Record<string, unknown>;
  const assertions = body.assertions;
  if (
    typeof body.objectApiName !== "string" ||
    typeof body.matchField !== "string" ||
    typeof body.matchValue !== "string" ||
    !Array.isArray(assertions) ||
    assertions.length < 1 ||
    assertions.length > 8
  ) {
    throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
  }
  const normalized = assertions.map((item) => {
    if (
      !item ||
      typeof item !== "object" ||
      Array.isArray(item) ||
      Object.keys(item).sort().join(",") !== "fieldApiName,value"
    ) {
      throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
    }
    const value = item as Record<string, unknown>;
    if (typeof value.fieldApiName !== "string" || typeof value.value !== "string") {
      throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
    }
    return { fieldApiName: value.fieldApiName, value: value.value };
  });
  const request = {
    objectApiName: body.objectApiName,
    matchField: body.matchField,
    matchValue: body.matchValue,
    assertions: normalized,
  };
  if (!hasSafePersistenceShape(request)) {
    throw new BrowserCoordinatorError("BUSINESS_PERSISTENCE_INVALID");
  }
  return request;
}

async function verifyPersistence(
  request: PersistenceAssertionRequest,
  broker: { targetOrgAlias: string; salesforceExecutable?: string },
): Promise<PersistenceAssertionResult> {
  const fields = [...new Set(["Id", request.matchField, ...request.assertions.map((item) => item.fieldApiName)])];
  const soql = `SELECT ${fields.join(",")} FROM ${request.objectApiName} WHERE ${request.matchField} = '${soqlString(request.matchValue)}' ORDER BY LastModifiedDate DESC LIMIT 1`;
  const runner = new NodeSalesforceCliProcessRunner();
  // On Windows the CLI launches through cmd.exe, where every argument must stay inside a strict
  // token allowlist to prevent interpreter injection. A SOQL string can never satisfy it, so the
  // query travels in a file with an allowlist-safe bare name instead of weakening that guard.
  const queryFileName = `neo-soql-${randomBytes(8).toString("hex")}.txt`;
  try {
    await writeFile(queryFileName, soql, { encoding: "utf8" });
    const result = await runner.run({
      executable: broker.salesforceExecutable ?? (process.platform === "win32" ? "sf.cmd" : "sf"),
      arguments: ["data", "query", "--target-org", broker.targetOrgAlias, "--file", queryFileName, "--json"],
      timeoutMs: 60_000,
      maximumOutputBytes: 128 * 1024,
      environment: { SF_AUTOUPDATE_DISABLE: "true", SF_DISABLE_TELEMETRY: "true" },
    });
    if (result.exitCode !== 0 || result.timedOut || result.outputExceeded) {
      return persistenceNotRun(request, "PERSISTENCE_QUERY_FAILED");
    }
    const parsed = parseStrictBoundedJson(new TextDecoder("utf-8", { fatal: true }).decode(result.stdout));
    const body = parsed as { status?: unknown; result?: { records?: unknown[] } };
    const record = body.status === 0 && Array.isArray(body.result?.records)
      ? body.result.records[0] as Record<string, unknown> | undefined
      : undefined;
    if (!record) return persistenceNotRun(request, "PERSISTENCE_RECORD_NOT_FOUND");
    const assertedFields = request.assertions.map((assertion) => ({
      fieldApiName: assertion.fieldApiName,
      valueDigest: digest(assertion.value),
      matched: String(record[assertion.fieldApiName] ?? "") === assertion.value,
    }));
    return {
      matched: assertedFields.every((item) => item.matched),
      objectApiName: request.objectApiName,
      matchField: request.matchField,
      matchValueDigest: digest(request.matchValue),
      assertedFields,
    };
  } catch {
    return persistenceNotRun(request, "PERSISTENCE_QUERY_FAILED");
  } finally {
    await rm(queryFileName, { force: true }).catch(() => undefined);
  }
}

function persistenceNotRun(
  request: PersistenceAssertionRequest,
  errorCode: string,
): PersistenceAssertionResult {
  return {
    matched: false,
    objectApiName: request.objectApiName,
    matchField: request.matchField,
    matchValueDigest: digest(request.matchValue),
    assertedFields: request.assertions.map((assertion) => ({
      fieldApiName: assertion.fieldApiName,
      valueDigest: digest(assertion.value),
      matched: false,
    })),
    errorCode,
  };
}

function hasSafePersistenceShape(request: PersistenceAssertionRequest): boolean {
  const names = [
    request.objectApiName,
    request.matchField,
    ...request.assertions.map((item) => item.fieldApiName),
  ];
  const values = [request.matchValue, ...request.assertions.map((item) => item.value)];
  return (
    names.every((name) => /^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(name)) &&
    values.every((value) => value.length >= 1 && value.length <= 160 && !containsSensitiveText(value))
  );
}

function soqlString(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function containsSensitiveText(value: string): boolean {
  return (
    /(?:sid=|frontdoor\.jsp|Bearer\s+|Authorization:|api[_-]?key|password|secret|token)/i
      .test(value) ||
    /^[A-Za-z]:[\\/]/.test(value) ||
    value.startsWith("\\\\")
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

function isSafeStartPath(value: string): boolean {
  return (
    value.length >= 1 &&
    value.length <= 256 &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !/[\u0000-\u001f\u007f\\]/.test(value)
  );
}

function safeErrorCode(error: unknown): string {
  if (
    error instanceof BrowserCoordinatorError ||
    (error instanceof Error && /^[A-Z][A-Z0-9_]{2,100}$/.test(error.message))
  ) {
    return error instanceof BrowserCoordinatorError ? error.code : error.message;
  }
  return "LIVE_BUSINESS_ACTION_FAILED";
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

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function constantTimeEqual(left: string, right: string): boolean {
  return digest(left) === digest(right);
}

if (process.argv[1]?.endsWith("live-business-action-cli.js")) {
  const code = await runLiveBusinessActionCli(process.argv.slice(2), process.env, (value) => {
    process.stdout.write(value);
  });
  process.exitCode = code;
}
