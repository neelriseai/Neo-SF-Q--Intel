from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.edge_envelope import TrustedEdgeReplayInput
from neo_sf_q_intel.ontology import (
    Materiality,
    NormalizedGraph,
    SourceEvidenceState,
    contract_sha256,
)
from neo_sf_q_intel.policy import ReasoningPolicy, ReasoningPolicyError
from neo_sf_q_intel.propagation import (
    PropagationEvaluationError,
    PropagationPathReceipt,
    PropagationPolicy,
    PropagationResult,
    traverse_propagation,
)


class ContextCompilerContractError(RuntimeError):
    """Raised when the context compiler policy is missing, invalid, or untrusted."""


class ContextCompilationError(RuntimeError):
    """Raised when context inputs cannot be safely compiled together."""


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ContextPosture(StrEnum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"


class ContextRelationshipState(StrEnum):
    CONFIRMED = "CONFIRMED"
    CANDIDATE = "CANDIDATE"


class ContextCompilerLimits(ContextModel):
    maximum_input_graph_nodes: int = Field(alias="maximumInputGraphNodes", ge=1)
    maximum_input_graph_edges: int = Field(alias="maximumInputGraphEdges", ge=1)
    maximum_input_graph_bytes: int = Field(alias="maximumInputGraphBytes", ge=1024)
    maximum_graph_attribute_bytes: int = Field(alias="maximumGraphAttributeBytes", ge=64)
    maximum_graph_attribute_string_characters: int = Field(
        alias="maximumGraphAttributeStringCharacters", ge=16, le=8192
    )
    maximum_input_seeds: int = Field(alias="maximumInputSeeds", ge=1)
    maximum_input_propagation_paths: int = Field(alias="maximumInputPropagationPaths", ge=1)
    maximum_input_propagation_hops: int = Field(alias="maximumInputPropagationHops", ge=1)
    maximum_input_propagation_gaps: int = Field(alias="maximumInputPropagationGaps", ge=1)
    maximum_input_source_fragments: int = Field(alias="maximumInputSourceFragments", ge=1)
    maximum_input_candidate_receipts: int = Field(alias="maximumInputCandidateReceipts", ge=1)
    maximum_authoritative_paths: int = Field(alias="maximumAuthoritativePaths", ge=1)
    maximum_authoritative_hops: int = Field(alias="maximumAuthoritativeHops", ge=1)
    maximum_source_fragments: int = Field(alias="maximumSourceFragments", ge=1)
    maximum_source_characters: int = Field(alias="maximumSourceCharacters", ge=1)
    maximum_candidate_receipts: int = Field(alias="maximumCandidateReceipts", ge=1)
    maximum_candidate_characters: int = Field(alias="maximumCandidateCharacters", ge=1)
    maximum_identifier_characters: int = Field(
        alias="maximumIdentifierCharacters", ge=16, le=256
    )
    maximum_locator_characters: int = Field(alias="maximumLocatorCharacters", ge=16, le=1024)
    maximum_content_characters: int = Field(alias="maximumContentCharacters", ge=16, le=8192)
    maximum_gap_detail_characters: int = Field(
        alias="maximumGapDetailCharacters", ge=16, le=1024
    )
    maximum_total_pack_bytes: int = Field(alias="maximumTotalPackBytes", ge=4096)


class ContextCompilerPolicy(ContextModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    posture: ContextPosture
    mandatory_path_materialities: tuple[Materiality, ...] = Field(
        alias="mandatoryPathMaterialities", min_length=1
    )
    limits: ContextCompilerLimits

    @model_validator(mode="after")
    def validate_identity(self) -> ContextCompilerPolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.policy_version, "policyVersion")
        if not _IDENTIFIER.fullmatch(self.policy_id):
            raise ValueError("policyId must be a lowercase stable identifier")
        if len(self.mandatory_path_materialities) != len(
            set(self.mandatory_path_materialities)
        ):
            raise ValueError("mandatoryPathMaterialities must not contain duplicates")
        if self.posture is not ContextPosture.ANALYSIS_ONLY:
            raise ValueError("Context compilation must remain analysis-only")
        return self


class UnresolvedSourceFragment(ContextModel):
    fragment_id: str = Field(min_length=1, max_length=256)
    evidence_id: str = Field(min_length=1, max_length=256)
    node_id: str = Field(min_length=1, max_length=256)
    source_locator: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=8192)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot: str = Field(min_length=1)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor_id: str = Field(min_length=1, max_length=256)
    evidence_state: Literal[SourceEvidenceState.UNVERIFIED] = SourceEvidenceState.UNVERIFIED
    relationship_state: Literal[ContextRelationshipState.CANDIDATE] = (
        ContextRelationshipState.CANDIDATE
    )
    mandatory: bool = False
    may_authorize: Literal[False] = False

    @model_validator(mode="after")
    def validate_fragment(self) -> UnresolvedSourceFragment:
        _require_relative_locator(self.source_locator)
        if _content_hash(self.content) != self.content_sha256:
            raise ValueError("Source fragment content digest does not match its content")
        return self


class SemanticCandidateReceipt(ContextModel):
    candidate_id: str = Field(min_length=1, max_length=256)
    evidence_id: str = Field(min_length=1, max_length=256)
    node_id: str = Field(min_length=1, max_length=256)
    source_locator: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=8192)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot: str = Field(min_length=1)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    score: float = Field(ge=-1.0, le=1.0)
    retriever_id: str = Field(min_length=1, max_length=256)
    retrieval_model: str = Field(min_length=1, max_length=256)
    relationship_state: Literal[ContextRelationshipState.CANDIDATE] = (
        ContextRelationshipState.CANDIDATE
    )
    may_authorize: Literal[False] = False

    @model_validator(mode="after")
    def validate_candidate(self) -> SemanticCandidateReceipt:
        _require_relative_locator(self.source_locator)
        if _content_hash(self.content) != self.content_sha256:
            raise ValueError("Semantic candidate content digest does not match its content")
        return self


class ContextGap(ContextModel):
    code: str = Field(min_length=1, max_length=256)
    item_id: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=1024)
    blocking: bool


