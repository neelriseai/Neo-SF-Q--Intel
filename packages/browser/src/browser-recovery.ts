import {
  createHash,
  createHmac,
  randomUUID,
  timingSafeEqual,
} from "node:crypto";
import { mkdir, open } from "node:fs/promises";
import { join } from "node:path";
import type { Page } from "playwright";
import type { BrowserWorkerReceipt, CandidateIntent } from "./browser-worker.js";

type CandidateRole = CandidateIntent["role"];
type StateAttribute = NonNullable<CandidateIntent["readback"]>["attribute"];

export interface BrowserRecoveryScope {
  campaignId: string;
  projectId: string;
  sourceContractSha256: string;
  candidateSha256: string;
  buildSha256: string;
  operationPlanSha256: string;
  restoreScopeSha256: string;
  policySha256: string;
  profileSha256: string;
  orgFingerprintSha256: string;
  actorFingerprintSha256: string;
  lightningOriginSha256: string;
  expectedExecutionContractSha256: string;
}

export interface BrowserRecoveryPermitPayload {
  schemaVersion: "1.0.0";
  permitId: string;
  authorityClass: "INDEPENDENT_BROWSER_RECOVERY_AUTHORITY";
  scope: BrowserRecoveryScope;
  candidateBinding: {
    workerExecutionId: string;
    workerInputDigest: string;
    candidateCount: 1;
    role: CandidateRole;
    accessibleNameSha256: string;
    stateAttribute: StateAttribute;
    preconditionSha256: string;
    expectedAfterSha256: string;
    enrollmentOriginDigest: string;
    enrollmentOrgBindingDigest: string;
    enrollmentActorBindingDigest: string;
  };
  permittedAction: "CLICK_REVERSIBLE_TOGGLE";
  beforeAssertion: "EXACT_PRECONDITION";
  afterAssertion: "EXACT_EXPECTED_AND_CHANGED";
  restoration: "REQUIRED_EXACT_PRECONDITION";
  cleanup: "REQUIRED_EPHEMERAL_SESSION_CLOSE";
  issuedAt: string;
  expiresAt: string;
}

export interface SignedBrowserRecoveryPermit {
  payload: BrowserRecoveryPermitPayload;
  issuerId: string;
  payloadSha256: string;
  signatureSha256: string;
}

export interface BrowserRecoveryRequest {
  permit: SignedBrowserRecoveryPermit;
  candidateReceipt: BrowserWorkerReceipt;
  candidate: { role: CandidateRole; accessibleName: string };
  preconditionValue: string;
  expectedAfterValue: string;
}

export type BrowserRecoveryStatus = "PASSED" | "FAILED" | "BLOCKED";

export interface SignedBrowserRecoveryEvidence {
  payload: {
    schemaVersion: "1.0.0";
    executionId: string;
    permitId: string;
    permitSha256: string;
    scopeSha256: string;
    workerExecutionId: string;
    workerInputDigest: string;
    action: "CLICK_REVERSIBLE_TOGGLE";
    status: BrowserRecoveryStatus;
    lifecycle: readonly (
      | "CANDIDATE_DISCOVERED"
      | "PROPOSAL_APPROVED"
      | "ACTION_APPLIED"
      | "OUTCOME_VERIFIED"
    )[];
    beforeSha256?: string;
    afterSha256?: string;
    restoredSha256?: string;
    assertions: {
      identityBeforeActionMatched: boolean;
      identityBeforeRestoreMatched: boolean;
      uniqueCurrentCandidate: boolean;
      preconditionMatched: boolean;
      changedAsExpected: boolean;
      restoredExactly: boolean;
      sessionClosed: boolean;
    };
    errorCode?: string;
    terminalAt: string;
  };
  issuerId: string;
  payloadSha256: string;
  signatureSha256: string;
}

export interface BrowserRecoverySession {
  page: Page;
  /** Host session broker reads the currently authenticated identity; never request data. */
  observeIdentity: () => Promise<BrowserRecoveryIdentityObservation>;
  close: () => Promise<void>;
}

export interface BrowserRecoveryIdentityObservation {
  lightningOriginSha256: string;
  orgFingerprintSha256: string;
  actorFingerprintSha256: string;
  observedAt: string;
  expiresAt: string;
}

