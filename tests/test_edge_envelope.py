import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neo_sf_q_intel.edge_envelope import (
    DEFAULT_EXTRACTOR_REGISTRY_SHA256,
    DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
    ArtifactContent,
    EdgeEnvelopeRejectionCode,
    ExtractorTrustRegistry,
    TrustedEdgeCompilationError,
    TrustedEdgeContractError,
    TrustedEdgePolicy,
    TrustedEdgeReplayInput,
    artifact_sha256,
    compile_trusted_edge_envelope,
    load_extractor_trust_registry,
    load_trusted_edge_policy,
    source_graph_sha256,
    stable_sha256,
    verify_trusted_edge_envelopes,
)
from neo_sf_q_intel.ontology import (
    contract_sha256,
    load_canonical_ontology,
    load_source_graph_profile,
    normalize_source_graph,
)
from neo_sf_q_intel.propagation import load_propagation_policy, traverse_propagation

ROOT = Path(__file__).parents[1]
ONTOLOGY_PATH = ROOT / "config" / "ontology" / "canonical-ontology.json"
PROFILE_PATH = ROOT / "config" / "source-profiles" / "salesforce-application-graph.json"
REGISTRY_PATH = ROOT / "config" / "extractor-trust-registry.json"
POLICY_PATH = ROOT / "config" / "trusted-edge-policy.json"
PROPAGATION_PATH = ROOT / "config" / "propagation-policy.json"
IMPLEMENTATION_SHA256 = "02d5afa252049f8cc8b66f30be7ff995f2311bec9b36392503567fc7421e05b4"
EXTRACTOR_ID = "canonical-edge-artifact-parser"
EXTRACTOR_VERSION = "1.0.0"
PROJECT_ID = "project-fixture"
SNAPSHOT = "snapshot-fixture"
LOCATOR = "evidence/edges/edge-1.json"
OBSERVED = datetime(2026, 1, 1, tzinfo=UTC)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _edge_artifact(
    *,
    edge_id: str = "edge-1",
    source_id: str = "node-a",
    target_id: str = "node-b",
    extractor_id: str = EXTRACTOR_ID,
    extractor_version: str = EXTRACTOR_VERSION,
) -> bytes:
    return _canonical_bytes(
        {
            "schemaVersion": "1.0.0",
            "edge": {
                "edgeId": edge_id,
                "sourceId": source_id,
                "targetId": target_id,
                "canonicalRelation": "revalidates",
                "evidenceState": "CONFIRMED",
                "sourceSnapshot": SNAPSHOT,
                "extractorId": extractor_id,
                "extractorVersion": extractor_version,
            },
        }
    )


def _build_system(
    *,
    edge_id: str = "edge-1",
    source_id: str = "node-a",
    target_id: str = "node-b",
    extractor_id: str = EXTRACTOR_ID,
    extractor_version: str = EXTRACTOR_VERSION,
    extractor_implementation_sha256: str = IMPLEMENTATION_SHA256,
    evidence_state: str = "CONFIRMED",
    extra_nodes: tuple[dict, ...] = (),
    profile_path: Path = PROFILE_PATH,
    raw_node_kind: str = "apex-class",
    raw_relation: str = "rechecks",
):
    ontology = load_canonical_ontology(ONTOLOGY_PATH)
    profile = load_source_graph_profile(profile_path, ontology)
    registry = load_extractor_trust_registry(REGISTRY_PATH, implementation_root=ROOT)
    policy = load_trusted_edge_policy(POLICY_PATH, registry)
    artifact = _edge_artifact(
        edge_id=edge_id,
        source_id=source_id,
        target_id=target_id,
        extractor_id=extractor_id,
        extractor_version=extractor_version,
    )
    base_provenance = {
        "evidenceState": "CONFIRMED",
        "sourceSnapshot": SNAPSHOT,
        "extractorId": "source-node-parser",
        "source": "evidence/source-graph.json",
    }
    nodes = [
        {**base_provenance, "id": source_id, "kind": raw_node_kind, "label": "A"},
        {**base_provenance, "id": target_id, "kind": raw_node_kind, "label": "B"},
        *extra_nodes,
    ]
    edge = {
        "id": edge_id,
        "from": source_id,
        "relation": raw_relation,
        "to": target_id,
        "source": LOCATOR,
        "evidenceState": evidence_state,
        "sourceSnapshot": SNAPSHOT,
        "extractorId": extractor_id,
        "extractorVersion": extractor_version,
        "extractorImplementationSha256": extractor_implementation_sha256,
        "sourceArtifactSha256": artifact_sha256(artifact),
    }
    raw_graph = {"sourceSnapshot": SNAPSHOT, "nodes": nodes, "edges": [edge]}
    source_content = _canonical_bytes(raw_graph)
    source_digest = source_graph_sha256(source_content)
    replay_input = deepcopy(raw_graph)
    for item in [*replay_input["nodes"], *replay_input["edges"]]:
        item["sourceHash"] = source_digest
    graph = normalize_source_graph(
        replay_input,
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
        "artifact": artifact,
        "source_content": source_content,
        "source_digest": source_digest,
        "graph": graph,
        "edge": graph.edges[0],
    }


