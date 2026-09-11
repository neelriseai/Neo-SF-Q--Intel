import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import type {
  BrowserWorker,
  EphemeralSessionHandoff,
  TrustedEnrollmentHandle,
  VerifiedSessionIdentityHandle,
} from "./browser-worker.js";

const DEFAULT_MAX_OUTPUT_BYTES = 128 * 1024;
const MAX_OUTPUT_BYTES = 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 60_000;
const MAX_TIMEOUT_MS = 120_000;
const MAX_JSON_DEPTH = 16;
const MAX_JSON_VALUES = 2_000;
const ALLOWED_PARENT_ENVIRONMENT = new Set([
  "APPDATA", "COMSPEC", "HOME", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA",
  "NODE_EXTRA_CA_CERTS", "NO_PROXY", "PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT",
  "TEMP", "TMP", "USERPROFILE", "HTTP_PROXY", "HTTPS_PROXY",
  "SF_CONFIG_DIR", "SFDX_CONFIG_DIR", "SF_USE_GENERIC_UNIX_KEYCHAIN",
]);
const ALLOWED_REQUEST_ENVIRONMENT = new Set(["SF_AUTOUPDATE_DISABLE", "SF_DISABLE_TELEMETRY"]);
const SECRET_ENVIRONMENT_NAME = /(?:OPENAI|AZURE|DATABASE|PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|HMAC|PROFILE)/i;

export interface SalesforceCliProcessRequest {
  readonly executable: string;
  readonly arguments: readonly string[];
  readonly timeoutMs: number;
  readonly maximumOutputBytes: number;
  readonly environment: Readonly<Record<string, string>>;
}

export interface SalesforceCliProcessResult {
  readonly exitCode: number;
  readonly stdout: Uint8Array;
  readonly outputExceeded: boolean;
  readonly timedOut: boolean;
}

export interface SalesforceCliProcessRunner {
  run(request: Readonly<SalesforceCliProcessRequest>): Promise<SalesforceCliProcessResult>;
}

export interface SalesforceCliSessionBrokerConfig {
  /** Machine-local configuration. This value must never come from an API request. */
  readonly targetOrgAlias: string;
  readonly frontdoorOrigin: string;
  readonly lightningOrigin: string;
  readonly navigationBridgeOrigins: readonly string[];
  readonly resourceOrigins: readonly string[];
  readonly orgBinding: string;
  readonly actorBinding: string;
  /** Selects the stable CLI identity field used to bind the authenticated actor. */
  readonly actorBindingSource: "USERNAME" | "USER_ID";
  readonly salesforceExecutable?: string;
  readonly allowedFrontdoorHostnameSuffixes?: readonly string[];
  readonly allowedLightningHostnameSuffixes?: readonly string[];
  readonly maximumOutputBytes?: number;
  readonly timeoutMs?: number;
}

interface SessionIssuer {
  verifySessionIdentity(
    handle: TrustedEnrollmentHandle,
    material: { orgBinding: string; actorBinding: string },
  ): VerifiedSessionIdentityHandle;
  issueSession(
    handle: TrustedEnrollmentHandle,
    identityHandle: VerifiedSessionIdentityHandle,
    material: {
      entryUrl: string;
      frontdoorOrigin: string;
      lightningOrigin: string;
      navigationBridgeOrigins: readonly string[];
      resourceOrigins: readonly string[];
    },
  ): EphemeralSessionHandoff;
}

type ValidatedConfig = {
  targetOrgAlias: string;
  frontdoorOrigin: string;
  lightningOrigin: string;
  navigationBridgeOrigins: readonly string[];
  resourceOrigins: readonly string[];
  orgBinding: string;
  actorBinding: string;
  actorBindingSource: "USERNAME" | "USER_ID";
  salesforceExecutable: string;
  allowedFrontdoorHostnameSuffixes: readonly string[];
  allowedLightningHostnameSuffixes: readonly string[];
  maximumOutputBytes: number;
  timeoutMs: number;
};

