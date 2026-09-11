import { createHash } from "node:crypto";
import type { Locator, Page } from "playwright";
import {
  composedStateAllowsInteraction,
  discoverLocatorCandidate,
  type LocatorIntent,
} from "./locator-healer.js";

export type ProbeAssertion = "EDITABLE" | "ENABLED" | "VISIBLE";

export interface ProbeOriginalLocator {
  readonly kind: "ATTRIBUTE_EQUALS";
  readonly attribute: string;
  readonly value: string;
}

export type ProbeSemanticIdentity =
  | {
      readonly kind: "FIELD";
      readonly objectApiName: string;
      readonly fieldApiName: string;
    }
  | {
      readonly kind: "ACTION";
      readonly objectApiName: string;
      readonly action: string;
    };

export interface LocatorProbeObligation {
  readonly obligationId: string;
  readonly originalLocator: ProbeOriginalLocator;
  readonly semanticIdentity: ProbeSemanticIdentity;
  readonly assertions: readonly ProbeAssertion[];
}

export interface LocatorProbeTarget {
  readonly obligationPolicy: "COMPLETE_DECLARED_SET";
  readonly dataMutation: "FORBIDDEN";
  readonly obligations: readonly LocatorProbeObligation[];
}

export type ProbeStage = "BASELINE" | "STALE_AND_DISCOVER" | "RERUN";
export type ProbeOutcome =
  | "PASSED"
  | "LOCATOR_NOT_STALE"
  | "LOCATOR_NOT_FOUND"
  | "CANDIDATE_AMBIGUOUS"
  | "ASSERTION_MISMATCH";

export interface LocatorProbeObservation {
  readonly obligationIdDigest: string;
  readonly originalLocatorDigest: string;
  readonly semanticIdentityDigest: string;
  readonly outcome: ProbeOutcome;
  readonly staleOriginal: boolean;
  readonly candidateCount: number;
  readonly candidateEvidenceDigest?: string;
  readonly visible?: boolean;
  readonly enabled?: boolean;
  readonly editable?: boolean;
}

export interface LocatorProbeReport {
  readonly schemaVersion: "1.0.0";
  readonly capabilityId: "automation.locator-healing-probe";
  readonly evidenceClass: "HEADLESS_READ_ONLY_PROBE_NOT_ACCEPTANCE_RECEIPT";
  readonly acceptanceCredit: false;
  readonly releaseEligible: false;
  readonly stage: ProbeStage;
  readonly targetDigest: string;
  readonly status: "PASSED" | "FAILED";
  readonly obligationCount: number;
  readonly observations: readonly LocatorProbeObservation[];
}

export class HealingProbeError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "HealingProbeError";
  }
}

/**
 * Executes one read-only browser stage over the complete source-compiled target.
 * It never clicks, fills, submits, saves, or persists a replacement locator.
 */
export async function runLocatorProbe(
  page: Page,
  targetInput: Readonly<LocatorProbeTarget>,
  stage: ProbeStage,
): Promise<LocatorProbeReport> {
  if (!["BASELINE", "STALE_AND_DISCOVER", "RERUN"].includes(stage)) {
    throw new HealingProbeError("PROBE_STAGE_INVALID");
  }
  const target = validateTarget(targetInput);
  const observations: LocatorProbeObservation[] = [];
  for (const obligation of target.obligations) {
    observations.push(await observe(page, obligation, stage));
  }
  const passed = observations.every((item) => item.outcome === "PASSED") &&
    (stage !== "STALE_AND_DISCOVER" || observations.some((item) => item.staleOriginal));
  return Object.freeze({
    schemaVersion: "1.0.0",
    capabilityId: "automation.locator-healing-probe",
    evidenceClass: "HEADLESS_READ_ONLY_PROBE_NOT_ACCEPTANCE_RECEIPT",
    acceptanceCredit: false,
    releaseEligible: false,
    stage,
    targetDigest: digest(target),
    status: passed ? "PASSED" : "FAILED",
    obligationCount: target.obligations.length,
    observations: Object.freeze(observations),
  });
}