class ContextBudgetUsage(ContextModel):
    seed_input_limit: int
    seed_input_count: int
    propagation_path_input_limit: int
    propagation_path_input_count: int
    propagation_hop_input_limit: int
    propagation_hop_input_count: int
    propagation_gap_input_limit: int
    propagation_gap_input_count: int
    source_fragment_input_limit: int
    source_fragment_input_count: int
    candidate_receipt_input_limit: int
    candidate_receipt_input_count: int
    structure_path_limit: int
    structure_paths_available: int
    structure_paths_selected: int
    structure_paths_omitted: int
    structure_hop_limit: int
    structure_hops_available: int
    structure_hops_selected: int
    structure_hops_omitted: int
    unresolved_fragment_limit: int
    unresolved_fragments_available: int
    unresolved_fragments_selected: int
    unresolved_fragments_omitted: int
    unresolved_character_limit: int
    unresolved_characters_available: int
    unresolved_characters_selected: int
    unresolved_characters_omitted: int
    candidate_receipt_limit: int
    candidate_receipts_available: int
    candidate_receipts_selected: int
    candidate_receipts_omitted: int
    candidate_character_limit: int
    candidate_characters_available: int
    candidate_characters_selected: int
    candidate_characters_omitted: int
    identifier_character_limit: int
    locator_character_limit: int
    content_character_limit: int
    gap_detail_character_limit: int
    total_pack_byte_limit: int
    total_pack_bytes: int


