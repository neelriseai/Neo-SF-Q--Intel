import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";
import { buildProfile, runLiveProfileCli } from "../src/live-profile-cli.js";

const FRONTDOOR_URL =
  "https://example--qa.my.salesforce.com/secur/frontdoor.jsp?otp=secret-one&cshc=secret-two";

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function display(overrides: Record<string, unknown> = {}) {
  return {
    status: 0,
    result: {
      id: "00D000000000001AAA",
      alias: "host-alias",
      username: "admin@example.invalid",
      userId: "005000000000001AAA",
      ...overrides,
    },
  };
}

function open(url = FRONTDOOR_URL) {
  return { status: 0, result: { url } };
}

test("builds a short-lived live browser profile without session material", () => {
  const profile = buildProfile({
    alias: "host-alias",
    executable: "sf-fixture",
    display: display(),
    open: open(),
    actorBindingSource: "USERNAME",
    validitySeconds: 600,
    captureLimit: 20,
    now: new Date("2026-09-11T12:00:00Z"),
  });
  const serialized = JSON.stringify(profile);

  expect(serialized).not.toContain("secret-one");
  expect(serialized).not.toContain("secret-two");
  expect(profile).toMatchObject({
    schemaVersion: "1.0.0",
    profileId: "neo-local-live-browser",
    enrollment: {
      classification: "NON_PRODUCTION",
      frontdoorOrigin: "https://example--qa.my.salesforce.com",
      lightningOrigin: "https://example--qa.lightning.force.com",
      orgBinding: digest("salesforce-org:00D000000000001AAA"),
      actorBinding: digest("salesforce-username:admin@example.invalid"),
      issuedAt: "2026-09-11T12:00:00.000Z",
      expiresAt: "2026-09-11T12:10:00.000Z",
      permittedModes: ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK"],
    },
    execution: {
      mode: "READ_ONLY_DOM_CAPTURE",
      captureLimit: 20,
      mutationActionsEnabled: false,
    },
  });
});

test("builds optional user-id actor binding from CLI display output", () => {
  const profile = buildProfile({
    alias: "host-alias",
    executable: "sf-fixture",
    display: display(),
    open: open(),
    actorBindingSource: "USER_ID",
    validitySeconds: 600,
    captureLimit: 20,
    now: new Date("2026-09-11T12:00:00Z"),
  });

  expect(JSON.stringify(profile)).toContain(digest("salesforce-user:005000000000001AAA"));
});

test("business-action profile opt-in is explicit and reflected in policy", () => {
  const profile = buildProfile({
    alias: "host-alias",
    executable: "sf-fixture",
    display: display(),
    open: open(),
    actorBindingSource: "USERNAME",
    validitySeconds: 600,
    captureLimit: 20,
    mutationActionsEnabled: true,
    now: new Date("2026-09-11T12:00:00Z"),
  });

  expect(profile.enrollment.permittedModes).toEqual([
    "READ_ONLY_DOM_CAPTURE",
    "CANDIDATE_READBACK",
    "BUSINESS_ACTION",
  ]);
  expect(profile.execution.mutationActionsEnabled).toBe(true);
});


test("rejects unexpected alias and non-frontdoor session URLs", () => {
  const base = {
    alias: "host-alias",
    executable: "sf-fixture",
    actorBindingSource: "USERNAME" as const,
    validitySeconds: 600,
    captureLimit: 20,
    now: new Date("2026-09-11T12:00:00Z"),
  };

  expect(() =>
    buildProfile({ ...base, display: display({ alias: "other-alias" }), open: open() }),
  ).toThrow("DISPLAY_ALIAS_MISMATCH");
  expect(() =>
    buildProfile({
      ...base,
      display: display(),
      open: open("https://example--qa.my.salesforce.com/home/home.jsp"),
    }),
  ).toThrow("FRONTDOOR_URL_INVALID");
});

test("profile CLI rejects caller arguments without exposing them", async () => {
  const output: string[] = [];
  const exitCode = await runLiveProfileCli(["--target-org", "other"], {}, (value) => {
    output.push(value);
  });

  expect(exitCode).toBe(2);
  expect(output).toHaveLength(1);
  expect(output[0]).toContain("CLI_ARGUMENTS_INVALID");
  expect(output[0]).not.toContain("other");
});
