from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from neo_sf_q_intel.context_pack import GraphContextPack
from neo_sf_q_intel.ontology import contract_sha256


class FusionContractError(RuntimeError):
    """Raised when the fusion policy is malformed or differs from its trusted root."""


class FusionError(RuntimeError):
    """Raised when candidate fusion cannot preserve its trust boundary."""


class FusionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FusionPosture(StrEnum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"


class CandidateState(StrEnum):
    CANDIDATE = "CANDIDATE"


class FusionModality(StrEnum):
    EXACT = "EXACT"
    LEXICAL = "LEXICAL"
    GRAPH = "GRAPH"
    SEMANTIC = "SEMANTIC"


class ModalityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    INVALID = "INVALID"
    TRUNCATED = "TRUNCATED"


class FusionLimits(FusionModel):
    maximum_input_candidates: int = Field(alias="maximumInputCandidates", ge=1)
    maximum_input_candidates_per_modality: int = Field(
        alias="maximumInputCandidatesPerModality", ge=1
    )
    maximum_input_bytes: int = Field(alias="maximumInputBytes", ge=1024)
    maximum_output_candidates: int = Field(alias="maximumOutputCandidates", ge=1)
    maximum_evidence_ids_per_candidate: int = Field(
        alias="maximumEvidenceIdsPerCandidate", ge=1
    )
    maximum_identifier_characters: int = Field(
        alias="maximumIdentifierCharacters", ge=16, le=1024
    )
    maximum_reason_characters: int = Field(alias="maximumReasonCharacters", ge=16, le=4096)
    maximum_omission_records: int = Field(alias="maximumOmissionRecords", ge=1)
    maximum_result_bytes: int = Field(alias="maximumResultBytes", ge=4096)


class FusionPolicy(FusionModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    posture: FusionPosture
    evaluation_set_id: str = Field(alias="evaluationSetId", min_length=1)
    evaluation_set_path: str = Field(alias="evaluationSetPath", min_length=1, max_length=1024)
    evaluation_set_sha256: str = Field(
        alias="evaluationSetSha256", pattern=r"^[a-f0-9]{64}$"
    )
    rank_method: Literal["WEIGHTED_RECIPROCAL_RANK"] = Field(alias="rankMethod")
    score_normalization: Literal["CHANNEL_NORMALIZED_UNIT_INTERVAL"] = Field(
        alias="scoreNormalization"
    )
    tie_break: tuple[
        Literal[
            "FUSED_SCORE_DESC",
            "ENTITY_ID",
            "ASSERTION_KEY",
            "ASSERTION_VALUE",
            "CANDIDATE_ID",
        ],
        ...,
    ] = Field(alias="tieBreak", min_length=5, max_length=5)
    degraded_profile: Literal["AVAILABLE_CHANNELS_ONLY_WITH_EXPLICIT_GAPS"] = Field(
        alias="degradedProfile"
    )
    supported_modalities: tuple[FusionModality, ...] = Field(
        alias="supportedModalities", min_length=1
    )
    protected_modalities: tuple[FusionModality, ...] = Field(
        alias="protectedModalities", min_length=1
    )
    reciprocal_rank_constant: int = Field(alias="reciprocalRankConstant", ge=1)
    score_decimal_places: int = Field(alias="scoreDecimalPlaces", ge=1, le=12)
    modality_weights: dict[FusionModality, int] = Field(alias="modalityWeights")
    limits: FusionLimits

    @model_validator(mode="after")
    def validate_policy(self) -> FusionPolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.policy_version, "policyVersion")
        if not _IDENTIFIER.fullmatch(self.policy_id):
            raise ValueError("policyId must be a lowercase stable identifier")
        if self.posture is not FusionPosture.ANALYSIS_ONLY:
            raise ValueError("Fusion must remain analysis-only")
        if not _IDENTIFIER.fullmatch(self.evaluation_set_id):
            raise ValueError("evaluationSetId must be a lowercase stable identifier")
        _require_relative_locator(self.evaluation_set_path)
        if self.tie_break != (
            "FUSED_SCORE_DESC",
            "ENTITY_ID",
            "ASSERTION_KEY",
            "ASSERTION_VALUE",
            "CANDIDATE_ID",
        ):
            raise ValueError("tieBreak must preserve the canonical deterministic ordering")
        if len(self.supported_modalities) != len(set(self.supported_modalities)):
            raise ValueError("supportedModalities must not contain duplicates")
        if (
            len(self.protected_modalities) != len(set(self.protected_modalities))
            or not set(self.protected_modalities).issubset(self.supported_modalities)
            or set(self.protected_modalities) != {
                FusionModality.EXACT,
                FusionModality.GRAPH,
            }
        ):
            raise ValueError("protectedModalities must be the exact and confirmed-graph tiers")
        if set(self.modality_weights) != set(self.supported_modalities):
            raise ValueError("modalityWeights must exactly cover supportedModalities")
        if any(weight <= 0 for weight in self.modality_weights.values()):
            raise ValueError("modalityWeights must be positive integers")
        if self.limits.maximum_omission_records < self.limits.maximum_input_candidates:
            raise ValueError("maximumOmissionRecords must cover every possible input candidate")
        return self


class FusionEvaluationContract(FusionModel):
    schema_version: str = Field(alias="schemaVersion")
    evaluation_set_id: str = Field(alias="evaluationSetId", min_length=1)
    evaluation_set_version: str = Field(alias="evaluationSetVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_policy_id: str = Field(alias="targetPolicyId", min_length=1)
    target_policy_version: str = Field(alias="targetPolicyVersion")
    quality_sample_status: Literal["INSUFFICIENT_ADJUDICATED_CASES"] = Field(
        alias="qualitySampleStatus"
    )
    adjudicated_case_count: int = Field(alias="adjudicatedCaseCount", ge=0)
    minimum_adjudicated_case_count: int = Field(
        alias="minimumAdjudicatedCaseCount", ge=1
    )
    required_cases: tuple[str, ...] = Field(alias="requiredCases", min_length=1)

    @model_validator(mode="after")
    def validate_evaluation_contract(self) -> FusionEvaluationContract:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.evaluation_set_version, "evaluationSetVersion")
        _require_semver(self.target_policy_version, "targetPolicyVersion")
        if not _IDENTIFIER.fullmatch(self.evaluation_set_id):
            raise ValueError("evaluationSetId must be a lowercase stable identifier")
        if not _IDENTIFIER.fullmatch(self.target_policy_id):
            raise ValueError("targetPolicyId must be a lowercase stable identifier")
        if tuple(sorted(set(self.required_cases))) != self.required_cases:
            raise ValueError("requiredCases must be sorted and unique")
        if self.adjudicated_case_count >= self.minimum_adjudicated_case_count:
            raise ValueError("Insufficient quality-sample status contradicts its case counts")
        return self


class ModalityAvailability(FusionModel):
    modality: FusionModality
    status: ModalityStatus
    reason_code: str | None = Field(default=None, max_length=256)
    detail: None = None

    @model_validator(mode="after")
    def validate_reason(self) -> ModalityAvailability:
        if self.status is ModalityStatus.AVAILABLE and self.reason_code is not None:
            raise ValueError("Available modalities must not carry degradation reasons")
        if self.status is not ModalityStatus.AVAILABLE and not self.reason_code:
            raise ValueError("Unavailable or degraded modalities require a reason code")
        if self.reason_code is not None and not _GAP_CODE.fullmatch(self.reason_code):
            raise ValueError("reason_code must be a stable uppercase code")
        return self


class FusionChannelRoot(FusionModel):
    modality: FusionModality
    producer_id: str = Field(min_length=1, max_length=1024)
    producer_version: str = Field(min_length=1, max_length=1024)
    corpus_id: str = Field(min_length=1, max_length=1024)
    corpus_version: str = Field(min_length=1, max_length=1024)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_id: str = Field(min_length=1, max_length=1024)
    index_version: str = Field(min_length=1, max_length=1024)
    index_build_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    built_at: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)
    analyzer_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    path_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    provider_kind: str | None = Field(default=None, max_length=1024)
    embedding_model_id: str | None = Field(default=None, max_length=1024)
    embedding_deployment_id: str | None = Field(default=None, max_length=1024)
    embedding_version: str | None = Field(default=None, max_length=1024)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=1_000_000)
    vector_normalization: Literal["L2", "NONE"] | None = None
    vector_distance: Literal["COSINE", "DOT_PRODUCT", "EUCLIDEAN"] | None = None
    chunking_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_channel_shape(self) -> FusionChannelRoot:
        _validate_modality_shape(self)
        if _parse_timestamp(self.built_at, "built_at") >= _parse_timestamp(
            self.expires_at, "expires_at"
        ):
            raise ValueError("Channel root must expire after its build time")
        return self