export interface BrowserRecoveryExecutorOptions {
  expectedScope: BrowserRecoveryScope;
  trustedAuthorityKeys: ReadonlyMap<string, Uint8Array>;
  evidenceIssuer: { issuerId: string; hmacKey: Uint8Array };
  openSession: () => Promise<BrowserRecoverySession>;
  permitLedger: BrowserRecoveryPermitLedger;
  now?: () => number;
  operationTimeoutMs?: number;
}

export interface BrowserRecoveryPermitLedger {
  claim(permitSha256: string, expiresAt: string): Promise<boolean>;
}

export class FileBrowserRecoveryPermitLedger implements BrowserRecoveryPermitLedger {
  constructor(private readonly directory: string) {
    if (!directory || /(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+|password\s*[=:])/i.test(directory)) {
      throw new BrowserRecoveryError("RECOVERY_LEDGER_CONFIGURATION_INVALID");
    }
  }

  async claim(permitSha256: string, expiresAt: string): Promise<boolean> {
    if (!SHA256.test(permitSha256) || !Number.isFinite(Date.parse(expiresAt))) {
      throw new BrowserRecoveryError("RECOVERY_PERMIT_CLAIM_INVALID");
    }
    await mkdir(this.directory, { recursive: true });
    try {
      const handle = await open(join(this.directory, `${permitSha256}.claim`), "wx", 0o600);
      try {
        await handle.writeFile(
          canonicalJson({ schemaVersion: "1.0.0", permitSha256, expiresAt }),
          "utf8",
        );
        await handle.sync();
      } finally {
        await handle.close();
      }
      return true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST") return false;
      throw new BrowserRecoveryError("RECOVERY_PERMIT_CLAIM_FAILED");
    }
  }
}

export class BrowserRecoveryError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "BrowserRecoveryError";
  }
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const MAX_PERMIT_LIFETIME_MS = 5 * 60 * 1000;

export function signBrowserRecoveryPermit(
  payload: BrowserRecoveryPermitPayload,
  issuerId: string,
  hmacKey: Uint8Array,
): SignedBrowserRecoveryPermit {
  validatePermitPayload(payload);
  if (!IDENTIFIER.test(issuerId) || hmacKey.byteLength < 32) {
    throw new BrowserRecoveryError("RECOVERY_SIGNER_INVALID");
  }
  const payloadSha256 = digest(canonicalJson(payload));
  return Object.freeze({
    payload,
    issuerId,
    payloadSha256,
    signatureSha256: hmac(payloadSha256, hmacKey),
  });
}

export function verifyBrowserRecoveryEvidence(
  evidence: SignedBrowserRecoveryEvidence,
  expectedIssuerId: string,
  hmacKey: Uint8Array,
): boolean {
  try {
    return (
      evidence.issuerId === expectedIssuerId &&
      evidence.payloadSha256 === digest(canonicalJson(evidence.payload)) &&
      safeEqual(evidence.signatureSha256, hmac(evidence.payloadSha256, hmacKey))
    );
  } catch {
    return false;
  }
}

export class BrowserRecoveryExecutor {
  readonly #expectedScope: BrowserRecoveryScope;
  readonly #trustedAuthorityKeys: ReadonlyMap<string, Uint8Array>;
  readonly #evidenceIssuer: BrowserRecoveryExecutorOptions["evidenceIssuer"];
  readonly #openSession: BrowserRecoveryExecutorOptions["openSession"];
  readonly #permitLedger: BrowserRecoveryPermitLedger;
  readonly #now: () => number;
  readonly #operationTimeoutMs: number;

