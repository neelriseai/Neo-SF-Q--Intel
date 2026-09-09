from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.edge_envelope import (
    TrustedEdgeEnvelope,
    TrustedEdgeReplayInput,
    TrustedEdgeVerification,
)
from neo_sf_q_intel.ontology import (
    CanonicalOntology,
    Materiality,
    NodeRole,
    NormalizedEdge,
    NormalizedGraph,
    SourceEvidenceState,
    contract_sha256,
)


class PolicyContractError(RuntimeError):
    """Raised when a propagation or risk policy is invalid or untrusted."""


class PropagationEvaluationError(RuntimeError):
    """Raised when graph and policy identities cannot be evaluated together."""


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TraversalDirection(StrEnum):
    SOURCE_TO_TARGET = "SOURCE_TO_TARGET"
    TARGET_TO_SOURCE = "TARGET_TO_SOURCE"


class PropagationRoute(PolicyModel):
    direction: TraversalDirection
    edge_source_roles: tuple[NodeRole, ...] = Field(alias="edgeSourceRoles", min_length=1)
    edge_target_roles: tuple[NodeRole, ...] = Field(alias="edgeTargetRoles", min_length=1)
    maximum_depth: int = Field(alias="maximumDepth", ge=1, le=20)
    required_evidence_states: tuple[SourceEvidenceState, ...] = Field(
        alias="requiredEvidenceStates", min_length=1
    )

    @model_validator(mode="after")
    def reject_ambiguous_values(self) -> PropagationRoute:
        _require_unique([item.value for item in self.edge_source_roles], "edge source role")
        _require_unique([item.value for item in self.edge_target_roles], "edge target role")
        _require_unique(
            [item.value for item in self.required_evidence_states], "required evidence state"
        )
        if SourceEvidenceState.UNVERIFIED in self.required_evidence_states:
            raise ValueError("UNVERIFIED evidence cannot authorize propagation")
        return self


class RelationPropagationRule(PolicyModel):
    relation_class: str = Field(alias="relationClass", min_length=1)
    routes: tuple[PropagationRoute, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_routes(self) -> RelationPropagationRule:
        identities = [
            (
                route.direction,
                route.edge_source_roles,
                route.edge_target_roles,
            )
            for route in self.routes
        ]
        if len(identities) != len(set(identities)):
            raise ValueError(f"Relation {self.relation_class!r} contains ambiguous routes")
        return self


class OntologyPolicyPin(PolicyModel):
    ontology_id: str = Field(alias="ontologyId", min_length=1)
    ontology_version: str = Field(alias="ontologyVersion", min_length=1)
    ontology_sha256: str = Field(alias="ontologySha256", pattern=r"^[a-f0-9]{64}$")


class PropagationPolicy(PolicyModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology: OntologyPolicyPin
    maximum_total_depth: int = Field(alias="maximumTotalDepth", ge=1, le=20)
    maximum_paths_per_target: int = Field(alias="maximumPathsPerTarget", ge=1, le=100)
    maximum_total_paths: int = Field(alias="maximumTotalPaths", ge=1, le=10_000)
    relation_rules: tuple[RelationPropagationRule, ...] = Field(
        alias="relationRules", min_length=1
    )

    @model_validator(mode="after")
    def validate_version_and_rules(self) -> PropagationPolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.policy_version, "policyVersion")
        _require_identifier(self.policy_id, "policyId")
        _require_unique(
            [rule.relation_class for rule in self.relation_rules], "relation rule"
        )
        for rule in self.relation_rules:
            if any(route.maximum_depth > self.maximum_total_depth for route in rule.routes):
                raise ValueError("Relation maximumDepth exceeds maximumTotalDepth")
        return self

    @property
    def rules_by_relation(self) -> dict[str, RelationPropagationRule]:
        return {item.relation_class: item for item in self.relation_rules}


class PropagationPolicyPin(PolicyModel):
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion", min_length=1)
    policy_sha256: str = Field(alias="policySha256", pattern=r"^[a-f0-9]{64}$")


class RiskFactorValue(PolicyModel):
    value: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)


