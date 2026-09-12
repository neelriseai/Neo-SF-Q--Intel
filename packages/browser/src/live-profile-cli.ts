import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { parseStrictBoundedJson, resolveLaunch } from "./salesforce-cli-session.js";

type Environment = Readonly<Record<string, string | undefined>>;
type OutputWriter = (value: string) => void;

const DEFAULT_VALIDITY_SECONDS = 10 * 60;
const MAXIMUM_VALIDITY_SECONDS = 15 * 60;
const DEFAULT_OUTPUT = ".runtime/live-browser-profile.json";

export async function runLiveProfileCli(
  arguments_: readonly string[],
  environment: Environment,
  write: OutputWriter,
): Promise<number> {
  try {
    if (arguments_.length !== 0) throw new LiveProfileError("CLI_ARGUMENTS_INVALID");
    const dotenv = await loadDotenv();
    const alias =
      setting(environment, dotenv, "NEO_BROWSER_TARGET_ORG_ALIAS") ??
      setting(environment, dotenv, "SF_OPERATOR_ALIAS");
    if (!alias || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(alias)) {
      throw new LiveProfileError("TARGET_ORG_ALIAS_REQUIRED");
    }
    const executable =
      setting(environment, dotenv, "NEO_BROWSER_SALESFORCE_EXECUTABLE") ??
      (process.platform === "win32" ? "sf.cmd" : "sf");
    const outputPath = resolveRepositoryPath(
      setting(environment, dotenv, "NEO_BROWSER_LIVE_PROFILE_PATH") ?? DEFAULT_OUTPUT,
    );
    const actorBindingSource = (
      setting(environment, dotenv, "NEO_BROWSER_ACTOR_BINDING_SOURCE") ?? "USERNAME"
    ).toUpperCase();
    if (actorBindingSource !== "USERNAME" && actorBindingSource !== "USER_ID") {
      throw new LiveProfileError("ACTOR_BINDING_SOURCE_INVALID");
    }
    const validitySeconds = boundedInteger(
      setting(environment, dotenv, "NEO_BROWSER_ENROLLMENT_SECONDS"),
      DEFAULT_VALIDITY_SECONDS,
      60,
      MAXIMUM_VALIDITY_SECONDS,
      "ENROLLMENT_SECONDS_INVALID",
    );
    const captureLimit = boundedInteger(
      setting(environment, dotenv, "NEO_BROWSER_CAPTURE_LIMIT"),
      25,
      1,
      100,
      "CAPTURE_LIMIT_INVALID",
    );
    const mutationActionsEnabled =
      (setting(environment, dotenv, "NEO_BROWSER_ENABLE_BUSINESS_ACTION") ?? "false")
        .toLowerCase() === "true";

    const display = await runSfJson(executable, [
      "org",
      "display",
      "--target-org",
      alias,
      "--json",
    ]);
    const open = await runSfJson(executable, [
      "org",
      "open",
      "--target-org",
      alias,
      "--url-only",
      "--json",
    ]);
    const profile = buildProfile({
      alias,
      executable,
      display,
      open,
      actorBindingSource,
      validitySeconds,
      captureLimit,
      mutationActionsEnabled,
      now: new Date(),
    });
    const bytes = Buffer.from(`${JSON.stringify(profile, null, 2)}\n`, "utf8");
    const sha256 = digestBytes(bytes);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, bytes, { encoding: "utf8", flag: "w" });
    write(`${JSON.stringify({
      schemaVersion: "1.0.0",
      status: "READY",
      profilePath: outputPath,
      profileSha256: sha256,
      expiresAt: profile.enrollment.expiresAt,
      targetOrgAlias: alias,
      mutationActionsEnabled: profile.execution.mutationActionsEnabled,
      command: "Set NEO_BROWSER_LIVE_PROFILE_PATH and NEO_BROWSER_PROFILE_SHA256, then run npm run live:smoke.",
    })}\n`);
    return 0;
  } catch (error) {
    write(`${JSON.stringify({
      schemaVersion: "1.0.0",
      status: "BLOCKED",
      errorCode: safeErrorCode(error),
    })}\n`);
    return 2;
  }
}

type ProfileInput = {
  alias: string;
  executable: string;
  display: Record<string, unknown>;
  open: Record<string, unknown>;
  actorBindingSource: "USERNAME" | "USER_ID";
  validitySeconds: number;
  captureLimit: number;
  mutationActionsEnabled?: boolean;
  now: Date;
};

