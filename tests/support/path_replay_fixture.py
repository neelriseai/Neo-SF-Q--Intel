import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from neo_sf_q_intel.edge_envelope import (
    DEFAULT_EXTRACTOR_REGISTRY_SHA256,
    DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
    ArtifactContent,
    TrustedEdgeReplayInput,
    artifact_sha256,
    compile_trusted_edge_envelope,
    load_extractor_trust_registry,
    load_trusted_edge_policy,
    source_graph_sha256,
)
from neo_sf_q_intel.ontology import (
    load_canonical_ontology,
    load_source_graph_profile,
    normalize_source_graph,
)

ROOT = Path(__file__).parents[2]
OBSERVED = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_ID = "project-fixture"
SNAPSHOT = "snapshot-fixture"
EXTRACTOR_ID = "canonical-edge-artifact-parser"
EXTRACTOR_VERSION = "1.0.0"
IMPLEMENTATION_SHA256 = "02d5afa252049f8cc8b66f30be7ff995f2311bec9b36392503567fc7421e05b4"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def edge_artifact(edge_id: str, source_id: str, target_id: str) -> bytes:
    return canonical_bytes(
        {
            "schemaVersion": "1.0.0",
            "edge": {
                "edgeId": edge_id,
                "sourceId": source_id,
                "targetId": target_id,
                "canonicalRelation": "revalidates",
                "evidenceState": "CONFIRMED",
                "sourceSnapshot": SNAPSHOT,
                "extractorId": EXTRACTOR_ID,
                "extractorVersion": EXTRACTOR_VERSION,
            },
        }
    )


def build_system(
    edge_specs: tuple[tuple[str, str, str], ...] = (
        ("edge-a", "node-a", "node-b"),
        ("edge-b", "node-b", "node-c"),
    ),
    *,
    isolated_node_ids: tuple[str, ...] = (),
):
    ontology = load_canonical_ontology(
        ROOT / "config" / "ontology" / "canonical-ontology.json"
    )
    profile = load_source_graph_profile(
        ROOT / "config" / "source-profiles" / "salesforce-application-graph.json",
        ontology,
    )
    registry = load_extractor_trust_registry(
        ROOT / "config" / "extractor-trust-registry.json", implementation_root=ROOT
    )
    policy = load_trusted_edge_policy(
        ROOT / "config" / "trusted-edge-policy.json", registry
    )
    artifacts = {
        edge_id: edge_artifact(edge_id, source, target)
        for edge_id, source, target in edge_specs
    }
    node_ids = tuple(
        sorted(
            {
                *(source for _, source, _ in edge_specs),
                *(target for _, _, target in edge_specs),
                *isolated_node_ids,
            }
        )
    )
    provenance = {
        "evidenceState": "CONFIRMED",
        "sourceSnapshot": SNAPSHOT,
        "extractorId": "source-node-parser",
        "source": "evidence/source-graph.json",
    }
    raw_graph = {
        "sourceSnapshot": SNAPSHOT,
        "nodes": [
            {**provenance, "id": node_id, "kind": "apex-class", "label": node_id}
            for node_id in node_ids
        ],
        "edges": [
            {
                "id": edge_id,
                "from": source,
                "relation": "rechecks",
                "to": target,
                "source": f"evidence/edges/{edge_id}.json",
                "evidenceState": "CONFIRMED",
                "sourceSnapshot": SNAPSHOT,
                "extractorId": EXTRACTOR_ID,
                "extractorVersion": EXTRACTOR_VERSION,
                "extractorImplementationSha256": IMPLEMENTATION_SHA256,
                "sourceArtifactSha256": artifact_sha256(artifacts[edge_id]),
            }
            for edge_id, source, target in edge_specs
        ],
    }
    source_content = canonical_bytes(raw_graph)
    source_digest = source_graph_sha256(source_content)
    normalized_input = deepcopy(raw_graph)
    for item in [*normalized_input["nodes"], *normalized_input["edges"]]:
        item["sourceHash"] = source_digest
    graph = normalize_source_graph(
        normalized_input,
        ontology,
        profile,
        project_id=PROJECT_ID,
        source_graph_sha256=source_digest,
    )
    return {
        "ontology": ontology,
        "profile": profile,
        "registry": registry,
        "policy": policy,
        "artifacts": artifacts,
        "source_content": source_content,
        "source_digest": source_digest,
        "graph": graph,
    }


def root_kwargs(system: dict) -> dict:
    return {
        "expected_source_graph_sha256": system["source_digest"],
        "expected_ontology_sha256": system["ontology"].sha256,
        "expected_profile_sha256": system["profile"].sha256,
        "expected_project_id": PROJECT_ID,
        "expected_source_snapshot": SNAPSHOT,
        "expected_normalized_graph_sha256": system["graph"].graph_sha256,
        "implementation_root": ROOT,
        "source_graph_content": system["source_content"],
    }


def compile_envelopes(system: dict):
    return tuple(
        compile_trusted_edge_envelope(
            edge,
            system["graph"],
            system["ontology"],
            system["profile"],
            system["registry"],
            system["policy"],
            source_artifact_locator=f"evidence/edges/{edge.edge_id}.json",
            source_artifact_content=system["artifacts"][edge.edge_id],
            observed_at=OBSERVED,
            valid_until=OBSERVED + timedelta(hours=1),
            **root_kwargs(system),
        )
        for edge in system["graph"].edges
    )


def replay_input(
    system: dict,
    *,
    envelopes=None,
    artifacts=None,
    required_edge_ids: tuple[str, ...] | None = None,
) -> TrustedEdgeReplayInput:
    compiled = compile_envelopes(system)
    artifact_inputs = tuple(
        ArtifactContent(locator=f"evidence/edges/{edge_id}.json", content=content)
        for edge_id, content in sorted(system["artifacts"].items())
    )
    return TrustedEdgeReplayInput(
        ontology=system["ontology"],
        profile=system["profile"],
        registry=system["registry"],
        policy=system["policy"],
        envelopes=compiled if envelopes is None else envelopes,
        artifact_contents=artifact_inputs if artifacts is None else artifacts,
        evaluated_at=OBSERVED,
        required_edge_ids=(
            tuple(sorted(system["artifacts"]))
            if required_edge_ids is None
            else required_edge_ids
        ),
        expected_registry_sha256=DEFAULT_EXTRACTOR_REGISTRY_SHA256,
        expected_policy_sha256=DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
        **root_kwargs(system),
    )