export class SalesforceCliSessionError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "SalesforceCliSessionError";
  }
}

/**
 * Acquires one CLI frontdoor credential and immediately converts it to the browser worker's
 * opaque, one-shot in-memory handoff. Callers cannot provide an alias, URL, origin, or binding.
 */
export class SalesforceCliSessionBroker {
  readonly #config: ValidatedConfig;
  readonly #runner: SalesforceCliProcessRunner;
  readonly #issuer: SessionIssuer;

  constructor(
    config: SalesforceCliSessionBrokerConfig,
    issuer: Pick<BrowserWorker, "verifySessionIdentity" | "issueSession">,
    runner: SalesforceCliProcessRunner = new NodeSalesforceCliProcessRunner(),
  ) {
    this.#config = validateConfig(config);
    this.#issuer = issuer;
    this.#runner = runner;
  }

  async acquire(handle: TrustedEnrollmentHandle): Promise<EphemeralSessionHandoff> {
    const display = await this.#run([
      "org",
      "display",
      "--target-org",
      this.#config.targetOrgAlias,
      "--json",
    ], "DISPLAY");

    let identity: { orgBinding: string; actorBinding: string };
    try {
      identity = extractIdentityBindings(display.stdout, this.#config);
    } catch {
      throw new SalesforceCliSessionError("CLI_DISPLAY_OUTPUT_INVALID");
    }
    let identityHandle: VerifiedSessionIdentityHandle;
    try {
      identityHandle = this.#issuer.verifySessionIdentity(handle, identity);
    } catch {
      throw new SalesforceCliSessionError("CLI_IDENTITY_MISMATCH");
    }

    const result = await this.#run([
      "org",
      "open",
      "--target-org",
      this.#config.targetOrgAlias,
      "--url-only",
      "--json",
    ], "OPEN");

    let entryUrl: string;
    try {
      entryUrl = extractSessionUrl(result.stdout);
      validateSessionUrl(entryUrl, this.#config);
    } catch (error) {
      if (error instanceof SalesforceCliSessionError) {
        throw error;
      }
      throw new SalesforceCliSessionError("CLI_OUTPUT_INVALID");
    }

    try {
      return this.#issuer.issueSession(handle, identityHandle, {
        entryUrl,
        frontdoorOrigin: this.#config.frontdoorOrigin,
        lightningOrigin: this.#config.lightningOrigin,
        navigationBridgeOrigins: this.#config.navigationBridgeOrigins,
        resourceOrigins: this.#config.resourceOrigins,
      });
    } catch {
      throw new SalesforceCliSessionError("SESSION_HANDOFF_REJECTED");
    }
  }

  async #run(
    arguments_: readonly string[],
    phase: "DISPLAY" | "OPEN",
  ): Promise<SalesforceCliProcessResult> {
    let result: SalesforceCliProcessResult;
    try {
      result = await this.#runner.run(Object.freeze({
        executable: this.#config.salesforceExecutable,
        arguments: Object.freeze([...arguments_]),
        timeoutMs: this.#config.timeoutMs,
        maximumOutputBytes: this.#config.maximumOutputBytes,
        environment: Object.freeze({
          SF_AUTOUPDATE_DISABLE: "true",
          SF_DISABLE_TELEMETRY: "true",
        }),
      }));
    } catch {
      throw new SalesforceCliSessionError(`CLI_${phase}_PROCESS_FAILED`);
    }
    if (result.timedOut) throw new SalesforceCliSessionError(`CLI_${phase}_TIMEOUT`);
    if (result.outputExceeded || result.stdout.byteLength > this.#config.maximumOutputBytes) {
      throw new SalesforceCliSessionError(`CLI_${phase}_OUTPUT_OVERSIZED`);
    }
    if (!Number.isSafeInteger(result.exitCode) || result.exitCode !== 0) {
      throw new SalesforceCliSessionError(`CLI_${phase}_EXIT_NONZERO`);
    }
    return result;
  }
}