type LiveBrowserProfileDocument = {
  schemaVersion: "1.0.0";
  profileId: string;
  profileVersion: string;
  enrollment: {
    enrollmentId: string;
    issuer: string;
    classification: "NON_PRODUCTION";
    frontdoorOrigin: string;
    lightningOrigin: string;
    navigationBridgeOrigins: string[];
    resourceOrigins: string[];
    orgBinding: string;
    actorBinding: string;
    policyDigest: string;
    issuedAt: string;
    expiresAt: string;
    permittedModes:
      | readonly ["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK", "LOCATOR_PROBE"]
      | readonly [
          "READ_ONLY_DOM_CAPTURE",
          "CANDIDATE_READBACK",
          "LOCATOR_PROBE",
          "BUSINESS_ACTION",
        ];
  };
  sessionBroker: Record<string, unknown>;
  browser: Record<string, unknown>;
  execution: Record<string, unknown>;
};

export function buildProfile(input: ProfileInput): LiveBrowserProfileDocument {
  const result = exactRecord(input.display.result, "DISPLAY_RESULT_INVALID");
  const openResult = exactRecord(input.open.result, "OPEN_RESULT_INVALID");
  const orgId = boundedSalesforceId(result.id, "DISPLAY_RESULT_INVALID");
  const reportedAlias = result.alias;
  if (reportedAlias !== undefined && reportedAlias !== input.alias) {
    throw new LiveProfileError("DISPLAY_ALIAS_MISMATCH");
  }
  const username = boundedUsername(result.username);
  const userId = result.userId === undefined ? undefined : boundedSalesforceId(result.userId, "DISPLAY_RESULT_INVALID");
  const actorBinding =
    input.actorBindingSource === "USER_ID"
      ? digest(`salesforce-user:${userId ?? ""}`)
      : digest(`salesforce-username:${username.toLowerCase()}`);
  if (input.actorBindingSource === "USER_ID" && !userId) {
    throw new LiveProfileError("DISPLAY_RESULT_INVALID");
  }
  const sessionUrl = typeof openResult.url === "string" ? openResult.url : "";
  const frontdoorOrigin = frontdoorOriginFromSessionUrl(sessionUrl);
  const lightningOrigin = lightningOriginFromFrontdoor(frontdoorOrigin);
  const issuedAt = new Date(Math.floor(input.now.getTime() / 1000) * 1000);
  const expiresAt = new Date(issuedAt.getTime() + input.validitySeconds * 1000);
  const navigationBridgeOrigins = [frontdoorOrigin.replace(/\.my\.salesforce\.com$/i, ".file.force.com")];
  const resourceOrigins = ["https://b.static.lightning.force.com", "https://login.salesforce.com"];
  const orgBinding = digest(`salesforce-org:${orgId}`);
  const mutationActionsEnabled = input.mutationActionsEnabled === true;
  const permittedModes = mutationActionsEnabled
    ? ([
        "READ_ONLY_DOM_CAPTURE",
        "CANDIDATE_READBACK",
        "LOCATOR_PROBE",
        "BUSINESS_ACTION",
      ] as const)
    : (["READ_ONLY_DOM_CAPTURE", "CANDIDATE_READBACK", "LOCATOR_PROBE"] as const);
  const policyDigest = digest(
    canonicalJson({
      mode: "READ_ONLY_DOM_CAPTURE",
      targetOrgAlias: input.alias,
      frontdoorOrigin,
      lightningOrigin,
      captureLimit: input.captureLimit,
      mutationActionsEnabled,
    }),
  );
  const enrollment = {
    enrollmentId: `live-enrollment-${randomUUID()}`,
    issuer: "neo-host-operator",
    classification: "NON_PRODUCTION" as const,
    frontdoorOrigin,
    lightningOrigin,
    navigationBridgeOrigins,
    resourceOrigins,
    orgBinding,
    actorBinding,
    policyDigest,
    issuedAt: issuedAt.toISOString(),
    expiresAt: expiresAt.toISOString(),
      permittedModes,
  };
  return {
    schemaVersion: "1.0.0",
    profileId: "neo-local-live-browser",
    profileVersion: "1.0.0",
    enrollment,
    sessionBroker: {
      targetOrgAlias: input.alias,
      frontdoorOrigin,
      lightningOrigin,
      navigationBridgeOrigins,
      resourceOrigins,
      orgBinding,
      actorBinding,
      actorBindingSource: input.actorBindingSource,
      salesforceExecutable: input.executable,
      allowedFrontdoorHostnameSuffixes: [".my.salesforce.com"],
      allowedLightningHostnameSuffixes: [".lightning.force.com"],
      maximumOutputBytes: 65536,
      timeoutMs: 60000,
    },
    browser: {
      headless: true,
      launchTimeoutMs: 60000,
      navigationTimeoutMs: 60000,
      operationTimeoutMs: 15000,
    },
    execution: {
      mode: "READ_ONLY_DOM_CAPTURE",
      captureLimit: input.captureLimit,
      mutationActionsEnabled,
    },
  };
}

class LiveProfileError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "LiveProfileError";
  }
}