async function observe(
  page: Page,
  obligation: LocatorProbeObligation,
  stage: ProbeStage,
): Promise<LocatorProbeObservation> {
  const original = exactAttributeLocator(page, obligation.originalLocator);
  const originalCount = await original.count();
  const base = {
    obligationIdDigest: digest(obligation.obligationId),
    originalLocatorDigest: digest(obligation.originalLocator),
    semanticIdentityDigest: digest(obligation.semanticIdentity),
  };

  if (stage === "BASELINE" && originalCount !== 1) {
    return {
      ...base, outcome: "LOCATOR_NOT_FOUND", staleOriginal: originalCount === 0,
      candidateCount: originalCount,
    };
  }

  if (originalCount > 1) {
    return {
      ...base, outcome: "LOCATOR_NOT_STALE", staleOriginal: false,
      candidateCount: originalCount,
    };
  }
  const candidate = await discoverSemanticCandidate(page, obligation.semanticIdentity);
  if (!candidate.locator) {
    return {
      ...base,
      outcome: candidate.candidateCount > 1 ? "CANDIDATE_AMBIGUOUS" : "LOCATOR_NOT_FOUND",
      staleOriginal: originalCount === 0,
      candidateCount: candidate.candidateCount,
    };
  }
  if (originalCount === 1 && !(await isComposedDescendant(candidate.locator, original))) {
    return {
      ...base, outcome: "ASSERTION_MISMATCH", staleOriginal: false, candidateCount: 1,
    };
  }
  const observed = await assertionObservation(
    base, candidate.locator, obligation.assertions, 1, originalCount === 0,
  );
  return {
    ...observed,
    candidateEvidenceDigest: digest({
      semanticIdentity: obligation.semanticIdentity,
      strategy: candidate.strategy,
    }),
  };
}

async function discoverSemanticCandidate(
  page: Page,
  identity: ProbeSemanticIdentity,
): Promise<{ locator?: Locator; candidateCount: number; strategy: string }> {
  if (identity.kind === "FIELD") {
    const result = await discoverLocatorCandidate(page, {
      objectApiName: identity.objectApiName,
      fieldApiName: identity.fieldApiName,
    });
    return {
      locator: result.status === "CANDIDATE_DISCOVERED" ? result.locator : undefined,
      candidateCount: result.candidateCount,
      strategy: result.strategy ?? "salesforce-metadata-identity",
    };
  }
  const object = selectorToken(identity.objectApiName);
  const action = selectorToken(identity.action);
  const locator = page.locator(
    `[data-object-api="${object}"][data-action="${action}"],` +
    `[data-object-api="${object}"] [data-action="${action}"]`,
  ).filter({ visible: true });
  const candidateCount = await locator.count();
  if (
    candidateCount !== 1 || !(await locator.isEnabled()) ||
    !(await composedStateAllowsInteraction(locator)) ||
    !(await belongsToObjectScope(locator, identity.objectApiName))
  ) return { candidateCount, strategy: "scoped-action-identity" };
  return { locator, candidateCount, strategy: "scoped-action-identity" };
}

async function belongsToObjectScope(locator: Locator, objectApiName: string): Promise<boolean> {
  return locator.evaluate((element, expected) => {
    let current: Element | null = element;
    for (let depth = 0; current && depth <= 64; depth += 1) {
      const observed = current.getAttribute("data-object-api");
      if (observed !== null) return observed === expected;
      const root = current.getRootNode();
      current = current.assignedSlot ?? current.parentElement ??
        (root instanceof ShadowRoot ? root.host : null);
    }
    return false;
  }, objectApiName);
}

async function isComposedDescendant(candidate: Locator, original: Locator): Promise<boolean> {
  const handle = await original.elementHandle();
  if (!handle) return false;
  try {
    return await candidate.evaluate((element, ancestor) => {
      let current: Element | null = element;
      for (let depth = 0; current && depth <= 64; depth += 1) {
        if (current === ancestor) return true;
        const root = current.getRootNode();
        current = current.assignedSlot ?? current.parentElement ??
          (root instanceof ShadowRoot ? root.host : null);
      }
      return false;
    }, handle);
  } finally {
    await handle.dispose();
  }
}

