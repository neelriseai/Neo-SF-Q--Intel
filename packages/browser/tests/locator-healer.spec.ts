import { expect, test } from "@playwright/test";
import type { Page } from "playwright";
import { discoverLocatorCandidate } from "../src/locator-healer.js";

test("discovers a candidate through Salesforce metadata identity after layout drift", async ({ page }) => {
  await page.setContent(`
    <section><label>Strategic discount<input data-object-api="Opportunity"
      data-field-api="Discount__c" aria-label="Strategic discount" /></label></section>
  `);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Opportunity",
    fieldApiName: "Discount__c",
    accessibleName: "Strategic discount",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.lifecycle).toEqual(["CANDIDATE_DISCOVERED"]);
  expect(result.strategy).toBe("salesforce-metadata-identity");
  expect(result.confidence).toBeGreaterThan(0.9);
});

test("abstains when target identity is ambiguous", async ({ page }) => {
  await page.setContent(`
    <input data-action="save" /><button data-action="save">Save</button>
  `);
  const result = await discoverLocatorCandidate(page, { action: "save" });
  expect(result.status).toBe("ABSTAINED");
  expect(result.lifecycle).toEqual([]);
  expect(result.candidateCount).toBe(2);
});

test("candidate discovery never claims approval, action, verification, or healing", async ({ page }) => {
  await page.setContent(`<button data-action="inspect">Inspect</button>`);

  const result = await discoverLocatorCandidate(page, { action: "inspect" });
  const projection = JSON.stringify(result);

  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(projection).not.toContain("PROPOSAL_APPROVED");
  expect(projection).not.toContain("ACTION_APPLIED");
  expect(projection).not.toContain("OUTCOME_VERIFIED");
  expect(projection).not.toContain("HEALED");
});

test("resolves a unique editable descendant under separate object and field identities", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c">
      <div data-field-api="Weight__c"><label>Weight<input type="number" value="12" /></label></div>
    </section>
  `);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Weight__c", accessibleName: "Weight",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.strategy).toBe("salesforce-metadata-identity");
  expect(result.candidateCount).toBe(1);
  expect(await result.locator!.evaluate((element) => element.tagName)).toBe("INPUT");
  expect(await result.locator!.inputValue()).toBe("12");
});

test("crosses open component shadow roots while retaining nearest object and field scope", async ({ page }) => {
  await page.setContent('<device-form></device-form>');
  await page.evaluate(() => {
    const component = document.querySelector("device-form")!;
    component.attachShadow({ mode: "open" }).innerHTML = `
      <section data-object-api="Device__c"><div data-field-api="Weight__c">
        <numeric-control></numeric-control>
      </div></section>`;
    component.shadowRoot!.querySelector("numeric-control")!
      .attachShadow({ mode: "open" }).innerHTML =
      '<label>Weight<input type="number" value="17" /></label>';
  });
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Weight__c",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.candidateCount).toBe(1);
  expect(await result.locator!.inputValue()).toBe("17");
});

for (const control of [
  '<select aria-label="State"><option value="ready">Ready</option></select>',
  '<input type="checkbox" aria-label="Enabled" checked />',
  '<textarea aria-label="Notes">Observed</textarea>',
]) {
  test(`supports metadata identity for native controls ${control.split(" ")[0]}`, async ({ page }) => {
    await page.setContent(`
      <section data-object-api="Device__c"><div data-field-api="Value__c">${control}</div></section>
    `);
    const result = await discoverLocatorCandidate(page, {
      objectApiName: "Device__c", fieldApiName: "Value__c",
    });
    expect(result.status).toBe("CANDIDATE_DISCOVERED");
    expect(await result.locator!.isEditable()).toBe(true);
  });
}

test("abstains on repeated field identities across matching component scopes", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c"><input /></div></section>
    <section data-object-api="Device__c"><div data-field-api="Value__c"><input /></div></section>
  `);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(2);
  expect(result.locator).toBeUndefined();
});

test("supports a unique semantic combobox when a field has no native editable control", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="State__c">
      <button role="combobox" aria-label="State" aria-expanded="false">Ready</button>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "State__c",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(await result.locator!.getAttribute("role")).toBe("combobox");
  expect(await result.locator!.getAttribute("aria-expanded")).toBe("false");
});

