from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.change_verification import ChangeOperation, GitChangeEntry
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.graph_production import (
    GraphDeltaEntry,
    GraphDeltaOperation,
    GraphProductionArtifact,
    GraphProductionInputs,
    LocalTreeGraphProducer,
    ProducedEdge,
    ProducedGraphSide,
    ProducedNode,
    TreeSide,
)
from neo_sf_q_intel.ontology import contract_sha256


class OperationSeedContractError(RuntimeError):
    """The pinned operation-aware seed contract is unavailable."""


class _OperationRejected(RuntimeError):
    def __init__(self, code: OperationSeedGapCode, *identity: Any) -> None:
        super().__init__(code.value)
        self.code = code
        self.identity = identity


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ContractPin(_Model):
    contract_id: str = Field(alias="contractId", min_length=1, max_length=200)
    contract_version: str = Field(alias="contractVersion")
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[a-f0-9]{64}$")


class ImplementationPin(_Model):
    implementation_id: str = Field(alias="implementationId", min_length=1, max_length=200)
    implementation_version: str = Field(alias="implementationVersion")
    implementation_locator: str = Field(
        alias="implementationLocator", min_length=1, max_length=500
    )
    implementation_sha256: str = Field(
        alias="implementationSha256", pattern=r"^[a-f0-9]{64}$"
    )


class OperationSeedPolicy(_Model):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_policy: ContractPin = Field(alias="verifiedChangePolicy")
    graph_producer_policy: ContractPin = Field(alias="graphProducerPolicy")
    compiler: ImplementationPin
    mapping_scope: Literal["SUPPORTED_FAMILY_OPERATION_SEEDS"] = Field(
        alias="mappingScope"
    )
    add_side: Literal["CANDIDATE"] = Field(alias="addSide")
    delete_side: Literal["BASE"] = Field(alias="deleteSide")
    modify_sides: tuple[Literal["BASE", "CANDIDATE"], Literal["BASE", "CANDIDATE"]] = (
        Field(alias="modifySides")
    )
    maximum_changes: int = Field(alias="maximumChanges", ge=1)
    maximum_graph_deltas: int = Field(alias="maximumGraphDeltas", ge=1)
    maximum_tombstones: int = Field(alias="maximumTombstones", ge=1)
    maximum_seeds: int = Field(alias="maximumSeeds", ge=1)
    maximum_bindings: int = Field(alias="maximumBindings", ge=1)
    maximum_binding_seed_references: int = Field(
        alias="maximumBindingSeedReferences", ge=1
    )
    maximum_binding_delta_references: int = Field(
        alias="maximumBindingDeltaReferences", ge=1
    )
    maximum_binding_tombstone_references: int = Field(
        alias="maximumBindingTombstoneReferences", ge=1
    )
    maximum_receipt_bytes: int = Field(alias="maximumReceiptBytes", ge=1024)

    @model_validator(mode="after")
    def validate_policy(self) -> OperationSeedPolicy:
        for version in (
            self.schema_version,
            self.policy_version,
            self.verified_change_policy.contract_version,
            self.graph_producer_policy.contract_version,
            self.compiler.implementation_version,
        ):
            _require_semver(version)
        _require_relative_locator(self.compiler.implementation_locator)
        if self.modify_sides != ("BASE", "CANDIDATE"):
            raise ValueError("MODIFY must preserve independent base and candidate evidence")
        if self.maximum_bindings < self.maximum_changes:
            raise ValueError("Binding capacity must cover change capacity")
        return self


class SemanticSeedOutcome(StrEnum):
    SEEDED = "SEEDED"
    NO_SEMANTIC_SEED = "NO_SEMANTIC_SEED"


class OperationSeedGapCode(StrEnum):
    CANDIDATE_BUILD_NOT_VERIFIED = "CANDIDATE_BUILD_NOT_VERIFIED"
    CHANGE_SEED_SCOPE_NOT_ATTESTED = "CHANGE_SEED_SCOPE_NOT_ATTESTED"
    CONFLICT_SCOPE_NOT_ATTESTED = "CONFLICT_SCOPE_NOT_ATTESTED"
    DEPLOYMENT_NOT_ATTESTED = "DEPLOYMENT_NOT_ATTESTED"
    GIT_COMMIT_SIGNATURE_NOT_ATTESTED = "GIT_COMMIT_SIGNATURE_NOT_ATTESTED"
    GRAPH_INPUT_TREE_NOT_ATTESTED = "GRAPH_INPUT_TREE_NOT_ATTESTED"
    HUMAN_APPROVAL_SCOPE_NOT_ATTESTED = "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED"
    RELEASE_EVIDENCE_MODEL_INCOMPLETE = "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    REPOSITORY_ORIGIN_NOT_ATTESTED = "REPOSITORY_ORIGIN_NOT_ATTESTED"
    RISK_FACTORS_NOT_ATTESTED = "RISK_FACTORS_NOT_ATTESTED"
    SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE = (
        "SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE"
    )
    TEST_EXECUTION_SCOPE_NOT_ATTESTED = "TEST_EXECUTION_SCOPE_NOT_ATTESTED"
    TEST_OBLIGATION_SCOPE_NOT_ATTESTED = "TEST_OBLIGATION_SCOPE_NOT_ATTESTED"
    UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED = "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"
    POLICY_ROOT_MISMATCH = "POLICY_ROOT_MISMATCH"
    COMPILER_IMPLEMENTATION_MISMATCH = "COMPILER_IMPLEMENTATION_MISMATCH"
    GRAPH_REPLAY_FAILED = "GRAPH_REPLAY_FAILED"
    OPERATION_BINDING_FAILED = "OPERATION_BINDING_FAILED"
    DELTA_PARTITION_FAILED = "DELTA_PARTITION_FAILED"
    TOMBSTONE_PARTITION_FAILED = "TOMBSTONE_PARTITION_FAILED"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    ARTIFACT_TAMPERED = "ARTIFACT_TAMPERED"
    ARTIFACT_EXPIRED = "ARTIFACT_EXPIRED"
    ARTIFACT_FROM_FUTURE = "ARTIFACT_FROM_FUTURE"
    TIME_AUTHORITY_UNAVAILABLE = "TIME_AUTHORITY_UNAVAILABLE"