def _build_multi_edge_system():
    ontology = load_canonical_ontology(ONTOLOGY_PATH)
    profile = load_source_graph_profile(PROFILE_PATH, ontology)
    registry = load_extractor_trust_registry(REGISTRY_PATH, implementation_root=ROOT)
    policy = load_trusted_edge_policy(POLICY_PATH, registry)
    edge_specs = (("edge-a", "node-a", "node-b"), ("edge-b", "node-b", "node-c"))
    artifacts = {
        edge_id: _edge_artifact(edge_id=edge_id, source_id=source, target_id=target)
        for edge_id, source, target in edge_specs
    }
    node_provenance = {
        "evidenceState": "CONFIRMED",
        "sourceSnapshot": SNAPSHOT,
        "extractorId": "source-node-parser",
        "source": "evidence/source-graph.json",
    }
    raw_graph = {
        "sourceSnapshot": SNAPSHOT,
        "nodes": [
            {**node_provenance, "id": node_id, "kind": "apex-class", "label": node_id}
            for node_id in ("node-a", "node-b", "node-c")
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
    source_content = _canonical_bytes(raw_graph)
    source_digest = source_graph_sha256(source_content)
    replay_input = deepcopy(raw_graph)
    for item in [*replay_input["nodes"], *replay_input["edges"]]:
        item["sourceHash"] = source_digest
    graph = normalize_source_graph(
        replay_input,
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


def _root_kwargs(system: dict) -> dict:
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


def _multi_root_kwargs(system: dict) -> dict:
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


def _compile_multi(system: dict):
    result = []
    for edge in system["graph"].edges:
        locator = f"evidence/edges/{edge.edge_id}.json"
        result.append(
            compile_trusted_edge_envelope(
                edge,
                system["graph"],
                system["ontology"],
                system["profile"],
                system["registry"],
                system["policy"],
                source_artifact_locator=locator,
                source_artifact_content=system["artifacts"][edge.edge_id],
                observed_at=OBSERVED,
                valid_until=OBSERVED + timedelta(hours=1),
                **_multi_root_kwargs(system),
            )
        )
    return tuple(result)


def _rebind_graph_to_source(system: dict, source_content: bytes):
    digest = source_graph_sha256(source_content)
    nodes = tuple(node.model_copy(update={"source_hash": digest}) for node in system["graph"].nodes)
    edges = tuple(edge.model_copy(update={"source_hash": digest}) for edge in system["graph"].edges)
    body = system["graph"].model_dump(mode="json")
    body.update(
        {
            "source_graph_sha256": digest,
            "nodes": [item.model_dump(mode="json") for item in nodes],
            "edges": [item.model_dump(mode="json") for item in edges],
        }
    )
    body.pop("graph_sha256")
    return system["graph"].model_copy(
        update={
            "source_graph_sha256": digest,
            "nodes": nodes,
            "edges": edges,
            "graph_sha256": stable_sha256(body),
        }
    )


def _compile(system: dict):
    return compile_trusted_edge_envelope(
        system["edge"],
        system["graph"],
        system["ontology"],
        system["profile"],
        system["registry"],
        system["policy"],
        source_artifact_locator=LOCATOR,
        source_artifact_content=system["artifact"],
        observed_at=OBSERVED,
        valid_until=OBSERVED + timedelta(hours=1),
        **_root_kwargs(system),
    )


def _verify(system: dict, envelopes: tuple, artifacts: tuple, *, at=OBSERVED):
    return verify_trusted_edge_envelopes(
        system["graph"],
        system["ontology"],
        system["profile"],
        system["registry"],
        system["policy"],
        envelopes,
        artifacts,
        evaluated_at=at,
        required_edge_ids=(system["edge"].edge_id,),
        **_root_kwargs(system),
    )


def _replay_input(system: dict, envelope, *, at: datetime) -> TrustedEdgeReplayInput:
    roots = _root_kwargs(system)
    return TrustedEdgeReplayInput(
        ontology=system["ontology"],
        profile=system["profile"],
        registry=system["registry"],
        policy=system["policy"],
        envelopes=(envelope,),
        artifact_contents=(ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
        evaluated_at=at,
        required_edge_ids=(system["edge"].edge_id,),
        expected_registry_sha256=DEFAULT_EXTRACTOR_REGISTRY_SHA256,
        expected_policy_sha256=DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
        **roots,
    )


def _codes(result) -> set[EdgeEnvelopeRejectionCode]:
    return {item.code for item in result.rejections}


def test_compiles_and_replays_distinct_artifact_and_graph_roots_deterministically() -> None:
    system = _build_system()
    first = _compile(system)
    second = _compile(system)

    assert first == second
    assert first.source_artifact_sha256 == artifact_sha256(system["artifact"])
    assert first.source_graph_sha256 == system["source_digest"]
    result = _verify(
        system,
        (first,),
        (ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
        at=OBSERVED + timedelta(minutes=1),
    )
    assert result.coverage_complete is True
    assert result.release_eligible is False
    assert result.blocking_gap_codes == ("UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",)
    assert result.rejections == ()


def test_wrong_artifact_bytes_and_forged_digest_cannot_replay() -> None:
    system = _build_system()
    envelope = _compile(system)
    wrong = _edge_artifact(edge_id="different-edge")

    result = _verify(
        system,
        (envelope,),
        (ArtifactContent(locator=LOCATOR, content=wrong),),
    )

    assert EdgeEnvelopeRejectionCode.ARTIFACT_DIGEST_MISMATCH in _codes(result)
    assert EdgeEnvelopeRejectionCode.EXTRACTOR_EXECUTION_MISMATCH in _codes(result)
    assert result.coverage_complete is False


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("INFERRED", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
        ("UNVERIFIED", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
        ("HUMAN_CONFIRMED", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
        ("CONTRADICTORY", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
        ("STALE", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
        ("REJECTED", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
        ("VENDOR_ASSERTED", EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED),
    ],
)
def test_nonconfirmed_source_claim_never_compiles(state: str, expected) -> None:
    system = _build_system(evidence_state=state)
    with pytest.raises(TrustedEdgeCompilationError) as raised:
        _compile(system)
    assert expected.value in raised.value.codes


def test_unknown_or_spoofed_extractor_never_compiles() -> None:
    system = _build_system(extractor_id="unregistered-parser")
    with pytest.raises(TrustedEdgeCompilationError) as raised:
        _compile(system)
    assert EdgeEnvelopeRejectionCode.UNKNOWN_EXTRACTOR.value in raised.value.codes


def test_known_extractor_wrong_version_or_implementation_never_compiles() -> None:
    wrong_version = _build_system(extractor_version="9.0.0")
    wrong_implementation = _build_system(extractor_implementation_sha256="a" * 64)

    with pytest.raises(TrustedEdgeCompilationError) as version_error:
        _compile(wrong_version)
    with pytest.raises(TrustedEdgeCompilationError) as implementation_error:
        _compile(wrong_implementation)

    assert EdgeEnvelopeRejectionCode.UNKNOWN_EXTRACTOR.value in version_error.value.codes
    assert (
        EdgeEnvelopeRejectionCode.EXTRACTOR_IMPLEMENTATION_MISMATCH.value
        in implementation_error.value.codes
    )


def test_absolute_locator_is_rejected_without_echoing_it() -> None:
    system = _build_system()
    with pytest.raises(TrustedEdgeCompilationError) as raised:
        compile_trusted_edge_envelope(
            system["edge"],
            system["graph"],
            system["ontology"],
            system["profile"],
            system["registry"],
            system["policy"],
            source_artifact_locator="C:/private/edge.json",
            source_artifact_content=system["artifact"],
            observed_at=OBSERVED,
            valid_until=OBSERVED + timedelta(hours=1),
            **_root_kwargs(system),
        )
    assert "C:/private" not in str(raised.value)
    assert EdgeEnvelopeRejectionCode.UNSAFE_ARTIFACT_LOCATOR.value in raised.value.codes


def test_relation_tamper_and_duplicate_envelope_are_blocking_and_deduplicated() -> None:
    system = _build_system()
    envelope = _compile(system)
    tampered = envelope.model_copy(update={"canonical_relation": "invokes"})
    body = tampered.model_dump(mode="json")
    body.pop("envelope_sha256")
    tampered = tampered.model_copy(update={"envelope_sha256": stable_sha256(body)})

    result = _verify(
        system,
        (tampered, tampered),
        (ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
    )

    assert EdgeEnvelopeRejectionCode.RELATION_MISMATCH in _codes(result)
    assert EdgeEnvelopeRejectionCode.DUPLICATE_EDGE_ENVELOPE in _codes(result)
    keys = [(item.edge_identity_sha256, item.code) for item in result.rejections]
    assert len(keys) == len(set(keys))


def test_missing_required_scope_and_missing_envelope_fail_closed() -> None:
    system = _build_system()
    kwargs = _root_kwargs(system)
    with pytest.raises(TrustedEdgeContractError, match="explicit non-empty"):
        verify_trusted_edge_envelopes(
            system["graph"],
            system["ontology"],
            system["profile"],
            system["registry"],
            system["policy"],
            (),
            (),
            evaluated_at=OBSERVED,
            required_edge_ids=(),
            **kwargs,
        )
    result = _verify(system, (), ())
    assert _codes(result) == {EdgeEnvelopeRejectionCode.MISSING_EDGE_ENVELOPE}


def test_future_and_expired_evidence_emit_typed_blocking_rejections() -> None:
    system = _build_system()
    envelope = _compile(system)
    artifact = (ArtifactContent(locator=LOCATOR, content=system["artifact"]),)

    future = _verify(system, (envelope,), artifact, at=OBSERVED - timedelta(minutes=10))
    expired = _verify(system, (envelope,), artifact, at=OBSERVED + timedelta(hours=2))

    assert EdgeEnvelopeRejectionCode.EVIDENCE_FROM_FUTURE in _codes(future)
    assert EdgeEnvelopeRejectionCode.EVIDENCE_EXPIRED in _codes(expired)


def test_external_graph_project_snapshot_and_profile_pins_cannot_be_self_asserted() -> None:
    system = _build_system()
    cases = [
        {"expected_project_id": "foreign-project"},
        {"expected_source_snapshot": "foreign-snapshot"},
        {"expected_source_graph_sha256": "f" * 64},
        {"expected_normalized_graph_sha256": "e" * 64},
        {"expected_profile_sha256": "d" * 64},
    ]
    for update in cases:
        kwargs = {**_root_kwargs(system), **update}
        with pytest.raises(TrustedEdgeContractError):
            compile_trusted_edge_envelope(
                system["edge"],
                system["graph"],
                system["ontology"],
                system["profile"],
                system["registry"],
                system["policy"],
                source_artifact_locator=LOCATOR,
                source_artifact_content=system["artifact"],
                observed_at=OBSERVED,
                valid_until=OBSERVED + timedelta(hours=1),
                **kwargs,
            )


def test_registry_content_and_implementation_are_both_externally_replayed(
    tmp_path: Path,
) -> None:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    trusted = document["sha256"]
    document["extractors"][0]["implementationSha256"] = "a" * 64
    document["sha256"] = contract_sha256(document)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(TrustedEdgeContractError, match="external trust pin"):
        load_extractor_trust_registry(path, implementation_root=ROOT, expected_sha256=trusted)
    with pytest.raises(TrustedEdgeContractError, match="implementation digest"):
        load_extractor_trust_registry(
            path, implementation_root=ROOT, expected_sha256=document["sha256"]
        )


def test_disabled_historical_extractor_needs_no_file_and_rejects_edge_typed(
    tmp_path: Path,
) -> None:
    system = _build_system(extractor_implementation_sha256="a" * 64)
    registry_document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    entry = registry_document["extractors"][0]
    entry["enabled"] = False
    entry["implementationLocator"] = "removed/historical-parser.py"
    entry["implementationSha256"] = "a" * 64
    registry_document["sha256"] = contract_sha256(registry_document)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry_document), encoding="utf-8")
    registry = load_extractor_trust_registry(
        registry_path,
        implementation_root=ROOT,
        expected_sha256=registry_document["sha256"],
    )
    assert isinstance(registry, ExtractorTrustRegistry)
    policy_document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy_document["extractorRegistry"]["registrySha256"] = registry.sha256
    policy_document["sha256"] = contract_sha256(policy_document)
    policy = TrustedEdgePolicy.model_validate(policy_document)
    system["registry"] = registry
    system["policy"] = policy

    with pytest.raises(TrustedEdgeCompilationError) as raised:
        compile_trusted_edge_envelope(
            system["edge"],
            system["graph"],
            system["ontology"],
            system["profile"],
            registry,
            policy,
            source_artifact_locator=LOCATOR,
            source_artifact_content=system["artifact"],
            observed_at=OBSERVED,
            valid_until=OBSERVED + timedelta(hours=1),
            expected_registry_sha256=registry.sha256,
            expected_policy_sha256=policy.sha256,
            **_root_kwargs(system),
        )
    assert EdgeEnvelopeRejectionCode.EXTRACTOR_DISABLED.value in raised.value.codes


def test_renamed_topology_and_disconnected_node_do_not_change_verification_class() -> None:
    baseline = _build_system()
    renamed = _build_system(edge_id="edge-z", source_id="alpha-z", target_id="omega-z")
    detached_node = {
        "evidenceState": "CONFIRMED",
        "sourceSnapshot": SNAPSHOT,
        "extractorId": "source-node-parser",
        "source": "evidence/source-graph.json",
        "id": "detached-z",
        "kind": "apex-class",
        "label": "Detached",
    }
    extended = _build_system(extra_nodes=(detached_node,))

    for system in (baseline, renamed, extended):
        envelope = _compile(system)
        result = _verify(
            system,
            (envelope,),
            (ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
            at=OBSERVED + timedelta(minutes=1),
        )
        assert result.coverage_complete is True
        assert result.release_eligible is False
        assert result.rejections == ()


def test_renamed_source_vocabulary_preserves_canonical_verification_class(
    tmp_path: Path,
) -> None:
    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    document["profileId"] = "renamed-source-profile"
    for item in document["nodeMappings"]:
        item["rawKind"] = f"renamed.{item['rawKind']}"
    for item in document["relationMappings"]:
        item["rawRelation"] = f"renamed.{item['rawRelation']}"
    document["sha256"] = contract_sha256(document)
    profile_path = tmp_path / "renamed-profile.json"
    profile_path.write_text(json.dumps(document), encoding="utf-8")
    renamed = _build_system(
        profile_path=profile_path,
        raw_node_kind="renamed.apex-class",
        raw_relation="renamed.rechecks",
    )
    baseline = _build_system()

    for system in (baseline, renamed):
        envelope = _compile(system)
        result = _verify(
            system,
            (envelope,),
            (ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
            at=OBSERVED + timedelta(minutes=1),
        )
        assert result.coverage_complete is True
        assert result.release_eligible is False
        assert result.rejections == ()


def test_source_graph_hash_is_line_ending_stable_but_artifact_hash_is_raw_bytes() -> None:
    lf = b'{"nodes":[],"edges":[]}\n'
    crlf = b'{"nodes":[],"edges":[]}\r\n'
    cr = b'{"nodes":[],"edges":[]}\r'
    assert source_graph_sha256(lf) == source_graph_sha256(crlf)
    assert source_graph_sha256(lf) == source_graph_sha256(cr)
    assert hashlib.sha256(lf).hexdigest() != hashlib.sha256(crlf).hexdigest()


def test_legacy_incomplete_graph_remains_analysis_only_but_cannot_compile_release_edge() -> None:
    system = _build_system()
    raw_graph = json.loads(system["source_content"])
    for field in (
        "sourceArtifactSha256",
        "extractorVersion",
        "extractorImplementationSha256",
    ):
        raw_graph["edges"][0].pop(field)
    source_content = _canonical_bytes(raw_graph)
    source_digest = source_graph_sha256(source_content)
    replay_input = deepcopy(raw_graph)
    for item in [*replay_input["nodes"], *replay_input["edges"]]:
        item["sourceHash"] = source_digest
    graph = normalize_source_graph(
        replay_input,
        system["ontology"],
        system["profile"],
        project_id=PROJECT_ID,
        source_graph_sha256=source_digest,
    )
    release_gaps = [
        gap for gap in graph.release_trust_gaps if gap.code.startswith("RELEASE_MISSING_")
    ]
    assert release_gaps
    assert all(gap.blocking is True for gap in release_gaps)
    propagation_policy = load_propagation_policy(PROPAGATION_PATH, system["ontology"])
    result = traverse_propagation(graph, (graph.edges[0].source_id,), propagation_policy)
    assert any(path.target_id == graph.edges[0].target_id for path in result.paths)

    with pytest.raises(TrustedEdgeCompilationError):
        compile_trusted_edge_envelope(
            graph.edges[0],
            graph,
            system["ontology"],
            system["profile"],
            system["registry"],
            system["policy"],
            source_artifact_locator=LOCATOR,
            source_artifact_content=system["artifact"],
            observed_at=OBSERVED,
            valid_until=OBSERVED + timedelta(hours=1),
            expected_source_graph_sha256=source_digest,
            expected_ontology_sha256=system["ontology"].sha256,
            expected_profile_sha256=system["profile"].sha256,
            expected_project_id=PROJECT_ID,
            expected_source_snapshot=SNAPSHOT,
            expected_normalized_graph_sha256=graph.graph_sha256,
            implementation_root=ROOT,
            source_graph_content=source_content,
        )


@pytest.mark.parametrize(
    "source_content",
    (
        b'{"sourceSnapshot":"snapshot-fixture","nodes":[],"nodes":[],"edges":[]}',
        _canonical_bytes(
            {
                "sourceSnapshot": SNAPSHOT,
                "nodes": [
                    {"id": "same", "kind": "apex-class"},
                    {"id": "same", "kind": "apex-class"},
                ],
                "edges": [],
            }
        ),
        _canonical_bytes(
            {
                "sourceSnapshot": SNAPSHOT,
                "nodes": [{"id": "a", "kind": "apex-class"}],
                "edges": [
                    {"id": "edge", "from": "a", "relation": "rechecks", "to": "missing"}
                ],
            }
        ),
        _canonical_bytes(
            {
                "sourceSnapshot": SNAPSHOT,
                "nodes": [
                    {"id": "a", "kind": "apex-class"},
                    {"id": "b", "kind": "apex-class"},
                ],
                "edges": [
                    {"id": "same", "from": "a", "relation": "rechecks", "to": "b"},
                    {"id": "same", "from": "a", "relation": "rechecks", "to": "b"},
                ],
            }
        ),
    ),
)
def test_raw_source_graph_structural_attacks_fail_replay(source_content: bytes) -> None:
    system = _build_system()
    graph = _rebind_graph_to_source(system, source_content)
    with pytest.raises(TrustedEdgeContractError):
        compile_trusted_edge_envelope(
            graph.edges[0],
            graph,
            system["ontology"],
            system["profile"],
            system["registry"],
            system["policy"],
            source_artifact_locator=LOCATOR,
            source_artifact_content=system["artifact"],
            observed_at=OBSERVED,
            valid_until=OBSERVED + timedelta(hours=1),
            expected_source_graph_sha256=graph.source_graph_sha256 or "",
            expected_ontology_sha256=system["ontology"].sha256,
            expected_profile_sha256=system["profile"].sha256,
            expected_project_id=PROJECT_ID,
            expected_source_snapshot=SNAPSHOT,
            expected_normalized_graph_sha256=graph.graph_sha256,
            implementation_root=ROOT,
            source_graph_content=source_content,
        )


def test_propagation_replays_verified_envelope_without_promoting_release_authority() -> None:
    system = _build_system()
    envelope = _compile(system)
    propagation_policy = load_propagation_policy(PROPAGATION_PATH, system["ontology"])

    legacy = traverse_propagation(
        system["graph"], (system["edge"].source_id,), propagation_policy
    )
    verified = traverse_propagation(
        system["graph"],
        (system["edge"].source_id,),
        propagation_policy,
        trusted_edge_replay=_replay_input(
            system, envelope, at=OBSERVED + timedelta(minutes=1)
        ),
    )

    legacy_path = next(path for path in legacy.paths if path.target_id == system["edge"].target_id)
    verified_path = next(
        path for path in verified.paths if path.target_id == system["edge"].target_id
    )
    assert legacy_path.authority_scope == "ANALYSIS_ONLY"
    assert legacy_path.local_edge_envelope_complete is False
    assert legacy_path.hops[0].provenance_scope == "LEGACY_ANALYSIS_ONLY"
    assert verified_path.authority_scope == "ANALYSIS_ONLY"
    assert verified_path.local_edge_envelope_complete is True
    assert verified_path.hops[0].trusted_edge_envelope_sha256 == envelope.envelope_sha256


def test_endpoint_reversal_is_rejected_even_when_envelope_digest_is_recomputed() -> None:
    system = _build_system()
    envelope = _compile(system)
    reversed_signature = envelope.endpoint_signature.model_copy(
        update={
            "source_id": envelope.endpoint_signature.target_id,
            "target_id": envelope.endpoint_signature.source_id,
        }
    )
    tampered = envelope.model_copy(update={"endpoint_signature": reversed_signature})
    body = tampered.model_dump(mode="json")
    body.pop("envelope_sha256")
    tampered = tampered.model_copy(update={"envelope_sha256": stable_sha256(body)})

    result = _verify(
        system,
        (tampered,),
        (ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
    )
    assert EdgeEnvelopeRejectionCode.ILLEGAL_ENDPOINT_SIGNATURE in _codes(result)


def test_noncanonical_direction_is_rejected_even_with_recomputed_digest() -> None:
    system = _build_system()
    envelope = _compile(system)
    bad_signature = envelope.endpoint_signature.model_construct(
        direction="TARGET_TO_SOURCE",
        source_id=envelope.endpoint_signature.source_id,
        source_class=envelope.endpoint_signature.source_class,
        target_id=envelope.endpoint_signature.target_id,
        target_class=envelope.endpoint_signature.target_class,
    )
    tampered = envelope.model_copy(update={"endpoint_signature": bad_signature})
    body = tampered.model_dump(mode="json")
    body.pop("envelope_sha256")
    tampered = tampered.model_copy(update={"envelope_sha256": stable_sha256(body)})

    result = _verify(
        system,
        (tampered,),
        (ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
    )
    assert EdgeEnvelopeRejectionCode.EDGE_ENVELOPE_DIGEST_MISMATCH in _codes(result)
    assert EdgeEnvelopeRejectionCode.ILLEGAL_ENDPOINT_SIGNATURE in _codes(result)


def test_multi_edge_input_permutations_have_one_verification_digest_and_order() -> None:
    system = _build_multi_edge_system()
    envelopes = _compile_multi(system)
    artifacts = tuple(
        ArtifactContent(
            locator=f"evidence/edges/{edge_id}.json", content=system["artifacts"][edge_id]
        )
        for edge_id in ("edge-a", "edge-b")
    )
    arguments = {
        "evaluated_at": OBSERVED + timedelta(minutes=1),
        "required_edge_ids": ("edge-a", "edge-b"),
        **_multi_root_kwargs(system),
    }
    first = verify_trusted_edge_envelopes(
        system["graph"],
        system["ontology"],
        system["profile"],
        system["registry"],
        system["policy"],
        envelopes,
        artifacts,
        **arguments,
    )
    second = verify_trusted_edge_envelopes(
        system["graph"],
        system["ontology"],
        system["profile"],
        system["registry"],
        system["policy"],
        tuple(reversed(envelopes)),
        tuple(reversed(artifacts)),
        **arguments,
    )
    assert first == second
    assert [item.edge_id for item in first.accepted_envelopes] == ["edge-a", "edge-b"]


def test_unverified_intermediate_hop_stays_analysis_only_after_current_replay() -> None:
    system = _build_multi_edge_system()
    envelopes = _compile_multi(system)
    first_envelope = envelopes[0]
    roots = _multi_root_kwargs(system)
    replay = TrustedEdgeReplayInput(
        ontology=system["ontology"],
        profile=system["profile"],
        registry=system["registry"],
        policy=system["policy"],
        envelopes=(first_envelope,),
        artifact_contents=(
            ArtifactContent(
                locator="evidence/edges/edge-a.json",
                content=system["artifacts"]["edge-a"],
            ),
        ),
        evaluated_at=OBSERVED + timedelta(minutes=1),
        required_edge_ids=("edge-a",),
        expected_registry_sha256=DEFAULT_EXTRACTOR_REGISTRY_SHA256,
        expected_policy_sha256=DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
        **roots,
    )
    propagation_policy = load_propagation_policy(PROPAGATION_PATH, system["ontology"])
    result = traverse_propagation(
        system["graph"],
        ("node-a",),
        propagation_policy,
        trusted_edge_replay=replay,
    )
    path = next(item for item in result.paths if item.target_id == "node-c")
    assert [hop.provenance_scope for hop in path.hops] == [
        "VERIFIED_EDGE_ENVELOPE",
        "LEGACY_ANALYSIS_ONLY",
    ]
    assert path.local_edge_envelope_complete is False
    assert path.authority_scope == "ANALYSIS_ONLY"


def test_expired_replay_is_not_consumed_as_verified_provenance() -> None:
    system = _build_system()
    envelope = _compile(system)
    propagation_policy = load_propagation_policy(PROPAGATION_PATH, system["ontology"])
    result = traverse_propagation(
        system["graph"],
        (system["edge"].source_id,),
        propagation_policy,
        trusted_edge_replay=_replay_input(
            system, envelope, at=OBSERVED + timedelta(hours=2)
        ),
    )
    path = next(item for item in result.paths if item.target_id == system["edge"].target_id)
    assert path.local_edge_envelope_complete is False
    assert path.hops[0].provenance_scope == "LEGACY_ANALYSIS_ONLY"
    assert any(
        gap.code == "TRUSTED_EDGE_EVIDENCE_EXPIRED"
        and gap.scope == "RELEASE_ONLY"
        and gap.blocking
        for gap in result.gaps
    )


def test_policy_rotation_prevents_old_envelope_reuse_at_propagation() -> None:
    system = _build_system()
    envelope = _compile(system)
    policy_document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy_document["policyVersion"] = "1.0.1"
    policy_document["sha256"] = contract_sha256(policy_document)
    rotated_policy = TrustedEdgePolicy.model_validate(policy_document)
    roots = _root_kwargs(system)
    replay = TrustedEdgeReplayInput(
        ontology=system["ontology"],
        profile=system["profile"],
        registry=system["registry"],
        policy=rotated_policy,
        envelopes=(envelope,),
        artifact_contents=(ArtifactContent(locator=LOCATOR, content=system["artifact"]),),
        evaluated_at=OBSERVED + timedelta(minutes=1),
        required_edge_ids=(system["edge"].edge_id,),
        expected_registry_sha256=DEFAULT_EXTRACTOR_REGISTRY_SHA256,
        expected_policy_sha256=rotated_policy.sha256,
        **roots,
    )
    propagation_policy = load_propagation_policy(PROPAGATION_PATH, system["ontology"])
    result = traverse_propagation(
        system["graph"],
        (system["edge"].source_id,),
        propagation_policy,
        trusted_edge_replay=replay,
    )
    path = next(item for item in result.paths if item.target_id == system["edge"].target_id)
    assert path.local_edge_envelope_complete is False
    assert any(
        gap.code == "TRUSTED_EDGE_POLICY_ROOT_MISMATCH"
        and gap.scope == "RELEASE_ONLY"
        and gap.blocking
        for gap in result.gaps
    )
