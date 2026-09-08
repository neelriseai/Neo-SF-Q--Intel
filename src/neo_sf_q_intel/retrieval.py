from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from neo_sf_q_intel.domain import EvidenceRef
from neo_sf_q_intel.salesforce_source import (
    SalesforceSourceSnapshot,
    node_to_evidence,
)

TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")


def tokenize(text: str) -> set[str]:
    return {token.casefold() for token in TOKEN_PATTERN.findall(text)}


@dataclass(frozen=True)
class RetrievalHit:
    node: dict[str, Any]
    score: float
    reasons: tuple[str, ...]


class EvidenceRetriever:
    def __init__(self, source: SalesforceSourceSnapshot) -> None:
        self.source = source
        self.nodes = {str(node["id"]): node for node in source.nodes}
        self.outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in source.edges:
            self.outgoing[str(edge["from"])].append(edge)
            self.incoming[str(edge["to"])].append(edge)

    def search(self, query: str, changed_paths: list[str], limit: int = 8) -> list[RetrievalHit]:
        query_tokens = tokenize(query)
        normalized_paths = {path.replace("\\", "/").casefold() for path in changed_paths}
        hits: list[RetrievalHit] = []
        for node in self.nodes.values():
            text = " ".join(str(node.get(key, "")) for key in ("id", "label", "kind", "source"))
            node_tokens = tokenize(text)
            overlap = len(query_tokens & node_tokens)
            source = str(node.get("source", "")).replace("\\", "/").casefold()
            path_match = bool(source) and any(
                source.endswith(path) or path.endswith(source) for path in normalized_paths
            )
            if not overlap and not path_match:
                continue
            score = overlap / max(1, len(query_tokens))
            reasons: list[str] = []
            if overlap:
                reasons.append(f"{overlap} lexical tokens")
            if path_match:
                score += 2.0
                reasons.append("changed source path")
            hits.append(RetrievalHit(node=node, score=score, reasons=tuple(reasons)))
        return sorted(hits, key=lambda hit: (-hit.score, str(hit.node["id"])))[:limit]

    def expand(
        self, seed_ids: list[str], depth: int = 2, limit: int = 100
    ) -> list[tuple[dict[str, Any], str, str]]:
        queue = deque((seed_id, 0) for seed_id in seed_ids)
        seen = set(seed_ids)
        expanded: list[tuple[dict[str, Any], str, str]] = []
        while queue and len(expanded) < limit:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            adjacent = [(edge, str(edge["to"]), "outgoing") for edge in self.outgoing[current]] + [
                (edge, str(edge["from"]), "incoming") for edge in self.incoming[current]
            ]
            for edge, neighbor_id, direction in adjacent:
                if neighbor_id in seen or neighbor_id not in self.nodes:
                    continue
                seen.add(neighbor_id)
                expanded.append((self.nodes[neighbor_id], str(edge["relation"]), direction))
                queue.append((neighbor_id, current_depth + 1))
                if len(expanded) >= limit:
                    break
        return expanded

    def evidence_for(self, node: dict[str, Any]) -> EvidenceRef:
        return node_to_evidence(node, self.source.snapshot_id)
