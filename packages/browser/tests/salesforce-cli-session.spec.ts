import { createHash, randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import type { EphemeralSessionHandoff, TrustedEnrollmentHandle, VerifiedSessionIdentityHandle } from "../src/browser-worker.js";
import { SalesforceCliSessionBroker, SalesforceCliSessionError, buildSalesforceCliEnvironment, resolveLaunch, type SalesforceCliProcessRequest, type SalesforceCliProcessResult, type SalesforceCliProcessRunner, type SalesforceCliSessionBrokerConfig } from "../src/salesforce-cli-session.js";

const FRONTDOOR = "https://example--qa.my.salesforce.com";
const LIGHTNING = "https://example--qa.lightning.force.com";
const ORG_ID = "00D000000000001AAA";
const USERNAME = "admin@example.invalid";
const digest = (value: string) => createHash("sha256").update(value).digest("hex");

class FakeRunner implements SalesforceCliProcessRunner {
  readonly requests: SalesforceCliProcessRequest[] = [];
  constructor(private readonly outcomes: Array<SalesforceCliProcessResult | Error>) {}
  async run(request: SalesforceCliProcessRequest): Promise<SalesforceCliProcessResult> {
    this.requests.push(request);
    const outcome = this.outcomes.shift();
    if (!outcome) throw new Error("missing fixture");
    if (outcome instanceof Error) throw outcome;
    return outcome;
  }
}

const result = (stdout: string, overrides: Partial<SalesforceCliProcessResult> = {}): SalesforceCliProcessResult => ({ exitCode: 0, stdout: Buffer.from(stdout), outputExceeded: false, timedOut: false, ...overrides });
const display = (overrides: Record<string, unknown> = {}) => JSON.stringify({ status: 0, result: { id: ORG_ID, username: USERNAME, alias: "host-alias", ...overrides } });
const open = (sid = "opaque") => JSON.stringify({ status: 0, result: { url: `${FRONTDOOR}/secur/frontdoor.jsp?sid=${sid}` } });
const config = () => ({ targetOrgAlias: "host-alias", frontdoorOrigin: FRONTDOOR, lightningOrigin: LIGHTNING, navigationBridgeOrigins: [] as const, resourceOrigins: [] as const, orgBinding: digest(`salesforce-org:${ORG_ID}`), actorBinding: digest(`salesforce-username:${USERNAME}`), actorBindingSource: "USERNAME" as const, salesforceExecutable: "sf-fixture", maximumOutputBytes: 4096, timeoutMs: 5000 });
const handle = () => Object.freeze({ kind: "TRUSTED_ENROLLMENT_HANDLE" as const });
const identity = () => Object.freeze(Object.defineProperty({}, "kind", { value: "VERIFIED_SESSION_IDENTITY_HANDLE" })) as VerifiedSessionIdentityHandle;
const handoff = () => Object.freeze(Object.defineProperty({}, "kind", { value: "EPHEMERAL_SESSION_HANDOFF" })) as EphemeralSessionHandoff;

function issuer(expected: SalesforceCliSessionBrokerConfig = config()) {
  const verified = identity();
  const issued = handoff();
  return {
    verified, issued,
    verifySessionIdentity: (_handle: TrustedEnrollmentHandle, bindings: { orgBinding: string; actorBinding: string }) => {
      if (bindings.orgBinding !== expected.orgBinding || bindings.actorBinding !== expected.actorBinding) throw new Error("mismatch");
      return verified;
    },
    issueSession: (_handle: TrustedEnrollmentHandle, actual: VerifiedSessionIdentityHandle) => { expect(actual).toBe(verified); return issued; },
  };
}

function expectRedacted(error: unknown, canary: string): void {
  expect(error).toBeInstanceOf(SalesforceCliSessionError);
  expect(`${String(error)}${JSON.stringify(error)}${(error as Error).stack ?? ""}`).not.toContain(canary);
}

test("verifies fixed-alias identity before acquiring an opaque session", async () => {
  const canary = `secret-${randomBytes(8).toString("hex")}`;
  const runner = new FakeRunner([result(display({ accessToken: canary })), result(open(canary))]);
  const expected = issuer();
  const broker = new SalesforceCliSessionBroker(config(), expected, runner);
  expect(await broker.acquire(handle())).toBe(expected.issued);
  expect(JSON.stringify(expected.issued)).toBe("{}");
  expect(runner.requests.map((request) => request.arguments)).toEqual([
    ["org", "display", "--target-org", "host-alias", "--json"],
    ["org", "open", "--target-org", "host-alias", "--url-only", "--json"],
  ]);
  expect(JSON.stringify(broker)).not.toContain(canary);
});

test("fails before org open when org, actor, or reported alias does not match", async () => {
  for (const [output, code] of [
    [display({ id: "00D000000000002AAA" }), "CLI_IDENTITY_MISMATCH"],
    [display({ username: "other@example.invalid" }), "CLI_IDENTITY_MISMATCH"],
    [display({ alias: "different-alias" }), "CLI_DISPLAY_OUTPUT_INVALID"],
  ] as const) {
    const runner = new FakeRunner([result(output)]);
    await expect(new SalesforceCliSessionBroker(config(), issuer(), runner).acquire(handle())).rejects.toMatchObject({ code });
    expect(runner.requests).toHaveLength(1);
  }
});

test("supports explicitly configured user-id actor binding", async () => {
  const userId = "005000000000001AAA";
  const configured = { ...config(), actorBindingSource: "USER_ID" as const, actorBinding: digest(`salesforce-user:${userId}`) };
  const runner = new FakeRunner([result(display({ userId })), result(open())]);
  const expected = issuer(configured);
  await expect(new SalesforceCliSessionBroker(configured, expected, runner).acquire(handle())).resolves.toBe(expected.issued);
});

test("rejects malformed, duplicate-key, and incomplete display output", async () => {
  for (const output of ["not-json", '{"status":0,"status":1,"result":{}}', '{"status":0,"result":{"id":"00D000000000001AAA","id":"00D000000000002AAA"}}', JSON.stringify({ status: 0, result: {} })]) {
    await expect(new SalesforceCliSessionBroker(config(), issuer(), new FakeRunner([result(output)])).acquire(handle())).rejects.toMatchObject({ code: "CLI_DISPLAY_OUTPUT_INVALID" });
  }
});

test("bounds and sanitizes display process failures", async () => {
  const canary = `token-${randomBytes(8).toString("hex")}`;
  const cases: Array<[SalesforceCliProcessResult | Error, string]> = [
    [result(canary, { outputExceeded: true }), "CLI_DISPLAY_OUTPUT_OVERSIZED"],
    [result(canary, { exitCode: 1 }), "CLI_DISPLAY_EXIT_NONZERO"],
    [result(canary, { timedOut: true }), "CLI_DISPLAY_TIMEOUT"],
    [new Error(canary), "CLI_DISPLAY_PROCESS_FAILED"],
  ];
  for (const [outcome, code] of cases) {
    let failure: unknown;
    try { await new SalesforceCliSessionBroker(config(), issuer(), new FakeRunner([outcome])).acquire(handle()); } catch (error) { failure = error; }
    expect(failure).toMatchObject({ code });
    expectRedacted(failure, canary);
  }
});

test("validates open output and frontdoor URL after identity binding", async () => {
  const cases: Array<[string, string]> = [
    ["not-json", "CLI_OUTPUT_INVALID"],
    [JSON.stringify({ status: 0, result: { url: "http://example.invalid/secur/frontdoor.jsp?sid=x" } }), "CLI_SESSION_URL_NOT_ALLOWED"],
    [JSON.stringify({ status: 0, result: { url: "https://other--qa.my.salesforce.com/secur/frontdoor.jsp?sid=x" } }), "CLI_SESSION_ORIGIN_MISMATCH"],
  ];
  for (const [output, code] of cases) {
    await expect(new SalesforceCliSessionBroker(config(), issuer(), new FakeRunner([result(display()), result(output)])).acquire(handle())).rejects.toMatchObject({ code });
  }
});

test("accepts only exact legacy sid or current otp+cshc frontdoor credentials", async () => {
  const current = JSON.stringify({ status: 0, result: { url: `${FRONTDOOR}/secur/frontdoor.jsp?otp=one&cshc=two` } });
  await expect(new SalesforceCliSessionBroker(config(), issuer(), new FakeRunner([result(display()), result(current)])).acquire(handle())).resolves.toBeDefined();
  for (const url of [
    `${FRONTDOOR}/secur/frontdoor.jsp?otp=one`,
    `${FRONTDOOR}/secur/frontdoor.jsp?otp=one&cshc=two&extra=three`,
    `${FRONTDOOR}/secur/frontdoor.jsp?sid=legacy&otp=one&cshc=two`,
    `${FRONTDOOR}/secur/frontdoor.jsp?otp=one&otp=two&cshc=three`,
    `${FRONTDOOR}/secur/frontdoor.jsp?otp=&cshc=two`,
  ]) {
    const output = JSON.stringify({ status: 0, result: { url } });
    await expect(new SalesforceCliSessionBroker(config(), issuer(), new FakeRunner([result(display()), result(output)])).acquire(handle())).rejects.toMatchObject({ code: "CLI_SESSION_URL_NOT_ALLOWED" });
  }
});

test("never exposes display secrets through errors", async () => {
  const canary = `access-${randomBytes(8).toString("hex")}`;
  let failure: unknown;
  try { await new SalesforceCliSessionBroker(config(), issuer(), new FakeRunner([result(display({ accessToken: canary, username: "different@example.invalid" }))])).acquire(handle()); } catch (error) { failure = error; }
  expect(failure).toMatchObject({ code: "CLI_IDENTITY_MISMATCH" });
  expectRedacted(failure, canary);
});

test("requires distinct enrolled origins and explicit actor binding source", () => {
  expect(() => new SalesforceCliSessionBroker({ ...config(), lightningOrigin: FRONTDOOR }, issuer(), new FakeRunner([]))).toThrow();
  expect(() => new SalesforceCliSessionBroker({ ...config(), actorBindingSource: "UNKNOWN" as never }, issuer(), new FakeRunner([]))).toThrow(expect.objectContaining({ code: "HOST_CONFIG_INVALID" }));
});

test("Salesforce CLI child environment excludes Neo and application secrets", () => {
  const child = buildSalesforceCliEnvironment({
    PATH: "safe-path",
    SystemRoot: "safe-root",
    HTTPS_PROXY: "https://proxy.invalid",
    OPENAI_API_KEY: "openai-secret",
    AZURE_OPENAI_API_KEY: "azure-secret",
    DATABASE_URL: "postgres-secret",
    NEO_RECEIPT_HMAC_KEY: "hmac-secret",
    NEO_BROWSER_PROFILE_SHA256: "profile-secret",
  }, { SF_AUTOUPDATE_DISABLE: "true", SF_DISABLE_TELEMETRY: "true" });
  expect(child).toMatchObject({ PATH: "safe-path", SystemRoot: "safe-root", HTTPS_PROXY: "https://proxy.invalid", SF_AUTOUPDATE_DISABLE: "true", SF_DISABLE_TELEMETRY: "true" });
  expect(JSON.stringify(child)).not.toMatch(/openai-secret|azure-secret|postgres-secret|hmac-secret|profile-secret/);
  expect(() => buildSalesforceCliEnvironment({}, { OPENAI_API_KEY: "secret" })).toThrow(expect.objectContaining({ code: "CLI_ENVIRONMENT_INVALID" }));
});

test("Windows cmd launch passes the validated command and arguments as separate argv", () => {
  expect(resolveLaunch("sf.cmd", ["org", "display", "--target-org", "host-alias", "--json"], "win32", "cmd.exe")).toEqual({
    executable: "cmd.exe",
    arguments: ["/d", "/s", "/c", "sf.cmd", "org", "display", "--target-org", "host-alias", "--json"],
  });
  expect(() => resolveLaunch("sf.cmd", ["org", "bad&argument"], "win32", "cmd.exe")).toThrow(expect.objectContaining({ code: "CLI_PROCESS_ARGUMENT_INVALID" }));
});
