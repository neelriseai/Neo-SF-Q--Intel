import { createHash, timingSafeEqual } from "node:crypto";
import { chromium, type Browser } from "playwright";
import {
  BrowserWorker,
  type BrowserWorkerRequest,
  type BrowserWorkerReceipt,
  type EphemeralSessionHandoff,
  type TrustedEnrollmentAssertion,
  type TrustedEnrollmentHandle,
} from "./browser-worker.js";
import {
  SalesforceCliSessionBroker,
  type SalesforceCliProcessRunner,
  type SalesforceCliSessionBrokerConfig,
  parseStrictBoundedJson,
} from "./salesforce-cli-session.js";

const MAX_PROFILE_BYTES = 1024 * 1024;

export interface TrustedLiveBrowserProfile {
  readonly schemaVersion: "1.0.0";
  readonly profileId: string;
  readonly profileVersion: string;
  readonly enrollment: TrustedEnrollmentAssertion;
  readonly sessionBroker: SalesforceCliSessionBrokerConfig;
  readonly browser: {
    readonly headless: true;
    readonly launchTimeoutMs: number;
    readonly navigationTimeoutMs: number;
    readonly operationTimeoutMs: number;
  };
  readonly execution: {
    readonly mode: "READ_ONLY_DOM_CAPTURE";
    readonly captureLimit: number;
    readonly mutationActionsEnabled: boolean;
  };
}

export interface ProductionBrowserFactory {
  launch(): Promise<Browser>;
}

export class PlaywrightChromiumBrowserFactory implements ProductionBrowserFactory {
  constructor(
    private readonly options: { headless: boolean; launchTimeoutMs: number; slowMoMs?: number },
  ) {}

  launch(): Promise<Browser> {
    return chromium.launch({
      headless: this.options.headless,
      timeout: this.options.launchTimeoutMs,
      slowMo: this.options.slowMoMs,
    });
  }
}

export class BrowserCoordinatorError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "BrowserCoordinatorError";
  }
}

export class SalesforceBrowserCoordinator {
  readonly #delegate: ReadOnlyBrowserSessionCoordinator;

  constructor(
    profile: TrustedLiveBrowserProfile,
    options: {
      processRunner?: SalesforceCliProcessRunner;
      browserFactory?: ProductionBrowserFactory;
    } = {},
  ) {
    const browserFactory =
      options.browserFactory ??
      new PlaywrightChromiumBrowserFactory({
        headless: true,
        launchTimeoutMs: profile.browser.launchTimeoutMs,
      });
    const expectedEnrollment = canonicalJson(profile.enrollment);
    const worker = new BrowserWorker({
      verifyEnrollment: (assertion) =>
        constantTimeEqual(canonicalJson(assertion), expectedEnrollment),
      allowedFrontdoorHostnameSuffixes:
        profile.sessionBroker.allowedFrontdoorHostnameSuffixes,
      allowedLightningHostnameSuffixes:
        profile.sessionBroker.allowedLightningHostnameSuffixes,
      navigationTimeoutMs: profile.browser.navigationTimeoutMs,
      operationTimeoutMs: profile.browser.operationTimeoutMs,
      launchBrowser: () => browserFactory.launch(),
    });
    const broker = new SalesforceCliSessionBroker(
      profile.sessionBroker,
      worker,
      options.processRunner,
    );
    this.#delegate = new ReadOnlyBrowserSessionCoordinator(profile, worker, broker);
  }

  async runReadOnlySmoke(): Promise<BrowserWorkerReceipt> {
    return this.#delegate.run();
  }
}

interface ReadOnlyWorkerPort {
  enroll(assertion: TrustedEnrollmentAssertion): Promise<TrustedEnrollmentHandle>;
  execute(request: BrowserWorkerRequest): Promise<BrowserWorkerReceipt>;
}

interface SessionAcquisitionPort {
  acquire(handle: TrustedEnrollmentHandle): Promise<EphemeralSessionHandoff>;
}

export class ReadOnlyBrowserSessionCoordinator {
  constructor(
    private readonly profile: TrustedLiveBrowserProfile,
    private readonly worker: ReadOnlyWorkerPort,
    private readonly broker: SessionAcquisitionPort,
  ) {}

  async run(): Promise<BrowserWorkerReceipt> {
    const enrollment = await this.worker.enroll(this.profile.enrollment);
    const handoff = await this.broker.acquire(enrollment);
    return this.worker.execute({
      handoff,
      mode: "READ_ONLY_DOM_CAPTURE",
      captureLimit: this.profile.execution.captureLimit,
    });
  }
}

