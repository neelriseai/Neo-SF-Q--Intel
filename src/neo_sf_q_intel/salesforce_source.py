from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any

from neo_sf_q_intel.domain import EvidenceRef, EvidenceState
from neo_sf_q_intel.ontology import (
    CanonicalOntology,
    NormalizedGraph,
    OntologyContractError,
    OntologyNormalizationError,
    SourceGraphProfile,
    load_canonical_ontology,
    load_source_graph_profile,
    normalize_source_graph,
)


def canonical_project_id(value: object) -> str:
    """Return the single project identity form used by every source adapter."""

    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    if not normalized:
        raise SourceContractError("Source contract must identify its project")
    return normalized


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ONTOLOGY_PATH = REPOSITORY_ROOT / "config" / "ontology" / "canonical-ontology.json"
DEFAULT_SOURCE_PROFILE_PATH = (
    REPOSITORY_ROOT / "config" / "source-profiles" / "salesforce-application-graph.json"
)
DEFAULT_SOURCE_PROFILE_SHA256 = "20f062050584fa4259485df0f5dc00c3ef186562495583f6c846cd9adaa6f7ae"
SOURCE_ADAPTER_TYPE = "salesforce-application-graph"


class SourceContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class SalesforceSourceSnapshot:
    root: Path
    contract: dict[str, Any]
    graph: dict[str, Any]
    project_index: dict[str, Any]
    trusted_graph_sha256: str | None = None
    ontology: CanonicalOntology | None = None
    source_profile: SourceGraphProfile | None = None
    normalization_project_id: str | None = None
    trust_valid_until: str | None = None

    def __post_init__(self) -> None:
        if self.trust_valid_until is not None:
            try:
                valid_until = datetime.fromisoformat(self.trust_valid_until.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SourceContractError("Source trust expiry is invalid") from exc
            if valid_until.tzinfo is None:
                raise SourceContractError("Source trust expiry must include a timezone")
        ontology = self.ontology or load_canonical_ontology(DEFAULT_ONTOLOGY_PATH)
        profile = self.source_profile or load_source_graph_profile(
            DEFAULT_SOURCE_PROFILE_PATH, ontology
        )
        object.__setattr__(self, "ontology", ontology)
        object.__setattr__(self, "source_profile", profile)

    @cached_property
    def normalized_graph(self) -> NormalizedGraph:
        assert self.ontology is not None
        assert self.source_profile is not None
        return normalize_source_graph(
            self.graph,
            self.ontology,
            self.source_profile,
            project_id=self.normalization_project_id or self.project_id,
            source_graph_sha256=self.trusted_graph_sha256,
        )

    @cached_property
    def snapshot_id(self) -> str:
        snapshot = self.graph.get("sourceSnapshot")
        if not snapshot:
            raise SourceContractError("Source graph must identify its snapshot")
        return str(snapshot)

    @property
    def nodes(self) -> list[dict[str, Any]]:
        normalized = self.normalized_graph
        gaps_by_id = _gap_codes_by_entity(normalized.trust_gaps)
        identity = _ontology_identity(normalized)
        return [
            {
                **node.attributes,
                "id": node.node_id,
                "kind": node.canonical_class,
                "sourceKind": node.raw_kind,
                "label": node.label,
                "source": node.source,
                "evidenceState": node.evidence_state,
                "sourceSnapshot": node.source_snapshot,
                "sourceHash": node.source_hash,
                **(
                    {"validUntil": self.trust_valid_until}
                    if self.trust_valid_until is not None
                    else {}
                ),
                "extractorId": node.extractor_id,
                "ontologyRole": node.role,
                "ontologyMateriality": node.materiality,
                "ontologyTrustGaps": gaps_by_id.get(node.node_id, []),
                **identity,
            }
            for node in normalized.nodes
        ]

    @property
    def edges(self) -> list[dict[str, Any]]:
        normalized = self.normalized_graph
        gaps_by_id = _gap_codes_by_entity(normalized.trust_gaps)
        identity = _ontology_identity(normalized)
        return [
            {
                **edge.attributes,
                "id": edge.edge_id,
                "from": edge.source_id,
                "relation": edge.canonical_relation,
                "sourceRelation": edge.raw_relation,
                "to": edge.target_id,
                "source": edge.source,
                "evidenceState": edge.evidence_state,
                "sourceSnapshot": edge.source_snapshot,
                "sourceHash": edge.source_hash,
                **(
                    {"validUntil": self.trust_valid_until}
                    if self.trust_valid_until is not None
                    else {}
                ),
                "extractorId": edge.extractor_id,
                "ontologyMateriality": edge.materiality,
                "ontologyTrustGaps": gaps_by_id.get(edge.edge_id, []),
                **identity,
            }
            for edge in normalized.edges
        ]

    @property
    def ontology_identity(self) -> dict[str, str]:
        return _ontology_identity(self.normalized_graph)

    @cached_property
    def project_id(self) -> str:
        candidate = str(
            self.contract.get("projectId")
            or self.contract.get("application")
            or self.graph.get("application")
            or ""
        )
        return canonical_project_id(candidate)


def _reject_duplicate_source_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceContractError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SourceContractError(f"Required source artifact is missing: {path.name}")
    try:
        body = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_source_keys
        )
    except SourceContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(body, dict):
        raise SourceContractError(f"Expected an object in {path.name}")
    return body


