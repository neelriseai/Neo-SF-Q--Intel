export type FoundationStageName =
  | "VERIFIED_CHANGE_CAPTURE"
  | "TREE_GRAPH_PRODUCTION"
  | "OPERATION_SEED_MAPPING";

export type FoundationStageState = "EXECUTED" | "FAILED" | "NOT_RUN";
export type FoundationOutcome = "EXECUTED" | "ABSTAINED";

export interface FoundationReceiptReference {
  role: string;
  sha256: string;
}

export interface FoundationMeasurement {
  name: string;
  value: number;
}

export interface FoundationStageEvidence {
  schema_version: "1.0.0";
  stage: FoundationStageName;
  sequence: number;
  capability_id: string;
  state: FoundationStageState;
  authority_scope: "ANALYSIS_ONLY";
  release_eligible: false;
  local_foundation_stage_complete: boolean;
  input_receipts: FoundationReceiptReference[];
  output_receipt: FoundationReceiptReference | null;
  policy_id: string;
  policy_version: string;
  policy_sha256: string;
  evaluated_at: string;
  valid_until: string | null;
  gap_codes: string[];
  measurements: FoundationMeasurement[];
  evidence_sha256: string;
}

export interface CandidateFoundationEvidence {
  schema_version: "1.0.0";
  authority_scope: "ANALYSIS_ONLY";
  release_eligible: false;
  project_id: string;
  stages: [FoundationStageEvidence, FoundationStageEvidence, FoundationStageEvidence];
  outcome: FoundationOutcome;
  foundation_execution_complete: boolean;
  evidence_completeness: "INCOMPLETE";
  non_authoritative_projection: true;
  pipeline_policy_id: string;
  pipeline_policy_version: "1.0.1";
  pipeline_policy_sha256: string;
  pipeline_implementation_sha256: string;
  blocking_gap_codes: string[];
  chain_sha256: string;
}

export type FoundationProblemCode =
  | "FOUNDATION_SCOPE_INPUT_FORBIDDEN"
  | "FOUNDATION_PIPELINE_UNAVAILABLE";

export interface FoundationCaptureProblem {
  type: "FOUNDATION_CAPTURE_PROBLEM";
  code: FoundationProblemCode;
  authority_scope: "ANALYSIS_ONLY";
  evidence_completeness: "INCOMPLETE";
  release_eligible: false;
  retryable: boolean;
}

