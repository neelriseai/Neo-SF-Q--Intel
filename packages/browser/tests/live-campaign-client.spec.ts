import { expect, test } from "@playwright/test";
import {
  decodeLiveCampaignStatus,
  getLiveCampaignStatus,
} from "../../../apps/web/src/lib/live-campaign-client.js";

const digest = "a".repeat(64);
const gateIds = [
  "SF-L01", "SF-L02", "SF-L03", "SF-L04", "SF-L05", "SF-L06", "SF-L07", "SF-L08", "SF-L09",
  "SF-C01", "SF-C02", "SF-C03", "SF-C04", "SF-C05", "SF-C06",
];

function status() {
  const locallyValidGateIds: string[] = [];
  return {
    campaign_id: "campaign-fixture",
    replay_state: "EMPTY",
    validator_replayed: false,
    evidence_state: "INCOMPLETE",
    requirements_satisfied: false,
    release_eligible: false,
    accepted_completion_numerator: 0,
    completion_denominator: 15,
    receipt_count: 0,
    acceptance_profile_sha256: digest,
    ledger_mode: "SQLITE",
    ledger_degradation_code: "POSTGRESQL_UNAVAILABLE",
    locally_valid_gate_ids: locallyValidGateIds,
    gates: gateIds.map((gate_id) => ({ gate_id, state: "NOT_CURRENT" })),
    quarantined: false,
    stored_quarantine_reasons: [],
    gap_codes: ["CAMPAIGN_RECEIPTS_NOT_FOUND"],
    evaluation_sha256: null,
  };
}

test("live campaign decoder preserves the fail-closed acceptance projection", () => {
  const decoded = decodeLiveCampaignStatus(status());
  expect(decoded.accepted_completion_numerator).toBe(0);
  expect(decoded.completion_denominator).toBe(15);
  expect(decoded.release_eligible).toBe(false);
  expect(decoded.gates).toHaveLength(15);
});

test("live campaign decoder rejects release promotion, extra data, and inconsistent gates", () => {
  expect(() => decodeLiveCampaignStatus({ ...status(), release_eligible: true })).toThrow("LIVE_STATUS_INVALID");
  expect(() => decodeLiveCampaignStatus({ ...status(), raw_receipts: [] })).toThrow("LIVE_STATUS_INVALID");
  const inconsistent = status();
  inconsistent.locally_valid_gate_ids = ["SF-L03"];
  expect(() => decodeLiveCampaignStatus(inconsistent)).toThrow("LIVE_STATUS_INVALID");
});

