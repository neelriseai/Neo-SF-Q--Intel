from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from neo_sf_q_intel.domain import EvidenceRef, EvidenceState
from neo_sf_q_intel.policy import ReasoningPolicy, ReasoningPolicyError
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
    source_relation: str
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
        normalized_graph = source.normalized_graph
        self.source_snapshot = source.snapshot_id
        self.source_graph_sha256 = source.trusted_graph_sha256
        self.project_id = source.project_id
        if normalized_graph.source_snapshot != self.source_snapshot:
            raise ReasoningPolicyError(
                "Normalized graph and raw source use different snapshot identities"
            )
        source_identity = source.ontology_identity
        self.ontology_identity = dict(source_identity)
        if (
            self.policy.ontology_id,
            self.policy.ontology_version,
            self.policy.ontology_sha256,
        ) != (
            source_identity["ontologyId"],
            source_identity["ontologyVersion"],
            source_identity["ontologySha256"],
        ):
            raise ReasoningPolicyError(
                "Reasoning policy and source profile use different ontology identities"
            )
        self.normalization_gaps = normalized_graph.mapping_gaps
        self.nodes = {str(node["id"]): node for node in source.nodes}
        self.outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in source.edges:
            self.outgoing[str(edge["from"])].append(edge)
            self.incoming[str(edge["to"])].append(edge)
        for adjacency in (*self.outgoing.values(), *self.incoming.values()):
            adjacency.sort(
                key=lambda edge: (
                    str(edge["id"]),
                    str(edge["from"]),
                    str(edge["to"]),
                )
            )

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
        ordered_seeds = sorted(set(seed_ids))
        queue = deque((seed_id, 0) for seed_id in ordered_seeds)
        seen = set(ordered_seeds)
        expanded: list[TraversalHit] = []
        gaps: list[TraversalGap] = []
        truncated = False
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            current_node = self.nodes.get(current)
            if current_node is None:
                continue
            current_trust_gaps = self.node_trust_gaps(current_node)
            if current_trust_gaps:
                gaps.extend(
                    TraversalGap(
                        code=code,
                        relation="",
                        source_id=current,
                        target_id=current,
                        neighbor_id=current,
                    )
                    for code in current_trust_gaps
                )
                continue
            adjacent = [(edge, str(edge["to"]), "outgoing") for edge in self.outgoing[current]] + [
                (edge, str(edge["from"]), "incoming") for edge in self.incoming[current]
            ]
            for edge, neighbor_id, direction in adjacent:
                relation = str(edge["relation"])
                trust_gaps = self._edge_trust_gaps(edge)
                if trust_gaps:
                    gaps.extend(
                        TraversalGap(
                            code=code,
                            relation=relation,
                            source_id=str(edge["from"]),
                            target_id=str(edge["to"]),
                            neighbor_id=neighbor_id,
                        )
                        for code in trust_gaps
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
                neighbor_trust_gaps = self.node_trust_gaps(self.nodes[neighbor_id])
                if neighbor_trust_gaps:
                    gaps.extend(
                        TraversalGap(
                            code=code,
                            relation=relation,
                            source_id=str(edge["from"]),
                            target_id=str(edge["to"]),
                            neighbor_id=neighbor_id,
                        )
                        for code in neighbor_trust_gaps
                    )
                    continue
                if len(expanded) >= limit:
                    truncated = True
                    break
                seen.add(neighbor_id)
                expanded.append(
                    TraversalHit(
                        node=self.nodes[neighbor_id],
                        relation=relation,
                        source_relation=str(edge.get("sourceRelation", relation)),
                        direction=direction,
                        source_id=str(edge["from"]),
                        target_id=str(edge["to"]),
                        depth=current_depth + 1,
                        evidence_state=EvidenceState(edge["evidenceState"]),
                        source_snapshot=str(edge["sourceSnapshot"]),
                        source_hash=str(edge["sourceHash"]),
                        valid_until=edge.get("validUntil"),
                    )
                )
                queue.append((neighbor_id, current_depth + 1))
            if truncated:
                break
        unique_gaps = {
            (gap.code, gap.relation, gap.source_id, gap.target_id, gap.neighbor_id): gap
            for gap in gaps
        }
        return expanded, [unique_gaps[key] for key in sorted(unique_gaps)], truncated

    def evidence_for(self, node: dict[str, Any]) -> EvidenceRef:
        return node_to_evidence(node, self.source_snapshot, self.source_graph_sha256)

    def node_trust_gaps(self, node: dict[str, Any]) -> tuple[str, ...]:
        return self._record_trust_gaps(node, record_type="NODE")

    def _edge_trust_gaps(self, edge: dict[str, Any]) -> tuple[str, ...]:
        return self._record_trust_gaps(edge, record_type="EDGE")

    def _record_trust_gaps(
        self, record: dict[str, Any], *, record_type: str
    ) -> tuple[str, ...]:
        normalization_gaps = record.get("ontologyTrustGaps")
        if isinstance(normalization_gaps, list) and normalization_gaps:
            return tuple(sorted({str(code) for code in normalization_gaps}))
        if not record.get("id"):
            return (f"{record_type}_ID_MISSING",)
        if not record.get("source"):
            return (f"{record_type}_SOURCE_ARTIFACT_MISSING",)
        if not record.get("extractorId"):
            return (f"{record_type}_EXTRACTOR_MISSING",)
        if "evidenceState" not in record:
            return (f"{record_type}_STATE_MISSING",)
        try:
            state = EvidenceState(record["evidenceState"])
        except ValueError:
            return (f"INVALID_{record_type}_STATE",)
        if state is EvidenceState.HUMAN_CONFIRMED:
            return (f"{record_type}_HUMAN_APPROVAL_MISSING",)
        if state is not EvidenceState.CONFIRMED:
            return (f"{record_type}_STATE_{state}",)
        if not self.source_graph_sha256:
            return (f"{record_type}_SOURCE_UNVERIFIED",)
        if "sourceHash" not in record:
            return (f"{record_type}_SOURCE_HASH_MISSING",)
        if record["sourceHash"] != self.source_graph_sha256:
            return (f"{record_type}_SOURCE_MISMATCH",)
        if "sourceSnapshot" not in record:
            return (f"{record_type}_SNAPSHOT_MISSING",)
        if str(record["sourceSnapshot"]) != self.source_snapshot:
            return (f"{record_type}_SNAPSHOT_MISMATCH",)
        valid_until = record.get("validUntil")
        if valid_until:
            try:
                expires = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            except ValueError:
                return (f"{record_type}_EXPIRY_INVALID",)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= datetime.now(UTC):
                return (f"{record_type}_EXPIRED",)
        return ()
