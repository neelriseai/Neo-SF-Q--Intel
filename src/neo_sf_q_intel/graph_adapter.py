from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class SemanticFileStatus(StrEnum):
    PARSED = "PARSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class AdapterFileResult:
    path: str
    status: SemanticFileStatus
    parser_id: str
    node_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterNode:
    node_id: str
    raw_kind: str
    label: str
    owner_paths: tuple[str, ...]
    attributes: Mapping[str, Any]
    evidence_state: str = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class AdapterEdge:
    edge_id: str
    source_id: str
    raw_relation: str
    target_id: str
    owner_paths: tuple[str, ...]
    attributes: Mapping[str, Any]
    evidence_state: str = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class AdapterGraph:
    files: tuple[AdapterFileResult, ...]
    nodes: tuple[AdapterNode, ...]
    edges: tuple[AdapterEdge, ...]


class GraphSemanticAdapter(Protocol):
    implementation_id: str
    implementation_version: str

    def extract(
        self,
        files: Mapping[str, bytes],
        *,
        maximum_nodes: int,
        maximum_edges: int,
        maximum_work_units: int,
        maximum_path_bytes: int = 1024,
    ) -> AdapterGraph: ...