test("live campaign client performs a bounded read without accepting execution scope", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ input: String(input), init });
    return new Response(JSON.stringify(status()), { status: 200 });
  };
  try {
    const result = await getLiveCampaignStatus("http://local.invalid", "campaign-fixture");
    expect(result.evidence_state).toBe("INCOMPLETE");
    expect(calls).toHaveLength(1);
    expect(calls[0].init?.method).toBe("GET");
    expect(calls[0].init?.body).toBeUndefined();
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("live campaign client rejects invalid identifiers before network access", async () => {
  const originalFetch = globalThis.fetch;
  let called = false;
  globalThis.fetch = async () => {
    called = true;
    return new Response();
  };
  try {
    await expect(getLiveCampaignStatus("http://local.invalid", "../escape?scope=all"))
      .rejects.toThrow("LIVE_CAMPAIGN_ID_INVALID");
    expect(called).toBe(false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

function validatedStatus() {
  const result = status();
  result.replay_state = "VALIDATED_INCOMPLETE";
  result.validator_replayed = true;
  result.receipt_count = 1;
  return { ...result, evaluation_sha256: digest };
}

test("decoder requires the exact fifteen gates and matching local validity in both directions", () => {
  const wrongGate = status();
  wrongGate.gates[14].gate_id = "SF-L10";
  expect(() => decodeLiveCampaignStatus(wrongGate)).toThrow("LIVE_STATUS_INVALID");

  const missingSummary = validatedStatus();
  missingSummary.gates[0].state = "LOCALLY_VALID";
  expect(() => decodeLiveCampaignStatus(missingSummary)).toThrow("LIVE_STATUS_INVALID");
  missingSummary.locally_valid_gate_ids = ["SF-L01"];
  expect(decodeLiveCampaignStatus(missingSummary).locally_valid_gate_ids).toEqual(["SF-L01"]);
});

test("decoder rejects contradictory replay, quarantine, count, and evaluation claims", () => {
  for (const invalid of [
    { ...status(), validator_replayed: true },
    { ...status(), evaluation_sha256: digest },
    { ...status(), receipt_count: 1 },
    { ...status(), replay_state: "VALIDATED_INCOMPLETE" },
    { ...status(), quarantined: true },
    { ...status(), replay_state: "QUARANTINED_INCOMPLETE" },
    { ...status(), stored_quarantine_reasons: ["CAMPAIGN_QUARANTINED"] },
    { ...validatedStatus(), validator_replayed: false },
    { ...validatedStatus(), evaluation_sha256: null },
    { ...validatedStatus(), receipt_count: 0 },
    { ...validatedStatus(), replay_state: "EMPTY" },
  ]) expect(() => decodeLiveCampaignStatus(invalid)).toThrow("LIVE_STATUS_INVALID");

  expect(decodeLiveCampaignStatus({ ...status(), quarantined: true,
    replay_state: "QUARANTINED_INCOMPLETE", stored_quarantine_reasons: ["CAMPAIGN_QUARANTINED"],
  }).validator_replayed).toBe(false);
  expect(decodeLiveCampaignStatus({ ...validatedStatus(), quarantined: true,
    replay_state: "QUARANTINED_INCOMPLETE",
  }).validator_replayed).toBe(true);
});

test("decoder bounds reason strings and refuses arbitrary diagnostic payloads", () => {
  for (const gap_codes of [["A".repeat(129)], ["TOKEN=secret"], ["DUPLICATE", "DUPLICATE"]]) {
    expect(() => decodeLiveCampaignStatus({ ...status(), gap_codes })).toThrow("LIVE_STATUS_INVALID");
  }
});

for (const responseKind of ["declared-oversize", "streamed-oversize", "malformed-json", "invalid-utf8", "wrong-campaign"] as const) {
  test(`client rejects ${responseKind} within the bounded response reader`, async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => {
      if (responseKind === "declared-oversize") return new Response("{}", { headers: { "content-length": "131073" } });
      if (responseKind === "streamed-oversize") return new Response("x".repeat(131073));
      if (responseKind === "malformed-json") return new Response('{"token":"never-log"');
      if (responseKind === "invalid-utf8") return new Response(new Uint8Array([0xff, 0xfe]));
      return new Response(JSON.stringify({ ...status(), campaign_id: "another-campaign" }));
    };
    try {
      await expect(getLiveCampaignStatus("http://local.invalid", "campaign-fixture"))
        .rejects.toThrow(responseKind === "wrong-campaign" ? "LIVE_STATUS_INVALID" : "LIVE_STATUS_RESPONSE_INVALID");
    } finally { globalThis.fetch = originalFetch; }
  });
}

test("client deadline aborts a fetch that never resolves", async () => {
  const originalFetch = globalThis.fetch;
  let signal: AbortSignal | undefined;
  globalThis.fetch = async (_, init) => {
    signal = init?.signal as AbortSignal;
    return new Promise<Response>(() => undefined);
  };
  try {
    await expect(getLiveCampaignStatus("http://local.invalid", "campaign-fixture", 20))
      .rejects.toThrow("LIVE_STATUS_TIMED_OUT");
    expect(signal?.aborted).toBe(true);
  } finally { globalThis.fetch = originalFetch; }
});

test("client deadline covers stalled response bodies and cancels the reader", async () => {
  const originalFetch = globalThis.fetch;
  let cancelled = false;
  globalThis.fetch = async () => new Response(new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode('{"campaign_id":')); },
    cancel() { cancelled = true; },
  }));
  try {
    await expect(getLiveCampaignStatus("http://local.invalid", "campaign-fixture", 20))
      .rejects.toThrow("LIVE_STATUS_TIMED_OUT");
    expect(cancelled).toBe(true);
  } finally { globalThis.fetch = originalFetch; }
});
