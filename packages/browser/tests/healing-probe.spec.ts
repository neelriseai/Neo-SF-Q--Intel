import { expect, test } from "@playwright/test";
import type { Page } from "playwright";
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

const inaccessiblePage = new Proxy({}, {
  get() { throw new Error("DOM_ACCESS_MUST_NOT_OCCUR"); },
}) as Page;

for (const level of ["target", "obligation", "original", "field", "action"] as const) {
  test(`rejects extra ${level} keys before DOM access`, async () => {
    const input = structuredClone(target());
    const selected = level === "target" ? input : level === "obligation" ? input.obligations[0]
      : level === "original" ? input.obligations[0].originalLocator
      : input.obligations[level === "field" ? 0 : 1].semanticIdentity;
    Object.assign(selected, { unexpected: "UNTRUSTED_INPUT_CANARY" });
    await expect(runLocatorProbe(inaccessiblePage, input, "BASELINE")).rejects.toMatchObject({
      code: level === "target" ? "PROBE_TARGET_INVALID" : "PROBE_OBLIGATION_INVALID",
    });
  });
}

for (const identity of ["originalLocator", "semanticIdentity"] as const) {
  test(`rejects duplicate ${identity} even with different unknown nonce properties`, async () => {
    const original = target().obligations[0];
    const duplicate = {
      ...original, obligationId: "field:Second",
      originalLocator: { ...original.originalLocator, value: "another-hook" },
      semanticIdentity: { kind: "FIELD", objectApiName: "Opportunity", fieldApiName: "Other" },
    };
    Object.assign(duplicate, { [identity]: { ...original[identity], nonce: "different" } });
    const input = { ...target(), obligations: [original, duplicate] };
    await expect(runLocatorProbe(inaccessiblePage, input, "BASELINE")).rejects.toMatchObject({
      code: "PROBE_OBLIGATION_INVALID",
    });
    Object.assign(duplicate, { [identity]: { ...original[identity] } });
    await expect(runLocatorProbe(inaccessiblePage, input, "BASELINE")).rejects.toMatchObject({
      code: "PROBE_OBLIGATION_SET_INVALID",
    });
  });
}

test("rejects malformed containers, inherited keys, accessors and sparse arrays without evaluation", async () => {
  let evaluated = false;
  const accessor = target();
  Object.defineProperty(accessor, "obligations", { enumerable: true, get() {
    evaluated = true; throw new Error("UNTRUSTED_INPUT_CANARY");
  } });
  const sparse = { ...target(), obligations: new Array(2) };
  const extraArray = target();
  Object.assign(extraArray.obligations, { nonce: "untrusted" });
  const symbol = target();
  Object.assign(symbol, { [Symbol("nonce")]: "untrusted" });
  for (const input of [null, undefined, [], {}, Object.create(target()), accessor, sparse, extraArray, symbol]) {
    await expect(runLocatorProbe(inaccessiblePage, input, "BASELINE")).rejects.toMatchObject({
      code: "PROBE_TARGET_INVALID",
    });
  }
  for (const item of [null, [], undefined]) {
    await expect(runLocatorProbe(inaccessiblePage, { ...target(), obligations: [item] }, "BASELINE"))
      .rejects.toMatchObject({ code: "PROBE_OBLIGATION_INVALID" });
  }
  expect(evaluated).toBe(false);
});

test("uses the same obligation cap and identity tokens as the closed source model", async () => {
  await expect(runLocatorProbe(inaccessiblePage, {
    ...target(), obligations: Array.from({ length: 65 }, () => target().obligations[0]),
  }, "BASELINE")).rejects.toMatchObject({ code: "PROBE_TARGET_INVALID" });
  for (const token of ["1starts-with-number", "has:colon", "has.dot", "a\n"]) {
    const item = target().obligations[1];
    await expect(runLocatorProbe(inaccessiblePage, { ...target(), obligations: [{
      ...item, semanticIdentity: { ...item.semanticIdentity, action: token },
    }] }, "BASELINE")).rejects.toMatchObject({ code: "PROBE_OBLIGATION_INVALID" });
  }
  await expect(runLocatorProbe(inaccessiblePage, { ...target(), obligations: [{
    ...target().obligations[0], obligationId: "1invalid",
  }] }, "BASELINE")).rejects.toMatchObject({ code: "PROBE_OBLIGATION_INVALID" });
});

test("renamed objects, fields and actions preserve behavior; property order preserves digest", async ({ page }) => {
  const input = {
    dataMutation: "FORBIDDEN", obligationPolicy: "COMPLETE_DECLARED_SET",
    obligations: [{
      assertions: ["EDITABLE", "ENABLED", "VISIBLE"], obligationId: "field:Metric",
      semanticIdentity: { fieldApiName: "Metric__c", objectApiName: "Independent__c", kind: "FIELD" },
      originalLocator: { value: "renamed-field", attribute: "data-testid", kind: "ATTRIBUTE_EQUALS" },
    }, {
      assertions: ["ENABLED", "VISIBLE"], obligationId: "probe:reassess",
      semanticIdentity: { action: "reassess", objectApiName: "Independent__c", kind: "ACTION" },
      originalLocator: { value: "renamed-action", attribute: "data-testid", kind: "ATTRIBUTE_EQUALS" },
    }],
  };
  await page.setContent(`<section data-object-api="Independent__c">
    <div data-field-api="Metric__c" data-testid="renamed-field"><input /></div>
    <button data-action="reassess" data-testid="renamed-action">Act</button></section>`);
  const report = await runLocatorProbe(page, input, "BASELINE");
  const reverseKeys = (value: unknown): unknown => Array.isArray(value) ? value.map(reverseKeys)
    : value !== null && typeof value === "object" ? Object.fromEntries(
      Object.entries(value).reverse().map(([key, item]) => [key, reverseKeys(item)]),
    ) : value;
  expect(report.status).toBe("PASSED");
  expect((await runLocatorProbe(page, reverseKeys(input), "BASELINE")).targetDigest).toBe(report.targetDigest);
  expect(JSON.stringify(report)).not.toContain("Independent__c");
});

test("browser failure cannot return a passing report", async ({ page }) => {
  await page.close();
  await expect(runLocatorProbe(page, target(), "BASELINE")).rejects.toThrow();
});