const SHA256 = /^[a-f0-9]{64}$/;
const IDENTIFIER = /^[a-z][a-z0-9.-]{0,99}$/;
const PROJECT_ID = /^[a-z0-9][a-z0-9._-]{0,199}$/;
const RECEIPT_ROLE = /^[a-z][a-z0-9-]{0,63}$/;
const MEASUREMENT_NAME = /^[a-z][a-z0-9_]{0,63}$/;
const GAP_CODE = /^[A-Z][A-Z0-9_]{0,99}$/;
const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const TIMESTAMP = /^(?:unavailable|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$/;
const AVAILABLE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

const STAGE_ORDER: FoundationStageName[] = [
  "VERIFIED_CHANGE_CAPTURE",
  "TREE_GRAPH_PRODUCTION",
  "OPERATION_SEED_MAPPING",
];

const CHANGE_GAPS = [
  "CANDIDATE_BUILD_NOT_VERIFIED",
  "CHANGE_SEED_SCOPE_NOT_ATTESTED",
  "CONFLICT_SCOPE_NOT_ATTESTED",
  "DEPLOYMENT_NOT_ATTESTED",
  "GIT_COMMIT_SIGNATURE_NOT_ATTESTED",
  "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
  "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
  "REPOSITORY_ORIGIN_NOT_ATTESTED",
  "RISK_FACTORS_NOT_ATTESTED",
  "TEST_EXECUTION_SCOPE_NOT_ATTESTED",
  "TEST_OBLIGATION_SCOPE_NOT_ATTESTED",
  "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
] as const;

const STAGE_CONTRACTS: Record<FoundationStageName, {
  capability: string;
  inputs: string[];
  output: string;
  requiredGaps: readonly string[];
}> = {
  VERIFIED_CHANGE_CAPTURE: {
    capability: "source.verified-change-set",
    inputs: [],
    output: "verified-change",
    requiredGaps: CHANGE_GAPS,
  },
  TREE_GRAPH_PRODUCTION: {
    capability: "source.tree-graph-production",
    inputs: ["verified-change"],
    output: "graph-production",
    requiredGaps: [...CHANGE_GAPS, "SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE"],
  },
  OPERATION_SEED_MAPPING: {
    capability: "source.change-seed-mapping",
    inputs: ["graph-production", "verified-change"],
    output: "operation-seeds",
    requiredGaps: [...CHANGE_GAPS, "GRAPH_INPUT_TREE_NOT_ATTESTED", "SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE"],
  },
};

const TOP_LEVEL_KEYS = [
  "schema_version",
  "authority_scope",
  "release_eligible",
  "project_id",
  "stages",
  "outcome",
  "foundation_execution_complete",
  "evidence_completeness",
  "non_authoritative_projection",
  "pipeline_policy_id",
  "pipeline_policy_version",
  "pipeline_policy_sha256",
  "pipeline_implementation_sha256",
  "blocking_gap_codes",
  "chain_sha256",
] as const;

const STAGE_KEYS = [
  "schema_version",
  "stage",
  "sequence",
  "capability_id",
  "state",
  "authority_scope",
  "release_eligible",
  "local_foundation_stage_complete",
  "input_receipts",
  "output_receipt",
  "policy_id",
  "policy_version",
  "policy_sha256",
  "evaluated_at",
  "valid_until",
  "gap_codes",
  "measurements",
  "evidence_sha256",
] as const;

export class FoundationContractError extends Error {
  constructor() {
    super("FOUNDATION_RESPONSE_INVALID");
    this.name = "FoundationContractError";
  }
}

function invalid(): never {
  throw new FoundationContractError();
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]) {
  const actual = Object.keys(value).sort();
  const required = [...expected].sort();
  if (actual.length !== required.length || actual.some((key, index) => key !== required[index])) invalid();
}

function stringValue(value: unknown, pattern: RegExp, maximum = 500): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum || !pattern.test(value)) invalid();
  return value;
}

function literal<T extends string | boolean>(value: unknown, expected: T): T {
  if (value !== expected) invalid();
  return expected;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) invalid();
  return value as T;
}

function boundedStrings(value: unknown, maximumItems: number, pattern = /^.{1,100}$/): string[] {
  if (!Array.isArray(value) || value.length > maximumItems) invalid();
  const items = value.map((item) => stringValue(item, pattern, 100));
  if (new Set(items).size !== items.length || items.some((item, index) => index > 0 && items[index - 1] > item)) invalid();
  return items;
}

function parseCanonicalTimestamp(value: string): number {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString().replace(".000Z", "Z") !== value) invalid();
  return parsed;
}

function receipt(value: unknown): FoundationReceiptReference {
  const item = record(value);
  exactKeys(item, ["role", "sha256"]);
  return {
    role: stringValue(item.role, RECEIPT_ROLE, 64),
    sha256: stringValue(item.sha256, SHA256, 64),
  };
}

function receipts(value: unknown): FoundationReceiptReference[] {
  if (!Array.isArray(value) || value.length > 3) invalid();
  const items = value.map(receipt);
  const roles = items.map((item) => item.role);
  if (new Set(roles).size !== roles.length || roles.some((role, index) => index > 0 && roles[index - 1] > role)) invalid();
  return items;
}

function measurement(value: unknown): FoundationMeasurement {
  const item = record(value);
  exactKeys(item, ["name", "value"]);
  if (!Number.isSafeInteger(item.value) || (item.value as number) < 0) invalid();
  return {
    name: stringValue(item.name, MEASUREMENT_NAME, 64),
    value: item.value as number,
  };
}

function measurements(value: unknown): FoundationMeasurement[] {
  if (!Array.isArray(value) || value.length > 20) invalid();
  const items = value.map(measurement);
  const names = items.map((item) => item.name);
  if (new Set(names).size !== names.length || names.some((name, index) => index > 0 && names[index - 1] > name)) invalid();
  return items;
}

