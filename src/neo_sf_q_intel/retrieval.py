from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from neo_sf_q_intel.domain import EvidenceRef, EvidenceState
from neo_sf_q_intel.policy import ReasoningPolicy
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


@dataclass(frozen=True)
class TraversalHit:
    node: dict[str, Any]
    relation: str
    direction: str
    source_id: str
    target_id: str
    depth: int
    evidence_state: EvidenceState
    source_snapshot: str
    source_hash: str
    valid_until: str | None


@dataclass(frozen=True)
class TraversalGap:
    code: str
    relation: str
    source_id: str
    target_id: str
    neighbor_id: str


class EvidenceRetriever:
    def __init__(
        self, source: SalesforceSourceSnapshot, policy: ReasoningPolicy | None = None
    ) -> None:
        self.source = source
        self.policy = policy or ReasoningPolicy.load()
        self.nodes = {str(node["id"]): node for node in source.nodes}
        self.outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in source.edges:
            self.outgoing[str(edge["from"])].append(edge)
            self.incoming[str(edge["to"])].append(edge)

    def search(self, query: str, changed_paths: list[str], limit: int = 8) -> list[RetrievalHit]:
        query_tokens = tokenize(query) - self.policy.low_information_tokens
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
        ranked = sorted(hits, key=lambda hit: (-hit.score, str(hit.node["id"])))
        if normalized_paths:
            return ranked[:limit]
        if not query_tokens:
            return []
        if len(query_tokens) == 1:
            token = next(iter(query_tokens))
            ranked = [
                hit
                for hit in ranked
                if str(hit.node.get("label", "")).casefold() == token
                or str(hit.node.get("id", "")).casefold().endswith((f":{token}", f".{token}"))
            ]
        if not ranked:
            return []
        minimum_score = (
            self.policy.short_query_minimum_score
            if len(query_tokens) <= 2
            else self.policy.long_query_minimum_score
        )
        top_score = ranked[0].score
        tied = [hit for hit in ranked if abs(hit.score - top_score) < 1e-9]
        if top_score < minimum_score or len(tied) >= self.policy.ambiguity_tie_limit:
            return []
        cutoff = max(minimum_score, top_score * self.policy.relative_cutoff_ratio)
        return [hit for hit in ranked if hit.score >= cutoff][:limit]

    def expand(
        self,
        seed_ids: list[str],
        depth: int = 2,
        limit: int = 100,
        allowed_relations: frozenset[str] | None = None,
    ) -> tuple[list[TraversalHit], list[TraversalGap], bool]:
        queue = deque((seed_id, 0) for seed_id in seed_ids)
        seen = set(seed_ids)
        expanded: list[TraversalHit] = []
        gaps: list[TraversalGap] = []
        truncated = False
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            adjacent = [(edge, str(edge["to"]), "outgoing") for edge in self.outgoing[current]] + [
                (edge, str(edge["from"]), "incoming") for edge in self.incoming[current]
            ]
            for edge, neighbor_id, direction in adjacent:
                relation = str(edge["relation"])
                trust_gap = self._edge_trust_gap(edge)
                if trust_gap:
                    gaps.append(
                        TraversalGap(
                            code=trust_gap,
                            relation=relation,
                            source_id=str(edge["from"]),
                            target_id=str(edge["to"]),
                            neighbor_id=neighbor_id,
                        )
                    )
                    continue
                if allowed_relations is not None and relation not in allowed_relations:
                    gaps.append(
                        TraversalGap(
                            code="UNKNOWN_RELATION",
                            relation=relation,
                            source_id=str(edge["from"]),
                            target_id=str(edge["to"]),
                            neighbor_id=neighbor_id,
                        )
                    )
                    continue
                if neighbor_id in seen or neighbor_id not in self.nodes:
                    continue
                if len(expanded) >= limit:
                    truncated = True
                    break
                seen.add(neighbor_id)
                expanded.append(
                    TraversalHit(
                        node=self.nodes[neighbor_id],
                        relation=relation,
                        direction=direction,
                        source_id=str(edge["from"]),
                        target_id=str(edge["to"]),
                        depth=current_depth + 1,
                        evidence_state=EvidenceState(
                            edge.get("evidenceState", EvidenceState.CONFIRMED)
                        ),
                        source_snapshot=str(edge.get("sourceSnapshot", self.source.snapshot_id)),
                        source_hash=str(self.source.trusted_graph_sha256),
                        valid_until=edge.get("validUntil"),
                    )
                )
                queue.append((neighbor_id, current_depth + 1))
            if truncated:
                break
        unique_gaps = {(gap.relation, gap.source_id, gap.target_id): gap for gap in gaps}
        return expanded, list(unique_gaps.values()), truncated

    def evidence_for(self, node: dict[str, Any]) -> EvidenceRef:
        return node_to_evidence(node, self.source.snapshot_id, self.source.trusted_graph_sha256)

    def _edge_trust_gap(self, edge: dict[str, Any]) -> str | None:
        try:
            state = EvidenceState(edge.get("evidenceState", EvidenceState.CONFIRMED))
        except ValueError:
            return "INVALID_EDGE_STATE"
        if state not in {EvidenceState.CONFIRMED, EvidenceState.HUMAN_CONFIRMED}:
            return f"EDGE_STATE_{state}"
        if not self.source.trusted_graph_sha256:
            return "EDGE_SOURCE_UNVERIFIED"
        if edge.get("sourceHash") and edge["sourceHash"] != self.source.trusted_graph_sha256:
            return "EDGE_SOURCE_MISMATCH"
        if str(edge.get("sourceSnapshot", self.source.snapshot_id)) != self.source.snapshot_id:
            return "EDGE_SNAPSHOT_MISMATCH"
        valid_until = edge.get("validUntil")
        if valid_until:
            try:
                expires = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            except ValueError:
                return "EDGE_EXPIRY_INVALID"
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= datetime.now(UTC):
                return "EDGE_EXPIRED"
        return None