_PERMANENT_GAPS = (
    OperationSeedGapCode.CANDIDATE_BUILD_NOT_VERIFIED,
    OperationSeedGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED,
    OperationSeedGapCode.CONFLICT_SCOPE_NOT_ATTESTED,
    OperationSeedGapCode.DEPLOYMENT_NOT_ATTESTED,
    OperationSeedGapCode.GIT_COMMIT_SIGNATURE_NOT_ATTESTED,
    OperationSeedGapCode.GRAPH_INPUT_TREE_NOT_ATTESTED,
    OperationSeedGapCode.HUMAN_APPROVAL_SCOPE_NOT_ATTESTED,
    OperationSeedGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE,
    OperationSeedGapCode.REPOSITORY_ORIGIN_NOT_ATTESTED,
    OperationSeedGapCode.RISK_FACTORS_NOT_ATTESTED,
    OperationSeedGapCode.SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE,
    OperationSeedGapCode.TEST_EXECUTION_SCOPE_NOT_ATTESTED,
    OperationSeedGapCode.TEST_OBLIGATION_SCOPE_NOT_ATTESTED,
    OperationSeedGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED,
)


class OperationSeedGap(_Model):
    code: OperationSeedGapCode
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["RELEASE_ONLY"] = "RELEASE_ONLY"
    blocking: Literal[True] = True


class SideFileEvidence(_Model):
    side: TreeSide
    path: str = Field(min_length=1, max_length=4096)
    disposition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_id: str = Field(min_length=1, max_length=4600)
    mode: Literal["100644", "100755"]
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_status: str = Field(min_length=1, max_length=100)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evidence(self) -> SideFileEvidence:
        _require_relative_locator(self.path)
        _verify_digest(self, "evidence_sha256")
        return self


class SideQualifiedSemanticSeed(_Model):
    side: TreeSide
    node_id: str = Field(min_length=1, max_length=4600)
    raw_kind: str = Field(min_length=1, max_length=200)
    evidence_state: Literal["CONFIRMED", "INFERRED"]
    semantic_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    element_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_paths: tuple[str, ...] = Field(min_length=1)
    owner_sha256s: tuple[str, ...] = Field(min_length=1)
    side_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_seed(self) -> SideQualifiedSemanticSeed:
        if self.owner_paths != tuple(sorted(set(self.owner_paths))):
            raise ValueError("Seed owners must be sorted and unique")
        if len(self.owner_paths) != len(self.owner_sha256s):
            raise ValueError("Seed owner paths and receipts differ")
        for path in self.owner_paths:
            _require_relative_locator(path)
        _verify_digest(self, "seed_sha256")
        return self