function stageEvidence(value: unknown): FoundationStageEvidence {
  const item = record(value);
  exactKeys(item, STAGE_KEYS);
  if (!Number.isSafeInteger(item.sequence) || (item.sequence as number) < 1 || (item.sequence as number) > 3) invalid();
  if (typeof item.local_foundation_stage_complete !== "boolean") invalid();
  const state = oneOf(item.state, ["EXECUTED", "FAILED", "NOT_RUN"] as const);
  const inputs = receipts(item.input_receipts);
  const output = item.output_receipt === null ? null : receipt(item.output_receipt);
  const complete = item.local_foundation_stage_complete as boolean;
  if ((state === "EXECUTED") !== complete || (state === "EXECUTED") !== (output !== null)) invalid();
  const gaps = boundedStrings(item.gap_codes, 100, GAP_CODE);
  if (state === "NOT_RUN" && (inputs.length > 0 || !gaps.includes("UPSTREAM_STAGE_INCOMPLETE"))) invalid();
  const stage = oneOf(item.stage, STAGE_ORDER);
  const stageContract = STAGE_CONTRACTS[stage];
  const capabilityId = stringValue(item.capability_id, IDENTIFIER, 100);
  if (capabilityId !== stageContract.capability) invalid();
  if (state !== "NOT_RUN") {
    if (
      inputs.length !== stageContract.inputs.length
      || inputs.some((input, index) => input.role !== stageContract.inputs[index])
      || stageContract.requiredGaps.some((gap) => !gaps.includes(gap))
    ) invalid();
  }
  if (output !== null && output.role !== stageContract.output) invalid();
  const validUntil = item.valid_until === null
    ? null
    : stringValue(item.valid_until, AVAILABLE_TIMESTAMP, 20);
  const evaluatedAt = stringValue(item.evaluated_at, TIMESTAMP, 20);
  const evaluatedMillis = evaluatedAt === "unavailable" ? null : parseCanonicalTimestamp(evaluatedAt);
  const validUntilMillis = validUntil === null ? null : parseCanonicalTimestamp(validUntil);
  const decodedMeasurements = measurements(item.measurements);
  if (state === "EXECUTED" && (
    evaluatedMillis === null
    || validUntilMillis === null
    || evaluatedMillis >= validUntilMillis
  )) invalid();
  if (state === "FAILED" && (validUntil !== null || decodedMeasurements.length > 0)) invalid();
  if (state === "NOT_RUN" && (
    evaluatedAt !== "unavailable"
    || validUntil !== null
    || decodedMeasurements.length > 0
  )) invalid();
  return {
    schema_version: literal(item.schema_version, "1.0.0"),
    stage,
    sequence: item.sequence as number,
    capability_id: capabilityId,
    state,
    authority_scope: literal(item.authority_scope, "ANALYSIS_ONLY"),
    release_eligible: literal(item.release_eligible, false),
    local_foundation_stage_complete: complete,
    input_receipts: inputs,
    output_receipt: output,
    policy_id: stringValue(item.policy_id, /^.{1,200}$/, 200),
    policy_version: stringValue(item.policy_version, VERSION, 50),
    policy_sha256: stringValue(item.policy_sha256, SHA256, 64),
    evaluated_at: evaluatedAt,
    valid_until: validUntil,
    gap_codes: gaps,
    measurements: decodedMeasurements,
    evidence_sha256: stringValue(item.evidence_sha256, SHA256, 64),
  };
}

