import { createHash } from "node:crypto";
import type { Locator, Page } from "playwright";
import {
  composedStateAllowsInteraction,
  discoverLocatorCandidate,
} from "./locator-healer.js";

export type ProbeAssertion = "EDITABLE" | "ENABLED" | "VISIBLE";

// Candidate push bounds. A large page truncates; it never throws and never streams unbounded DOM.
const MAX_CANDIDATES = 40;
const MAX_ATTRS_PER_CANDIDATE = 24;
const MAX_NEARBY = 12;
const MAX_STRUCTURE_DEPTH = 6;
export const CANDIDATE_SELECTOR =
  "input,select,textarea,button,a,[role=combobox],[role=textbox],[role=button]," +
  "lightning-input-field";
// Clear-text vocabulary is allowlisted so an author-chosen tag or role can never smuggle data out.
const STRUCTURAL_TOKEN = /^[a-z][a-z0-9-]{0,63}$/;
const ATTRIBUTE_NAME = /^[a-z_:][a-z0-9_.:-]{0,63}$/;

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

/**
 * One sanitized interactive element. Every author-controlled value is reduced to a 16-hex digest
 * before it is recorded; only structural vocabulary (tag names, roles, attribute names) is clear.
 */
export interface DomCandidate {
  readonly ordinal: number;
  readonly tag: string;
  readonly role: string;
  readonly structure: string;
  readonly attrNames: readonly string[];
  readonly attrHashes: Readonly<Record<string, string>>;
  readonly nameDigest: string | null;
  readonly nearby: readonly string[];
  readonly visible: boolean;
  readonly enabled: boolean;
}

export interface DomEvidence {
  readonly capturedForOutcome: ProbeOutcome;
  readonly candidateCount: number;
  readonly truncated: boolean;
  readonly candidates: readonly DomCandidate[];
}

