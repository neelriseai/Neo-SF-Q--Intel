import { expect, test } from "@playwright/test";
import {
  HealingProbeError,
  runLocatorProbe,
  type LocatorProbeTarget,
} from "../src/healing-probe.js";

const target = (): LocatorProbeTarget => ({
  obligationPolicy: "COMPLETE_DECLARED_SET",
  dataMutation: "FORBIDDEN",
  obligations: [
    {
      obligationId: "field:Amount",
      originalLocator: {
        kind: "ATTRIBUTE_EQUALS",
        attribute: "data-testid",
        value: "deal-baseline-Amount",
      },
      semanticIdentity: {
        kind: "FIELD",
        objectApiName: "Opportunity",
        fieldApiName: "Amount",
      },
      assertions: ["EDITABLE", "ENABLED", "VISIBLE"],
    },
    {
      obligationId: "probe:save-evaluate",
      originalLocator: {
        kind: "ATTRIBUTE_EQUALS",
        attribute: "data-testid",
        value: "save-evaluate-v1",
      },
      semanticIdentity: {
        kind: "ACTION",
        objectApiName: "Opportunity",
        action: "save-evaluate",
      },
      assertions: ["ENABLED", "VISIBLE"],
    },
  ],
});

test("proves a complete baseline without mutating the page", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Opportunity">
      <div data-field-api="Amount" data-testid="deal-baseline-Amount"><input value="42" /></div>
      <button data-testid="save-evaluate-v1" data-action="save-evaluate">Save</button>
    </section>
  `);
  const before = await page.content();
  const report = await runLocatorProbe(page, target(), "BASELINE");
  expect(report.status).toBe("PASSED");
  expect(report.obligationCount).toBe(2);
  expect(report.acceptanceCredit).toBe(false);
  expect(report.observations.every((item) => item.staleOriginal === false)).toBe(true);
  expect(await page.content()).toBe(before);
});

test("proves stale hooks, discovers semantic candidates, and reruns assertions", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Opportunity" data-locator-variant="reordered">
      <div data-field-api="Amount" data-testid="deal-reordered-Amount"><input value="42" /></div>
      <button data-testid="save-evaluate-v2" data-action="save-evaluate">Save</button>
    </section>
  `);
  for (const stage of ["STALE_AND_DISCOVER", "RERUN"] as const) {
    const report = await runLocatorProbe(page, target(), stage);
    expect(report.status).toBe("PASSED");
    expect(report.observations).toHaveLength(2);
    expect(report.observations.every((item) => item.candidateEvidenceDigest)).toBe(true);
    expect(report.observations.every((item) => item.staleOriginal)).toBe(true);
  }
});

test("rejects a decoy original hook that is not the semantic control boundary", async ({ page }) => {
  await page.setContent(`
    <div data-testid="deal-baseline-Amount">decoy</div>
    <section data-object-api="Opportunity">
      <div data-field-api="Amount"><input /></div>
      <button data-action="save-evaluate">Save</button>
    </section>
  `);
  const report = await runLocatorProbe(page, target(), "BASELINE");
  expect(report.status).toBe("FAILED");
  expect(report.observations[0].outcome).toBe("ASSERTION_MISMATCH");
});

test("scopes action identity to the declared Salesforce object", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Account"><button data-action="save-evaluate">Wrong</button></section>
    <section data-object-api="Opportunity">
      <div data-field-api="Amount"><input /></div>
    </section>
  `);
  const report = await runLocatorProbe(page, target(), "STALE_AND_DISCOVER");
  expect(report.status).toBe("FAILED");
  expect(report.observations[1].outcome).toBe("LOCATOR_NOT_FOUND");
});

test("rejects custom-element and composed-ancestor disabled actions", async ({ page }) => {
  for (const action of [
    '<lightning-button data-action="save-evaluate" disabled>Save</lightning-button>',
    '<div disabled><button data-action="save-evaluate">Save</button></div>',
  ]) {
    await page.setContent(`
      <section data-object-api="Opportunity">
        <div data-field-api="Amount"><input /></div>${action}
      </section>
    `);
    const report = await runLocatorProbe(page, target(), "STALE_AND_DISCOVER");
    expect(report.status).toBe("FAILED");
    expect(report.observations[1].outcome).toBe("LOCATOR_NOT_FOUND");
  }
});

test("complete rerun preserves unaffected original locators while requiring some real drift", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Opportunity">
      <div data-field-api="Amount" data-testid="deal-baseline-Amount"><input /></div>
      <button data-action="save-evaluate" data-testid="save-evaluate-v2">Save</button>
    </section>
  `);
  const stale = await runLocatorProbe(page, target(), "STALE_AND_DISCOVER");
  expect(stale.status).toBe("PASSED");
  expect(stale.observations.map((item) => item.staleOriginal)).toEqual([false, true]);
  expect((await runLocatorProbe(page, target(), "RERUN")).status).toBe("PASSED");
});

test("fails closed for an incomplete, ambiguous, or non-editable target", async ({ page }) => {
  const reversed = target();
  const reversedTarget = { ...reversed, obligations: [...reversed.obligations].reverse() };
  await expect(runLocatorProbe(page, reversedTarget, "BASELINE")).rejects.toMatchObject({
    code: "PROBE_OBLIGATION_SET_INVALID",
  } satisfies Partial<HealingProbeError>);

  await page.setContent(`
    <section data-object-api="Opportunity">
      <div data-field-api="Amount"><input /><input /></div>
      <button data-action="save-evaluate" disabled>Save</button>
    </section>
  `);
  const report = await runLocatorProbe(page, target(), "STALE_AND_DISCOVER");
  expect(report.status).toBe("FAILED");
  expect(report.observations.map((item) => item.outcome)).toEqual([
    "CANDIDATE_AMBIGUOUS",
    "LOCATOR_NOT_FOUND",
  ]);
});

test("rejects selector injection and action editability claims before DOM access", async ({ page }) => {
  const injected = target();
  const injectedTarget = {
    ...injected,
    obligations: injected.obligations.map((item, index) => index === 0 ? {
      ...item, originalLocator: { ...item.originalLocator, value: 'x"] *' },
    } : item),
  };
  await expect(runLocatorProbe(page, injectedTarget, "BASELINE")).rejects.toMatchObject({
    code: "PROBE_OBLIGATION_INVALID",
  });

  const actionEditable = target();
  const actionEditableTarget = {
    ...actionEditable,
    obligations: actionEditable.obligations.map((item, index) => index === 1 ? {
      ...item, assertions: ["EDITABLE", "ENABLED", "VISIBLE"] as const,
    } : item),
  };
  await expect(runLocatorProbe(page, actionEditableTarget, "BASELINE")).rejects.toMatchObject({
    code: "PROBE_OBLIGATION_INVALID",
  });
});