export class NodeSalesforceCliProcessRunner implements SalesforceCliProcessRunner {
  run(request: Readonly<SalesforceCliProcessRequest>): Promise<SalesforceCliProcessResult> {
    return new Promise((resolve, reject) => {
      let stdoutBytes = 0;
      let outputExceeded = false;
      let timedOut = false;
      const stdout: Buffer[] = [];
      let settled = false;

      const launch = resolveLaunch(request.executable, request.arguments);
      const child = spawn(launch.executable, launch.arguments, {
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "ignore"],
        env: buildSalesforceCliEnvironment(process.env, request.environment),
      });
      const timeout = setTimeout(() => {
        timedOut = true;
        child.kill();
      }, request.timeoutMs);

      child.stdout.on("data", (chunk: Buffer | string) => {
        const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        stdoutBytes += bytes.byteLength;
        if (stdoutBytes > request.maximumOutputBytes) {
          outputExceeded = true;
          child.kill();
          return;
        }
        stdout.push(bytes);
      });
      child.once("error", () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        reject(new SalesforceCliSessionError("CLI_PROCESS_FAILED"));
      });
      child.once("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve(
          Object.freeze({
            exitCode: Number.isInteger(code) ? code! : -1,
            stdout: outputExceeded ? new Uint8Array() : Buffer.concat(stdout),
            outputExceeded,
            timedOut,
          }),
        );
      });
    });
  }
}

/** Builds a minimal child environment so unrelated Neo credentials never reach Salesforce CLI. */
export function buildSalesforceCliEnvironment(
  parent: Readonly<Record<string, string | undefined>>,
  requested: Readonly<Record<string, string>>,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = Object.create(null) as NodeJS.ProcessEnv;
  for (const [name, value] of Object.entries(parent)) {
    const canonical = name.toUpperCase();
    if (value !== undefined && ALLOWED_PARENT_ENVIRONMENT.has(canonical) && !SECRET_ENVIRONMENT_NAME.test(canonical)) {
      environment[name] = value;
    }
  }
  for (const [name, value] of Object.entries(requested)) {
    if (!ALLOWED_REQUEST_ENVIRONMENT.has(name.toUpperCase()) || !/^(?:true|false)$/i.test(value)) {
      throw new SalesforceCliSessionError("CLI_ENVIRONMENT_INVALID");
    }
    environment[name] = value;
  }
  return environment;
}

function validateConfig(config: SalesforceCliSessionBrokerConfig): ValidatedConfig {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(config.targetOrgAlias)) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  if (!isBinding(config.orgBinding) || !isBinding(config.actorBinding)) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  if (config.actorBindingSource !== "USERNAME" && config.actorBindingSource !== "USER_ID") {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  const frontdoorSuffixes = validateSuffixes(
    config.allowedFrontdoorHostnameSuffixes ?? [
      ".lightning.force.com",
      ".my.salesforce.com",
    ],
  );
  const lightningSuffixes = validateSuffixes(
    config.allowedLightningHostnameSuffixes ?? [".lightning.force.com"],
  );
  const frontdoorOrigin = validateExactOrigin(
    config.frontdoorOrigin,
    frontdoorSuffixes,
    "FRONTDOOR_ORIGIN_NOT_ALLOWED",
  );
  const lightningOrigin = validateExactOrigin(
    config.lightningOrigin,
    lightningSuffixes,
    "LIGHTNING_ORIGIN_NOT_ALLOWED",
  );
  if (frontdoorOrigin === lightningOrigin) {
    throw new SalesforceCliSessionError("DISTINCT_ORIGINS_REQUIRED");
  }
  const navigationBridgeOrigins = validatePinnedOrigins(
    config.navigationBridgeOrigins,
    (hostname) => hostname.endsWith(".file.force.com"),
  );
  const resourceOrigins = validatePinnedOrigins(
    config.resourceOrigins,
    (hostname) =>
      hostname === "login.salesforce.com" ||
      hostname.endsWith(".static.lightning.force.com"),
  );
  const maximumOutputBytes = config.maximumOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
  const timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  if (
    !Number.isInteger(maximumOutputBytes) ||
    maximumOutputBytes < 1024 ||
    maximumOutputBytes > MAX_OUTPUT_BYTES ||
    !Number.isInteger(timeoutMs) ||
    timeoutMs < 1_000 ||
    timeoutMs > MAX_TIMEOUT_MS
  ) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  const executable = config.salesforceExecutable ?? (process.platform === "win32" ? "sf.cmd" : "sf");
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._:\\/ -]{0,1023}$/.test(executable) ||
    executable.endsWith(" ")
  ) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  return Object.freeze({
    targetOrgAlias: config.targetOrgAlias,
    frontdoorOrigin,
    lightningOrigin,
    navigationBridgeOrigins,
    resourceOrigins,
    orgBinding: config.orgBinding,
    actorBinding: config.actorBinding,
    actorBindingSource: config.actorBindingSource,
    salesforceExecutable: executable,
    allowedFrontdoorHostnameSuffixes: frontdoorSuffixes,
    allowedLightningHostnameSuffixes: lightningSuffixes,
    maximumOutputBytes,
    timeoutMs,
  });
}