def _gap_codes_by_entity(gaps: tuple[Any, ...]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for gap in gaps:
        if gap.entity_id:
            result.setdefault(gap.entity_id, []).append(gap.code)
    return {key: sorted(set(value)) for key, value in result.items()}


def _ontology_identity(normalized: NormalizedGraph) -> dict[str, str]:
    return {
        "ontologyId": normalized.ontology_id,
        "ontologyVersion": normalized.ontology_version,
        "ontologySha256": normalized.ontology_sha256,
        "sourceProfileId": normalized.profile_id,
        "sourceProfileVersion": normalized.profile_version,
        "sourceProfileSha256": normalized.profile_sha256,
        "normalizedGraphSha256": normalized.graph_sha256,
    }


def _version_tuple(version: object) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in str(version).split("."))
    except ValueError as exc:
        raise SourceContractError("Contract schema version must use semantic versioning") from exc
    if len(parts) != 3:
        raise SourceContractError("Contract schema version must contain major, minor and patch")
    return parts


def _validate_graph(graph: dict[str, Any]) -> None:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise SourceContractError("Graph nodes and edges must be arrays")
    identifiers = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(identifiers) != len(nodes) or any(not item for item in identifiers):
        raise SourceContractError("Every graph node must contain an ID")
    if len(identifiers) != len(set(identifiers)):
        raise SourceContractError("Graph node IDs must be unique")
    known = set(identifiers)
    for edge in edges:
        if not isinstance(edge, dict) or not {"from", "relation", "to"} <= edge.keys():
            raise SourceContractError("Every graph edge must contain from, relation and to")
        if edge["from"] not in known or edge["to"] not in known:
            raise SourceContractError("Graph edge refers to an unknown node")


