from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.edge_envelope import (
    TrustedEdgeCompilationError,
    TrustedEdgeContractError,
    TrustedEdgeReplayInput,
    TrustedEdgeVerification,
    stable_sha256,
)
from neo_sf_q_intel.ontology import (
    Materiality,
    NormalizedGraph,
    contract_sha256,
)
from neo_sf_q_intel.propagation import (
    PropagationEvaluationError,
    PropagationPathReceipt,
    PropagationPolicy,
    PropagationPolicyPin,
    PropagationResult,
    StructuralPathCandidate,
    StructuralPropagationScope,
    enumerate_structural_propagation,
    traverse_propagation,
)


class PathReplayContractError(RuntimeError):
    """Raised when a path-replay policy is not independently pinned and valid."""


class PathReplayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TrustedEdgePolicyPin(PathReplayModel):
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion", min_length=1)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[a-f0-9]{64}$")


class ExtractorRegistryPolicyPin(PathReplayModel):
    registry_id: str = Field(alias="registryId", min_length=1)
    registry_version: str = Field(alias="registryVersion", min_length=1)
    registry_sha256: str = Field(alias="registrySha256", pattern=r"^[a-f0-9]{64}$")


class CompletePathReplayPolicy(PathReplayModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propagation_policy: PropagationPolicyPin = Field(alias="propagationPolicy")
    trusted_edge_policy: TrustedEdgePolicyPin = Field(alias="trustedEdgePolicy")
    extractor_registry: ExtractorRegistryPolicyPin = Field(alias="extractorRegistry")
    seed_selection_mode: Literal["ALL_GRAPH_NODES"] = Field(
        alias="seedSelectionMode"
    )
    required_target_materialities: tuple[Materiality, ...] = Field(
        alias="requiredTargetMaterialities", min_length=1
    )
    maximum_required_seeds: int = Field(alias="maximumRequiredSeeds", ge=1, le=100_000)
    maximum_required_paths: int = Field(alias="maximumRequiredPaths", ge=1, le=100_000)
    maximum_required_hops: int = Field(alias="maximumRequiredHops", ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_contract(self) -> CompletePathReplayPolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.policy_version, "policyVersion")
        _require_identifier(self.policy_id, "policyId")
        if tuple(
            sorted(set(self.required_target_materialities), key=lambda value: value.value)
        ) != self.required_target_materialities:
            raise ValueError("Required target materialities must be sorted and unique")
        if self.required_target_materialities != (Materiality.MATERIAL,):
            raise ValueError("R0.2 requires every and only MATERIAL path target")
        return self


class CompletePathScope(PathReplayModel):
    scope_mode: Literal["ALL_REACHABLE_MATERIAL_PATHS"] = "ALL_REACHABLE_MATERIAL_PATHS"
    seed_ids: tuple[str, ...] = Field(min_length=1)
    path_seed_ids: tuple[str, ...] = Field(min_length=1)
    zero_material_path_seed_ids: tuple[str, ...]
    material_output_ids: tuple[str, ...] = Field(min_length=1)
    required_edge_ids: tuple[str, ...] = Field(min_length=1)
    required_path_sha256s: tuple[str, ...] = Field(min_length=1)
    path_count: int = Field(ge=1)
    hop_count: int = Field(ge=1)
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_scope(self) -> CompletePathScope:
        for values, label in (
            (self.seed_ids, "seed IDs"),
            (self.path_seed_ids, "path seed IDs"),
            (self.zero_material_path_seed_ids, "zero-path seed IDs"),
            (self.material_output_ids, "material output IDs"),
            (self.required_edge_ids, "required edge IDs"),
            (self.required_path_sha256s, "required path digests"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        if self.path_count != len(self.required_path_sha256s):
            raise ValueError("Path count differs from the required path scope")
        if set(self.path_seed_ids) & set(self.zero_material_path_seed_ids):
            raise ValueError("Path and zero-path seeds must be disjoint")
        if tuple(sorted((*self.path_seed_ids, *self.zero_material_path_seed_ids))) != (
            self.seed_ids
        ):
            raise ValueError("Path and zero-path seed partitions differ from full scope")
        body = self.model_dump(mode="json")
        declared = body.pop("scope_sha256")
        if stable_sha256(body) != declared:
            raise ValueError("Path scope digest does not match its canonical content")
        return self


class CompleteGraphPathArtifact(PathReplayModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    local_edge_envelope_path_coverage_complete: Literal[True] = True
    project_id: str = Field(min_length=1)
    source_snapshot: str = Field(min_length=1)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str = Field(min_length=1)
    ontology_version: str = Field(min_length=1)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propagation_policy_id: str = Field(min_length=1)
    propagation_policy_version: str = Field(min_length=1)
    propagation_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_replay_policy_id: str = Field(min_length=1)
    path_replay_policy_version: str = Field(min_length=1)
    path_replay_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trusted_edge_policy_id: str = Field(min_length=1)
    trusted_edge_policy_version: str = Field(min_length=1)
    trusted_edge_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trusted_edge_registry_id: str = Field(min_length=1)
    trusted_edge_registry_version: str = Field(min_length=1)
    trusted_edge_registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trusted_edge_verification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str = Field(min_length=1)
    propagation_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_scope: CompletePathScope
    paths: tuple[PropagationPathReceipt, ...] = Field(min_length=1)
    blocking_gap_codes: tuple[
        Literal[
            "CHANGE_SEED_SCOPE_NOT_ATTESTED",
            "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
        ],
        ...,
    ] = (
        "CHANGE_SEED_SCOPE_NOT_ATTESTED",
        "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
    )
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> CompleteGraphPathArtifact:
        evaluated = _parse_timestamp(self.evaluated_at)
        if tuple(sorted(self.paths, key=_path_sort_key)) != self.paths:
            raise ValueError("Complete paths must use canonical deterministic order")
        if len({path.path_sha256 for path in self.paths}) != len(self.paths):
            raise ValueError("Complete path artifact contains duplicate paths")
        if tuple(sorted(path.path_sha256 for path in self.paths)) != (
            self.required_scope.required_path_sha256s
        ):
            raise ValueError("Artifact paths differ from its required path scope")
        if sum(len(path.hops) for path in self.paths) != self.required_scope.hop_count:
            raise ValueError("Artifact hop count differs from its required path scope")
        if any(not path.local_edge_envelope_complete for path in self.paths):
            raise ValueError("Every complete path must use verified R0.1 edge envelopes")
        if self.required_scope.seed_ids != tuple(
            sorted({*self.required_scope.seed_ids})
        ):
            raise ValueError("Artifact seed scope is not canonical")
        if self.required_scope.material_output_ids != tuple(
            sorted({path.target_id for path in self.paths})
        ):
            raise ValueError("Artifact material outputs differ from its paths")
        if self.required_scope.path_seed_ids != tuple(
            sorted({path.seed_id for path in self.paths})
        ):
            raise ValueError("Artifact path seeds differ from its paths")
        if self.required_scope.required_edge_ids != tuple(
            sorted({hop.edge_id for path in self.paths for hop in path.hops})
        ):
            raise ValueError("Artifact edge scope differs from its ordered path hops")
        expected_path_identity = (
            self.project_id,
            self.source_graph_sha256,
            self.source_snapshot,
            self.ontology_id,
            self.ontology_version,
            self.ontology_sha256,
            self.profile_id,
            self.profile_version,
            self.profile_sha256,
            self.normalized_graph_sha256,
            self.propagation_policy_id,
            self.propagation_policy_version,
            self.propagation_policy_sha256,
        )
        for path in self.paths:
            if (
                path.project_id,
                path.source_graph_sha256,
                path.source_snapshot,
                path.ontology_id,
                path.ontology_version,
                path.ontology_sha256,
                path.profile_id,
                path.profile_version,
                path.profile_sha256,
                path.graph_sha256,
                path.policy_id,
                path.policy_version,
                path.policy_sha256,
            ) != expected_path_identity:
                raise ValueError("Path identity differs from the complete replay artifact")
            if path.seed_id not in self.required_scope.seed_ids:
                raise ValueError("Path seed is outside the required seed scope")
            for hop in path.hops:
                if _parse_timestamp(hop.trusted_edge_evaluated_at or "") != evaluated:
                    raise ValueError("Path hop evaluated-at differs from its artifact")
                if _parse_timestamp(hop.trusted_edge_valid_until or "") <= evaluated:
                    raise ValueError("Path hop was not fresh at artifact evaluation")
                if (
                    hop.trusted_edge_verification_sha256
                    != self.trusted_edge_verification_sha256
                    or hop.trusted_edge_evaluated_at != self.evaluated_at
                    or hop.trusted_edge_policy_sha256 != self.trusted_edge_policy_sha256
                    or hop.trusted_edge_registry_sha256
                    != self.trusted_edge_registry_sha256
                ):
                    raise ValueError("Path hop differs from current R0.1 verification")
        if self.blocking_gap_codes != (
            "CHANGE_SEED_SCOPE_NOT_ATTESTED",
            "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
        ):
            raise ValueError("R0.2 must retain upstream capture and change-seed gaps")
        body = self.model_dump(mode="json")
        declared = body.pop("artifact_sha256")
        if stable_sha256(body) != declared:
            raise ValueError("Complete path artifact digest does not match its content")
        return self


class PathReplayGapCode(StrEnum):
    CHANGE_SEED_SCOPE_NOT_ATTESTED = "CHANGE_SEED_SCOPE_NOT_ATTESTED"
    UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED = "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"
    NO_REQUIRED_SEED_SCOPE = "NO_REQUIRED_SEED_SCOPE"
    REQUIRED_SEED_LIMIT_EXCEEDED = "REQUIRED_SEED_LIMIT_EXCEEDED"
    NO_REQUIRED_MATERIAL_OUTPUT_SCOPE = "NO_REQUIRED_MATERIAL_OUTPUT_SCOPE"
    NO_REQUIRED_MATERIAL_PATH_SCOPE = "NO_REQUIRED_MATERIAL_PATH_SCOPE"
    REQUIRED_PATH_LIMIT_EXCEEDED = "REQUIRED_PATH_LIMIT_EXCEEDED"
    REQUIRED_HOP_LIMIT_EXCEEDED = "REQUIRED_HOP_LIMIT_EXCEEDED"
    PROPAGATION_SCOPE_INCOMPLETE = "PROPAGATION_SCOPE_INCOMPLETE"
    EDGE_REPLAY_CONTRACT_REJECTED = "EDGE_REPLAY_CONTRACT_REJECTED"
    EDGE_REPLAY_INCOMPLETE = "EDGE_REPLAY_INCOMPLETE"
    EDGE_SCOPE_MISMATCH = "EDGE_SCOPE_MISMATCH"
    TRUSTED_PATH_SET_MISMATCH = "TRUSTED_PATH_SET_MISMATCH"
    PATH_ARTIFACT_STRUCTURALLY_INVALID = "PATH_ARTIFACT_STRUCTURALLY_INVALID"
    PATH_ARTIFACT_DIGEST_MISMATCH = "PATH_ARTIFACT_DIGEST_MISMATCH"
    PATH_REMOVED = "PATH_REMOVED"
    PATH_SUBSTITUTED = "PATH_SUBSTITUTED"
    PATH_DUPLICATED = "PATH_DUPLICATED"
    PATH_ORDER_CHANGED = "PATH_ORDER_CHANGED"
    PATH_HOP_TAMPERED = "PATH_HOP_TAMPERED"
    PATH_ROOT_OR_POLICY_MISMATCH = "PATH_ROOT_OR_POLICY_MISMATCH"
    TIME_AUTHORITY_UNAVAILABLE = "TIME_AUTHORITY_UNAVAILABLE"


class PathReplayGap(PathReplayModel):
    code: PathReplayGapCode
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["RELEASE_ONLY"] = "RELEASE_ONLY"
    blocking: Literal[True] = True


class CompletePathReplayEvaluation(PathReplayModel):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    project_id: str
    source_snapshot: str
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propagation_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_replay_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trusted_edge_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trusted_edge_registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    local_edge_envelope_path_coverage_complete: bool
    artifact: CompleteGraphPathArtifact | None = None
    gaps: tuple[PathReplayGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> CompletePathReplayEvaluation:
        expected_complete = self.artifact is not None
        if self.local_edge_envelope_path_coverage_complete is not expected_complete:
            raise ValueError("Local path coverage state differs from artifact presence")
        keys = [(gap.code.value, gap.identity_sha256) for gap in self.gaps]
        if keys != sorted(set(keys)):
            raise ValueError("Path replay gaps must be sorted and deduplicated")
        required_planned = {
            PathReplayGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED,
            PathReplayGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED,
        }
        if not required_planned <= {gap.code for gap in self.gaps}:
            raise ValueError("R0.2 evaluation must retain planned authority gaps")
        if self.artifact is not None and (
            self.project_id,
            self.source_snapshot,
            self.source_graph_sha256,
            self.normalized_graph_sha256,
            self.ontology_sha256,
            self.profile_sha256,
            self.propagation_policy_sha256,
            self.path_replay_policy_sha256,
            self.trusted_edge_policy_sha256,
            self.trusted_edge_registry_sha256,
            self.evaluated_at,
        ) != (
            self.artifact.project_id,
            self.artifact.source_snapshot,
            self.artifact.source_graph_sha256,
            self.artifact.normalized_graph_sha256,
            self.artifact.ontology_sha256,
            self.artifact.profile_sha256,
            self.artifact.propagation_policy_sha256,
            self.artifact.path_replay_policy_sha256,
            self.artifact.trusted_edge_policy_sha256,
            self.artifact.trusted_edge_registry_sha256,
            self.artifact.evaluated_at,
        ):
            raise ValueError("Path replay evaluation roots differ from its artifact")
        body = self.model_dump(mode="json")
        declared = body.pop("evaluation_sha256")
        if stable_sha256(body) != declared:
            raise ValueError("Path replay evaluation digest does not match its content")
        return self


DEFAULT_PATH_REPLAY_POLICY_SHA256 = (
    "0c86193a76dd48dcdd04597e65ec202ba6cae61fc93537eee209df8bfdbc19aa"
)

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must use semantic versioning")


def _require_identifier(value: str, field: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase stable identifier")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PathReplayContractError("Path replay policy contains duplicate keys")
        result[key] = value
    return result


def load_complete_path_replay_policy(
    path: Path,
    propagation_policy: PropagationPolicy,
    *,
    expected_sha256: str = DEFAULT_PATH_REPLAY_POLICY_SHA256,
) -> CompletePathReplayPolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise PathReplayContractError("Path replay policy cannot be loaded") from exc
    if not isinstance(raw, dict):
        raise PathReplayContractError("Path replay policy root must be an object")
    declared = raw.get("sha256")
    computed = contract_sha256(raw)
    if (
        not isinstance(declared, str)
        or declared != computed
        or declared != expected_sha256.casefold()
    ):
        raise PathReplayContractError("Path replay policy digest is not externally pinned")
    try:
        policy = CompletePathReplayPolicy.model_validate(raw)
    except ValidationError as exc:
        raise PathReplayContractError("Path replay policy is structurally invalid") from exc
    _require_policy_runtime(policy, propagation_policy, expected_sha256)
    return policy


def _require_policy_runtime(
    policy: CompletePathReplayPolicy,
    propagation_policy: PropagationPolicy,
    expected_sha256: str,
) -> None:
    if (
        contract_sha256(policy.model_dump(mode="json", by_alias=True)) != policy.sha256
        or policy.sha256 != expected_sha256.casefold()
    ):
        raise PathReplayContractError("In-memory path replay policy is not pinned")
    pin = policy.propagation_policy
    if (pin.policy_id, pin.policy_version, pin.policy_sha256) != (
        propagation_policy.policy_id,
        propagation_policy.policy_version,
        propagation_policy.sha256,
    ):
        raise PathReplayContractError("Path replay and propagation policy identities differ")
    if policy.maximum_required_paths < propagation_policy.maximum_total_paths:
        raise PathReplayContractError(
            "Path replay path capacity is smaller than propagation capacity"
        )
    if policy.maximum_required_hops < (
        propagation_policy.maximum_total_paths * propagation_policy.maximum_total_depth
    ):
        raise PathReplayContractError(
            "Path replay hop capacity is smaller than propagation capacity"
        )


def _require_edge_replay_policy_pins(
    path_policy: CompletePathReplayPolicy,
    replay_input: TrustedEdgeReplayInput,
) -> None:
    edge_pin = path_policy.trusted_edge_policy
    if (edge_pin.policy_id, edge_pin.policy_version, edge_pin.policy_sha256) != (
        replay_input.policy.policy_id,
        replay_input.policy.policy_version,
        replay_input.policy.sha256,
    ):
        raise PathReplayContractError("Path replay trusted-edge policy pin differs")
    registry_pin = path_policy.extractor_registry
    if (
        registry_pin.registry_id,
        registry_pin.registry_version,
        registry_pin.registry_sha256,
    ) != (
        replay_input.registry.registry_id,
        replay_input.registry.registry_version,
        replay_input.registry.sha256,
    ):
        raise PathReplayContractError("Path replay extractor registry pin differs")
    if (
        replay_input.expected_policy_sha256 != edge_pin.policy_sha256
        or replay_input.expected_registry_sha256 != registry_pin.registry_sha256
    ):
        raise PathReplayContractError("R0.1 external pins differ from the R0.2 trust roots")


def _path_sort_key(path: PropagationPathReceipt) -> tuple[Any, ...]:
    return (
        path.target_id,
        path.seed_id,
        len(path.hops),
        tuple((hop.edge_id, hop.direction.value) for hop in path.hops),
        path.path_sha256,
    )


def _static_path_structure(
    path: PropagationPathReceipt | StructuralPathCandidate,
) -> tuple[Any, ...]:
    return (
        path.seed_id,
        path.target_id,
        tuple(
            (
                hop.edge_id,
                hop.direction.value,
                hop.traversal_from_id,
                hop.traversal_to_id,
                hop.edge_source_id,
                hop.edge_target_id,
                hop.relation_class,
            )
            for hop in path.hops
        ),
    )


def _path_structure(path: PropagationPathReceipt) -> tuple[Any, ...]:
    static = _static_path_structure(path)
    return (
        *static,
        tuple(
            (
                hop.position,
                hop.edge_id,
                hop.relation_class,
                hop.direction.value,
                hop.traversal_from_id,
                hop.traversal_to_id,
                hop.edge_source_id,
                hop.edge_target_id,
                hop.source_artifact,
                hop.evidence_state.value,
                hop.source_snapshot,
                hop.source_hash,
                hop.extractor_id,
                hop.materiality.value,
                hop.provenance_scope,
                hop.trusted_edge_envelope_sha256,
                hop.source_artifact_sha256,
                hop.extractor_version,
                hop.extractor_implementation_sha256,
                hop.trusted_edge_policy_sha256,
                hop.trusted_edge_registry_sha256,
                hop.trusted_edge_valid_until,
                hop.release_blocking_gap_codes,
            )
            for hop in path.hops
        ),
    )


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be canonical UTC seconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _identity_digest(*values: Any) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _gap(code: PathReplayGapCode, *identity: Any) -> PathReplayGap:
    return PathReplayGap(code=code, identity_sha256=_identity_digest(code.value, *identity))


def _sorted_gaps(gaps: list[PathReplayGap]) -> tuple[PathReplayGap, ...]:
    return tuple(
        sorted(
            set(gaps),
            key=lambda gap: (gap.code.value, gap.identity_sha256, gap.scope, gap.blocking),
        )
    )


def _evaluation(
    graph: NormalizedGraph,
    propagation_policy: PropagationPolicy,
    path_policy: CompletePathReplayPolicy,
    evaluated_at: str,
    gaps: list[PathReplayGap],
    artifact: CompleteGraphPathArtifact | None,
) -> CompletePathReplayEvaluation:
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "project_id": graph.project_id or "unavailable",
        "source_snapshot": graph.source_snapshot or "unavailable",
        "source_graph_sha256": graph.source_graph_sha256 or "0" * 64,
        "normalized_graph_sha256": graph.graph_sha256,
        "ontology_sha256": graph.ontology_sha256,
        "profile_sha256": graph.profile_sha256,
        "propagation_policy_sha256": propagation_policy.sha256,
        "path_replay_policy_sha256": path_policy.sha256,
        "trusted_edge_policy_sha256": path_policy.trusted_edge_policy.policy_sha256,
        "trusted_edge_registry_sha256": path_policy.extractor_registry.registry_sha256,
        "evaluated_at": evaluated_at,
        "local_edge_envelope_path_coverage_complete": artifact is not None,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "gaps": [gap.model_dump(mode="json") for gap in _sorted_gaps(gaps)],
    }
    return CompletePathReplayEvaluation.model_validate(
        {**body, "evaluation_sha256": stable_sha256(body)}
    )


def _planned_gaps(graph: NormalizedGraph) -> list[PathReplayGap]:
    return [
        _gap(PathReplayGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED, graph.graph_sha256),
        _gap(PathReplayGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED, graph.graph_sha256),
    ]


def _derived_seed_ids(
    graph: NormalizedGraph, policy: CompletePathReplayPolicy
) -> tuple[str, ...]:
    if policy.seed_selection_mode != "ALL_GRAPH_NODES":
        raise PathReplayContractError("Unsupported seed selection mode")
    return tuple(sorted(node.node_id for node in graph.nodes))


def _required_paths(
    propagation: PropagationResult,
    graph: NormalizedGraph,
    policy: CompletePathReplayPolicy,
) -> tuple[PropagationPathReceipt, ...]:
    nodes = {node.node_id: node for node in graph.nodes}
    selected = [
        path
        for path in propagation.paths
        if nodes[path.target_id].materiality in policy.required_target_materialities
    ]
    return tuple(sorted(selected, key=_path_sort_key))


def _required_structural_paths(
    propagation: StructuralPropagationScope,
    graph: NormalizedGraph,
    policy: CompletePathReplayPolicy,
) -> tuple[StructuralPathCandidate, ...]:
    nodes = {node.node_id: node for node in graph.nodes}
    selected = [
        path
        for path in propagation.paths
        if nodes[path.target_id].materiality in policy.required_target_materialities
    ]
    return tuple(
        sorted(
            selected,
            key=lambda item: (
                item.target_id,
                item.seed_id,
                len(item.hops),
                tuple((hop.edge_id, hop.direction.value) for hop in item.hops),
            ),
        )
    )


def _route_relevant_analysis_gaps(
    propagation: PropagationResult | StructuralPropagationScope,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                gap.code
                for gap in propagation.gaps
                if gap.scope == "ANALYSIS"
            }
        )
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _current_time() -> datetime:
    current = _utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise PathReplayContractError("Replay clock did not provide an aware UTC time")
    return current.astimezone(UTC).replace(microsecond=0)


def compile_complete_graph_path_replay(
    graph: NormalizedGraph,
    propagation_policy: PropagationPolicy,
    path_policy: CompletePathReplayPolicy,
    trusted_edge_replay: TrustedEdgeReplayInput,
    *,
    expected_path_policy_sha256: str = DEFAULT_PATH_REPLAY_POLICY_SHA256,
) -> CompletePathReplayEvaluation:
    """Derive and replay the complete local material-path scope without release authority."""

    gaps = _planned_gaps(graph)
    try:
        now = _current_time()
    except (PathReplayContractError, OSError):
        gaps.append(_gap(PathReplayGapCode.TIME_AUTHORITY_UNAVAILABLE, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, "unavailable", gaps, artifact=None
        )
    current_replay = replace(trusted_edge_replay, evaluated_at=now)
    evaluated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _require_policy_runtime(path_policy, propagation_policy, expected_path_policy_sha256)
        _require_edge_replay_policy_pins(path_policy, current_replay)
    except PathReplayContractError:
        gaps.append(_gap(PathReplayGapCode.PATH_ROOT_OR_POLICY_MISMATCH, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )

    seeds = _derived_seed_ids(graph, path_policy)
    if not seeds:
        gaps.append(_gap(PathReplayGapCode.NO_REQUIRED_SEED_SCOPE, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )
    if len(seeds) > path_policy.maximum_required_seeds:
        gaps.append(_gap(PathReplayGapCode.REQUIRED_SEED_LIMIT_EXCEEDED, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )

    try:
        baseline = enumerate_structural_propagation(graph, seeds, propagation_policy)
    except PropagationEvaluationError:
        gaps.append(_gap(PathReplayGapCode.PROPAGATION_SCOPE_INCOMPLETE, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )
    baseline_blockers = _route_relevant_analysis_gaps(baseline)
    if baseline_blockers:
        gaps.append(
            _gap(
                PathReplayGapCode.PROPAGATION_SCOPE_INCOMPLETE,
                _identity_digest(baseline.model_dump(mode="json")),
                baseline_blockers,
            )
        )
    paths = _required_structural_paths(baseline, graph, path_policy)
    baseline_identity = _identity_digest(baseline.model_dump(mode="json"))
    material_outputs = tuple(sorted({path.target_id for path in paths}))
    if not material_outputs:
        gaps.append(
            _gap(PathReplayGapCode.NO_REQUIRED_MATERIAL_OUTPUT_SCOPE, baseline_identity)
        )
    if not paths:
        gaps.append(_gap(PathReplayGapCode.NO_REQUIRED_MATERIAL_PATH_SCOPE, baseline_identity))
    hop_count = sum(len(path.hops) for path in paths)
    if len(paths) > path_policy.maximum_required_paths:
        gaps.append(_gap(PathReplayGapCode.REQUIRED_PATH_LIMIT_EXCEEDED, baseline_identity))
    if hop_count > path_policy.maximum_required_hops:
        gaps.append(_gap(PathReplayGapCode.REQUIRED_HOP_LIMIT_EXCEEDED, baseline_identity))
    structural_keys = tuple(_static_path_structure(path) for path in paths)
    if len(set(structural_keys)) != len(paths):
        gaps.append(_gap(PathReplayGapCode.PATH_DUPLICATED, baseline_identity))
    if len(gaps) > 2:
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )

    required_edge_ids = tuple(
        sorted({hop.edge_id for path in paths for hop in path.hops})
    )
    if current_replay.required_edge_ids != required_edge_ids:
        gaps.append(
            _gap(
                PathReplayGapCode.EDGE_SCOPE_MISMATCH,
                required_edge_ids,
                current_replay.required_edge_ids,
            )
        )
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )
    try:
        edge_verification = current_replay.verify(graph)
    except (TrustedEdgeContractError, TrustedEdgeCompilationError):
        gaps.append(_gap(PathReplayGapCode.EDGE_REPLAY_CONTRACT_REJECTED, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, evaluated_at, gaps, artifact=None
        )
    if not edge_verification.coverage_complete:
        gaps.append(
            _gap(
                PathReplayGapCode.EDGE_REPLAY_INCOMPLETE,
                edge_verification.verification_sha256,
                tuple(
                    (item.edge_identity_sha256, item.code.value)
                    for item in edge_verification.rejections
                ),
            )
        )
        return _evaluation(
            graph, propagation_policy, path_policy, edge_verification.evaluated_at, gaps, None
        )
    if tuple(envelope.edge_id for envelope in edge_verification.accepted_envelopes) != (
        required_edge_ids
    ):
        gaps.append(
            _gap(
                PathReplayGapCode.EDGE_SCOPE_MISMATCH,
                required_edge_ids,
                tuple(
                    envelope.edge_id for envelope in edge_verification.accepted_envelopes
                ),
            )
        )
        return _evaluation(
            graph, propagation_policy, path_policy, edge_verification.evaluated_at, gaps, None
        )
    try:
        trusted = traverse_propagation(
            graph, seeds, propagation_policy, trusted_edge_replay=current_replay
        )
    except (
        PropagationEvaluationError,
        TrustedEdgeContractError,
        TrustedEdgeCompilationError,
    ):
        gaps.append(_gap(PathReplayGapCode.EDGE_REPLAY_CONTRACT_REJECTED, graph.graph_sha256))
        return _evaluation(
            graph, propagation_policy, path_policy, edge_verification.evaluated_at, gaps, None
        )
    trusted_paths = _required_paths(trusted, graph, path_policy)
    trusted_analysis_gaps = _route_relevant_analysis_gaps(trusted)
    if trusted_analysis_gaps:
        gaps.append(
            _gap(
                PathReplayGapCode.PROPAGATION_SCOPE_INCOMPLETE,
                trusted.result_sha256,
                trusted_analysis_gaps,
            )
        )
        return _evaluation(
            graph, propagation_policy, path_policy, edge_verification.evaluated_at, gaps, None
        )
    if tuple(_static_path_structure(path) for path in paths) != tuple(
        _static_path_structure(path) for path in trusted_paths
    ) or any(not path.local_edge_envelope_complete for path in trusted_paths):
        gaps.append(
            _gap(
                PathReplayGapCode.TRUSTED_PATH_SET_MISMATCH,
                baseline_identity,
                trusted.result_sha256,
            )
        )
        return _evaluation(
            graph, propagation_policy, path_policy, edge_verification.evaluated_at, gaps, None
        )

    scope_body = {
        "scope_mode": "ALL_REACHABLE_MATERIAL_PATHS",
        "seed_ids": list(seeds),
        "path_seed_ids": sorted({path.seed_id for path in trusted_paths}),
        "zero_material_path_seed_ids": sorted(
            set(seeds) - {path.seed_id for path in trusted_paths}
        ),
        "material_output_ids": list(material_outputs),
        "required_edge_ids": list(required_edge_ids),
        "required_path_sha256s": sorted(path.path_sha256 for path in trusted_paths),
        "path_count": len(trusted_paths),
        "hop_count": sum(len(path.hops) for path in trusted_paths),
    }
    scope = CompletePathScope.model_validate(
        {**scope_body, "scope_sha256": stable_sha256(scope_body)}
    )
    artifact_body = _artifact_body(
        graph,
        propagation_policy,
        path_policy,
        edge_verification,
        trusted,
        scope,
        trusted_paths,
    )
    artifact = CompleteGraphPathArtifact.model_validate(
        {**artifact_body, "artifact_sha256": stable_sha256(artifact_body)}
    )
    return _evaluation(
        graph,
        propagation_policy,
        path_policy,
        edge_verification.evaluated_at,
        gaps,
        artifact,
    )


def _artifact_body(
    graph: NormalizedGraph,
    propagation_policy: PropagationPolicy,
    path_policy: CompletePathReplayPolicy,
    edge_verification: TrustedEdgeVerification,
    propagation: PropagationResult,
    scope: CompletePathScope,
    paths: tuple[PropagationPathReceipt, ...],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "local_edge_envelope_path_coverage_complete": True,
        "project_id": graph.project_id,
        "source_snapshot": graph.source_snapshot,
        "source_graph_sha256": graph.source_graph_sha256,
        "normalized_graph_sha256": graph.graph_sha256,
        "ontology_id": graph.ontology_id,
        "ontology_version": graph.ontology_version,
        "ontology_sha256": graph.ontology_sha256,
        "profile_id": graph.profile_id,
        "profile_version": graph.profile_version,
        "profile_sha256": graph.profile_sha256,
        "propagation_policy_id": propagation_policy.policy_id,
        "propagation_policy_version": propagation_policy.policy_version,
        "propagation_policy_sha256": propagation_policy.sha256,
        "path_replay_policy_id": path_policy.policy_id,
        "path_replay_policy_version": path_policy.policy_version,
        "path_replay_policy_sha256": path_policy.sha256,
        "trusted_edge_policy_id": path_policy.trusted_edge_policy.policy_id,
        "trusted_edge_policy_version": path_policy.trusted_edge_policy.policy_version,
        "trusted_edge_policy_sha256": edge_verification.trust_policy_sha256,
        "trusted_edge_registry_id": path_policy.extractor_registry.registry_id,
        "trusted_edge_registry_version": path_policy.extractor_registry.registry_version,
        "trusted_edge_registry_sha256": edge_verification.extractor_registry_sha256,
        "trusted_edge_verification_sha256": edge_verification.verification_sha256,
        "evaluated_at": edge_verification.evaluated_at,
        "propagation_result_sha256": propagation.result_sha256,
        "required_scope": scope.model_dump(mode="json"),
        "paths": [path.model_dump(mode="json") for path in paths],
        "blocking_gap_codes": [
            "CHANGE_SEED_SCOPE_NOT_ATTESTED",
            "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
        ],
    }


def verify_complete_graph_path_replay(
    candidate: CompleteGraphPathArtifact,
    graph: NormalizedGraph,
    propagation_policy: PropagationPolicy,
    path_policy: CompletePathReplayPolicy,
    trusted_edge_replay: TrustedEdgeReplayInput,
    *,
    expected_path_policy_sha256: str = DEFAULT_PATH_REPLAY_POLICY_SHA256,
) -> CompletePathReplayEvaluation:
    """Recompile from current roots and reject any candidate path-set mutation."""

    expected = compile_complete_graph_path_replay(
        graph,
        propagation_policy,
        path_policy,
        trusted_edge_replay,
        expected_path_policy_sha256=expected_path_policy_sha256,
    )
    if expected.artifact is None:
        return expected
    gaps = list(expected.gaps)
    expected_artifact = expected.artifact
    expected_paths = tuple(expected_artifact.paths)
    expected_structures = tuple(_path_structure(path) for path in expected_paths)
    try:
        raw_candidate_paths = tuple(candidate.paths)
        candidate_structures = tuple(
            _path_structure(path) for path in raw_candidate_paths
        )
        if len(candidate_structures) != len(set(candidate_structures)):
            gaps.append(_gap(PathReplayGapCode.PATH_DUPLICATED, candidate_structures))
        if set(expected_structures) - set(candidate_structures):
            gaps.append(
                _gap(
                    PathReplayGapCode.PATH_REMOVED,
                    expected_structures,
                    candidate_structures,
                )
            )
        if set(candidate_structures) - set(expected_structures):
            gaps.append(
                _gap(
                    PathReplayGapCode.PATH_SUBSTITUTED,
                    expected_structures,
                    candidate_structures,
                )
            )
        if (
            set(candidate_structures) == set(expected_structures)
            and candidate_structures != expected_structures
        ):
            gaps.append(_gap(PathReplayGapCode.PATH_ORDER_CHANGED, candidate_structures))
    except (AttributeError, TypeError, ValueError):
        candidate_structures = ()
    try:
        validated = CompleteGraphPathArtifact.model_validate(
            candidate.model_dump(mode="json")
        )
    except (ValidationError, AttributeError, TypeError, ValueError):
        gaps.append(
            _gap(
                PathReplayGapCode.PATH_ARTIFACT_STRUCTURALLY_INVALID,
                expected.artifact.artifact_sha256,
            )
        )
        return _evaluation(
            graph,
            propagation_policy,
            path_policy,
            expected.evaluated_at,
            gaps,
            artifact=None,
        )
    candidate_paths = tuple(validated.paths)
    roots = (
        validated.project_id,
        validated.source_snapshot,
        validated.source_graph_sha256,
        validated.normalized_graph_sha256,
        validated.ontology_id,
        validated.ontology_version,
        validated.ontology_sha256,
        validated.profile_id,
        validated.profile_version,
        validated.profile_sha256,
        validated.propagation_policy_id,
        validated.propagation_policy_version,
        validated.propagation_policy_sha256,
        validated.path_replay_policy_id,
        validated.path_replay_policy_version,
        validated.path_replay_policy_sha256,
        validated.trusted_edge_policy_id,
        validated.trusted_edge_policy_version,
        validated.trusted_edge_policy_sha256,
        validated.trusted_edge_registry_id,
        validated.trusted_edge_registry_version,
        validated.trusted_edge_registry_sha256,
    )
    expected_roots = (
        expected_artifact.project_id,
        expected_artifact.source_snapshot,
        expected_artifact.source_graph_sha256,
        expected_artifact.normalized_graph_sha256,
        expected_artifact.ontology_id,
        expected_artifact.ontology_version,
        expected_artifact.ontology_sha256,
        expected_artifact.profile_id,
        expected_artifact.profile_version,
        expected_artifact.profile_sha256,
        expected_artifact.propagation_policy_id,
        expected_artifact.propagation_policy_version,
        expected_artifact.propagation_policy_sha256,
        expected_artifact.path_replay_policy_id,
        expected_artifact.path_replay_policy_version,
        expected_artifact.path_replay_policy_sha256,
        expected_artifact.trusted_edge_policy_id,
        expected_artifact.trusted_edge_policy_version,
        expected_artifact.trusted_edge_policy_sha256,
        expected_artifact.trusted_edge_registry_id,
        expected_artifact.trusted_edge_registry_version,
        expected_artifact.trusted_edge_registry_sha256,
    )
    if roots != expected_roots:
        gaps.append(_gap(PathReplayGapCode.PATH_ROOT_OR_POLICY_MISMATCH, roots))
    if tuple(_path_structure(path) for path in candidate_paths) != expected_structures:
        gaps.append(
            _gap(
                PathReplayGapCode.PATH_HOP_TAMPERED,
                tuple(_path_structure(path) for path in candidate_paths),
            )
        )
    scope_identity = (
        validated.required_scope.seed_ids,
        validated.required_scope.path_seed_ids,
        validated.required_scope.zero_material_path_seed_ids,
        validated.required_scope.material_output_ids,
        validated.required_scope.required_edge_ids,
        validated.required_scope.path_count,
        validated.required_scope.hop_count,
    )
    expected_scope_identity = (
        expected_artifact.required_scope.seed_ids,
        expected_artifact.required_scope.path_seed_ids,
        expected_artifact.required_scope.zero_material_path_seed_ids,
        expected_artifact.required_scope.material_output_ids,
        expected_artifact.required_scope.required_edge_ids,
        expected_artifact.required_scope.path_count,
        expected_artifact.required_scope.hop_count,
    )
    if scope_identity != expected_scope_identity:
        gaps.append(_gap(PathReplayGapCode.PATH_SUBSTITUTED, scope_identity))
    candidate_time = _parse_timestamp(validated.evaluated_at)
    expected_time = _parse_timestamp(expected_artifact.evaluated_at)
    if candidate_time > expected_time:
        gaps.append(_gap(PathReplayGapCode.PATH_ROOT_OR_POLICY_MISMATCH, validated.evaluated_at))
    static_match = not (
        {gap.code for gap in gaps}
        - {
            PathReplayGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED,
            PathReplayGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED,
        }
    )
    if candidate_time == expected_time and (
        validated.artifact_sha256 != expected_artifact.artifact_sha256
    ):
        gaps.append(
            _gap(
                PathReplayGapCode.PATH_ARTIFACT_DIGEST_MISMATCH,
                validated.artifact_sha256,
            )
        )
    if not static_match or candidate_time > expected_time or (
        candidate_time == expected_time and validated != expected_artifact
    ):
        return _evaluation(
            graph,
            propagation_policy,
            path_policy,
            expected.evaluated_at,
            gaps,
            artifact=None,
        )
    # A still-valid historical candidate refreshes to the current replay receipt. Its
    # evaluation-derived verification/path/artifact hashes are intentionally not authority.
    return expected