test("abstains when one field wrapper contains multiple visible editable controls", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <input aria-label="First" /><input aria-label="Second" />
    </div></section>
  `);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(2);
});

for (const nested of [false, true]) {
  test(`rejects matching labels and fields on the wrong object, nested=${nested}`, async ({ page }) => {
    const wrongObject = `
      <section data-object-api="Other__c"><div data-field-api="Value__c">
        <input aria-label="Value" />
      </div></section>`;
    await page.setContent(nested
      ? `<section data-object-api="Device__c">${wrongObject}</section>` : wrongObject);
    const result = await discoverLocatorCandidate(page, {
      objectApiName: "Device__c", fieldApiName: "Value__c", accessibleName: "Value",
    });
    expect(result.status).toBe("ABSTAINED");
    expect(result.candidateCount).toBe(0);
    expect(result.locator).toBeUndefined();
  });
}

test("rejects a wrong-object boundary inside an open shadow root", async ({ page }) => {
  await page.setContent('<section data-object-api="Device__c"><nested-form></nested-form></section>');
  await page.evaluate(() => {
    document.querySelector("nested-form")!.attachShadow({ mode: "open" }).innerHTML = `
      <section data-object-api="Other__c"><div data-field-api="Value__c">
        <input aria-label="Value" />
      </div></section>`;
  });
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c", accessibleName: "Value",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(0);
});

for (const inner of [
  '<div data-field-api="Other__c"><input aria-label="Value" /></div>',
  '<div data-field-api="Value__c">Read-only display</div>',
  '<div data-field-api="Value__c"><input aria-label="Value" disabled /></div>',
  '<div data-field-api="Value__c"><input aria-label="Value" readonly /></div>',
]) {
  test(`abstains with no matching editable control ${inner}`, async ({ page }) => {
    await page.setContent(`<section data-object-api="Device__c">${inner}</section>`);
    const result = await discoverLocatorCandidate(page, {
      objectApiName: "Device__c", fieldApiName: "Value__c", accessibleName: "Value",
    });
    expect(result.status).toBe("ABSTAINED");
    expect(result.candidateCount).toBe(0);
    expect(result.locator).toBeUndefined();
  });
}

test("abstains when the nearest field identity differs from the requested wrapper", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <div data-field-api="Other__c"><input aria-label="Value" /></div>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(0);
});

test("bounds ancestor traversal and refuses selector injection", async ({ page }) => {
  await page.setContent(`<section data-object-api="Device__c">${"<div>".repeat(40)}
    <input data-field-api="Value__c" aria-label="Value" />
    ${"</div>".repeat(40)}</section>`);
  for (const objectApiName of ["Device__c", "", 'Device__c"] input, [data-object-api="Device__c']) {
    const result = await discoverLocatorCandidate(page, {
      objectApiName, fieldApiName: "Value__c", accessibleName: "Value",
    });
    expect(result.status).toBe("ABSTAINED");
    expect(result.candidateCount).toBe(0);
  }
});

for (const action of [
  'missing"], [data-action="dangerous',
  'missing"],button,[data-action="missing',
  'missing\\"],button,[data-action="missing',
  "",
  "\n",
  "x".repeat(201),
]) {
  test(`action identity cannot inject a selector or fall back to a matching label ${JSON.stringify(action)}`, async ({ page }) => {
    await page.setContent(`
      <button data-action="dangerous">Execute</button>
      <label>Value<input value="unchanged" /></label>`);
    const result = await discoverLocatorCandidate(page, { action, accessibleName: "Value" });
    expect(result.status).toBe("ABSTAINED");
    expect(result.candidateCount).toBe(0);
    expect(result.locator).toBeUndefined();
    expect(await page.getByLabel("Value").inputValue()).toBe("unchanged");
  });
}

for (const metadata of [
  { objectApiName: "Device__c" },
  { fieldApiName: "Value__c" },
  { objectApiName: "" },
  { fieldApiName: "" },
]) {
  test(`partial metadata identity never falls through to an unscoped label or action ${JSON.stringify(metadata)}`, async ({ page }) => {
    await page.setContent(`
      <section data-object-api="Other__c"><input data-field-api="Value__c" aria-label="Value" /></section>
      <button data-action="inspect">Inspect</button>`);
    const result = await discoverLocatorCandidate(page, {
      ...metadata, accessibleName: "Value", action: "inspect",
    });
    expect(result.status).toBe("ABSTAINED");
    expect(result.strategy).toBe("salesforce-metadata-identity");
    expect(result.candidateCount).toBe(0);
    expect(result.locator).toBeUndefined();
  });
}

test("counts distinct native and semantic controls as ambiguous", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <input aria-label="Value" />
      <button role="combobox" aria-label="Other value">Ready</button>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c", accessibleName: "Value",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(2);
  expect(result.locator).toBeUndefined();
});

test("does not double-count one element matching both native and semantic selectors", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <input role="textbox" aria-label="Value" value="unique" />
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.candidateCount).toBe(1);
  expect(await result.locator!.inputValue()).toBe("unique");
});

test("counts native and semantic ambiguity across component shadow roots", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <input aria-label="Value" /><choice-control></choice-control>
    </div></section>`);
  await page.evaluate(() => {
    document.querySelector("choice-control")!.attachShadow({ mode: "open" }).innerHTML =
      '<button role="combobox" aria-label="Other value">Ready</button>';
  });
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(2);
  expect(result.locator).toBeUndefined();
});