class RiskFactorDefinition(PolicyModel):
    factor_id: str = Field(alias="factorId", min_length=1)
    values: tuple[RiskFactorValue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_ambiguous_values(self) -> RiskFactorDefinition:
        _require_unique([item.value for item in self.values], f"{self.factor_id} factor value")
        return self

    @property
    def scores(self) -> dict[str, int]:
        return {item.value: item.score for item in self.values}


class RelationRiskScore(PolicyModel):
    relation_class: str = Field(alias="relationClass", min_length=1)
    score: int = Field(ge=0, le=100)


class MaterialityRiskScore(PolicyModel):
    materiality: Materiality
    score: int = Field(ge=0, le=100)


class DepthRiskBand(PolicyModel):
    minimum_depth: int = Field(alias="minimumDepth", ge=1, le=20)
    maximum_depth: int = Field(alias="maximumDepth", ge=1, le=20)
    score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_range(self) -> DepthRiskBand:
        if self.maximum_depth < self.minimum_depth:
            raise ValueError("Depth band maximumDepth must not precede minimumDepth")
        return self


class RiskLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskThreshold(PolicyModel):
    level: RiskLevel
    minimum_score: int = Field(alias="minimumScore", ge=0)


class AnalysisRiskPolicy(PolicyModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology: OntologyPolicyPin
    propagation_policy: PropagationPolicyPin = Field(alias="propagationPolicy")
    required_factors: tuple[str, ...] = Field(alias="requiredFactors", min_length=1)
    factors: tuple[RiskFactorDefinition, ...] = Field(min_length=1)
    relation_scores: tuple[RelationRiskScore, ...] = Field(
        alias="relationScores", min_length=1
    )
    materiality_scores: tuple[MaterialityRiskScore, ...] = Field(
        alias="materialityScores", min_length=1
    )
    depth_bands: tuple[DepthRiskBand, ...] = Field(alias="depthBands", min_length=1)
    thresholds: tuple[RiskThreshold, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_version_and_tables(self) -> AnalysisRiskPolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.policy_version, "policyVersion")
        _require_identifier(self.policy_id, "policyId")
        factor_ids = [item.factor_id for item in self.factors]
        _require_unique(factor_ids, "risk factor")
        _require_unique(list(self.required_factors), "required risk factor")
        missing = set(self.required_factors) - set(factor_ids)
        if missing:
            raise ValueError("Required risk factors are undefined: " + ", ".join(sorted(missing)))
        _require_unique(
            [item.relation_class for item in self.relation_scores], "relation risk score"
        )
        _require_unique(
            [item.materiality.value for item in self.materiality_scores],
            "materiality risk score",
        )
        if {item.materiality for item in self.materiality_scores} != set(Materiality):
            raise ValueError("Materiality risk scores must be exhaustive")
        if {item.level for item in self.thresholds} != set(RiskLevel):
            raise ValueError("Risk thresholds must define HIGH, MEDIUM and LOW")
        ordered = sorted(self.thresholds, key=lambda item: item.minimum_score, reverse=True)
        if [item.level for item in ordered] != [RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW]:
            raise ValueError("Risk thresholds must descend HIGH, MEDIUM, LOW")
        if ordered[-1].minimum_score != 0:
            raise ValueError("LOW risk threshold must start at zero")
        sorted_bands = sorted(self.depth_bands, key=lambda item: item.minimum_depth)
        expected_start = 1
        for band in sorted_bands:
            if band.minimum_depth != expected_start:
                raise ValueError("Depth risk bands must be contiguous and non-overlapping")
            expected_start = band.maximum_depth + 1
        return self

    @property
    def factors_by_id(self) -> dict[str, RiskFactorDefinition]:
        return {item.factor_id: item for item in self.factors}

    @property
    def relation_score_map(self) -> dict[str, int]:
        return {item.relation_class: item.score for item in self.relation_scores}

    @property
    def materiality_score_map(self) -> dict[Materiality, int]:
        return {item.materiality: item.score for item in self.materiality_scores}


class PathHopReceipt(PolicyModel):
    position: int = Field(ge=1)
    edge_id: str = Field(min_length=1)
    relation_class: str = Field(min_length=1)
    direction: TraversalDirection
    traversal_from_id: str = Field(min_length=1)
    traversal_to_id: str = Field(min_length=1)
    edge_source_id: str = Field(min_length=1)
    edge_target_id: str = Field(min_length=1)
    source_artifact: str = Field(min_length=1)
    evidence_state: SourceEvidenceState
    source_snapshot: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor_id: str = Field(min_length=1)
    materiality: Materiality
    provenance_scope: Literal[
        "LEGACY_ANALYSIS_ONLY", "VERIFIED_EDGE_ENVELOPE"
    ] = "LEGACY_ANALYSIS_ONLY"
    source_artifact_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    extractor_version: str | None = None
    extractor_implementation_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    trusted_edge_envelope_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    trusted_edge_policy_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    trusted_edge_registry_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    trusted_edge_verification_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    trusted_edge_evaluated_at: str | None = None
    trusted_edge_valid_until: str | None = None
    release_blocking_gap_codes: tuple[
        Literal["UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"], ...
    ] = ()

    @model_validator(mode="after")
    def validate_provenance_scope(self) -> PathHopReceipt:
        trusted_values = (
            self.source_artifact_sha256,
            self.extractor_version,
            self.extractor_implementation_sha256,
            self.trusted_edge_envelope_sha256,
            self.trusted_edge_policy_sha256,
            self.trusted_edge_registry_sha256,
            self.trusted_edge_verification_sha256,
            self.trusted_edge_evaluated_at,
            self.trusted_edge_valid_until,
        )
        if self.provenance_scope == "VERIFIED_EDGE_ENVELOPE" and not all(trusted_values):
            raise ValueError("Verified edge hops require the complete R0.1 envelope binding")
        if self.provenance_scope == "VERIFIED_EDGE_ENVELOPE" and (
            self.release_blocking_gap_codes
            != ("UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",)
        ):
            raise ValueError("Verified R0.1 hops must preserve the source-capture gap")
        if self.provenance_scope == "LEGACY_ANALYSIS_ONLY" and any(trusted_values):
            raise ValueError("Legacy analysis hops cannot carry partial trusted-edge fields")
        if self.provenance_scope == "LEGACY_ANALYSIS_ONLY" and self.release_blocking_gap_codes:
            raise ValueError("Legacy analysis hops cannot carry trusted-edge gap claims")
        return self


class PropagationPathReceipt(PolicyModel):
    project_id: str = Field(min_length=1)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    source_snapshot: str = Field(min_length=1)
    ontology_id: str = Field(min_length=1)
    ontology_version: str = Field(min_length=1)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hops: tuple[PathHopReceipt, ...] = Field(min_length=1)
    local_edge_envelope_complete: bool = False
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    path_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_full_ordered_path(self) -> PropagationPathReceipt:
        if [item.position for item in self.hops] != list(range(1, len(self.hops) + 1)):
            raise ValueError("Path hop positions must be contiguous and ordered")
        if self.hops[0].traversal_from_id != self.seed_id:
            raise ValueError("First path hop must begin at seed_id")
        if self.hops[-1].traversal_to_id != self.target_id:
            raise ValueError("Last path hop must end at target_id")
        for left, right in zip(self.hops, self.hops[1:], strict=False):
            if left.traversal_to_id != right.traversal_from_id:
                raise ValueError("Path hops must be contiguous")
        visited = [self.seed_id]
        for hop in self.hops:
            expected = (
                (hop.edge_source_id, hop.edge_target_id)
                if hop.direction is TraversalDirection.SOURCE_TO_TARGET
                else (hop.edge_target_id, hop.edge_source_id)
            )
            if (hop.traversal_from_id, hop.traversal_to_id) != expected:
                raise ValueError("Path hop direction disagrees with original edge orientation")
            if hop.source_snapshot != self.source_snapshot:
                raise ValueError("Every path hop must belong to the receipt source snapshot")
            if hop.source_hash != self.source_graph_sha256:
                raise ValueError("Every path hop must match the trusted source graph digest")
            visited.append(hop.traversal_to_id)
        if len(visited) != len(set(visited)):
            raise ValueError("A path receipt must not contain a cycle")
        if self.local_edge_envelope_complete != all(
            hop.provenance_scope == "VERIFIED_EDGE_ENVELOPE" for hop in self.hops
        ):
            raise ValueError("Path edge-provenance completeness differs from its hops")
        body = self.model_dump(mode="json")
        declared = body.pop("path_sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Path receipt digest does not match its content")
        return self


class PropagationGap(PolicyModel):
    code: str = Field(min_length=1)
    entity_id: str | None = None
    edge_id: str | None = None
    detail: str | None = None
    blocking: bool
    scope: Literal["ANALYSIS", "RELEASE_ONLY"] = "ANALYSIS"


class PropagationResult(PolicyModel):
    project_id: str
    source_graph_sha256: str
    policy_id: str
    policy_version: str
    policy_sha256: str
    ontology_id: str
    ontology_version: str
    ontology_sha256: str
    profile_id: str
    profile_version: str
    profile_sha256: str
    graph_sha256: str
    source_snapshot: str
    seed_ids: tuple[str, ...]
    paths: tuple[PropagationPathReceipt, ...]
    gaps: tuple[PropagationGap, ...]
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_result_identity_and_digest(self) -> PropagationResult:
        expected_identity = (
            self.project_id,
            self.source_graph_sha256,
            self.policy_id,
            self.policy_version,
            self.policy_sha256,
            self.ontology_id,
            self.ontology_version,
            self.ontology_sha256,
            self.profile_id,
            self.profile_version,
            self.profile_sha256,
            self.graph_sha256,
            self.source_snapshot,
        )
        for path in self.paths:
            path_identity = (
                path.project_id,
                path.source_graph_sha256,
                path.policy_id,
                path.policy_version,
                path.policy_sha256,
                path.ontology_id,
                path.ontology_version,
                path.ontology_sha256,
                path.profile_id,
                path.profile_version,
                path.profile_sha256,
                path.graph_sha256,
                path.source_snapshot,
            )
            if path_identity != expected_identity or path.seed_id not in self.seed_ids:
                raise ValueError("Propagation path identity differs from its result envelope")
        body = self.model_dump(mode="json")
        declared = body.pop("result_sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Propagation result digest does not match its content")
        return self


class PathRiskContribution(PolicyModel):
    path_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    score: int = Field(ge=0)
    level: RiskLevel
    components: dict[str, int]


class RiskEvaluationGap(PolicyModel):
    code: str
    target_id: str
    factor_id: str | None = None
    value: str | None = None
    blocking: bool = True


class TargetRiskAssessment(PolicyModel):
    target_id: str
    level: RiskLevel
    score: int = Field(ge=0)
    factors: dict[str, str]
    path_contributions: tuple[PathRiskContribution, ...] = Field(min_length=1)
    conflicting_path_levels: bool


class AnalysisRiskResult(PolicyModel):
    policy_id: str
    policy_version: str
    policy_sha256: str
    propagation_result_sha256: str
    assessments: tuple[TargetRiskAssessment, ...]
    gaps: tuple[RiskEvaluationGap, ...]
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_result_digest(self) -> AnalysisRiskResult:
        body = self.model_dump(mode="json")
        declared = body.pop("result_sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Analysis risk result digest does not match its content")
        return self


_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
DEFAULT_PROPAGATION_POLICY_SHA256 = (
    "66c83e8cb13b68cbffb363a582f0a3b5927f4ec04bddc3dcab5b560b02c2cdf5"
)
DEFAULT_ANALYSIS_RISK_POLICY_SHA256 = (
    "57ab48f6c9016e20d599aee4ff9070e34b7583b566251ded78019d895c683e6a"
)


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must use semantic versioning")


def _require_identifier(value: str, field: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase stable identifier")


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"Ambiguous duplicate {label}: {', '.join(duplicates)}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyContractError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _read_policy(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file():
        raise PolicyContractError(f"Required policy is missing: {path.name}")
    try:
        body = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except PolicyContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(body, dict):
        raise PolicyContractError(f"Expected a JSON object in {path.name}")
    actual = contract_sha256(body)
    if body.get("sha256") != actual:
        raise PolicyContractError(f"{path.name} digest does not match its content")
    if actual != expected_sha256.casefold():
        raise PolicyContractError(f"{path.name} does not match its trusted digest")
    return body


def _validate_ontology_pin(pin: OntologyPolicyPin, ontology: CanonicalOntology) -> None:
    if (pin.ontology_id, pin.ontology_version, pin.ontology_sha256) != (
        ontology.ontology_id,
        ontology.ontology_version,
        ontology.sha256,
    ):
        raise PolicyContractError("Policy does not pin the loaded canonical ontology")


def load_propagation_policy(
    path: Path,
    ontology: CanonicalOntology,
    *,
    expected_sha256: str = DEFAULT_PROPAGATION_POLICY_SHA256,
) -> PropagationPolicy:
    body = _read_policy(path, expected_sha256)
    try:
        policy = PropagationPolicy.model_validate(body)
    except ValidationError as exc:
        raise PolicyContractError(f"Invalid propagation policy: {exc}") from exc
    _validate_ontology_pin(policy.ontology, ontology)
    policy_relations = set(policy.rules_by_relation)
    ontology_relations = set(ontology.relations_by_id)
    if policy_relations != ontology_relations:
        missing = sorted(ontology_relations - policy_relations)
        unknown = sorted(policy_relations - ontology_relations)
        raise PolicyContractError(
            f"Propagation relation rules must be exhaustive; missing={missing}, unknown={unknown}"
        )
    node_roles = ontology.nodes_by_id
    for rule in policy.relation_rules:
        relation = ontology.relations_by_id[rule.relation_class]
        legal_role_pairs = {
            (
                node_roles[item.source_class].role,
                node_roles[item.target_class].role,
            )
            for item in relation.legal_endpoints
        }
        for route in rule.routes:
            configured_pairs = {
                (source_role, target_role)
                for source_role in route.edge_source_roles
                for target_role in route.edge_target_roles
            }
            if not configured_pairs <= legal_role_pairs:
                raise PolicyContractError(
                    f"Propagation route for {rule.relation_class!r} allows an illegal role pair"
                )
    return policy


def load_analysis_risk_policy(
    path: Path,
    ontology: CanonicalOntology,
    propagation_policy: PropagationPolicy,
    *,
    expected_sha256: str = DEFAULT_ANALYSIS_RISK_POLICY_SHA256,
) -> AnalysisRiskPolicy:
    body = _read_policy(path, expected_sha256)
    try:
        policy = AnalysisRiskPolicy.model_validate(body)
    except ValidationError as exc:
        raise PolicyContractError(f"Invalid analysis risk policy: {exc}") from exc
    _validate_ontology_pin(policy.ontology, ontology)
    pin = policy.propagation_policy
    if (pin.policy_id, pin.policy_version, pin.policy_sha256) != (
        propagation_policy.policy_id,
        propagation_policy.policy_version,
        propagation_policy.sha256,
    ):
        raise PolicyContractError("Risk policy does not pin the loaded propagation policy")
    relation_ids = {item.relation_class for item in policy.relation_scores}
    ontology_ids = set(ontology.relations_by_id)
    if relation_ids != ontology_ids:
        raise PolicyContractError("Risk relation scores must cover every canonical relation")
    if max(item.maximum_depth for item in policy.depth_bands) < (
        propagation_policy.maximum_total_depth
    ):
        raise PolicyContractError("Risk depth bands do not cover propagation maximumTotalDepth")
    return policy


def _edge_is_legacy_analysis_receiptable(
    edge: NormalizedEdge, graph: NormalizedGraph
) -> bool:
    """Preserve pre-R0.1 analysis behavior without asserting release authority."""

    return bool(
        graph.source_snapshot
        and edge.source_snapshot == graph.source_snapshot
        and edge.source
        and edge.source_hash == graph.source_graph_sha256
        and edge.extractor_id
    )


def _route_neighbors(
    edge: NormalizedEdge,
    current_id: str,
    source_role: NodeRole,
    target_role: NodeRole,
    route: PropagationRoute,
    depth: int,
) -> tuple[str, TraversalDirection] | None:
    if depth > route.maximum_depth:
        return None
    if source_role not in route.edge_source_roles or target_role not in route.edge_target_roles:
        return None
    if route.direction is TraversalDirection.SOURCE_TO_TARGET and current_id == edge.source_id:
        return edge.target_id, route.direction
    if route.direction is TraversalDirection.TARGET_TO_SOURCE and current_id == edge.target_id:
        return edge.source_id, route.direction
    return None


def _path_payload(
    seed_id: str,
    target_id: str,
    graph: NormalizedGraph,
    policy: PropagationPolicy,
    hops: tuple[PathHopReceipt, ...],
) -> dict[str, Any]:
    return {
        "project_id": graph.project_id,
        "source_graph_sha256": graph.source_graph_sha256,
        "seed_id": seed_id,
        "target_id": target_id,
        "source_snapshot": graph.source_snapshot,
        "ontology_id": graph.ontology_id,
        "ontology_version": graph.ontology_version,
        "ontology_sha256": graph.ontology_sha256,
        "profile_id": graph.profile_id,
        "profile_version": graph.profile_version,
        "profile_sha256": graph.profile_sha256,
        "graph_sha256": graph.graph_sha256,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_sha256": policy.sha256,
        "hops": [item.model_dump(mode="json") for item in hops],
        "local_edge_envelope_complete": all(
            hop.provenance_scope == "VERIFIED_EDGE_ENVELOPE" for hop in hops
        ),
        "authority_scope": "ANALYSIS_ONLY",
    }


def _trusted_envelopes_for_graph(
    graph: NormalizedGraph,
    replay_input: TrustedEdgeReplayInput | None,
) -> tuple[
    dict[str, tuple[TrustedEdgeEnvelope, TrustedEdgeVerification]],
    TrustedEdgeVerification | None,
]:
    if replay_input is None:
        return {}, None
    replayed = replay_input.verify(graph)
    identity = (
        replayed.project_id,
        replayed.source_snapshot,
        replayed.source_graph_sha256,
        replayed.normalized_graph_sha256,
        replayed.ontology_id,
        replayed.ontology_version,
        replayed.ontology_sha256,
        replayed.profile_id,
        replayed.profile_version,
        replayed.profile_sha256,
    )
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
    if identity != graph_identity:
        raise PropagationEvaluationError(
            "Trusted-edge verification receipt differs from the normalized graph"
        )
    if not replayed.coverage_complete:
        return {}, replayed
    return (
        {
            edge_id: (envelope, replayed)
            for edge_id, envelope in replayed.accepted_by_edge_id.items()
        },
        replayed,
    )


def _stable_hash(body: dict[str, Any]) -> str:
    encoded = json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_runtime_policy_integrity(
    policy: PropagationPolicy | AnalysisRiskPolicy,
) -> None:
    body = policy.model_dump(mode="json", by_alias=True)
    if contract_sha256(body) != policy.sha256:
        raise PropagationEvaluationError("In-memory policy digest does not match its content")
    try:
        type(policy).model_validate(body)
    except ValidationError as exc:
        raise PropagationEvaluationError("In-memory policy is structurally invalid") from exc


def _require_normalized_graph_integrity(
    graph: NormalizedGraph, policy: PropagationPolicy
) -> dict[str, Any]:
    body = graph.model_dump(mode="json")
    declared = body.pop("graph_sha256")
    if _stable_hash(body) != declared:
        raise PropagationEvaluationError("Normalized graph digest does not match its content")
    nodes = {item.node_id: item for item in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise PropagationEvaluationError("Normalized graph node IDs must be unique")
    edge_ids = {item.edge_id for item in graph.edges}
    if len(edge_ids) != len(graph.edges):
        raise PropagationEvaluationError("Normalized graph edge IDs must be unique")
    for edge in graph.edges:
        if edge.source_id not in nodes or edge.target_id not in nodes:
            raise PropagationEvaluationError("Normalized graph edge has an unknown endpoint")
        if edge.canonical_relation not in policy.rules_by_relation:
            raise PropagationEvaluationError("Normalized graph edge has an unknown relation")
    if any(gap.blocking for gap in graph.mapping_gaps):
        raise PropagationEvaluationError("Normalized graph has blocking ontology mapping gaps")
    return nodes


def _require_propagation_result_integrity(propagation: PropagationResult) -> None:
    try:
        PropagationResult.model_validate(propagation.model_dump(mode="json"))
    except ValidationError as exc:
        raise PropagationEvaluationError(
            "Propagation result or path digest does not match its content"
        ) from exc


def _node_is_receiptable(node: Any, graph: NormalizedGraph) -> bool:
    return bool(
        node.evidence_state is SourceEvidenceState.CONFIRMED
        and node.source
        and node.source_snapshot == graph.source_snapshot
        and node.source_hash == graph.source_graph_sha256
        and node.extractor_id
    )


def traverse_propagation(
    graph: NormalizedGraph,
    seed_ids: list[str] | tuple[str, ...],
    policy: PropagationPolicy,
    *,
    trusted_edge_replay: TrustedEdgeReplayInput | None = None,
) -> PropagationResult:
    """Enumerate deterministic simple paths under explicit directional relation rules."""

    _require_runtime_policy_integrity(policy)
    if (graph.ontology_id, graph.ontology_version, graph.ontology_sha256) != (
        policy.ontology.ontology_id,
        policy.ontology.ontology_version,
        policy.ontology.ontology_sha256,
    ):
        raise PropagationEvaluationError("Graph and propagation ontology identities differ")
    if not graph.source_snapshot:
        raise PropagationEvaluationError("A source snapshot is required for path receipts")
    if not graph.project_id or not graph.source_graph_sha256:
        raise PropagationEvaluationError(
            "Project and trusted source-graph identities are required for path receipts"
        )
    nodes = _require_normalized_graph_integrity(graph, policy)
    trusted_envelopes, trusted_verification = _trusted_envelopes_for_graph(
        graph, trusted_edge_replay
    )
    ordered_seeds = tuple(sorted(set(seed_ids)))
    gaps: list[PropagationGap] = []
    if trusted_verification is not None:
        gaps.append(
            PropagationGap(
                code="UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
                detail=None,
                blocking=True,
                scope="RELEASE_ONLY",
            )
        )
        for rejection in trusted_verification.rejections:
            gaps.append(
                PropagationGap(
                    code=f"TRUSTED_EDGE_{rejection.code.value}",
                    detail=rejection.edge_identity_sha256,
                    blocking=True,
                    scope="RELEASE_ONLY",
                )
            )
    valid_seeds: list[str] = []
    for seed_id in ordered_seeds:
        if seed_id not in nodes:
            gaps.append(
                PropagationGap(
                    code="UNKNOWN_SEED", entity_id=seed_id, detail=None, blocking=True
                )
            )
        elif (
            nodes[seed_id].source_hash
            and nodes[seed_id].source_hash != graph.source_graph_sha256
        ):
            gaps.append(
                PropagationGap(
                    code="NODE_SOURCE_HASH_MISMATCH",
                    entity_id=seed_id,
                    detail=None,
                    blocking=nodes[seed_id].materiality is Materiality.MATERIAL,
                )
            )
        elif not _node_is_receiptable(nodes[seed_id], graph):
            gaps.append(
                PropagationGap(
                    code="SEED_PROVENANCE_INCOMPLETE",
                    entity_id=seed_id,
                    detail=None,
                    blocking=nodes[seed_id].materiality is Materiality.MATERIAL,
                )
            )
        else:
            valid_seeds.append(seed_id)

    edges = sorted(graph.edges, key=lambda item: item.edge_id)
    paths: list[PropagationPathReceipt] = []
    target_path_counts: dict[str, int] = {}
    budget_exhausted = False
    seen_path_signatures: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for seed_id in valid_seeds:
        queue: deque[tuple[str, tuple[PathHopReceipt, ...], frozenset[str]]] = deque(
            [(seed_id, (), frozenset({seed_id}))]
        )
        while queue:
            if budget_exhausted:
                break
            current_id, prior_hops, visited = queue.popleft()
            next_depth = len(prior_hops) + 1
            if next_depth > policy.maximum_total_depth:
                continue
            for edge in edges:
                if budget_exhausted:
                    break
                rule = policy.rules_by_relation[edge.canonical_relation]
                source_role = nodes[edge.source_id].role
                target_role = nodes[edge.target_id].role
                for route in rule.routes:
                    neighbor = _route_neighbors(
                        edge,
                        current_id,
                        source_role,
                        target_role,
                        route,
                        next_depth,
                    )
                    if neighbor is None:
                        continue
                    neighbor_id, direction = neighbor
                    if (
                        nodes[neighbor_id].source_hash
                        and nodes[neighbor_id].source_hash != graph.source_graph_sha256
                    ):
                        gaps.append(
                            PropagationGap(
                                code="NODE_SOURCE_HASH_MISMATCH",
                                entity_id=neighbor_id,
                                edge_id=edge.edge_id,
                                detail=None,
                                blocking=(
                                    nodes[neighbor_id].materiality is Materiality.MATERIAL
                                ),
                            )
                        )
                        continue
                    if not _node_is_receiptable(nodes[neighbor_id], graph):
                        gaps.append(
                            PropagationGap(
                                code="NODE_PROVENANCE_INCOMPLETE",
                                entity_id=neighbor_id,
                                edge_id=edge.edge_id,
                                detail=None,
                                blocking=(
                                    nodes[neighbor_id].materiality is Materiality.MATERIAL
                                ),
                            )
                        )
                        continue
                    if edge.evidence_state not in route.required_evidence_states:
                        gaps.append(
                            PropagationGap(
                                code="EDGE_EVIDENCE_STATE_REJECTED",
                                entity_id=neighbor_id,
                                edge_id=edge.edge_id,
                                detail=edge.evidence_state.value,
                                blocking=edge.materiality is Materiality.MATERIAL,
                            )
                        )
                        continue
                    if edge.source_hash and edge.source_hash != graph.source_graph_sha256:
                        gaps.append(
                            PropagationGap(
                                code="EDGE_SOURCE_HASH_MISMATCH",
                                entity_id=neighbor_id,
                                edge_id=edge.edge_id,
                                detail=None,
                                blocking=edge.materiality is Materiality.MATERIAL,
                            )
                        )
                        continue
                    if not _edge_is_legacy_analysis_receiptable(edge, graph):
                        gaps.append(
                            PropagationGap(
                                code="EDGE_PROVENANCE_INCOMPLETE",
                                entity_id=neighbor_id,
                                edge_id=edge.edge_id,
                                detail=None,
                                blocking=edge.materiality is Materiality.MATERIAL,
                            )
                        )
                        continue
                    if neighbor_id in visited:
                        continue
                    trusted_pair = trusted_envelopes.get(edge.edge_id)
                    trusted_fields: dict[str, Any] = {}
                    if trusted_pair is not None:
                        trusted_envelope, verification = trusted_pair
                        trusted_fields = {
                            "provenance_scope": "VERIFIED_EDGE_ENVELOPE",
                            "source_artifact_sha256": (
                                trusted_envelope.source_artifact_sha256
                            ),
                            "extractor_version": trusted_envelope.extractor_version,
                            "extractor_implementation_sha256": (
                                trusted_envelope.extractor_implementation_sha256
                            ),
                            "trusted_edge_envelope_sha256": (
                                trusted_envelope.envelope_sha256
                            ),
                            "trusted_edge_policy_sha256": (
                                trusted_envelope.trust_policy_sha256
                            ),
                            "trusted_edge_registry_sha256": (
                                trusted_envelope.extractor_registry_sha256
                            ),
                            "trusted_edge_verification_sha256": (
                                verification.verification_sha256
                            ),
                            "trusted_edge_evaluated_at": verification.evaluated_at,
                            "trusted_edge_valid_until": trusted_envelope.valid_until,
                            "release_blocking_gap_codes": (
                                "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
                            ),
                        }
                    hop = PathHopReceipt(
                        position=next_depth,
                        edge_id=edge.edge_id,
                        relation_class=edge.canonical_relation,
                        direction=direction,
                        traversal_from_id=current_id,
                        traversal_to_id=neighbor_id,
                        edge_source_id=edge.source_id,
                        edge_target_id=edge.target_id,
                        source_artifact=edge.source,
                        evidence_state=edge.evidence_state,
                        source_snapshot=edge.source_snapshot,
                        source_hash=edge.source_hash,
                        extractor_id=edge.extractor_id,
                        materiality=edge.materiality,
                        **trusted_fields,
                    )
                    hops = (*prior_hops, hop)
                    signature = (
                        seed_id,
                        tuple((item.edge_id, item.direction.value) for item in hops),
                    )
                    if signature in seen_path_signatures:
                        continue
                    seen_path_signatures.add(signature)
                    payload = _path_payload(seed_id, neighbor_id, graph, policy, hops)
                    if len(paths) >= policy.maximum_total_paths:
                        gaps.append(
                            PropagationGap(
                                code="TOTAL_PATH_LIMIT_REACHED",
                                detail=None,
                                blocking=True,
                            )
                        )
                        budget_exhausted = True
                        break
                    target_count = target_path_counts.get(neighbor_id, 0)
                    if target_count >= policy.maximum_paths_per_target:
                        gaps.append(
                            PropagationGap(
                                code="TARGET_PATH_LIMIT_REACHED",
                                entity_id=neighbor_id,
                                detail=None,
                                blocking=True,
                            )
                        )
                        continue
                    paths.append(
                        PropagationPathReceipt(
                            **payload,
                            path_sha256=_stable_hash(payload),
                        )
                    )
                    target_path_counts[neighbor_id] = target_count + 1
                    queue.append((neighbor_id, hops, visited | {neighbor_id}))
        if budget_exhausted:
            break

    paths.sort(
        key=lambda item: (
            item.target_id,
            item.seed_id,
            len(item.hops),
            tuple((hop.edge_id, hop.direction.value) for hop in item.hops),
            item.path_sha256,
        )
    )
    gaps = sorted(
        set(gaps),
        key=lambda item: (
            item.code,
            item.scope,
            item.blocking,
            item.entity_id or "",
            item.edge_id or "",
            item.detail or "",
        ),
    )
    body = {
        "project_id": graph.project_id,
        "source_graph_sha256": graph.source_graph_sha256,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_sha256": policy.sha256,
        "ontology_id": graph.ontology_id,
        "ontology_version": graph.ontology_version,
        "ontology_sha256": graph.ontology_sha256,
        "profile_id": graph.profile_id,
        "profile_version": graph.profile_version,
        "profile_sha256": graph.profile_sha256,
        "graph_sha256": graph.graph_sha256,
        "source_snapshot": graph.source_snapshot,
        "seed_ids": ordered_seeds,
        "paths": [item.model_dump(mode="json") for item in paths],
        "gaps": [item.model_dump(mode="json") for item in gaps],
    }
    return PropagationResult.model_validate({**body, "result_sha256": _stable_hash(body)})


def _risk_level(score: int, policy: AnalysisRiskPolicy) -> RiskLevel:
    for threshold in sorted(
        policy.thresholds, key=lambda item: item.minimum_score, reverse=True
    ):
        if score >= threshold.minimum_score:
            return threshold.level
    raise AssertionError("Validated thresholds always include LOW at zero")


def _depth_score(depth: int, policy: AnalysisRiskPolicy) -> int:
    for band in policy.depth_bands:
        if band.minimum_depth <= depth <= band.maximum_depth:
            return band.score
    raise PolicyContractError(f"Risk policy does not score path depth {depth}")


def evaluate_analysis_risk(
    propagation: PropagationResult,
    factors_by_target: dict[str, dict[str, str]],
    policy: AnalysisRiskPolicy,
    *,
    expected_graph: NormalizedGraph,
) -> AnalysisRiskResult:
    """Evaluate path-bound risk; missing factors produce gaps, never a default level."""

    _require_runtime_policy_integrity(policy)
    _require_propagation_result_integrity(propagation)
    expected_graph_body = expected_graph.model_dump(mode="json")
    expected_graph_digest = expected_graph_body.pop("graph_sha256")
    if _stable_hash(expected_graph_body) != expected_graph_digest:
        raise PropagationEvaluationError("Expected normalized graph digest is invalid")
    expected_graph_identity = (
        expected_graph.project_id,
        expected_graph.source_graph_sha256,
        expected_graph.ontology_id,
        expected_graph.ontology_version,
        expected_graph.ontology_sha256,
        expected_graph.profile_id,
        expected_graph.profile_version,
        expected_graph.profile_sha256,
        expected_graph.graph_sha256,
        expected_graph.source_snapshot,
    )
    propagation_graph_identity = (
        propagation.project_id,
        propagation.source_graph_sha256,
        propagation.ontology_id,
        propagation.ontology_version,
        propagation.ontology_sha256,
        propagation.profile_id,
        propagation.profile_version,
        propagation.profile_sha256,
        propagation.graph_sha256,
        propagation.source_snapshot,
    )
    if propagation_graph_identity != expected_graph_identity:
        raise PropagationEvaluationError(
            "Propagation result differs from the expected source and graph binding"
        )
    pin = policy.propagation_policy
    if (propagation.policy_id, propagation.policy_version, propagation.policy_sha256) != (
        pin.policy_id,
        pin.policy_version,
        pin.policy_sha256,
    ):
        raise PropagationEvaluationError("Risk policy and propagation result identities differ")
    blocking_propagation_gaps = [
        gap for gap in propagation.gaps if gap.blocking and gap.scope == "ANALYSIS"
    ]
    if blocking_propagation_gaps:
        risk_gaps = [
            RiskEvaluationGap(
                code="PROPAGATION_INCOMPLETE",
                target_id=gap.entity_id or "*",
                value=gap.code,
            )
            for gap in blocking_propagation_gaps
        ]
        body = {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_sha256": policy.sha256,
            "propagation_result_sha256": propagation.result_sha256,
            "assessments": [],
            "gaps": [item.model_dump(mode="json") for item in risk_gaps],
        }
        return AnalysisRiskResult.model_validate(
            {**body, "result_sha256": _stable_hash(body)}
        )
    paths_by_target: dict[str, list[PropagationPathReceipt]] = {}
    for path in propagation.paths:
        paths_by_target.setdefault(path.target_id, []).append(path)
    gaps: list[RiskEvaluationGap] = []
    assessments: list[TargetRiskAssessment] = []
    factor_definitions = policy.factors_by_id
    all_targets = sorted(set(paths_by_target) | set(factors_by_target))
    for target_id in all_targets:
        target_paths = paths_by_target.get(target_id, [])
        if not target_paths:
            gaps.append(RiskEvaluationGap(code="NO_PROPAGATION_PATH", target_id=target_id))
            continue
        supplied = factors_by_target.get(target_id, {})
        missing = sorted(set(policy.required_factors) - set(supplied))
        if missing:
            gaps.extend(
                RiskEvaluationGap(
                    code="MISSING_RISK_FACTOR", target_id=target_id, factor_id=factor
                )
                for factor in missing
            )
            continue
        invalid = [
            (factor_id, value)
            for factor_id, value in sorted(supplied.items())
            if factor_id not in factor_definitions
            or value not in factor_definitions[factor_id].scores
        ]
        if invalid:
            gaps.extend(
                RiskEvaluationGap(
                    code="UNKNOWN_RISK_FACTOR_VALUE",
                    target_id=target_id,
                    factor_id=factor_id,
                    value=value,
                )
                for factor_id, value in invalid
            )
            continue
        factor_scores = {
            factor_id: factor_definitions[factor_id].scores[supplied[factor_id]]
            for factor_id in policy.required_factors
        }
        contributions: list[PathRiskContribution] = []
        for path in sorted(target_paths, key=lambda item: item.path_sha256):
            relation_component = max(
                policy.relation_score_map[hop.relation_class] for hop in path.hops
            )
            materiality_component = max(
                policy.materiality_score_map[hop.materiality] for hop in path.hops
            )
            components = {
                **factor_scores,
                "pathRelation": relation_component,
                "pathMateriality": materiality_component,
                "pathDepth": _depth_score(len(path.hops), policy),
            }
            score = sum(components.values())
            contributions.append(
                PathRiskContribution(
                    path_sha256=path.path_sha256,
                    score=score,
                    level=_risk_level(score, policy),
                    components=components,
                )
            )
        highest = max(contributions, key=lambda item: item.score)
        assessments.append(
            TargetRiskAssessment(
                target_id=target_id,
                level=highest.level,
                score=highest.score,
                factors={key: supplied[key] for key in sorted(supplied)},
                path_contributions=tuple(contributions),
                conflicting_path_levels=len({item.level for item in contributions}) > 1,
            )
        )
    body = {
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_sha256": policy.sha256,
        "propagation_result_sha256": propagation.result_sha256,
        "assessments": [item.model_dump(mode="json") for item in assessments],
        "gaps": [item.model_dump(mode="json") for item in gaps],
    }
    return AnalysisRiskResult.model_validate({**body, "result_sha256": _stable_hash(body)})
