from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.change_verification import (
    DEFAULT_VERIFIED_CHANGE_POLICY_SHA256,
    GitChangeEntry,
    LocalGitChangeProducer,
    VerifiedChangeSet,
)
from neo_sf_q_intel.edge_envelope import TrustedEdgeReplayInput, stable_sha256
from neo_sf_q_intel.ontology import (
    CanonicalOntology,
    NormalizedGraph,
    SourceGraphProfile,
    contract_sha256,
)
from neo_sf_q_intel.path_replay import (
    DEFAULT_PATH_REPLAY_POLICY_SHA256,
    CompleteGraphPathArtifact,
    CompletePathReplayPolicy,
    verify_complete_graph_path_replay,
)
from neo_sf_q_intel.propagation import PropagationPolicy


class ChangeSeedContractError(RuntimeError):
    """The independently pinned change-seed contract is unavailable."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ContractPin(_Model):
    contract_id: str = Field(alias="contractId", min_length=1, max_length=200)
    contract_version: str = Field(alias="contractVersion", min_length=1, max_length=40)
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[a-f0-9]{64}$")


class MapperPin(_Model):
    mapper_id: str = Field(alias="mapperId", min_length=1, max_length=200)
    mapper_version: str = Field(alias="mapperVersion", min_length=1, max_length=40)
    implementation_locator: str = Field(alias="implementationLocator", min_length=1)
    implementation_sha256: str = Field(
        alias="implementationSha256", pattern=r"^[a-f0-9]{64}$"
    )


class ChangeSeedPolicy(_Model):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_policy: ContractPin = Field(alias="verifiedChangePolicy")
    ontology: ContractPin
    source_profile: ContractPin = Field(alias="sourceProfile")
    propagation_policy: ContractPin = Field(alias="propagationPolicy")
    path_replay_policy: ContractPin = Field(alias="pathReplayPolicy")
    trusted_edge_policy: ContractPin = Field(alias="trustedEdgePolicy")
    extractor_registry: ContractPin = Field(alias="extractorRegistry")
    mapper: MapperPin
    artifact_anchor_class: str = Field(alias="artifactAnchorClass", min_length=1)
    locator_attribute: str = Field(alias="locatorAttribute", min_length=1)
    declaration_relation_class: str = Field(alias="declarationRelationClass", min_length=1)
    declaration_direction: Literal["TARGET_TO_SOURCE"] = Field(alias="declarationDirection")
    include_artifact_anchor: Literal[True] = Field(alias="includeArtifactAnchor")
    graph_input_binding: Literal["REQUIRES_PRODUCER_RECEIPT"] = Field(
        alias="graphInputBinding"
    )
    maximum_changes: int = Field(alias="maximumChanges", ge=1, le=100_000)
    maximum_graph_nodes: int = Field(alias="maximumGraphNodes", ge=1, le=1_000_000)
    maximum_locator_bytes: int = Field(alias="maximumLocatorBytes", ge=32, le=32768)
    maximum_seed_bindings: int = Field(alias="maximumSeedBindings", ge=1, le=1_000_000)
    maximum_affected_paths: int = Field(alias="maximumAffectedPaths", ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_policy(self) -> ChangeSeedPolicy:
        for value in (
            self.schema_version,
            self.policy_version,
            self.mapper.mapper_version,
            self.verified_change_policy.contract_version,
            self.ontology.contract_version,
            self.source_profile.contract_version,
            self.propagation_policy.contract_version,
            self.path_replay_policy.contract_version,
            self.trusted_edge_policy.contract_version,
            self.extractor_registry.contract_version,
        ):
            _require_semver(value)
        _require_relative_locator(self.mapper.implementation_locator)
        if self.maximum_seed_bindings < self.maximum_changes:
            raise ValueError("Seed-binding capacity must cover the change capacity")
        return self


class ChangeSeedGapCode(StrEnum):
    CHANGE_SEED_SCOPE_NOT_ATTESTED = "CHANGE_SEED_SCOPE_NOT_ATTESTED"
    GRAPH_INPUT_TREE_NOT_ATTESTED = "GRAPH_INPUT_TREE_NOT_ATTESTED"
    UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED = "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"
    CANDIDATE_BUILD_NOT_VERIFIED = "CANDIDATE_BUILD_NOT_VERIFIED"
    CONFLICT_SCOPE_NOT_ATTESTED = "CONFLICT_SCOPE_NOT_ATTESTED"
    DEPLOYMENT_NOT_ATTESTED = "DEPLOYMENT_NOT_ATTESTED"
    GIT_COMMIT_SIGNATURE_NOT_ATTESTED = "GIT_COMMIT_SIGNATURE_NOT_ATTESTED"
    HUMAN_APPROVAL_SCOPE_NOT_ATTESTED = "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED"
    RELEASE_EVIDENCE_MODEL_INCOMPLETE = "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    REPOSITORY_ORIGIN_NOT_ATTESTED = "REPOSITORY_ORIGIN_NOT_ATTESTED"
    RISK_FACTORS_NOT_ATTESTED = "RISK_FACTORS_NOT_ATTESTED"
    TEST_EXECUTION_SCOPE_NOT_ATTESTED = "TEST_EXECUTION_SCOPE_NOT_ATTESTED"
    TEST_OBLIGATION_SCOPE_NOT_ATTESTED = "TEST_OBLIGATION_SCOPE_NOT_ATTESTED"
    CHANGE_CAPTURE_REPLAY_FAILED = "CHANGE_CAPTURE_REPLAY_FAILED"
    GRAPH_REPLAY_FAILED = "GRAPH_REPLAY_FAILED"
    PATH_REPLAY_FAILED = "PATH_REPLAY_FAILED"
    PROJECT_SCOPE_MISMATCH = "PROJECT_SCOPE_MISMATCH"
    POLICY_ROOT_MISMATCH = "POLICY_ROOT_MISMATCH"
    MAPPER_IMPLEMENTATION_MISMATCH = "MAPPER_IMPLEMENTATION_MISMATCH"
    MAPPING_ARTIFACT_TAMPERED = "MAPPING_ARTIFACT_TAMPERED"
    MAPPING_ARTIFACT_EXPIRED = "MAPPING_ARTIFACT_EXPIRED"
    MAPPING_ARTIFACT_FROM_FUTURE = "MAPPING_ARTIFACT_FROM_FUTURE"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    UNMAPPED_CHANGED_ARTIFACT = "UNMAPPED_CHANGED_ARTIFACT"
    AMBIGUOUS_CHANGED_ARTIFACT = "AMBIGUOUS_CHANGED_ARTIFACT"
    DELETED_ARTIFACT_REQUIRES_BASE_GRAPH = "DELETED_ARTIFACT_REQUIRES_BASE_GRAPH"
    UNSAFE_GRAPH_LOCATOR = "UNSAFE_GRAPH_LOCATOR"
    LOCATOR_ALIAS_COLLISION = "LOCATOR_ALIAS_COLLISION"
    SEED_OUTSIDE_COMPLETE_REPLAY = "SEED_OUTSIDE_COMPLETE_REPLAY"
    ZERO_MATERIAL_PATH_FOR_CHANGED_SEED = "ZERO_MATERIAL_PATH_FOR_CHANGED_SEED"
    TIME_AUTHORITY_UNAVAILABLE = "TIME_AUTHORITY_UNAVAILABLE"


_PERMANENT_GAPS = (
    ChangeSeedGapCode.CANDIDATE_BUILD_NOT_VERIFIED,
    ChangeSeedGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED,
    ChangeSeedGapCode.CONFLICT_SCOPE_NOT_ATTESTED,
    ChangeSeedGapCode.DEPLOYMENT_NOT_ATTESTED,
    ChangeSeedGapCode.GIT_COMMIT_SIGNATURE_NOT_ATTESTED,
    ChangeSeedGapCode.GRAPH_INPUT_TREE_NOT_ATTESTED,
    ChangeSeedGapCode.HUMAN_APPROVAL_SCOPE_NOT_ATTESTED,
    ChangeSeedGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE,
    ChangeSeedGapCode.REPOSITORY_ORIGIN_NOT_ATTESTED,
    ChangeSeedGapCode.RISK_FACTORS_NOT_ATTESTED,
    ChangeSeedGapCode.TEST_EXECUTION_SCOPE_NOT_ATTESTED,
    ChangeSeedGapCode.TEST_OBLIGATION_SCOPE_NOT_ATTESTED,
    ChangeSeedGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED,
)


class ChangeSeedGap(_Model):
    code: ChangeSeedGapCode
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["RELEASE_ONLY"] = "RELEASE_ONLY"
    blocking: Literal[True] = True


class ChangedArtifactSeedBinding(_Model):
    change_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    change: GitChangeEntry
    anchor_node_id: str = Field(min_length=1)
    declared_entity_ids: tuple[str, ...]
    seed_ids: tuple[str, ...] = Field(min_length=1)
    affected_path_identity_sha256s: tuple[str, ...] = Field(min_length=1)
    affected_path_sha256s: tuple[str, ...] = Field(min_length=1)
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_binding(self) -> ChangedArtifactSeedBinding:
        if self.change_identity_sha256 != _change_identity(self.change):
            raise ValueError("Change identity differs from its complete bytewise change")
        if self.seed_ids != tuple(sorted(set(self.seed_ids))):
            raise ValueError("Seed IDs must be sorted and unique")
        if self.declared_entity_ids != tuple(sorted(set(self.declared_entity_ids))):
            raise ValueError("Declared entity IDs must be sorted and unique")
        if self.affected_path_sha256s != tuple(sorted(set(self.affected_path_sha256s))):
            raise ValueError("Affected paths must be sorted and unique")
        if self.affected_path_identity_sha256s != tuple(
            sorted(set(self.affected_path_identity_sha256s))
        ):
            raise ValueError("Affected path identities must be sorted and unique")
        _verify_digest(self, "binding_sha256")
        return self


class ChangeSeedMappingArtifact(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    graph_input_tree_attested: Literal[False] = False
    change_seed_scope_attested: Literal[False] = False
    local_mapping_complete: Literal[True] = True
    project_id: str
    repository_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_commit_oid: str
    base_tree_oid: str
    candidate_input_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_build_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_source_snapshot: str
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propagation_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_replay_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    complete_path_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: str
    policy_version: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapper_id: str
    mapper_version: str
    mapper_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    valid_until: str
    change_ids: tuple[str, ...] = Field(min_length=1)
    seed_ids: tuple[str, ...] = Field(min_length=1)
    affected_path_identity_sha256s: tuple[str, ...] = Field(min_length=1)
    affected_path_sha256s: tuple[str, ...] = Field(min_length=1)
    bindings: tuple[ChangedArtifactSeedBinding, ...] = Field(min_length=1)
    blocking_gap_codes: tuple[ChangeSeedGapCode, ...] = _PERMANENT_GAPS
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> ChangeSeedMappingArtifact:
        _parse_timestamp(self.evaluated_at)
        if _parse_timestamp(self.valid_until) <= _parse_timestamp(self.evaluated_at):
            raise ValueError("Change-seed artifact is not fresh")
        binding_ids = tuple(item.change_identity_sha256 for item in self.bindings)
        if binding_ids != tuple(sorted(set(binding_ids))) or binding_ids != self.change_ids:
            raise ValueError("Bindings do not exactly partition the changed artifacts")
        binding_seeds = {seed for item in self.bindings for seed in item.seed_ids}
        if self.seed_ids != tuple(sorted(binding_seeds)):
            raise ValueError("Artifact seed union differs from its bindings")
        if self.affected_path_sha256s != tuple(
            sorted({path for item in self.bindings for path in item.affected_path_sha256s})
        ):
            raise ValueError("Artifact path union differs from its bindings")
        if self.affected_path_identity_sha256s != tuple(
            sorted(
                {
                    path
                    for item in self.bindings
                    for path in item.affected_path_identity_sha256s
                }
            )
        ):
            raise ValueError("Artifact stable path union differs from its bindings")
        if self.blocking_gap_codes != _PERMANENT_GAPS:
            raise ValueError("Permanent release gaps cannot be omitted")
        _verify_digest(self, "artifact_sha256")
        return self


class ChangeSeedEvaluation(_Model):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    local_mapping_complete: bool
    graph_input_tree_attested: Literal[False] = False
    change_seed_scope_attested: Literal[False] = False
    project_id: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    artifact: ChangeSeedMappingArtifact | None = None
    gaps: tuple[ChangeSeedGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> ChangeSeedEvaluation:
        if self.local_mapping_complete is not (self.artifact is not None):
            raise ValueError("Local mapping completeness differs from artifact presence")
        keys = tuple((item.code.value, item.identity_sha256) for item in self.gaps)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Change-seed gaps must be sorted and unique")
        if not set(_PERMANENT_GAPS).issubset(item.code for item in self.gaps):
            raise ValueError("Permanent release gaps cannot be omitted")
        _verify_digest(self, "evaluation_sha256")
        return self


DEFAULT_CHANGE_SEED_POLICY_SHA256 = (
    "0425dd77b55b90178902b61ac893bd6f800aee2f019386a1efb5836e46c9a8d3"
)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WINDOWS_DEVICE = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


def _require_semver(value: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError("Version must use semantic versioning")


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be canonical UTC seconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _require_relative_locator(value: str, maximum_bytes: int = 4096) -> str:
    if (
        not value
        or "\\" in value
        or ":" in value
        or unicodedata.normalize("NFC", value) != value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Repository locator is unsafe")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError("Repository locator exceeds policy capacity")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Repository locator is unsafe")
    if any(part.endswith((".", " ")) or _WINDOWS_DEVICE.fullmatch(part) for part in path.parts):
        raise ValueError("Repository locator is unsafe")
    return path.as_posix()


def _verify_digest(model: BaseModel, field: str) -> None:
    body = model.model_dump(mode="json")
    declared = body.pop(field)
    if stable_sha256(body) != declared:
        raise ValueError(f"{field} differs from canonical content")


def _identity(*values: Any) -> str:
    return stable_sha256(values)


def _change_identity(change: GitChangeEntry) -> str:
    return stable_sha256(change.model_dump(mode="json"))


def _stable_path_identity(path: Any) -> str:
    return stable_sha256(
        {
            "seed_id": path.seed_id,
            "target_id": path.target_id,
            "hops": [
                {
                    "edge_id": hop.edge_id,
                    "direction": hop.direction.value,
                    "traversal_from_id": hop.traversal_from_id,
                    "traversal_to_id": hop.traversal_to_id,
                    "edge_source_id": hop.edge_source_id,
                    "edge_target_id": hop.edge_target_id,
                    "relation_class": hop.relation_class,
                }
                for hop in path.hops
            ],
        }
    )


def _gap(code: ChangeSeedGapCode, *identity: Any) -> ChangeSeedGap:
    return ChangeSeedGap(code=code, identity_sha256=_identity(code.value, *identity))


def _implementation_sha256(content: bytes) -> str:
    normalized = re.sub(
        rb"DEFAULT_CHANGE_SEED_POLICY_SHA256\s*=\s*"
        rb'(?:"[a-f0-9]{64}"|\(\s*"[a-f0-9]{64}"\s*\))',
        b'DEFAULT_CHANGE_SEED_POLICY_SHA256 = (\n    "' + (b"0" * 64) + b'"\n)',
        content,
        count=1,
    )
    return hashlib.sha256(normalized).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChangeSeedContractError("Policy contains duplicate keys")
        result[key] = value
    return result


def load_change_seed_policy(
    path: Path,
    *,
    implementation_root: Path,
    expected_sha256: str = DEFAULT_CHANGE_SEED_POLICY_SHA256,
) -> ChangeSeedPolicy:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except ChangeSeedContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChangeSeedContractError("Change-seed policy cannot be loaded") from exc
    if not isinstance(document, dict) or document.get("sha256") != contract_sha256(document):
        raise ChangeSeedContractError("Change-seed policy self-hash is invalid")
    if document.get("sha256") != expected_sha256:
        raise ChangeSeedContractError("Change-seed policy is not externally pinned")
    try:
        policy = ChangeSeedPolicy.model_validate(document)
    except ValidationError as exc:
        raise ChangeSeedContractError("Change-seed policy is invalid") from exc
    implementation = (
        implementation_root.resolve() / policy.mapper.implementation_locator
    ).resolve()
    try:
        implementation.relative_to(implementation_root.resolve())
    except ValueError as exc:
        raise ChangeSeedContractError("Mapper implementation escapes its root") from exc
    loaded = Path(inspect.getsourcefile(compile_change_seed_mapping) or "").resolve()
    if implementation != loaded:
        raise ChangeSeedContractError("Loaded mapper differs from its policy locator")
    try:
        digest = _implementation_sha256(implementation.read_bytes())
    except OSError as exc:
        raise ChangeSeedContractError("Mapper implementation is unavailable") from exc
    if digest != policy.mapper.implementation_sha256:
        raise ChangeSeedContractError("Mapper implementation digest is invalid")
    return policy


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _runtime_policy_valid(
    policy: ChangeSeedPolicy,
    producer: LocalGitChangeProducer,
    ontology: CanonicalOntology,
    profile: SourceGraphProfile,
    propagation_policy: PropagationPolicy,
    path_policy: CompletePathReplayPolicy,
    trusted_edge_replay: TrustedEdgeReplayInput,
) -> bool:
    body = policy.model_dump(mode="json", by_alias=True)
    loaded = Path(inspect.getsourcefile(compile_change_seed_mapping) or "").resolve()
    try:
        implementation_digest = _implementation_sha256(loaded.read_bytes())
    except OSError:
        return False
    pins = (
        policy.verified_change_policy.contract_id == producer.policy.policy_id,
        policy.verified_change_policy.contract_version
        == producer.policy.policy_version,
        policy.verified_change_policy.contract_sha256
        == producer.policy.sha256
        == DEFAULT_VERIFIED_CHANGE_POLICY_SHA256,
        policy.ontology.contract_id == ontology.ontology_id,
        policy.ontology.contract_version == ontology.ontology_version,
        policy.ontology.contract_sha256 == ontology.sha256,
        policy.source_profile.contract_id == profile.profile_id,
        policy.source_profile.contract_version == profile.profile_version,
        policy.source_profile.contract_sha256 == profile.sha256,
        policy.propagation_policy.contract_id == propagation_policy.policy_id,
        policy.propagation_policy.contract_version == propagation_policy.policy_version,
        policy.propagation_policy.contract_sha256 == propagation_policy.sha256,
        policy.path_replay_policy.contract_id == path_policy.policy_id,
        policy.path_replay_policy.contract_version == path_policy.policy_version,
        policy.path_replay_policy.contract_sha256 == path_policy.sha256,
        policy.trusted_edge_policy.contract_id
        == trusted_edge_replay.policy.policy_id,
        policy.trusted_edge_policy.contract_version
        == trusted_edge_replay.policy.policy_version,
        policy.trusted_edge_policy.contract_sha256
        == trusted_edge_replay.policy.sha256,
        policy.extractor_registry.contract_id
        == trusted_edge_replay.registry.registry_id,
        policy.extractor_registry.contract_version
        == trusted_edge_replay.registry.registry_version,
        policy.extractor_registry.contract_sha256
        == trusted_edge_replay.registry.sha256,
        policy.artifact_anchor_class in set(profile.node_mapping.values()),
        policy.declaration_relation_class in set(profile.relation_mapping.values()),
    )
    return bool(
        contract_sha256(body) == policy.sha256
        and policy.sha256 == DEFAULT_CHANGE_SEED_POLICY_SHA256
        and implementation_digest == policy.mapper.implementation_sha256
        and all(pins)
    )


def _planned_gaps() -> list[ChangeSeedGap]:
    return [_gap(code) for code in _PERMANENT_GAPS]


def _evaluation(
    policy: ChangeSeedPolicy,
    project_id: str,
    evaluated_at: str,
    gaps: list[ChangeSeedGap],
    artifact: ChangeSeedMappingArtifact | None,
) -> ChangeSeedEvaluation:
    ordered = tuple(sorted(set(gaps), key=lambda item: (item.code.value, item.identity_sha256)))
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "local_mapping_complete": artifact is not None,
        "graph_input_tree_attested": False,
        "change_seed_scope_attested": False,
        "project_id": project_id,
        "policy_sha256": policy.sha256,
        "evaluated_at": evaluated_at,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "gaps": [item.model_dump(mode="json") for item in ordered],
    }
    return ChangeSeedEvaluation.model_validate(
        {**body, "evaluation_sha256": stable_sha256(body)}
    )


def _locator_index(
    graph: NormalizedGraph, policy: ChangeSeedPolicy
) -> tuple[dict[str, list[str]], set[str], set[str]]:
    by_locator: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    collisions: set[str] = set()
    unsafe: set[str] = set()
    for node in graph.nodes:
        if node.canonical_class != policy.artifact_anchor_class:
            continue
        raw_locator = node.attributes.get(policy.locator_attribute)
        if not isinstance(raw_locator, str):
            continue
        try:
            locator = _require_relative_locator(raw_locator, policy.maximum_locator_bytes)
        except ValueError:
            unsafe.add(_identity("unsafe", node.node_id, raw_locator))
            continue
        alias = unicodedata.normalize("NFC", locator).casefold()
        prior = aliases.setdefault(alias, locator)
        if prior != locator:
            collisions.add(_identity("alias", prior, locator))
        by_locator.setdefault(locator, []).append(node.node_id)
    for values in by_locator.values():
        values.sort()
    return by_locator, collisions, unsafe


def _declared_entity_ids(
    anchor_id: str, graph: NormalizedGraph, policy: ChangeSeedPolicy
) -> tuple[str, ...]:
    return tuple(sorted({
        edge.source_id
        for edge in graph.edges
        if edge.canonical_relation == policy.declaration_relation_class
        and edge.target_id == anchor_id
    }))


def _seed_ids(anchor_id: str, policy: ChangeSeedPolicy) -> tuple[str, ...]:
    # The source-artifact is the propagation seed. Its replayed TARGET_TO_SOURCE
    # declared-in hop reaches every bound entity without manufacturing zero-hop
    # path receipts for those entities.
    return (anchor_id,) if policy.include_artifact_anchor else ()


@dataclass(frozen=True, slots=True)
class ChangeSeedInputs:
    repository_hint: Path
    candidate: VerifiedChangeSet
    producer: LocalGitChangeProducer
    graph: NormalizedGraph
    ontology: CanonicalOntology
    profile: SourceGraphProfile
    propagation_policy: PropagationPolicy
    path_policy: CompletePathReplayPolicy
    trusted_edge_replay: TrustedEdgeReplayInput
    complete_path_candidate: CompleteGraphPathArtifact


def compile_change_seed_mapping(
    inputs: ChangeSeedInputs,
    policy: ChangeSeedPolicy,
    *,
    expected_policy_sha256: str = DEFAULT_CHANGE_SEED_POLICY_SHA256,
) -> ChangeSeedEvaluation:
    """Map every current verified Git change to graph seeds and replayed path receipts.

    This R0.4b foundation deliberately retains graph-input and change-seed attestation
    gaps until a graph producer proves that its snapshot came from the same Git tree.
    """

    gaps = _planned_gaps()
    producer_project_id = getattr(inputs.producer, "project_id", "unavailable")
    if type(inputs.producer) is not LocalGitChangeProducer:
        return _evaluation(
            policy,
            producer_project_id,
            "unavailable",
            [*gaps, _gap(ChangeSeedGapCode.CHANGE_CAPTURE_REPLAY_FAILED, "producer-type")],
            None,
        )
    try:
        now = _utc_now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        now = now.astimezone(UTC).replace(microsecond=0)
        evaluated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError):
        return _evaluation(
            policy,
            inputs.producer.project_id,
            "unavailable",
            [*gaps, _gap(ChangeSeedGapCode.TIME_AUTHORITY_UNAVAILABLE)],
            None,
        )
    if expected_policy_sha256 != DEFAULT_CHANGE_SEED_POLICY_SHA256 or not _runtime_policy_valid(
        policy,
        inputs.producer,
        inputs.ontology,
        inputs.profile,
        inputs.propagation_policy,
        inputs.path_policy,
        inputs.trusted_edge_replay,
    ):
        return _evaluation(
            policy,
            inputs.producer.project_id,
            evaluated_at,
            [*gaps, _gap(ChangeSeedGapCode.POLICY_ROOT_MISMATCH)],
            None,
        )
    change_replay = inputs.producer.verify(inputs.candidate, inputs.repository_hint)
    if change_replay.artifact is None:
        return _evaluation(
            policy,
            inputs.producer.project_id,
            evaluated_at,
            [
                *gaps,
                _gap(
                    ChangeSeedGapCode.CHANGE_CAPTURE_REPLAY_FAILED,
                    change_replay.evaluation_sha256,
                ),
            ],
            None,
        )
    verified = change_replay.artifact
    graph = inputs.graph
    if graph.project_id != verified.project_id or inputs.producer.project_id != verified.project_id:
        return _evaluation(
            policy,
            verified.project_id,
            evaluated_at,
            [*gaps, _gap(ChangeSeedGapCode.PROJECT_SCOPE_MISMATCH, graph.project_id)],
            None,
        )
    if (
        len(verified.changes) > policy.maximum_changes
        or len(graph.nodes) > policy.maximum_graph_nodes
    ):
        return _evaluation(
            policy,
            verified.project_id,
            evaluated_at,
            [
                *gaps,
                _gap(
                    ChangeSeedGapCode.CAPACITY_EXCEEDED,
                    len(verified.changes),
                    len(graph.nodes),
                ),
            ],
            None,
        )

    path_replay = verify_complete_graph_path_replay(
        inputs.complete_path_candidate,
        graph,
        inputs.propagation_policy,
        inputs.path_policy,
        inputs.trusted_edge_replay,
        expected_path_policy_sha256=DEFAULT_PATH_REPLAY_POLICY_SHA256,
    )
    if path_replay.artifact is None:
        return _evaluation(
            policy,
            verified.project_id,
            evaluated_at,
            [*gaps, _gap(ChangeSeedGapCode.PATH_REPLAY_FAILED, path_replay.evaluation_sha256)],
            None,
        )
    complete_paths = path_replay.artifact
    by_locator, locator_collisions, unsafe_locators = _locator_index(graph, policy)
    if locator_collisions:
        gaps.extend(
            _gap(ChangeSeedGapCode.LOCATOR_ALIAS_COLLISION, identity)
            for identity in sorted(locator_collisions)
        )
    if unsafe_locators:
        gaps.extend(
            _gap(ChangeSeedGapCode.UNSAFE_GRAPH_LOCATOR, identity)
            for identity in sorted(unsafe_locators)
        )

    complete_seed_scope = set(complete_paths.required_scope.seed_ids)
    paths_by_seed: dict[str, list[str]] = {}
    stable_paths_by_seed: dict[str, list[str]] = {}
    for path in complete_paths.paths:
        paths_by_seed.setdefault(path.seed_id, []).append(path.path_sha256)
        stable_paths_by_seed.setdefault(path.seed_id, []).append(_stable_path_identity(path))
    bindings: list[ChangedArtifactSeedBinding] = []
    for change in verified.changes:
        identity = _change_identity(change)
        if change.operation.value == "DELETE":
            gaps.append(
                _gap(ChangeSeedGapCode.DELETED_ARTIFACT_REQUIRES_BASE_GRAPH, identity)
            )
            continue
        anchors = by_locator.get(change.path, [])
        if not anchors:
            gaps.append(_gap(ChangeSeedGapCode.UNMAPPED_CHANGED_ARTIFACT, identity))
            continue
        if len(anchors) != 1:
            gaps.append(
                _gap(ChangeSeedGapCode.AMBIGUOUS_CHANGED_ARTIFACT, identity, anchors)
            )
            continue
        anchor = anchors[0]
        declared_entities = _declared_entity_ids(anchor, graph, policy)
        seeds = _seed_ids(anchor, policy)
        if len(seeds) > policy.maximum_seed_bindings:
            gaps.append(_gap(ChangeSeedGapCode.CAPACITY_EXCEEDED, identity, len(seeds)))
            continue
        outside = tuple(sorted(set(seeds) - complete_seed_scope))
        if outside:
            gaps.append(_gap(ChangeSeedGapCode.SEED_OUTSIDE_COMPLETE_REPLAY, identity, outside))
            continue
        zero = tuple(seed for seed in seeds if not paths_by_seed.get(seed))
        if zero:
            gaps.append(
                _gap(ChangeSeedGapCode.ZERO_MATERIAL_PATH_FOR_CHANGED_SEED, identity, zero)
            )
            continue
        affected = tuple(
            sorted({path for seed in seeds for path in paths_by_seed.get(seed, [])})
        )
        affected_stable = tuple(
            sorted(
                {
                    path
                    for seed in seeds
                    for path in stable_paths_by_seed.get(seed, [])
                }
            )
        )
        if len(affected) > policy.maximum_affected_paths:
            gaps.append(_gap(ChangeSeedGapCode.CAPACITY_EXCEEDED, identity, len(affected)))
            continue
        body = {
            "change_identity_sha256": identity,
            "change": change.model_dump(mode="json"),
            "anchor_node_id": anchor,
            "declared_entity_ids": list(declared_entities),
            "seed_ids": list(seeds),
            "affected_path_identity_sha256s": list(affected_stable),
            "affected_path_sha256s": list(affected),
        }
        bindings.append(
            ChangedArtifactSeedBinding.model_validate(
                {**body, "binding_sha256": stable_sha256(body)}
            )
        )

    if sum(len(item.seed_ids) for item in bindings) > policy.maximum_seed_bindings:
        gaps.append(
            _gap(
                ChangeSeedGapCode.CAPACITY_EXCEEDED,
                "seed-bindings",
                sum(len(item.seed_ids) for item in bindings),
            )
        )
    if len({path for item in bindings for path in item.affected_path_sha256s}) > (
        policy.maximum_affected_paths
    ):
        gaps.append(
            _gap(ChangeSeedGapCode.CAPACITY_EXCEEDED, "affected-path-union")
        )

    dynamic_codes = {gap.code for gap in gaps} - set(_PERMANENT_GAPS)
    if dynamic_codes or len(bindings) != len(verified.changes):
        return _evaluation(policy, verified.project_id, evaluated_at, gaps, None)
    bindings.sort(key=lambda item: item.change_identity_sha256)
    change_ids = tuple(_change_identity(change) for change in verified.changes)
    change_ids = tuple(sorted(change_ids))
    if tuple(item.change_identity_sha256 for item in bindings) != change_ids:
        return _evaluation(
            policy,
            verified.project_id,
            evaluated_at,
            [*gaps, _gap(ChangeSeedGapCode.CHANGE_CAPTURE_REPLAY_FAILED, change_ids)],
            None,
        )
    try:
        final_now = _utc_now()
        if final_now.tzinfo is None or final_now.utcoffset() is None:
            raise ValueError
        final_now = final_now.astimezone(UTC).replace(microsecond=0)
    except (OSError, ValueError):
        return _evaluation(
            policy,
            verified.project_id,
            "unavailable",
            [*gaps, _gap(ChangeSeedGapCode.TIME_AUTHORITY_UNAVAILABLE, "final")],
            None,
        )
    evaluated_at = final_now.strftime("%Y-%m-%dT%H:%M:%SZ")
    nested_observed = (
        _parse_timestamp(verified.observed_at),
        _parse_timestamp(complete_paths.evaluated_at),
    )
    if any(observed > final_now for observed in nested_observed):
        return _evaluation(
            policy,
            verified.project_id,
            evaluated_at,
            [*gaps, _gap(ChangeSeedGapCode.TIME_AUTHORITY_UNAVAILABLE, "nested-future")],
            None,
        )
    valid_until = min(
        _parse_timestamp(verified.valid_until),
        *(
            _parse_timestamp(hop.trusted_edge_valid_until or "")
            for path in complete_paths.paths
            for hop in path.hops
        ),
    )
    if valid_until <= final_now:
        return _evaluation(
            policy,
            verified.project_id,
            evaluated_at,
            [*gaps, _gap(ChangeSeedGapCode.PATH_REPLAY_FAILED, "expired")],
            None,
        )
    artifact_body = {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "graph_input_tree_attested": False,
        "change_seed_scope_attested": False,
        "local_mapping_complete": True,
        "project_id": verified.project_id,
        "repository_identity_sha256": verified.repository_identity_sha256,
        "source_snapshot_sha256": verified.source_snapshot_sha256,
        "base_commit_oid": verified.base_commit_oid,
        "base_tree_oid": verified.base_tree_oid,
        "candidate_input_tree_sha256": verified.candidate_input_tree_sha256,
        "candidate_build_input_sha256": verified.candidate_build_input_sha256,
        "verified_change_manifest_sha256": verified.manifest_sha256,
        "source_graph_sha256": graph.source_graph_sha256,
        "graph_source_snapshot": graph.source_snapshot,
        "normalized_graph_sha256": graph.graph_sha256,
        "ontology_sha256": graph.ontology_sha256,
        "profile_sha256": graph.profile_sha256,
        "propagation_policy_sha256": inputs.propagation_policy.sha256,
        "path_replay_policy_sha256": inputs.path_policy.sha256,
        "complete_path_artifact_sha256": complete_paths.artifact_sha256,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_sha256": policy.sha256,
        "mapper_id": policy.mapper.mapper_id,
        "mapper_version": policy.mapper.mapper_version,
        "mapper_implementation_sha256": policy.mapper.implementation_sha256,
        "evaluated_at": evaluated_at,
        "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "change_ids": list(change_ids),
        "seed_ids": sorted({seed for item in bindings for seed in item.seed_ids}),
        "affected_path_identity_sha256s": sorted(
            {
                path
                for item in bindings
                for path in item.affected_path_identity_sha256s
            }
        ),
        "affected_path_sha256s": sorted(
            {path for item in bindings for path in item.affected_path_sha256s}
        ),
        "bindings": [item.model_dump(mode="json") for item in bindings],
        "blocking_gap_codes": [code.value for code in _PERMANENT_GAPS],
    }
    artifact = ChangeSeedMappingArtifact.model_validate(
        {**artifact_body, "artifact_sha256": stable_sha256(artifact_body)}
    )
    return _evaluation(policy, verified.project_id, evaluated_at, gaps, artifact)


def verify_change_seed_mapping(
    candidate: ChangeSeedMappingArtifact,
    inputs: ChangeSeedInputs,
    policy: ChangeSeedPolicy,
    *,
    expected_policy_sha256: str = DEFAULT_CHANGE_SEED_POLICY_SHA256,
) -> ChangeSeedEvaluation:
    """Recompile current inputs before accepting a historical local mapping artifact."""

    current = compile_change_seed_mapping(
        inputs, policy, expected_policy_sha256=expected_policy_sha256
    )
    if current.artifact is None:
        return current
    try:
        now = _parse_timestamp(current.evaluated_at)
        raw_candidate = candidate.model_dump(mode="json")
        candidate_observed = _parse_timestamp(str(raw_candidate.get("evaluated_at", "")))
        candidate_valid = _parse_timestamp(str(raw_candidate.get("valid_until", "")))
    except (AttributeError, TypeError, ValueError):
        return _evaluation(
            policy,
            current.project_id,
            current.evaluated_at,
            [*current.gaps, _gap(ChangeSeedGapCode.MAPPING_ARTIFACT_TAMPERED)],
            None,
        )
    if candidate_observed > now:
        return _evaluation(
            policy,
            current.project_id,
            current.evaluated_at,
            [
                *current.gaps,
                _gap(
                    ChangeSeedGapCode.MAPPING_ARTIFACT_FROM_FUTURE,
                    candidate_observed.isoformat(),
                ),
            ],
            None,
        )
    if candidate_valid <= now:
        return _evaluation(
            policy,
            current.project_id,
            current.evaluated_at,
            [
                *current.gaps,
                _gap(ChangeSeedGapCode.MAPPING_ARTIFACT_EXPIRED, candidate_valid.isoformat()),
            ],
            None,
        )
    try:
        validated = ChangeSeedMappingArtifact.model_validate(raw_candidate)
    except (ValidationError, TypeError, ValueError):
        return _evaluation(
            policy,
            current.project_id,
            current.evaluated_at,
            [*current.gaps, _gap(ChangeSeedGapCode.MAPPING_ARTIFACT_TAMPERED)],
            None,
        )
    historical_paths_by_seed: dict[str, list[str]] = {}
    for path in inputs.complete_path_candidate.paths:
        historical_paths_by_seed.setdefault(path.seed_id, []).append(path.path_sha256)
    historical_raw_by_change = {
        binding.change_identity_sha256: tuple(
            sorted(
                {
                    path
                    for seed in binding.seed_ids
                    for path in historical_paths_by_seed.get(seed, [])
                }
            )
        )
        for binding in validated.bindings
    }
    historical_roots_match = (
        validated.verified_change_manifest_sha256 == inputs.candidate.manifest_sha256
        and validated.complete_path_artifact_sha256
        == inputs.complete_path_candidate.artifact_sha256
        and all(
            binding.affected_path_sha256s
            == historical_raw_by_change[binding.change_identity_sha256]
            for binding in validated.bindings
        )
        and validated.affected_path_sha256s
        == tuple(
            sorted(
                {
                    path
                    for paths in historical_raw_by_change.values()
                    for path in paths
                }
            )
        )
    )
    if not historical_roots_match:
        return _evaluation(
            policy,
            current.project_id,
            current.evaluated_at,
            [
                *current.gaps,
                _gap(
                    ChangeSeedGapCode.MAPPING_ARTIFACT_TAMPERED,
                    "historical-roots",
                    validated.artifact_sha256,
                ),
            ],
            None,
        )
    def static_projection(artifact: ChangeSeedMappingArtifact) -> dict[str, Any]:
        body = artifact.model_dump(mode="json")
        for key in (
            "evaluated_at",
            "valid_until",
            "artifact_sha256",
            "verified_change_manifest_sha256",
            "complete_path_artifact_sha256",
            "affected_path_sha256s",
        ):
            body.pop(key)
        for binding in body["bindings"]:
            binding.pop("binding_sha256")
            binding.pop("affected_path_sha256s")
        return body

    candidate_static = static_projection(validated)
    current_static = static_projection(current.artifact)
    if candidate_static != current_static:
        return _evaluation(
            policy,
            current.project_id,
            current.evaluated_at,
            [
                *current.gaps,
                _gap(
                    ChangeSeedGapCode.MAPPING_ARTIFACT_TAMPERED,
                    validated.artifact_sha256,
                    current.artifact.artifact_sha256,
                ),
            ],
            None,
        )
    return current