class FusionCandidate(FusionModel):
    candidate_id: str = Field(min_length=1, max_length=1024)
    entity_id: str = Field(min_length=1, max_length=1024)
    assertion_key: str = Field(min_length=1, max_length=1024)
    assertion_value: str = Field(min_length=1, max_length=1024)
    modality: FusionModality
    normalized_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_id: str = Field(min_length=1, max_length=1024)
    source_snapshot: str = Field(min_length=1, max_length=1024)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_pack_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str = Field(min_length=1, max_length=1024)
    ontology_version: str = Field(min_length=1, max_length=1024)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1, max_length=1024)
    profile_version: str = Field(min_length=1, max_length=1024)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    producer_id: str = Field(min_length=1, max_length=1024)
    producer_version: str = Field(min_length=1, max_length=1024)
    corpus_id: str = Field(min_length=1, max_length=1024)
    corpus_version: str = Field(min_length=1, max_length=1024)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_id: str = Field(min_length=1, max_length=1024)
    index_version: str = Field(min_length=1, max_length=1024)
    index_build_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    built_at: str = Field(min_length=1, max_length=64)
    evaluated_at: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)
    analyzer_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    exact_match_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    path_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    graph_path_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    provider_kind: str | None = Field(default=None, max_length=1024)
    embedding_model_id: str | None = Field(default=None, max_length=1024)
    embedding_deployment_id: str | None = Field(default=None, max_length=1024)
    embedding_version: str | None = Field(default=None, max_length=1024)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=1_000_000)
    vector_normalization: Literal["L2", "NONE"] | None = None
    vector_distance: Literal["COSINE", "DOT_PRODUCT", "EUCLIDEAN"] | None = None
    chunking_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    relationship_state: Literal[CandidateState.CANDIDATE] = CandidateState.CANDIDATE
    may_authorize: Literal[False] = False

    @field_validator("normalized_score")
    @classmethod
    def score_must_be_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("normalized_score must be finite")
        return value

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> FusionCandidate:
        if tuple(sorted(set(self.evidence_ids))) != self.evidence_ids:
            raise ValueError("evidence_ids must be sorted and unique")
        _validate_modality_shape(self)
        if self.modality is FusionModality.EXACT:
            exact_body = {
                "query_sha256": self.query_sha256,
                "entity_id": self.entity_id,
                "assertion_key": self.assertion_key,
                "assertion_value": self.assertion_value,
                "evidence_ids": self.evidence_ids,
                "analyzer_policy_sha256": self.analyzer_policy_sha256,
                "index_build_sha256": self.index_build_sha256,
            }
            if self.exact_match_sha256 != _stable_hash(exact_body):
                raise ValueError("Exact-match receipt digest is invalid")
        elif self.exact_match_sha256 is not None:
            raise ValueError("Only exact-match receipts may carry exact_match_sha256")
        built = _parse_timestamp(self.built_at, "built_at")
        evaluated = _parse_timestamp(self.evaluated_at, "evaluated_at")
        expires = _parse_timestamp(self.expires_at, "expires_at")
        if built > evaluated:
            raise ValueError("Candidate index build is in the future")
        if expires < evaluated:
            raise ValueError("Candidate index receipt is stale or expired")
        return self


class FusionContribution(FusionModel):
    modality: FusionModality
    rank: int = Field(ge=1)
    quantized_input_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    weighted_reciprocal_rank: Decimal = Field(gt=Decimal("0"))
    source_candidate_ids: tuple[str, ...] = Field(min_length=1)


class FusedCandidate(FusionModel):
    fused_candidate_id: str = Field(min_length=1, max_length=256)
    entity_id: str = Field(min_length=1, max_length=1024)
    assertion_key: str = Field(min_length=1, max_length=1024)
    assertion_value: str = Field(min_length=1, max_length=1024)
    fused_score: Decimal = Field(gt=Decimal("0"))
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    contributions: tuple[FusionContribution, ...] = Field(min_length=1)
    conflict_set_id: str | None = Field(default=None, max_length=256)
    has_conflict: bool
    project_id: str = Field(min_length=1, max_length=1024)
    source_snapshot: str = Field(min_length=1, max_length=1024)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_pack_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str = Field(min_length=1, max_length=1024)
    ontology_version: str = Field(min_length=1, max_length=1024)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1, max_length=1024)
    profile_version: str = Field(min_length=1, max_length=1024)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    relationship_state: Literal[CandidateState.CANDIDATE] = CandidateState.CANDIDATE
    may_authorize: Literal[False] = False
    may_satisfy_release_evidence: Literal[False] = False
    authority_eligible: Literal[False] = False

    @model_validator(mode="after")
    def validate_candidate(self) -> FusedCandidate:
        if self.has_conflict != (self.conflict_set_id is not None):
            raise ValueError("Conflict flag and conflict-set identity disagree")
        if tuple(sorted(set(self.evidence_ids))) != self.evidence_ids:
            raise ValueError("Fused evidence IDs must be sorted and unique")
        modalities = tuple(item.modality for item in self.contributions)
        if modalities != tuple(sorted(set(modalities), key=lambda item: item.value)):
            raise ValueError("Contributions must be ordered and unique by modality")
        return self


