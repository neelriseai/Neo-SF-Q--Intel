import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  outputDir: "test-results",
  use: {
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/browser-contract-results.json" }],
  ],
});