def _normalized_text_hash(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise SourceContractError(f"Cannot verify indexed source file {path.name}") from exc
    return hashlib.sha256(body.encode()).hexdigest()


def _generated_graph_sha256(project_index: dict[str, Any]) -> str:
    value = project_index.get("applicationGraphSha256")
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise SourceContractError(
            "Project index must bind the generated application graph with a SHA-256 digest"
        )
    return value


def _validate_source_freshness(root: Path, project_index: dict[str, Any]) -> None:
    inventory = project_index.get("files")
    if not isinstance(inventory, list) or not inventory:
        raise SourceContractError("Project index must contain a non-empty source inventory")

    path_base = project_index.get("pathBase")
    if path_base == "repository-root":
        base = root.parent.resolve()
    elif path_base == "project-root":
        base = root.resolve()
    else:
        raise SourceContractError("Project index pathBase must be repository-root or project-root")

    normalized_inventory: list[dict[str, str]] = []
    for item in inventory:
        if not isinstance(item, dict):
            raise SourceContractError("Project index inventory entries must be objects")
        relative = item.get("path")
        expected_hash = item.get("sha256NormalizedLf")
        category = item.get("category")
        if not all(
            isinstance(value, str) and value for value in (relative, category, expected_hash)
        ):
            raise SourceContractError("Project index inventory entries are incomplete")
        candidate = (base / relative).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise SourceContractError(
                "Project index contains a path outside its repository"
            ) from exc
        if _normalized_text_hash(candidate) != expected_hash:
            raise SourceContractError(f"Indexed source file is stale: {relative}")
        normalized_inventory.append(
            {
                "path": relative,
                "category": category,
                "sha256NormalizedLf": expected_hash,
            }
        )

    encoded = json.dumps(normalized_inventory, ensure_ascii=False, separators=(",", ":")).encode()
    actual_snapshot = hashlib.sha256(encoded).hexdigest()
    if actual_snapshot != project_index.get("sourceSnapshot"):
        raise SourceContractError("Project index source snapshot is invalid")


def load_salesforce_source(
    root: Path,
    *,
    expected_graph_sha256: str | None = None,
    minimum_contract_version: str = "1.0.0",
    required_capabilities: tuple[str, ...] = (),
    ontology_path: Path | None = None,
    source_profile_path: Path | None = None,
    expected_source_profile_sha256: str = DEFAULT_SOURCE_PROFILE_SHA256,
) -> SalesforceSourceSnapshot:
    resolved = root.resolve()
    contract = _read_json(resolved / "contracts" / "agent-interface.json")
    graph = _read_json(resolved / "knowledge" / "application-graph.json")
    project_index = _read_json(resolved / "knowledge" / "project-index.json")
    _validate_graph(graph)
    _validate_source_freshness(resolved, project_index)
    graph_path = resolved / "knowledge" / "application-graph.json"
    generated_graph_sha256 = _generated_graph_sha256(project_index)
    if _normalized_text_hash(graph_path) != generated_graph_sha256:
        raise SourceContractError(
            "Application graph does not match its generated project-index binding"
        )
    if (
        expected_graph_sha256 is not None
        and generated_graph_sha256 != expected_graph_sha256.casefold()
    ):
        raise SourceContractError("Application graph does not match its trusted digest")

    actual_version = _version_tuple(contract.get("schemaVersion"))
    minimum_version = _version_tuple(minimum_contract_version)
    if actual_version[0] != minimum_version[0] or actual_version < minimum_version:
        raise SourceContractError(
            f"Contract schema must be compatible with {minimum_contract_version} or newer"
        )
    if contract.get("apiVersion") != graph.get("apiVersion"):
        raise SourceContractError("Contract and graph API versions disagree")
    if graph.get("sourceSnapshot") != project_index.get("sourceSnapshot"):
        raise SourceContractError("Graph and project-index snapshots disagree")
    capabilities = {row.get("id"): row for row in contract.get("capabilities", [])}
    unavailable = [
        capability
        for capability in required_capabilities
        if capabilities.get(capability, {}).get("status") != "implemented"
    ]
    if unavailable:
        raise SourceContractError(
            f"Required capabilities are unavailable: {', '.join(unavailable)}"
        )
    try:
        ontology = load_canonical_ontology(ontology_path or DEFAULT_ONTOLOGY_PATH)
        source_profile = load_source_graph_profile(
            source_profile_path or DEFAULT_SOURCE_PROFILE_PATH,
            ontology,
            expected_sha256=expected_source_profile_sha256,
        )
        selector = source_profile.source_selector
        if selector.source_type != SOURCE_ADAPTER_TYPE:
            raise SourceContractError("Source profile does not target this source adapter")
        contract_schema = str(contract.get("schemaVersion", ""))
        graph_schema = str(graph.get("schemaVersion", ""))
        try:
            contract_supported = selector.contract_schema.supports(contract_schema)
            graph_supported = selector.graph_schema.supports(graph_schema)
        except ValueError as exc:
            raise SourceContractError("Source schema version is invalid") from exc
        if not contract_supported:
            raise SourceContractError("Source contract schema is outside the profile range")
        if not graph_supported:
            raise SourceContractError("Source graph schema is outside the profile range")
        source = SalesforceSourceSnapshot(
            resolved,
            contract,
            graph,
            project_index,
            trusted_graph_sha256=generated_graph_sha256,
            ontology=ontology,
            source_profile=source_profile,
        )
        normalized = source.normalized_graph
    except (OntologyContractError, OntologyNormalizationError) as exc:
        raise SourceContractError("Source ontology/profile normalization failed") from exc
    blocking_mapping_gaps = [gap for gap in normalized.mapping_gaps if gap.blocking]
    if blocking_mapping_gaps:
        codes = ", ".join(sorted({gap.code for gap in blocking_mapping_gaps}))
        raise SourceContractError(f"Source ontology mapping is incomplete: {codes}")
    return source


def node_to_evidence(
    node: dict[str, Any], snapshot_id: str, trusted_graph_sha256: str | None
) -> EvidenceRef:
    node_id = str(node["id"])
    state = EvidenceState.UNVERIFIED
    if (
        node.get("evidenceState") == EvidenceState.CONFIRMED
        and node.get("sourceSnapshot") == snapshot_id
        and node.get("sourceHash") == trusted_graph_sha256
        and node.get("extractorId")
        and node.get("source")
    ):
        state = EvidenceState.CONFIRMED
    return EvidenceRef(
        evidence_id=f"graph:{snapshot_id}:{node_id}",
        kind=str(node.get("kind", "unknown")),
        label=str(node.get("label", node_id)),
        source=str(node.get("source") or "unverified:source-artifact-missing"),
        state=state,
        attributes={
            "entity_id": node_id,
            "snapshot_id": node.get("sourceSnapshot"),
            "source_hash": node.get("sourceHash"),
            "expected_graph_snapshot": snapshot_id,
            "expected_graph_sha256": trusted_graph_sha256,
            "extractor_id": node.get("extractorId"),
            "source_kind": node.get("sourceKind"),
            "canonical_class": node.get("kind"),
            "ontology_id": node.get("ontologyId"),
            "ontology_version": node.get("ontologyVersion"),
            "ontology_sha256": node.get("ontologySha256"),
            "source_profile_id": node.get("sourceProfileId"),
            "source_profile_version": node.get("sourceProfileVersion"),
            "source_profile_sha256": node.get("sourceProfileSha256"),
            "normalized_graph_sha256": node.get("normalizedGraphSha256"),
        },
    )