function extractIdentityBindings(
  raw: Uint8Array,
  config: ValidatedConfig,
): { orgBinding: string; actorBinding: string } {
  const parsed = parseCliJson(raw, "CLI_DISPLAY_OUTPUT_INVALID");
  if (!isRecord(parsed) || parsed.status !== 0 || !isRecord(parsed.result)) {
    throw new SalesforceCliSessionError("CLI_DISPLAY_OUTPUT_INVALID");
  }
  const orgId = parsed.result.id;
  if (!isSalesforceId(orgId)) {
    throw new SalesforceCliSessionError("CLI_DISPLAY_OUTPUT_INVALID");
  }
  const reportedAlias = parsed.result.alias;
  if (reportedAlias !== undefined && reportedAlias !== config.targetOrgAlias) {
    throw new SalesforceCliSessionError("CLI_DISPLAY_OUTPUT_INVALID");
  }
  let actorBinding: string;
  if (config.actorBindingSource === "USER_ID") {
    const userId = parsed.result.userId;
    if (!isSalesforceId(userId)) {
      throw new SalesforceCliSessionError("CLI_DISPLAY_OUTPUT_INVALID");
    }
    actorBinding = digest(`salesforce-user:${userId}`);
  } else {
    const username = parsed.result.username;
    if (
      typeof username !== "string" ||
      username.length < 3 ||
      username.length > 320 ||
      username.trim() !== username ||
      /[\u0000-\u001f\u007f]/.test(username)
    ) {
      throw new SalesforceCliSessionError("CLI_DISPLAY_OUTPUT_INVALID");
    }
    actorBinding = digest(`salesforce-username:${username.toLowerCase()}`);
  }
  return Object.freeze({
    orgBinding: digest(`salesforce-org:${orgId}`),
    actorBinding,
  });
}