function sameReceipt(actual: FoundationReceiptReference | undefined, role: string, sha256: string) {
  return actual?.role === role && actual.sha256 === sha256;
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const item = record(value);
  return `{${Object.keys(item).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`).join(",")}}`;
}

async function stableSha256(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function decodeCandidateFoundationEvidence(value: unknown): Promise<CandidateFoundationEvidence> {
  const item = record(value);
  exactKeys(item, TOP_LEVEL_KEYS);
  if (!Array.isArray(item.stages) || item.stages.length !== 3) invalid();
  const decodedStages = item.stages.map(stageEvidence);
  decodedStages.forEach((stage, index) => {
    if (stage.stage !== STAGE_ORDER[index] || stage.sequence !== index + 1) invalid();
  });
  const stages = decodedStages as [FoundationStageEvidence, FoundationStageEvidence, FoundationStageEvidence];
  if (
    stages[0].state === "NOT_RUN"
    || (stages[1].state === "NOT_RUN") !== (stages[0].state !== "EXECUTED")
    || (stages[2].state === "NOT_RUN") !== (stages[1].state !== "EXECUTED")
  ) invalid();
  const allExecuted = stages.every((stage) => stage.state === "EXECUTED");
  if (item.foundation_execution_complete !== allExecuted) invalid();
  const outcome = oneOf(item.outcome, ["EXECUTED", "ABSTAINED"] as const);
  if ((outcome === "EXECUTED") !== allExecuted) invalid();

  const [change, graph, seeds] = stages;
  if (change.output_receipt && graph.state !== "NOT_RUN") {
    if (!sameReceipt(graph.input_receipts.find((entry) => entry.role === "verified-change"), "verified-change", change.output_receipt.sha256)) invalid();
  }
  if (change.output_receipt && graph.output_receipt && seeds.state !== "NOT_RUN") {
    if (
      seeds.input_receipts.length !== 2
      || !sameReceipt(seeds.input_receipts[0], "graph-production", graph.output_receipt.sha256)
      || !sameReceipt(seeds.input_receipts[1], "verified-change", change.output_receipt.sha256)
    ) invalid();
  }

  const blockingGaps = boundedStrings(item.blocking_gap_codes, 100, GAP_CODE);
  const stageGapUnion = [...new Set(stages.flatMap((stage) => stage.gap_codes))].sort();
  if (
    !blockingGaps.includes("RELEASE_EVIDENCE_MODEL_INCOMPLETE")
    || blockingGaps.length !== stageGapUnion.length
    || blockingGaps.some((gap, index) => gap !== stageGapUnion[index])
  ) invalid();

  const decoded: CandidateFoundationEvidence = {
    schema_version: literal(item.schema_version, "1.0.0"),
    authority_scope: literal(item.authority_scope, "ANALYSIS_ONLY"),
    release_eligible: literal(item.release_eligible, false),
    project_id: stringValue(item.project_id, PROJECT_ID, 200),
    stages,
    outcome,
    foundation_execution_complete: allExecuted,
    evidence_completeness: literal(item.evidence_completeness, "INCOMPLETE"),
    non_authoritative_projection: literal(item.non_authoritative_projection, true),
    pipeline_policy_id: stringValue(item.pipeline_policy_id, /^.{1,200}$/, 200),
    pipeline_policy_version: literal(item.pipeline_policy_version, "1.0.1"),
    pipeline_policy_sha256: stringValue(item.pipeline_policy_sha256, SHA256, 64),
    pipeline_implementation_sha256: stringValue(item.pipeline_implementation_sha256, SHA256, 64),
    blocking_gap_codes: blockingGaps,
    chain_sha256: stringValue(item.chain_sha256, SHA256, 64),
  };
  for (const stage of decoded.stages) {
    const { evidence_sha256: declared, ...body } = stage;
    if (declared !== await stableSha256(body)) invalid();
  }
  const { chain_sha256: declaredChain, ...chainBody } = decoded;
  if (declaredChain !== await stableSha256(chainBody)) invalid();
  return decoded;
}

export function decodeFoundationCaptureProblem(value: unknown): FoundationCaptureProblem {
  const item = record(value);
  exactKeys(item, ["type", "code", "authority_scope", "evidence_completeness", "release_eligible", "retryable"]);
  if (typeof item.retryable !== "boolean") invalid();
  return {
    type: literal(item.type, "FOUNDATION_CAPTURE_PROBLEM"),
    code: oneOf(item.code, ["FOUNDATION_SCOPE_INPUT_FORBIDDEN", "FOUNDATION_PIPELINE_UNAVAILABLE"] as const),
    authority_scope: literal(item.authority_scope, "ANALYSIS_ONLY"),
    evidence_completeness: literal(item.evidence_completeness, "INCOMPLETE"),
    release_eligible: literal(item.release_eligible, false),
    retryable: item.retryable,
  };
}
