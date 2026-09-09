from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.domain import AssuranceRun
from neo_sf_q_intel.edge_envelope import TrustedEdgeReplayInput, stable_sha256
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.ontology import NormalizedGraph, contract_sha256, load_canonical_ontology
from neo_sf_q_intel.path_replay import (
    CompleteGraphPathArtifact,
    CompletePathReplayPolicy,
    verify_complete_graph_path_replay,
)
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.propagation import (
    AnalysisRiskResult,
    PolicyContractError,
    PropagationEvaluationError,
    PropagationPolicy,
    evaluate_analysis_risk,
    load_analysis_risk_policy,
    traverse_propagation,
)


class ReleaseInputContractError(RuntimeError):
    """Raised when the independent R0.3 policy root is invalid."""


class ReleaseInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PolicyPin(ReleaseInputModel):
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion", min_length=1)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[a-f0-9]{64}$")


class ReleaseInputPolicy(ReleaseInputModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_replay_policy: PolicyPin = Field(alias="pathReplayPolicy")
    analysis_risk_policy: PolicyPin = Field(alias="analysisRiskPolicy")
    pinned_producer_policies: tuple[PolicyBinding, ...] = Field(
        alias="pinnedProducerPolicies", min_length=1
    )
    planned_missing_producer_roles: tuple[str, ...] = Field(
        alias="plannedMissingProducerRoles", min_length=1
    )
    required_policy_roles: tuple[str, ...] = Field(alias="requiredPolicyRoles", min_length=1)
    maximum_artifact_bytes: int = Field(alias="maximumArtifactBytes", ge=1)
    maximum_receipts_per_partition: int = Field(alias="maximumReceiptsPerPartition", ge=1)
    maximum_future_skew_seconds: int = Field(alias="maximumFutureSkewSeconds", ge=0)
    maximum_receipt_age_seconds: int = Field(alias="maximumReceiptAgeSeconds", ge=1)
    maximum_binding_freshness_seconds: int = Field(alias="maximumBindingFreshnessSeconds", ge=1)
    maximum_manifest_bytes: int = Field(alias="maximumManifestBytes", ge=1)

    @model_validator(mode="after")
    def validate_policy(self) -> ReleaseInputPolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.policy_version, "policyVersion")
        _require_identifier(self.policy_id, "policyId")
        if self.required_policy_roles != tuple(sorted(set(self.required_policy_roles))):
            raise ValueError("Required policy roles must be sorted and unique")
        roles = tuple(item.role for item in self.pinned_producer_policies)
        _require_sorted_unique(roles, "pinned producer policy roles")
        if not set(roles).issubset(self.required_policy_roles):
            raise ValueError("Pinned producer policy role is not required")
        _require_sorted_unique(
            self.planned_missing_producer_roles, "planned missing producer roles"
        )
        if set(self.planned_missing_producer_roles) & set(self.required_policy_roles):
            raise ValueError("A producer policy cannot be both required and missing")
        return self


class PolicyBinding(ReleaseInputModel):
    role: str = Field(min_length=1, max_length=100)
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: str = Field(alias="policyVersion", min_length=1, max_length=40)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[a-f0-9]{64}$")


class CandidateAnalysisBinding(ReleaseInputModel):
    """Digest-only advisory partitions; this is not a verified change set."""

    run_id: str = Field(min_length=1, max_length=100)
    trace_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=200)
    change_intent: str = Field(min_length=1, max_length=40)
    source_ref_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_ref_size_bytes: int = Field(ge=1, le=10_000)
    requirement_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requirement_size_bytes: int = Field(ge=1, le=100_000)
    changed_path_sha256s: tuple[str, ...] = Field(min_length=1)
    evidence_record_sha256s: tuple[str, ...] = Field(min_length=1)
    impact_record_sha256s: tuple[str, ...] = Field(min_length=1)
    claim_record_sha256s: tuple[str, ...] = Field(min_length=1)
    analysis_gap_record_sha256s: tuple[str, ...]
    selected_test_record_sha256s: tuple[str, ...] = Field(min_length=1)
    reasoning_policy_version: str = Field(min_length=1, max_length=40)
    reasoning_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_eval_set_id: str = Field(min_length=1, max_length=200)
    reasoning_eval_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_binding(self) -> CandidateAnalysisBinding:
        for values, label in (
            (self.changed_path_sha256s, "changed paths"),
            (self.evidence_record_sha256s, "evidence records"),
            (self.impact_record_sha256s, "impact records"),
            (self.claim_record_sha256s, "claim records"),
            (self.analysis_gap_record_sha256s, "analysis gaps"),
            (self.selected_test_record_sha256s, "selected tests"),
        ):
            _require_sorted_unique(values, label)
        _verify_model_digest(self, "binding_sha256")
        return self


class ChangeBuildBinding(ReleaseInputModel):
    project_id: str = Field(min_length=1, max_length=200)
    source_snapshot: str = Field(min_length=1, max_length=500)
    environment_id: str = Field(min_length=1, max_length=200)
    change_artifact_id: str = Field(min_length=1, max_length=500)
    change_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    change_artifact_size_bytes: int = Field(ge=1)
    build_artifact_id: str = Field(min_length=1, max_length=500)
    build_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    build_artifact_size_bytes: int = Field(ge=1)
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_digest(self) -> ChangeBuildBinding:
        _require_bounded_text(self.change_artifact_id, "change artifact locator")
        _require_bounded_text(self.build_artifact_id, "build artifact locator")
        _require_safe_relative_locator(self.change_artifact_id)
        _require_safe_relative_locator(self.build_artifact_id)
        _verify_model_digest(self, "binding_sha256")
        return self


class TimedReceipt(ReleaseInputModel):
    receipt_id: str = Field(min_length=1, max_length=200)
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=40)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: str
    valid_until: str
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RiskReceiptBinding(TimedReceipt):
    target_id: str = Field(min_length=1)
    build_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_sha256s: tuple[str, ...] = Field(min_length=1)
    risk_result: AnalysisRiskResult

    @model_validator(mode="after")
    def validate_receipt(self) -> RiskReceiptBinding:
        _require_sorted_unique(self.path_sha256s, "risk path digests")
        if self.risk_result.policy_id != self.policy_id or (
            self.risk_result.policy_version,
            self.risk_result.policy_sha256,
        ) != (self.policy_version, self.policy_sha256):
            raise ValueError("Risk result policy differs from candidate receipt")
        if self.target_id not in {item.target_id for item in self.risk_result.assessments}:
            raise ValueError("Risk result does not contain candidate target")
        _verify_model_digest(self, "receipt_sha256")
        return self