class ChangedOperationBinding(_Model):
    change_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    change: GitChangeEntry
    file_evidence: tuple[SideFileEvidence, ...] = Field(min_length=1, max_length=2)
    seed_outcome: SemanticSeedOutcome
    seeds: tuple[SideQualifiedSemanticSeed, ...]
    graph_delta_sha256s: tuple[str, ...]
    tombstone_sha256s: tuple[str, ...]
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_binding(self) -> ChangedOperationBinding:
        if self.change_identity_sha256 != _change_identity(self.change):
            raise ValueError("Change identity differs from the complete bytewise change")
        expected_sides = {
            ChangeOperation.ADD: (TreeSide.CANDIDATE,),
            ChangeOperation.DELETE: (TreeSide.BASE,),
            ChangeOperation.MODIFY: (TreeSide.BASE, TreeSide.CANDIDATE),
        }[self.change.operation]
        if tuple(item.side for item in self.file_evidence) != expected_sides:
            raise ValueError("File evidence does not preserve operation-side semantics")
        if any(item.path != self.change.path for item in self.file_evidence):
            raise ValueError("File evidence path differs from changed path")
        expected_images = {
            TreeSide.BASE: (
                self.change.before_mode,
                self.change.before_size_bytes,
                self.change.before_sha256,
            ),
            TreeSide.CANDIDATE: (
                self.change.after_mode,
                self.change.after_size_bytes,
                self.change.after_sha256,
            ),
        }
        for item in self.file_evidence:
            if (item.mode, item.size_bytes, item.content_sha256) != expected_images[item.side]:
                raise ValueError("File evidence differs from the verified change image")
        seed_keys = tuple((item.side.value, item.node_id) for item in self.seeds)
        if seed_keys != tuple(sorted(set(seed_keys))):
            raise ValueError("Side-qualified seeds must be sorted and unique")
        if any(item.side not in expected_sides for item in self.seeds):
            raise ValueError("Semantic seed side differs from the Git operation scope")
        if (self.seed_outcome is SemanticSeedOutcome.SEEDED) is not bool(self.seeds):
            raise ValueError("Semantic seed outcome differs from materialized seeds")
        for values, label in (
            (self.graph_delta_sha256s, "Graph delta references"),
            (self.tombstone_sha256s, "Tombstone references"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        _verify_digest(self, "binding_sha256")
        return self


class OperationSeedArtifact(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    mapping_scope: Literal["SUPPORTED_FAMILY_OPERATION_SEEDS"] = (
        "SUPPORTED_FAMILY_OPERATION_SEEDS"
    )
    supported_family_operation_seed_scope_complete: Literal[True] = True
    graph_input_tree_attested: Literal[False] = False
    change_seed_scope_attested: Literal[False] = False
    path_scope_attested: Literal[False] = False
    project_id: str = Field(min_length=1, max_length=200)
    repository_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_commit_oid: str
    base_tree_oid: str
    base_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_production_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_side_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_side_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_producer_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: str
    policy_version: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    compiler_id: str
    compiler_version: str
    compiler_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    valid_until: str
    change_identity_sha256s: tuple[str, ...] = Field(min_length=1)
    graph_delta_sha256s: tuple[str, ...]
    tombstone_sha256s: tuple[str, ...]
    direct_graph_delta_sha256s: tuple[str, ...]
    indirect_graph_delta_sha256s: tuple[str, ...]
    direct_tombstone_sha256s: tuple[str, ...]
    indirect_tombstone_sha256s: tuple[str, ...]
    bindings: tuple[ChangedOperationBinding, ...] = Field(min_length=1)
    indirect_seeds: tuple[SideQualifiedSemanticSeed, ...]
    blocking_gap_codes: tuple[OperationSeedGapCode, ...] = _PERMANENT_GAPS
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> OperationSeedArtifact:
        if _parse_timestamp(self.valid_until) <= _parse_timestamp(self.evaluated_at):
            raise ValueError("Operation-seed artifact is not fresh")
        binding_ids = tuple(item.change_identity_sha256 for item in self.bindings)
        if binding_ids != tuple(sorted(set(binding_ids))) or binding_ids != (
            self.change_identity_sha256s
        ):
            raise ValueError("Bindings do not exactly partition verified changes")
        for values, label in (
            (self.graph_delta_sha256s, "Graph deltas"),
            (self.tombstone_sha256s, "Tombstones"),
            (self.direct_graph_delta_sha256s, "Direct graph deltas"),
            (self.indirect_graph_delta_sha256s, "Indirect graph deltas"),
            (self.direct_tombstone_sha256s, "Direct tombstones"),
            (self.indirect_tombstone_sha256s, "Indirect tombstones"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        direct = {value for item in self.bindings for value in item.graph_delta_sha256s}
        if self.direct_graph_delta_sha256s != tuple(sorted(direct)):
            raise ValueError("Direct delta union differs from changed-file bindings")
        if set(self.direct_graph_delta_sha256s) & set(self.indirect_graph_delta_sha256s):
            raise ValueError("Direct and indirect graph-delta partitions overlap")
        if set(self.graph_delta_sha256s) != (
            set(self.direct_graph_delta_sha256s) | set(self.indirect_graph_delta_sha256s)
        ):
            raise ValueError("Graph deltas are not completely partitioned")
        bound_tombstones = {value for item in self.bindings for value in item.tombstone_sha256s}
        if self.direct_tombstone_sha256s != tuple(sorted(bound_tombstones)):
            raise ValueError("Direct tombstone union differs from changed-file bindings")
        if set(self.direct_tombstone_sha256s) & set(self.indirect_tombstone_sha256s):
            raise ValueError("Direct and indirect tombstone partitions overlap")
        if set(self.tombstone_sha256s) != (
            set(self.direct_tombstone_sha256s) | set(self.indirect_tombstone_sha256s)
        ):
            raise ValueError("Tombstones are not completely partitioned")
        seed_keys = tuple((item.side.value, item.node_id) for item in self.indirect_seeds)
        if seed_keys != tuple(sorted(set(seed_keys))):
            raise ValueError("Indirect side-qualified seeds must be sorted and unique")
        if self.blocking_gap_codes != _PERMANENT_GAPS:
            raise ValueError("Permanent release gaps cannot be omitted")
        _verify_digest(self, "artifact_sha256")
        return self


class OperationSeedEvaluation(_Model):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    supported_family_operation_seed_scope_complete: bool
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    artifact: OperationSeedArtifact | None = None
    gaps: tuple[OperationSeedGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> OperationSeedEvaluation:
        if self.supported_family_operation_seed_scope_complete is not (
            self.artifact is not None
        ):
            raise ValueError("Mapping completeness differs from artifact presence")
        keys = tuple((item.code.value, item.identity_sha256) for item in self.gaps)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Operation-seed gaps must be sorted and unique")
        if not set(_PERMANENT_GAPS).issubset(item.code for item in self.gaps):
            raise ValueError("Permanent release gaps cannot be omitted")
        _verify_digest(self, "evaluation_sha256")
        return self


DEFAULT_OPERATION_SEED_POLICY_SHA256 = (
    "3943267d34ae1f41a03e4b2bec7e8dce714d28538a67bd2149c7d2891064b1d7"
)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _require_semver(value: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError("Version must use semantic versioning")


def _require_relative_locator(value: str) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Repository locator is unsafe")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Repository locator is unsafe")
    return value


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be canonical UTC seconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sample_now() -> datetime:
    sample = _utc_now()
    if sample.tzinfo is None or sample.utcoffset() is None:
        raise ValueError("Time authority must be timezone-aware")
    return sample.astimezone(UTC).replace(microsecond=0)


def _verify_digest(model: BaseModel, field: str) -> None:
    body = model.model_dump(mode="json")
    declared = body.pop(field)
    if stable_sha256(body) != declared:
        raise ValueError(f"{field} differs from canonical content")


def _implementation_sha256(content: bytes) -> str:
    normalized = re.sub(
        rb"DEFAULT_OPERATION_SEED_POLICY_SHA256\s*=\s*"
        rb'(?:"[a-f0-9]{64}"|\(\s*"[a-f0-9]{64}"\s*\))',
        b'DEFAULT_OPERATION_SEED_POLICY_SHA256 = (\n    "' + (b"0" * 64) + b'"\n)',
        content,
        count=1,
    )
    return hashlib.sha256(normalized).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperationSeedContractError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def load_operation_seed_policy(
    path: Path,
    *,
    implementation_root: Path,
    expected_sha256: str = DEFAULT_OPERATION_SEED_POLICY_SHA256,
) -> OperationSeedPolicy:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except OperationSeedContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationSeedContractError("Operation-seed policy cannot be loaded") from exc
    if not isinstance(document, dict) or document.get("sha256") != contract_sha256(document):
        raise OperationSeedContractError("Operation-seed policy self-hash is invalid")
    if document.get("sha256") != expected_sha256:
        raise OperationSeedContractError("Operation-seed policy is not externally pinned")
    try:
        policy = OperationSeedPolicy.model_validate(document)
    except ValidationError as exc:
        raise OperationSeedContractError("Operation-seed policy is invalid") from exc
    implementation = (
        implementation_root.resolve() / policy.compiler.implementation_locator
    ).resolve()
    try:
        implementation.relative_to(implementation_root.resolve())
    except ValueError as exc:
        raise OperationSeedContractError("Compiler implementation escapes its root") from exc
    loaded = Path(inspect.getsourcefile(OperationAwareSeedCompiler) or "").resolve()
    if implementation != loaded:
        raise OperationSeedContractError("Loaded compiler differs from policy")
    try:
        digest = _implementation_sha256(implementation.read_bytes())
    except OSError as exc:
        raise OperationSeedContractError("Compiler implementation is unavailable") from exc
    if digest != policy.compiler.implementation_sha256:
        raise OperationSeedContractError("Compiler implementation digest is invalid")
    return policy


def _identity(*values: Any) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _change_identity(change: GitChangeEntry) -> str:
    return stable_sha256(change.model_dump(mode="json"))


def _gap(code: OperationSeedGapCode, *identity: Any) -> OperationSeedGap:
    return OperationSeedGap(code=code, identity_sha256=_identity(code.value, *identity))


def _planned_gaps() -> list[OperationSeedGap]:
    return [_gap(code) for code in _PERMANENT_GAPS]


def _sorted_gaps(gaps: list[OperationSeedGap]) -> tuple[OperationSeedGap, ...]:
    return tuple(sorted(set(gaps), key=lambda item: (item.code.value, item.identity_sha256)))


def _evaluation(
    policy: OperationSeedPolicy,
    evaluated_at: str,
    gaps: list[OperationSeedGap],
    artifact: OperationSeedArtifact | None,
) -> OperationSeedEvaluation:
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "supported_family_operation_seed_scope_complete": artifact is not None,
        "policy_sha256": policy.sha256,
        "evaluated_at": evaluated_at,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "gaps": [item.model_dump(mode="json") for item in _sorted_gaps(gaps)],
    }
    return OperationSeedEvaluation.model_validate(
        {**body, "evaluation_sha256": stable_sha256(body)}
    )


def _file_evidence(side: TreeSide, disposition: Any) -> SideFileEvidence:
    body = {
        "side": side.value,
        "path": disposition.path,
        "disposition_sha256": disposition.record_sha256,
        "source_artifact_id": disposition.source_artifact_id,
        "mode": disposition.mode,
        "size_bytes": disposition.size_bytes,
        "content_sha256": disposition.content_sha256,
        "semantic_status": disposition.semantic_status.value,
    }
    return SideFileEvidence.model_validate(
        {**body, "evidence_sha256": stable_sha256(body)}
    )


def _semantic_seed(side: ProducedGraphSide, node: ProducedNode) -> SideQualifiedSemanticSeed:
    owners = tuple(sorted(node.owners, key=lambda item: item.path))
    body = {
        "side": side.side.value,
        "node_id": node.node_id,
        "raw_kind": node.raw_kind,
        "evidence_state": node.evidence_state,
        "semantic_sha256": node.semantic_sha256,
        "element_sha256": node.element_sha256,
        "owner_paths": [item.path for item in owners],
        "owner_sha256s": [item.owner_sha256 for item in owners],
        "side_receipt_sha256": side.side_receipt_sha256,
        "normalized_graph_sha256": side.normalized_graph_sha256,
    }
    return SideQualifiedSemanticSeed.model_validate(
        {**body, "seed_sha256": stable_sha256(body)}
    )


def _delta_sides(delta: GraphDeltaEntry) -> tuple[TreeSide, ...]:
    if delta.operation is GraphDeltaOperation.ADD:
        return (TreeSide.CANDIDATE,)
    if delta.operation is GraphDeltaOperation.DELETE:
        return (TreeSide.BASE,)
    return (TreeSide.BASE, TreeSide.CANDIDATE)


def _static_artifact(artifact: OperationSeedArtifact) -> dict[str, Any]:
    body = artifact.model_dump(mode="json")
    for key in (
        "evaluated_at",
        "valid_until",
        "graph_production_receipt_sha256",
        "verified_change_manifest_sha256",
        "artifact_sha256",
    ):
        body.pop(key)
    return body


@dataclass(frozen=True, slots=True)
class OperationSeedInputs:
    graph_candidate: GraphProductionArtifact
    graph_producer: LocalTreeGraphProducer
    graph_inputs: GraphProductionInputs


@dataclass(frozen=True, slots=True)
class OperationAwareSeedCompiler:
    policy: OperationSeedPolicy

    def compile(self, inputs: OperationSeedInputs) -> OperationSeedEvaluation:
        gaps = _planned_gaps()
        try:
            now = _sample_now()
        except (OSError, ValueError):
            return _evaluation(
                self.policy,
                "unavailable",
                [*gaps, _gap(OperationSeedGapCode.TIME_AUTHORITY_UNAVAILABLE)],
                None,
            )
        now_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            artifact = self._compile(inputs, now)
        except _OperationRejected as exc:
            return _evaluation(self.policy, now_text, [*gaps, _gap(exc.code, *exc.identity)], None)
        return _evaluation(self.policy, artifact.evaluated_at, gaps, artifact)

    def verify(
        self, candidate: OperationSeedArtifact, inputs: OperationSeedInputs
    ) -> OperationSeedEvaluation:
        try:
            now = _sample_now()
            validated = OperationSeedArtifact.model_validate(
                candidate.model_dump(mode="json")
            )
        except (AttributeError, OSError, TypeError, ValidationError, ValueError):
            validated = None
            now = None
        if validated is not None and (
            validated.graph_production_receipt_sha256
            != inputs.graph_candidate.receipt_sha256
            or validated.verified_change_manifest_sha256
            != inputs.graph_candidate.verified_change_manifest_sha256
            or validated.base_side_receipt_sha256
            != inputs.graph_candidate.base.side_receipt_sha256
            or validated.candidate_side_receipt_sha256
            != inputs.graph_candidate.candidate.side_receipt_sha256
            or validated.base_normalized_graph_sha256
            != inputs.graph_candidate.base.normalized_graph_sha256
            or validated.candidate_normalized_graph_sha256
            != inputs.graph_candidate.candidate.normalized_graph_sha256
        ):
            validated = None
        if validated is not None and now is not None:
            if _parse_timestamp(validated.evaluated_at) > now:
                return _evaluation(
                    self.policy,
                    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    [*_planned_gaps(), _gap(OperationSeedGapCode.ARTIFACT_FROM_FUTURE)],
                    None,
                )
            if _parse_timestamp(validated.valid_until) <= now:
                return _evaluation(
                    self.policy,
                    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    [*_planned_gaps(), _gap(OperationSeedGapCode.ARTIFACT_EXPIRED)],
                    None,
                )
        current = self.compile(inputs)
        if current.artifact is None:
            return current
        if now is not None and _parse_timestamp(current.artifact.evaluated_at) < now:
            return _evaluation(
                self.policy,
                current.evaluated_at,
                [*current.gaps, _gap(OperationSeedGapCode.TIME_AUTHORITY_UNAVAILABLE)],
                None,
            )
        if validated is None or _static_artifact(validated) != _static_artifact(current.artifact):
            return _evaluation(
                self.policy,
                current.evaluated_at,
                [*current.gaps, _gap(OperationSeedGapCode.ARTIFACT_TAMPERED)],
                None,
            )
        return current

    def _compile(
        self, inputs: OperationSeedInputs, initial_now: datetime
    ) -> OperationSeedArtifact:
        self._require_runtime_policy(inputs)
        if type(inputs.graph_producer) is not LocalTreeGraphProducer:
            raise _OperationRejected(OperationSeedGapCode.GRAPH_REPLAY_FAILED, "producer-type")
        replay = inputs.graph_producer.verify(inputs.graph_candidate, inputs.graph_inputs)
        graph = replay.artifact
        if graph is None or not replay.supported_semantic_graph_input_tree_attested:
            raise _OperationRejected(OperationSeedGapCode.GRAPH_REPLAY_FAILED)
        changes = inputs.graph_inputs.candidate.changes
        if len(changes) > self.policy.maximum_changes:
            raise _OperationRejected(OperationSeedGapCode.CAPACITY_EXCEEDED, "changes")
        if len(graph.delta) > self.policy.maximum_graph_deltas:
            raise _OperationRejected(OperationSeedGapCode.CAPACITY_EXCEEDED, "deltas")
        if len(graph.tombstones) > self.policy.maximum_tombstones:
            raise _OperationRejected(OperationSeedGapCode.CAPACITY_EXCEEDED, "tombstones")
        self._require_graph_linkage(graph, inputs)

        sides = {TreeSide.BASE: graph.base, TreeSide.CANDIDATE: graph.candidate}
        dispositions = {
            side: {item.path: item for item in materialized.dispositions}
            for side, materialized in sides.items()
        }
        nodes = {
            side: {item.node_id: item for item in materialized.nodes}
            for side, materialized in sides.items()
        }
        edges = {
            side: {item.edge_id: item for item in materialized.edges}
            for side, materialized in sides.items()
        }
        tombstones = {
            (item.entity_type, item.entity_id): item for item in graph.tombstones
        }
        source_artifact_ids = {
            side: {item.source_artifact_id for item in materialized.dispositions}
            for side, materialized in sides.items()
        }
        changed_paths = {item.path for item in changes}
        direct_deltas: set[str] = set()
        bindings: list[ChangedOperationBinding] = []
        deltas_by_owner: dict[str, list[GraphDeltaEntry]] = {}
        delta_owner_references = 0
        for delta in graph.delta:
            owner_paths = tuple(
                sorted(set(delta.base_owner_paths) | set(delta.candidate_owner_paths))
            )
            delta_owner_references += len(owner_paths)
            if delta_owner_references > self.policy.maximum_binding_delta_references:
                raise _OperationRejected(
                    OperationSeedGapCode.CAPACITY_EXCEEDED, "delta-owner-references"
                )
            for owner_path in owner_paths:
                deltas_by_owner.setdefault(owner_path, []).append(delta)
        total_seed_references = 0
        total_delta_references = 0
        total_tombstone_references = 0

        for change in changes:
            selected_sides = {
                ChangeOperation.ADD: (TreeSide.CANDIDATE,),
                ChangeOperation.DELETE: (TreeSide.BASE,),
                ChangeOperation.MODIFY: (TreeSide.BASE, TreeSide.CANDIDATE),
            }[change.operation]
            opposite_absence = {
                ChangeOperation.ADD: (TreeSide.BASE,),
                ChangeOperation.DELETE: (TreeSide.CANDIDATE,),
                ChangeOperation.MODIFY: (),
            }[change.operation]
            if any(change.path not in dispositions[side] for side in selected_sides) or any(
                change.path in dispositions[side] for side in opposite_absence
            ):
                raise _OperationRejected(
                    OperationSeedGapCode.OPERATION_BINDING_FAILED, change.path, "presence"
                )
            file_evidence = tuple(
                _file_evidence(side, dispositions[side][change.path])
                for side in selected_sides
            )
            source_id = file_evidence[0].source_artifact_id
            file_delta = next(
                (
                    item
                    for item in graph.delta
                    if item.entity_type == "NODE" and item.entity_id == source_id
                ),
                None,
            )
            if file_delta is None or file_delta.operation.value != change.operation.value:
                raise _OperationRejected(
                    OperationSeedGapCode.OPERATION_BINDING_FAILED, change.path, "file-delta"
                )
            related = tuple(deltas_by_owner.get(change.path, ()))
            if file_delta not in related:
                raise _OperationRejected(
                    OperationSeedGapCode.DELTA_PARTITION_FAILED, change.path, "owner"
                )
            direct_deltas.update(item.delta_sha256 for item in related)
            related_delete_keys = {
                (item.entity_type, item.entity_id)
                for item in related
                if item.operation is GraphDeltaOperation.DELETE
            }
            bound_tombstones = [
                tombstones[key].tombstone_sha256
                for key in sorted(related_delete_keys)
                if key in tombstones
            ]
            if len(bound_tombstones) != len(related_delete_keys):
                raise _OperationRejected(
                    OperationSeedGapCode.TOMBSTONE_PARTITION_FAILED,
                    change.path,
                    "semantic-delete",
                )
            if change.operation is ChangeOperation.DELETE:
                file_tombstone = tombstones.get(("NODE", source_id))
                if file_tombstone is None or change.path not in file_tombstone.prior_owner_paths:
                    raise _OperationRejected(
                        OperationSeedGapCode.TOMBSTONE_PARTITION_FAILED, change.path
                    )
                if file_tombstone.tombstone_sha256 not in bound_tombstones:
                    raise _OperationRejected(
                        OperationSeedGapCode.TOMBSTONE_PARTITION_FAILED,
                        change.path,
                        "file",
                    )

            seed_ids: dict[tuple[TreeSide, str], None] = {}
            for side in selected_sides:
                disposition = dispositions[side][change.path]
                for node_id in disposition.semantic_node_ids:
                    seed_ids[(side, node_id)] = None
                for edge_id in disposition.semantic_edge_ids:
                    edge = edges[side].get(edge_id)
                    if edge is None:
                        raise _OperationRejected(
                            OperationSeedGapCode.OPERATION_BINDING_FAILED,
                            change.path,
                            "edge",
                        )
                    seed_ids[(side, edge.source_id)] = None
                    seed_ids[(side, edge.target_id)] = None
            for delta in related:
                for side in _delta_sides(delta):
                    if side not in selected_sides:
                        continue
                    self._add_delta_seed_ids(
                        delta,
                        side,
                        nodes,
                        edges,
                        source_artifact_ids,
                        seed_ids,
                    )
            seeds = self._materialize_seeds(
                seed_ids, sides, nodes, source_artifact_ids
            )
            total_seed_references += len(seeds)
            total_delta_references += len(related)
            total_tombstone_references += len(bound_tombstones)
            if (
                total_seed_references > self.policy.maximum_binding_seed_references
                or total_delta_references
                > self.policy.maximum_binding_delta_references
                or total_tombstone_references
                > self.policy.maximum_binding_tombstone_references
            ):
                raise _OperationRejected(
                    OperationSeedGapCode.CAPACITY_EXCEEDED, "binding-references"
                )
            body = {
                "change_identity_sha256": _change_identity(change),
                "change": change.model_dump(mode="json"),
                "file_evidence": [item.model_dump(mode="json") for item in file_evidence],
                "seed_outcome": (
                    SemanticSeedOutcome.SEEDED.value
                    if seeds
                    else SemanticSeedOutcome.NO_SEMANTIC_SEED.value
                ),
                "seeds": [item.model_dump(mode="json") for item in seeds],
                "graph_delta_sha256s": sorted(item.delta_sha256 for item in related),
                "tombstone_sha256s": sorted(bound_tombstones),
            }
            bindings.append(
                ChangedOperationBinding.model_validate(
                    {**body, "binding_sha256": stable_sha256(body)}
                )
            )

        all_delta_sha256s = {item.delta_sha256 for item in graph.delta}
        indirect_delta_sha256s = all_delta_sha256s - direct_deltas
        direct_tombstone_sha256s = {
            value for binding in bindings for value in binding.tombstone_sha256s
        }
        all_tombstone_sha256s = {item.tombstone_sha256 for item in graph.tombstones}
        indirect_tombstone_sha256s = all_tombstone_sha256s - direct_tombstone_sha256s
        indirect_seed_ids: dict[tuple[TreeSide, str], None] = {}
        for delta in graph.delta:
            if delta.delta_sha256 not in indirect_delta_sha256s:
                continue
            for side in _delta_sides(delta):
                self._add_delta_seed_ids(
                    delta,
                    side,
                    nodes,
                    edges,
                    source_artifact_ids,
                    indirect_seed_ids,
                )
        indirect_seeds = self._materialize_seeds(
            indirect_seed_ids, sides, nodes, source_artifact_ids
        )
        all_seeds = {
            item.seed_sha256 for binding in bindings for item in binding.seeds
        } | {item.seed_sha256 for item in indirect_seeds}
        if len(bindings) > self.policy.maximum_bindings or len(all_seeds) > (
            self.policy.maximum_seeds
        ):
            raise _OperationRejected(OperationSeedGapCode.CAPACITY_EXCEEDED, "outputs")
        if {item.path for item in changes} != changed_paths or len(bindings) != len(changes):
            raise _OperationRejected(OperationSeedGapCode.OPERATION_BINDING_FAILED, "partition")

        try:
            final_now = _sample_now()
            if final_now < initial_now:
                raise ValueError
        except (OSError, ValueError):
            raise _OperationRejected(OperationSeedGapCode.TIME_AUTHORITY_UNAVAILABLE) from None
        authorities = (graph, inputs.graph_inputs.candidate)
        if any(_parse_timestamp(item.observed_at) > final_now for item in authorities):
            raise _OperationRejected(OperationSeedGapCode.ARTIFACT_FROM_FUTURE)
        valid_until = min(_parse_timestamp(item.valid_until) for item in authorities)
        if valid_until <= final_now:
            raise _OperationRejected(OperationSeedGapCode.ARTIFACT_EXPIRED)

        bindings_tuple = tuple(sorted(bindings, key=lambda item: item.change_identity_sha256))
        body = {
            "schema_version": "1.0.0",
            "authority_scope": "ANALYSIS_ONLY",
            "release_eligible": False,
            "mapping_scope": "SUPPORTED_FAMILY_OPERATION_SEEDS",
            "supported_family_operation_seed_scope_complete": True,
            "graph_input_tree_attested": False,
            "change_seed_scope_attested": False,
            "path_scope_attested": False,
            "project_id": graph.project_id,
            "repository_identity_sha256": graph.repository_identity_sha256,
            "source_snapshot_sha256": inputs.graph_inputs.candidate.source_snapshot_sha256,
            "base_commit_oid": inputs.graph_inputs.candidate.base_commit_oid,
            "base_tree_oid": inputs.graph_inputs.candidate.base_tree_oid,
            "base_tree_sha256": graph.base.tree_sha256,
            "candidate_tree_sha256": graph.candidate.tree_sha256,
            "verified_change_manifest_sha256": graph.verified_change_manifest_sha256,
            "graph_production_receipt_sha256": graph.receipt_sha256,
            "base_side_receipt_sha256": graph.base.side_receipt_sha256,
            "candidate_side_receipt_sha256": graph.candidate.side_receipt_sha256,
            "base_normalized_graph_sha256": graph.base.normalized_graph_sha256,
            "candidate_normalized_graph_sha256": graph.candidate.normalized_graph_sha256,
            "graph_producer_policy_sha256": graph.policy_sha256,
            "ontology_sha256": graph.ontology_sha256,
            "source_profile_sha256": graph.source_profile_sha256,
            "policy_id": self.policy.policy_id,
            "policy_version": self.policy.policy_version,
            "policy_sha256": self.policy.sha256,
            "compiler_id": self.policy.compiler.implementation_id,
            "compiler_version": self.policy.compiler.implementation_version,
            "compiler_implementation_sha256": self.policy.compiler.implementation_sha256,
            "evaluated_at": final_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "change_identity_sha256s": [
                item.change_identity_sha256 for item in bindings_tuple
            ],
            "graph_delta_sha256s": sorted(all_delta_sha256s),
            "tombstone_sha256s": sorted(
                item.tombstone_sha256 for item in graph.tombstones
            ),
            "direct_graph_delta_sha256s": sorted(direct_deltas),
            "indirect_graph_delta_sha256s": sorted(indirect_delta_sha256s),
            "direct_tombstone_sha256s": sorted(direct_tombstone_sha256s),
            "indirect_tombstone_sha256s": sorted(indirect_tombstone_sha256s),
            "bindings": [item.model_dump(mode="json") for item in bindings_tuple],
            "indirect_seeds": [item.model_dump(mode="json") for item in indirect_seeds],
            "blocking_gap_codes": [item.value for item in _PERMANENT_GAPS],
        }
        if len(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()) > (
            self.policy.maximum_receipt_bytes
        ):
            raise _OperationRejected(OperationSeedGapCode.CAPACITY_EXCEEDED, "receipt")
        return OperationSeedArtifact.model_validate(
            {**body, "artifact_sha256": stable_sha256(body)}
        )

    def _require_runtime_policy(self, inputs: OperationSeedInputs) -> None:
        body = self.policy.model_dump(mode="json", by_alias=True)
        if (
            contract_sha256(body) != self.policy.sha256
            or self.policy.sha256 != DEFAULT_OPERATION_SEED_POLICY_SHA256
        ):
            raise _OperationRejected(OperationSeedGapCode.POLICY_ROOT_MISMATCH)
        loaded = Path(inspect.getsourcefile(OperationAwareSeedCompiler) or "").resolve()
        try:
            digest = _implementation_sha256(loaded.read_bytes())
        except OSError:
            raise _OperationRejected(
                OperationSeedGapCode.COMPILER_IMPLEMENTATION_MISMATCH
            ) from None
        if digest != self.policy.compiler.implementation_sha256:
            raise _OperationRejected(OperationSeedGapCode.COMPILER_IMPLEMENTATION_MISMATCH)
        if self.policy.verified_change_policy.contract_sha256 != (
            inputs.graph_inputs.candidate.policy_sha256
        ):
            raise _OperationRejected(OperationSeedGapCode.POLICY_ROOT_MISMATCH, "change")
        if self.policy.graph_producer_policy.contract_sha256 != (
            inputs.graph_producer.policy.sha256
        ):
            raise _OperationRejected(OperationSeedGapCode.POLICY_ROOT_MISMATCH, "graph")

    def _require_graph_linkage(
        self, graph: GraphProductionArtifact, inputs: OperationSeedInputs
    ) -> None:
        change = inputs.graph_inputs.candidate
        expected = (
            graph.project_id,
            graph.repository_identity_sha256,
            graph.verified_change_manifest_sha256,
            graph.verified_change_policy_sha256,
            graph.base.tree_sha256,
            graph.candidate.tree_sha256,
        )
        actual = (
            change.project_id,
            change.repository_identity_sha256,
            change.manifest_sha256,
            change.policy_sha256,
            change.base_tree_sha256,
            change.candidate_input_tree_sha256,
        )
        if expected != actual:
            raise _OperationRejected(OperationSeedGapCode.GRAPH_REPLAY_FAILED, "linkage")

    @staticmethod
    def _add_delta_seed_ids(
        delta: GraphDeltaEntry,
        side: TreeSide,
        nodes: dict[TreeSide, dict[str, ProducedNode]],
        edges: dict[TreeSide, dict[str, ProducedEdge]],
        source_artifact_ids: dict[TreeSide, set[str]],
        target: dict[tuple[TreeSide, str], None],
    ) -> None:
        if delta.entity_type == "NODE":
            node = nodes[side].get(delta.entity_id)
            if node is not None and node.node_id not in source_artifact_ids[side]:
                target[(side, node.node_id)] = None
            return
        edge = edges[side].get(delta.entity_id)
        if edge is not None:
            target[(side, edge.source_id)] = None
            target[(side, edge.target_id)] = None

    def _materialize_seeds(
        self,
        identities: dict[tuple[TreeSide, str], None],
        sides: dict[TreeSide, ProducedGraphSide],
        nodes: dict[TreeSide, dict[str, ProducedNode]],
        source_artifact_ids: dict[TreeSide, set[str]],
    ) -> tuple[SideQualifiedSemanticSeed, ...]:
        values: list[SideQualifiedSemanticSeed] = []
        for side, node_id in sorted(identities, key=lambda item: (item[0].value, item[1])):
            node = nodes[side].get(node_id)
            if node is None:
                raise _OperationRejected(
                    OperationSeedGapCode.OPERATION_BINDING_FAILED, side.value, node_id
                )
            if node.node_id not in source_artifact_ids[side]:
                values.append(_semantic_seed(sides[side], node))
        return tuple(values)