class FusionOmission(FusionModel):
    fused_candidate_id: str = Field(min_length=1, max_length=256)
    reason_code: Literal["OUTPUT_CANDIDATE_LIMIT", "RESULT_BYTE_LIMIT"]
    conflict_set_id: str | None = Field(default=None, max_length=256)


class FusionGap(FusionModel):
    code: str = Field(min_length=1, max_length=256)
    modality: FusionModality | None = None
    detail: str | None = Field(default=None, max_length=4096)
    blocking: bool


class FusionBudget(FusionModel):
    input_candidate_limit: int
    input_candidate_count: int
    input_candidates_by_modality: dict[FusionModality, int]
    input_candidates_per_modality_limit: int
    input_bytes_limit: int
    input_bytes: int
    output_candidate_limit: int
    output_candidates_available: int
    output_candidates_selected: int
    output_candidates_omitted: int
    evidence_ids_per_candidate_limit: int
    identifier_character_limit: int
    reason_character_limit: int
    omission_record_limit: int
    result_byte_limit: int
    result_bytes: int


class CandidateFusionResult(FusionModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    posture: Literal[FusionPosture.ANALYSIS_ONLY] = FusionPosture.ANALYSIS_ONLY
    relationship_state: Literal[CandidateState.CANDIDATE] = CandidateState.CANDIDATE
    may_authorize: Literal[False] = False
    may_satisfy_release_evidence: Literal[False] = False
    authority_eligible: Literal[False] = False
    analysis_complete: bool
    fusion_complete: bool
    upstream_context_complete: bool
    degraded: bool
    project_id: str = Field(min_length=1, max_length=1024)
    source_snapshot: str = Field(min_length=1, max_length=1024)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_pack_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str = Field(min_length=1, max_length=1024)
    ontology_version: str = Field(min_length=1, max_length=1024)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1, max_length=1024)
    profile_version: str = Field(min_length=1, max_length=1024)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieval_eval_set_id: str = Field(min_length=1, max_length=1024)
    retrieval_eval_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    compiler_policy_id: str = Field(min_length=1, max_length=1024)
    compiler_policy_version: str = Field(min_length=1, max_length=1024)
    compiler_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fusion_policy_id: str = Field(min_length=1, max_length=1024)
    fusion_policy_version: str = Field(min_length=1, max_length=1024)
    fusion_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fusion_evaluation_set_id: str = Field(min_length=1, max_length=1024)
    fusion_evaluation_set_path: str = Field(min_length=1, max_length=1024)
    fusion_evaluation_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel_roots_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str = Field(min_length=1, max_length=64)
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fusion_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    modality_availability: tuple[ModalityAvailability, ...]
    candidates: tuple[FusedCandidate, ...]
    omissions: tuple[FusionOmission, ...]
    gaps: tuple[FusionGap, ...]
    budget: FusionBudget
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_result(self) -> CandidateFusionResult:
        _require_relative_locator(self.fusion_evaluation_set_path)
        _parse_timestamp(self.evaluated_at, "evaluated_at")
        if self.analysis_complete != (not any(gap.blocking for gap in self.gaps)):
            raise ValueError("analysis_complete disagrees with blocking gaps")
        upstream_gap = any(gap.code == "UPSTREAM_CONTEXT_INCOMPLETE" for gap in self.gaps)
        if self.upstream_context_complete == upstream_gap:
            raise ValueError("upstream_context_complete disagrees with its blocking gap")
        non_upstream_blocking = any(
            gap.blocking and gap.code != "UPSTREAM_CONTEXT_INCOMPLETE" for gap in self.gaps
        )
        if self.fusion_complete == non_upstream_blocking:
            raise ValueError("fusion_complete disagrees with fusion-local blocking gaps")
        if self.degraded != any(
            item.status is not ModalityStatus.AVAILABLE for item in self.modality_availability
        ):
            raise ValueError("degraded disagrees with modality availability")
        modalities = tuple(item.modality for item in self.modality_availability)
        if modalities != tuple(sorted(set(modalities), key=lambda item: item.value)):
            raise ValueError("Modality availability must be ordered and unique")
        expected_identity = (
            self.project_id,
            self.source_snapshot,
            self.source_graph_sha256,
            self.normalized_graph_sha256,
            self.context_pack_sha256,
            self.ontology_id,
            self.ontology_version,
            self.ontology_sha256,
            self.profile_id,
            self.profile_version,
            self.profile_sha256,
        )
        if any(
            (
                item.project_id,
                item.source_snapshot,
                item.source_graph_sha256,
                item.normalized_graph_sha256,
                item.context_pack_sha256,
                item.ontology_id,
                item.ontology_version,
                item.ontology_sha256,
                item.profile_id,
                item.profile_version,
                item.profile_sha256,
            )
            != expected_identity
            or item.relationship_state is not CandidateState.CANDIDATE
            or item.may_authorize
            or item.may_satisfy_release_evidence
            or item.authority_eligible
            for item in self.candidates
        ):
            raise ValueError("Fused candidate identity or authority posture is invalid")
        selected_ids = {item.fused_candidate_id for item in self.candidates}
        omitted_ids = {item.fused_candidate_id for item in self.omissions}
        if len(selected_ids) != len(self.candidates) or len(omitted_ids) != len(self.omissions):
            raise ValueError("Selected and omitted candidate identities must be unique")
        if selected_ids & omitted_ids:
            raise ValueError("Selected and omitted candidate identities must be disjoint")
        budget = self.budget
        if (
            budget.output_candidates_available
            != budget.output_candidates_selected + budget.output_candidates_omitted
            or budget.output_candidates_selected != len(self.candidates)
            or budget.output_candidates_omitted != len(self.omissions)
            or budget.input_candidate_count > budget.input_candidate_limit
            or sum(budget.input_candidates_by_modality.values())
            != budget.input_candidate_count
            or set(budget.input_candidates_by_modality) != set(modalities)
            or budget.input_bytes > budget.input_bytes_limit
            or len(self.candidates) > budget.output_candidate_limit
            or len(self.omissions) > budget.omission_record_limit
        ):
            raise ValueError("Fusion budget accounting is inconsistent")
        _require_result_field_limits(self)
        body = self.model_dump(mode="json")
        declared = body.pop("result_sha256")
        envelope = {**body, "result_sha256": "0" * 64}
        if len(_canonical_json(envelope)) != budget.result_bytes:
            raise ValueError("Fusion result byte accounting is inconsistent")
        if budget.result_bytes > budget.result_byte_limit:
            raise ValueError("Fusion result exceeds its byte limit")
        if _stable_hash(body) != declared:
            raise ValueError("Fusion result digest does not match its content")
        return self


DEFAULT_FUSION_POLICY_SHA256 = (
    "9e525d71006be7eb1ac354ddd11077f485c48a09045135a2699831a064808fa3"
)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_GAP_CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _require_relative_locator(locator: str) -> None:
    normalized = locator.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(locator).is_absolute()
        or any(part in ("", ".", "..") for part in normalized.split("/"))
    ):
        raise ValueError("evaluationSetPath must be a normalized repository-relative path")


