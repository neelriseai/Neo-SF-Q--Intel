from __future__ import annotations

import hashlib


def fixture_digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def add_trusted_envelopes(
    graph: dict,
    *,
    snapshot_id: str,
    source_hash: str,
    extractor_id: str = "deterministic-fixture-parser",
) -> dict:
    """Add explicit synthetic provenance to records that a test intends to trust."""
    for node in graph.get("nodes", []):
        node.setdefault("source", f"fixtures/{node['id']}.json")
        node.setdefault("extractorId", extractor_id)
        node.setdefault("evidenceState", "CONFIRMED")
        node.setdefault("sourceSnapshot", snapshot_id)
        node.setdefault("sourceHash", source_hash)
    for index, edge in enumerate(graph.get("edges", [])):
        identity = f"{edge['from']}|{edge['relation']}|{edge['to']}|{index}"
        edge.setdefault("id", f"edge:{hashlib.sha256(identity.encode()).hexdigest()[:24]}")
        edge.setdefault("source", "fixtures/application-graph.json")
        edge.setdefault("extractorId", extractor_id)
        edge.setdefault("evidenceState", "CONFIRMED")
        edge.setdefault("sourceSnapshot", snapshot_id)
        edge.setdefault("sourceHash", source_hash)
    return graph
