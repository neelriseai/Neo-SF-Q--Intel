import { expect, test } from "@playwright/test";

test("dashboard presents a clean assurance workspace", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Know the blast radius/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Analyze change/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cooperating agents" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Governance signals" })).toBeVisible();
});

test("cooperating agents return an evidence-backed decision", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Analyze change/ }).click();
  await expect(page.getByText("CONDITIONAL GO", { exact: true })).toBeVisible();
  await expect(page.getByText("Governance Review", { exact: true })).toBeVisible();
  await expect(page.getByText("material claim evidence coverage")).toBeVisible();
  await expect(page.locator("tbody tr").first()).toBeVisible();
});