def _validate_modality_shape(item: Any) -> None:
    semantic_fields = (
        item.provider_kind,
        item.embedding_model_id,
        item.embedding_deployment_id,
        item.embedding_version,
        item.embedding_dimensions,
        item.vector_normalization,
        item.vector_distance,
        item.chunking_policy_sha256,
    )
    if item.modality in (FusionModality.EXACT, FusionModality.LEXICAL):
        if item.analyzer_policy_sha256 is None or item.path_policy_sha256 is not None:
            raise ValueError("Exact and lexical receipts require only analyzer policy provenance")
        if any(value is not None for value in semantic_fields):
            raise ValueError("Exact/lexical receipts cannot carry semantic provider provenance")
    elif item.modality is FusionModality.GRAPH:
        if item.path_policy_sha256 is None or item.analyzer_policy_sha256 is not None:
            raise ValueError("Graph receipts require only path-policy provenance")
        if any(value is not None for value in semantic_fields):
            raise ValueError("Graph receipts cannot carry semantic provider provenance")
        if hasattr(item, "graph_path_sha256") and item.graph_path_sha256 is None:
            raise ValueError("Graph candidate receipts require a path receipt digest")
    elif item.modality is FusionModality.SEMANTIC:
        if item.analyzer_policy_sha256 is not None or item.path_policy_sha256 is not None:
            raise ValueError("Semantic receipts cannot carry lexical or graph policy provenance")
        if any(value is None for value in semantic_fields):
            raise ValueError("Semantic receipts require complete embedding and index provenance")


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must be semantic version x.y.z")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FusionContractError(f"Duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def load_fusion_policy(
    path: Path, *, expected_sha256: str = DEFAULT_FUSION_POLICY_SHA256
) -> FusionPolicy:
    if not path.is_file():
        raise FusionContractError(f"Required policy is missing: {path.name}")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except FusionContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FusionContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(document, dict):
        raise FusionContractError(f"Expected a JSON object in {path.name}")
    actual = contract_sha256(document)
    if document.get("sha256") != actual:
        raise FusionContractError(f"{path.name} digest does not match its content")
    if actual != expected_sha256.casefold():
        raise FusionContractError(f"{path.name} does not match its trusted digest")
    try:
        return FusionPolicy.model_validate(document)
    except ValidationError as exc:
        raise FusionContractError(f"Invalid fusion policy: {exc}") from exc


def load_fusion_evaluation_contract(
    path: Path, *, expected_sha256: str
) -> FusionEvaluationContract:
    if not path.is_file():
        raise FusionContractError(f"Required evaluation contract is missing: {path.name}")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except FusionContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FusionContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(document, dict):
        raise FusionContractError(f"Expected a JSON object in {path.name}")
    actual = contract_sha256(document)
    if document.get("sha256") != actual:
        raise FusionContractError(f"{path.name} digest does not match its content")
    if actual != expected_sha256.casefold():
        raise FusionContractError(f"{path.name} does not match its trusted digest")
    try:
        return FusionEvaluationContract.model_validate(document)
    except ValidationError as exc:
        raise FusionContractError(f"Invalid fusion evaluation contract: {exc}") from exc


def _require_pack_integrity(pack: GraphContextPack, expected_context_pack_sha256: str) -> None:
    try:
        validated = GraphContextPack.model_validate(pack.model_dump(mode="json"))
    except ValidationError as exc:
        raise FusionError("Context pack integrity validation failed") from exc
    if validated != pack or pack.context_pack_sha256 != expected_context_pack_sha256:
        raise FusionError("Context pack differs from its trusted replay root")


def _visible_context(pack: GraphContextPack) -> tuple[set[str], dict[str, set[str]], set[str]]:
    entity_ids = set(pack.seed_ids)
    evidence_to_entities: dict[str, set[str]] = defaultdict(set)
    path_ids: set[str] = set()
    for path in pack.confirmed_structure_paths:
        path_entities = {path.seed_id, path.target_id}
        path_entities.update(
            entity
            for hop in path.hops
            for entity in (hop.traversal_from_id, hop.traversal_to_id)
        )
        entity_ids.update(path_entities)
        evidence_to_entities[path.path_sha256].update(path_entities)
        path_ids.add(path.path_sha256)
        for hop in path.hops:
            entity_ids.update((hop.traversal_from_id, hop.traversal_to_id))
            evidence_to_entities[hop.edge_id].update(
                (hop.traversal_from_id, hop.traversal_to_id)
            )
    for item in pack.unresolved_fragments:
        entity_ids.add(item.node_id)
        evidence_to_entities[item.evidence_id].add(item.node_id)
    for item in pack.semantic_candidates:
        entity_ids.add(item.node_id)
        evidence_to_entities[item.evidence_id].add(item.node_id)
    return entity_ids, evidence_to_entities, path_ids


def _channel_root_identity(item: FusionChannelRoot | FusionCandidate) -> tuple[Any, ...]:
    return (
        item.modality,
        item.producer_id,
        item.producer_version,
        item.corpus_id,
        item.corpus_version,
        item.corpus_manifest_sha256,
        item.index_id,
        item.index_version,
        item.index_build_sha256,
        item.built_at,
        item.expires_at,
        item.analyzer_policy_sha256,
        item.path_policy_sha256,
        item.provider_kind,
        item.embedding_model_id,
        item.embedding_deployment_id,
        item.embedding_version,
        item.embedding_dimensions,
        item.vector_normalization,
        item.vector_distance,
        item.chunking_policy_sha256,
    )


def channel_roots_sha256(roots: tuple[FusionChannelRoot, ...]) -> str:
    ordered = sorted(roots, key=lambda item: item.modality.value)
    if len({item.modality for item in ordered}) != len(ordered):
        raise FusionError("Channel roots must be unique by modality")
    return _stable_hash([item.model_dump(mode="json") for item in ordered])