for (const type of ["submit", "reset", "button", "image", "file", "hidden", "SUBMIT"]) {
  test(`rejects non-value input type ${type}, even with a semantic role`, async ({ page }) => {
    await page.setContent(`
      <section data-object-api="Device__c"><div data-field-api="Value__c">
        <input type="${type}" role="textbox" aria-label="Value" value="untouched" />
      </div></section>`);
    for (const intent of [
      { objectApiName: "Device__c", fieldApiName: "Value__c", accessibleName: "Value" },
      { accessibleName: "Value" },
    ]) {
      const result = await discoverLocatorCandidate(page, intent);
      expect(result.status).toBe("ABSTAINED");
      expect(result.candidateCount).toBe(0);
      expect(result.locator).toBeUndefined();
    }
  });
}

for (const state of ["aria-readonly", "aria-disabled"]) {
  for (const location of ["control", "field", "object", "outside-object", "shadow-host"]) {
    test(`rejects ${state} on composed ${location} boundary`, async ({ page }) => {
      await page.setContent(`
        <article ${location === "outside-object" ? `${state}="true"` : ""}>
          <section data-object-api="Device__c" ${location === "object" ? `${state}="true"` : ""}>
            <div data-field-api="Value__c" ${location === "field" ? `${state}="true"` : ""}>
              <value-control ${location === "shadow-host" ? `${state}="true"` : ""}></value-control>
            </div>
          </section>
        </article>`);
      await page.evaluate(({ state, location }) => {
        document.querySelector("value-control")!.attachShadow({ mode: "open" }).innerHTML =
          `<input aria-label="Value" value="untouched" ${location === "control" ? `${state}="true"` : ""} />`;
      }, { state, location });
      const result = await discoverLocatorCandidate(page, {
        objectApiName: "Device__c", fieldApiName: "Value__c",
      });
      expect(result.status).toBe("ABSTAINED");
      expect(result.candidateCount).toBe(0);
      expect(result.locator).toBeUndefined();
      expect(await page.getByLabel("Value").inputValue()).toBe("untouched");
    });
  }
}

for (const state of ["aria-readonly", "aria-disabled"]) {
  test(`explicit action respects ${state} on its composed container`, async ({ page }) => {
    await page.setContent(`<div ${state}="true"><action-control></action-control></div>`);
    await page.evaluate(() => {
      document.querySelector("action-control")!.attachShadow({ mode: "open" }).innerHTML =
        '<button data-action="inspect">Inspect</button>';
    });
    const result = await discoverLocatorCandidate(page, { action: "inspect" });
    expect(result.status).toBe("ABSTAINED");
    expect(result.locator).toBeUndefined();
  });
}

