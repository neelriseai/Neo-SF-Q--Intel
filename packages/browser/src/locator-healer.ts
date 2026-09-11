import type { Locator, Page } from "playwright";

export interface LocatorIntent {
  objectApiName?: string;
  fieldApiName?: string;
  accessibleName?: string;
  action?: string;
}

export type LocatorLifecycleState =
  | "CANDIDATE_DISCOVERED"
  | "PROPOSAL_APPROVED"
  | "ACTION_APPLIED"
  | "OUTCOME_VERIFIED";

export interface LocatorCandidateResult {
  status: "CANDIDATE_DISCOVERED" | "ABSTAINED";
  lifecycle: readonly LocatorLifecycleState[];
  strategy?: string;
  candidateCount: number;
  confidence: number;
  locator?: Locator;
}

async function unique(locator: Locator) {
  const count = await locator.count();
  return { locator, count };
}

const MAX_SCOPE_DEPTH = 32;
const MAX_STATE_DEPTH = 64;
const MAX_CANDIDATE_COUNT = 100;
const NATIVE_CONTROLS =
  'input,textarea,select,[contenteditable="true"],[contenteditable=""]';
const SEMANTIC_CONTROLS =
  '[role="textbox"],[role="spinbutton"],[role="combobox"],[role="checkbox"],[role="switch"]';
const NON_VALUE_INPUTS = ["submit", "reset", "button", "image", "file", "hidden"]
  .map((type) => `input[type="${type}" i]`).join(",");
const NATIVE_VALUE_CONTROLS = `:is(${NATIVE_CONTROLS}):not(${NON_VALUE_INPUTS})`;
const VALUE_CONTROLS = `:is(${NATIVE_CONTROLS},${SEMANTIC_CONTROLS}):not(${NON_VALUE_INPUTS})`;
const ACCESSIBLE_VALUE_ROLES = [
  "textbox", "searchbox", "spinbutton", "combobox", "checkbox", "radio", "switch", "slider",
] as const;