  constructor(options: BrowserRecoveryExecutorOptions) {
    validateScope(options.expectedScope);
    if (
      !IDENTIFIER.test(options.evidenceIssuer.issuerId) ||
      options.evidenceIssuer.hmacKey.byteLength < 32 ||
      options.trustedAuthorityKeys.size < 1 ||
      options.trustedAuthorityKeys.size > 16
    ) {
      throw new BrowserRecoveryError("RECOVERY_CONFIGURATION_INVALID");
    }
    this.#expectedScope = Object.freeze({ ...options.expectedScope });
    this.#trustedAuthorityKeys = options.trustedAuthorityKeys;
    this.#evidenceIssuer = options.evidenceIssuer;
    this.#openSession = options.openSession;
    if (!options.permitLedger || typeof options.permitLedger.claim !== "function") {
      throw new BrowserRecoveryError("RECOVERY_CONFIGURATION_INVALID");
    }
    this.#permitLedger = options.permitLedger;
    this.#now = options.now ?? Date.now;
    this.#operationTimeoutMs = options.operationTimeoutMs ?? 10_000;
    if (
      !Number.isInteger(this.#operationTimeoutMs) ||
      this.#operationTimeoutMs < 100 ||
      this.#operationTimeoutMs > 30_000
    ) {
      throw new BrowserRecoveryError("RECOVERY_CONFIGURATION_INVALID");
    }
  }

  async execute(request: BrowserRecoveryRequest): Promise<SignedBrowserRecoveryEvidence> {
    const snapshot = immutableRequestSnapshot(request);
    const permit = this.#authorize(snapshot);
    let claimed = false;
    try {
      claimed = await this.#permitLedger.claim(snapshot.permit.payloadSha256, permit.expiresAt);
    } catch {
      throw new BrowserRecoveryError("RECOVERY_PERMIT_CLAIM_FAILED");
    }
    if (!claimed) {
      throw new BrowserRecoveryError("RECOVERY_PERMIT_ALREADY_CLAIMED");
    }
    this.#revalidateImmediatelyBeforeAction(snapshot, permit);
    const assertions = {
      identityBeforeActionMatched: false,
      identityBeforeRestoreMatched: false,
      uniqueCurrentCandidate: false,
      preconditionMatched: false,
      changedAsExpected: false,
      restoredExactly: false,
      sessionClosed: false,
    };
    const lifecycle: SignedBrowserRecoveryEvidence["payload"]["lifecycle"][number][] = [
      "CANDIDATE_DISCOVERED",
      "PROPOSAL_APPROVED",
    ];
    let before: string | null | undefined;
    let after: string | null | undefined;
    let restored: string | null | undefined;
    let session: BrowserRecoverySession | undefined;
    let errorCode: string | undefined;

    try {
      this.#revalidateImmediatelyBeforeAction(snapshot, permit);
      session = await this.#openSession();
      this.#revalidateImmediatelyBeforeAction(snapshot, permit);
      await this.#verifyCurrentIdentity(session, permit);
      session.page.setDefaultTimeout(this.#operationTimeoutMs);
      const locator = session.page.getByRole(snapshot.candidate.role, {
        name: snapshot.candidate.accessibleName,
        exact: true,
      });
      const count = await locator.count();
      if (count !== 1 || !(await locator.isVisible()) || !(await locator.isEnabled())) {
        errorCode = count > 1 ? "RECOVERY_CANDIDATE_AMBIGUOUS" : "RECOVERY_CANDIDATE_UNAVAILABLE";
      } else {
        assertions.uniqueCurrentCandidate = true;
        before = await locator.getAttribute(permit.candidateBinding.stateAttribute);
        if (before !== snapshot.preconditionValue) {
          errorCode = "RECOVERY_PRECONDITION_MISMATCH";
        } else {
          assertions.preconditionMatched = true;
          this.#revalidateImmediatelyBeforeAction(snapshot, permit);
          await this.#verifyCurrentIdentity(session, permit);
          this.#revalidateImmediatelyBeforeAction(snapshot, permit);
          assertions.identityBeforeActionMatched = true;
          await locator.click();
          lifecycle.push("ACTION_APPLIED");
          after = await locator.getAttribute(permit.candidateBinding.stateAttribute);
          if (after === before) {
            errorCode = "RECOVERY_UNCHANGED_READBACK";
          } else if (after !== snapshot.expectedAfterValue) {
            errorCode = "RECOVERY_AFTER_ASSERTION_MISMATCH";
          } else {
            assertions.changedAsExpected = true;
            lifecycle.push("OUTCOME_VERIFIED");
          }
        }
      }
    } catch (error) {
      errorCode =
        errorCode ??
        (error instanceof BrowserRecoveryError ? error.code : "RECOVERY_ACTION_FAILED");
    } finally {
      if (session) {
        try {
          if (before !== undefined) {
            await this.#verifyCurrentIdentity(session, permit);
            const locator = session.page.getByRole(snapshot.candidate.role, {
              name: snapshot.candidate.accessibleName,
              exact: true,
            });
            if ((await locator.count()) === 1) {
              const current = await locator.getAttribute(permit.candidateBinding.stateAttribute);
              if (current !== before) {
                await this.#verifyCurrentIdentity(session, permit);
                await locator.click();
              }
              await this.#verifyCurrentIdentity(session, permit);
              assertions.identityBeforeRestoreMatched = true;
              restored = await locator.getAttribute(permit.candidateBinding.stateAttribute);
              assertions.restoredExactly = restored === before;
              if (!assertions.restoredExactly) errorCode = "RECOVERY_RESTORE_FAILED";
            } else {
              errorCode = "RECOVERY_RESTORE_FAILED";
            }
          }
        } catch (error) {
          errorCode = error instanceof BrowserRecoveryError
            ? error.code : "RECOVERY_RESTORE_FAILED";
        }
        try {
          await session.close();
          assertions.sessionClosed = true;
        } catch {
          errorCode = "RECOVERY_CLEANUP_FAILED";
        }
      } else {
        errorCode = "RECOVERY_SESSION_FAILED";
      }
    }

    const status: BrowserRecoveryStatus =
      !errorCode && Object.values(assertions).every(Boolean) ? "PASSED" : "FAILED";
    return this.#signEvidence({
      schemaVersion: "1.0.0",
      executionId: randomUUID(),
      permitId: permit.permitId,
      permitSha256: snapshot.permit.payloadSha256,
      scopeSha256: digest(canonicalJson(this.#expectedScope)),
      workerExecutionId: snapshot.candidateReceipt.executionId,
      workerInputDigest: snapshot.candidateReceipt.inputDigest,
      action: "CLICK_REVERSIBLE_TOGGLE",
      status,
      lifecycle,
      beforeSha256: before === undefined || before === null ? undefined : digest(before),
      afterSha256: after === undefined || after === null ? undefined : digest(after),
      restoredSha256: restored === undefined || restored === null ? undefined : digest(restored),
      assertions,
      errorCode,
      terminalAt: new Date(this.#now()).toISOString(),
    });
  }

  #authorize(request: BrowserRecoveryRequest): BrowserRecoveryPermitPayload {
    try {
      validateRequest(request);
      const permit = request.permit;
      if (
        !hasExactKeys(permit, [
          "payload",
          "issuerId",
          "payloadSha256",
          "signatureSha256",
        ])
      ) {
        throw new Error();
      }
      validatePermitPayload(permit.payload);
      const authorityKey = this.#trustedAuthorityKeys.get(permit.issuerId);
      const payloadSha256 = digest(canonicalJson(permit.payload));
      if (
        !authorityKey ||
        authorityKey.byteLength < 32 ||
        permit.payloadSha256 !== payloadSha256 ||
        !safeEqual(permit.signatureSha256, hmac(payloadSha256, authorityKey)) ||
        canonicalJson(permit.payload.scope) !== canonicalJson(this.#expectedScope) ||
        Date.parse(permit.payload.issuedAt) > this.#now() ||
        Date.parse(permit.payload.expiresAt) <= this.#now()
      ) {
        throw new Error();
      }
      const binding = permit.payload.candidateBinding;
      const receipt = request.candidateReceipt;
      if (
        receipt.mode !== "CANDIDATE_READBACK" ||
        receipt.status !== "PASSED" ||
        receipt.candidateCount !== 1 ||
        !receipt.lifecycle.includes("CANDIDATE_DISCOVERED") ||
        receipt.executionId !== binding.workerExecutionId ||
        receipt.inputDigest !== binding.workerInputDigest ||
        receipt.enrollment.originDigest !== binding.enrollmentOriginDigest ||
        receipt.enrollment.orgBindingDigest !== binding.enrollmentOrgBindingDigest ||
        receipt.enrollment.actorBindingDigest !== binding.enrollmentActorBindingDigest ||
        request.candidate.role !== binding.role ||
        digest(request.candidate.accessibleName) !== binding.accessibleNameSha256 ||
        digest(request.preconditionValue) !== binding.preconditionSha256 ||
        digest(request.expectedAfterValue) !== binding.expectedAfterSha256
      ) {
        throw new Error();
      }
      return permit.payload;
    } catch {
      throw new BrowserRecoveryError("RECOVERY_AUTHORITY_INVALID");
    }
  }

  #revalidateImmediatelyBeforeAction(
    request: BrowserRecoveryRequest,
    permit: BrowserRecoveryPermitPayload,
  ): void {
    const binding = permit.candidateBinding;
    if (
      Date.parse(permit.expiresAt) <= this.#now() ||
      canonicalJson(permit.scope) !== canonicalJson(this.#expectedScope) ||
      request.candidateReceipt.enrollment.originDigest !== binding.enrollmentOriginDigest ||
      request.candidateReceipt.enrollment.orgBindingDigest !==
        binding.enrollmentOrgBindingDigest ||
      request.candidateReceipt.enrollment.actorBindingDigest !==
        binding.enrollmentActorBindingDigest
    ) {
      throw new BrowserRecoveryError("RECOVERY_AUTHORITY_EXPIRED_OR_CHANGED");
    }
  }

  async #verifyCurrentIdentity(
    session: BrowserRecoverySession,
    permit: BrowserRecoveryPermitPayload,
  ): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      if (typeof session.observeIdentity !== "function") throw new Error();
      const startedAt = this.#now();
      const originBefore = new URL(session.page.url()).origin;
      const observed = await Promise.race([
        session.observeIdentity(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error()), this.#operationTimeoutMs);
        }),
      ]);
      const originAfter = new URL(session.page.url()).origin;
      const now = this.#now();
      if (
        !hasExactKeys(observed, [
          "lightningOriginSha256", "orgFingerprintSha256", "actorFingerprintSha256",
          "observedAt", "expiresAt",
        ]) ||
        !originBefore.startsWith("https://") || originAfter !== originBefore ||
        digest(originAfter) !== permit.scope.lightningOriginSha256 ||
        observed.lightningOriginSha256 !== permit.scope.lightningOriginSha256 ||
        observed.orgFingerprintSha256 !== permit.scope.orgFingerprintSha256 ||
        observed.actorFingerprintSha256 !== permit.scope.actorFingerprintSha256 ||
        !Number.isFinite(Date.parse(observed.observedAt)) ||
        Date.parse(observed.observedAt) < startedAt || Date.parse(observed.observedAt) > now ||
        !Number.isFinite(Date.parse(observed.expiresAt)) || Date.parse(observed.expiresAt) <= now
      ) throw new Error();
    } catch {
      throw new BrowserRecoveryError("RECOVERY_CURRENT_IDENTITY_MISMATCH");
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  #signEvidence(
    payload: SignedBrowserRecoveryEvidence["payload"],
  ): SignedBrowserRecoveryEvidence {
    const payloadSha256 = digest(canonicalJson(payload));
    return Object.freeze({
      payload,
      issuerId: this.#evidenceIssuer.issuerId,
      payloadSha256,
      signatureSha256: hmac(payloadSha256, this.#evidenceIssuer.hmacKey),
    });
  }
}