for (const shadow of [false, true]) {
  test(`collapses a combobox wrapper around its sole native value control, shadow=${shadow}`, async ({ page }) => {
    await page.setContent(`
      <section data-object-api="Device__c"><div data-field-api="Value__c">
        <div role="combobox" aria-label="Value" aria-expanded="false">
          ${shadow ? "<value-control></value-control>" : '<input role="textbox" aria-label="Value" value="unique" />'}
        </div>
      </div></section>`);
    if (shadow) {
      await page.evaluate(() => {
        document.querySelector("value-control")!.attachShadow({ mode: "open" }).innerHTML =
          '<input role="textbox" aria-label="Value" value="unique" />';
      });
    }
    const result = await discoverLocatorCandidate(page, {
      objectApiName: "Device__c", fieldApiName: "Value__c",
    });
    expect(result.status).toBe("CANDIDATE_DISCOVERED");
    expect(result.candidateCount).toBe(1);
    expect(await result.locator!.evaluate((element) => element.tagName)).toBe("INPUT");
    expect(await result.locator!.inputValue()).toBe("unique");
  });
}

test("rejects a readonly semantic wrapper even when its native child is editable", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <div role="combobox" aria-readonly="true"><input aria-label="Value" /></div>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(0);
});

test("a composite plus a distinct sibling semantic control remains ambiguous", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <div role="combobox"><input aria-label="Value" /></div>
      <button role="combobox" aria-label="Other value">Other</button>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(3);
  expect(result.locator).toBeUndefined();
});

test("a combobox with multiple native value controls cannot be collapsed", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <div role="combobox"><input aria-label="First" /><input aria-label="Second" /></div>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(3);
});

test("a non-value auxiliary input does not compete with the sole value control", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c"><div data-field-api="Value__c">
      <div role="combobox"><input aria-label="Value" value="unique" /><input type="hidden" /></div>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.candidateCount).toBe(1);
  expect(await result.locator!.inputValue()).toBe("unique");
});

test("explicit false ARIA states do not block an editable composite", async ({ page }) => {
  await page.setContent(`
    <section data-object-api="Device__c" aria-disabled="false"><div data-field-api="Value__c">
      <div role="combobox" aria-readonly="false"><input aria-label="Value" value="unique" /></div>
    </div></section>`);
  const result = await discoverLocatorCandidate(page, {
    objectApiName: "Device__c", fieldApiName: "Value__c",
  });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.candidateCount).toBe(1);
});

for (const actualName of ["Value extended", "Earlier Value"]) {
  test(`accessible role lookup rejects prefix or suffix confusion ${actualName}`, async ({ page }) => {
    await page.setContent(`<input aria-label="${actualName}" value="untouched" />`);
    const result = await discoverLocatorCandidate(page, { accessibleName: "Value" });
    expect(result.status).toBe("ABSTAINED");
    expect(result.candidateCount).toBe(0);
    expect(result.locator).toBeUndefined();
  });
}

test("exact accessible role match is independent of a similarly named control", async ({ page }) => {
  await page.setContent(`
    <input aria-label="Value" value="exact" />
    <input aria-label="Value extended" value="other" />`);
  const result = await discoverLocatorCandidate(page, { accessibleName: "Value" });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.strategy).toBe("accessible-role-and-name");
  expect(await result.locator!.inputValue()).toBe("exact");
});

test("accessible label fallback retains exact matching for controls without implicit roles", async ({ page }) => {
  await page.setContent(`
    <div contenteditable="true" aria-label="Value">exact</div>
    <div contenteditable="true" aria-label="Value extended">other</div>`);
  expect(await page.getByLabel("Value", { exact: true }).count()).toBe(1);
  const result = await discoverLocatorCandidate(page, { accessibleName: "Value" });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.strategy).toBe("visible-label");
  expect(await result.locator!.textContent()).toBe("exact");
});

test("wrapped select resolves through its exact semantic combobox name", async ({ page }) => {
  await page.setContent(`
    <label>Value<select><option value="exact">Exact</option></select></label>
    <label>Value extended<select><option value="other">Other</option></select></label>`);
  const result = await discoverLocatorCandidate(page, { accessibleName: "Value" });
  expect(result.status).toBe("CANDIDATE_DISCOVERED");
  expect(result.strategy).toBe("accessible-role-and-name");
  expect(await result.locator!.inputValue()).toBe("exact");
});