function attributeValue(value: unknown): string | undefined {
  if (
    typeof value !== "string" || !value || value.length > 200 ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) return undefined;
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function metadataAbstention(count: number): LocatorCandidateResult {
  return {
    status: "ABSTAINED",
    lifecycle: [],
    strategy: "salesforce-metadata-identity",
    candidateCount: Math.min(count, MAX_CANDIDATE_COUNT + 1),
    confidence: 0,
  };
}

export async function composedStateAllowsInteraction(locator: Locator): Promise<boolean> {
  return locator.evaluate((element, maximumDepth) => {
    let current: Element | null = element;
    for (let depth = 0; current && depth <= maximumDepth; depth += 1) {
      if (
        ["aria-disabled", "aria-readonly", "aria-hidden"].some(
          (name) => current!.getAttribute(name)?.trim().toLowerCase() === "true",
        ) ||
        ["disabled", "readonly", "inert", "hidden"].some((name) => current!.hasAttribute(name))
      ) return false;
      const root = current.getRootNode();
      current = current.assignedSlot ?? current.parentElement ??
        (root instanceof ShadowRoot ? root.host : null);
    }
    // Exhausting the bound is an unknown ancestor state, never an editable claim.
    return current === null;
  }, MAX_STATE_DEPTH);
}

async function isSingleComboboxComposite(controls: Locator): Promise<boolean> {
  return controls.evaluateAll((elements, options) => {
    if (elements.length !== 2) return false;
    const native = elements.filter((element) => element.matches(options.nativeSelector));
    if (native.length !== 1) return false;
    const wrapper = elements.find((element) => element !== native[0]);
    if (wrapper?.getAttribute("role") !== "combobox") return false;
    let current: Element | null = native[0];
    for (let depth = 0; current && depth <= options.maximumDepth; depth += 1) {
      if (current === wrapper) return true;
      const root = current.getRootNode();
      current = current.assignedSlot ?? current.parentElement ??
        (root instanceof ShadowRoot ? root.host : null);
    }
    return false;
  }, { nativeSelector: NATIVE_VALUE_CONTROLS, maximumDepth: MAX_SCOPE_DEPTH });
}

async function belongsToMetadataScope(
  locator: Locator,
  objectApiName: string,
  fieldApiName: string,
): Promise<boolean> {
  return locator.evaluate(
    (element, scope) => {
      let current: Element | null = element;
      let fieldFound = false;
      for (let depth = 0; current && depth <= scope.maximumDepth; depth += 1) {
        const field = current.getAttribute("data-field-api");
        if (field !== null) {
          // A nested field is another identity boundary, even inside the same object.
          if (field !== scope.fieldApiName || fieldFound) return false;
          fieldFound = true;
        }
        const object = current.getAttribute("data-object-api");
        if (object !== null) return fieldFound && object === scope.objectApiName;
        const root = current.getRootNode();
        current = current.assignedSlot ?? current.parentElement ??
          (root instanceof ShadowRoot ? root.host : null);
      }
      return false;
    },
    { objectApiName, fieldApiName, maximumDepth: MAX_SCOPE_DEPTH },
  );
}

async function discoverMetadataControl(
  page: Page,
  objectApiName: string,
  fieldApiName: string,
): Promise<LocatorCandidateResult> {
  const object = attributeValue(objectApiName);
  const field = attributeValue(fieldApiName);
  if (object === undefined || field === undefined) return metadataAbstention(0);
  // Playwright CSS locators pierce open shadow roots. XPath and DOM closest() do not.
  const objectSelector = `[data-object-api="${object}"]`;
  const fieldSelector = `[data-field-api="${field}"]`;
  const boundary = page.locator(
    `${objectSelector}${fieldSelector},${objectSelector} ${fieldSelector}`,
  );
  const boundaryCount = await boundary.count();
  if (boundaryCount !== 1) return metadataAbstention(boundaryCount);
  if (!(await belongsToMetadataScope(boundary, objectApiName, fieldApiName))) {
    return metadataAbstention(0);
  }

  // The field identity may be on a wrapper. Return the actual editable control,
  // never a wrapper that merely happens to be visible. The selector union counts
  // each element once; only a proven combobox wrapper of its sole native value
  // control represents one composite. Distinct sibling controls remain ambiguous.
  let control = boundary
    .and(page.locator(VALUE_CONTROLS))
    .or(boundary.locator(VALUE_CONTROLS))
    .filter({ visible: true });
  let count = await control.count();
  if (count === 2) {
    const native = boundary
      .and(page.locator(NATIVE_VALUE_CONTROLS))
      .or(boundary.locator(NATIVE_VALUE_CONTROLS));
    if (await native.count() === 1 && await isSingleComboboxComposite(control)) {
      control = native;
      count = 1;
    }
  }
  if (count !== 1) return metadataAbstention(count);
  if (
    !(await control.isVisible()) ||
    !(await control.isEnabled()) ||
    !(await control.isEditable()) ||
    !(await composedStateAllowsInteraction(control)) ||
    !(await belongsToMetadataScope(control, objectApiName, fieldApiName))
  ) {
    return metadataAbstention(0);
  }
  return {
    status: "CANDIDATE_DISCOVERED",
    lifecycle: ["CANDIDATE_DISCOVERED"],
    strategy: "salesforce-metadata-identity",
    candidateCount: 1,
    confidence: 0.98,
    locator: control,
  };
}

export async function discoverLocatorCandidate(
  page: Page,
  intent: LocatorIntent,
): Promise<LocatorCandidateResult> {
  if (
    intent.accessibleName !== undefined && (
      typeof intent.accessibleName !== "string" || !intent.accessibleName.trim() ||
      intent.accessibleName.length > 200 || /[\u0000-\u001f\u007f]/.test(intent.accessibleName)
    )
  ) {
    return { status: "ABSTAINED", lifecycle: [], candidateCount: 0, confidence: 0 };
  }
  if (intent.objectApiName !== undefined || intent.fieldApiName !== undefined) {
    if (intent.objectApiName === undefined || intent.fieldApiName === undefined) {
      return metadataAbstention(0);
    }
    // Explicit metadata identity cannot fall through to an unscoped label match
    // on another object or a similarly named field.
    try {
      return await discoverMetadataControl(page, intent.objectApiName, intent.fieldApiName);
    } catch {
      return metadataAbstention(0);
    }
  }
  const candidates: Array<{ strategy: string; locator: Locator; confidence: number }> = [];
  if (intent.action !== undefined) {
    const action = attributeValue(intent.action);
    if (action === undefined) {
      return {
        status: "ABSTAINED", lifecycle: [], strategy: "stable-action-identity",
        candidateCount: 0, confidence: 0,
      };
    }
    candidates.push({
      strategy: "stable-action-identity",
      locator: page.locator(`[data-action="${action}"]`),
      confidence: 0.96,
    });
  } else if (intent.accessibleName) {
    let semanticControl = page.getByRole(ACCESSIBLE_VALUE_ROLES[0], {
      name: intent.accessibleName, exact: true,
    });
    for (const role of ACCESSIBLE_VALUE_ROLES.slice(1)) {
      semanticControl = semanticControl.or(page.getByRole(role, {
        name: intent.accessibleName, exact: true,
      }));
    }
    candidates.push({
      strategy: "accessible-role-and-name",
      locator: semanticControl,
      confidence: 0.85,
    });
    candidates.push({
      strategy: "visible-label",
      locator: page.getByLabel(intent.accessibleName, { exact: true }),
      confidence: 0.78,
    });
  }

  for (const candidate of candidates) {
    const result = await unique(candidate.locator);
    if (
      result.count === 1 && await result.locator.isVisible() &&
      await result.locator.isEnabled() && await composedStateAllowsInteraction(result.locator) &&
      (candidate.strategy === "stable-action-identity" || (
        await result.locator.evaluate((element, selector) => element.matches(selector), VALUE_CONTROLS) &&
        await result.locator.isEditable()
      ))
    ) {
      return {
        status: "CANDIDATE_DISCOVERED",
        lifecycle: ["CANDIDATE_DISCOVERED"],
        strategy: candidate.strategy,
        candidateCount: 1,
        confidence: candidate.confidence,
        locator: result.locator,
      };
    }
    if (result.count > 1) {
      return {
        status: "ABSTAINED",
        lifecycle: [],
        strategy: candidate.strategy,
        candidateCount: result.count,
        confidence: 0,
      };
    }
  }
  return { status: "ABSTAINED", lifecycle: [], candidateCount: 0, confidence: 0 };
}
