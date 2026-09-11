export type LiveGateStatus = {
  gate_id: string;
  state: "LOCALLY_VALID" | "NOT_CURRENT";
};

export type LiveCampaignStatus = {
  campaign_id: string;
  replay_state: "EMPTY" | "VALIDATED_INCOMPLETE" | "QUARANTINED_INCOMPLETE";
  validator_replayed: boolean;
  evidence_state: "INCOMPLETE";
  requirements_satisfied: false;
  release_eligible: false;
  accepted_completion_numerator: 0;
  completion_denominator: 15;
  receipt_count: number;
  acceptance_profile_sha256: string;
  ledger_mode: "POSTGRESQL" | "SQLITE";
  ledger_degradation_code: string | null;
  locally_valid_gate_ids: string[];
  gates: LiveGateStatus[];
  quarantined: boolean;
  stored_quarantine_reasons: string[];
  gap_codes: string[];
  evaluation_sha256: string | null;
};

export class LiveCampaignClientError extends Error {}

const campaignPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const digestPattern = /^[a-f0-9]{64}$/;
const requiredGateIds = new Set([
  "SF-L01", "SF-L02", "SF-L03", "SF-L04", "SF-L05", "SF-L06", "SF-L07", "SF-L08", "SF-L09",
  "SF-C01", "SF-C02", "SF-C03", "SF-C04", "SF-C05", "SF-C06",
]);
const maximumResponseBytes = 131_072;
const codePattern = /^[A-Z][A-Z0-9_]{0,127}$/;

function exactKeys(value: Record<string, unknown>, expected: readonly string[]) {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === [...expected].sort()[index]);
}

function strings(value: unknown, maximum: number): value is string[] {
  return Array.isArray(value) && value.length <= maximum
    && value.every((item) => typeof item === "string" && item.length <= 128)
    && new Set(value).size === value.length;
}

function decodeGate(value: unknown): LiveGateStatus {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new LiveCampaignClientError("LIVE_STATUS_INVALID");
  const item = value as Record<string, unknown>;
  if (!exactKeys(item, ["gate_id", "state"]) || typeof item.gate_id !== "string" || !requiredGateIds.has(item.gate_id)
    || !["LOCALLY_VALID", "NOT_CURRENT"].includes(String(item.state))) {
    throw new LiveCampaignClientError("LIVE_STATUS_INVALID");
  }
  return item as LiveGateStatus;
}

export function decodeLiveCampaignStatus(value: unknown): LiveCampaignStatus {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new LiveCampaignClientError("LIVE_STATUS_INVALID");
  const item = value as Record<string, unknown>;
  const keys = [
    "campaign_id", "replay_state", "validator_replayed", "evidence_state",
    "requirements_satisfied", "release_eligible", "accepted_completion_numerator",
    "completion_denominator", "receipt_count", "acceptance_profile_sha256", "ledger_mode",
    "ledger_degradation_code", "locally_valid_gate_ids", "gates", "quarantined",
    "stored_quarantine_reasons", "gap_codes", "evaluation_sha256",
  ] as const;
  if (!exactKeys(item, keys)
    || typeof item.campaign_id !== "string" || !campaignPattern.test(item.campaign_id)
    || !["EMPTY", "VALIDATED_INCOMPLETE", "QUARANTINED_INCOMPLETE"].includes(String(item.replay_state))
    || typeof item.validator_replayed !== "boolean"
    || item.evidence_state !== "INCOMPLETE"
    || item.requirements_satisfied !== false || item.release_eligible !== false
    || item.accepted_completion_numerator !== 0 || item.completion_denominator !== 15
    || !Number.isInteger(item.receipt_count) || Number(item.receipt_count) < 0 || Number(item.receipt_count) > 256
    || typeof item.acceptance_profile_sha256 !== "string" || !digestPattern.test(item.acceptance_profile_sha256)
    || !["POSTGRESQL", "SQLITE"].includes(String(item.ledger_mode))
    || !(item.ledger_degradation_code === null || (typeof item.ledger_degradation_code === "string" && /^[A-Z][A-Z0-9_]{0,127}$/.test(item.ledger_degradation_code)))
    || !strings(item.locally_valid_gate_ids, 15) || !item.locally_valid_gate_ids.every((gate) => requiredGateIds.has(gate))
    || !Array.isArray(item.gates) || item.gates.length !== 15
    || typeof item.quarantined !== "boolean"
    || !strings(item.stored_quarantine_reasons, 256) || !strings(item.gap_codes, 512)
    || !item.stored_quarantine_reasons.every((code) => codePattern.test(code))
    || !item.gap_codes.every((code) => codePattern.test(code))
    || !(item.evaluation_sha256 === null || (typeof item.evaluation_sha256 === "string" && digestPattern.test(item.evaluation_sha256)))) {
    throw new LiveCampaignClientError("LIVE_STATUS_INVALID");
  }
  const gates = item.gates.map(decodeGate);
  const locallyValidIds = item.locally_valid_gate_ids;
  const localIds = gates.filter((gate) => gate.state === "LOCALLY_VALID").map((gate) => gate.gate_id);
  if (new Set(gates.map((gate) => gate.gate_id)).size !== 15
    || localIds.length !== item.locally_valid_gate_ids.length
    || localIds.some((gate) => !locallyValidIds.includes(gate))
    || localIds.length > Number(item.receipt_count)
    || (item.quarantined !== (item.replay_state === "QUARANTINED_INCOMPLETE"))
    || (item.stored_quarantine_reasons.length > 0 && !item.quarantined)
    || (item.receipt_count === 0 && (item.validator_replayed || item.evaluation_sha256 !== null || localIds.length > 0
      || item.replay_state === "VALIDATED_INCOMPLETE"))
    || (Number(item.receipt_count) > 0 && (!item.validator_replayed || item.evaluation_sha256 === null
      || item.replay_state === "EMPTY"))) {
    throw new LiveCampaignClientError("LIVE_STATUS_INVALID");
  }
  return { ...(item as Omit<LiveCampaignStatus, "gates">), gates };
}

