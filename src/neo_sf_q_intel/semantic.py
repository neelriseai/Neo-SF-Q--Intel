from __future__ import annotations

import math
from dataclasses import dataclass

from neo_sf_q_intel.domain import EvidenceRef
from neo_sf_q_intel.providers import ModelProvider
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot, node_to_evidence


@dataclass(frozen=True)
class SemanticHit:
    evidence: EvidenceRef
    score: float


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Embedding dimensions must match and be non-empty")
    magnitude = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if magnitude == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / magnitude


class SemanticEvidenceIndex:
    """Ephemeral small-corpus ranking foundation; ChromaDB will own persistence."""

    def __init__(self, source: SalesforceSourceSnapshot, provider: ModelProvider) -> None:
        self.source = source
        self.provider = provider
        self._rows: list[tuple[dict, list[float]]] = []

    async def build(self) -> None:
        nodes = self.source.nodes
        texts = [self._node_text(node) for node in nodes]
        vectors = await self.provider.embed(texts)
        if len(vectors) != len(nodes):
            raise ValueError("Embedding provider returned an unexpected row count")
        self._rows = list(zip(nodes, vectors, strict=True))

    async def search(self, query: str, limit: int = 8) -> list[SemanticHit]:
        if not self._rows:
            await self.build()
        query_vector = (await self.provider.embed([query]))[0]
        hits = [
            SemanticHit(
                evidence=node_to_evidence(
                    node,
                    self.source.snapshot_id,
                    self.source.trusted_graph_sha256,
                ),
                score=cosine_similarity(query_vector, vector),
            )
            for node, vector in self._rows
        ]
        return sorted(hits, key=lambda hit: -hit.score)[:limit]

    @staticmethod
    def _node_text(node: dict) -> str:
        return " ".join(str(node.get(key, "")) for key in ("id", "kind", "label", "source"))
