from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.change_verification import (
    GitFileEntry,
    LocalGitChangeProducer,
    VerifiedChangeSet,
    read_git_blobs_batch,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.graph_adapter import (
    AdapterFileResult,
    AdapterGraph,
    GraphSemanticAdapter,
    SemanticFileStatus,
)
from neo_sf_q_intel.ontology import (
    CanonicalOntology,
    NormalizedGraph,
    OntologyNormalizationError,
    SourceGraphProfile,
    contract_sha256,
    normalize_source_graph,
)
from neo_sf_q_intel.salesforce_graph_adapter import (
    SalesforceGraphAdapterError,
    SalesforceGraphCapacityError,
    SalesforceSemanticGraphAdapter,
)


class GraphProductionContractError(RuntimeError):
    """The independently pinned tree-to-graph contract is unavailable."""


class _ProductionRejected(RuntimeError):
    def __init__(self, code: GraphProductionGapCode, *identity: Any) -> None:
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
    implementation_locator: str = Field(alias="implementationLocator", min_length=1, max_length=500)
    implementation_sha256: str = Field(alias="implementationSha256", pattern=r"^[a-f0-9]{64}$")


class GraphProducerPolicy(_Model):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_policy: ContractPin = Field(alias="verifiedChangePolicy")
    ontology: ContractPin
    source_profile: ContractPin = Field(alias="sourceProfile")
    producer: ImplementationPin
    adapter: ImplementationPin
    adapter_contract: ImplementationPin = Field(alias="adapterContract")
    normalizer: ImplementationPin
    capture_scope: Literal["COMPLETE_BASE_AND_CANDIDATE_TREE"] = Field(alias="captureScope")
    disposition_policy: Literal["ALL_FILES_ACCOUNTED_WITH_SEMANTIC_CLASSIFICATION"] = Field(
        alias="dispositionPolicy"
    )
    inventory_parser_id: str = Field(alias="inventoryParserId", min_length=1, max_length=200)
    inventory_parser_version: str = Field(alias="inventoryParserVersion")
    maximum_path_bytes: int = Field(alias="maximumPathBytes", ge=32, le=32768)
    maximum_files: int = Field(alias="maximumFiles", ge=1)
    maximum_file_bytes: int = Field(alias="maximumFileBytes", ge=1)
    maximum_total_bytes: int = Field(alias="maximumTotalBytes", ge=1)
    maximum_nodes: int = Field(alias="maximumNodes", ge=1)
    maximum_edges: int = Field(alias="maximumEdges", ge=0)
    maximum_semantic_work_units: int = Field(alias="maximumSemanticWorkUnits", ge=1)
    maximum_receipt_bytes: int = Field(alias="maximumReceiptBytes", ge=1024)
    git_timeout_seconds: int = Field(alias="gitTimeoutSeconds", ge=1, le=120)
    maximum_git_output_bytes: int = Field(alias="maximumGitOutputBytes", ge=1024)
    freshness_seconds: int = Field(alias="freshnessSeconds", ge=1, le=86400)

    @model_validator(mode="after")
    def validate_policy(self) -> GraphProducerPolicy:
        for value in (
            self.schema_version,
            self.policy_version,
            self.verified_change_policy.contract_version,
            self.ontology.contract_version,
            self.source_profile.contract_version,
            self.producer.implementation_version,
            self.adapter.implementation_version,
            self.adapter_contract.implementation_version,
            self.normalizer.implementation_version,
            self.inventory_parser_version,
        ):
            _require_semver(value)
        _require_safe_locator(self.producer.implementation_locator, self.maximum_path_bytes)
        _require_safe_locator(self.adapter.implementation_locator, self.maximum_path_bytes)
        _require_safe_locator(self.adapter_contract.implementation_locator, self.maximum_path_bytes)
        _require_safe_locator(self.normalizer.implementation_locator, self.maximum_path_bytes)
        if self.maximum_git_output_bytes < self.maximum_file_bytes:
            raise ValueError("Git output capacity must cover one maximum-sized blob")
        if self.maximum_nodes < self.maximum_files:
            raise ValueError("Node capacity must cover every admitted file")
        return self


class TreeSide(StrEnum):
    BASE = "BASE"
    CANDIDATE = "CANDIDATE"


class FileDisposition(_Model):
    side: TreeSide
    path: str = Field(min_length=1, max_length=4096)
    disposition: Literal["ACCOUNTED"] = "ACCOUNTED"
    mode: Literal["100644", "100755"]
    tracking: Literal["TRACKED", "UNTRACKED"]
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_kind: Literal["TEXT_UTF8", "BINARY"]
    normalized_text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    inventory_parser_id: str = Field(min_length=1, max_length=200)
    inventory_parser_version: str
    semantic_status: SemanticFileStatus
    semantic_parser_id: str = Field(min_length=1, max_length=200)
    semantic_reason_code: str | None = Field(default=None, min_length=1, max_length=200)
    semantic_node_ids: tuple[str, ...] = ()
    semantic_edge_ids: tuple[str, ...] = ()
    adapter_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_id: str = Field(min_length=1, max_length=4600)
    record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_record(self) -> FileDisposition:
        _require_safe_locator(self.path, 4096)
        _require_semver(self.inventory_parser_version)
        if self.source_artifact_id != f"file:{self.path}":
            raise ValueError("Source-artifact identity differs from its path")
        if self.content_kind == "TEXT_UTF8" and self.normalized_text_sha256 is None:
            raise ValueError("UTF-8 text must bind its normalized-text digest")
        if self.content_kind == "BINARY" and self.normalized_text_sha256 is not None:
            raise ValueError("Binary content cannot claim a normalized-text digest")
        for values, label in (
            (self.semantic_node_ids, "semantic node IDs"),
            (self.semantic_edge_ids, "semantic edge IDs"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        if self.semantic_status is SemanticFileStatus.NOT_APPLICABLE and (
            self.semantic_node_ids or self.semantic_edge_ids or not self.semantic_reason_code
        ):
            raise ValueError("Nonsemantic files require a reason and no semantic output")
        if self.semantic_status is SemanticFileStatus.PARSED and self.semantic_reason_code:
            raise ValueError("Parsed semantic files cannot carry an exclusion reason")
        if self.semantic_status is SemanticFileStatus.UNSUPPORTED:
            raise ValueError("Unsupported semantic files cannot enter an attested receipt")
        _verify_digest(self, "record_sha256")
        return self


class GraphElementOwner(_Model):
    path: str = Field(min_length=1, max_length=4096)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_id: str = Field(min_length=1, max_length=200)
    parser_version: str
    parser_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_owner(self) -> GraphElementOwner:
        _require_safe_locator(self.path, 4096)
        _require_semver(self.parser_version)
        _verify_digest(self, "owner_sha256")
        return self


class ProducedNode(_Model):
    node_id: str = Field(min_length=1, max_length=4600)
    raw_kind: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=4096)
    evidence_state: Literal["CONFIRMED", "INFERRED"]
    owners: tuple[GraphElementOwner, ...] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    semantic_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    element_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_node(self) -> ProducedNode:
        owner_paths = tuple(item.path for item in self.owners)
        if owner_paths != tuple(sorted(set(owner_paths))):
            raise ValueError("Node owners must be sorted and unique")
        semantic_body = self.model_dump(mode="json")
        semantic_body.pop("owners")
        semantic_body.pop("semantic_sha256")
        semantic_body.pop("element_sha256")
        if stable_sha256(semantic_body) != self.semantic_sha256:
            raise ValueError("Node semantic digest differs from canonical semantics")
        _verify_digest(self, "element_sha256")
        return self


class ProducedEdge(_Model):
    edge_id: str = Field(min_length=1, max_length=4600)
    source_id: str = Field(min_length=1, max_length=4600)
    raw_relation: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=4600)
    evidence_state: Literal["CONFIRMED", "INFERRED"]
    owners: tuple[GraphElementOwner, ...] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    semantic_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    element_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_edge(self) -> ProducedEdge:
        owner_paths = tuple(item.path for item in self.owners)
        if owner_paths != tuple(sorted(set(owner_paths))):
            raise ValueError("Edge owners must be sorted and unique")
        semantic_body = self.model_dump(mode="json")
        semantic_body.pop("owners")
        semantic_body.pop("semantic_sha256")
        semantic_body.pop("element_sha256")
        if stable_sha256(semantic_body) != self.semantic_sha256:
            raise ValueError("Edge semantic digest differs from canonical semantics")
        _verify_digest(self, "element_sha256")
        return self


class ProducedGraphSide(_Model):
    side: TreeSide
    tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dispositions: tuple[FileDisposition, ...]
    nodes: tuple[ProducedNode, ...]
    edges: tuple[ProducedEdge, ...]
    input_file_count: int = Field(ge=0)
    input_total_bytes: int = Field(ge=0)
    raw_node_count: int = Field(ge=0)
    raw_edge_count: int = Field(ge=0)
    raw_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    side_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_side(self) -> ProducedGraphSide:
        paths = tuple(item.path for item in self.dispositions)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("File dispositions must be complete, sorted and unique")
        if any(item.side is not self.side for item in self.dispositions):
            raise ValueError("File disposition side differs from graph side")
        if self.input_file_count != len(self.dispositions):
            raise ValueError("Input file count differs from dispositions")
        if self.input_total_bytes != sum(item.size_bytes for item in self.dispositions):
            raise ValueError("Input byte count differs from dispositions")
        node_ids = tuple(item.node_id for item in self.nodes)
        edge_ids = tuple(item.edge_id for item in self.edges)
        if node_ids != tuple(sorted(set(node_ids))):
            raise ValueError("Produced nodes must be sorted and unique")
        if edge_ids != tuple(sorted(set(edge_ids))):
            raise ValueError("Produced edges must be sorted and unique")
        if self.raw_node_count != len(self.nodes) or self.raw_edge_count != len(self.edges):
            raise ValueError("Graph counts differ from materialized elements")
        if not set(item.source_id for item in self.edges) <= set(node_ids) or not set(
            item.target_id for item in self.edges
        ) <= set(node_ids):
            raise ValueError("Produced edge has an unknown endpoint")
        _verify_digest(self, "side_receipt_sha256")
        return self


class GraphDeltaOperation(StrEnum):
    ADD = "ADD"
    MODIFY = "MODIFY"
    DELETE = "DELETE"


class GraphDeltaEntry(_Model):
    operation: GraphDeltaOperation
    entity_type: Literal["NODE", "EDGE"]
    entity_id: str = Field(min_length=1, max_length=4600)
    base_entity_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    candidate_entity_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    base_owner_paths: tuple[str, ...] = ()
    candidate_owner_paths: tuple[str, ...] = ()
    delta_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_delta(self) -> GraphDeltaEntry:
        for paths in (self.base_owner_paths, self.candidate_owner_paths):
            if paths != tuple(sorted(set(paths))):
                raise ValueError("Delta owners must be sorted and unique")
            for path in paths:
                _require_safe_locator(path, 4096)
        if self.operation is GraphDeltaOperation.ADD and (
            self.base_entity_sha256 is not None
            or self.candidate_entity_sha256 is None
            or self.base_owner_paths
            or not self.candidate_owner_paths
        ):
            raise ValueError("ADD must bind only a candidate entity")
        if self.operation is GraphDeltaOperation.DELETE and (
            self.base_entity_sha256 is None
            or self.candidate_entity_sha256 is not None
            or not self.base_owner_paths
            or self.candidate_owner_paths
        ):
            raise ValueError("DELETE must bind only a base entity")
        if self.operation is GraphDeltaOperation.MODIFY and (
            self.base_entity_sha256 is None
            or self.candidate_entity_sha256 is None
            or not self.base_owner_paths
            or not self.candidate_owner_paths
            or self.base_entity_sha256 == self.candidate_entity_sha256
        ):
            raise ValueError("MODIFY must bind distinct base and candidate entities")
        _verify_digest(self, "delta_sha256")
        return self


class GraphTombstone(_Model):
    entity_type: Literal["NODE", "EDGE"]
    entity_id: str = Field(min_length=1, max_length=4600)
    prior_entity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prior_owner_paths: tuple[str, ...] = Field(min_length=1)
    base_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_absence_proof_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tombstone_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_tombstone(self) -> GraphTombstone:
        if self.prior_owner_paths != tuple(sorted(set(self.prior_owner_paths))):
            raise ValueError("Tombstone owners must be sorted and unique")
        for path in self.prior_owner_paths:
            _require_safe_locator(path, 4096)
        expected_absence = stable_sha256(
            {
                "entity_type": self.entity_type,
                "entity_id": self.entity_id,
                "candidate_tree_sha256": self.candidate_tree_sha256,
                "candidate_graph_sha256": self.candidate_graph_sha256,
                "state": "ABSENT",
            }
        )
        if self.candidate_absence_proof_sha256 != expected_absence:
            raise ValueError("Tombstone absence proof is invalid")
        _verify_digest(self, "tombstone_sha256")
        return self


_PERMANENT_GAPS = (
    "CANDIDATE_BUILD_NOT_VERIFIED",
    "CHANGE_SEED_SCOPE_NOT_ATTESTED",
    "CONFLICT_SCOPE_NOT_ATTESTED",
    "DEPLOYMENT_NOT_ATTESTED",
    "GIT_COMMIT_SIGNATURE_NOT_ATTESTED",
    "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
    "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
    "REPOSITORY_ORIGIN_NOT_ATTESTED",
    "RISK_FACTORS_NOT_ATTESTED",
    "SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE",
    "TEST_EXECUTION_SCOPE_NOT_ATTESTED",
    "TEST_OBLIGATION_SCOPE_NOT_ATTESTED",
    "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
)


class GraphProductionArtifact(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    graph_scope: Literal["SALESFORCE_CHANGE_EVIDENCE_GRAPH"] = "SALESFORCE_CHANGE_EVIDENCE_GRAPH"
    file_inventory_input_tree_attested: Literal[True] = True
    supported_semantic_graph_input_tree_attested: Literal[True] = True
    semantic_source_family_coverage: Literal["SUPPORTED_FAMILIES"] = "SUPPORTED_FAMILIES"
    project_id: str = Field(min_length=1, max_length=200)
    repository_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: str
    policy_version: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    producer_id: str
    producer_version: str
    producer_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adapter_id: str
    adapter_version: str
    adapter_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adapter_contract_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalizer_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: str
    valid_until: str
    freshness_seconds: int = Field(ge=1)
    base: ProducedGraphSide
    candidate: ProducedGraphSide
    delta: tuple[GraphDeltaEntry, ...]
    tombstones: tuple[GraphTombstone, ...]
    blocking_gap_codes: tuple[str, ...] = _PERMANENT_GAPS
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> GraphProductionArtifact:
        _require_semver(self.policy_version)
        _require_semver(self.producer_version)
        _require_semver(self.adapter_version)
        observed = _parse_timestamp(self.observed_at)
        valid = _parse_timestamp(self.valid_until)
        if valid <= observed or int((valid - observed).total_seconds()) != self.freshness_seconds:
            raise ValueError("Graph-production freshness is inconsistent")
        if self.base.side is not TreeSide.BASE or self.candidate.side is not TreeSide.CANDIDATE:
            raise ValueError("Graph sides are swapped")
        delta_keys = tuple(
            (item.entity_type, item.entity_id, item.operation.value) for item in self.delta
        )
        if delta_keys != tuple(sorted(set(delta_keys))):
            raise ValueError("Graph delta must be sorted and unique")
        tombstone_ids = tuple((item.entity_type, item.entity_id) for item in self.tombstones)
        if tombstone_ids != tuple(sorted(set(tombstone_ids))):
            raise ValueError("Tombstones must be sorted and unique")
        deleted = tuple(
            (item.entity_type, item.entity_id)
            for item in self.delta
            if item.operation is GraphDeltaOperation.DELETE
        )
        if tombstone_ids != deleted:
            raise ValueError("Every deleted entity must have exactly one tombstone")
        expected_delta, expected_tombstones = _make_delta(self.base, self.candidate)
        if self.delta != expected_delta or self.tombstones != expected_tombstones:
            raise ValueError("Graph delta or tombstones differ from graph materializations")
        if self.blocking_gap_codes != _PERMANENT_GAPS:
            raise ValueError("Permanent graph-production gaps cannot be omitted")
        _verify_digest(self, "receipt_sha256")
        return self


class GraphProductionGapCode(StrEnum):
    CANDIDATE_BUILD_NOT_VERIFIED = "CANDIDATE_BUILD_NOT_VERIFIED"
    CHANGE_SEED_SCOPE_NOT_ATTESTED = "CHANGE_SEED_SCOPE_NOT_ATTESTED"
    CONFLICT_SCOPE_NOT_ATTESTED = "CONFLICT_SCOPE_NOT_ATTESTED"
    DEPLOYMENT_NOT_ATTESTED = "DEPLOYMENT_NOT_ATTESTED"
    GIT_COMMIT_SIGNATURE_NOT_ATTESTED = "GIT_COMMIT_SIGNATURE_NOT_ATTESTED"
    HUMAN_APPROVAL_SCOPE_NOT_ATTESTED = "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED"
    RELEASE_EVIDENCE_MODEL_INCOMPLETE = "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    REPOSITORY_ORIGIN_NOT_ATTESTED = "REPOSITORY_ORIGIN_NOT_ATTESTED"
    RISK_FACTORS_NOT_ATTESTED = "RISK_FACTORS_NOT_ATTESTED"
    SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE = "SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE"
    TEST_EXECUTION_SCOPE_NOT_ATTESTED = "TEST_EXECUTION_SCOPE_NOT_ATTESTED"
    TEST_OBLIGATION_SCOPE_NOT_ATTESTED = "TEST_OBLIGATION_SCOPE_NOT_ATTESTED"
    UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED = "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"
    POLICY_ROOT_MISMATCH = "POLICY_ROOT_MISMATCH"
    PRODUCER_IMPLEMENTATION_MISMATCH = "PRODUCER_IMPLEMENTATION_MISMATCH"
    ADAPTER_IMPLEMENTATION_MISMATCH = "ADAPTER_IMPLEMENTATION_MISMATCH"
    VERIFIED_CHANGE_POLICY_MISMATCH = "VERIFIED_CHANGE_POLICY_MISMATCH"
    ONTOLOGY_POLICY_MISMATCH = "ONTOLOGY_POLICY_MISMATCH"
    SOURCE_PROFILE_POLICY_MISMATCH = "SOURCE_PROFILE_POLICY_MISMATCH"
    CHANGE_REPLAY_FAILED = "CHANGE_REPLAY_FAILED"
    REPOSITORY_ROOT_MISMATCH = "REPOSITORY_ROOT_MISMATCH"
    TREE_BYTES_MISMATCH = "TREE_BYTES_MISMATCH"
    TREE_MANIFEST_MISMATCH = "TREE_MANIFEST_MISMATCH"
    UNSAFE_PATH = "UNSAFE_PATH"
    OUTPUT_COLLISION = "OUTPUT_COLLISION"
    SEMANTIC_ADAPTER_FAILED = "SEMANTIC_ADAPTER_FAILED"
    SEMANTIC_OUTPUT_INVALID = "SEMANTIC_OUTPUT_INVALID"
    UNSUPPORTED_SOURCE_FAMILY = "UNSUPPORTED_SOURCE_FAMILY"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    GIT_COMMAND_FAILED = "GIT_COMMAND_FAILED"
    GIT_TIMEOUT = "GIT_TIMEOUT"
    CONCURRENT_MUTATION = "CONCURRENT_MUTATION"
    TIME_AUTHORITY_UNAVAILABLE = "TIME_AUTHORITY_UNAVAILABLE"
    CAPTURE_FROM_FUTURE = "CAPTURE_FROM_FUTURE"
    CAPTURE_EXPIRED = "CAPTURE_EXPIRED"
    CAPTURE_TAMPERED = "CAPTURE_TAMPERED"


class GraphProductionGap(_Model):
    code: GraphProductionGapCode
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["RELEASE_ONLY"] = "RELEASE_ONLY"
    blocking: Literal[True] = True


class GraphProductionEvaluation(_Model):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    file_inventory_input_tree_attested: bool
    supported_semantic_graph_input_tree_attested: bool
    semantic_source_family_coverage: Literal["SUPPORTED_FAMILIES"] = "SUPPORTED_FAMILIES"
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    artifact: GraphProductionArtifact | None = None
    gaps: tuple[GraphProductionGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> GraphProductionEvaluation:
        expected = self.artifact is not None
        if self.file_inventory_input_tree_attested is not expected:
            raise ValueError("File-inventory attestation differs from artifact presence")
        if self.supported_semantic_graph_input_tree_attested is not expected:
            raise ValueError("Supported semantic-graph attestation differs from artifact presence")
        keys = tuple((item.code.value, item.identity_sha256) for item in self.gaps)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Graph-production gaps must be sorted and unique")
        if not set(_PERMANENT_GAPS).issubset(item.code.value for item in self.gaps):
            raise ValueError("Permanent graph-production gaps cannot be omitted")
        _verify_digest(self, "evaluation_sha256")
        return self


DEFAULT_GRAPH_PRODUCER_POLICY_SHA256 = (
    "d3d5f6a7efc7a636b477c83b6609b08427dcc24b762d63b2b6cf3dab3e2bc290"
)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_OBJECT_ID = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_RESERVED_RAW_ATTRIBUTE_KEYS = frozenset(
    {
        "id",
        "kind",
        "label",
        "from",
        "to",
        "relation",
        "evidenceState",
        "source",
        "sourceSnapshot",
        "sourceHash",
        "validUntil",
        "extractorId",
        "owners",
        "sourceArtifactSha256",
        "extractorVersion",
        "extractorImplementationSha256",
    }
)


def _require_semver(value: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError("Version must use semantic versioning")


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be canonical UTC seconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _require_safe_locator(value: str, maximum_bytes: int) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ValueError("Repository locator is unsafe")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Repository locator is unsafe")
    return value


def _verify_digest(model: BaseModel, field: str) -> None:
    body = model.model_dump(mode="json")
    declared = body.pop(field)
    if stable_sha256(body) != declared:
        raise ValueError(f"{field} differs from canonical content")


def _implementation_sha256(content: bytes) -> str:
    normalized = re.sub(
        rb"DEFAULT_GRAPH_PRODUCER_POLICY_SHA256\s*=\s*"
        rb'(?:"[a-f0-9]{64}"|\(\s*"[a-f0-9]{64}"\s*\))',
        b'DEFAULT_GRAPH_PRODUCER_POLICY_SHA256 = (\n    "' + (b"0" * 64) + b'"\n)',
        content,
        count=1,
    )
    return hashlib.sha256(normalized).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GraphProductionContractError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def load_graph_producer_policy(
    path: Path,
    *,
    implementation_root: Path,
    expected_sha256: str = DEFAULT_GRAPH_PRODUCER_POLICY_SHA256,
) -> GraphProducerPolicy:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except GraphProductionContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphProductionContractError("Graph-producer policy cannot be loaded") from exc
    if not isinstance(document, dict) or document.get("sha256") != contract_sha256(document):
        raise GraphProductionContractError("Graph-producer policy self-hash is invalid")
    if document.get("sha256") != expected_sha256:
        raise GraphProductionContractError("Graph-producer policy is not externally pinned")
    try:
        policy = GraphProducerPolicy.model_validate(document)
    except ValidationError as exc:
        raise GraphProductionContractError("Graph-producer policy is invalid") from exc
    loaded = Path(inspect.getsourcefile(LocalTreeGraphProducer) or "").resolve()
    for pin in (
        policy.producer,
        policy.adapter,
        policy.adapter_contract,
        policy.normalizer,
    ):
        implementation = (implementation_root.resolve() / pin.implementation_locator).resolve()
        try:
            implementation.relative_to(implementation_root.resolve())
        except ValueError as exc:
            raise GraphProductionContractError("Graph implementation escapes its root") from exc
        if pin is policy.producer and implementation != loaded:
            raise GraphProductionContractError("Loaded graph implementation differs from policy")
        try:
            content = implementation.read_bytes()
        except OSError as exc:
            raise GraphProductionContractError("Graph implementation is unavailable") from exc
        digest = (
            _implementation_sha256(content)
            if pin is policy.producer
            else hashlib.sha256(content).hexdigest()
        )
        if digest != pin.implementation_sha256:
            raise GraphProductionContractError("Graph implementation digest is invalid")
    return policy


def _identity(*values: Any) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _gap(code: GraphProductionGapCode, *identity: Any) -> GraphProductionGap:
    return GraphProductionGap(code=code, identity_sha256=_identity(code.value, *identity))


def _planned_gaps() -> list[GraphProductionGap]:
    return [_gap(GraphProductionGapCode(code)) for code in _PERMANENT_GAPS]


def _sorted_gaps(gaps: list[GraphProductionGap]) -> tuple[GraphProductionGap, ...]:
    return tuple(sorted(set(gaps), key=lambda item: (item.code.value, item.identity_sha256)))


def _evaluation(
    policy: GraphProducerPolicy,
    evaluated_at: str,
    gaps: list[GraphProductionGap],
    artifact: GraphProductionArtifact | None,
) -> GraphProductionEvaluation:
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "file_inventory_input_tree_attested": artifact is not None,
        "supported_semantic_graph_input_tree_attested": artifact is not None,
        "semantic_source_family_coverage": "SUPPORTED_FAMILIES",
        "policy_sha256": policy.sha256,
        "evaluated_at": evaluated_at,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "gaps": [item.model_dump(mode="json") for item in _sorted_gaps(gaps)],
    }
    return GraphProductionEvaluation.model_validate(
        {**body, "evaluation_sha256": stable_sha256(body)}
    )


def _content_identity(content: bytes) -> tuple[str, str | None]:
    if b"\x00" in content:
        return "BINARY", None
    try:
        text = content.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return "BINARY", None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").encode()
    return "TEXT_UTF8", hashlib.sha256(normalized).hexdigest()


def _owner(
    path: str,
    manifest: dict[str, GitFileEntry],
    parser_id: str,
    parser_version: str,
    implementation_sha256: str,
) -> GraphElementOwner:
    item = manifest.get(path)
    if item is None:
        raise _ProductionRejected(GraphProductionGapCode.TREE_MANIFEST_MISMATCH, "owner")
    body = {
        "path": path,
        "content_sha256": item.content_sha256,
        "parser_id": parser_id,
        "parser_version": parser_version,
        "parser_implementation_sha256": implementation_sha256,
    }
    return GraphElementOwner.model_validate({**body, "owner_sha256": stable_sha256(body)})


def _record(
    side: TreeSide,
    item: GitFileEntry,
    content: bytes,
    semantic: AdapterFileResult,
    policy: GraphProducerPolicy,
) -> FileDisposition:
    content_kind, normalized_sha = _content_identity(content)
    body = {
        "side": side.value,
        "path": item.path,
        "disposition": "ACCOUNTED",
        "mode": item.mode,
        "tracking": item.tracking,
        "size_bytes": item.size_bytes,
        "content_sha256": item.content_sha256,
        "content_kind": content_kind,
        "normalized_text_sha256": normalized_sha,
        "inventory_parser_id": policy.inventory_parser_id,
        "inventory_parser_version": policy.inventory_parser_version,
        "semantic_status": semantic.status.value,
        "semantic_parser_id": semantic.parser_id,
        "semantic_reason_code": semantic.reason_code,
        "semantic_node_ids": list(semantic.node_ids),
        "semantic_edge_ids": list(semantic.edge_ids),
        "adapter_implementation_sha256": policy.adapter.implementation_sha256,
        "source_artifact_id": f"file:{item.path}",
    }
    return FileDisposition.model_validate({**body, "record_sha256": stable_sha256(body)})


def _element_owner_paths(element: ProducedNode | ProducedEdge) -> tuple[str, ...]:
    return tuple(item.path for item in element.owners)


def _raw_graph(
    nodes: tuple[ProducedNode, ...],
    edges: tuple[ProducedEdge, ...],
    source_snapshot_sha256: str,
    policy: GraphProducerPolicy,
) -> dict[str, Any]:
    def provenance(owners: tuple[GraphElementOwner, ...]) -> dict[str, Any]:
        owner_body = [item.model_dump(mode="json") for item in owners]
        parser_ids = tuple(sorted({item.parser_id for item in owners}))
        return {
            "source": owners[0].path,
            "extractorId": (
                parser_ids[0] if len(parser_ids) == 1 else policy.adapter.implementation_id
            ),
            "owners": owner_body,
            "ownerReceiptSha256": stable_sha256(owner_body),
        }

    return {
        "schemaVersion": "1.0.0",
        "sourceSnapshot": source_snapshot_sha256,
        "nodes": [
            {
                **item.attributes,
                "id": item.node_id,
                "kind": item.raw_kind,
                "label": item.label,
                "evidenceState": item.evidence_state,
                "sourceSnapshot": source_snapshot_sha256,
                **provenance(item.owners),
            }
            for item in nodes
        ],
        "edges": [
            {
                **item.attributes,
                "id": item.edge_id,
                "from": item.source_id,
                "relation": item.raw_relation,
                "to": item.target_id,
                "evidenceState": item.evidence_state,
                "sourceSnapshot": source_snapshot_sha256,
                **provenance(item.owners),
            }
            for item in edges
        ],
    }


def materialize_source_attested_reasoning_graph(
    raw_graph: dict[str, Any],
    *,
    source_graph_sha256: str,
) -> dict[str, Any]:
    """Overlay the immutable content root used as per-record source authority."""

    if not re.fullmatch(r"[a-f0-9]{64}", source_graph_sha256):
        raise ValueError("Reasoning graph source digest is invalid")
    return {
        **raw_graph,
        "nodes": [
            {
                **item,
                "sourceHash": source_graph_sha256,
            }
            for item in raw_graph["nodes"]
        ],
        "edges": [
            {
                **item,
                "sourceHash": source_graph_sha256,
            }
            for item in raw_graph["edges"]
        ],
    }


def _graph_elements(
    side: ProducedGraphSide,
) -> dict[tuple[str, str], ProducedNode | ProducedEdge]:
    values: dict[tuple[str, str], ProducedNode | ProducedEdge] = {}
    values.update({("NODE", item.node_id): item for item in side.nodes})
    values.update({("EDGE", item.edge_id): item for item in side.edges})
    return values


def _make_delta(
    base: ProducedGraphSide, candidate: ProducedGraphSide
) -> tuple[tuple[GraphDeltaEntry, ...], tuple[GraphTombstone, ...]]:
    before = _graph_elements(base)
    after = _graph_elements(candidate)
    deltas: list[GraphDeltaEntry] = []
    tombstones: list[GraphTombstone] = []
    for entity_type, entity_id in sorted(set(before) | set(after)):
        old = before.get((entity_type, entity_id))
        new = after.get((entity_type, entity_id))
        old_sha = old.semantic_sha256 if old else None
        new_sha = new.semantic_sha256 if new else None
        old_owners = list(_element_owner_paths(old)) if old else []
        new_owners = list(_element_owner_paths(new)) if new else []
        if old is None:
            operation = "ADD"
        elif new is None:
            operation = "DELETE"
        elif old_sha != new_sha:
            operation = "MODIFY"
        else:
            continue
        body = {
            "operation": operation,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "base_entity_sha256": old_sha,
            "candidate_entity_sha256": new_sha,
            "base_owner_paths": old_owners,
            "candidate_owner_paths": new_owners,
        }
        deltas.append(GraphDeltaEntry.model_validate({**body, "delta_sha256": stable_sha256(body)}))
        if operation == "DELETE":
            absence = stable_sha256(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "candidate_tree_sha256": candidate.tree_sha256,
                    "candidate_graph_sha256": candidate.normalized_graph_sha256,
                    "state": "ABSENT",
                }
            )
            tombstone_body = {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "prior_entity_sha256": old_sha,
                "prior_owner_paths": old_owners,
                "base_tree_sha256": base.tree_sha256,
                "base_graph_sha256": base.normalized_graph_sha256,
                "candidate_tree_sha256": candidate.tree_sha256,
                "candidate_graph_sha256": candidate.normalized_graph_sha256,
                "candidate_absence_proof_sha256": absence,
            }
            tombstones.append(
                GraphTombstone.model_validate(
                    {**tombstone_body, "tombstone_sha256": stable_sha256(tombstone_body)}
                )
            )
    return tuple(deltas), tuple(tombstones)


def _static_artifact(artifact: GraphProductionArtifact) -> dict[str, Any]:
    body = artifact.model_dump(mode="json")
    for key in (
        "observed_at",
        "valid_until",
        "freshness_seconds",
        "verified_change_manifest_sha256",
        "receipt_sha256",
    ):
        body.pop(key)
    return body


@dataclass(frozen=True, slots=True)
class GraphProductionInputs:
    candidate: VerifiedChangeSet
    change_producer: LocalGitChangeProducer
    repository_hint: Path
    ontology: CanonicalOntology
    profile: SourceGraphProfile
    reuse_request_scoped_capture: bool = False


@dataclass(frozen=True, slots=True)
class LocalTreeGraphProducer:
    policy: GraphProducerPolicy

    def capture(self, inputs: GraphProductionInputs) -> GraphProductionEvaluation:
        gaps = _planned_gaps()
        if type(self) is not LocalTreeGraphProducer:
            return _evaluation(
                self.policy,
                "unavailable",
                [*gaps, _gap(GraphProductionGapCode.PRODUCER_IMPLEMENTATION_MISMATCH)],
                None,
            )
        try:
            now = _utc_now()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
            now = now.astimezone(UTC).replace(microsecond=0)
        except (OSError, ValueError):
            return _evaluation(
                self.policy,
                "unavailable",
                [*gaps, _gap(GraphProductionGapCode.TIME_AUTHORITY_UNAVAILABLE)],
                None,
            )
        now_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            artifact = self._capture(inputs, now)
        except _ProductionRejected as exc:
            return _evaluation(self.policy, now_text, [*gaps, _gap(exc.code, *exc.identity)], None)
        return _evaluation(self.policy, artifact.observed_at, gaps, artifact)

    def verify(
        self, candidate: GraphProductionArtifact, inputs: GraphProductionInputs
    ) -> GraphProductionEvaluation:
        current = self.capture(inputs)
        now = (
            _parse_timestamp(current.evaluated_at)
            if current.evaluated_at != "unavailable"
            else None
        )
        try:
            validated = GraphProductionArtifact.model_validate(candidate.model_dump(mode="json"))
        except (AttributeError, TypeError, ValidationError, ValueError):
            validated = None
        if validated is not None and validated.verified_change_manifest_sha256 != (
            inputs.candidate.manifest_sha256
        ):
            validated = None
        if validated is not None and now is not None:
            if _parse_timestamp(validated.observed_at) > now:
                return _evaluation(
                    self.policy,
                    current.evaluated_at,
                    [*current.gaps, _gap(GraphProductionGapCode.CAPTURE_FROM_FUTURE)],
                    None,
                )
            if _parse_timestamp(validated.valid_until) <= now:
                return _evaluation(
                    self.policy,
                    current.evaluated_at,
                    [*current.gaps, _gap(GraphProductionGapCode.CAPTURE_EXPIRED)],
                    None,
                )
        if current.artifact is None:
            return current
        if validated is None or _static_artifact(validated) != _static_artifact(current.artifact):
            return _evaluation(
                self.policy,
                current.evaluated_at,
                [*current.gaps, _gap(GraphProductionGapCode.CAPTURE_TAMPERED)],
                None,
            )
        return current

    def verify_bound(
        self,
        candidate: GraphProductionArtifact,
        inputs: GraphProductionInputs,
    ) -> GraphProductionEvaluation:
        """Replay the full graph producer; caller labels cannot substitute for provenance."""

        return self.verify(candidate, inputs)

    def _capture(self, inputs: GraphProductionInputs, now: datetime) -> GraphProductionArtifact:
        self._require_runtime_policy(inputs)
        if type(inputs.change_producer) is not LocalGitChangeProducer:
            raise _ProductionRejected(GraphProductionGapCode.CHANGE_REPLAY_FAILED, "producer-type")
        change_verifier = (
            inputs.change_producer.verify_bound
            if inputs.reuse_request_scoped_capture
            else inputs.change_producer.verify
        )
        initial_replay = change_verifier(inputs.candidate, inputs.repository_hint)
        if initial_replay.artifact is None or not initial_replay.change_capture_complete:
            raise _ProductionRejected(GraphProductionGapCode.CHANGE_REPLAY_FAILED)
        root = self._repository_root(inputs)
        base_bytes = self._read_base(root, inputs.candidate)
        candidate_bytes = self._read_candidate(root, inputs.candidate)
        base = self._produce_side(
            TreeSide.BASE,
            inputs.candidate.base_files,
            base_bytes,
            inputs.candidate.base_tree_sha256,
            inputs,
        )
        candidate = self._produce_side(
            TreeSide.CANDIDATE,
            inputs.candidate.candidate_files,
            candidate_bytes,
            inputs.candidate.candidate_input_tree_sha256,
            inputs,
        )
        final_replay = change_verifier(inputs.candidate, inputs.repository_hint)
        if final_replay.artifact is None or not final_replay.change_capture_complete:
            raise _ProductionRejected(GraphProductionGapCode.CONCURRENT_MUTATION)
        try:
            final_sample = _utc_now()
            if final_sample.tzinfo is None or final_sample.utcoffset() is None:
                raise ValueError
            final_now = final_sample.astimezone(UTC).replace(microsecond=0)
            if final_now < now:
                raise ValueError
        except (OSError, ValueError):
            raise _ProductionRejected(GraphProductionGapCode.TIME_AUTHORITY_UNAVAILABLE) from None
        authorities = (
            inputs.candidate,
            initial_replay.artifact,
            final_replay.artifact,
        )
        for authority in authorities:
            if _parse_timestamp(authority.observed_at) > final_now:
                raise _ProductionRejected(GraphProductionGapCode.CAPTURE_FROM_FUTURE)
            if _parse_timestamp(authority.valid_until) <= final_now:
                raise _ProductionRejected(GraphProductionGapCode.CAPTURE_EXPIRED)
        delta, tombstones = _make_delta(base, candidate)
        valid_until = min(
            final_now + timedelta(seconds=self.policy.freshness_seconds),
            *(_parse_timestamp(authority.valid_until) for authority in authorities),
        )
        freshness_seconds = int((valid_until - final_now).total_seconds())
        if freshness_seconds < 1:
            raise _ProductionRejected(GraphProductionGapCode.CAPTURE_EXPIRED)
        body = {
            "schema_version": "1.0.0",
            "authority_scope": "ANALYSIS_ONLY",
            "release_eligible": False,
            "graph_scope": "SALESFORCE_CHANGE_EVIDENCE_GRAPH",
            "file_inventory_input_tree_attested": True,
            "supported_semantic_graph_input_tree_attested": True,
            "semantic_source_family_coverage": "SUPPORTED_FAMILIES",
            "project_id": inputs.candidate.project_id,
            "repository_identity_sha256": inputs.candidate.repository_identity_sha256,
            "verified_change_manifest_sha256": inputs.candidate.manifest_sha256,
            "verified_change_policy_sha256": inputs.candidate.policy_sha256,
            "policy_id": self.policy.policy_id,
            "policy_version": self.policy.policy_version,
            "policy_sha256": self.policy.sha256,
            "producer_id": self.policy.producer.implementation_id,
            "producer_version": self.policy.producer.implementation_version,
            "producer_implementation_sha256": self.policy.producer.implementation_sha256,
            "adapter_id": self.policy.adapter.implementation_id,
            "adapter_version": self.policy.adapter.implementation_version,
            "adapter_implementation_sha256": self.policy.adapter.implementation_sha256,
            "adapter_contract_implementation_sha256": (
                self.policy.adapter_contract.implementation_sha256
            ),
            "normalizer_implementation_sha256": self.policy.normalizer.implementation_sha256,
            "ontology_sha256": inputs.ontology.sha256,
            "source_profile_sha256": inputs.profile.sha256,
            "observed_at": final_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "freshness_seconds": freshness_seconds,
            "base": base.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
            "delta": [item.model_dump(mode="json") for item in delta],
            "tombstones": [item.model_dump(mode="json") for item in tombstones],
            "blocking_gap_codes": list(_PERMANENT_GAPS),
        }
        if len(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()) > (
            self.policy.maximum_receipt_bytes
        ):
            raise _ProductionRejected(GraphProductionGapCode.CAPACITY_EXCEEDED, "receipt")
        return GraphProductionArtifact.model_validate(
            {**body, "receipt_sha256": stable_sha256(body)}
        )

    def _require_runtime_policy(self, inputs: GraphProductionInputs) -> None:
        body = self.policy.model_dump(mode="json", by_alias=True)
        if (
            contract_sha256(body) != self.policy.sha256
            or self.policy.sha256 != DEFAULT_GRAPH_PRODUCER_POLICY_SHA256
        ):
            raise _ProductionRejected(GraphProductionGapCode.POLICY_ROOT_MISMATCH)
        loaded = Path(inspect.getsourcefile(LocalTreeGraphProducer) or "").resolve()
        try:
            digest = _implementation_sha256(loaded.read_bytes())
        except OSError:
            raise _ProductionRejected(
                GraphProductionGapCode.PRODUCER_IMPLEMENTATION_MISMATCH
            ) from None
        if digest != self.policy.producer.implementation_sha256:
            raise _ProductionRejected(GraphProductionGapCode.PRODUCER_IMPLEMENTATION_MISMATCH)
        adapter_loaded = Path(inspect.getsourcefile(SalesforceSemanticGraphAdapter) or "").resolve()
        expected_adapter = (
            loaded.parents[2] / self.policy.adapter.implementation_locator
        ).resolve()
        try:
            adapter_digest = hashlib.sha256(adapter_loaded.read_bytes()).hexdigest()
        except OSError:
            raise _ProductionRejected(
                GraphProductionGapCode.ADAPTER_IMPLEMENTATION_MISMATCH
            ) from None
        if (
            adapter_loaded != expected_adapter
            or adapter_digest != self.policy.adapter.implementation_sha256
            or SalesforceSemanticGraphAdapter.implementation_id
            != self.policy.adapter.implementation_id
            or SalesforceSemanticGraphAdapter.implementation_version
            != self.policy.adapter.implementation_version
        ):
            raise _ProductionRejected(GraphProductionGapCode.ADAPTER_IMPLEMENTATION_MISMATCH)
        dependency_files = (
            (
                self.policy.adapter_contract,
                Path(inspect.getsourcefile(GraphSemanticAdapter) or "").resolve(),
            ),
            (
                self.policy.normalizer,
                Path(inspect.getsourcefile(normalize_source_graph) or "").resolve(),
            ),
        )
        for pin, dependency in dependency_files:
            expected = (loaded.parents[2] / pin.implementation_locator).resolve()
            try:
                dependency_digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
            except OSError:
                raise _ProductionRejected(
                    GraphProductionGapCode.ADAPTER_IMPLEMENTATION_MISMATCH
                ) from None
            if dependency != expected or dependency_digest != pin.implementation_sha256:
                raise _ProductionRejected(GraphProductionGapCode.ADAPTER_IMPLEMENTATION_MISMATCH)
        change_policy = inputs.change_producer.policy
        change_pin = self.policy.verified_change_policy
        if (
            change_policy.policy_id,
            change_policy.policy_version,
            change_policy.sha256,
        ) != (change_pin.contract_id, change_pin.contract_version, change_pin.contract_sha256):
            raise _ProductionRejected(GraphProductionGapCode.VERIFIED_CHANGE_POLICY_MISMATCH)
        ontology_pin = self.policy.ontology
        if (
            type(inputs.ontology) is not CanonicalOntology
            or contract_sha256(inputs.ontology.model_dump(mode="json", by_alias=True))
            != inputs.ontology.sha256
            or (
                inputs.ontology.ontology_id,
                inputs.ontology.ontology_version,
                inputs.ontology.sha256,
            )
            != (
                ontology_pin.contract_id,
                ontology_pin.contract_version,
                ontology_pin.contract_sha256,
            )
        ):
            raise _ProductionRejected(GraphProductionGapCode.ONTOLOGY_POLICY_MISMATCH)
        profile_pin = self.policy.source_profile
        if (
            type(inputs.profile) is not SourceGraphProfile
            or contract_sha256(inputs.profile.model_dump(mode="json", by_alias=True))
            != inputs.profile.sha256
            or inputs.profile.ontology.ontology_id != inputs.ontology.ontology_id
            or inputs.profile.ontology.ontology_version != inputs.ontology.ontology_version
            or inputs.profile.ontology.ontology_sha256 != inputs.ontology.sha256
            or (
                inputs.profile.profile_id,
                inputs.profile.profile_version,
                inputs.profile.sha256,
            )
            != (
                profile_pin.contract_id,
                profile_pin.contract_version,
                profile_pin.contract_sha256,
            )
        ):
            raise _ProductionRejected(GraphProductionGapCode.SOURCE_PROFILE_POLICY_MISMATCH)

    def _repository_root(self, inputs: GraphProductionInputs) -> Path:
        try:
            expected = inputs.change_producer.expected_repository_root.resolve()
            hint = inputs.repository_hint.resolve()
        except OSError:
            raise _ProductionRejected(GraphProductionGapCode.REPOSITORY_ROOT_MISMATCH) from None
        if expected != hint:
            raise _ProductionRejected(GraphProductionGapCode.REPOSITORY_ROOT_MISMATCH)
        try:
            actual = Path(
                self._git(hint, "rev-parse", "--show-toplevel").decode("utf-8", "strict").strip()
            ).resolve()
        except (OSError, UnicodeDecodeError):
            raise _ProductionRejected(GraphProductionGapCode.REPOSITORY_ROOT_MISMATCH) from None
        if actual != expected:
            raise _ProductionRejected(GraphProductionGapCode.REPOSITORY_ROOT_MISMATCH)
        return actual

    def _git(self, root: Path, *arguments: str) -> bytes:
        try:
            completed = subprocess.run(
                ["git", "-c", "core.quotepath=false", "-C", str(root), *arguments],
                capture_output=True,
                check=False,
                timeout=self.policy.git_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise _ProductionRejected(GraphProductionGapCode.GIT_TIMEOUT) from None
        except OSError:
            raise _ProductionRejected(GraphProductionGapCode.GIT_COMMAND_FAILED) from None
        if (
            completed.returncode != 0
            or len(completed.stdout) > self.policy.maximum_git_output_bytes
            or len(completed.stderr) > self.policy.maximum_git_output_bytes
        ):
            raise _ProductionRejected(GraphProductionGapCode.GIT_COMMAND_FAILED)
        return completed.stdout

    def _read_base(self, root: Path, candidate: VerifiedChangeSet) -> dict[str, bytes]:
        output = self._git(root, "ls-tree", "-rz", "--full-tree", candidate.base_tree_oid)
        objects: dict[str, str] = {}
        for raw in output.split(b"\0"):
            if not raw:
                continue
            try:
                metadata, path_raw = raw.split(b"\t", 1)
                mode, kind, object_raw = metadata.split(b" ", 2)
                path = path_raw.decode("utf-8", "strict")
                object_id = object_raw.decode("ascii")
            except (ValueError, UnicodeDecodeError):
                raise _ProductionRejected(GraphProductionGapCode.TREE_MANIFEST_MISMATCH) from None
            if kind != b"blob" or mode.decode("ascii") not in {"100644", "100755"}:
                raise _ProductionRejected(GraphProductionGapCode.TREE_MANIFEST_MISMATCH)
            if not _OBJECT_ID.fullmatch(object_id) or path in objects:
                raise _ProductionRejected(GraphProductionGapCode.TREE_MANIFEST_MISMATCH)
            try:
                _require_safe_locator(path, self.policy.maximum_path_bytes)
            except ValueError:
                raise _ProductionRejected(GraphProductionGapCode.UNSAFE_PATH) from None
            objects[path] = object_id
        if tuple(sorted(objects)) != tuple(item.path for item in candidate.base_files):
            raise _ProductionRejected(GraphProductionGapCode.TREE_MANIFEST_MISMATCH, "base")
        try:
            blobs = read_git_blobs_batch(
                root,
                tuple(objects[item.path] for item in candidate.base_files),
                timeout_seconds=self.policy.git_timeout_seconds,
                maximum_file_bytes=self.policy.maximum_file_bytes,
                maximum_total_bytes=self.policy.maximum_total_bytes,
                maximum_stderr_bytes=self.policy.maximum_git_output_bytes,
            )
        except subprocess.TimeoutExpired:
            raise _ProductionRejected(GraphProductionGapCode.GIT_TIMEOUT) from None
        except OverflowError:
            raise _ProductionRejected(
                GraphProductionGapCode.CAPACITY_EXCEEDED, "tree-bytes"
            ) from None
        except (OSError, RuntimeError, TypeError, ValueError):
            raise _ProductionRejected(GraphProductionGapCode.GIT_COMMAND_FAILED, "batch") from None
        contents: dict[str, bytes] = {}
        total = 0
        for item in candidate.base_files:
            content = blobs[objects[item.path]]
            total += len(content)
            self._check_content(item, content, total)
            contents[item.path] = content
        return contents

    def _read_candidate(self, root: Path, candidate: VerifiedChangeSet) -> dict[str, bytes]:
        contents: dict[str, bytes] = {}
        total = 0
        if len(candidate.candidate_files) > self.policy.maximum_files:
            raise _ProductionRejected(GraphProductionGapCode.CAPACITY_EXCEEDED, "files")
        for item in candidate.candidate_files:
            try:
                _require_safe_locator(item.path, self.policy.maximum_path_bytes)
            except ValueError:
                raise _ProductionRejected(GraphProductionGapCode.UNSAFE_PATH) from None
            target = root.joinpath(*PurePosixPath(item.path).parts)
            try:
                details = target.lstat()
                relative = target.resolve(strict=True).relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                raise _ProductionRejected(GraphProductionGapCode.CONCURRENT_MUTATION) from None
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            file_attributes = getattr(details, "st_file_attributes", 0)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_ISLNK(details.st_mode)
                or bool(file_attributes & reparse_flag)
                or relative != Path(*PurePosixPath(item.path).parts)
            ):
                raise _ProductionRejected(GraphProductionGapCode.TREE_BYTES_MISMATCH)
            if (
                details.st_size != item.size_bytes
                or item.size_bytes > self.policy.maximum_file_bytes
                or total + item.size_bytes > self.policy.maximum_total_bytes
            ):
                raise _ProductionRejected(GraphProductionGapCode.CAPACITY_EXCEEDED, "tree-bytes")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor: int | None = None
            try:
                descriptor = os.open(target, flags)
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_size != item.size_bytes
                    or (details.st_dev, details.st_ino, details.st_size)
                    != (opened.st_dev, opened.st_ino, opened.st_size)
                ):
                    raise _ProductionRejected(GraphProductionGapCode.CONCURRENT_MUTATION)
                chunks: list[bytes] = []
                remaining = item.size_bytes + 1
                while remaining:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                content = b"".join(chunks)
                final_opened = os.fstat(descriptor)
            except _ProductionRejected:
                raise
            except OSError:
                raise _ProductionRejected(GraphProductionGapCode.CONCURRENT_MUTATION) from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            try:
                final_path = target.lstat()
            except OSError:
                raise _ProductionRejected(GraphProductionGapCode.CONCURRENT_MUTATION) from None
            if (
                len(content) != item.size_bytes
                or (opened.st_dev, opened.st_ino, opened.st_size)
                != (final_opened.st_dev, final_opened.st_ino, final_opened.st_size)
                or (details.st_dev, details.st_ino, details.st_size)
                != (final_path.st_dev, final_path.st_ino, final_path.st_size)
            ):
                raise _ProductionRejected(GraphProductionGapCode.CONCURRENT_MUTATION)
            total += item.size_bytes
            self._check_content(item, content, total)
            contents[item.path] = content
        return contents

    def _check_content(self, item: GitFileEntry, content: bytes, total: int) -> None:
        if (
            len(content) != item.size_bytes
            or hashlib.sha256(content).hexdigest() != item.content_sha256
        ):
            raise _ProductionRejected(GraphProductionGapCode.TREE_BYTES_MISMATCH, item.path)
        if len(content) > self.policy.maximum_file_bytes or total > self.policy.maximum_total_bytes:
            raise _ProductionRejected(GraphProductionGapCode.CAPACITY_EXCEEDED, "tree-bytes")

    def _produce_side(
        self,
        side: TreeSide,
        manifest: tuple[GitFileEntry, ...],
        contents: dict[str, bytes],
        tree_sha256: str,
        inputs: GraphProductionInputs,
    ) -> ProducedGraphSide:
        if len(manifest) > self.policy.maximum_files:
            raise _ProductionRejected(GraphProductionGapCode.CAPACITY_EXCEEDED, "files")
        if set(contents) != {item.path for item in manifest}:
            raise _ProductionRejected(GraphProductionGapCode.TREE_MANIFEST_MISMATCH, side.value)
        try:
            extracted: AdapterGraph = SalesforceSemanticGraphAdapter().extract(
                contents,
                maximum_nodes=self.policy.maximum_nodes - len(manifest),
                maximum_edges=self.policy.maximum_edges,
                maximum_work_units=self.policy.maximum_semantic_work_units,
                maximum_path_bytes=self.policy.maximum_path_bytes,
            )
        except SalesforceGraphCapacityError:
            raise _ProductionRejected(
                GraphProductionGapCode.CAPACITY_EXCEEDED, "semantic-adapter"
            ) from None
        except SalesforceGraphAdapterError as exc:
            raise _ProductionRejected(
                GraphProductionGapCode.SEMANTIC_ADAPTER_FAILED,
                type(exc).__name__,
            ) from None
        except Exception as exc:
            raise _ProductionRejected(
                GraphProductionGapCode.SEMANTIC_ADAPTER_FAILED,
                type(exc).__name__,
            ) from None

        manifest_by_path = {item.path: item for item in manifest}
        semantic_by_path = {item.path: item for item in extracted.files}
        if tuple(item.path for item in extracted.files) != tuple(sorted(manifest_by_path)) or len(
            semantic_by_path
        ) != len(extracted.files):
            raise _ProductionRejected(GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, "files")
        if any(item.status is SemanticFileStatus.UNSUPPORTED for item in extracted.files):
            raise _ProductionRejected(GraphProductionGapCode.UNSUPPORTED_SOURCE_FAMILY)

        adapter_nodes = {item.node_id: item for item in extracted.nodes}
        adapter_edges = {item.edge_id: item for item in extracted.edges}
        if len(adapter_nodes) != len(extracted.nodes) or len(adapter_edges) != len(extracted.edges):
            raise _ProductionRejected(GraphProductionGapCode.OUTPUT_COLLISION)
        for path, result in semantic_by_path.items():
            expected_nodes = tuple(
                sorted(item.node_id for item in extracted.nodes if path in item.owner_paths)
            )
            expected_edges = tuple(
                sorted(item.edge_id for item in extracted.edges if path in item.owner_paths)
            )
            if result.node_ids != expected_nodes or result.edge_ids != expected_edges:
                raise _ProductionRejected(GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, path)

        semantic_tree = stable_sha256(
            [
                {
                    "path": item.path,
                    "mode": item.mode,
                    "size_bytes": item.size_bytes,
                    "content_sha256": item.content_sha256,
                }
                for item in manifest
            ]
        )
        snapshot = stable_sha256(
            {
                "project_id": inputs.candidate.project_id,
                "repository_identity_sha256": inputs.candidate.repository_identity_sha256,
                "side": side.value,
                "tree_sha256": tree_sha256,
                "semantic_tree_sha256": semantic_tree,
                "policy_sha256": self.policy.sha256,
                "producer_implementation_sha256": self.policy.producer.implementation_sha256,
                "adapter_implementation_sha256": self.policy.adapter.implementation_sha256,
                "ontology_sha256": inputs.ontology.sha256,
                "source_profile_sha256": inputs.profile.sha256,
            }
        )
        records = tuple(
            _record(side, item, contents[item.path], semantic_by_path[item.path], self.policy)
            for item in manifest
        )

        nodes: list[ProducedNode] = []
        for item in manifest:
            content_kind, normalized_sha = _content_identity(contents[item.path])
            owners = (
                _owner(
                    item.path,
                    manifest_by_path,
                    self.policy.inventory_parser_id,
                    self.policy.inventory_parser_version,
                    self.policy.producer.implementation_sha256,
                ),
            )
            attributes = {
                "mode": item.mode,
                "sizeBytes": item.size_bytes,
                "contentSha256": item.content_sha256,
                "contentKind": content_kind,
                "normalizedTextSha256": normalized_sha,
            }
            node_body = {
                "node_id": f"file:{item.path}",
                "raw_kind": "file",
                "label": item.path,
                "evidence_state": "CONFIRMED",
                "owners": [owner.model_dump(mode="json") for owner in owners],
                "attributes": attributes,
            }
            nodes.append(
                ProducedNode.model_validate(
                    {
                        **node_body,
                        "semantic_sha256": stable_sha256(
                            {key: value for key, value in node_body.items() if key != "owners"}
                        ),
                        "element_sha256": stable_sha256(
                            {
                                **node_body,
                                "semantic_sha256": stable_sha256(
                                    {
                                        key: value
                                        for key, value in node_body.items()
                                        if key != "owners"
                                    }
                                ),
                            }
                        ),
                    }
                )
            )

        def semantic_owners(paths: tuple[str, ...]) -> tuple[GraphElementOwner, ...]:
            if paths != tuple(sorted(set(paths))) or not paths:
                raise _ProductionRejected(GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, "owners")
            if not set(paths) <= set(manifest_by_path):
                raise _ProductionRejected(GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, "owners")
            return tuple(
                _owner(
                    path,
                    manifest_by_path,
                    semantic_by_path[path].parser_id,
                    self.policy.adapter.implementation_version,
                    self.policy.adapter.implementation_sha256,
                )
                for path in paths
            )

        for item in extracted.nodes:
            if set(item.attributes) & _RESERVED_RAW_ATTRIBUTE_KEYS:
                raise _ProductionRejected(
                    GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID,
                    item.node_id,
                    "reserved-attribute",
                )
            owners = semantic_owners(item.owner_paths)
            node_body = {
                "node_id": item.node_id,
                "raw_kind": item.raw_kind,
                "label": item.label,
                "evidence_state": item.evidence_state,
                "owners": [owner.model_dump(mode="json") for owner in owners],
                "attributes": dict(item.attributes),
            }
            try:
                nodes.append(
                    ProducedNode.model_validate(
                        {
                            **node_body,
                            "semantic_sha256": stable_sha256(
                                {key: value for key, value in node_body.items() if key != "owners"}
                            ),
                            "element_sha256": stable_sha256(
                                {
                                    **node_body,
                                    "semantic_sha256": stable_sha256(
                                        {
                                            key: value
                                            for key, value in node_body.items()
                                            if key != "owners"
                                        }
                                    ),
                                }
                            ),
                        }
                    )
                )
            except (TypeError, ValidationError, ValueError):
                raise _ProductionRejected(
                    GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, item.node_id
                ) from None

        edges: list[ProducedEdge] = []
        node_ids = {item.node_id for item in nodes}
        for item in extracted.edges:
            if set(item.attributes) & _RESERVED_RAW_ATTRIBUTE_KEYS:
                raise _ProductionRejected(
                    GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID,
                    item.edge_id,
                    "reserved-attribute",
                )
            if item.source_id not in node_ids or item.target_id not in node_ids:
                raise _ProductionRejected(
                    GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, item.edge_id
                )
            owners = semantic_owners(item.owner_paths)
            edge_body = {
                "edge_id": item.edge_id,
                "source_id": item.source_id,
                "raw_relation": item.raw_relation,
                "target_id": item.target_id,
                "evidence_state": item.evidence_state,
                "owners": [owner.model_dump(mode="json") for owner in owners],
                "attributes": dict(item.attributes),
            }
            try:
                edges.append(
                    ProducedEdge.model_validate(
                        {
                            **edge_body,
                            "semantic_sha256": stable_sha256(
                                {key: value for key, value in edge_body.items() if key != "owners"}
                            ),
                            "element_sha256": stable_sha256(
                                {
                                    **edge_body,
                                    "semantic_sha256": stable_sha256(
                                        {
                                            key: value
                                            for key, value in edge_body.items()
                                            if key != "owners"
                                        }
                                    ),
                                }
                            ),
                        }
                    )
                )
            except (TypeError, ValidationError, ValueError):
                raise _ProductionRejected(
                    GraphProductionGapCode.SEMANTIC_OUTPUT_INVALID, item.edge_id
                ) from None

        nodes_tuple = tuple(sorted(nodes, key=lambda item: item.node_id))
        edges_tuple = tuple(sorted(edges, key=lambda item: item.edge_id))
        aliases: dict[str, str] = {}
        for entity_id in [item.node_id for item in nodes_tuple] + [
            item.edge_id for item in edges_tuple
        ]:
            alias = unicodedata.normalize("NFC", entity_id).casefold()
            prior = aliases.setdefault(alias, entity_id)
            if prior != entity_id:
                raise _ProductionRejected(GraphProductionGapCode.OUTPUT_COLLISION)
        if (
            len(nodes_tuple) > self.policy.maximum_nodes
            or len(edges_tuple) > self.policy.maximum_edges
        ):
            raise _ProductionRejected(GraphProductionGapCode.CAPACITY_EXCEEDED, "graph")
        raw_graph = _raw_graph(nodes_tuple, edges_tuple, snapshot, self.policy)
        raw_sha = stable_sha256(raw_graph)
        reasoning_graph = materialize_source_attested_reasoning_graph(
            raw_graph,
            source_graph_sha256=raw_sha,
        )
        try:
            normalized: NormalizedGraph = normalize_source_graph(
                reasoning_graph,
                inputs.ontology,
                inputs.profile,
                project_id=inputs.candidate.project_id,
                source_graph_sha256=raw_sha,
            )
        except (OntologyNormalizationError, TypeError, ValueError):
            raise _ProductionRejected(GraphProductionGapCode.NORMALIZATION_FAILED) from None
        if normalized.mapping_gaps:
            raise _ProductionRejected(GraphProductionGapCode.NORMALIZATION_FAILED, "mapping")
        body = {
            "side": side.value,
            "tree_sha256": tree_sha256,
            "source_snapshot_sha256": snapshot,
            "dispositions": [item.model_dump(mode="json") for item in records],
            "nodes": [item.model_dump(mode="json") for item in nodes_tuple],
            "edges": [item.model_dump(mode="json") for item in edges_tuple],
            "input_file_count": len(records),
            "input_total_bytes": sum(item.size_bytes for item in records),
            "raw_node_count": len(nodes_tuple),
            "raw_edge_count": len(edges_tuple),
            "raw_graph_sha256": raw_sha,
            "normalized_graph_sha256": normalized.graph_sha256,
        }
        return ProducedGraphSide.model_validate(
            {**body, "side_receipt_sha256": stable_sha256(body)}
        )
