import {
  decodeCandidateFoundationEvidence,
  decodeFoundationCaptureProblem,
  FoundationContractError,
  type CandidateFoundationEvidence,
  type FoundationProblemCode,
} from "./foundation-contract";

export type FoundationClientFailureCode =
  | FoundationProblemCode
  | "FOUNDATION_CAPTURE_UNAVAILABLE"
  | "FOUNDATION_RESPONSE_INVALID";

export class FoundationClientError extends Error {
  constructor(readonly code: FoundationClientFailureCode) {
    super(code);
    this.name = "FoundationClientError";
  }
}

const DEFAULT_FOUNDATION_CAPTURE_TIMEOUT_MS = 300_000;
const MINIMUM_FOUNDATION_CAPTURE_TIMEOUT_MS = 30_000;
const MAXIMUM_FOUNDATION_CAPTURE_TIMEOUT_MS = 900_000;

export function resolveFoundationCaptureTimeout(value: string | undefined): number {
  if (value === undefined || !/^\d+$/.test(value)) return DEFAULT_FOUNDATION_CAPTURE_TIMEOUT_MS;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed)
    && parsed >= MINIMUM_FOUNDATION_CAPTURE_TIMEOUT_MS
    && parsed <= MAXIMUM_FOUNDATION_CAPTURE_TIMEOUT_MS
    ? parsed
    : DEFAULT_FOUNDATION_CAPTURE_TIMEOUT_MS;
}

const FOUNDATION_CAPTURE_TIMEOUT_MS = resolveFoundationCaptureTimeout(
  process.env.NEXT_PUBLIC_FOUNDATION_CAPTURE_TIMEOUT_MS,
);

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function responseJson(response: Response): Promise<unknown> {
  const maximumBytes = 32_768;
  try {
    const declaredLength = response.headers.get("content-length");
    if (declaredLength !== null && Number(declaredLength) > maximumBytes) {
      throw new FoundationClientError("FOUNDATION_RESPONSE_INVALID");
    }
    if (!response.body) throw new FoundationClientError("FOUNDATION_RESPONSE_INVALID");
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maximumBytes) {
        await reader.cancel();
        throw new FoundationClientError("FOUNDATION_RESPONSE_INVALID");
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } catch (error) {
    if (error instanceof FoundationClientError || isAbort(error)) throw error;
    throw new FoundationClientError("FOUNDATION_RESPONSE_INVALID");
  }
}

export async function captureCandidateFoundation(
  apiBase: string,
  signal: AbortSignal,
  timeoutMs = FOUNDATION_CAPTURE_TIMEOUT_MS,
): Promise<CandidateFoundationEvidence> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), timeoutMs);
  const combinedSignal = AbortSignal.any([signal, timeout.signal]);
  try {
    const response = await fetch(`${apiBase}/api/v1/foundation/candidate-evidence`, {
      method: "POST",
      cache: "no-store",
      signal: combinedSignal,
    });
    const payload = await responseJson(response);
    if (!response.ok) {
      try {
        const problem = decodeFoundationCaptureProblem(payload);
        throw new FoundationClientError(problem.code);
      } catch (error) {
        if (error instanceof FoundationClientError) throw error;
        throw new FoundationClientError("FOUNDATION_CAPTURE_UNAVAILABLE");
      }
    }
    try {
      return await decodeCandidateFoundationEvidence(payload);
    } catch (error) {
      if (error instanceof FoundationContractError) {
        throw new FoundationClientError("FOUNDATION_RESPONSE_INVALID");
      }
      throw error;
    }
  } catch (error) {
    if (error instanceof FoundationClientError) throw error;
    if (signal.aborted) throw error;
    if (timeout.signal.aborted || isAbort(error)) throw new FoundationClientError("FOUNDATION_CAPTURE_UNAVAILABLE");
    throw new FoundationClientError("FOUNDATION_CAPTURE_UNAVAILABLE");
  } finally {
    clearTimeout(timer);
  }
}