function parseCliJson(raw: Uint8Array, code: string): unknown {
  try {
    return parseStrictBoundedJson(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch {
    throw new SalesforceCliSessionError(code);
  }
}

function extractSessionUrl(raw: Uint8Array): string {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
  } catch {
    throw new SalesforceCliSessionError("CLI_OUTPUT_INVALID");
  }
  let parsed: unknown;
  try {
    parsed = parseStrictBoundedJson(text);
  } catch {
    throw new SalesforceCliSessionError("CLI_OUTPUT_INVALID");
  }
  if (!isRecord(parsed) || parsed.status !== 0 || !isRecord(parsed.result)) {
    throw new SalesforceCliSessionError("CLI_OUTPUT_INVALID");
  }
  const url = parsed.result.url;
  if (typeof url !== "string" || url.length < 1 || url.length > 8_192) {
    throw new SalesforceCliSessionError("CLI_OUTPUT_INVALID");
  }
  return url;
}

function validateSessionUrl(entryUrl: string, config: ValidatedConfig): void {
  let parsed: URL;
  try {
    parsed = new URL(entryUrl);
  } catch {
    throw new SalesforceCliSessionError("CLI_SESSION_URL_INVALID");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.hash ||
    parsed.pathname !== "/secur/frontdoor.jsp" ||
    !hasExactFrontdoorCredentialShape(parsed) ||
    !hostnameMatches(parsed.hostname, config.allowedFrontdoorHostnameSuffixes)
  ) {
    throw new SalesforceCliSessionError("CLI_SESSION_URL_NOT_ALLOWED");
  }
  if (parsed.origin !== config.frontdoorOrigin) {
    throw new SalesforceCliSessionError("CLI_SESSION_ORIGIN_MISMATCH");
  }
}

function hasExactFrontdoorCredentialShape(parsed: URL): boolean {
  const keys = [...parsed.searchParams.keys()];
  const legacy =
    keys.length === 1 &&
    keys[0] === "sid" &&
    parsed.searchParams.getAll("sid").length === 1 &&
    Boolean(parsed.searchParams.get("sid"));
  const current =
    keys.length === 2 &&
    new Set(keys).size === 2 &&
    keys.every((key) => key === "otp" || key === "cshc") &&
    parsed.searchParams.getAll("otp").length === 1 &&
    parsed.searchParams.getAll("cshc").length === 1 &&
    Boolean(parsed.searchParams.get("otp")) &&
    Boolean(parsed.searchParams.get("cshc"));
  return legacy || current;
}

function validateExactOrigin(value: string, suffixes: readonly string[], code: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new SalesforceCliSessionError(code);
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    parsed.origin !== value ||
    !hostnameMatches(parsed.hostname, suffixes)
  ) {
    throw new SalesforceCliSessionError(code);
  }
  return parsed.origin;
}

function validateSuffixes(values: readonly string[]): readonly string[] {
  if (
    values.length < 1 ||
    values.length > 16 ||
    values.some(
      (value) =>
        !/^\.[a-z0-9.-]+$/.test(value) ||
        value.includes("..") ||
        value.length > 253,
    ) ||
    new Set(values).size !== values.length
  ) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  return Object.freeze([...values].sort());
}

function validatePinnedOrigins(
  values: readonly string[],
  allowedHostname: (hostname: string) => boolean,
): readonly string[] {
  if (!Array.isArray(values) || values.length > 8) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  const origins = values.map((value) => {
    let parsed: URL;
    try { parsed = new URL(value); } catch { throw new SalesforceCliSessionError("HOST_CONFIG_INVALID"); }
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash || parsed.origin !== value ||
      !allowedHostname(parsed.hostname)
    ) throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
    return parsed.origin;
  });
  if (new Set(origins).size !== origins.length || [...origins].sort().some((value, index) => value !== origins[index])) {
    throw new SalesforceCliSessionError("HOST_CONFIG_INVALID");
  }
  return Object.freeze(origins);
}

function hostnameMatches(hostname: string, suffixes: readonly string[]): boolean {
  const canonical = hostname.toLowerCase();
  return suffixes.some(
    (suffix) => canonical.length > suffix.length && canonical.endsWith(suffix),
  );
}

export function resolveLaunch(
  executable: string,
  arguments_: readonly string[],
  platform: NodeJS.Platform = process.platform,
  commandInterpreter = process.env.ComSpec ?? "cmd.exe",
): { executable: string; arguments: string[] } {
  if (platform !== "win32" || !/\.(?:cmd|bat)$/i.test(executable)) {
    return { executable, arguments: [...arguments_] };
  }
  if (arguments_.some((value) => !/^[A-Za-z0-9._-]+$/.test(value))) {
    throw new SalesforceCliSessionError("CLI_PROCESS_ARGUMENT_INVALID");
  }
  return {
    executable: commandInterpreter,
    arguments: ["/d", "/s", "/c", executable, ...arguments_],
  };
}

