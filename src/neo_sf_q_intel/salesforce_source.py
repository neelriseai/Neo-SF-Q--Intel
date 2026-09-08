from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neo_sf_q_intel.domain import EvidenceRef, EvidenceState


class SourceContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class SalesforceSourceSnapshot:
    root: Path
    contract: dict[str, Any]
    graph: dict[str, Any]
    project_index: dict[str, Any]
    trusted_graph_sha256: str | None = None

    @property
    def snapshot_id(self) -> str:
        return str(self.graph["sourceSnapshot"])

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return list(self.graph["nodes"])

    @property
    def edges(self) -> list[dict[str, Any]]:
        return list(self.graph["edges"])

    @property
    def project_id(self) -> str:
        candidate = str(
            self.contract.get("projectId")
            or self.contract.get("application")
            or self.graph.get("application")
            or ""
        )
        normalized = re.sub(r"[^a-z0-9]+", "-", candidate.casefold()).strip("-")
        if not normalized:
            raise SourceContractError("Source contract must identify its project")
        return normalized


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SourceContractError(f"Required source artifact is missing: {path.name}")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(body, dict):
        raise SourceContractError(f"Expected an object in {path.name}")
    return body


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
    expected_graph_sha256: str,
    minimum_contract_version: str = "1.0.0",
    required_capabilities: tuple[str, ...] = (),
) -> SalesforceSourceSnapshot:
    resolved = root.resolve()
    contract = _read_json(resolved / "contracts" / "agent-interface.json")
    graph = _read_json(resolved / "knowledge" / "application-graph.json")
    project_index = _read_json(resolved / "knowledge" / "project-index.json")
    _validate_graph(graph)
    _validate_source_freshness(resolved, project_index)
    graph_path = resolved / "knowledge" / "application-graph.json"
    if _normalized_text_hash(graph_path) != expected_graph_sha256.casefold():
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
    return SalesforceSourceSnapshot(
        resolved,
        contract,
        graph,
        project_index,
        trusted_graph_sha256=expected_graph_sha256.casefold(),
    )


def node_to_evidence(
    node: dict[str, Any], snapshot_id: str, trusted_graph_sha256: str | None
) -> EvidenceRef:
    node_id = str(node["id"])
    return EvidenceRef(
        evidence_id=f"graph:{snapshot_id}:{node_id}",
        kind=str(node.get("kind", "unknown")),
        label=str(node.get("label", node_id)),
        source=str(node.get("source", "knowledge/application-graph.json")),
        state=(EvidenceState.CONFIRMED if trusted_graph_sha256 else EvidenceState.STALE),
        attributes={
            "entity_id": node_id,
            "snapshot_id": snapshot_id,
            "source_hash": trusted_graph_sha256,
        },
    )
