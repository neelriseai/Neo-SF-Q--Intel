import type { Locator, Page } from "playwright";

export interface LocatorIntent {
  objectApiName?: string;
  fieldApiName?: string;
  accessibleName?: string;
  action?: string;
}

export interface HealingResult {
  status: "HEALED" | "ABSTAINED";
  strategy?: string;
  candidateCount: number;
  confidence: number;
  locator?: Locator;
}

async function unique(locator: Locator) {
  const count = await locator.count();
  return { locator, count };
}

export async function healLocator(page: Page, intent: LocatorIntent): Promise<HealingResult> {
  const candidates: Array<{ strategy: string; locator: Locator; confidence: number }> = [];
  if (intent.objectApiName && intent.fieldApiName) {
    candidates.push({
      strategy: "salesforce-metadata-identity",
      locator: page.locator(
        `[data-object-api="${intent.objectApiName}"][data-field-api="${intent.fieldApiName}"]`,
      ),
      confidence: 0.98,
    });
  }
  if (intent.action) {
    candidates.push({
      strategy: "stable-action-identity",
      locator: page.locator(`[data-action="${intent.action}"]`),
      confidence: 0.96,
    });
  }
  if (intent.accessibleName) {
    candidates.push({
      strategy: "accessible-role-and-name",
      locator: page.getByRole("textbox", { name: intent.accessibleName }),
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
    if (result.count === 1 && (await result.locator.isVisible())) {
      return { status: "HEALED", strategy: candidate.strategy, candidateCount: 1, confidence: candidate.confidence, locator: result.locator };
    }
    if (result.count > 1) {
      return { status: "ABSTAINED", strategy: candidate.strategy, candidateCount: result.count, confidence: 0 };
    }
  }
  return { status: "ABSTAINED", candidateCount: 0, confidence: 0 };
}