function isBinding(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSalesforceId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$/.test(value);
}

function digest(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

/** A bounded JSON parser used so duplicate object keys cannot be hidden by JSON.parse. */
export function parseStrictBoundedJson(source: string): unknown {
  if (Buffer.byteLength(source, "utf8") > MAX_OUTPUT_BYTES) {
    throw new SalesforceCliSessionError("JSON_INPUT_OVERSIZED");
  }
  return new StrictJsonParser(source).parse();
}

class StrictJsonParser {
  #index = 0;
  #depth = 0;
  #values = 0;

  constructor(private readonly source: string) {}

  parse(): unknown {
    const value = this.#value();
    this.#space();
    if (this.#index !== this.source.length) throw new Error("INVALID_JSON");
    return value;
  }

  #value(): unknown {
    this.#space();
    this.#values += 1;
    if (this.#values > MAX_JSON_VALUES) throw new Error("JSON_CAPACITY");
    const character = this.source[this.#index];
    if (character === "{") return this.#object();
    if (character === "[") return this.#array();
    if (character === '"') return this.#string();
    if (this.source.startsWith("true", this.#index)) return this.#literal("true", true);
    if (this.source.startsWith("false", this.#index)) return this.#literal("false", false);
    if (this.source.startsWith("null", this.#index)) return this.#literal("null", null);
    return this.#number();
  }

  #object(): Record<string, unknown> {
    this.#enter();
    this.#index += 1;
    const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    const keys = new Set<string>();
    this.#space();
    if (this.source[this.#index] === "}") {
      this.#index += 1;
      this.#leave();
      return output;
    }
    while (true) {
      this.#space();
      if (this.source[this.#index] !== '"') throw new Error("INVALID_JSON");
      const key = this.#string();
      if (keys.has(key)) throw new Error("DUPLICATE_KEY");
      keys.add(key);
      this.#space();
      if (this.source[this.#index] !== ":") throw new Error("INVALID_JSON");
      this.#index += 1;
      output[key] = this.#value();
      this.#space();
      const separator = this.source[this.#index++];
      if (separator === "}") break;
      if (separator !== ",") throw new Error("INVALID_JSON");
    }
    this.#leave();
    return output;
  }

  #array(): unknown[] {
    this.#enter();
    this.#index += 1;
    const output: unknown[] = [];
    this.#space();
    if (this.source[this.#index] === "]") {
      this.#index += 1;
      this.#leave();
      return output;
    }
    while (true) {
      output.push(this.#value());
      this.#space();
      const separator = this.source[this.#index++];
      if (separator === "]") break;
      if (separator !== ",") throw new Error("INVALID_JSON");
    }
    this.#leave();
    return output;
  }

  #string(): string {
    const start = this.#index;
    this.#index += 1;
    let escaped = false;
    while (this.#index < this.source.length) {
      const character = this.source[this.#index++];
      if (escaped) {
        escaped = false;
        continue;
      }
      if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        const token = this.source.slice(start, this.#index);
        const value: unknown = JSON.parse(token);
        if (typeof value !== "string") throw new Error("INVALID_JSON");
        return value;
      } else if (character.charCodeAt(0) < 0x20) {
        throw new Error("INVALID_JSON");
      }
    }
    throw new Error("INVALID_JSON");
  }

  #number(): number {
    const match = this.source
      .slice(this.#index)
      .match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!match) throw new Error("INVALID_JSON");
    this.#index += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value)) throw new Error("INVALID_JSON");
    return value;
  }

  #literal(token: string, value: unknown): unknown {
    this.#index += token.length;
    return value;
  }

  #space(): void {
    while (/\s/.test(this.source[this.#index] ?? "")) this.#index += 1;
  }

  #enter(): void {
    this.#depth += 1;
    if (this.#depth > MAX_JSON_DEPTH) throw new Error("JSON_DEPTH");
  }

  #leave(): void {
    this.#depth -= 1;
  }
}