class ObligationReceiptBinding(TimedReceipt):
    target_id: str = Field(min_length=1)
    build_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    test_ids: tuple[str, ...] = Field(min_length=1)
    candidate_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_payload_size_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_receipt(self) -> ObligationReceiptBinding:
        _require_sorted_unique(self.test_ids, "obligation test IDs")
        _verify_model_digest(self, "receipt_sha256")
        return self


class ExecutionOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class ExecutionReceiptBinding(TimedReceipt):
    obligation_receipt_id: str = Field(min_length=1)
    test_id: str = Field(min_length=1)
    build_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment_id: str = Field(min_length=1, max_length=200)
    candidate_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: ExecutionOutcome
    result_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_payload_size_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_receipt(self) -> ExecutionReceiptBinding:
        _verify_model_digest(self, "receipt_sha256")
        return self


class CandidateStatement(ReleaseInputModel):
    statement_id: str = Field(min_length=1, max_length=200)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_size_bytes: int = Field(ge=1)


class ConflictSetBinding(TimedReceipt):
    project_id: str = Field(min_length=1, max_length=200)
    source_snapshot: str = Field(min_length=1, max_length=500)
    build_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propositions: tuple[CandidateStatement, ...]
    proposition_ids: tuple[str, ...]
    unresolved_conflict_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_receipt(self) -> ConflictSetBinding:
        _require_sorted_unique(self.proposition_ids, "conflict proposition IDs")
        _require_sorted_unique(self.unresolved_conflict_ids, "unresolved conflict IDs")
        if self.proposition_ids != tuple(item.statement_id for item in self.propositions):
            raise ValueError("Conflict proposition IDs differ from bound payloads")
        _verify_model_digest(self, "receipt_sha256")
        return self


class HumanConfirmationSetBinding(TimedReceipt):
    project_id: str = Field(min_length=1, max_length=200)
    source_snapshot: str = Field(min_length=1, max_length=500)
    build_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmations: tuple[CandidateStatement, ...]
    confirmation_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_receipt(self) -> HumanConfirmationSetBinding:
        _require_sorted_unique(self.confirmation_ids, "human confirmation IDs")
        if self.confirmation_ids != tuple(item.statement_id for item in self.confirmations):
            raise ValueError("Human confirmation IDs differ from bound payloads")
        _verify_model_digest(self, "receipt_sha256")
        return self


_PERMANENT_GAP_CODES = (
    "CANDIDATE_BUILD_NOT_VERIFIED",
    "CHANGE_SEED_SCOPE_NOT_ATTESTED",
    "CONFLICT_SCOPE_NOT_ATTESTED",
    "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
    "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
    "RISK_FACTORS_NOT_ATTESTED",
    "TEST_EXECUTION_SCOPE_NOT_ATTESTED",
    "TEST_OBLIGATION_SCOPE_NOT_ATTESTED",
    "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
    "VERIFIED_CHANGE_SET_MISSING",
)


