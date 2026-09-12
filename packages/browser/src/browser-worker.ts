import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { chromium, type Browser, type BrowserContext, type Locator, type Page } from "playwright";
import { discoverLocatorCandidate } from "./locator-healer.js";

export type WorkerMode = "READ_ONLY_DOM_CAPTURE" | "CANDIDATE_READBACK" | "BUSINESS_ACTION";
export type WorkerStatus = "PASSED" | "FAILED" | "BLOCKED";

export interface TrustedEnrollmentAssertion {
  enrollmentId: string;
  issuer: string;
  classification: "NON_PRODUCTION";
  frontdoorOrigin: string;
  lightningOrigin: string;
  navigationBridgeOrigins: readonly string[];
  resourceOrigins: readonly string[];
  orgBinding: string;
  actorBinding: string;
  policyDigest: string;
  issuedAt: string;
  expiresAt: string;
  permittedModes: readonly WorkerMode[];
}

export interface CandidateIntent {
  role?: Parameters<Page["getByRole"]>[0];
  accessibleName?: string;
  readback?: {
    attribute:
      | "aria-checked"
      | "aria-expanded"
      | "aria-pressed"
      | "data-action"
      | "data-state";
    expectedValue: string;
  };
  hostAttribute?: {
    tag: "lightning-button" | "button";
    attribute: "data-action";
    expectedValue: string;
  };
}

export interface BusinessActionIntent {
  objectApiName: string;
  fields: readonly {
    fieldApiName: string;
    value: string;
  }[];
  submit: {
    tag: "lightning-button" | "button";
    attribute: "data-action";
    expectedValue: string;
  };
  successText: string;
}

export interface BrowserWorkerRequest {
  handoff: EphemeralSessionHandoff;
  mode: WorkerMode;
  startPath?: string;
  candidate?: CandidateIntent;
  businessAction?: BusinessActionIntent;
  captureLimit?: number;
}

export interface SanitizedDomCandidate {
  ordinal: number;
  tag: string;
  role: string;
  accessibleName: string;
  visible: boolean;
  enabled: boolean;
}

export type CandidateLifecycleState =
  | "CAPTURED"
  | "CANDIDATE_DISCOVERED"
  | "CANDIDATE_AMBIGUOUS"
  | "CANDIDATE_NOT_FOUND"
  | "READBACK_VERIFIED"
  | "READBACK_MISMATCH";

export interface BrowserWorkerReceipt {
  schemaVersion: "1.0.0";
  executionId: string;
  inputDigest: string;
  capabilityId: "automation.browser-worker";
  status: WorkerStatus;
  mode: WorkerMode;
  enrollment: {
    enrollmentIdDigest: string;
    issuerDigest: string;
    originDigest: string;
    orgBindingDigest: string;
    actorBindingDigest: string;
    policyDigest: string;
  };
  lifecycle: CandidateLifecycleState[];
  capture: readonly SanitizedDomCandidate[];
  candidateCount: number;
  readbackMatched?: boolean;
  businessAction?: {
    fieldCount: number;
    submitted: boolean;
    successTextMatched: boolean;
    healedFieldCount?: number;
    abstainedFieldCount?: number;
    strategies?: readonly string[];
  };
  postSubmit?: {
    alertPresent: boolean;
    alertTextDigest?: string;
    alertTextLength?: number;
    statusPresent: boolean;
    statusTextDigest?: string;
    statusTextLength?: number;
  };
  cleanup: {
    contextClosed: boolean;
    browserClosed: boolean;
  };
  error?: {
    class: "POLICY_BLOCKED" | "NAVIGATION_FAILED" | "CAPTURE_FAILED" | "CLEANUP_FAILED";
    code: string;
  };
}

export interface TrustedEnrollmentHandle {
  readonly kind: "TRUSTED_ENROLLMENT_HANDLE";
}

export interface VerifiedSessionIdentityHandle {
  readonly kind: "VERIFIED_SESSION_IDENTITY_HANDLE";
}

export interface EphemeralSessionHandoff {
  readonly kind: "EPHEMERAL_SESSION_HANDOFF";
}

type VerifiedEnrollment = TrustedEnrollmentAssertion & {
  canonicalFrontdoorOrigin: string;
  canonicalLightningOrigin: string;
  issuedAtMs: number;
  expiresAtMs: number;
};

type SessionMaterial = {
  enrollment: VerifiedEnrollment;
  entryUrl: string;
  sensitiveValues: readonly string[];
  consumed: boolean;
};

type OfflineDocument = {
  status?: number;
  contentType?: string;
  body: string;
};

export interface BrowserWorkerOptions {
  verifyEnrollment: (assertion: Readonly<TrustedEnrollmentAssertion>) => boolean | Promise<boolean>;
  allowedLightningHostnameSuffixes?: readonly string[];
  allowedFrontdoorHostnameSuffixes?: readonly string[];
  maxEnrollmentLifetimeMs?: number;
  launchBrowser?: () => Promise<Browser>;
  now?: () => number;
  navigationTimeoutMs?: number;
  operationTimeoutMs?: number;
  /** Test/local adapter. It receives only the non-secret URL pathname. */
  offlineDocumentForPath?: (pathname: string) => OfflineDocument | undefined;
}

const enrollmentHandles = new WeakMap<object, VerifiedEnrollment>();
const verifiedIdentityHandles = new WeakMap<
  object,
  { enrollment: VerifiedEnrollment; consumed: boolean }
>();
const sessionHandoffs = new WeakMap<object, SessionMaterial>();
const DEFAULT_CAPTURE_LIMIT = 25;
const MAX_CAPTURE_LIMIT = 100;
const DEFAULT_MAX_ENROLLMENT_LIFETIME_MS = 15 * 60 * 1000;
const BLOCKED_RECEIPT_ENROLLMENT = {
  enrollmentIdDigest: digest("unavailable"),
  issuerDigest: digest("unavailable"),
  originDigest: digest("unavailable"),
  orgBindingDigest: digest("unavailable"),
  actorBindingDigest: digest("unavailable"),
  policyDigest: "unavailable",
} as const;