export async function getLiveCampaignStatus(
  apiBase: string, campaignId: string, timeoutMs = 10_000,
): Promise<LiveCampaignStatus> {
  if (!campaignPattern.test(campaignId)) throw new LiveCampaignClientError("LIVE_CAMPAIGN_ID_INVALID");
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000) {
    throw new LiveCampaignClientError("LIVE_STATUS_CONFIGURATION_INVALID");
  }
  const controller = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timedOut = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      if (reader) void reader.cancel().catch(() => undefined);
      reject(new LiveCampaignClientError("LIVE_STATUS_TIMED_OUT"));
    }, timeoutMs);
  });
  try {
    const load = async (): Promise<LiveCampaignStatus> => {
      const response = await fetch(`${apiBase}/api/v1/live-campaigns/${encodeURIComponent(campaignId)}/status`, {
        method: "GET", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      if (controller.signal.aborted) throw new LiveCampaignClientError("LIVE_STATUS_TIMED_OUT");
      if (!response.ok) throw new LiveCampaignClientError(`LIVE_STATUS_${response.status}`);
      const declaredLength = response.headers.get("content-length");
      if (!response.body) throw new LiveCampaignClientError("LIVE_STATUS_RESPONSE_INVALID");
      reader = response.body.getReader();
      if ((declaredLength !== null && (!/^\d+$/.test(declaredLength)
        || Number(declaredLength) > maximumResponseBytes))) {
        throw new LiveCampaignClientError("LIVE_STATUS_RESPONSE_INVALID");
      }
      const chunks: Uint8Array[] = [];
      let byteCount = 0;
      while (true) {
        const chunk = await reader.read();
        if (controller.signal.aborted) throw new LiveCampaignClientError("LIVE_STATUS_TIMED_OUT");
        if (chunk.done) break;
        byteCount += chunk.value.byteLength;
        if (byteCount > maximumResponseBytes) throw new LiveCampaignClientError("LIVE_STATUS_RESPONSE_INVALID");
        chunks.push(chunk.value);
      }
      const bytes = new Uint8Array(byteCount);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      let document: unknown;
      try {
        document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      } catch { throw new LiveCampaignClientError("LIVE_STATUS_RESPONSE_INVALID"); }
      const status = decodeLiveCampaignStatus(document);
      if (status.campaign_id !== campaignId) throw new LiveCampaignClientError("LIVE_STATUS_INVALID");
      return status;
    };
    return await Promise.race([load(), timedOut]);
  } catch (error) {
    if (error instanceof LiveCampaignClientError) throw error;
    if (controller.signal.aborted) throw new LiveCampaignClientError("LIVE_STATUS_TIMED_OUT");
    throw new LiveCampaignClientError("LIVE_STATUS_UNAVAILABLE");
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    controller.abort();
    if (reader) void reader.cancel().catch(() => undefined);
  }
}