export function loadTrustedLiveBrowserProfile(
  raw: Uint8Array,
  expectedSha256: string,
): TrustedLiveBrowserProfile {
  if (raw.byteLength < 2 || raw.byteLength > MAX_PROFILE_BYTES || !isSha256(expectedSha256)) {
    throw new BrowserCoordinatorError("PROFILE_NOT_TRUSTED");
  }
  const actual = createHash("sha256").update(raw).digest("hex");
  if (!constantTimeEqual(actual, expectedSha256)) {
    throw new BrowserCoordinatorError("PROFILE_NOT_TRUSTED");
  }
  let value: unknown;
  try {
    value = parseStrictBoundedJson(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  try {
    return validateProfile(value);
  } catch (error) {
    if (error instanceof BrowserCoordinatorError) throw error;
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
}

function validateProfile(value: unknown): TrustedLiveBrowserProfile {
  const profile = exactRecord(value, [
    "schemaVersion",
    "profileId",
    "profileVersion",
    "enrollment",
    "sessionBroker",
    "browser",
    "execution",
  ]);
  if (
    profile.schemaVersion !== "1.0.0" ||
    !isIdentifier(profile.profileId) ||
    !isSemver(profile.profileVersion)
  ) {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  const enrollment = validateEnrollment(profile.enrollment);
  const sessionBroker = validateSessionBroker(profile.sessionBroker);
  const browser = exactRecord(profile.browser, [
    "headless",
    "launchTimeoutMs",
    "navigationTimeoutMs",
    "operationTimeoutMs",
  ]);
  const execution = exactRecord(profile.execution, [
    "mode",
    "captureLimit",
    "mutationActionsEnabled",
  ]);
  if (
    browser.headless !== true ||
    execution.mode !== "READ_ONLY_DOM_CAPTURE" ||
    typeof execution.mutationActionsEnabled !== "boolean" ||
    !isIntegerBetween(execution.captureLimit, 1, 100)
  ) {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  const validatedBrowser = {
    headless: true as const,
    launchTimeoutMs: boundedInteger(browser.launchTimeoutMs, 1_000, 120_000),
    navigationTimeoutMs: boundedInteger(browser.navigationTimeoutMs, 1_000, 120_000),
    operationTimeoutMs: boundedInteger(browser.operationTimeoutMs, 1_000, 120_000),
  };
  if (
    enrollment.frontdoorOrigin !== sessionBroker.frontdoorOrigin ||
    enrollment.lightningOrigin !== sessionBroker.lightningOrigin ||
    !equalStrings(enrollment.navigationBridgeOrigins, sessionBroker.navigationBridgeOrigins) ||
    !equalStrings(enrollment.resourceOrigins, sessionBroker.resourceOrigins) ||
    enrollment.orgBinding !== sessionBroker.orgBinding ||
    enrollment.actorBinding !== sessionBroker.actorBinding ||
    !enrollment.permittedModes.includes("READ_ONLY_DOM_CAPTURE") ||
    (execution.mutationActionsEnabled && !enrollment.permittedModes.includes("BUSINESS_ACTION"))
  ) {
    throw new BrowserCoordinatorError("PROFILE_BINDING_MISMATCH");
  }
  return Object.freeze({
    schemaVersion: "1.0.0",
    profileId: profile.profileId,
    profileVersion: profile.profileVersion,
    enrollment,
    sessionBroker,
    browser: Object.freeze(validatedBrowser),
    execution: Object.freeze({
      mode: "READ_ONLY_DOM_CAPTURE",
      captureLimit: execution.captureLimit,
      mutationActionsEnabled: execution.mutationActionsEnabled,
    }),
  });
}

function validateEnrollment(value: unknown): TrustedEnrollmentAssertion {
  const enrollment = exactRecord(value, [
    "enrollmentId",
    "issuer",
    "classification",
    "frontdoorOrigin",
    "lightningOrigin",
    "navigationBridgeOrigins",
    "resourceOrigins",
    "orgBinding",
    "actorBinding",
    "policyDigest",
    "issuedAt",
    "expiresAt",
    "permittedModes",
  ]);
  if (
    !isIdentifier(enrollment.enrollmentId) ||
    !isIdentifier(enrollment.issuer) ||
    enrollment.classification !== "NON_PRODUCTION" ||
    typeof enrollment.frontdoorOrigin !== "string" ||
    typeof enrollment.lightningOrigin !== "string" ||
    !isStringArray(enrollment.navigationBridgeOrigins) ||
    !isStringArray(enrollment.resourceOrigins) ||
    !isBinding(enrollment.orgBinding) ||
    !isBinding(enrollment.actorBinding) ||
    !isSha256(enrollment.policyDigest) ||
    typeof enrollment.issuedAt !== "string" ||
    typeof enrollment.expiresAt !== "string" ||
    !Array.isArray(enrollment.permittedModes) ||
    enrollment.permittedModes.length < 1 ||
    enrollment.permittedModes.some(
      (mode) =>
        mode !== "READ_ONLY_DOM_CAPTURE" &&
        mode !== "CANDIDATE_READBACK" &&
        mode !== "BUSINESS_ACTION" &&
        mode !== "LOCATOR_PROBE",
    )
  ) {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  return Object.freeze({
    enrollmentId: enrollment.enrollmentId,
    issuer: enrollment.issuer,
    classification: "NON_PRODUCTION",
    frontdoorOrigin: enrollment.frontdoorOrigin,
    lightningOrigin: enrollment.lightningOrigin,
    navigationBridgeOrigins: Object.freeze([...enrollment.navigationBridgeOrigins]),
    resourceOrigins: Object.freeze([...enrollment.resourceOrigins]),
    orgBinding: enrollment.orgBinding,
    actorBinding: enrollment.actorBinding,
    policyDigest: enrollment.policyDigest,
    issuedAt: enrollment.issuedAt,
    expiresAt: enrollment.expiresAt,
    permittedModes: Object.freeze([...enrollment.permittedModes]),
  });
}

function validateSessionBroker(value: unknown): SalesforceCliSessionBrokerConfig {
  const broker = exactRecord(value, [
    "targetOrgAlias",
    "frontdoorOrigin",
    "lightningOrigin",
    "navigationBridgeOrigins",
    "resourceOrigins",
    "orgBinding",
    "actorBinding",
    "actorBindingSource",
    "salesforceExecutable",
    "allowedFrontdoorHostnameSuffixes",
    "allowedLightningHostnameSuffixes",
    "maximumOutputBytes",
    "timeoutMs",
  ]);
  if (
    typeof broker.targetOrgAlias !== "string" ||
    typeof broker.frontdoorOrigin !== "string" ||
    typeof broker.lightningOrigin !== "string" ||
    !Array.isArray(broker.navigationBridgeOrigins) ||
    !Array.isArray(broker.resourceOrigins) ||
    !isBinding(broker.orgBinding) ||
    !isBinding(broker.actorBinding) ||
    (broker.actorBindingSource !== "USERNAME" && broker.actorBindingSource !== "USER_ID") ||
    (broker.salesforceExecutable !== undefined &&
      typeof broker.salesforceExecutable !== "string") ||
    !isStringArrayOrUndefined(broker.allowedFrontdoorHostnameSuffixes) ||
    !isStringArrayOrUndefined(broker.allowedLightningHostnameSuffixes) ||
    (broker.maximumOutputBytes !== undefined &&
      typeof broker.maximumOutputBytes !== "number") ||
    (broker.timeoutMs !== undefined && typeof broker.timeoutMs !== "number")
  ) {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  return Object.freeze({
    targetOrgAlias: broker.targetOrgAlias,
    frontdoorOrigin: broker.frontdoorOrigin,
    lightningOrigin: broker.lightningOrigin,
    navigationBridgeOrigins: Object.freeze([...(broker.navigationBridgeOrigins as string[])]),
    resourceOrigins: Object.freeze([...(broker.resourceOrigins as string[])]),
    orgBinding: broker.orgBinding,
    actorBinding: broker.actorBinding,
    actorBindingSource: broker.actorBindingSource,
    salesforceExecutable: broker.salesforceExecutable,
    allowedFrontdoorHostnameSuffixes: broker.allowedFrontdoorHostnameSuffixes,
    allowedLightningHostnameSuffixes: broker.allowedLightningHostnameSuffixes,
    maximumOutputBytes: broker.maximumOutputBytes,
    timeoutMs: broker.timeoutMs,
  });
}

function exactRecord(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!isRecord(value)) throw new BrowserCoordinatorError("PROFILE_INVALID");
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  return value;
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number {
  if (!isIntegerBetween(value, minimum, maximum)) {
    throw new BrowserCoordinatorError("PROFILE_INVALID");
  }
  return value;
}

function isIntegerBetween(value: unknown, minimum: number, maximum: number): value is number {
  return Number.isInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isIdentifier(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function isBinding(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/.test(value);
}

function isSemver(value: unknown): value is string {
  return typeof value === "string" && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function isStringArrayOrUndefined(value: unknown): value is readonly string[] | undefined {
  return value === undefined || (Array.isArray(value) && value.every((item) => typeof item === "string"));
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function equalStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function constantTimeEqual(left: string, right: string): boolean {
  return timingSafeEqual(Buffer.from(digest(left), "hex"), Buffer.from(digest(right), "hex"));
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
