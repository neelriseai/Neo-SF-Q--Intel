from __future__ import annotations

import json
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

    @property
    def snapshot_id(self) -> str:
        return str(self.graph["sourceSnapshot"])

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return list(self.graph["nodes"])

    @property
    def edges(self) -> list[dict[str, Any]]:
        return list(self.graph["edges"])


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


def load_salesforce_source(root: Path) -> SalesforceSourceSnapshot:
    resolved = root.resolve()
    contract = _read_json(resolved / "contracts" / "agent-interface.json")
    graph = _read_json(resolved / "knowledge" / "application-graph.json")
    project_index = _read_json(resolved / "knowledge" / "project-index.json")

    if contract.get("schemaVersion") != "1.4.0":
        raise SourceContractError("Salesforce agent contract schema 1.4.0 is required")
    if contract.get("apiVersion") != graph.get("apiVersion"):
        raise SourceContractError("Contract and graph API versions disagree")
    if graph.get("sourceSnapshot") != project_index.get("sourceSnapshot"):
        raise SourceContractError("Graph and project-index snapshots disagree")
    capabilities = {row.get("id"): row for row in contract.get("capabilities", [])}
    if capabilities.get("api.custom.rest", {}).get("status") != "implemented":
        raise SourceContractError("Required api.custom.rest capability is unavailable")
    return SalesforceSourceSnapshot(resolved, contract, graph, project_index)


def node_to_evidence(node: dict[str, Any], snapshot_id: str) -> EvidenceRef:
    node_id = str(node["id"])
    return EvidenceRef(
        evidence_id=f"graph:{snapshot_id}:{node_id}",
        kind=str(node.get("kind", "unknown")),
        label=str(node.get("label", node_id)),
        source=str(node.get("source", "knowledge/application-graph.json")),
        state=EvidenceState.CONFIRMED,
        attributes={"entity_id": node_id, "snapshot_id": snapshot_id},
    )