async function assertionObservation(
  base: Pick<LocatorProbeObservation, "obligationIdDigest" | "originalLocatorDigest" | "semanticIdentityDigest">,
  locator: Locator,
  assertions: readonly ProbeAssertion[],
  candidateCount: number,
  staleOriginal: boolean,
): Promise<LocatorProbeObservation> {
  const visible = await locator.isVisible();
  const enabled = await locator.isEnabled();
  const interactionAllowed = await composedStateAllowsInteraction(locator);
  const editable = assertions.includes("EDITABLE") ? await locator.isEditable() : undefined;
  const matches = assertions.every((assertion) =>
    assertion === "VISIBLE" ? visible : assertion === "ENABLED"
      ? enabled && interactionAllowed : editable === true && interactionAllowed,
  );
  return {
    ...base,
    outcome: matches ? "PASSED" : "ASSERTION_MISMATCH",
    staleOriginal,
    candidateCount,
    visible,
    enabled,
    editable,
  };
}

function exactAttributeLocator(page: Page, value: ProbeOriginalLocator): Locator {
  return page.locator(`[${value.attribute}="${value.value}"]`);
}

function validateTarget(value: Readonly<LocatorProbeTarget>): LocatorProbeTarget {
  if (
    value.obligationPolicy !== "COMPLETE_DECLARED_SET" ||
    value.dataMutation !== "FORBIDDEN" ||
    !Array.isArray(value.obligations) ||
    value.obligations.length < 1 ||
    value.obligations.length > 200
  ) throw new HealingProbeError("PROBE_TARGET_INVALID");

  const normalized: LocatorProbeObligation[] = value.obligations.map((item) => {
    const assertions: readonly unknown[] = item.assertions;
    if (
      !boundedToken(item.obligationId, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/) ||
      item.originalLocator?.kind !== "ATTRIBUTE_EQUALS" ||
      !boundedToken(item.originalLocator.attribute, /^data-[a-z][a-z0-9-]{0,63}$/) ||
      !boundedToken(item.originalLocator.value, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/) ||
      !Array.isArray(assertions) || assertions.length < 1 || assertions.length > 3 ||
      assertions.some((entry) => typeof entry !== "string" ||
        !["EDITABLE", "ENABLED", "VISIBLE"].includes(entry)) ||
      [...assertions].sort().some((entry, index) => entry !== assertions[index]) ||
      new Set(assertions).size !== assertions.length ||
      !validIdentity(item.semanticIdentity)
    ) throw new HealingProbeError("PROBE_OBLIGATION_INVALID");
    const expectedAssertions = item.semanticIdentity.kind === "ACTION"
      ? ["ENABLED", "VISIBLE"]
      : ["EDITABLE", "ENABLED", "VISIBLE"];
    if (
      assertions.length !== expectedAssertions.length ||
      expectedAssertions.some((entry, index) => assertions[index] !== entry)
    ) {
      throw new HealingProbeError("PROBE_OBLIGATION_INVALID");
    }
    return Object.freeze({
      obligationId: item.obligationId,
      originalLocator: Object.freeze({ ...item.originalLocator }),
      semanticIdentity: Object.freeze({ ...item.semanticIdentity }),
      assertions: Object.freeze([...item.assertions]),
    });
  });
  const ids = normalized.map((item) => item.obligationId);
  const originals = normalized.map((item) => canonicalJson(item.originalLocator));
  const identities = normalized.map((item) => canonicalJson(item.semanticIdentity));
  if (
    new Set(ids).size !== ids.length || [...ids].sort().some((entry, index) => entry !== ids[index]) ||
    new Set(originals).size !== originals.length || new Set(identities).size !== identities.length
  ) {
    throw new HealingProbeError("PROBE_OBLIGATION_SET_INVALID");
  }
  return Object.freeze({
    obligationPolicy: "COMPLETE_DECLARED_SET",
    dataMutation: "FORBIDDEN",
    obligations: Object.freeze(normalized),
  });
}

function validIdentity(value: ProbeSemanticIdentity): boolean {
  if (!value || !boundedToken(value.objectApiName, /^[A-Za-z][A-Za-z0-9_]{0,199}$/)) return false;
  if (value.kind === "FIELD") {
    return boundedToken(value.fieldApiName, /^[A-Za-z][A-Za-z0-9_]{0,199}$/);
  }
  return value.kind === "ACTION" && boundedToken(value.action, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/);
}

function boundedToken(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.test(value);
}

function selectorToken(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function digest(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
