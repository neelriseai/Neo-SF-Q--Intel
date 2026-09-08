import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  webServer: {
    command: "npm run dev --workspace=@neo/web -- -p 3100",
    cwd: "../..",
    url: "http://localhost:3100",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  use: {
    baseURL: "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  reporter: [["list"]],
});