class GraphContextPack(ContextModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    posture: Literal[ContextPosture.ANALYSIS_ONLY] = ContextPosture.ANALYSIS_ONLY
    may_authorize: Literal[False] = False
    authority_eligible: Literal[False] = False
    analysis_complete: bool
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    compiler_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_id: str = Field(min_length=1, max_length=256)
    source_snapshot: str = Field(min_length=1, max_length=256)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str = Field(min_length=1, max_length=256)
    ontology_version: str = Field(min_length=1, max_length=256)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1, max_length=256)
    profile_version: str = Field(min_length=1, max_length=256)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_policy_schema_version: str = Field(min_length=1, max_length=256)
    reasoning_policy_locator: str = Field(min_length=1, max_length=1024)
    reasoning_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieval_eval_set_id: str = Field(min_length=1, max_length=256)
    retrieval_eval_set_path: str = Field(min_length=1, max_length=1024)
    retrieval_eval_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propagation_policy_id: str = Field(min_length=1, max_length=256)
    propagation_policy_version: str = Field(min_length=1, max_length=256)
    propagation_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    propagation_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    compiler_policy_id: str = Field(min_length=1, max_length=256)
    compiler_policy_version: str = Field(min_length=1, max_length=256)
    compiler_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed_ids: tuple[str, ...]
    confirmed_structure_paths: tuple[PropagationPathReceipt, ...]
    unresolved_fragments: tuple[UnresolvedSourceFragment, ...]
    semantic_candidates: tuple[SemanticCandidateReceipt, ...]
    budget: ContextBudgetUsage
    gaps: tuple[ContextGap, ...]
    context_pack_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_pack_digest_and_planes(self) -> GraphContextPack:
        if any(item.may_authorize for item in self.unresolved_fragments):
            raise ValueError("Unresolved fragments cannot authorize decisions")
        if any(
            item.relationship_state is not ContextRelationshipState.CANDIDATE
            or item.may_authorize
            for item in self.semantic_candidates
        ):
            raise ValueError("Semantic receipts must remain candidate-only")
        if any(
            item.relationship_state is not ContextRelationshipState.CANDIDATE
            or item.evidence_state is not SourceEvidenceState.UNVERIFIED
            or item.may_authorize
            for item in self.unresolved_fragments
        ):
            raise ValueError("Caller-supplied fragments must remain unresolved candidates")
        if any(
            item.source_snapshot != self.source_snapshot
            or item.source_graph_sha256 != self.source_graph_sha256
            for item in [*self.unresolved_fragments, *self.semantic_candidates]
        ):
            raise ValueError("Context item source identity differs from the context pack")
        unresolved_ids = {item.evidence_id for item in self.unresolved_fragments}
        candidate_ids = {item.evidence_id for item in self.semantic_candidates}
        if unresolved_ids & candidate_ids:
            raise ValueError("Unresolved and semantic candidate evidence must be disjoint")
        expected_path_identity = (
            self.project_id,
            self.source_snapshot,
            self.source_graph_sha256,
            self.normalized_graph_sha256,
            self.ontology_id,
            self.ontology_version,
            self.ontology_sha256,
            self.profile_id,
            self.profile_version,
            self.profile_sha256,
            self.propagation_policy_id,
            self.propagation_policy_version,
            self.propagation_policy_sha256,
        )
        for path in self.confirmed_structure_paths:
            if (
                path.project_id,
                path.source_snapshot,
                path.source_graph_sha256,
                path.graph_sha256,
                path.ontology_id,
                path.ontology_version,
                path.ontology_sha256,
                path.profile_id,
                path.profile_version,
                path.profile_sha256,
                path.policy_id,
                path.policy_version,
                path.policy_sha256,
            ) != expected_path_identity:
                raise ValueError("Confirmed structure path identity differs from the context pack")
            for hop in path.hops:
                _require_relative_locator(hop.source_artifact)
        if self.budget.structure_paths_selected != len(self.confirmed_structure_paths):
            raise ValueError("Structure-path budget usage is inconsistent")
        if self.budget.unresolved_fragments_selected != len(self.unresolved_fragments):
            raise ValueError("Unresolved-fragment budget usage is inconsistent")
        if self.budget.candidate_receipts_selected != len(self.semantic_candidates):
            raise ValueError("Candidate budget usage is inconsistent")
        if any(
            gap.blocking and gap.code.startswith("PROPAGATION_") for gap in self.gaps
        ) and self.confirmed_structure_paths:
            raise ValueError("Blocking propagation gaps forbid confirmed structure paths")
        if self.analysis_complete != (not any(gap.blocking for gap in self.gaps)):
            raise ValueError("analysis_complete disagrees with blocking gaps")
        self._validate_budget_reconciliation()
        envelope = self.model_dump(mode="json")
        declared = envelope["context_pack_sha256"]
        declared_size = envelope["budget"]["total_pack_bytes"]
        envelope["context_pack_sha256"] = "0" * 64
        actual_size = len(_canonical_json(envelope))
        if declared_size != actual_size or actual_size > self.budget.total_pack_byte_limit:
            raise ValueError("Context pack byte budget is invalid")
        _require_relative_locator(self.retrieval_eval_set_path)
        body = self.model_dump(mode="json")
        body.pop("context_pack_sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Context pack digest does not match its content")
        return self

    def _validate_budget_reconciliation(self) -> None:
        budget = self.budget
        if any(value < 0 for value in budget.model_dump(mode="json").values()):
            raise ValueError("Context budget values must be nonnegative")
        selected_hops = sum(len(path.hops) for path in self.confirmed_structure_paths)
        unresolved_characters = sum(len(item.content) for item in self.unresolved_fragments)
        candidate_characters = sum(len(item.content) for item in self.semantic_candidates)
        pairs = (
            (
                budget.structure_paths_available,
                budget.structure_paths_selected,
                budget.structure_paths_omitted,
            ),
            (
                budget.structure_hops_available,
                budget.structure_hops_selected,
                budget.structure_hops_omitted,
            ),
            (
                budget.unresolved_fragments_available,
                budget.unresolved_fragments_selected,
                budget.unresolved_fragments_omitted,
            ),
            (
                budget.unresolved_characters_available,
                budget.unresolved_characters_selected,
                budget.unresolved_characters_omitted,
            ),
            (
                budget.candidate_receipts_available,
                budget.candidate_receipts_selected,
                budget.candidate_receipts_omitted,
            ),
            (
                budget.candidate_characters_available,
                budget.candidate_characters_selected,
                budget.candidate_characters_omitted,
            ),
        )
        if any(available != selected + omitted for available, selected, omitted in pairs):
            raise ValueError("Context budget available/selected/omitted counts disagree")
        if (
            budget.seed_input_count > budget.seed_input_limit
            or budget.propagation_path_input_count > budget.propagation_path_input_limit
            or budget.propagation_hop_input_count > budget.propagation_hop_input_limit
            or budget.propagation_gap_input_count > budget.propagation_gap_input_limit
            or budget.source_fragment_input_count > budget.source_fragment_input_limit
            or budget.candidate_receipt_input_count > budget.candidate_receipt_input_limit
            or budget.structure_paths_available != budget.propagation_path_input_count
            or budget.structure_hops_available != budget.propagation_hop_input_count
            or budget.unresolved_fragments_available != budget.source_fragment_input_count
            or budget.candidate_receipts_available != budget.candidate_receipt_input_count
            or budget.structure_hops_selected != selected_hops
            or budget.unresolved_characters_selected != unresolved_characters
            or budget.candidate_characters_selected != candidate_characters
            or budget.structure_paths_selected > budget.structure_path_limit
            or budget.structure_hops_selected > budget.structure_hop_limit
            or budget.unresolved_fragments_selected > budget.unresolved_fragment_limit
            or budget.unresolved_characters_selected > budget.unresolved_character_limit
            or budget.candidate_receipts_selected > budget.candidate_receipt_limit
            or budget.candidate_characters_selected > budget.candidate_character_limit
        ):
            raise ValueError("Context budget usage exceeds or disagrees with selected content")
        identifiers = [
            self.project_id,
            self.source_snapshot,
            self.ontology_id,
            self.ontology_version,
            self.profile_id,
            self.profile_version,
            self.reasoning_policy_schema_version,
            self.retrieval_eval_set_id,
            self.propagation_policy_id,
            self.propagation_policy_version,
            self.compiler_policy_id,
            self.compiler_policy_version,
            *self.seed_ids,
            *(
                value
                for path in self.confirmed_structure_paths
                for value in (path.seed_id, path.target_id)
            ),
            *(
                value
                for path in self.confirmed_structure_paths
                for hop in path.hops
                for value in (
                    hop.edge_id,
                    hop.relation_class,
                    hop.traversal_from_id,
                    hop.traversal_to_id,
                    hop.edge_source_id,
                    hop.edge_target_id,
                    hop.extractor_id,
                )
            ),
            *(
                value
                for item in self.unresolved_fragments
                for value in (
                    item.fragment_id,
                    item.evidence_id,
                    item.node_id,
                    item.extractor_id,
                )
            ),
            *(
                value
                for item in self.semantic_candidates
                for value in (
                    item.candidate_id,
                    item.evidence_id,
                    item.node_id,
                    item.retriever_id,
                    item.retrieval_model,
                )
            ),
            *(gap.code for gap in self.gaps),
            *(gap.item_id for gap in self.gaps if gap.item_id is not None),
        ]
        locators = [
            self.reasoning_policy_locator,
            self.retrieval_eval_set_path,
            *(hop.source_artifact for path in self.confirmed_structure_paths for hop in path.hops),
            *(item.source_locator for item in self.unresolved_fragments),
            *(item.source_locator for item in self.semantic_candidates),
        ]
        if (
            any(len(value) > budget.identifier_character_limit for value in identifiers)
            or any(len(value) > budget.locator_character_limit for value in locators)
            or any(
                len(item.content) > budget.content_character_limit
                for item in [*self.unresolved_fragments, *self.semantic_candidates]
            )
            or any(
                gap.detail is not None
                and len(gap.detail) > budget.gap_detail_character_limit
                for gap in self.gaps
            )
        ):
            raise ValueError("Context content exceeds its declared field budgets")


_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
DEFAULT_CONTEXT_COMPILER_POLICY_SHA256 = (
    "c513a7cc5d2f74991e9d15ff0eff38be7ed150e9e50c0d3f513ae9b69eccc3aa"
)


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must use semantic versioning")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key, value in pairs:
        if key in body:
            raise ContextCompilerContractError(f"Duplicate JSON key is not allowed: {key}")
        body[key] = value
    return body