function validatePermitPayload(payload: BrowserRecoveryPermitPayload): void {
  validateScope(payload.scope);
  const issuedAt = Date.parse(payload.issuedAt);
  const expiresAt = Date.parse(payload.expiresAt);
  const binding = payload.candidateBinding;
  if (
    !hasExactKeys(payload, [
      "schemaVersion",
      "permitId",
      "authorityClass",
      "scope",
      "candidateBinding",
      "permittedAction",
      "beforeAssertion",
      "afterAssertion",
      "restoration",
      "cleanup",
      "issuedAt",
      "expiresAt",
    ]) ||
    !hasExactKeys(binding, [
      "workerExecutionId",
      "workerInputDigest",
      "candidateCount",
      "role",
      "accessibleNameSha256",
      "stateAttribute",
      "preconditionSha256",
      "expectedAfterSha256",
      "enrollmentOriginDigest",
      "enrollmentOrgBindingDigest",
      "enrollmentActorBindingDigest",
    ]) ||
    payload.schemaVersion !== "1.0.0" ||
    !IDENTIFIER.test(payload.permitId) ||
    payload.authorityClass !== "INDEPENDENT_BROWSER_RECOVERY_AUTHORITY" ||
    payload.permittedAction !== "CLICK_REVERSIBLE_TOGGLE" ||
    payload.beforeAssertion !== "EXACT_PRECONDITION" ||
    payload.afterAssertion !== "EXACT_EXPECTED_AND_CHANGED" ||
    payload.restoration !== "REQUIRED_EXACT_PRECONDITION" ||
    payload.cleanup !== "REQUIRED_EPHEMERAL_SESSION_CLOSE" ||
    !IDENTIFIER.test(binding.workerExecutionId) ||
    !SHA256.test(binding.workerInputDigest) ||
    binding.candidateCount !== 1 ||
    typeof binding.role !== "string" ||
    !SHA256.test(binding.accessibleNameSha256) ||
    !SHA256.test(binding.preconditionSha256) ||
    !SHA256.test(binding.expectedAfterSha256) ||
    !SHA256.test(binding.enrollmentOriginDigest) ||
    !SHA256.test(binding.enrollmentOrgBindingDigest) ||
    !SHA256.test(binding.enrollmentActorBindingDigest) ||
    binding.enrollmentOriginDigest !== payload.scope.lightningOriginSha256 ||
    binding.enrollmentOrgBindingDigest !== payload.scope.orgFingerprintSha256 ||
    binding.enrollmentActorBindingDigest !== payload.scope.actorFingerprintSha256 ||
    !Number.isFinite(issuedAt) ||
    !Number.isFinite(expiresAt) ||
    expiresAt <= issuedAt ||
    expiresAt - issuedAt > MAX_PERMIT_LIFETIME_MS
  ) {
    throw new BrowserRecoveryError("RECOVERY_PERMIT_INVALID");
  }
}