test("exact name ambiguity across distinct semantic roles cannot be hidden by priority", async ({ page }) => {
  await page.setContent(`
    <input aria-label="Value" value="first" />
    <label>Value<select><option value="second">Second</option></select></label>`);
  const result = await discoverLocatorCandidate(page, { accessibleName: "Value" });
  expect(result.status).toBe("ABSTAINED");
  expect(result.candidateCount).toBe(2);
  expect(result.locator).toBeUndefined();
});

for (const accessibleName of ["", "   ", "\n", "Value\u0000", "Value\u007f", "x".repeat(201)]) {
  test(`invalid accessible names abstain before any selector ${JSON.stringify(accessibleName)}`, async () => {
    let selectors = 0;
    const forbiddenSelector = () => {
      selectors += 1;
      throw new Error("Invalid accessible name reached a selector");
    };
    const page = {
      locator: forbiddenSelector, getByRole: forbiddenSelector, getByLabel: forbiddenSelector,
    } as unknown as Page;
    for (const intent of [
      { accessibleName },
      { accessibleName, objectApiName: "Device__c", fieldApiName: "Value__c" },
      { accessibleName, action: "inspect" },
    ]) {
      const result = await discoverLocatorCandidate(page, intent);
      expect(result.status).toBe("ABSTAINED");
      expect(result.candidateCount).toBe(0);
      expect(result.locator).toBeUndefined();
    }
    expect(selectors).toBe(0);
  });
}

async function mountSlottedField(
  page: Page,
  slotMode: "field" | "control",
  slotAttributes = "",
): Promise<void> {
  await page.setContent(slotMode === "field" ? `
    <section data-object-api="Device__c"><field-host>
      <div slot="value" data-field-api="Value__c"><input aria-label="Value" value="unique" /></div>
    </field-host></section>` : `
    <section data-object-api="Device__c"><div data-field-api="Value__c"><field-host>
      <input slot="value" aria-label="Value" value="unique" />
    </field-host></div></section>`);
  await page.evaluate(({ slotMode, slotAttributes }) => {
    document.querySelector("field-host")!.attachShadow({ mode: "open" }).innerHTML =
      slotMode === "control"
        ? `<div role="combobox"><slot name="value" ${slotAttributes}></slot></div>`
        : `<div><slot name="value" ${slotAttributes}></slot></div>`;
  }, { slotMode, slotAttributes });
}

for (const slotMode of ["field", "control"] as const) {
  test(`permits an editable slotted ${slotMode} and collapses its actual composed combobox`, async ({ page }) => {
    await mountSlottedField(page, slotMode, 'aria-readonly="false" aria-disabled="false"');
    const result = await discoverLocatorCandidate(page, {
      objectApiName: "Device__c", fieldApiName: "Value__c",
    });
    expect(result.status).toBe("CANDIDATE_DISCOVERED");
    expect(result.candidateCount).toBe(1);
    expect(await result.locator!.evaluate((element) => element.tagName)).toBe("INPUT");
    expect(await result.locator!.inputValue()).toBe("unique");
  });

  for (const slotAttributes of ['aria-readonly="true"', 'aria-disabled="true"', "disabled"]) {
    test(`slotted ${slotMode} respects composed slot state ${slotAttributes}`, async ({ page }) => {
      await mountSlottedField(page, slotMode, slotAttributes);
      const result = await discoverLocatorCandidate(page, {
        objectApiName: "Device__c", fieldApiName: "Value__c",
      });
      expect(result.status).toBe("ABSTAINED");
      expect(result.candidateCount).toBe(0);
      expect(result.locator).toBeUndefined();
    });
  }

  for (const slotAttributes of ['data-object-api="Other__c"', 'data-field-api="Other__c"']) {
    test(`slotted ${slotMode} cannot bypass a nested composed identity ${slotAttributes}`, async ({ page }) => {
      await mountSlottedField(page, slotMode, slotAttributes);
      const result = await discoverLocatorCandidate(page, {
        objectApiName: "Device__c", fieldApiName: "Value__c",
      });
      expect(result.status).toBe("ABSTAINED");
      expect(result.candidateCount).toBe(0);
      expect(result.locator).toBeUndefined();
    });
  }
}