def _require_relative_locator(locator: str) -> None:
    if "\\" in locator:
        raise ValueError("Source locators must use repository-relative POSIX paths")
    posix = PurePosixPath(locator)
    windows = PureWindowsPath(locator)
    if (
        not locator.strip()
        or "://" in locator
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or "." in posix.parts
    ):
        raise ValueError("Source locators must be repository-relative without traversal")


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _stable_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def _canonical_json(body: dict[str, Any]) -> bytes:
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def _set_total_pack_bytes(body: dict[str, Any]) -> None:
    size = 0
    for _ in range(8):
        body["budget"]["total_pack_bytes"] = size
        envelope = {**body, "context_pack_sha256": "0" * 64}
        measured = len(_canonical_json(envelope))
        if measured == size:
            return
        size = measured
    raise ContextCompilationError("Context pack byte measurement did not converge")


def load_context_compiler_policy(
    path: Path,
    *,
    expected_sha256: str = DEFAULT_CONTEXT_COMPILER_POLICY_SHA256,
) -> ContextCompilerPolicy:
    if not path.is_file():
        raise ContextCompilerContractError(f"Required policy is missing: {path.name}")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except ContextCompilerContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCompilerContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(document, dict):
        raise ContextCompilerContractError(f"Expected a JSON object in {path.name}")
    actual_sha256 = contract_sha256(document)
    if document.get("sha256") != actual_sha256:
        raise ContextCompilerContractError(f"{path.name} digest does not match its content")
    if actual_sha256 != expected_sha256.casefold():
        raise ContextCompilerContractError(f"{path.name} does not match its trusted digest")
    try:
        return ContextCompilerPolicy.model_validate(document)
    except ValidationError as exc:
        raise ContextCompilerContractError(f"Invalid context compiler policy: {exc}") from exc