export interface LocatorProbeOptions {
  /** Push a sanitized candidate list when the deterministic tier abstains. Defaults to false. */
  readonly captureCandidates?: boolean;
}

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
  readonly domEvidence?: DomEvidence;
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
  targetInput: unknown,
  stage: ProbeStage,
  options?: LocatorProbeOptions,
): Promise<LocatorProbeReport> {
  if (!["BASELINE", "STALE_AND_DISCOVER", "RERUN"].includes(stage)) {
    throw new HealingProbeError("PROBE_STAGE_INVALID");
  }
  const target = validateTarget(targetInput);
  const captureCandidates = options?.captureCandidates === true;
  const observations: LocatorProbeObservation[] = [];
  // The probe never mutates the page, so one capture serves every abstaining obligation.
  let captured: CapturedCandidates | undefined;
  for (const obligation of target.obligations) {
    const observation = await observe(page, obligation, stage);
    if (!captureCandidates || !deterministicTierAbstained(observation.outcome)) {
      observations.push(observation);
      continue;
    }
    captured ??= await captureDomCandidates(page);
    observations.push({
      ...observation,
      domEvidence: Object.freeze({
        capturedForOutcome: observation.outcome,
        candidateCount: captured.total,
        truncated: captured.total > captured.candidates.length,
        candidates: captured.candidates,
      }),
    });
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

interface RawDomCandidate {
  readonly tag: string;
  readonly role: string;
  readonly structure: string;
  readonly attributes: readonly (readonly [string, string])[];
  readonly name: string;
  readonly nearby: readonly string[];
  readonly visible: boolean;
  readonly enabled: boolean;
}

export interface CapturedCandidates {
  readonly total: number;
  readonly candidates: readonly DomCandidate[];
}

function deterministicTierAbstained(outcome: ProbeOutcome): boolean {
  return outcome === "LOCATOR_NOT_FOUND" || outcome === "CANDIDATE_AMBIGUOUS";
}

/**
 * Reads a bounded interactive-element list and sanitizes it in this process. Raw values are held
 * only long enough to digest them; nothing raw is ever placed on an observation or a report.
 */
export async function captureDomCandidates(scope: Page | Locator): Promise<CapturedCandidates> {
  let raw: { total: number; raw: RawDomCandidate[] };
  try {
    raw = await scope.locator(CANDIDATE_SELECTOR).evaluateAll(readCandidates, {
      candidates: MAX_CANDIDATES,
      attributes: MAX_ATTRS_PER_CANDIDATE,
      nearby: MAX_NEARBY,
      structureDepth: MAX_STRUCTURE_DEPTH,
    });
  } catch {
    return Object.freeze({ total: 0, candidates: Object.freeze([]) });
  }
  return Object.freeze({
    total: raw.total,
    candidates: Object.freeze(raw.raw.map(sanitizeDomCandidate)),
  });
}

function sanitizeDomCandidate(raw: RawDomCandidate, ordinal: number): DomCandidate {
  const attrHashes: Record<string, string> = {};
  for (const [name, value] of raw.attributes) {
    if (!ATTRIBUTE_NAME.test(name) || name in attrHashes) continue;
    attrHashes[name] = valueDigest(value);
  }
  const name = raw.name.trim();
  return Object.freeze({
    ordinal,
    tag: structuralToken(raw.tag),
    role: structuralToken(raw.role),
    structure: raw.structure,
    attrNames: Object.freeze(Object.keys(attrHashes).sort()),
    attrHashes: Object.freeze(attrHashes),
    nameDigest: name.length === 0 ? null : valueDigest(name),
    nearby: Object.freeze(raw.nearby.slice(0, MAX_NEARBY).map(structuralToken)),
    visible: raw.visible === true,
    enabled: raw.enabled === true,
  });
}

function structuralToken(value: unknown): string {
  return typeof value === "string" && STRUCTURAL_TOKEN.test(value) ? value : "unknown";
}

/** 16-hex digest: the first 16 hex characters of the SHA-256 of the UTF-8 value. */
function valueDigest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex").slice(0, 16);
}

/** Serialized into the page. Returns raw values that the caller digests before recording them. */
function readCandidates(
  elements: Element[],
  bounds: { candidates: number; attributes: number; nearby: number; structureDepth: number },
): { total: number; raw: RawDomCandidate[] } {
  const token = (value: string): string =>
    /^[a-z][a-z0-9-]{0,63}$/.test(value) ? value : "unknown";
  const parentOf = (element: Element): Element | null => {
    const root = element.getRootNode();
    return element.assignedSlot ?? element.parentElement ??
      (root instanceof ShadowRoot ? root.host : null);
  };
  const roleOf = (element: Element): string => {
    const explicit = element.getAttribute("role");
    if (explicit && explicit.trim()) return token(explicit.trim().toLowerCase());
    const tag = element.tagName.toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a") return element.hasAttribute("href") ? "link" : "generic";
    if (tag === "textarea") return "textbox";
    if (tag === "select") {
      const select = element as HTMLSelectElement;
      return select.multiple || select.size > 1 ? "listbox" : "combobox";
    }
    if (tag === "input") {
      const type = (element.getAttribute("type") ?? "text").trim().toLowerCase();
      const roles: Record<string, string> = {
        button: "button", checkbox: "checkbox", radio: "radio", range: "slider",
        number: "spinbutton", reset: "button", search: "searchbox", submit: "button",
      };
      return roles[type] ?? "textbox";
    }
    return "generic";
  };
  const structureOf = (element: Element): string => {
    const segments: string[] = [];
    let current: Element | null = element;
    for (let depth = 0; current && depth < bounds.structureDepth; depth += 1) {
      const role = current.getAttribute("role");
      const tag = token(current.tagName.toLowerCase());
      segments.push(role && role.trim()
        ? `${tag}[role=${token(role.trim().toLowerCase())}]`
        : tag);
      current = parentOf(current);
    }
    return segments.reverse().join(">");
  };
  const nameOf = (element: Element): string => {
    const aria = element.getAttribute("aria-label");
    if (aria && aria.trim()) return aria.trim();
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy && labelledBy.trim()) {
      const root = element.getRootNode() as Document | ShadowRoot;
      const text = labelledBy.trim().split(/\s+/)
        .map((id) => root.getElementById?.(id)?.textContent ?? "")
        .join(" ").trim();
      if (text) return text;
    }
    const labels = (element as HTMLInputElement).labels;
    if (labels && labels.length > 0) {
      const text = Array.from(labels).map((label) => label.textContent ?? "").join(" ").trim();
      if (text) return text;
    }
    const tag = element.tagName.toLowerCase();
    if (tag === "button" || tag === "a") {
      const text = (element.textContent ?? "").trim();
      if (text) return text;
    }
    return (element.getAttribute("placeholder") ?? element.getAttribute("title") ?? "").trim();
  };
  const visibleOf = (element: Element): boolean => {
    const view = element.ownerDocument.defaultView;
    const style = view ? view.getComputedStyle(element) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const enabledOf = (element: Element): boolean => {
    if ((element as HTMLInputElement).disabled === true) return false;
    let current: Element | null = element;
    for (let depth = 0; current && depth <= 64; depth += 1) {
      if (current.hasAttribute("disabled") || current.getAttribute("aria-disabled") === "true") {
        return false;
      }
      current = parentOf(current);
    }
    return true;
  };
  const nearbyOf = (element: Element): string[] => {
    const result: string[] = [];
    const parent = parentOf(element);
    if (!parent) return result;
    result.push(token(parent.tagName.toLowerCase()));
    for (const sibling of Array.from(parent.children)) {
      if (result.length >= bounds.nearby) break;
      result.push(token(sibling.tagName.toLowerCase()));
    }
    return result;
  };

  const seen = new Set<Element>();
  const unique: Element[] = [];
  for (const element of elements) {
    if (seen.has(element)) continue;
    seen.add(element);
    unique.push(element);
  }
  return {
    total: unique.length,
    raw: unique.slice(0, bounds.candidates).map((element) => ({
      tag: token(element.tagName.toLowerCase()),
      role: roleOf(element),
      structure: structureOf(element),
      attributes: Array.from(element.attributes)
        .map((attribute) => [attribute.name.toLowerCase(), attribute.value] as [string, string])
        .sort((left, right) => left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0)
        .slice(0, bounds.attributes),
      name: nameOf(element),
      nearby: nearbyOf(element),
      visible: visibleOf(element),
      enabled: enabledOf(element),
    })),
  };
}

function exactAttributeLocator(page: Page, value: ProbeOriginalLocator): Locator {
  return page.locator(`[${value.attribute}="${value.value}"]`);
}

function validateTarget(value: unknown): LocatorProbeTarget {
  try {
    return validateClosedTarget(value);
  } catch (error) {
    if (error instanceof HealingProbeError) throw error;
    throw new HealingProbeError("PROBE_TARGET_INVALID");
  }
}

function validateClosedTarget(value: unknown): LocatorProbeTarget {
  if (
    !closedObject(value, ["obligationPolicy", "dataMutation", "obligations"]) ||
    value.obligationPolicy !== "COMPLETE_DECLARED_SET" ||
    value.dataMutation !== "FORBIDDEN" ||
    !closedArray(value.obligations, 1, 64)
  ) throw new HealingProbeError("PROBE_TARGET_INVALID");

  const normalized: LocatorProbeObligation[] = value.obligations.map((item) => {
    if (
      !closedObject(item, ["obligationId", "originalLocator", "semanticIdentity", "assertions"]) ||
      !boundedToken(item.obligationId, /^[A-Za-z][A-Za-z0-9_.:-]{0,199}$/) ||
      !closedObject(item.originalLocator, ["kind", "attribute", "value"]) ||
      item.originalLocator.kind !== "ATTRIBUTE_EQUALS" ||
      !boundedToken(item.originalLocator.attribute, /^data-[a-z][a-z0-9-]{0,63}$/) ||
      !boundedToken(item.originalLocator.value, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/) ||
      !closedArray(item.assertions, 2, 3) ||
      !validIdentity(item.semanticIdentity)
    ) throw new HealingProbeError("PROBE_OBLIGATION_INVALID");
    const assertions = item.assertions;
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
      originalLocator: Object.freeze({
        kind: "ATTRIBUTE_EQUALS" as const,
        attribute: item.originalLocator.attribute,
        value: item.originalLocator.value,
      }),
      semanticIdentity: Object.freeze(item.semanticIdentity.kind === "FIELD" ? {
        kind: "FIELD" as const,
        objectApiName: item.semanticIdentity.objectApiName,
        fieldApiName: item.semanticIdentity.fieldApiName,
      } : {
        kind: "ACTION" as const,
        objectApiName: item.semanticIdentity.objectApiName,
        action: item.semanticIdentity.action,
      }),
      assertions: Object.freeze([...assertions] as ProbeAssertion[]),
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

function validIdentity(value: unknown): value is ProbeSemanticIdentity {
  if (closedObject(value, ["kind", "objectApiName", "fieldApiName"]) && value.kind === "FIELD") {
    return boundedToken(value.objectApiName, /^[A-Za-z][A-Za-z0-9_]{0,199}$/) &&
      boundedToken(value.fieldApiName, /^[A-Za-z][A-Za-z0-9_]{0,199}$/);
  }
  return closedObject(value, ["kind", "objectApiName", "action"]) && value.kind === "ACTION" &&
    boundedToken(value.objectApiName, /^[A-Za-z][A-Za-z0-9_]{0,199}$/) &&
    boundedToken(value.action, /^[A-Za-z][A-Za-z0-9_-]{0,199}$/);
}

function closedObject(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  const observed = Reflect.ownKeys(value);
  return observed.length === keys.length && keys.every((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return descriptor !== undefined && descriptor.enumerable === true && "value" in descriptor;
  });
}

function closedArray(value: unknown, minimum: number, maximum: number): value is unknown[] {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype ||
    value.length < minimum || value.length > maximum ||
    Reflect.ownKeys(value).length !== value.length + 1) return false;
  for (let index = 0; index < value.length; index += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) return false;
  }
  return true;
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