class ImmutableReleaseInput(ReleaseInputModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    canonical_candidate_binding_complete: Literal[True] = True
    release_prerequisites_complete: Literal[False] = False
    release_interlock: Literal["RELEASE_EVIDENCE_MODEL_INCOMPLETE"] = (
        "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    )
    policy_id: str
    policy_version: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    valid_until: str
    freshness_seconds: int = Field(ge=1)
    candidate_analysis: CandidateAnalysisBinding
    change_build: ChangeBuildBinding
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str
    ontology_version: str
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str
    profile_version: str
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    complete_path_replay: CompleteGraphPathArtifact
    trusted_edge_verification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    risk_receipts: tuple[RiskReceiptBinding, ...] = Field(min_length=1)
    obligation_receipts: tuple[ObligationReceiptBinding, ...] = Field(min_length=1)
    execution_receipts: tuple[ExecutionReceiptBinding, ...] = Field(min_length=1)
    conflict_set: ConflictSetBinding
    human_confirmation_set: HumanConfirmationSetBinding
    policy_bindings: tuple[PolicyBinding, ...] = Field(min_length=1)
    blocking_gap_codes: tuple[str, ...] = Field(min_length=1)
    release_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> ImmutableReleaseInput:
        _parse_timestamp(self.evaluated_at)
        if _parse_timestamp(self.valid_until) <= _parse_timestamp(self.evaluated_at):
            raise ValueError("Release input must expire after evaluation")
        for items, label in (
            (self.risk_receipts, "risk receipts"),
            (self.obligation_receipts, "obligation receipts"),
            (self.execution_receipts, "execution receipts"),
        ):
            _require_sorted_unique(tuple(item.receipt_id for item in items), label)
        _require_sorted_unique(
            tuple(item.role for item in self.policy_bindings), "policy binding roles"
        )
        if self.blocking_gap_codes != tuple(sorted(set(self.blocking_gap_codes))):
            raise ValueError("Blocking gaps must be sorted and unique")
        if self.blocking_gap_codes != _PERMANENT_GAP_CODES:
            raise ValueError("Permanent release prerequisite gaps cannot be omitted")
        if self.trusted_edge_verification_sha256 != (
            self.complete_path_replay.trusted_edge_verification_sha256
        ):
            raise ValueError("Trusted-edge verification differs from complete path replay")
        if self.candidate_analysis.project_id != self.change_build.project_id:
            raise ValueError("Candidate request project differs from change/build scope")
        path = self.complete_path_replay
        if (
            self.change_build.project_id,
            self.change_build.source_snapshot,
            self.source_graph_sha256,
            self.normalized_graph_sha256,
            self.ontology_id,
            self.ontology_version,
            self.ontology_sha256,
            self.profile_id,
            self.profile_version,
            self.profile_sha256,
        ) != (
            path.project_id,
            path.source_snapshot,
            path.source_graph_sha256,
            path.normalized_graph_sha256,
            path.ontology_id,
            path.ontology_version,
            path.ontology_sha256,
            path.profile_id,
            path.profile_version,
            path.profile_sha256,
        ):
            raise ValueError("Release input graph roots differ from complete path replay")
        scope = (
            self.change_build.project_id,
            self.change_build.source_snapshot,
            self.change_build.build_artifact_sha256,
            _static_path_replay_sha256(self.complete_path_replay),
        )
        candidate_scope = candidate_scope_sha256(
            self.candidate_analysis, self.change_build, self.complete_path_replay
        )
        for aggregate in (self.conflict_set, self.human_confirmation_set):
            if (
                aggregate.project_id,
                aggregate.source_snapshot,
                aggregate.build_artifact_sha256,
                aggregate.required_scope_sha256,
            ) != scope:
                raise ValueError("Candidate aggregate differs from release-input scope")
            if aggregate.candidate_scope_sha256 != candidate_scope:
                raise ValueError("Candidate aggregate has a different candidate scope")
        if any(
            item.candidate_scope_sha256 != candidate_scope
            for item in (
                *self.risk_receipts,
                *self.obligation_receipts,
                *self.execution_receipts,
            )
        ):
            raise ValueError("Candidate receipt has a different candidate scope")
        _verify_model_digest(self, "release_input_sha256")
        return self


class ReleaseInputGapCode(StrEnum):
    RELEASE_EVIDENCE_MODEL_INCOMPLETE = "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED = "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"
    CHANGE_SEED_SCOPE_NOT_ATTESTED = "CHANGE_SEED_SCOPE_NOT_ATTESTED"
    VERIFIED_CHANGE_SET_MISSING = "VERIFIED_CHANGE_SET_MISSING"
    CANDIDATE_BUILD_NOT_VERIFIED = "CANDIDATE_BUILD_NOT_VERIFIED"
    RISK_FACTORS_NOT_ATTESTED = "RISK_FACTORS_NOT_ATTESTED"
    TEST_OBLIGATION_SCOPE_NOT_ATTESTED = "TEST_OBLIGATION_SCOPE_NOT_ATTESTED"
    TEST_EXECUTION_SCOPE_NOT_ATTESTED = "TEST_EXECUTION_SCOPE_NOT_ATTESTED"
    HUMAN_APPROVAL_SCOPE_NOT_ATTESTED = "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED"
    CONFLICT_SCOPE_NOT_ATTESTED = "CONFLICT_SCOPE_NOT_ATTESTED"
    PATH_REPLAY_INCOMPLETE = "PATH_REPLAY_INCOMPLETE"
    POLICY_ROOT_MISMATCH = "POLICY_ROOT_MISMATCH"
    TIME_AUTHORITY_UNAVAILABLE = "TIME_AUTHORITY_UNAVAILABLE"
    REQUIRED_PARTITION_EMPTY = "REQUIRED_PARTITION_EMPTY"
    DUPLICATE_RECEIPT_ID = "DUPLICATE_RECEIPT_ID"
    RECEIPT_MALFORMED = "RECEIPT_MALFORMED"
    RECEIPT_EXPIRED_OR_FUTURE = "RECEIPT_EXPIRED_OR_FUTURE"
    CHANGE_BUILD_MISMATCH = "CHANGE_BUILD_MISMATCH"
    MATERIAL_TARGET_COVERAGE_MISMATCH = "MATERIAL_TARGET_COVERAGE_MISMATCH"
    PATH_COVERAGE_MISMATCH = "PATH_COVERAGE_MISMATCH"
    TEST_EXECUTION_COVERAGE_MISMATCH = "TEST_EXECUTION_COVERAGE_MISMATCH"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    CANDIDATE_INPUT_MISMATCH = "CANDIDATE_INPUT_MISMATCH"
    RELEASE_INPUT_EXPIRED = "RELEASE_INPUT_EXPIRED"
    RELEASE_INPUT_FROM_FUTURE = "RELEASE_INPUT_FROM_FUTURE"
    RELEASE_INPUT_TAMPERED = "RELEASE_INPUT_TAMPERED"


class ReleaseInputGap(ReleaseInputModel):
    code: ReleaseInputGapCode
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["RELEASE_ONLY"] = "RELEASE_ONLY"
    blocking: Literal[True] = True


class ReleaseInputEvaluation(ReleaseInputModel):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    release_interlock: Literal["RELEASE_EVIDENCE_MODEL_INCOMPLETE"] = (
        "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    )
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    canonical_candidate_binding_complete: bool
    artifact: ImmutableReleaseInput | None = None
    gaps: tuple[ReleaseInputGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> ReleaseInputEvaluation:
        if self.canonical_candidate_binding_complete is not (self.artifact is not None):
            raise ValueError("Candidate binding completeness differs from artifact presence")
        keys = [(gap.code.value, gap.identity_sha256) for gap in self.gaps]
        if keys != sorted(set(keys)):
            raise ValueError("Release-input gaps must be sorted and unique")
        if ReleaseInputGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE not in {
            gap.code for gap in self.gaps
        }:
            raise ValueError("Global release interlock cannot be omitted")
        gap_values = {gap.code.value for gap in self.gaps}
        if not set(_PERMANENT_GAP_CODES).issubset(gap_values):
            raise ValueError("Permanent release prerequisite gaps cannot be omitted")
        _verify_model_digest(self, "evaluation_sha256")
        return self


DEFAULT_RELEASE_INPUT_POLICY_SHA256 = (
    "0ad61225242088f8225aaf07b23cb7edb2d56deaf365af7ebca04c9ba746c60b"
)

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WINDOWS_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must use semantic versioning")


def _require_identifier(value: str, field: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase stable identifier")


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def _require_safe_relative_locator(value: str) -> None:
    if (
        not value
        or len(value) > 500
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Artifact locator must be a safe repository-relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Artifact locator must be a safe repository-relative path")
    if any(
        part.endswith((".", " ")) or _WINDOWS_DEVICE.fullmatch(part)
        for part in path.parts
    ):
        raise ValueError("Artifact locator must be a safe repository-relative path")


def _require_bounded_text(value: str, label: str) -> None:
    if not value or len(value) > 500 or any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} must be bounded text without control characters")


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be canonical UTC seconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _verify_model_digest(model: BaseModel, field: str) -> None:
    body = model.model_dump(mode="json")
    declared = body.pop(field)
    if stable_sha256(body) != declared:
        raise ValueError(f"{field} does not match canonical content")


def stable_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal_receipt(model_type, body: dict[str, Any]):
    """Seal a candidate payload binding; this does not attest its authority."""

    return model_type.model_validate({**body, "receipt_sha256": stable_sha256(body)})


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseInputContractError("Release-input policy contains duplicate keys")
        result[key] = value
    return result


def load_release_input_policy(
    path: Path,
    *,
    expected_sha256: str = DEFAULT_RELEASE_INPUT_POLICY_SHA256,
) -> ReleaseInputPolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseInputContractError("Release-input policy cannot be loaded") from exc
    if not isinstance(raw, dict):
        raise ReleaseInputContractError("Release-input policy root must be an object")
    if raw.get("sha256") != contract_sha256(raw) or raw.get("sha256") != expected_sha256:
        raise ReleaseInputContractError("Release-input policy digest is not externally pinned")
    try:
        return ReleaseInputPolicy.model_validate(raw)
    except ValidationError as exc:
        raise ReleaseInputContractError("Release-input policy is structurally invalid") from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identity(*values: Any) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _gap(code: ReleaseInputGapCode, *values: Any) -> ReleaseInputGap:
    return ReleaseInputGap(code=code, identity_sha256=_identity(code.value, *values))


def _planned_gaps() -> list[ReleaseInputGap]:
    return [
        _gap(ReleaseInputGapCode.CANDIDATE_BUILD_NOT_VERIFIED),
        _gap(ReleaseInputGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.CONFLICT_SCOPE_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.HUMAN_APPROVAL_SCOPE_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE),
        _gap(ReleaseInputGapCode.RISK_FACTORS_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.TEST_OBLIGATION_SCOPE_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.TEST_EXECUTION_SCOPE_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED),
        _gap(ReleaseInputGapCode.VERIFIED_CHANGE_SET_MISSING),
    ]


def _evaluation(
    policy: ReleaseInputPolicy,
    evaluated_at: str,
    gaps: list[ReleaseInputGap],
    artifact: ImmutableReleaseInput | None,
) -> ReleaseInputEvaluation:
    ordered = tuple(sorted(set(gaps), key=lambda gap: (gap.code.value, gap.identity_sha256)))
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "release_interlock": "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
        "policy_sha256": policy.sha256,
        "evaluated_at": evaluated_at,
        "canonical_candidate_binding_complete": artifact is not None,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "gaps": [gap.model_dump(mode="json") for gap in ordered],
    }
    return ReleaseInputEvaluation.model_validate({**body, "evaluation_sha256": stable_sha256(body)})


def _receipt_valid(receipt: TimedReceipt, now: datetime, policy: ReleaseInputPolicy) -> bool:
    try:
        observed = _parse_timestamp(receipt.observed_at)
        valid_until = _parse_timestamp(receipt.valid_until)
    except ValueError:
        return False
    return bool(
        observed <= now + timedelta(seconds=policy.maximum_future_skew_seconds)
        and observed >= now - timedelta(seconds=policy.maximum_receipt_age_seconds)
        and valid_until > observed
        and valid_until > now
        and valid_until - observed <= timedelta(seconds=policy.maximum_binding_freshness_seconds)
    )


def _candidate_analysis_binding(run: AssuranceRun) -> CandidateAnalysisBinding:
    if run.schema_version != "2.0.0":
        raise ValueError("Candidate analysis must use the current graph-bound schema")
    partitions = (run.evidence, run.impacts, run.claims, run.selected_tests)
    if any(not partition for partition in partitions):
        raise ValueError("Candidate analysis has an empty required partition")
    changed_paths = [item.replace("\\", "/") for item in run.request.changed_paths]
    if len(changed_paths) != len(set(changed_paths)):
        raise ValueError("Candidate request contains duplicate changed paths")
    for locator in changed_paths:
        _require_safe_relative_locator(locator)

    def record_roots(items: list[BaseModel], key) -> list[str]:
        identities = [key(item) for item in items]
        if len(identities) != len(set(identities)):
            raise ValueError("Candidate analysis contains duplicate record identities")
        return sorted(stable_sha256(item.model_dump(mode="json")) for item in items)

    requirement_bytes = run.request.requirement.encode("utf-8")
    source_ref_bytes = run.request.source_ref.encode("utf-8")
    body = {
        "run_id": str(run.run_id),
        "trace_id": str(run.trace_id),
        "project_id": run.request.project_id,
        "change_intent": run.request.change_intent.value,
        "source_ref_sha256": hashlib.sha256(source_ref_bytes).hexdigest(),
        "source_ref_size_bytes": len(source_ref_bytes),
        "requirement_sha256": hashlib.sha256(requirement_bytes).hexdigest(),
        "requirement_size_bytes": len(requirement_bytes),
        "changed_path_sha256s": sorted(
            hashlib.sha256(item.encode("utf-8")).hexdigest() for item in changed_paths
        ),
        "evidence_record_sha256s": record_roots(run.evidence, lambda item: item.evidence_id),
        "impact_record_sha256s": record_roots(
            run.impacts, lambda item: (item.entity_id, item.relation, item.kind)
        ),
        "claim_record_sha256s": record_roots(run.claims, lambda item: item.claim_id),
        "analysis_gap_record_sha256s": record_roots(
            run.analysis_gaps,
            lambda item: (item.code, item.entity_id, item.relation, item.message),
        ),
        "selected_test_record_sha256s": record_roots(run.selected_tests, lambda item: item.test_id),
        "reasoning_policy_version": run.reasoning_policy_version,
        "reasoning_policy_sha256": run.reasoning_policy_sha256,
        "reasoning_eval_set_id": run.reasoning_eval_set_id,
        "reasoning_eval_set_sha256": run.reasoning_eval_set_sha256,
    }
    return CandidateAnalysisBinding.model_validate({**body, "binding_sha256": stable_sha256(body)})


def candidate_scope_sha256(
    candidate_analysis: CandidateAnalysisBinding,
    change_build: ChangeBuildBinding,
    path: CompleteGraphPathArtifact,
) -> str:
    """Bind every advisory candidate partition to one cross-receipt scope root."""

    return stable_sha256(
        {
            "candidate_analysis_sha256": candidate_analysis.binding_sha256,
            "change_build_sha256": change_build.binding_sha256,
            "static_path_replay_sha256": _static_path_replay_sha256(path),
        }
    )


def _static_path_replay_sha256(path: CompleteGraphPathArtifact) -> str:
    body = path.model_dump(mode="json")
    for field in (
        "artifact_sha256",
        "evaluated_at",
        "trusted_edge_verification_sha256",
        "propagation_result_sha256",
    ):
        body.pop(field)
    scope = body["required_scope"]
    for field in ("required_path_sha256s", "scope_sha256"):
        scope.pop(field)
    for path_body in body["paths"]:
        path_body.pop("path_sha256")
        for hop in path_body["hops"]:
            hop.pop("trusted_edge_evaluated_at")
            hop.pop("trusted_edge_verification_sha256")
    return stable_sha256(body)


def _static_propagation_path_identity(path: BaseModel) -> str:
    body = path.model_dump(mode="json")
    body.pop("path_sha256")
    for hop in body["hops"]:
        hop.pop("trusted_edge_evaluated_at")
        hop.pop("trusted_edge_verification_sha256")
    return stable_sha256(body)


def _static_risk_result(
    result: AnalysisRiskResult, path_identities: dict[str, str]
) -> dict[str, Any]:
    body = result.model_dump(mode="json")
    body.pop("result_sha256")
    body.pop("propagation_result_sha256")
    for assessment in body["assessments"]:
        for contribution in assessment["path_contributions"]:
            path_sha256 = contribution.pop("path_sha256")
            contribution["path_identity_sha256"] = path_identities.get(
                path_sha256, "UNBOUND_PATH"
            )
        assessment["path_contributions"] = sorted(
            assessment["path_contributions"], key=stable_sha256
        )
    return body


def compile_immutable_release_input(
    *,
    candidate_path_artifact: CompleteGraphPathArtifact,
    graph: NormalizedGraph,
    propagation_policy: PropagationPolicy,
    path_policy: CompletePathReplayPolicy,
    trusted_edge_replay: TrustedEdgeReplayInput,
    policy: ReleaseInputPolicy,
    candidate_run: AssuranceRun,
    project_id: str,
    source_snapshot: str,
    environment_id: str,
    change_artifact_id: str,
    change_artifact_content: bytes,
    build_artifact_id: str,
    build_artifact_content: bytes,
    risk_receipts: tuple[RiskReceiptBinding, ...],
    obligation_receipts: tuple[ObligationReceiptBinding, ...],
    execution_receipts: tuple[ExecutionReceiptBinding, ...],
    conflict_set: ConflictSetBinding | None,
    human_confirmation_set: HumanConfirmationSetBinding | None,
    policy_bindings: tuple[PolicyBinding, ...],
) -> ReleaseInputEvaluation:
    gaps = _planned_gaps()
    try:
        now = _utc_now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        now = now.astimezone(UTC).replace(microsecond=0)
    except (OSError, RuntimeError, ValueError):
        gaps.append(_gap(ReleaseInputGapCode.TIME_AUTHORITY_UNAVAILABLE))
        return _evaluation(policy, "unavailable", gaps, None)
    now_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    policy_body = policy.model_dump(mode="json", by_alias=True)
    if (
        contract_sha256(policy_body) != policy.sha256
        or policy.sha256 != DEFAULT_RELEASE_INPUT_POLICY_SHA256
        or (
            path_policy.policy_id,
            path_policy.policy_version,
            path_policy.sha256,
        )
        != (
            policy.path_replay_policy.policy_id,
            policy.path_replay_policy.policy_version,
            policy.path_replay_policy.policy_sha256,
        )
    ):
        gaps.append(_gap(ReleaseInputGapCode.POLICY_ROOT_MISMATCH))
        return _evaluation(policy, now_text, gaps, None)

    path_evaluation = verify_complete_graph_path_replay(
        candidate_path_artifact,
        graph,
        propagation_policy,
        path_policy,
        trusted_edge_replay,
    )
    path = path_evaluation.artifact
    if path is None:
        gaps.append(_gap(ReleaseInputGapCode.PATH_REPLAY_INCOMPLETE))
        return _evaluation(policy, now_text, gaps, None)
    for label, partition in (
        ("evidence", candidate_run.evidence),
        ("impact", candidate_run.impacts),
        ("claim", candidate_run.claims),
        ("selected-test", candidate_run.selected_tests),
    ):
        if not partition:
            gaps.append(_gap(ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY, label))
    if any(
        not partition
        for partition in (
            candidate_run.evidence,
            candidate_run.impacts,
            candidate_run.claims,
            candidate_run.selected_tests,
        )
    ):
        return _evaluation(policy, now_text, gaps, None)
    try:
        candidate_analysis = _candidate_analysis_binding(candidate_run)
    except (ValidationError, ValueError):
        gaps.append(_gap(ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH, "analysis"))
        return _evaluation(policy, now_text, gaps, None)
    if len(change_artifact_content) not in range(1, policy.maximum_artifact_bytes + 1) or len(
        build_artifact_content
    ) not in range(1, policy.maximum_artifact_bytes + 1):
        gaps.append(_gap(ReleaseInputGapCode.INPUT_TOO_LARGE))
        return _evaluation(policy, now_text, gaps, None)

    change_sha = hashlib.sha256(change_artifact_content).hexdigest()
    build_sha = hashlib.sha256(build_artifact_content).hexdigest()
    change_body = {
        "project_id": project_id,
        "source_snapshot": source_snapshot,
        "environment_id": environment_id,
        "change_artifact_id": change_artifact_id,
        "change_artifact_sha256": change_sha,
        "change_artifact_size_bytes": len(change_artifact_content),
        "build_artifact_id": build_artifact_id,
        "build_artifact_sha256": build_sha,
        "build_artifact_size_bytes": len(build_artifact_content),
    }
    try:
        change_build = ChangeBuildBinding.model_validate(
            {**change_body, "binding_sha256": stable_sha256(change_body)}
        )
    except ValidationError:
        gaps.append(_gap(ReleaseInputGapCode.CHANGE_BUILD_MISMATCH, "identity"))
        return _evaluation(policy, now_text, gaps, None)
    candidate_scope = candidate_scope_sha256(
        candidate_analysis, change_build, candidate_path_artifact
    )
    if (project_id, source_snapshot) != (path.project_id, path.source_snapshot):
        gaps.append(_gap(ReleaseInputGapCode.CHANGE_BUILD_MISMATCH))
    run_roots = (
        candidate_run.request.project_id,
        candidate_run.source_snapshot,
        candidate_run.source_graph_sha256,
        candidate_run.normalized_graph_sha256,
        candidate_run.ontology_id,
        candidate_run.ontology_version,
        candidate_run.ontology_sha256,
        candidate_run.source_profile_id,
        candidate_run.source_profile_version,
        candidate_run.source_profile_sha256,
    )
    path_roots = (
        path.project_id,
        path.source_snapshot,
        path.source_graph_sha256,
        path.normalized_graph_sha256,
        path.ontology_id,
        path.ontology_version,
        path.ontology_sha256,
        path.profile_id,
        path.profile_version,
        path.profile_sha256,
    )
    if run_roots != path_roots:
        gaps.append(_gap(ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH, "graph-roots"))
    expected_evidence_roots = {
        "project_id": project_id,
        "source_snapshot": source_snapshot,
        "source_graph_sha256": path.source_graph_sha256,
        "normalized_graph_sha256": path.normalized_graph_sha256,
        "ontology_id": path.ontology_id,
        "ontology_version": path.ontology_version,
        "ontology_sha256": path.ontology_sha256,
        "source_profile_id": path.profile_id,
        "source_profile_version": path.profile_version,
        "source_profile_sha256": path.profile_sha256,
    }
    for evidence in candidate_run.evidence:
        for key, expected_value in expected_evidence_roots.items():
            if key in evidence.attributes and evidence.attributes[key] != expected_value:
                gaps.append(
                    _gap(
                        ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH,
                        "evidence-root",
                        evidence.evidence_id,
                        key,
                    )
                )

    partitions: tuple[tuple[Any, ...], ...] = (
        risk_receipts,
        obligation_receipts,
        execution_receipts,
        policy_bindings,
    )
    for label, items in zip(("risk", "obligation", "execution", "policy"), partitions, strict=True):
        if not items:
            gaps.append(_gap(ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY, label))
    if conflict_set is None:
        gaps.append(_gap(ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY, "conflict"))
    if human_confirmation_set is None:
        gaps.append(_gap(ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY, "human"))
    if any(len(items) > policy.maximum_receipts_per_partition for items in partitions):
        gaps.append(_gap(ReleaseInputGapCode.INPUT_TOO_LARGE))
    all_receipts: tuple[TimedReceipt, ...] = (
        *risk_receipts,
        *obligation_receipts,
        *execution_receipts,
        *((conflict_set,) if conflict_set else ()),
        *((human_confirmation_set,) if human_confirmation_set else ()),
    )
    try:
        for receipt in all_receipts:
            type(receipt).model_validate(receipt.model_dump(mode="json"))
    except (ValidationError, AttributeError, TypeError, ValueError):
        gaps.append(_gap(ReleaseInputGapCode.RECEIPT_MALFORMED))
    if any(not _receipt_valid(receipt, now, policy) for receipt in all_receipts):
        gaps.append(_gap(ReleaseInputGapCode.RECEIPT_EXPIRED_OR_FUTURE))

    ids = [receipt.receipt_id for receipt in all_receipts]
    if len(ids) != len(set(ids)):
        gaps.append(_gap(ReleaseInputGapCode.DUPLICATE_RECEIPT_ID))
    bindings = {item.role: item for item in policy_bindings}
    if tuple(sorted(bindings)) != policy.required_policy_roles or len(bindings) != len(
        policy_bindings
    ):
        gaps.append(_gap(ReleaseInputGapCode.POLICY_ROOT_MISMATCH))
    expected_roots = {
        "canonical-ontology": (path.ontology_id, path.ontology_version, path.ontology_sha256),
        "source-profile": (path.profile_id, path.profile_version, path.profile_sha256),
        "propagation": (
            path.propagation_policy_id,
            path.propagation_policy_version,
            path.propagation_policy_sha256,
        ),
        "path-replay": (
            path.path_replay_policy_id,
            path.path_replay_policy_version,
            path.path_replay_policy_sha256,
        ),
        "trusted-edge": (
            path.trusted_edge_policy_id,
            path.trusted_edge_policy_version,
            path.trusted_edge_policy_sha256,
        ),
        "extractor-registry": (
            path.trusted_edge_registry_id,
            path.trusted_edge_registry_version,
            path.trusted_edge_registry_sha256,
        ),
        "analysis-risk": (
            policy.analysis_risk_policy.policy_id,
            policy.analysis_risk_policy.policy_version,
            policy.analysis_risk_policy.policy_sha256,
        ),
        **{
            item.role: (item.policy_id, item.policy_version, item.policy_sha256)
            for item in policy.pinned_producer_policies
        },
    }
    try:
        active_reasoning = ReasoningPolicy.load()
        active_governance = GovernancePolicy.load()
    except (OSError, RuntimeError, ValueError):
        gaps.append(_gap(ReleaseInputGapCode.POLICY_ROOT_MISMATCH, "runtime"))
    else:
        runtime_roots = {
            "reasoning": (
                "reasoning-policy",
                active_reasoning.schema_version,
                active_reasoning.policy_sha256,
            ),
            "reasoning-evaluation": (
                active_reasoning.retrieval_eval_set_id,
                "1.0.0",
                active_reasoning.retrieval_eval_set_sha256,
            ),
            "governance": (
                "governance-policy",
                active_governance.schema_version,
                active_governance.sha256,
            ),
        }
        for role, root in runtime_roots.items():
            if expected_roots.get(role) != root:
                gaps.append(_gap(ReleaseInputGapCode.POLICY_ROOT_MISMATCH, role))
    for role, root in expected_roots.items():
        item = bindings.get(role)
        if item is None or (item.policy_id, item.policy_version, item.policy_sha256) != root:
            gaps.append(_gap(ReleaseInputGapCode.POLICY_ROOT_MISMATCH, role))
    reasoning_binding = bindings.get("reasoning")
    eval_binding = bindings.get("reasoning-evaluation")
    if reasoning_binding is None or (
        candidate_analysis.reasoning_policy_version,
        candidate_analysis.reasoning_policy_sha256,
    ) != (reasoning_binding.policy_version, reasoning_binding.policy_sha256):
        gaps.append(_gap(ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH, "reasoning"))
    if eval_binding is None or (
        candidate_analysis.reasoning_eval_set_id,
        candidate_analysis.reasoning_eval_set_sha256,
    ) != (eval_binding.policy_id, eval_binding.policy_sha256):
        gaps.append(_gap(ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH, "evaluation"))

    material_targets = path.required_scope.material_output_ids
    if tuple(sorted(receipt.target_id for receipt in risk_receipts)) != material_targets:
        gaps.append(_gap(ReleaseInputGapCode.MATERIAL_TARGET_COVERAGE_MISMATCH, "risk"))
    if tuple(sorted(receipt.target_id for receipt in obligation_receipts)) != material_targets:
        gaps.append(_gap(ReleaseInputGapCode.MATERIAL_TARGET_COVERAGE_MISMATCH, "obligation"))
    path_hashes_by_target = {
        target: tuple(
            sorted(
                item.path_sha256
                for item in candidate_path_artifact.paths
                if item.target_id == target
            )
        )
        for target in material_targets
    }
    risk_result_roots = {item.risk_result.result_sha256 for item in risk_receipts}
    if len(risk_result_roots) != 1:
        gaps.append(_gap(ReleaseInputGapCode.PATH_COVERAGE_MISMATCH, "risk-result-root"))
    if risk_receipts:
        supplied_risk = risk_receipts[0].risk_result
        factors_by_target = {
            assessment.target_id: dict(assessment.factors)
            for assessment in supplied_risk.assessments
        }
        try:
            ontology = load_canonical_ontology(
                Path(__file__).resolve().parents[2]
                / "config"
                / "ontology"
                / "canonical-ontology.json"
            )
            risk_policy = load_analysis_risk_policy(
                Path(__file__).resolve().parents[2] / "config" / "analysis-risk-policy.json",
                ontology,
                propagation_policy,
                expected_sha256=policy.analysis_risk_policy.policy_sha256,
            )
            trusted_propagation = traverse_propagation(
                graph,
                path.required_scope.seed_ids,
                propagation_policy,
                trusted_edge_replay=replace(trusted_edge_replay, evaluated_at=now),
            )
            replayed_risk = evaluate_analysis_risk(
                trusted_propagation,
                factors_by_target,
                risk_policy,
                expected_graph=graph,
            )
        except (PolicyContractError, PropagationEvaluationError, ValidationError):
            gaps.append(_gap(ReleaseInputGapCode.PATH_COVERAGE_MISMATCH, "risk-replay"))
        else:
            candidate_path_identities = {
                item.path_sha256: _static_propagation_path_identity(item)
                for item in candidate_path_artifact.paths
            }
            current_path_identities = {
                item.path_sha256: _static_propagation_path_identity(item)
                for item in trusted_propagation.paths
            }
            if _static_risk_result(
                supplied_risk, candidate_path_identities
            ) != _static_risk_result(replayed_risk, current_path_identities):
                gaps.append(
                    _gap(ReleaseInputGapCode.PATH_COVERAGE_MISMATCH, "risk-replay")
                )
    for receipt in risk_receipts:
        assessments = {item.target_id: item for item in receipt.risk_result.assessments}
        assessment = assessments.get(receipt.target_id)
        contribution_paths = (
            tuple(sorted(item.path_sha256 for item in assessment.path_contributions))
            if assessment is not None
            else ()
        )
        if (
            receipt.build_artifact_sha256 != build_sha
            or receipt.candidate_scope_sha256 != candidate_scope
            or receipt.path_sha256s != path_hashes_by_target.get(receipt.target_id, ())
            or (receipt.policy_id, receipt.policy_version, receipt.policy_sha256)
            != (
                policy.analysis_risk_policy.policy_id,
                policy.analysis_risk_policy.policy_version,
                policy.analysis_risk_policy.policy_sha256,
            )
            or tuple(sorted(assessments)) != material_targets
            or contribution_paths != receipt.path_sha256s
            or receipt.risk_result.propagation_result_sha256
            != candidate_path_artifact.propagation_result_sha256
        ):
            gaps.append(_gap(ReleaseInputGapCode.PATH_COVERAGE_MISMATCH, receipt.receipt_id))
    obligation_by_id = {item.receipt_id: item for item in obligation_receipts}
    expected_tests = {
        (obligation.receipt_id, test_id)
        for obligation in obligation_receipts
        for test_id in obligation.test_ids
    }
    actual_tests = {(item.obligation_receipt_id, item.test_id) for item in execution_receipts}
    if expected_tests != actual_tests or len(actual_tests) != len(execution_receipts):
        gaps.append(_gap(ReleaseInputGapCode.TEST_EXECUTION_COVERAGE_MISMATCH))
    for obligation in obligation_receipts:
        if (
            obligation.build_artifact_sha256 != build_sha
            or obligation.candidate_scope_sha256 != candidate_scope
        ):
            gaps.append(_gap(ReleaseInputGapCode.CHANGE_BUILD_MISMATCH, obligation.receipt_id))
    for execution in execution_receipts:
        if (
            execution.build_artifact_sha256 != build_sha
            or execution.obligation_receipt_id not in obligation_by_id
            or execution.environment_id != environment_id
            or execution.candidate_scope_sha256 != candidate_scope
        ):
            gaps.append(_gap(ReleaseInputGapCode.CHANGE_BUILD_MISMATCH, execution.receipt_id))
    for role, receipt in (
        ("obligation", obligation_receipts[0] if obligation_receipts else None),
        ("execution", execution_receipts[0] if execution_receipts else None),
        ("conflict", conflict_set),
        ("human-confirmation", human_confirmation_set),
    ):
        binding = bindings.get(role)
        if (
            receipt
            and binding
            and (
                receipt.policy_id,
                receipt.policy_version,
                receipt.policy_sha256,
            )
            != (binding.policy_id, binding.policy_version, binding.policy_sha256)
        ):
            gaps.append(_gap(ReleaseInputGapCode.POLICY_ROOT_MISMATCH, role))
    scope_tuple = (
        project_id,
        source_snapshot,
        build_sha,
        _static_path_replay_sha256(path),
    )
    for label, aggregate in (
        ("conflict", conflict_set),
        ("human", human_confirmation_set),
    ):
        if (
            aggregate is not None
            and (
                aggregate.project_id,
                aggregate.source_snapshot,
                aggregate.build_artifact_sha256,
                aggregate.required_scope_sha256,
            )
            != scope_tuple
        ):
            gaps.append(_gap(ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH, label))
        if aggregate is not None and aggregate.candidate_scope_sha256 != candidate_scope:
            gaps.append(_gap(ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH, label, "scope"))
    if len(gaps) > len(_planned_gaps()):
        return _evaluation(policy, now_text, gaps, None)

    assert conflict_set is not None and human_confirmation_set is not None
    expiry = min(
        [
            now + timedelta(seconds=policy.maximum_binding_freshness_seconds),
            *(_parse_timestamp(receipt.valid_until) for receipt in all_receipts),
            *(
                _parse_timestamp(hop.trusted_edge_valid_until or "")
                for path_receipt in path.paths
                for hop in path_receipt.hops
            ),
        ]
    )
    artifact_body = {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "canonical_candidate_binding_complete": True,
        "release_prerequisites_complete": False,
        "release_interlock": "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_sha256": policy.sha256,
        "evaluated_at": now_text,
        "valid_until": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "freshness_seconds": int((expiry - now).total_seconds()),
        "candidate_analysis": candidate_analysis.model_dump(mode="json"),
        "change_build": change_build.model_dump(mode="json"),
        "source_graph_sha256": path.source_graph_sha256,
        "normalized_graph_sha256": path.normalized_graph_sha256,
        "ontology_id": path.ontology_id,
        "ontology_version": path.ontology_version,
        "ontology_sha256": path.ontology_sha256,
        "profile_id": path.profile_id,
        "profile_version": path.profile_version,
        "profile_sha256": path.profile_sha256,
        "complete_path_replay": path.model_dump(mode="json"),
        "trusted_edge_verification_sha256": path.trusted_edge_verification_sha256,
        "risk_receipts": [
            item.model_dump(mode="json")
            for item in sorted(risk_receipts, key=lambda item: item.receipt_id)
        ],
        "obligation_receipts": [
            item.model_dump(mode="json")
            for item in sorted(obligation_receipts, key=lambda item: item.receipt_id)
        ],
        "execution_receipts": [
            item.model_dump(mode="json")
            for item in sorted(execution_receipts, key=lambda item: item.receipt_id)
        ],
        "conflict_set": conflict_set.model_dump(mode="json"),
        "human_confirmation_set": human_confirmation_set.model_dump(mode="json"),
        "policy_bindings": [
            item.model_dump(mode="json")
            for item in sorted(policy_bindings, key=lambda item: item.role)
        ],
        "blocking_gap_codes": sorted(gap.code.value for gap in _planned_gaps()),
    }
    if len(stable_json_bytes(artifact_body)) > policy.maximum_manifest_bytes:
        gaps.append(_gap(ReleaseInputGapCode.INPUT_TOO_LARGE, "manifest"))
        return _evaluation(policy, now_text, gaps, None)
    artifact = ImmutableReleaseInput.model_validate(
        {**artifact_body, "release_input_sha256": stable_sha256(artifact_body)}
    )
    return _evaluation(policy, now_text, gaps, artifact)


def verify_immutable_release_input(
    candidate: ImmutableReleaseInput,
    **compile_inputs: Any,
) -> ReleaseInputEvaluation:
    expected = compile_immutable_release_input(**compile_inputs)
    try:
        validated = ImmutableReleaseInput.model_validate(candidate.model_dump(mode="json"))
    except (ValidationError, AttributeError, TypeError, ValueError):
        validated = None
    policy: ReleaseInputPolicy = compile_inputs["policy"]
    try:
        expected_time = _parse_timestamp(expected.evaluated_at)
    except ValueError:
        return expected
    if validated is not None:
        candidate_time = _parse_timestamp(validated.evaluated_at)
        if candidate_time > expected_time + timedelta(seconds=policy.maximum_future_skew_seconds):
            return _evaluation(
                policy,
                expected.evaluated_at,
                [
                    *expected.gaps,
                    _gap(ReleaseInputGapCode.RELEASE_INPUT_FROM_FUTURE),
                ],
                None,
            )
        if _parse_timestamp(validated.valid_until) <= expected_time:
            return _evaluation(
                policy,
                expected.evaluated_at,
                [*expected.gaps, _gap(ReleaseInputGapCode.RELEASE_INPUT_EXPIRED)],
                None,
            )
    if expected.artifact is None:
        return expected
    if validated is None or _static_artifact(validated) != _static_artifact(expected.artifact):
        return _evaluation(
            policy,
            expected.evaluated_at,
            [*expected.gaps, _gap(ReleaseInputGapCode.RELEASE_INPUT_TAMPERED)],
            None,
        )
    return expected


def _static_artifact(artifact: ImmutableReleaseInput) -> dict[str, Any]:
    """Return all candidate semantics, excluding only refresh-derived fields."""

    body = artifact.model_dump(mode="json")
    for field in ("evaluated_at", "valid_until", "freshness_seconds", "release_input_sha256"):
        body.pop(field)
    replay = body["complete_path_replay"]
    for field in (
        "artifact_sha256",
        "evaluated_at",
        "trusted_edge_verification_sha256",
        "propagation_result_sha256",
    ):
        replay.pop(field)
    scope = replay["required_scope"]
    for field in ("required_path_sha256s", "scope_sha256"):
        scope.pop(field)
    for path in replay["paths"]:
        path.pop("path_sha256")
        for hop in path["hops"]:
            hop.pop("trusted_edge_evaluated_at")
            hop.pop("trusted_edge_verification_sha256")
    body.pop("trusted_edge_verification_sha256")
    return body
