import { expect, test } from "@playwright/test";
import { healLocator } from "../src/locator-healer.js";

test("heals through Salesforce metadata identity after layout drift", async ({ page }) => {
  await page.setContent(`
    <section><label>Strategic discount<input data-object-api="Opportunity"
      data-field-api="Discount__c" aria-label="Strategic discount" /></label></section>
  `);
  const result = await healLocator(page, {
    objectApiName: "Opportunity",
    fieldApiName: "Discount__c",
    accessibleName: "Strategic discount",
  });
  expect(result.status).toBe("HEALED");
  expect(result.strategy).toBe("salesforce-metadata-identity");
  expect(result.confidence).toBeGreaterThan(0.9);
});

test("abstains when target identity is ambiguous", async ({ page }) => {
  await page.setContent(`
    <input data-action="save" /><button data-action="save">Save</button>
  `);
  const result = await healLocator(page, { action: "save" });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(2);
});