export class BrowserWorkerError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "BrowserWorkerError";
  }
}

export class BrowserWorker {
  readonly #verifyEnrollment: BrowserWorkerOptions["verifyEnrollment"];
  readonly #allowedLightningHostnameSuffixes: readonly string[];
  readonly #allowedFrontdoorHostnameSuffixes: readonly string[];
  readonly #maxEnrollmentLifetimeMs: number;
  readonly #launchBrowser: () => Promise<Browser>;
  readonly #now: () => number;
  readonly #navigationTimeoutMs: number;
  readonly #operationTimeoutMs: number;
  readonly #offlineDocumentForPath?: BrowserWorkerOptions["offlineDocumentForPath"];

  constructor(options: BrowserWorkerOptions) {
    this.#verifyEnrollment = options.verifyEnrollment;
    this.#allowedLightningHostnameSuffixes = options.allowedLightningHostnameSuffixes ?? [
      ".lightning.force.com",
    ];
    this.#allowedFrontdoorHostnameSuffixes = options.allowedFrontdoorHostnameSuffixes ?? [
      ".lightning.force.com",
      ".my.salesforce.com",
    ];
    this.#maxEnrollmentLifetimeMs =
      options.maxEnrollmentLifetimeMs ?? DEFAULT_MAX_ENROLLMENT_LIFETIME_MS;
    this.#launchBrowser = options.launchBrowser ?? (() => chromium.launch({ headless: true }));
    this.#now = options.now ?? Date.now;
    this.#navigationTimeoutMs = boundedTimeout(
      options.navigationTimeoutMs ?? 15_000,
      "NAVIGATION_TIMEOUT_INVALID",
    );
    this.#operationTimeoutMs = boundedTimeout(
      options.operationTimeoutMs ?? 10_000,
      "OPERATION_TIMEOUT_INVALID",
    );
    this.#offlineDocumentForPath = options.offlineDocumentForPath;
  }

  async enroll(assertion: TrustedEnrollmentAssertion): Promise<TrustedEnrollmentHandle> {
    const verified = this.#validateEnrollment(assertion);
    let trusted = false;
    try {
      trusted = await this.#verifyEnrollment(Object.freeze({ ...assertion }));
    } catch {
      throw new BrowserWorkerError("ENROLLMENT_VERIFICATION_FAILED");
    }
    if (!trusted) {
      throw new BrowserWorkerError("ENROLLMENT_NOT_TRUSTED");
    }

    const handle = privateHandle("TRUSTED_ENROLLMENT_HANDLE") as TrustedEnrollmentHandle;
    enrollmentHandles.set(handle, verified);
    return handle;
  }

  verifySessionIdentity(
    handle: TrustedEnrollmentHandle,
    material: { orgBinding: string; actorBinding: string },
  ): VerifiedSessionIdentityHandle {
    const enrollment = enrollmentHandles.get(handle);
    if (!enrollment) {
      throw new BrowserWorkerError("UNKNOWN_ENROLLMENT_HANDLE");
    }
    this.#assertCurrent(enrollment);
    if (
      !constantTimeDigestEqual(material.orgBinding, enrollment.orgBinding) ||
      !constantTimeDigestEqual(material.actorBinding, enrollment.actorBinding)
    ) {
      throw new BrowserWorkerError("SESSION_BINDING_MISMATCH");
    }
    const identity = privateHandle(
      "VERIFIED_SESSION_IDENTITY_HANDLE",
    ) as VerifiedSessionIdentityHandle;
    verifiedIdentityHandles.set(identity, { enrollment, consumed: false });
    return identity;
  }

  issueSession(
    handle: TrustedEnrollmentHandle,
    identityHandle: VerifiedSessionIdentityHandle,
    material: {
      entryUrl: string;
      frontdoorOrigin: string;
      lightningOrigin: string;
      navigationBridgeOrigins?: readonly string[];
      resourceOrigins?: readonly string[];
    },
  ): EphemeralSessionHandoff {
    const enrollment = enrollmentHandles.get(handle);
    const identity = verifiedIdentityHandles.get(identityHandle);
    if (!enrollment) {
      throw new BrowserWorkerError("UNKNOWN_ENROLLMENT_HANDLE");
    }
    if (!identity || identity.consumed || identity.enrollment !== enrollment) {
      throw new BrowserWorkerError("SESSION_IDENTITY_HANDLE_INVALID");
    }
    identity.consumed = true;
    verifiedIdentityHandles.delete(identityHandle);
    this.#assertCurrent(enrollment);
    if (
      material.frontdoorOrigin !== enrollment.canonicalFrontdoorOrigin ||
      material.lightningOrigin !== enrollment.canonicalLightningOrigin ||
      !equalStrings(material.navigationBridgeOrigins ?? [], enrollment.navigationBridgeOrigins) ||
      !equalStrings(material.resourceOrigins ?? [], enrollment.resourceOrigins)
    ) {
      throw new BrowserWorkerError("SESSION_BINDING_MISMATCH");
    }

    const parsedEntry = parseUrl(material.entryUrl, "SESSION_URL_INVALID");
    if (parsedEntry.origin !== enrollment.canonicalFrontdoorOrigin) {
      throw new BrowserWorkerError("SESSION_ORIGIN_MISMATCH");
    }
    if (
      parsedEntry.pathname !== "/secur/frontdoor.jsp" ||
      parsedEntry.hash ||
      !hasExactFrontdoorCredentialShape(parsedEntry)
    ) {
      throw new BrowserWorkerError("SESSION_HANDOFF_INVALID");
    }

    const handoff = privateHandle("EPHEMERAL_SESSION_HANDOFF") as EphemeralSessionHandoff;
    sessionHandoffs.set(handoff, {
      enrollment,
      entryUrl: material.entryUrl,
      sensitiveValues: collectSensitiveValues(parsedEntry, material.entryUrl),
      consumed: false,
    });
    return handoff;
  }

  async execute(request: BrowserWorkerRequest): Promise<BrowserWorkerReceipt> {
    const executionId = randomUUID();
    const session = sessionHandoffs.get(request.handoff);
    if (!session || session.consumed) {
      return blockedReceipt(executionId, request.mode, "INVALID_OR_CONSUMED_HANDOFF");
    }
    session.consumed = true;
    sessionHandoffs.delete(request.handoff);

    const enrollmentReceipt = enrollmentProjection(session.enrollment);
    const inputDigest = digest(
      canonicalJson({
        enrollmentId: session.enrollment.enrollmentId,
        mode: request.mode,
        startPath: request.startPath,
        candidate: request.candidate
          ? {
              role: request.candidate.role,
              accessibleNameDigest: request.candidate.accessibleName
                ? digest(request.candidate.accessibleName)
                : undefined,
              readback: request.candidate.readback
                ? {
                    attribute: request.candidate.readback.attribute,
                    expectedValueDigest: digest(request.candidate.readback.expectedValue),
                  }
                : undefined,
              hostAttribute: request.candidate.hostAttribute
                ? {
                    tag: request.candidate.hostAttribute.tag,
                    attribute: request.candidate.hostAttribute.attribute,
                    expectedValueDigest: digest(request.candidate.hostAttribute.expectedValue),
                  }
                : undefined,
            }
          : undefined,
        businessAction: request.businessAction
          ? {
              objectApiName: request.businessAction.objectApiName,
              fields: request.businessAction.fields.map((field) => ({
                fieldApiName: field.fieldApiName,
                valueDigest: digest(field.value),
              })),
              submit: {
                tag: request.businessAction.submit.tag,
                attribute: request.businessAction.submit.attribute,
                expectedValueDigest: digest(request.businessAction.submit.expectedValue),
              },
              successTextDigest: digest(request.businessAction.successText),
            }
          : undefined,
      }),
    );

    let browser: Browser | undefined;
    let context: BrowserContext | undefined;
    let pending: Omit<BrowserWorkerReceipt, "cleanup">;
    let contextClosed = false;
    let browserClosed = false;

    try {
      this.#assertCurrent(session.enrollment);
      if (!session.enrollment.permittedModes.includes(request.mode)) {
        throw new BrowserWorkerError("MODE_NOT_AUTHORIZED");
      }
      validateRequest(request);

      browser = await this.#launchBrowser();
      context = await browser.newContext({
        acceptDownloads: false,
        serviceWorkers: "block",
      });
      await this.#installNetworkBoundary(context, session.enrollment);
      const page = await context.newPage();
      page.setDefaultTimeout(this.#operationTimeoutMs);
      await navigate(
        page,
        session.entryUrl,
        session.enrollment.canonicalLightningOrigin,
        this.#navigationTimeoutMs,
      );
      validateCurrentPageOrigin(page, session.enrollment.canonicalLightningOrigin);
      if (request.startPath) {
        await navigate(
          page,
          new URL(request.startPath, session.enrollment.canonicalLightningOrigin).toString(),
          session.enrollment.canonicalLightningOrigin,
          this.#navigationTimeoutMs,
        );
        validateCurrentPageOrigin(page, session.enrollment.canonicalLightningOrigin);
      }

      pending = await this.#executeOnPage(
        page,
        request,
        executionId,
        inputDigest,
        enrollmentReceipt,
        session.sensitiveValues,
      );
    } catch (error) {
      const mapped = mapFailure(error);
      pending = {
        schemaVersion: "1.0.0",
        executionId,
        inputDigest,
        capabilityId: "automation.browser-worker",
        status: mapped.class === "POLICY_BLOCKED" ? "BLOCKED" : "FAILED",
        mode: request.mode,
        enrollment: enrollmentReceipt,
        lifecycle: [],
        capture: [],
        candidateCount: 0,
        error: mapped,
      };
    } finally {
      if (context) {
        try {
          await context.close();
          contextClosed = true;
        } catch {
          contextClosed = false;
        }
      } else {
        contextClosed = true;
      }
      if (browser) {
        try {
          await browser.close();
          browserClosed = true;
        } catch {
          browserClosed = false;
        }
      } else {
        browserClosed = true;
      }
    }

    if (!contextClosed || !browserClosed) {
      pending = {
        ...pending,
        status: "FAILED",
        error: { class: "CLEANUP_FAILED", code: "EPHEMERAL_BROWSER_CLEANUP_FAILED" },
      };
    }
    return Object.freeze({ ...pending, cleanup: { contextClosed, browserClosed } });
  }

  #validateEnrollment(assertion: TrustedEnrollmentAssertion): VerifiedEnrollment {
    if (
      !isBoundedIdentifier(assertion.enrollmentId) ||
      !isBoundedIdentifier(assertion.issuer) ||
      assertion.classification !== "NON_PRODUCTION" ||
      !isBinding(assertion.orgBinding) ||
      !isBinding(assertion.actorBinding) ||
      !isSha256(assertion.policyDigest) ||
      !Array.isArray(assertion.permittedModes) ||
      assertion.permittedModes.length === 0 ||
      assertion.permittedModes.some(
        (mode) =>
          mode !== "READ_ONLY_DOM_CAPTURE" &&
          mode !== "CANDIDATE_READBACK" &&
          mode !== "BUSINESS_ACTION",
      )
    ) {
      throw new BrowserWorkerError("ENROLLMENT_INVALID");
    }

    const parsedOrigin = parseUrl(assertion.lightningOrigin, "LIGHTNING_ORIGIN_INVALID");
    if (
      parsedOrigin.protocol !== "https:" ||
      parsedOrigin.username ||
      parsedOrigin.password ||
      parsedOrigin.pathname !== "/" ||
      parsedOrigin.search ||
      parsedOrigin.hash ||
      parsedOrigin.origin !== assertion.lightningOrigin ||
      !this.#allowedLightningHostnameSuffixes.some(
        (suffix) =>
          suffix.startsWith(".") &&
          parsedOrigin.hostname.length > suffix.length &&
          parsedOrigin.hostname.endsWith(suffix),
      )
    ) {
      throw new BrowserWorkerError("LIGHTNING_ORIGIN_NOT_ALLOWED");
    }
    const parsedFrontdoorOrigin = parseUrl(
      assertion.frontdoorOrigin,
      "FRONTDOOR_ORIGIN_INVALID",
    );
    if (
      parsedFrontdoorOrigin.protocol !== "https:" ||
      parsedFrontdoorOrigin.username ||
      parsedFrontdoorOrigin.password ||
      parsedFrontdoorOrigin.pathname !== "/" ||
      parsedFrontdoorOrigin.search ||
      parsedFrontdoorOrigin.hash ||
      parsedFrontdoorOrigin.origin !== assertion.frontdoorOrigin ||
      !this.#allowedFrontdoorHostnameSuffixes.some(
        (suffix) =>
          suffix.startsWith(".") &&
          parsedFrontdoorOrigin.hostname.length > suffix.length &&
          parsedFrontdoorOrigin.hostname.endsWith(suffix),
      )
    ) {
      throw new BrowserWorkerError("FRONTDOOR_ORIGIN_NOT_ALLOWED");
    }
    const navigationBridgeOrigins = validatePinnedOrigins(
      assertion.navigationBridgeOrigins,
      (hostname) => hostname.endsWith(".file.force.com"),
      "NAVIGATION_BRIDGE_ORIGINS_INVALID",
    );
    const resourceOrigins = validatePinnedOrigins(
      assertion.resourceOrigins,
      (hostname) =>
        hostname === "login.salesforce.com" ||
        hostname.endsWith(".static.lightning.force.com"),
      "RESOURCE_ORIGINS_INVALID",
    );

    const issuedAtMs = Date.parse(assertion.issuedAt);
    const expiresAtMs = Date.parse(assertion.expiresAt);
    if (
      !Number.isFinite(issuedAtMs) ||
      !Number.isFinite(expiresAtMs) ||
      issuedAtMs > this.#now() ||
      expiresAtMs <= issuedAtMs ||
      expiresAtMs - issuedAtMs > this.#maxEnrollmentLifetimeMs
    ) {
      throw new BrowserWorkerError("ENROLLMENT_TIME_INVALID");
    }
    if (expiresAtMs <= this.#now()) {
      throw new BrowserWorkerError("ENROLLMENT_EXPIRED");
    }

    return Object.freeze({
      ...assertion,
      permittedModes: Object.freeze([...assertion.permittedModes]),
      navigationBridgeOrigins,
      resourceOrigins,
      canonicalFrontdoorOrigin: parsedFrontdoorOrigin.origin,
      canonicalLightningOrigin: parsedOrigin.origin,
      issuedAtMs,
      expiresAtMs,
    });
  }

  #assertCurrent(enrollment: VerifiedEnrollment): void {
    if (enrollment.expiresAtMs <= this.#now()) {
      throw new BrowserWorkerError("ENROLLMENT_EXPIRED");
    }
  }

  async #installNetworkBoundary(
    context: BrowserContext,
    enrollment: VerifiedEnrollment,
  ): Promise<void> {
    const allowedOrigins = new Set([
      enrollment.canonicalFrontdoorOrigin,
      enrollment.canonicalLightningOrigin,
    ]);
    const navigationBridgeOrigins = new Set(enrollment.navigationBridgeOrigins);
    const resourceOrigins = new Set(enrollment.resourceOrigins);
    await context.route("**/*", async (route) => {
      let parsed: URL;
      try {
        parsed = new URL(route.request().url());
      } catch {
        await route.abort("blockedbyclient");
        return;
      }
      const request = route.request();
      const permittedApplicationOrigin = allowedOrigins.has(parsed.origin);
      const permittedBridgeNavigation =
        navigationBridgeOrigins.has(parsed.origin) &&
        request.isNavigationRequest() &&
        request.frame().parentFrame() === null;
      const permittedResourceOrigin =
        resourceOrigins.has(parsed.origin) && !request.isNavigationRequest();
      if (
        !permittedApplicationOrigin &&
        !permittedBridgeNavigation &&
        !permittedResourceOrigin
      ) {
        await route.abort("blockedbyclient");
        return;
      }
      const offline = this.#offlineDocumentForPath?.(parsed.pathname);
      if (offline) {
        await route.fulfill({
          status: offline.status ?? 200,
          contentType: offline.contentType ?? "text/html; charset=utf-8",
          body: offline.body,
        });
        return;
      }
      await route.continue();
    });
  }

  async #executeOnPage(
    page: Page,
    request: BrowserWorkerRequest,
    executionId: string,
    inputDigest: string,
    enrollment: BrowserWorkerReceipt["enrollment"],
    sensitiveValues: readonly string[],
  ): Promise<Omit<BrowserWorkerReceipt, "cleanup">> {
    const captureLimit = Math.min(request.captureLimit ?? DEFAULT_CAPTURE_LIMIT, MAX_CAPTURE_LIMIT);
    const capture = await captureDom(page, captureLimit, sensitiveValues);
    const base = {
      schemaVersion: "1.0.0" as const,
      executionId,
      inputDigest,
      capabilityId: "automation.browser-worker" as const,
      mode: request.mode,
      enrollment,
      capture,
    };

    if (request.mode === "READ_ONLY_DOM_CAPTURE") {
      return {
        ...base,
        status: "PASSED",
        lifecycle: ["CAPTURED"],
        candidateCount: capture.length,
      };
    }

    if (request.mode === "BUSINESS_ACTION") {
      const action = request.businessAction!;
      const lifecycle: CandidateLifecycleState[] = ["CAPTURED"];
      let healedFieldCount = 0;
      let abstainedFieldCount = 0;
      const strategies: string[] = [];
      for (const field of action.fields) {
        const wrapper = page.locator(`[data-field-api="${cssString(field.fieldApiName)}"]`);
        await wrapper.first().waitFor({ state: "attached", timeout: this.#operationTimeoutMs })
          .catch(() => undefined);
        const wrapperCount = await wrapper.count();
        let filled = false;
        let fieldStrategy: string | undefined;
        if (wrapperCount === 1) {
          fieldStrategy = await fillBusinessField(wrapper, field.value, this.#operationTimeoutMs);
          if (fieldStrategy) {
            strategies.push(fieldStrategy);
            filled = true;
          }
        }
        if (!filled) {
          const healed = await discoverLocatorCandidate(page, {
            objectApiName: action.objectApiName,
            fieldApiName: field.fieldApiName,
          });
          if (healed.status === "CANDIDATE_DISCOVERED" && healed.locator) {
            fieldStrategy = await fillBusinessField(
              healed.locator,
              field.value,
              this.#operationTimeoutMs,
            );
            if (fieldStrategy) {
              healedFieldCount += 1;
              strategies.push(healed.strategy ?? fieldStrategy);
              filled = true;
            }
          } else {
            abstainedFieldCount += 1;
          }
        }
        if (!filled) {
          return {
            ...base,
            status: abstainedFieldCount > 0 ? "BLOCKED" : "FAILED",
            lifecycle: [
              ...lifecycle,
              wrapperCount === 0 ? "CANDIDATE_NOT_FOUND" : "CANDIDATE_AMBIGUOUS",
            ],
            candidateCount: Math.max(wrapperCount, abstainedFieldCount),
            businessAction: {
              fieldCount: action.fields.length,
              submitted: false,
              successTextMatched: false,
              healedFieldCount,
              abstainedFieldCount,
              strategies,
            },
            error: {
              class: abstainedFieldCount > 0 ? "POLICY_BLOCKED" : "CAPTURE_FAILED",
              code: abstainedFieldCount > 0
                ? "BUSINESS_FIELD_HEALING_ABSTAINED"
                : "BUSINESS_FIELD_FILL_FAILED",
            },
          };
        }
      }
      const submit = page.locator(
        `${action.submit.tag}[${action.submit.attribute}="${cssString(action.submit.expectedValue)}"]`,
      );
      await submit.first().waitFor({ state: "attached", timeout: this.#operationTimeoutMs })
        .catch(() => undefined);
      let submitCount = await submit.count();
      let submitLocator = submit;
      if (submitCount !== 1) {
        const healedSubmit = await discoverLocatorCandidate(page, {
          action: action.submit.expectedValue,
        });
        if (healedSubmit.status === "CANDIDATE_DISCOVERED" && healedSubmit.locator) {
          submitLocator = healedSubmit.locator;
          submitCount = 1;
          healedFieldCount += 1;
          strategies.push(healedSubmit.strategy ?? "action-healer");
        }
      }
      if (submitCount !== 1) {
        return {
          ...base,
          status: "BLOCKED",
          lifecycle: [
            ...lifecycle,
            submitCount === 0 ? "CANDIDATE_NOT_FOUND" : "CANDIDATE_AMBIGUOUS",
          ],
          candidateCount: submitCount,
          businessAction: {
            fieldCount: action.fields.length,
            submitted: false,
            successTextMatched: false,
            healedFieldCount,
            abstainedFieldCount: abstainedFieldCount + 1,
            strategies,
          },
          error: {
            class: "POLICY_BLOCKED",
            code: submitCount === 0 ? "BUSINESS_SUBMIT_NOT_FOUND" : "BUSINESS_SUBMIT_AMBIGUOUS",
          },
        };
      }
      lifecycle.push("CANDIDATE_DISCOVERED");
      // Locating a submit candidate is not a submission. Only a completed click may report submitted.
      let submitDispatched = false;
      try {
        // Salesforce base components expose the real control inside the custom element. Clicking the
        // host can land outside the interactive child, so prefer that child when it is present.
        const interactive = submitLocator
          .first()
          .locator('button,input[type="submit"],[role="button"]')
          .first();
        const clickTarget = (await interactive.count()) > 0 ? interactive : submitLocator.first();
        await clickTarget.click({ timeout: this.#operationTimeoutMs });
        submitDispatched = true;
        await page.getByRole("status").filter({ hasText: action.successText }).first().waitFor({
          state: "visible",
          timeout: this.#operationTimeoutMs,
        });
        lifecycle.push("READBACK_VERIFIED");
      } catch {
        return {
          ...base,
          status: "FAILED",
          lifecycle,
          candidateCount: 1,
          businessAction: {
            fieldCount: action.fields.length,
            submitted: submitDispatched,
            successTextMatched: false,
            healedFieldCount,
            abstainedFieldCount,
            strategies,
          },
          postSubmit: await capturePostSubmitSignal(page, this.#operationTimeoutMs),
          error: { class: "CAPTURE_FAILED", code: "BUSINESS_ACTION_ASSERTION_FAILED" },
        };
      }
      return {
        ...base,
        status: "PASSED",
        lifecycle,
        candidateCount: 1,
        businessAction: {
          fieldCount: action.fields.length,
          submitted: true,
          successTextMatched: true,
          healedFieldCount,
          abstainedFieldCount,
          strategies,
        },
      };
    }

    const candidate = request.candidate!;
    if (candidate.hostAttribute) {
      const hostLocator = page.locator(
        `${candidate.hostAttribute.tag}[${candidate.hostAttribute.attribute}="${cssString(
          candidate.hostAttribute.expectedValue,
        )}"]`,
      );
      try {
        await hostLocator.first().waitFor({ state: "attached", timeout: this.#operationTimeoutMs });
      } catch {
        return {
          ...base,
          status: "BLOCKED",
          lifecycle: ["CAPTURED", "CANDIDATE_NOT_FOUND"],
          candidateCount: 0,
          error: { class: "POLICY_BLOCKED", code: "CANDIDATE_NOT_FOUND" },
        };
      }
      const count = await hostLocator.count();
      if (count !== 1) {
        return {
          ...base,
          status: "BLOCKED",
          lifecycle: ["CAPTURED", count === 0 ? "CANDIDATE_NOT_FOUND" : "CANDIDATE_AMBIGUOUS"],
          candidateCount: count,
          error: {
            class: "POLICY_BLOCKED",
            code: count === 0 ? "CANDIDATE_NOT_FOUND" : "CANDIDATE_AMBIGUOUS",
          },
        };
      }
      return {
        ...base,
        status: "PASSED",
        lifecycle: ["CAPTURED", "CANDIDATE_DISCOVERED", "READBACK_VERIFIED"],
        candidateCount: 1,
        readbackMatched: true,
      };
    }
    if (!candidate.role || !candidate.accessibleName) {
      throw new BrowserWorkerError("CANDIDATE_INTENT_INVALID");
    }
    const locator = page.getByRole(candidate.role, {
      name: candidate.accessibleName,
      exact: true,
    });
    const count = await locator.count();
    if (count === 0) {
      return {
        ...base,
        status: "BLOCKED",
        lifecycle: ["CAPTURED", "CANDIDATE_NOT_FOUND"],
        candidateCount: 0,
        error: { class: "POLICY_BLOCKED", code: "CANDIDATE_NOT_FOUND" },
      };
    }
    if (count !== 1) {
      return {
        ...base,
        status: "BLOCKED",
        lifecycle: ["CAPTURED", "CANDIDATE_AMBIGUOUS"],
        candidateCount: count,
        error: { class: "POLICY_BLOCKED", code: "CANDIDATE_AMBIGUOUS" },
      };
    }

    const lifecycle: CandidateLifecycleState[] = ["CAPTURED", "CANDIDATE_DISCOVERED"];
    if (!candidate.readback) {
      return { ...base, status: "PASSED", lifecycle, candidateCount: 1 };
    }
    const actual = await locator.getAttribute(candidate.readback.attribute);
    const readbackMatched = actual === candidate.readback.expectedValue;
    lifecycle.push(readbackMatched ? "READBACK_VERIFIED" : "READBACK_MISMATCH");
    return {
      ...base,
      status: readbackMatched ? "PASSED" : "FAILED",
      lifecycle,
      candidateCount: 1,
      readbackMatched,
      error: readbackMatched
        ? undefined
        : { class: "CAPTURE_FAILED", code: "READBACK_MISMATCH" },
    };
  }
}

function hasExactFrontdoorCredentialShape(parsed: URL): boolean {
  const keys = [...parsed.searchParams.keys()];
  return (
    (keys.length === 1 &&
      keys[0] === "sid" &&
      parsed.searchParams.getAll("sid").length === 1 &&
      Boolean(parsed.searchParams.get("sid"))) ||
    (keys.length === 2 &&
      new Set(keys).size === 2 &&
      keys.every((key) => key === "otp" || key === "cshc") &&
      parsed.searchParams.getAll("otp").length === 1 &&
      parsed.searchParams.getAll("cshc").length === 1 &&
      Boolean(parsed.searchParams.get("otp")) &&
      Boolean(parsed.searchParams.get("cshc")))
  );
}

function validateRequest(request: BrowserWorkerRequest): void {
  if (
    !Number.isInteger(request.captureLimit ?? DEFAULT_CAPTURE_LIMIT) ||
    (request.captureLimit ?? DEFAULT_CAPTURE_LIMIT) < 1 ||
    (request.captureLimit ?? DEFAULT_CAPTURE_LIMIT) > MAX_CAPTURE_LIMIT
  ) {
    throw new BrowserWorkerError("CAPTURE_LIMIT_INVALID");
  }
  if (request.startPath !== undefined && !isSafeRelativeStartPath(request.startPath)) {
    throw new BrowserWorkerError("START_PATH_INVALID");
  }
  if (request.mode === "CANDIDATE_READBACK") {
    if (
      !request.candidate ||
      !hasValidCandidateLocator(request.candidate)
    ) {
      throw new BrowserWorkerError("CANDIDATE_INTENT_INVALID");
    }
  } else if (request.mode === "BUSINESS_ACTION") {
    if (!request.businessAction || !hasValidBusinessAction(request.businessAction)) {
      throw new BrowserWorkerError("BUSINESS_ACTION_INVALID");
    }
  } else if (request.candidate || request.businessAction) {
    throw new BrowserWorkerError("CANDIDATE_NOT_ALLOWED_FOR_CAPTURE");
  }
}

function hasValidCandidateLocator(candidate: CandidateIntent): boolean {
  const hasRoleLocator =
    typeof candidate.accessibleName === "string" &&
    candidate.accessibleName.length >= 1 &&
    candidate.accessibleName.length <= 200 &&
    typeof candidate.role === "string";
  const host = candidate.hostAttribute;
  const hasHostLocator =
    host !== undefined &&
    (host.tag === "button" || host.tag === "lightning-button") &&
    host.attribute === "data-action" &&
    typeof host.expectedValue === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/.test(host.expectedValue);
  if (candidate.readback && !hasRoleLocator) return false;
  return hasRoleLocator || hasHostLocator;
}

function hasValidBusinessAction(action: BusinessActionIntent): boolean {
  return (
    Array.isArray(action.fields) &&
    /^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(action.objectApiName) &&
    action.fields.length >= 1 &&
    action.fields.length <= 8 &&
    action.fields.every(
      (field) =>
        /^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(field.fieldApiName) &&
        typeof field.value === "string" &&
        field.value.length >= 1 &&
        field.value.length <= 160 &&
        !containsSensitiveText(field.value),
    ) &&
    (action.submit.tag === "button" || action.submit.tag === "lightning-button") &&
    action.submit.attribute === "data-action" &&
    /^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/.test(action.submit.expectedValue) &&
    typeof action.successText === "string" &&
    action.successText.length >= 1 &&
    action.successText.length <= 160 &&
    !containsSensitiveText(action.successText)
  );
}

function cssString(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

async function fillBusinessField(
  scope: Locator,
  value: string,
  timeoutMs: number,
): Promise<string | undefined> {
  try {
    await scope.fill(value, { timeout: timeoutMs });
    return "direct-data-field-api";
  } catch {
    // Continue to scoped descendants and Salesforce base-component fallbacks.
  }

  // Salesforce Boolean fields render a real checkbox, which cannot be filled with text. Set the
  // checked state from the declared value so LDS receives a Boolean rather than a string.
  const checkbox = scope.locator('input[type="checkbox"]').first();
  try {
    if (await checkbox.count()) {
      const normalized = value.trim().toLowerCase();
      if (normalized === "true" || normalized === "false") {
        await checkbox.setChecked(normalized === "true", { timeout: timeoutMs });
        return "salesforce-checkbox-field";
      }
    }
  } catch {
    // Continue to the remaining strategies.
  }

  const native = scope.locator(
    'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]),textarea,[role="textbox"],[role="spinbutton"],[role="combobox"]',
  ).first();
  try {
    if (await native.count()) {
      await native.fill(value, { timeout: timeoutMs });
      return "direct-data-field-api";
    }
  } catch {
    // Continue to select and Lightning component fallbacks.
  }

  const select = scope.locator("select").first();
  try {
    if (await select.count()) {
      await select.selectOption(value, { timeout: timeoutMs });
      return "direct-data-field-api";
    }
  } catch {
    // Continue to Salesforce base-component fallback.
  }

  const lightningInputField = scope.locator("lightning-input-field").first();
  try {
    if (!(await lightningInputField.count())) return undefined;
    const applied = await lightningInputField.evaluate((element, nextValue) => {
      const target = element as HTMLElement & { value?: unknown };
      target.value = nextValue;
      target.setAttribute("value", nextValue);
      target.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        composed: true,
        data: nextValue,
      }));
      target.dispatchEvent(new CustomEvent("change", {
        bubbles: true,
        composed: true,
        detail: { value: nextValue },
      }));
      return String(target.value ?? target.getAttribute("value") ?? "") === nextValue;
    }, value, { timeout: timeoutMs });
    return applied ? "salesforce-lightning-input-field" : undefined;
  } catch {
    return undefined;
  }
}

async function capturePostSubmitSignal(
  page: Page,
  timeoutMs: number,
): Promise<BrowserWorkerReceipt["postSubmit"]> {
  const read = async (role: "alert" | "status") => {
    try {
      const locator = page.getByRole(role).first();
      if ((await locator.count()) === 0) return undefined;
      const text = ((await locator.textContent({ timeout: timeoutMs })) ?? "").trim();
      return text.length > 0 ? { digest: digest(text), length: text.length } : undefined;
    } catch {
      return undefined;
    }
  };
  try {
    const alert = await read("alert");
    const status = await read("status");
    return {
      alertPresent: alert !== undefined,
      alertTextDigest: alert?.digest,
      alertTextLength: alert?.length,
      statusPresent: status !== undefined,
      statusTextDigest: status?.digest,
      statusTextLength: status?.length,
    };
  } catch {
    return { alertPresent: false, statusPresent: false };
  }
}

function isSafeRelativeStartPath(value: string): boolean {
  if (
    typeof value !== "string" ||
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

async function captureDom(
  page: Page,
  limit: number,
  sensitiveValues: readonly string[],
): Promise<SanitizedDomCandidate[]> {
  const raw = await page.locator("button,input,select,textarea,a,[role]").evaluateAll(
    (elements, maximum) =>
      elements.slice(0, maximum).map((element, index) => {
        const html = element as HTMLElement;
        const input = element as HTMLInputElement;
        const tag = element.tagName.toLowerCase();
        const implicitRole =
          tag === "button"
            ? "button"
            : tag === "a"
              ? "link"
              : tag === "select"
                ? "combobox"
                : tag === "textarea" || tag === "input"
                  ? "textbox"
                  : "unknown";
        const label =
          element.getAttribute("aria-label") ??
          input.labels?.[0]?.textContent ??
          (tag === "button" || tag === "a" ? element.textContent ?? "" : "");
        const style = getComputedStyle(html);
        const rect = html.getBoundingClientRect();
        return {
          ordinal: index,
          tag,
          role: element.getAttribute("role") ?? implicitRole,
          accessibleName: label,
          visible:
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0,
          enabled: !(element as HTMLButtonElement | HTMLInputElement).disabled,
        };
      }),
    limit,
  );
  return raw.map((item) => ({
    ...item,
    accessibleName: sanitizeText(item.accessibleName, sensitiveValues),
  }));
}

async function navigate(
  page: Page,
  entryUrl: string,
  expectedLightningOrigin: string,
  timeoutMs: number,
): Promise<void> {
  try {
    await page.goto(entryUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    if (new URL(page.url()).origin !== expectedLightningOrigin) {
      await page.waitForURL(
        (url) => url.origin === expectedLightningOrigin,
        { waitUntil: "domcontentloaded", timeout: timeoutMs },
      );
    }
  } catch {
    throw new BrowserWorkerError("NAVIGATION_FAILED");
  }
}

function validateCurrentPageOrigin(page: Page, expectedOrigin: string): void {
  let current: URL;
  try {
    current = new URL(page.url());
  } catch {
    throw new BrowserWorkerError("POST_NAVIGATION_ORIGIN_INVALID");
  }
  if (current.origin !== expectedOrigin) {
    throw new BrowserWorkerError("POST_NAVIGATION_ORIGIN_MISMATCH");
  }
}

function parseUrl(value: string, code: string): URL {
  try {
    return new URL(value);
  } catch {
    throw new BrowserWorkerError(code);
  }
}

function validatePinnedOrigins(
  values: readonly string[],
  allowedHostname: (hostname: string) => boolean,
  code: string,
): readonly string[] {
  if (!Array.isArray(values) || values.length > 8) throw new BrowserWorkerError(code);
  const origins = values.map((value) => {
    const parsed = parseUrl(value, code);
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash || parsed.origin !== value ||
      !allowedHostname(parsed.hostname)
    ) throw new BrowserWorkerError(code);
    return parsed.origin;
  });
  if (new Set(origins).size !== origins.length || [...origins].sort().some((value, index) => value !== origins[index])) {
    throw new BrowserWorkerError(code);
  }
  return Object.freeze(origins);
}

function equalStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function collectSensitiveValues(parsed: URL, fullUrl: string): readonly string[] {
  const values = new Set<string>([fullUrl]);
  for (const value of parsed.searchParams.values()) {
    if (value) {
      values.add(value);
      try {
        values.add(decodeURIComponent(value));
      } catch {
        // The original opaque value is still protected.
      }
    }
  }
  for (const parameter of parsed.search.slice(1).split("&")) {
    const separator = parameter.indexOf("=");
    const rawValue = separator >= 0 ? parameter.slice(separator + 1) : "";
    if (rawValue) {
      values.add(rawValue);
      try {
        values.add(decodeURIComponent(rawValue.replace(/\+/g, " ")));
      } catch {
        // The original encoded value is still protected.
      }
    }
  }
  return Object.freeze([...values].sort((left, right) => right.length - left.length));
}

function sanitizeText(value: string, sensitiveValues: readonly string[]): string {
  let sanitized = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  for (const sensitive of sensitiveValues) {
    if (sensitive) {
      sanitized = sanitized.split(sensitive).join("[REDACTED]");
    }
  }
  sanitized = sanitized.replace(
    /https:\/\/[^\s"'<>]+\/secur\/frontdoor\.jsp\?[^\s"'<>]+/gi,
    "[REDACTED_SESSION_URL]",
  );
  return sanitized.slice(0, 200);
}

function enrollmentProjection(enrollment: VerifiedEnrollment): BrowserWorkerReceipt["enrollment"] {
  return Object.freeze({
    enrollmentIdDigest: digest(enrollment.enrollmentId),
    issuerDigest: digest(enrollment.issuer),
    originDigest: digest(
      canonicalJson({
        frontdoorOrigin: enrollment.canonicalFrontdoorOrigin,
        lightningOrigin: enrollment.canonicalLightningOrigin,
        navigationBridgeOrigins: enrollment.navigationBridgeOrigins,
        resourceOrigins: enrollment.resourceOrigins,
      }),
    ),
    orgBindingDigest: digest(enrollment.orgBinding),
    actorBindingDigest: digest(enrollment.actorBinding),
    policyDigest: enrollment.policyDigest,
  });
}

function blockedReceipt(
  executionId: string,
  mode: WorkerMode,
  code: string,
): BrowserWorkerReceipt {
  const receipt: BrowserWorkerReceipt = {
    schemaVersion: "1.0.0",
    executionId,
    inputDigest: digest(canonicalJson({ mode, blocked: true })),
    capabilityId: "automation.browser-worker",
    status: "BLOCKED",
    mode,
    enrollment: BLOCKED_RECEIPT_ENROLLMENT,
    lifecycle: [],
    capture: [],
    candidateCount: 0,
    cleanup: { contextClosed: true, browserClosed: true },
    error: { class: "POLICY_BLOCKED", code },
  };
  return Object.freeze(receipt);
}

function mapFailure(error: unknown): NonNullable<BrowserWorkerReceipt["error"]> {
  if (error instanceof BrowserWorkerError) {
    const policyCodes = new Set([
      "ENROLLMENT_EXPIRED",
      "MODE_NOT_AUTHORIZED",
      "CAPTURE_LIMIT_INVALID",
      "CANDIDATE_INTENT_INVALID",
      "CANDIDATE_NOT_ALLOWED_FOR_CAPTURE",
      "POST_NAVIGATION_ORIGIN_INVALID",
      "POST_NAVIGATION_ORIGIN_MISMATCH",
      "START_PATH_INVALID",
    ]);
    return {
      class: error.code === "NAVIGATION_FAILED" ? "NAVIGATION_FAILED" : policyCodes.has(error.code) ? "POLICY_BLOCKED" : "CAPTURE_FAILED",
      code: error.code,
    };
  }
  return { class: "CAPTURE_FAILED", code: "BROWSER_OPERATION_FAILED" };
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
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

function constantTimeDigestEqual(left: string, right: string): boolean {
  return timingSafeEqual(Buffer.from(digest(left), "hex"), Buffer.from(digest(right), "hex"));
}

function isBoundedIdentifier(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function isBinding(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/.test(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function containsSensitiveText(value: string): boolean {
  return (
    /(?:sid=|frontdoor\.jsp|Bearer\s+|Authorization:|api[_-]?key|password|secret|token)/i
      .test(value) ||
    /^[A-Za-z]:[\\/]/.test(value) ||
    value.startsWith("\\\\")
  );
}

function boundedTimeout(value: number, code: string): number {
  if (!Number.isInteger(value) || value < 1_000 || value > 120_000) {
    throw new BrowserWorkerError(code);
  }
  return value;
}

function privateHandle(kind: string): object {
  return Object.freeze(
    Object.defineProperty({}, "kind", {
      value: kind,
      enumerable: false,
      writable: false,
      configurable: false,
    }),
  );
}