def _require_graph_integrity(graph: NormalizedGraph) -> dict[str, Any]:
    body = graph.model_dump(mode="json")
    declared = body.pop("graph_sha256")
    if _stable_hash(body) != declared:
        raise ContextCompilationError("Normalized graph digest does not match its content")
    if not graph.project_id or not graph.source_snapshot or not graph.source_graph_sha256:
        raise ContextCompilationError("Graph project, snapshot and source digest are required")
    nodes = {node.node_id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise ContextCompilationError("Normalized graph node IDs must be unique")
    edges = {edge.edge_id: edge for edge in graph.edges}
    if len(edges) != len(graph.edges):
        raise ContextCompilationError("Normalized graph edge IDs must be unique")
    if any(
        edge.source_id not in nodes or edge.target_id not in nodes for edge in graph.edges
    ):
        raise ContextCompilationError("Normalized graph edge has an unknown endpoint")
    return nodes


def _require_propagation_integrity(
    graph: NormalizedGraph, propagation: PropagationResult
) -> None:
    try:
        PropagationResult.model_validate(propagation.model_dump(mode="json"))
    except ValidationError as exc:
        raise ContextCompilationError("Propagation result digest is invalid") from exc
    graph_identity = (
        graph.project_id,
        graph.source_snapshot,
        graph.source_graph_sha256,
        graph.graph_sha256,
        graph.ontology_id,
        graph.ontology_version,
        graph.ontology_sha256,
        graph.profile_id,
        graph.profile_version,
        graph.profile_sha256,
    )
    propagation_identity = (
        propagation.project_id,
        propagation.source_snapshot,
        propagation.source_graph_sha256,
        propagation.graph_sha256,
        propagation.ontology_id,
        propagation.ontology_version,
        propagation.ontology_sha256,
        propagation.profile_id,
        propagation.profile_version,
        propagation.profile_sha256,
    )
    if graph_identity != propagation_identity:
        raise ContextCompilationError("Propagation result does not match the normalized graph")
    if tuple(sorted(set(propagation.seed_ids))) != propagation.seed_ids:
        raise ContextCompilationError("Propagation seed identities are not canonical")
    nodes = {node.node_id: node for node in graph.nodes}
    edges = {edge.edge_id: edge for edge in graph.edges}
    for path in propagation.paths:
        visited_ids = [path.seed_id, *(hop.traversal_to_id for hop in path.hops)]
        for node_id in visited_ids:
            node = nodes.get(node_id)
            if node is None:
                raise ContextCompilationError("Propagation path refers to an unknown graph node")
            if (
                node.evidence_state is not SourceEvidenceState.CONFIRMED
                or not node.source
                or not node.extractor_id
                or node.source_snapshot != graph.source_snapshot
                or node.source_hash != graph.source_graph_sha256
            ):
                raise ContextCompilationError("Propagation path refers to an untrusted graph node")
            _require_relative_locator(node.source)
        for hop in path.hops:
            edge = edges.get(hop.edge_id)
            if edge is None:
                raise ContextCompilationError("Propagation receipt refers to an unknown graph edge")
            edge_identity = (
                edge.canonical_relation,
                edge.source_id,
                edge.target_id,
                edge.source,
                edge.evidence_state,
                edge.source_snapshot,
                edge.source_hash,
                edge.extractor_id,
                edge.materiality,
            )
            receipt_identity = (
                hop.relation_class,
                hop.edge_source_id,
                hop.edge_target_id,
                hop.source_artifact,
                hop.evidence_state,
                hop.source_snapshot,
                hop.source_hash,
                hop.extractor_id,
                hop.materiality,
            )
            if edge_identity != receipt_identity:
                raise ContextCompilationError(
                    "Propagation receipt differs from its exact normalized graph edge"
                )
            if (
                edge.evidence_state is not SourceEvidenceState.CONFIRMED
                or not edge.source
                or not edge.extractor_id
                or edge.source_snapshot != graph.source_snapshot
                or edge.source_hash != graph.source_graph_sha256
            ):
                raise ContextCompilationError("Propagation receipt edge is not authoritative")
            _require_relative_locator(edge.source)


def _deduplicate(items: tuple[Any, ...], identity_field: str, label: str) -> list[Any]:
    by_id: dict[str, Any] = {}
    for item in items:
        identity = str(getattr(item, identity_field))
        prior = by_id.get(identity)
        if prior is not None and prior != item:
            raise ContextCompilationError(f"Conflicting duplicate {label}: {identity}")
        by_id[identity] = item
    return list(by_id.values())


def _validate_fragment_against_graph(
    fragment: UnresolvedSourceFragment | SemanticCandidateReceipt,
    graph: NormalizedGraph,
    nodes: dict[str, Any],
) -> None:
    if (
        fragment.source_snapshot != graph.source_snapshot
        or fragment.source_graph_sha256 != graph.source_graph_sha256
    ):
        raise ContextCompilationError(
            f"Context item {fragment.evidence_id!r} crosses the graph source boundary"
        )
    node = nodes.get(fragment.node_id)
    if node is None:
        raise ContextCompilationError(
            f"Context item {fragment.evidence_id!r} refers to an unknown graph node"
        )
    if (
        node.evidence_state is not SourceEvidenceState.CONFIRMED
        or not node.extractor_id
        or node.source_snapshot != graph.source_snapshot
        or node.source_hash != graph.source_graph_sha256
    ):
        raise ContextCompilationError(
            f"Context item {fragment.evidence_id!r} refers to an untrusted graph node"
        )
    if not node.source or node.source != fragment.source_locator:
        raise ContextCompilationError(
            f"Context item {fragment.evidence_id!r} locator differs from graph provenance"
        )
    _require_relative_locator(node.source)
    if (
        isinstance(fragment, UnresolvedSourceFragment)
        and fragment.extractor_id != node.extractor_id
    ):
        raise ContextCompilationError(
            f"Context item {fragment.evidence_id!r} extractor differs from graph provenance"
        )


def _select_paths(
    paths: list[PropagationPathReceipt], policy: ContextCompilerPolicy
) -> tuple[list[PropagationPathReceipt], list[PropagationPathReceipt], list[ContextGap]]:
    mandatory_values = set(policy.mandatory_path_materialities)
    ordered = sorted(
        paths,
        key=lambda path: (
            not any(hop.materiality in mandatory_values for hop in path.hops),
            path.target_id,
            path.seed_id,
            len(path.hops),
            path.path_sha256,
        ),
    )
    selected: list[PropagationPathReceipt] = []
    omitted: list[PropagationPathReceipt] = []
    gaps: list[ContextGap] = []
    used_hops = 0
    for path in ordered:
        mandatory = any(hop.materiality in mandatory_values for hop in path.hops)
        fits = (
            len(selected) < policy.limits.maximum_authoritative_paths
            and used_hops + len(path.hops) <= policy.limits.maximum_authoritative_hops
        )
        if fits:
            selected.append(path)
            used_hops += len(path.hops)
        else:
            omitted.append(path)
            gaps.append(
                ContextGap(
                    code=(
                        "MANDATORY_PATH_BUDGET_EXCEEDED"
                        if mandatory
                        else "OPTIONAL_PATH_BUDGET_EXCEEDED"
                    ),
                    item_id=path.path_sha256,
                    detail="The complete path was omitted; path receipts are never truncated.",
                    blocking=mandatory,
                )
            )
    return selected, omitted, gaps


def _select_fragments(
    fragments: list[UnresolvedSourceFragment], policy: ContextCompilerPolicy
) -> tuple[
    list[UnresolvedSourceFragment], list[UnresolvedSourceFragment], list[ContextGap]
]:
    ordered = sorted(fragments, key=lambda item: (not item.mandatory, item.fragment_id))
    selected: list[UnresolvedSourceFragment] = []
    omitted: list[UnresolvedSourceFragment] = []
    gaps: list[ContextGap] = []
    used_characters = 0
    for fragment in ordered:
        fits = (
            len(selected) < policy.limits.maximum_source_fragments
            and used_characters + len(fragment.content)
            <= policy.limits.maximum_source_characters
        )
        if fits:
            selected.append(fragment)
            used_characters += len(fragment.content)
        else:
            omitted.append(fragment)
            gaps.append(
                ContextGap(
                    code=(
                        "MANDATORY_FRAGMENT_BUDGET_EXCEEDED"
                        if fragment.mandatory
                        else "OPTIONAL_FRAGMENT_BUDGET_EXCEEDED"
                    ),
                    item_id=fragment.fragment_id,
                    detail="The complete source fragment was omitted.",
                    blocking=fragment.mandatory,
                )
            )
    return selected, omitted, gaps


def _select_candidates(
    candidates: list[SemanticCandidateReceipt], policy: ContextCompilerPolicy
) -> tuple[list[SemanticCandidateReceipt], list[SemanticCandidateReceipt], list[ContextGap]]:
    ordered = sorted(candidates, key=lambda item: (-item.score, item.candidate_id))
    selected: list[SemanticCandidateReceipt] = []
    omitted: list[SemanticCandidateReceipt] = []
    used_characters = 0
    for candidate in ordered:
        fits = (
            len(selected) < policy.limits.maximum_candidate_receipts
            and used_characters + len(candidate.content)
            <= policy.limits.maximum_candidate_characters
        )
        if fits:
            selected.append(candidate)
            used_characters += len(candidate.content)
        else:
            omitted.append(candidate)
    gaps = (
        [
            ContextGap(
                code="CANDIDATE_BUDGET_TRUNCATED",
                detail=f"{len(omitted)} semantic candidates were omitted by deterministic budget.",
                blocking=False,
            )
        ]
        if omitted
        else []
    )
    return selected, omitted, gaps


def _require_input_bounds(
    graph: NormalizedGraph,
    propagation: PropagationResult,
    fragments: tuple[UnresolvedSourceFragment, ...],
    candidates: tuple[SemanticCandidateReceipt, ...],
    policy: ContextCompilerPolicy,
) -> None:
    limits = policy.limits
    counts = (
        (len(propagation.seed_ids), limits.maximum_input_seeds, "seed"),
        (len(propagation.paths), limits.maximum_input_propagation_paths, "path"),
        (
            sum(len(path.hops) for path in propagation.paths),
            limits.maximum_input_propagation_hops,
            "hop",
        ),
        (
            len(propagation.gaps) + len(graph.mapping_gaps) + len(graph.trust_gaps),
            limits.maximum_input_propagation_gaps,
            "gap",
        ),
        (len(fragments), limits.maximum_input_source_fragments, "source-fragment"),
        (len(candidates), limits.maximum_input_candidate_receipts, "semantic-candidate"),
    )
    for count, limit, label in counts:
        if count > limit:
            raise ContextCompilationError(f"{label} input exceeds the pre-sort safety limit")


def _attribute_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _attribute_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _attribute_strings(item)


def _require_graph_input_bounds(
    graph: NormalizedGraph, policy: ContextCompilerPolicy
) -> None:
    limits = policy.limits
    if len(graph.nodes) > limits.maximum_input_graph_nodes:
        raise ContextCompilationError("graph-node input exceeds the pre-hash safety limit")
    if len(graph.edges) > limits.maximum_input_graph_edges:
        raise ContextCompilationError("graph-edge input exceeds the pre-hash safety limit")
    graph_bytes = len(_canonical_json(graph.model_dump(mode="json")))
    if graph_bytes > limits.maximum_input_graph_bytes:
        raise ContextCompilationError("graph input exceeds maximumInputGraphBytes")
    for record in (*graph.nodes, *graph.edges):
        attribute_bytes = len(_canonical_json(record.attributes))
        if attribute_bytes > limits.maximum_graph_attribute_bytes:
            raise ContextCompilationError("graph attributes exceed maximumGraphAttributeBytes")
        if any(
            len(value) > limits.maximum_graph_attribute_string_characters
            for value in _attribute_strings(record.attributes)
        ):
            raise ContextCompilationError(
                "graph attribute string exceeds maximumGraphAttributeStringCharacters"
            )


def _require_reasoning_policy_root(
    policy: ReasoningPolicy,
    locator: str,
    expected_policy_sha256: str,
    expected_eval_set_sha256: str,
) -> None:
    _require_relative_locator(locator)
    repository_root = Path(__file__).resolve().parents[2]
    policy_path = (repository_root / locator).resolve()
    try:
        policy_path.relative_to(repository_root)
        loaded = ReasoningPolicy.load(policy_path)
    except (ValueError, OSError, ReasoningPolicyError) as exc:
        raise ContextCompilationError("Trusted reasoning policy cannot be loaded") from exc
    if (
        policy != loaded
        or policy.policy_sha256 != expected_policy_sha256
        or policy.retrieval_eval_set_sha256 != expected_eval_set_sha256
    ):
        raise ContextCompilationError("Reasoning policy or evaluation-set root is untrusted")


def _require_policy_field_limits(
    graph: NormalizedGraph,
    propagation: PropagationResult,
    fragments: list[UnresolvedSourceFragment],
    candidates: list[SemanticCandidateReceipt],
    policy: ContextCompilerPolicy,
) -> None:
    limits = policy.limits
    identifiers = [
        graph.project_id,
        graph.source_snapshot,
        graph.ontology_id,
        graph.ontology_version,
        graph.profile_id,
        graph.profile_version,
        propagation.policy_id,
        propagation.policy_version,
        *propagation.seed_ids,
        *(item.fragment_id for item in fragments),
        *(item.evidence_id for item in fragments),
        *(item.node_id for item in fragments),
        *(item.extractor_id for item in fragments),
        *(item.candidate_id for item in candidates),
        *(item.evidence_id for item in candidates),
        *(item.node_id for item in candidates),
        *(item.retriever_id for item in candidates),
        *(item.retrieval_model for item in candidates),
    ]
    if any(
        value is None or len(value) > limits.maximum_identifier_characters
        for value in identifiers
    ):
        raise ContextCompilationError("Context identifier exceeds the compiler policy limit")
    if any(
        len(item.source_locator) > limits.maximum_locator_characters
        for item in [*fragments, *candidates]
    ):
        raise ContextCompilationError("Context locator exceeds the compiler policy limit")
    if any(
        len(item.content) > limits.maximum_content_characters
        for item in [*fragments, *candidates]
    ):
        raise ContextCompilationError("Context content exceeds the compiler policy limit")
    if any(
        gap.detail is not None and len(gap.detail) > limits.maximum_gap_detail_characters
        for gap in propagation.gaps
    ):
        raise ContextCompilationError("Propagation gap detail exceeds the compiler policy limit")


def _graph_context_gaps(graph: NormalizedGraph) -> list[ContextGap]:
    return [
        ContextGap(
            code=f"GRAPH_{gap.code}",
            item_id=gap.entity_id,
            detail=gap.raw_term,
            blocking=gap.blocking,
        )
        for gap in (*graph.mapping_gaps, *graph.trust_gaps)
    ]


def compile_graph_context_pack(
    graph: NormalizedGraph,
    propagation: PropagationResult,
    propagation_policy: PropagationPolicy,
    reasoning_policy: ReasoningPolicy,
    compiler_policy: ContextCompilerPolicy,
    *,
    request_sha256: str,
    reasoning_policy_locator: str,
    expected_reasoning_policy_sha256: str,
    expected_retrieval_eval_set_sha256: str,
    expected_propagation_policy_sha256: str,
    expected_compiler_policy_sha256: str,
    unresolved_fragments: tuple[UnresolvedSourceFragment, ...] = (),
    semantic_candidates: tuple[SemanticCandidateReceipt, ...] = (),
    trusted_edge_replay: TrustedEdgeReplayInput | None = None,
) -> GraphContextPack:
    """Compile deterministic analysis context without promoting caller-supplied text."""

    if not _SHA256.fullmatch(request_sha256):
        raise ContextCompilationError("request_sha256 must be a lowercase SHA-256 digest")
    policy_body = compiler_policy.model_dump(mode="json", by_alias=True)
    if (
        contract_sha256(policy_body) != compiler_policy.sha256
        or compiler_policy.sha256 != expected_compiler_policy_sha256
    ):
        raise ContextCompilationError("In-memory compiler policy or expected root is invalid")
    if propagation_policy.sha256 != expected_propagation_policy_sha256:
        raise ContextCompilationError("Propagation policy differs from its expected root")
    _require_graph_input_bounds(graph, compiler_policy)
    _require_input_bounds(
        graph, propagation, unresolved_fragments, semantic_candidates, compiler_policy
    )
    _require_reasoning_policy_root(
        reasoning_policy,
        reasoning_policy_locator,
        expected_reasoning_policy_sha256,
        expected_retrieval_eval_set_sha256,
    )
    nodes = _require_graph_integrity(graph)
    _require_propagation_integrity(graph, propagation)
    try:
        replayed = traverse_propagation(
            graph,
            propagation.seed_ids,
            propagation_policy,
            trusted_edge_replay=trusted_edge_replay,
        )
    except PropagationEvaluationError as exc:
        raise ContextCompilationError("Propagation cannot be replayed against the graph") from exc
    if replayed != propagation:
        raise ContextCompilationError("Propagation result differs from deterministic replay")
    if (
        reasoning_policy.ontology_id,
        reasoning_policy.ontology_version,
        reasoning_policy.ontology_sha256,
    ) != (graph.ontology_id, graph.ontology_version, graph.ontology_sha256):
        raise ContextCompilationError("Reasoning policy and graph ontology identities differ")

    paths = _deduplicate(propagation.paths, "path_sha256", "propagation path")
    fragments = _deduplicate(unresolved_fragments, "fragment_id", "unresolved fragment")
    candidates = _deduplicate(semantic_candidates, "candidate_id", "semantic candidate")
    _require_policy_field_limits(graph, propagation, fragments, candidates, compiler_policy)
    if len({item.evidence_id for item in fragments}) != len(fragments):
        raise ContextCompilationError("Duplicate unresolved-fragment evidence ID is ambiguous")
    if len({item.evidence_id for item in candidates}) != len(candidates):
        raise ContextCompilationError("Duplicate semantic-candidate evidence ID is ambiguous")
    unresolved_ids = {item.evidence_id for item in fragments}
    candidate_ids = {item.evidence_id for item in candidates}
    if unresolved_ids & candidate_ids:
        raise ContextCompilationError(
            "Unresolved fragments and semantic candidates must use disjoint evidence IDs"
        )
    for item in [*fragments, *candidates]:
        _validate_fragment_against_graph(item, graph, nodes)

    compiler_input = {
        "request_sha256": request_sha256,
        "propagation_result_sha256": propagation.result_sha256,
        "reasoning_policy_sha256": reasoning_policy.policy_sha256,
        "retrieval_eval_set_sha256": reasoning_policy.retrieval_eval_set_sha256,
        "compiler_policy_sha256": compiler_policy.sha256,
        "unresolved_fragments": [
            item.model_dump(mode="json")
            for item in sorted(fragments, key=lambda item: item.fragment_id)
        ],
        "semantic_candidates": [
            item.model_dump(mode="json")
            for item in sorted(candidates, key=lambda item: item.candidate_id)
        ],
    }
    compiler_input_sha256 = _stable_hash(compiler_input)

    selected_paths, omitted_paths, path_gaps = _select_paths(paths, compiler_policy)
    graph_gaps = _graph_context_gaps(graph)
    if any(gap.blocking for gap in graph_gaps) or any(
        gap.blocking and gap.scope == "ANALYSIS" for gap in propagation.gaps
    ):
        omitted_paths = paths
        selected_paths = []
        path_gaps = [
            ContextGap(
                code="BLOCKING_GRAPH_GAPS_PREVENT_CONFIRMED_STRUCTURE",
                detail="No structure path is exposed while graph or propagation gaps block.",
                blocking=True,
            )
        ]
    selected_fragments, omitted_fragments, fragment_gaps = _select_fragments(
        fragments, compiler_policy
    )
    selected_candidates, omitted_candidates, candidate_gaps = _select_candidates(
        candidates, compiler_policy
    )
    freshness_gaps = []
    if not selected_paths or any(
        not path.local_edge_envelope_complete for path in selected_paths
    ):
        freshness_gaps.append(
            ContextGap(
                code="EDGE_FRESHNESS_RECEIPTS_UNAVAILABLE",
                detail=(
                    "One or more selected analysis paths lack current R0.1 envelope "
                    "evaluation and validity receipts."
                ),
                blocking=True,
            )
        )
    gaps = [
        *graph_gaps,
        *(
            ContextGap(
                code=f"PROPAGATION_{gap.code}",
                item_id=gap.entity_id or gap.edge_id,
                detail=gap.detail,
                blocking=gap.blocking and gap.scope == "ANALYSIS",
            )
            for gap in propagation.gaps
        ),
        *path_gaps,
        *fragment_gaps,
        *candidate_gaps,
        *freshness_gaps,
        ContextGap(
            code="RISK_FACTOR_RECEIPTS_UNAVAILABLE",
            detail="Typed provenance-bound risk-factor observations are not yet available.",
            blocking=True,
        ),
    ]
    if fragments:
        gaps.append(
            ContextGap(
                code="FRAGMENT_RESOLUTION_UNAVAILABLE",
                detail="Caller-supplied text is unresolved candidate context, not evidence.",
                blocking=True,
            )
        )
    gaps.sort(key=lambda gap: (gap.code, gap.item_id or "", gap.detail or ""))
    budget = ContextBudgetUsage(
        seed_input_limit=compiler_policy.limits.maximum_input_seeds,
        seed_input_count=len(propagation.seed_ids),
        propagation_path_input_limit=compiler_policy.limits.maximum_input_propagation_paths,
        propagation_path_input_count=len(propagation.paths),
        propagation_hop_input_limit=compiler_policy.limits.maximum_input_propagation_hops,
        propagation_hop_input_count=sum(len(path.hops) for path in propagation.paths),
        propagation_gap_input_limit=compiler_policy.limits.maximum_input_propagation_gaps,
        propagation_gap_input_count=(
            len(propagation.gaps) + len(graph.mapping_gaps) + len(graph.trust_gaps)
        ),
        source_fragment_input_limit=compiler_policy.limits.maximum_input_source_fragments,
        source_fragment_input_count=len(fragments),
        candidate_receipt_input_limit=compiler_policy.limits.maximum_input_candidate_receipts,
        candidate_receipt_input_count=len(candidates),
        structure_path_limit=compiler_policy.limits.maximum_authoritative_paths,
        structure_paths_available=len(paths),
        structure_paths_selected=len(selected_paths),
        structure_paths_omitted=len(omitted_paths),
        structure_hop_limit=compiler_policy.limits.maximum_authoritative_hops,
        structure_hops_available=sum(len(path.hops) for path in paths),
        structure_hops_selected=sum(len(path.hops) for path in selected_paths),
        structure_hops_omitted=sum(len(path.hops) for path in omitted_paths),
        unresolved_fragment_limit=compiler_policy.limits.maximum_source_fragments,
        unresolved_fragments_available=len(fragments),
        unresolved_fragments_selected=len(selected_fragments),
        unresolved_fragments_omitted=len(omitted_fragments),
        unresolved_character_limit=compiler_policy.limits.maximum_source_characters,
        unresolved_characters_available=sum(len(item.content) for item in fragments),
        unresolved_characters_selected=sum(len(item.content) for item in selected_fragments),
        unresolved_characters_omitted=sum(len(item.content) for item in omitted_fragments),
        candidate_receipt_limit=compiler_policy.limits.maximum_candidate_receipts,
        candidate_receipts_available=len(candidates),
        candidate_receipts_selected=len(selected_candidates),
        candidate_receipts_omitted=len(omitted_candidates),
        candidate_character_limit=compiler_policy.limits.maximum_candidate_characters,
        candidate_characters_available=sum(len(item.content) for item in candidates),
        candidate_characters_selected=sum(len(item.content) for item in selected_candidates),
        candidate_characters_omitted=sum(len(item.content) for item in omitted_candidates),
        identifier_character_limit=compiler_policy.limits.maximum_identifier_characters,
        locator_character_limit=compiler_policy.limits.maximum_locator_characters,
        content_character_limit=compiler_policy.limits.maximum_content_characters,
        gap_detail_character_limit=compiler_policy.limits.maximum_gap_detail_characters,
        total_pack_byte_limit=compiler_policy.limits.maximum_total_pack_bytes,
        total_pack_bytes=0,
    )
    body = {
        "schema_version": "1.0.0",
        "posture": ContextPosture.ANALYSIS_ONLY,
        "may_authorize": False,
        "authority_eligible": False,
        "analysis_complete": not any(gap.blocking for gap in gaps),
        "request_sha256": request_sha256,
        "compiler_input_sha256": compiler_input_sha256,
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
        "reasoning_policy_schema_version": reasoning_policy.schema_version,
        "reasoning_policy_locator": reasoning_policy_locator,
        "reasoning_policy_sha256": reasoning_policy.policy_sha256,
        "retrieval_eval_set_id": reasoning_policy.retrieval_eval_set_id,
        "retrieval_eval_set_path": reasoning_policy.retrieval_eval_set_path,
        "retrieval_eval_set_sha256": reasoning_policy.retrieval_eval_set_sha256,
        "propagation_policy_id": propagation.policy_id,
        "propagation_policy_version": propagation.policy_version,
        "propagation_policy_sha256": propagation.policy_sha256,
        "propagation_result_sha256": propagation.result_sha256,
        "compiler_policy_id": compiler_policy.policy_id,
        "compiler_policy_version": compiler_policy.policy_version,
        "compiler_policy_sha256": compiler_policy.sha256,
        "seed_ids": propagation.seed_ids,
        "confirmed_structure_paths": [
            item.model_dump(mode="json") for item in selected_paths
        ],
        "unresolved_fragments": [
            item.model_dump(mode="json") for item in selected_fragments
        ],
        "semantic_candidates": [
            item.model_dump(mode="json") for item in selected_candidates
        ],
        "budget": budget.model_dump(mode="json"),
        "gaps": [item.model_dump(mode="json") for item in gaps],
    }
    _set_total_pack_bytes(body)
    if body["budget"]["total_pack_bytes"] > compiler_policy.limits.maximum_total_pack_bytes:
        raise ContextCompilationError("Context pack exceeds maximumTotalPackBytes")
    return GraphContextPack.model_validate(
        {**body, "context_pack_sha256": _stable_hash(body)}
    )


def validate_graph_context_pack(
    pack: GraphContextPack,
    graph: NormalizedGraph,
    propagation: PropagationResult,
    propagation_policy: PropagationPolicy,
    reasoning_policy: ReasoningPolicy,
    compiler_policy: ContextCompilerPolicy,
    *,
    request_sha256: str,
    reasoning_policy_locator: str,
    expected_reasoning_policy_sha256: str,
    expected_retrieval_eval_set_sha256: str,
    expected_propagation_policy_sha256: str,
    expected_compiler_policy_sha256: str,
    unresolved_fragments: tuple[UnresolvedSourceFragment, ...] = (),
    semantic_candidates: tuple[SemanticCandidateReceipt, ...] = (),
) -> GraphContextPack:
    """Replay compilation from trusted inputs and reject any altered context envelope."""

    expected = compile_graph_context_pack(
        graph,
        propagation,
        propagation_policy,
        reasoning_policy,
        compiler_policy,
        request_sha256=request_sha256,
        reasoning_policy_locator=reasoning_policy_locator,
        expected_reasoning_policy_sha256=expected_reasoning_policy_sha256,
        expected_retrieval_eval_set_sha256=expected_retrieval_eval_set_sha256,
        expected_propagation_policy_sha256=expected_propagation_policy_sha256,
        expected_compiler_policy_sha256=expected_compiler_policy_sha256,
        unresolved_fragments=unresolved_fragments,
        semantic_candidates=semantic_candidates,
    )
    if pack != expected:
        raise ContextCompilationError("Context pack differs from trusted deterministic replay")
    return pack
