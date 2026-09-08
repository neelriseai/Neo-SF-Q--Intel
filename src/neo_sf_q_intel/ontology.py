from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class OntologyContractError(RuntimeError):
    """Raised when an ontology or source profile is missing, ambiguous, or untrusted."""


class OntologyNormalizationError(RuntimeError):
    """Raised when a source graph cannot be deterministically identified."""


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Materiality(StrEnum):
    MATERIAL = "MATERIAL"
    SUPPORTING = "SUPPORTING"


class NodeRole(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    CAPABILITY = "CAPABILITY"
    IMPLEMENTATION = "IMPLEMENTATION"
    AUTOMATION = "AUTOMATION"
    CONFIGURATION = "CONFIGURATION"
    DATA_SCHEMA = "DATA_SCHEMA"
    SECURITY = "SECURITY"
    TEST = "TEST"
    ARTIFACT = "ARTIFACT"
    EXPERIENCE = "EXPERIENCE"
    PROCESS = "PROCESS"
    ACTOR = "ACTOR"
    DATA_FIXTURE = "DATA_FIXTURE"


class SourceEvidenceState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    CONTRADICTORY = "CONTRADICTORY"
    STALE = "STALE"
    REJECTED = "REJECTED"


Identifier = str
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class CanonicalNodeClass(ContractModel):
    id: Identifier
    role: NodeRole
    materiality: Materiality


class EndpointSignature(ContractModel):
    source_class: Identifier = Field(alias="sourceClass")
    target_class: Identifier = Field(alias="targetClass")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CanonicalRelationClass(ContractModel):
    id: Identifier
    materiality: Materiality
    legal_endpoints: tuple[EndpointSignature, ...] = Field(alias="legalEndpoints", min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def reject_duplicate_signatures(self) -> CanonicalRelationClass:
        pairs = [(item.source_class, item.target_class) for item in self.legal_endpoints]
        if len(pairs) != len(set(pairs)):
            raise ValueError(f"Relation {self.id!r} contains duplicate endpoint signatures")
        return self


class CanonicalOntology(ContractModel):
    schema_version: str = Field(alias="schemaVersion")
    ontology_id: Identifier = Field(alias="ontologyId")
    ontology_version: str = Field(alias="ontologyVersion")
    sha256: str
    node_classes: tuple[CanonicalNodeClass, ...] = Field(alias="nodeClasses", min_length=1)
    relation_classes: tuple[CanonicalRelationClass, ...] = Field(
        alias="relationClasses", min_length=1
    )

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> CanonicalOntology:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.ontology_version, "ontologyVersion")
        _require_identifier(self.ontology_id, "ontologyId")
        _require_sha256(self.sha256, "sha256")
        node_ids = [item.id for item in self.node_classes]
        relation_ids = [item.id for item in self.relation_classes]
        _require_unique(node_ids, "canonical node class")
        _require_unique(relation_ids, "canonical relation class")
        known_nodes = set(node_ids)
        for relation in self.relation_classes:
            _require_identifier(relation.id, "canonical relation class")
            for signature in relation.legal_endpoints:
                missing = {
                    signature.source_class,
                    signature.target_class,
                } - known_nodes
                if missing:
                    raise ValueError(
                        f"Relation {relation.id!r} refers to unknown canonical node class: "
                        + ", ".join(sorted(missing))
                    )
        for node in self.node_classes:
            _require_identifier(node.id, "canonical node class")
        return self

    @property
    def nodes_by_id(self) -> dict[str, CanonicalNodeClass]:
        return {item.id: item for item in self.node_classes}

    @property
    def relations_by_id(self) -> dict[str, CanonicalRelationClass]:
        return {item.id: item for item in self.relation_classes}


class OntologyPin(ContractModel):
    ontology_id: Identifier = Field(alias="ontologyId")
    ontology_version: str = Field(alias="ontologyVersion")
    ontology_sha256: str = Field(alias="ontologySha256")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class NodeKindMapping(ContractModel):
    raw_kind: str = Field(alias="rawKind", min_length=1)
    canonical_class: Identifier = Field(alias="canonicalClass")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SupportedVersionRange(ContractModel):
    minimum_version: str = Field(alias="minimumVersion")
    maximum_exclusive: str = Field(alias="maximumExclusive")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def validate_range(self) -> SupportedVersionRange:
        _require_semver(self.minimum_version, "minimumVersion")
        _require_semver(self.maximum_exclusive, "maximumExclusive")
        if _semver_tuple(self.minimum_version) >= _semver_tuple(self.maximum_exclusive):
            raise ValueError("Supported version range must be non-empty")
        return self

    def supports(self, version: str) -> bool:
        candidate = _semver_tuple(version)
        return _semver_tuple(self.minimum_version) <= candidate < _semver_tuple(
            self.maximum_exclusive
        )


class SourceSelector(ContractModel):
    source_type: Identifier = Field(alias="sourceType")
    contract_schema: SupportedVersionRange = Field(alias="contractSchema")
    graph_schema: SupportedVersionRange = Field(alias="graphSchema")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RelationMapping(ContractModel):
    raw_relation: str = Field(alias="rawRelation", min_length=1)
    canonical_relation: Identifier = Field(alias="canonicalRelation")
    legal_endpoints: tuple[EndpointSignature, ...] = Field(alias="legalEndpoints", min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def reject_duplicate_signatures(self) -> RelationMapping:
        pairs = [(item.source_class, item.target_class) for item in self.legal_endpoints]
        if len(pairs) != len(set(pairs)):
            raise ValueError(f"Relation mapping {self.raw_relation!r} has duplicate signatures")
        return self


class SourceGraphProfile(ContractModel):
    schema_version: str = Field(alias="schemaVersion")
    profile_id: Identifier = Field(alias="profileId")
    profile_version: str = Field(alias="profileVersion")
    sha256: str
    ontology: OntologyPin
    source_selector: SourceSelector = Field(alias="sourceSelector")
    node_mappings: tuple[NodeKindMapping, ...] = Field(alias="nodeMappings", min_length=1)
    relation_mappings: tuple[RelationMapping, ...] = Field(alias="relationMappings", min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def validate_identity_and_ambiguity(self) -> SourceGraphProfile:
        _require_semver(self.schema_version, "schemaVersion")
        _require_semver(self.profile_version, "profileVersion")
        _require_semver(self.ontology.ontology_version, "ontologyVersion")
        _require_identifier(self.profile_id, "profileId")
        _require_identifier(self.source_selector.source_type, "sourceType")
        _require_sha256(self.sha256, "sha256")
        _require_sha256(self.ontology.ontology_sha256, "ontologySha256")
        _require_unique([item.raw_kind for item in self.node_mappings], "raw node-kind mapping")
        _require_unique(
            [item.raw_relation for item in self.relation_mappings],
            "raw relation mapping",
        )
        return self

    @property
    def node_mapping(self) -> dict[str, str]:
        return {item.raw_kind: item.canonical_class for item in self.node_mappings}

    @property
    def relation_mapping(self) -> dict[str, str]:
        return {item.raw_relation: item.canonical_relation for item in self.relation_mappings}

    @property
    def relation_definitions(self) -> dict[str, RelationMapping]:
        return {item.raw_relation: item for item in self.relation_mappings}


class NormalizationGap(ContractModel):
    code: str
    entity_id: str | None = None
    raw_term: str | None = None
    materiality: Materiality | None = None
    blocking: bool


class NormalizedNode(ContractModel):
    node_id: str
    raw_kind: str
    canonical_class: str
    role: NodeRole
    materiality: Materiality
    label: str
    source: str | None = None
    raw_evidence_state: str | None = None
    evidence_state: SourceEvidenceState = SourceEvidenceState.UNVERIFIED
    source_snapshot: str | None = None
    source_hash: str | None = None
    extractor_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class NormalizedEdge(ContractModel):
    edge_id: str
    source_id: str
    target_id: str
    raw_relation: str
    canonical_relation: str
    materiality: Materiality
    source: str | None = None
    raw_evidence_state: str | None = None
    evidence_state: SourceEvidenceState = SourceEvidenceState.UNVERIFIED
    source_snapshot: str | None = None
    source_hash: str | None = None
    extractor_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class NormalizedGraph(ContractModel):
    project_id: str | None = None
    source_graph_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    ontology_id: str
    ontology_version: str
    ontology_sha256: str
    profile_id: str
    profile_version: str
    profile_sha256: str
    source_snapshot: str | None = None
    nodes: tuple[NormalizedNode, ...]
    edges: tuple[NormalizedEdge, ...]
    mapping_gaps: tuple[NormalizationGap, ...]
    trust_gaps: tuple[NormalizationGap, ...]
    graph_sha256: str


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must use semantic versioning")


def _semver_tuple(value: str) -> tuple[int, int, int]:
    _require_semver(value, "version")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _require_identifier(value: str, field: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase stable identifier")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"Ambiguous duplicate {label}: {', '.join(duplicates)}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OntologyContractError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _read_contract(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OntologyContractError(f"Required contract is missing: {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except OntologyContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OntologyContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(value, dict):
        raise OntologyContractError(f"Expected a JSON object in {path.name}")
    return value


def contract_sha256(document: dict[str, Any]) -> str:
    """Hash a contract canonically, excluding only its self-referential digest field."""

    payload = {key: value for key, value in document.items() if key != "sha256"}
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_canonical_ontology(path: Path) -> CanonicalOntology:
    document = _read_contract(path)
    declared = document.get("sha256")
    actual = contract_sha256(document)
    if declared != actual:
        raise OntologyContractError("Canonical ontology digest does not match its content")
    try:
        return CanonicalOntology.model_validate(document)
    except ValidationError as exc:
        raise OntologyContractError(f"Invalid canonical ontology: {exc}") from exc


def load_source_graph_profile(
    path: Path,
    ontology: CanonicalOntology,
    *,
    expected_sha256: str | None = None,
) -> SourceGraphProfile:
    document = _read_contract(path)
    declared = document.get("sha256")
    actual = contract_sha256(document)
    if declared != actual:
        raise OntologyContractError("Source graph profile digest does not match its content")
    if expected_sha256 is not None and actual != expected_sha256.casefold():
        raise OntologyContractError("Source graph profile does not match its trusted digest")
    try:
        profile = SourceGraphProfile.model_validate(document)
    except ValidationError as exc:
        raise OntologyContractError(f"Invalid source graph profile: {exc}") from exc
    pin = profile.ontology
    expected = (ontology.ontology_id, ontology.ontology_version, ontology.sha256)
    actual_pin = (pin.ontology_id, pin.ontology_version, pin.ontology_sha256)
    if actual_pin != expected:
        raise OntologyContractError("Source graph profile does not pin the loaded ontology")
    unknown_nodes = sorted(
        {
            item.canonical_class
            for item in profile.node_mappings
            if item.canonical_class not in ontology.nodes_by_id
        }
    )
    unknown_relations = sorted(
        {
            item.canonical_relation
            for item in profile.relation_mappings
            if item.canonical_relation not in ontology.relations_by_id
        }
    )
    if unknown_nodes or unknown_relations:
        refs = unknown_nodes + unknown_relations
        raise OntologyContractError(
            "Source graph profile refers to unknown canonical class: " + ", ".join(refs)
        )
    for mapping in profile.relation_mappings:
        canonical = ontology.relations_by_id[mapping.canonical_relation]
        canonical_signatures = {
            (item.source_class, item.target_class) for item in canonical.legal_endpoints
        }
        source_signatures = {
            (item.source_class, item.target_class) for item in mapping.legal_endpoints
        }
        if not source_signatures <= canonical_signatures:
            raise OntologyContractError(
                f"Source relation {mapping.raw_relation!r} permits an endpoint signature "
                "outside its canonical relation"
            )
    return profile


_NODE_CORE_FIELDS = {
    "id",
    "kind",
    "label",
    "source",
    "evidenceState",
    "sourceSnapshot",
    "sourceHash",
    "extractorId",
}
_EDGE_CORE_FIELDS = {
    "id",
    "from",
    "relation",
    "to",
    "source",
    "evidenceState",
    "sourceSnapshot",
    "sourceHash",
    "extractorId",
}


def _copy_attributes(item: dict[str, Any], core_fields: set[str]) -> dict[str, Any]:
    return {key: item[key] for key in sorted(item) if key not in core_fields}


def _trust_fields(
    item: dict[str, Any], entity_id: str, materiality: Materiality
) -> tuple[dict[str, Any], list[NormalizationGap]]:
    gaps: list[NormalizationGap] = []
    raw_state = item.get("evidenceState")
    trusted_state = SourceEvidenceState.UNVERIFIED
    if raw_state == "HUMAN_CONFIRMED":
        gaps.append(
            NormalizationGap(
                code="UNVERIFIED_HUMAN_CONFIRMATION",
                entity_id=entity_id,
                raw_term=raw_state,
                materiality=materiality,
                blocking=materiality is Materiality.MATERIAL,
            )
        )
    elif raw_state is not None:
        try:
            trusted_state = SourceEvidenceState(str(raw_state))
            if trusted_state is not SourceEvidenceState.CONFIRMED:
                gaps.append(
                    NormalizationGap(
                        code=f"EVIDENCE_STATE_{trusted_state}",
                        entity_id=entity_id,
                        raw_term=str(raw_state),
                        materiality=materiality,
                        blocking=materiality is Materiality.MATERIAL,
                    )
                )
        except ValueError:
            gaps.append(
                NormalizationGap(
                    code="UNKNOWN_EVIDENCE_STATE",
                    entity_id=entity_id,
                    raw_term=str(raw_state),
                    materiality=materiality,
                    blocking=materiality is Materiality.MATERIAL,
                )
            )
    else:
        gaps.append(
            NormalizationGap(
                code="MISSING_EVIDENCE_STATE",
                entity_id=entity_id,
                materiality=materiality,
                blocking=materiality is Materiality.MATERIAL,
            )
        )
    missing_provenance = False
    for field, code in (
        ("source", "MISSING_SOURCE_ARTIFACT"),
        ("sourceSnapshot", "MISSING_SOURCE_SNAPSHOT"),
        ("sourceHash", "MISSING_SOURCE_HASH"),
        ("extractorId", "MISSING_EXTRACTOR_ID"),
    ):
        if not isinstance(item.get(field), str) or not item[field]:
            missing_provenance = True
            gaps.append(
                NormalizationGap(
                    code=code,
                    entity_id=entity_id,
                    materiality=materiality,
                    blocking=materiality is Materiality.MATERIAL,
                )
            )
    source_hash = item.get("sourceHash")
    if source_hash is not None and (
        not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash)
    ):
        missing_provenance = True
        gaps.append(
            NormalizationGap(
                code="INVALID_SOURCE_HASH",
                entity_id=entity_id,
                raw_term=str(source_hash),
                materiality=materiality,
                blocking=materiality is Materiality.MATERIAL,
            )
        )
        source_hash = None
    if trusted_state is SourceEvidenceState.CONFIRMED and missing_provenance:
        trusted_state = SourceEvidenceState.UNVERIFIED
    return (
        {
            "raw_evidence_state": str(raw_state) if raw_state is not None else None,
            "evidence_state": trusted_state,
            "source_snapshot": item.get("sourceSnapshot"),
            "source_hash": source_hash,
            "extractor_id": item.get("extractorId"),
        },
        gaps,
    )


def normalize_source_graph(
    raw_graph: dict[str, Any],
    ontology: CanonicalOntology,
    profile: SourceGraphProfile,
    *,
    project_id: str | None = None,
    source_graph_sha256: str | None = None,
) -> NormalizedGraph:
    """Normalize source vocabulary without manufacturing provenance or trust state."""

    raw_nodes = raw_graph.get("nodes")
    raw_edges = raw_graph.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise OntologyNormalizationError("Source graph nodes and edges must be arrays")
    if any(not isinstance(item, dict) for item in [*raw_nodes, *raw_edges]):
        raise OntologyNormalizationError("Source graph nodes and edges must be objects")

    node_ids = [item.get("id") for item in raw_nodes]
    if any(not isinstance(item, str) or not item for item in node_ids):
        raise OntologyNormalizationError("Every source node must have a non-empty string ID")
    if len(node_ids) != len(set(node_ids)):
        raise OntologyNormalizationError("Source node IDs must be unique")
    edge_ids = [item.get("id") for item in raw_edges]
    if any(not isinstance(item, str) or not item for item in edge_ids):
        raise OntologyNormalizationError("Every source edge must have a non-empty string ID")
    if len(edge_ids) != len(set(edge_ids)):
        raise OntologyNormalizationError("Source edge IDs must be unique")

    node_map = profile.node_mapping
    relation_map = profile.relation_mapping
    relation_definitions = profile.relation_definitions
    canonical_nodes = ontology.nodes_by_id
    canonical_relations = ontology.relations_by_id
    normalized_nodes: list[NormalizedNode] = []
    normalized_edges: list[NormalizedEdge] = []
    mapping_gaps: list[NormalizationGap] = []
    trust_gaps: list[NormalizationGap] = []
    normalized_by_source_id: dict[str, NormalizedNode] = {}

    for item in sorted(raw_nodes, key=lambda row: str(row["id"])):
        node_id = str(item["id"])
        raw_kind = item.get("kind")
        canonical_id = node_map.get(raw_kind) if isinstance(raw_kind, str) else None
        if canonical_id is None:
            mapping_gaps.append(
                NormalizationGap(
                    code="UNMAPPED_NODE_KIND",
                    entity_id=node_id,
                    raw_term=str(raw_kind) if raw_kind is not None else None,
                    blocking=True,
                )
            )
            continue
        node_class = canonical_nodes[canonical_id]
        trust, node_trust_gaps = _trust_fields(item, node_id, node_class.materiality)
        node = NormalizedNode(
            node_id=node_id,
            raw_kind=raw_kind,
            canonical_class=canonical_id,
            role=node_class.role,
            materiality=node_class.materiality,
            label=str(item.get("label", node_id)),
            source=str(item["source"]) if item.get("source") is not None else None,
            attributes=_copy_attributes(item, _NODE_CORE_FIELDS),
            **trust,
        )
        normalized_nodes.append(node)
        normalized_by_source_id[node_id] = node
        trust_gaps.extend(node_trust_gaps)

    for item in sorted(raw_edges, key=lambda row: str(row["id"])):
        edge_id = str(item["id"])
        raw_relation = item.get("relation")
        canonical_id = relation_map.get(raw_relation) if isinstance(raw_relation, str) else None
        if canonical_id is None:
            source_node = normalized_by_source_id.get(item.get("from"))
            target_node = normalized_by_source_id.get(item.get("to"))
            material = bool(
                source_node
                and target_node
                and (
                    source_node.materiality is Materiality.MATERIAL
                    or target_node.materiality is Materiality.MATERIAL
                )
            )
            mapping_gaps.append(
                NormalizationGap(
                    code="UNMAPPED_RELATION",
                    entity_id=edge_id,
                    raw_term=str(raw_relation) if raw_relation is not None else None,
                    materiality=(Materiality.MATERIAL if material else Materiality.SUPPORTING),
                    blocking=material,
                )
            )
            continue
        source_id = item.get("from")
        target_id = item.get("to")
        source_node = normalized_by_source_id.get(source_id)
        target_node = normalized_by_source_id.get(target_id)
        relation = canonical_relations[canonical_id]
        if source_node is None or target_node is None:
            mapping_gaps.append(
                NormalizationGap(
                    code="UNMAPPED_EDGE_ENDPOINT",
                    entity_id=edge_id,
                    raw_term=raw_relation,
                    materiality=relation.materiality,
                    blocking=relation.materiality is Materiality.MATERIAL,
                )
            )
            continue
        signature = (source_node.canonical_class, target_node.canonical_class)
        legal = {
            (entry.source_class, entry.target_class)
            for entry in relation_definitions[raw_relation].legal_endpoints
        }
        if signature not in legal:
            mapping_gaps.append(
                NormalizationGap(
                    code="ILLEGAL_ENDPOINT_SIGNATURE",
                    entity_id=edge_id,
                    raw_term=f"{signature[0]}|{canonical_id}|{signature[1]}",
                    materiality=relation.materiality,
                    blocking=relation.materiality is Materiality.MATERIAL,
                )
            )
            continue
        trust, edge_trust_gaps = _trust_fields(item, edge_id, relation.materiality)
        normalized_edges.append(
            NormalizedEdge(
                edge_id=edge_id,
                source_id=str(source_id),
                target_id=str(target_id),
                raw_relation=raw_relation,
                canonical_relation=canonical_id,
                materiality=relation.materiality,
                source=str(item["source"]) if item.get("source") is not None else None,
                attributes=_copy_attributes(item, _EDGE_CORE_FIELDS),
                **trust,
            )
        )
        trust_gaps.extend(edge_trust_gaps)

    mapping_gaps.sort(key=lambda gap: (gap.entity_id or "", gap.code, gap.raw_term or ""))
    trust_gaps.sort(key=lambda gap: (gap.entity_id or "", gap.code, gap.raw_term or ""))
    body = {
        "project_id": project_id,
        "source_graph_sha256": source_graph_sha256,
        "ontology_id": ontology.ontology_id,
        "ontology_version": ontology.ontology_version,
        "ontology_sha256": ontology.sha256,
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_sha256": profile.sha256,
        "source_snapshot": raw_graph.get("sourceSnapshot"),
        "nodes": [item.model_dump(mode="json") for item in normalized_nodes],
        "edges": [item.model_dump(mode="json") for item in normalized_edges],
        "mapping_gaps": [item.model_dump(mode="json") for item in mapping_gaps],
        "trust_gaps": [item.model_dump(mode="json") for item in trust_gaps],
    }
    graph_sha256 = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return NormalizedGraph.model_validate({**body, "graph_sha256": graph_sha256})