async function runSfJson(executable: string, arguments_: readonly string[]): Promise<Record<string, unknown>> {
  const result = await new Promise<{ code: number | null; stdout: Buffer }>((resolvePromise, reject) => {
    const launch = resolveLaunch(executable, arguments_);
    const child = spawn(launch.executable, launch.arguments, {
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
      env: {
        PATH: process.env.PATH,
        PATHEXT: process.env.PATHEXT,
        SystemRoot: process.env.SystemRoot,
        APPDATA: process.env.APPDATA,
        LOCALAPPDATA: process.env.LOCALAPPDATA,
        USERPROFILE: process.env.USERPROFILE,
        SF_AUTOUPDATE_DISABLE: "true",
        SF_DISABLE_TELEMETRY: "true",
      },
    });
    const chunks: Buffer[] = [];
    let total = 0;
    const timer = setTimeout(() => child.kill(), 60_000);
    child.stdout.on("data", (chunk: Buffer) => {
      total += chunk.byteLength;
      if (total > 128 * 1024) child.kill();
      chunks.push(chunk);
    });
    child.once("error", reject);
    child.once("close", (code) => {
      clearTimeout(timer);
      resolvePromise({ code, stdout: Buffer.concat(chunks) });
    });
  });
  if (result.code !== 0) throw new LiveProfileError("SF_CLI_COMMAND_FAILED");
  const parsed = parseStrictBoundedJson(result.stdout.toString("utf8"));
  const document = exactRecord(parsed, "SF_CLI_OUTPUT_INVALID");
  if (document.status !== 0) throw new LiveProfileError("SF_CLI_OUTPUT_INVALID");
  return document;
}

async function loadDotenv(): Promise<Record<string, string>> {
  const file = resolveRepositoryPath(".env");
  try {
    const raw = await readFile(file, "utf8");
    return Object.fromEntries(
      raw
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith("#") && line.includes("="))
        .map((line) => {
          const separator = line.indexOf("=");
          return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()];
        }),
    );
  } catch {
    return {};
  }
}

function setting(environment: Environment, dotenv: Record<string, string>, name: string): string | undefined {
  return environment[name] ?? dotenv[name];
}

function resolveRepositoryPath(value: string): string {
  if (resolve(value) === value) return value;
  return resolve(repositoryRoot(), value);
}

function repositoryRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
}

function exactRecord(value: unknown, code: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new LiveProfileError(code);
  }
  return value as Record<string, unknown>;
}

function boundedSalesforceId(value: unknown, code: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$/.test(value)) {
    throw new LiveProfileError(code);
  }
  return value;
}

function boundedUsername(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length < 3 ||
    value.length > 320 ||
    value.trim() !== value ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    throw new LiveProfileError("DISPLAY_RESULT_INVALID");
  }
  return value;
}

function frontdoorOriginFromSessionUrl(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new LiveProfileError("FRONTDOOR_URL_INVALID");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/secur/frontdoor.jsp" ||
    !parsed.hostname.endsWith(".my.salesforce.com") ||
    !hasExactFrontdoorCredentialShape(parsed)
  ) {
    throw new LiveProfileError("FRONTDOOR_URL_INVALID");
  }
  return parsed.origin;
}

function lightningOriginFromFrontdoor(frontdoorOrigin: string): string {
  return frontdoorOrigin.replace(/\.my\.salesforce\.com$/i, ".lightning.force.com");
}

function hasExactFrontdoorCredentialShape(parsed: URL): boolean {
  const keys = [...parsed.searchParams.keys()];
  return (
    (keys.length === 1 &&
      keys[0] === "sid" &&
      parsed.searchParams.getAll("sid").length === 1 &&
      Boolean(parsed.searchParams.get("sid"))) ||
    (keys.length === 2 &&
      new Set(keys).size === 2 &&
      keys.every((key) => key === "otp" || key === "cshc") &&
      parsed.searchParams.getAll("otp").length === 1 &&
      parsed.searchParams.getAll("cshc").length === 1 &&
      Boolean(parsed.searchParams.get("otp")) &&
      Boolean(parsed.searchParams.get("cshc")))
  );
}

function boundedInteger(
  value: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
  code: string,
): number {
  const parsed = value === undefined || value === "" ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new LiveProfileError(code);
  }
  return parsed;
}

function safeErrorCode(error: unknown): string {
  if (error instanceof LiveProfileError) return error.code;
  return "LIVE_PROFILE_FAILED";
}

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function digestBytes(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

const invokedPath = process.argv[1];
if (invokedPath && fileURLToPath(import.meta.url) === resolve(invokedPath)) {
  const exitCode = await runLiveProfileCli(process.argv.slice(2), process.env, (value) => {
    process.stdout.write(value);
  });
  process.exitCode = exitCode;
}