function immutableRequestSnapshot(request: BrowserRecoveryRequest): BrowserRecoveryRequest {
  try {
    const snapshot = structuredClone(request);
    return deepFreeze(snapshot);
  } catch {
    throw new BrowserRecoveryError("RECOVERY_REQUEST_INVALID");
  }
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

function validateScope(scope: BrowserRecoveryScope): void {
  const hashValues = [
    scope.sourceContractSha256,
    scope.candidateSha256,
    scope.buildSha256,
    scope.operationPlanSha256,
    scope.restoreScopeSha256,
    scope.policySha256,
    scope.profileSha256,
    scope.orgFingerprintSha256,
    scope.actorFingerprintSha256,
    scope.lightningOriginSha256,
    scope.expectedExecutionContractSha256,
  ];
  if (
    !hasExactKeys(scope, [
      "campaignId",
      "projectId",
      "sourceContractSha256",
      "candidateSha256",
      "buildSha256",
      "operationPlanSha256",
      "restoreScopeSha256",
      "policySha256",
      "profileSha256",
      "orgFingerprintSha256",
      "actorFingerprintSha256",
      "lightningOriginSha256",
      "expectedExecutionContractSha256",
    ]) ||
    !IDENTIFIER.test(scope.campaignId) ||
    !IDENTIFIER.test(scope.projectId) ||
    hashValues.some((value) => !SHA256.test(value))
  ) {
    throw new BrowserRecoveryError("RECOVERY_SCOPE_INVALID");
  }
}

function hasExactKeys(value: object, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const required = [...expected].sort();
  return (
    actual.length === required.length &&
    actual.every((key, index) => key === required[index])
  );
}

function validateRequest(request: BrowserRecoveryRequest): void {
  const values = [
    request.candidate.accessibleName,
    request.preconditionValue,
    request.expectedAfterValue,
  ];
  if (
    !hasExactKeys(request, [
      "permit",
      "candidateReceipt",
      "candidate",
      "preconditionValue",
      "expectedAfterValue",
    ]) ||
    !hasExactKeys(request.candidate, ["role", "accessibleName"]) ||
    typeof request.candidate.role !== "string" ||
    values.some((value) => typeof value !== "string" || value.length < 1 || value.length > 200) ||
    values.some(containsSensitiveText)
  ) {
    throw new BrowserRecoveryError("RECOVERY_REQUEST_INVALID");
  }
}

function containsSensitiveText(value: string): boolean {
  return /(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+|\bsid=|password\s*[=:]|api[_ -]?key|[A-Za-z]:[\\/](?:Users|Documents)[\\/]|^\/(?:home|Users)\/)/i.test(
    value,
  );
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function hmac(value: string, key: Uint8Array): string {
  return createHmac("sha256", key).update(value).digest("hex");
}

function safeEqual(left: string, right: string): boolean {
  if (!SHA256.test(left) || !SHA256.test(right)) {
    return false;
  }
  return timingSafeEqual(Buffer.from(left, "hex"), Buffer.from(right, "hex"));
}