def _quantize(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _candidate_identity(item: FusionCandidate) -> tuple[str, str, str]:
    return item.entity_id, item.assertion_key, item.assertion_value


def _fused_id(identity: tuple[str, str, str]) -> str:
    return f"fused-{_stable_hash(identity)[:32]}"


def _conflict_id(entity_id: str, assertion_key: str) -> str:
    return f"conflict-{_stable_hash((entity_id, assertion_key))[:32]}"


def _require_candidate_inputs(
    pack: GraphContextPack,
    policy: FusionPolicy,
    candidates: tuple[FusionCandidate, ...],
    availability: tuple[ModalityAvailability, ...],
    channel_roots: tuple[FusionChannelRoot, ...],
    evaluated_at: str,
    expected_query_sha256: str,
) -> tuple[list[FusionCandidate], list[FusionCandidate], list[ModalityAvailability], int]:
    limits = policy.limits
    if len(candidates) > limits.maximum_input_candidates:
        raise FusionError("Candidate input exceeds maximumInputCandidates")
    availability_by_modality = {item.modality: item for item in availability}
    if len(availability_by_modality) != len(availability) or set(availability_by_modality) != set(
        policy.supported_modalities
    ):
        raise FusionError("Availability must exactly and uniquely cover policy modalities")
    counts = Counter(item.modality for item in candidates)
    if any(count > limits.maximum_input_candidates_per_modality for count in counts.values()):
        raise FusionError("Candidate input exceeds maximumInputCandidatesPerModality")
    raw_ordered = sorted(
        candidates,
        key=lambda item: (item.candidate_id, _stable_hash(item.model_dump(mode="json"))),
    )
    ordered_availability = sorted(availability, key=lambda item: item.modality.value)
    raw_input_body = {
        "context_pack_sha256": pack.context_pack_sha256,
        "candidates": [item.model_dump(mode="json") for item in raw_ordered],
        "availability": [item.model_dump(mode="json") for item in ordered_availability],
        "channel_roots": [
            item.model_dump(mode="json")
            for item in sorted(channel_roots, key=lambda item: item.modality.value)
        ],
        "evaluated_at": evaluated_at,
    }
    input_bytes = len(_canonical_json(raw_input_body))
    if input_bytes > limits.maximum_input_bytes:
        raise FusionError("Candidate input exceeds maximumInputBytes")
    root_by_modality = {item.modality: item for item in channel_roots}
    if len(root_by_modality) != len(channel_roots) or set(root_by_modality) != set(
        policy.supported_modalities
    ):
        raise FusionError("Channel roots must exactly and uniquely cover policy modalities")
    evaluation_time = _parse_timestamp(evaluated_at, "evaluated_at")
    for root in channel_roots:
        try:
            validated_root = FusionChannelRoot.model_validate(root.model_dump(mode="json"))
        except ValidationError as exc:
            raise FusionError("Channel root is malformed, stale, or invalid") from exc
        if validated_root != root:
            raise FusionError("Channel root differs after strict validation")
        built_at = _parse_timestamp(root.built_at, "channel root built_at")
        expires_at = _parse_timestamp(root.expires_at, "channel root expires_at")
        if built_at > evaluation_time:
            raise FusionError("Channel root index build is in the future")
        if expires_at < evaluation_time:
            raise FusionError("Channel root index is stale or expired")
    by_id: dict[str, FusionCandidate] = {}
    for item in raw_ordered:
        try:
            validated = FusionCandidate.model_validate(item.model_dump(mode="json"))
        except ValidationError as exc:
            raise FusionError("Candidate receipt is malformed, stale, or invalid") from exc
        if validated != item:
            raise FusionError("Candidate receipt differs after strict validation")
        prior = by_id.get(item.candidate_id)
        if prior is not None and prior != item:
            raise FusionError("Conflicting duplicate candidate_id is ambiguous")
        by_id[item.candidate_id] = item
    ordered = sorted(by_id.values(), key=lambda item: item.candidate_id)
    visible_entities, evidence_to_entities, path_ids = _visible_context(pack)
    expected_identity = (
        pack.project_id,
        pack.source_snapshot,
        pack.source_graph_sha256,
        pack.normalized_graph_sha256,
        pack.context_pack_sha256,
        pack.ontology_id,
        pack.ontology_version,
        pack.ontology_sha256,
        pack.profile_id,
        pack.profile_version,
        pack.profile_sha256,
    )
    for item in ordered:
        if item.modality not in policy.supported_modalities:
            raise FusionError("Candidate modality is not authorized by the fusion policy")
        if availability_by_modality[item.modality].status is ModalityStatus.UNAVAILABLE:
            raise FusionError("Unavailable modality cannot supply candidates")
        if availability_by_modality[item.modality].status in (
            ModalityStatus.STALE,
            ModalityStatus.INVALID,
        ):
            raise FusionError("Stale or invalid modality cannot supply candidates")
        if _channel_root_identity(item) != _channel_root_identity(root_by_modality[item.modality]):
            raise FusionError("Candidate references an unknown or untrusted channel index")
        if _parse_timestamp(item.evaluated_at, "candidate evaluated_at") != evaluation_time:
            raise FusionError("Candidate evaluation time differs from the fusion evaluation")
        if (
            item.project_id,
            item.source_snapshot,
            item.source_graph_sha256,
            item.normalized_graph_sha256,
            item.context_pack_sha256,
            item.ontology_id,
            item.ontology_version,
            item.ontology_sha256,
            item.profile_id,
            item.profile_version,
            item.profile_sha256,
        ) != expected_identity:
            raise FusionError("Candidate crosses the trusted context source boundary")
        if item.request_sha256 != pack.request_sha256:
            raise FusionError("Candidate request identity differs from the context pack")
        if item.query_sha256 != expected_query_sha256:
            raise FusionError("Candidate query identity differs from the trusted query root")
        if item.entity_id not in visible_entities:
            raise FusionError("Candidate entity is not visible in the trusted context pack")
        if not set(item.evidence_ids).issubset(evidence_to_entities):
            raise FusionError("Candidate cites evidence outside the trusted context pack")
        if any(item.entity_id not in evidence_to_entities[value] for value in item.evidence_ids):
            raise FusionError("Candidate evidence is not bound to its asserted entity")
        if item.modality is FusionModality.GRAPH and item.graph_path_sha256 not in path_ids:
            raise FusionError("Graph candidate does not cite a confirmed context path")
        if (
            item.modality is FusionModality.GRAPH
            and item.graph_path_sha256 not in item.evidence_ids
        ):
            raise FusionError("Graph candidate path receipt must be cited as evidence")
        strings = (
            item.candidate_id,
            item.entity_id,
            item.assertion_key,
            item.assertion_value,
            item.project_id,
            item.source_snapshot,
            *item.evidence_ids,
        )
        if any(len(value) > limits.maximum_identifier_characters for value in strings):
            raise FusionError("Candidate field exceeds maximumIdentifierCharacters")
        if len(item.evidence_ids) > limits.maximum_evidence_ids_per_candidate:
            raise FusionError("Candidate exceeds maximumEvidenceIdsPerCandidate")
    if any(
        item.detail is not None and len(item.detail) > limits.maximum_reason_characters
        for item in ordered_availability
    ):
        raise FusionError("Availability detail exceeds maximumReasonCharacters")
    return ordered, raw_ordered, ordered_availability, input_bytes


def _fuse(
    pack: GraphContextPack, policy: FusionPolicy, candidates: list[FusionCandidate]
) -> list[FusedCandidate]:
    quantum_places = policy.score_decimal_places
    ranks: dict[tuple[FusionModality, tuple[str, str, str]], int] = {}
    quantized: dict[str, Decimal] = {}
    for modality in policy.supported_modalities:
        rows = [item for item in candidates if item.modality is modality]
        for item in rows:
            quantized[item.candidate_id] = _quantize(item.normalized_score, quantum_places)
        logical_claims: dict[tuple[str, str, str], list[FusionCandidate]] = defaultdict(list)
        for item in rows:
            logical_claims[_candidate_identity(item)].append(item)
        ordered_claims = sorted(
            logical_claims,
            key=lambda identity: (
                -max(quantized[item.candidate_id] for item in logical_claims[identity]),
                identity,
            ),
        )
        ranks.update(
            {
                (modality, identity): index
                for index, identity in enumerate(ordered_claims, start=1)
            }
        )

    grouped: dict[tuple[str, str, str], list[FusionCandidate]] = defaultdict(list)
    for item in candidates:
        grouped[_candidate_identity(item)].append(item)
    conflicting_keys = {
        key
        for key, values in defaultdict(set, {
            (entity, assertion): {
                value
                for candidate_entity, candidate_assertion, value in grouped
                if (candidate_entity, candidate_assertion) == (entity, assertion)
            }
            for entity, assertion, _ in grouped
        }).items()
        if len(values) > 1
    }
    results: list[FusedCandidate] = []
    for identity, rows in grouped.items():
        contributions: list[FusionContribution] = []
        for modality in policy.supported_modalities:
            modality_rows = [row for row in rows if row.modality is modality]
            if not modality_rows:
                continue
            best_rank = ranks[(modality, identity)]
            best_score = max(quantized[row.candidate_id] for row in modality_rows)
            component = _quantize(
                Decimal(policy.modality_weights[modality])
                / Decimal(policy.reciprocal_rank_constant + best_rank),
                quantum_places,
            )
            contributions.append(
                FusionContribution(
                    modality=modality,
                    rank=best_rank,
                    quantized_input_score=best_score,
                    weighted_reciprocal_rank=component,
                    source_candidate_ids=tuple(
                        sorted(row.candidate_id for row in modality_rows)
                    ),
                )
            )
        entity_id, assertion_key, assertion_value = identity
        has_conflict = (entity_id, assertion_key) in conflicting_keys
        results.append(
            FusedCandidate(
                fused_candidate_id=_fused_id(identity),
                entity_id=entity_id,
                assertion_key=assertion_key,
                assertion_value=assertion_value,
                fused_score=_quantize(
                    sum(
                        (item.weighted_reciprocal_rank for item in contributions),
                        start=Decimal("0"),
                    ),
                    quantum_places,
                ),
                evidence_ids=tuple(sorted({value for row in rows for value in row.evidence_ids})),
                contributions=tuple(sorted(contributions, key=lambda item: item.modality.value)),
                conflict_set_id=(
                    _conflict_id(entity_id, assertion_key) if has_conflict else None
                ),
                has_conflict=has_conflict,
                project_id=pack.project_id,
                source_snapshot=pack.source_snapshot,
                source_graph_sha256=pack.source_graph_sha256,
                normalized_graph_sha256=pack.normalized_graph_sha256,
                context_pack_sha256=pack.context_pack_sha256,
                ontology_id=pack.ontology_id,
                ontology_version=pack.ontology_version,
                ontology_sha256=pack.ontology_sha256,
                profile_id=pack.profile_id,
                profile_version=pack.profile_version,
                profile_sha256=pack.profile_sha256,
            )
        )
    return sorted(
        results,
        key=lambda item: (
            -item.fused_score,
            item.entity_id,
            item.assertion_key,
            item.assertion_value,
            item.fused_candidate_id,
        ),
    )


def _selection_bundles(
    candidates: list[FusedCandidate], policy: FusionPolicy
) -> list[list[FusedCandidate]]:
    positions = {item.fused_candidate_id: index for index, item in enumerate(candidates)}
    grouped: dict[str, list[FusedCandidate]] = defaultdict(list)
    for item in candidates:
        grouped[item.conflict_set_id or item.fused_candidate_id].append(item)
    def bundle_key(rows: list[FusedCandidate]) -> tuple[Any, ...]:
        protected = any(
            contribution.modality in policy.protected_modalities
            for item in rows
            for contribution in item.contributions
        )
        if protected:
            protected_score = max(
                sum(
                    (
                        contribution.weighted_reciprocal_rank
                        for contribution in item.contributions
                        if contribution.modality in policy.protected_modalities
                    ),
                    start=Decimal("0"),
                )
                for item in rows
            )
            canonical_identity = min(
                (
                    item.entity_id,
                    item.assertion_key,
                    item.assertion_value,
                    item.fused_candidate_id,
                )
                for item in rows
            )
            return (0, -protected_score, canonical_identity)
        return (1, min(positions[item.fused_candidate_id] for item in rows))

    return sorted(
        (
            sorted(rows, key=lambda item: positions[item.fused_candidate_id])
            for rows in grouped.values()
        ),
        key=bundle_key,
    )


def _gaps(
    pack: GraphContextPack, availability: list[ModalityAvailability]
) -> list[FusionGap]:
    gaps = [
        FusionGap(
            code=f"{item.modality.value}_{item.status.value}",
            modality=item.modality,
            detail=item.reason_code,
            blocking=False,
        )
        for item in availability
        if item.status is not ModalityStatus.AVAILABLE
    ]
    if not pack.analysis_complete:
        gaps.append(
            FusionGap(
                code="UPSTREAM_CONTEXT_INCOMPLETE",
                detail="The trusted context pack contains blocking upstream gaps.",
                blocking=True,
            )
        )
    if all(item.status is ModalityStatus.UNAVAILABLE for item in availability):
        gaps.append(
            FusionGap(
                code="NO_MODALITY_AVAILABLE",
                detail="No candidate modality is available.",
                blocking=True,
            )
        )
    deterministic = [
        item
        for item in availability
        if item.modality in (FusionModality.EXACT, FusionModality.GRAPH)
    ]
    if deterministic and all(
        item.status in (
            ModalityStatus.UNAVAILABLE,
            ModalityStatus.STALE,
            ModalityStatus.INVALID,
        )
        for item in deterministic
    ):
        gaps.append(
            FusionGap(
                code="DETERMINISTIC_CHANNELS_UNAVAILABLE",
                detail="Exact and confirmed-graph channels are unavailable for corroboration.",
                blocking=True,
            )
        )
    return sorted(gaps, key=lambda item: (item.code, item.modality.value if item.modality else ""))


def _omission_gaps(
    omissions: list[FusionOmission],
    fused: list[FusedCandidate],
    policy: FusionPolicy,
) -> list[FusionGap]:
    omitted_ids = {item.fused_candidate_id for item in omissions}
    omitted_candidates = [item for item in fused if item.fused_candidate_id in omitted_ids]
    gaps: list[FusionGap] = []
    conflict_sets = sorted(
        {item.conflict_set_id for item in omitted_candidates if item.conflict_set_id}
    )
    gaps.extend(
        FusionGap(
            code="CONFLICT_SET_BUDGET_EXCEEDED",
            detail=conflict_set_id,
            blocking=True,
        )
        for conflict_set_id in conflict_sets
    )
    if any(
        contribution.modality in policy.protected_modalities
        for item in omitted_candidates
        for contribution in item.contributions
    ):
        gaps.append(
            FusionGap(
                code="PROTECTED_CANDIDATE_BUDGET_EXCEEDED",
                detail="A verified exact or confirmed-graph candidate bundle was omitted.",
                blocking=True,
            )
        )
    if any(item.conflict_set_id is None for item in omitted_candidates):
        gaps.append(
            FusionGap(
                code="CANDIDATE_BUDGET_TRUNCATED",
                detail="One or more nonconflicting proposal candidates were omitted.",
                blocking=False,
            )
        )
    return sorted(gaps, key=lambda item: (item.code, item.detail or ""))


def _build_body(
    pack: GraphContextPack,
    policy: FusionPolicy,
    input_sha256: str,
    availability: list[ModalityAvailability],
    selected: list[FusedCandidate],
    omissions: list[FusionOmission],
    gaps: list[FusionGap],
    input_candidates: list[FusionCandidate],
    input_bytes: int,
    available_count: int,
    channel_root_sha256: str,
    evaluated_at: str,
    query_sha256: str,
) -> dict[str, Any]:
    limits = policy.limits
    return {
        "schema_version": "1.0.0",
        "posture": FusionPosture.ANALYSIS_ONLY,
        "relationship_state": CandidateState.CANDIDATE,
        "may_authorize": False,
        "may_satisfy_release_evidence": False,
        "authority_eligible": False,
        "analysis_complete": not any(gap.blocking for gap in gaps),
        "fusion_complete": not any(
            gap.blocking and gap.code != "UPSTREAM_CONTEXT_INCOMPLETE" for gap in gaps
        ),
        "upstream_context_complete": pack.analysis_complete,
        "degraded": any(item.status is not ModalityStatus.AVAILABLE for item in availability),
        "project_id": pack.project_id,
        "source_snapshot": pack.source_snapshot,
        "source_graph_sha256": pack.source_graph_sha256,
        "normalized_graph_sha256": pack.normalized_graph_sha256,
        "context_pack_sha256": pack.context_pack_sha256,
        "ontology_id": pack.ontology_id,
        "ontology_version": pack.ontology_version,
        "ontology_sha256": pack.ontology_sha256,
        "profile_id": pack.profile_id,
        "profile_version": pack.profile_version,
        "profile_sha256": pack.profile_sha256,
        "reasoning_policy_sha256": pack.reasoning_policy_sha256,
        "retrieval_eval_set_id": pack.retrieval_eval_set_id,
        "retrieval_eval_set_sha256": pack.retrieval_eval_set_sha256,
        "compiler_policy_id": pack.compiler_policy_id,
        "compiler_policy_version": pack.compiler_policy_version,
        "compiler_policy_sha256": pack.compiler_policy_sha256,
        "fusion_policy_id": policy.policy_id,
        "fusion_policy_version": policy.policy_version,
        "fusion_policy_sha256": policy.sha256,
        "fusion_evaluation_set_id": policy.evaluation_set_id,
        "fusion_evaluation_set_path": policy.evaluation_set_path,
        "fusion_evaluation_set_sha256": policy.evaluation_set_sha256,
        "channel_roots_sha256": channel_root_sha256,
        "evaluated_at": evaluated_at,
        "query_sha256": query_sha256,
        "fusion_input_sha256": input_sha256,
        "modality_availability": [item.model_dump(mode="json") for item in availability],
        "candidates": [item.model_dump(mode="json") for item in selected],
        "omissions": [item.model_dump(mode="json") for item in omissions],
        "gaps": [item.model_dump(mode="json") for item in gaps],
        "budget": {
            "input_candidate_limit": limits.maximum_input_candidates,
            "input_candidate_count": len(input_candidates),
            "input_candidates_by_modality": {
                modality.value: sum(
                    1
                    for item in input_candidates
                    if item.modality is modality
                )
                for modality in sorted(policy.supported_modalities, key=lambda item: item.value)
            },
            "input_candidates_per_modality_limit": limits.maximum_input_candidates_per_modality,
            "input_bytes_limit": limits.maximum_input_bytes,
            "input_bytes": input_bytes,
            "output_candidate_limit": limits.maximum_output_candidates,
            "output_candidates_available": available_count,
            "output_candidates_selected": len(selected),
            "output_candidates_omitted": len(omissions),
            "evidence_ids_per_candidate_limit": limits.maximum_evidence_ids_per_candidate,
            "identifier_character_limit": limits.maximum_identifier_characters,
            "reason_character_limit": limits.maximum_reason_characters,
            "omission_record_limit": limits.maximum_omission_records,
            "result_byte_limit": limits.maximum_result_bytes,
            "result_bytes": 0,
        },
    }


def _set_result_bytes(body: dict[str, Any]) -> None:
    for _ in range(12):
        envelope = {**body, "result_sha256": "0" * 64}
        size = len(_canonical_json(envelope))
        if body["budget"]["result_bytes"] == size:
            return
        body["budget"]["result_bytes"] = size
    raise FusionError("Fusion result byte accounting did not converge")


def _require_result_field_limits(result: CandidateFusionResult) -> None:
    budget = result.budget
    identifiers = [
        result.project_id,
        result.source_snapshot,
        result.ontology_id,
        result.ontology_version,
        result.profile_id,
        result.profile_version,
        result.retrieval_eval_set_id,
        result.compiler_policy_id,
        result.compiler_policy_version,
        result.fusion_policy_id,
        result.fusion_policy_version,
        result.fusion_evaluation_set_id,
        result.fusion_evaluation_set_path,
        result.evaluated_at,
        *(item.reason_code for item in result.modality_availability if item.reason_code),
        *(item.code for item in result.gaps),
        *(item.fused_candidate_id for item in result.omissions),
        *(
            value
            for item in result.candidates
            for value in (
                item.fused_candidate_id,
                item.entity_id,
                item.assertion_key,
                item.assertion_value,
                *(item.evidence_ids),
                *(
                    source_id
                    for part in item.contributions
                    for source_id in part.source_candidate_ids
                ),
            )
        ),
    ]
    reasons = [
        *(item.detail for item in result.modality_availability if item.detail),
        *(item.detail for item in result.gaps if item.detail),
    ]
    if any(len(value) > budget.identifier_character_limit for value in identifiers):
        raise ValueError("Fusion result identifier exceeds its field budget")
    if any(len(value) > budget.reason_character_limit for value in reasons):
        raise ValueError("Fusion result reason exceeds its field budget")
    if any(
        len(item.evidence_ids) > budget.evidence_ids_per_candidate_limit
        for item in result.candidates
    ):
        raise ValueError("Fusion result evidence count exceeds its field budget")


def compile_candidate_fusion(
    pack: GraphContextPack,
    policy: FusionPolicy,
    evaluation_contract: FusionEvaluationContract,
    candidates: tuple[FusionCandidate, ...],
    modality_availability: tuple[ModalityAvailability, ...],
    channel_roots: tuple[FusionChannelRoot, ...],
    *,
    evaluated_at: str,
    expected_query_sha256: str,
    expected_context_pack_sha256: str,
    expected_reasoning_policy_sha256: str,
    expected_retrieval_eval_set_sha256: str,
    expected_compiler_policy_sha256: str,
    expected_fusion_policy_sha256: str,
    expected_fusion_evaluation_set_sha256: str,
    expected_channel_roots_sha256: str,
) -> CandidateFusionResult:
    """Fuse bounded candidate planes without creating evidence or authority."""

    if contract_sha256(policy.model_dump(mode="json", by_alias=True)) != policy.sha256:
        raise FusionError("In-memory fusion policy digest is invalid")
    if policy.sha256 != expected_fusion_policy_sha256:
        raise FusionError("Fusion policy differs from its externally trusted root")
    if policy.evaluation_set_sha256 != expected_fusion_evaluation_set_sha256:
        raise FusionError("Fusion evaluation set differs from its externally trusted root")
    repository_root = Path(__file__).resolve().parents[2]
    evaluation_path = (repository_root / policy.evaluation_set_path).resolve()
    try:
        evaluation_path.relative_to(repository_root)
        loaded_evaluation = load_fusion_evaluation_contract(
            evaluation_path,
            expected_sha256=expected_fusion_evaluation_set_sha256,
        )
    except (ValueError, FusionContractError) as exc:
        raise FusionError("Trusted fusion evaluation artifact cannot be loaded") from exc
    evaluation_body = evaluation_contract.model_dump(mode="json", by_alias=True)
    if (
        evaluation_contract != loaded_evaluation
        or contract_sha256(evaluation_body) != evaluation_contract.sha256
        or evaluation_contract.sha256 != expected_fusion_evaluation_set_sha256
        or evaluation_contract.evaluation_set_id != policy.evaluation_set_id
        or evaluation_contract.target_policy_id != policy.policy_id
        or evaluation_contract.target_policy_version != policy.policy_version
    ):
        raise FusionError("Fusion evaluation contract differs from policy or trusted root")
    actual_channel_roots_sha256 = channel_roots_sha256(channel_roots)
    if actual_channel_roots_sha256 != expected_channel_roots_sha256:
        raise FusionError("Channel roots differ from their externally trusted root")
    _parse_timestamp(evaluated_at, "evaluated_at")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_query_sha256):
        raise FusionError("expected_query_sha256 must be a lowercase SHA-256 digest")
    _require_pack_integrity(pack, expected_context_pack_sha256)
    if (
        pack.reasoning_policy_sha256 != expected_reasoning_policy_sha256
        or pack.retrieval_eval_set_sha256 != expected_retrieval_eval_set_sha256
        or pack.compiler_policy_sha256 != expected_compiler_policy_sha256
    ):
        raise FusionError("Context policy or evaluation identity differs from its trusted root")
    ordered, raw_ordered, availability, input_bytes = _require_candidate_inputs(
        pack,
        policy,
        candidates,
        modality_availability,
        channel_roots,
        evaluated_at,
        expected_query_sha256,
    )
    input_body = {
        "context_pack_sha256": pack.context_pack_sha256,
        "reasoning_policy_sha256": pack.reasoning_policy_sha256,
        "retrieval_eval_set_sha256": pack.retrieval_eval_set_sha256,
        "compiler_policy_sha256": pack.compiler_policy_sha256,
        "fusion_policy_sha256": policy.sha256,
        "fusion_evaluation_set_sha256": policy.evaluation_set_sha256,
        "channel_roots_sha256": actual_channel_roots_sha256,
        "evaluated_at": evaluated_at,
        "query_sha256": expected_query_sha256,
        "candidates": [item.model_dump(mode="json") for item in raw_ordered],
        "availability": [item.model_dump(mode="json") for item in availability],
    }
    input_sha256 = _stable_hash(input_body)
    fused = _fuse(pack, policy, ordered)
    bundles = _selection_bundles(fused, policy)
    accepted_bundles: list[list[FusedCandidate]] = []
    selected_count = 0
    omitted: list[FusionOmission] = []
    count_exhausted = False
    for bundle in bundles:
        if (
            count_exhausted
            or selected_count + len(bundle) > policy.limits.maximum_output_candidates
        ):
            count_exhausted = True
            omitted.extend(
                FusionOmission(
                    fused_candidate_id=item.fused_candidate_id,
                    conflict_set_id=item.conflict_set_id,
                    reason_code="OUTPUT_CANDIDATE_LIMIT",
                )
                for item in bundle
            )
        else:
            accepted_bundles.append(bundle)
            selected_count += len(bundle)
    availability_gaps = _gaps(pack, availability)

    while True:
        selected_ids = {
            item.fused_candidate_id for bundle in accepted_bundles for item in bundle
        }
        selected = [item for item in fused if item.fused_candidate_id in selected_ids]
        gaps = [
            *availability_gaps,
            *_omission_gaps(omitted, fused, policy),
        ]
        gaps.sort(key=lambda item: (item.code, item.detail or ""))
        body = _build_body(
            pack,
            policy,
            input_sha256,
            availability,
            selected,
            sorted(omitted, key=lambda item: item.fused_candidate_id),
            gaps,
            raw_ordered,
            input_bytes,
            len(fused),
            actual_channel_roots_sha256,
            evaluated_at,
            expected_query_sha256,
        )
        _set_result_bytes(body)
        if body["budget"]["result_bytes"] <= policy.limits.maximum_result_bytes:
            break
        if not accepted_bundles:
            raise FusionError("Fusion envelope exceeds maximumResultBytes")
        removed = accepted_bundles.pop()
        omitted.extend(
            FusionOmission(
                fused_candidate_id=item.fused_candidate_id,
                conflict_set_id=item.conflict_set_id,
                reason_code="RESULT_BYTE_LIMIT",
            )
            for item in removed
        )
    if len(omitted) > policy.limits.maximum_omission_records:
        raise FusionError("Fusion omissions exceed maximumOmissionRecords")
    ordered_omissions = sorted(omitted, key=lambda item: item.fused_candidate_id)
    body["omissions"] = [item.model_dump(mode="json") for item in ordered_omissions]
    _set_result_bytes(body)
    return CandidateFusionResult.model_validate({**body, "result_sha256": _stable_hash(body)})


def validate_candidate_fusion(
    result: CandidateFusionResult,
    pack: GraphContextPack,
    policy: FusionPolicy,
    evaluation_contract: FusionEvaluationContract,
    candidates: tuple[FusionCandidate, ...],
    modality_availability: tuple[ModalityAvailability, ...],
    channel_roots: tuple[FusionChannelRoot, ...],
    **trusted_roots: str,
) -> CandidateFusionResult:
    """Replay fusion from trusted inputs and reject altered persisted results."""

    expected = compile_candidate_fusion(
        pack,
        policy,
        evaluation_contract,
        candidates,
        modality_availability,
        channel_roots,
        **trusted_roots,
    )
    if result != expected:
        raise FusionError("Fusion result differs from trusted deterministic replay")
    return result
