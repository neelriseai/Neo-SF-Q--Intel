from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import neo_sf_q_intel.change_seed as change_seed_module
import neo_sf_q_intel.change_verification as change_verification_module
import neo_sf_q_intel.path_replay as path_replay_module
from neo_sf_q_intel.change_seed import (
    DEFAULT_CHANGE_SEED_POLICY_SHA256,
    ChangeSeedGapCode,
    ChangeSeedInputs,
    ChangeSeedMappingArtifact,
    compile_change_seed_mapping,
    load_change_seed_policy,
    verify_change_seed_mapping,
)
from neo_sf_q_intel.change_verification import (
    LocalGitChangeProducer,
    load_verified_change_policy,
)
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
    stable_sha256,
)
from neo_sf_q_intel.ontology import (
    contract_sha256,
    load_canonical_ontology,
    load_source_graph_profile,
    normalize_source_graph,
)
from neo_sf_q_intel.path_replay import (
    compile_complete_graph_path_replay,
    load_complete_path_replay_policy,
)
from neo_sf_q_intel.propagation import load_propagation_policy

ROOT = Path(__file__).parents[1]
T0 = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
PROJECT = "project-fixture"
SNAPSHOT = "graph-snapshot-fixture"
EXTRACTOR_ID = "canonical-edge-artifact-parser"
EXTRACTOR_VERSION = "1.0.0"
EXTRACTOR_SHA256 = "02d5afa252049f8cc8b66f30be7ff995f2311bec9b36392503567fc7421e05b4"


def _run(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        shell=False,
    ).stdout


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _run(repository, "init", "--quiet")
    _run(repository, "config", "user.email", "fixture@example.invalid")
    _run(repository, "config", "user.name", "Fixture")
    target = repository / "src" / "alpha.cls"
    target.parent.mkdir()
    target.write_text("base\n", encoding="utf-8")
    _run(repository, "add", "--all")
    _run(repository, "commit", "--quiet", "-m", "base")
    target.write_text("changed\n", encoding="utf-8")
    return repository


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _edge_artifact(edge_id: str, source: str, target: str) -> bytes:
    return _canonical_bytes(
        {
            "schemaVersion": "1.0.0",
            "edge": {
                "edgeId": edge_id,
                "sourceId": source,
                "targetId": target,
                "canonicalRelation": "declared-in",
                "evidenceState": "CONFIRMED",
                "sourceSnapshot": SNAPSHOT,
                "extractorId": EXTRACTOR_ID,
                "extractorVersion": EXTRACTOR_VERSION,
            },
        }
    )


def _graph_system(
    *,
    anchors: int = 1,
    locator: str = "src/alpha.cls",
    locators: tuple[str, ...] | None = None,
) -> dict:
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
    edge_policy = load_trusted_edge_policy(
        ROOT / "config" / "trusted-edge-policy.json", registry
    )
    selected_locators = locators or tuple(locator for _ in range(anchors))
    artifact_nodes = [
        {
            "id": f"file:{index}",
            "kind": "file",
            "label": item_locator,
            "path": item_locator,
            "source": "knowledge/source-graph.json",
            "evidenceState": "CONFIRMED",
            "sourceSnapshot": SNAPSHOT,
            "extractorId": "source-node-parser",
        }
        for index, item_locator in enumerate(selected_locators)
    ]
    owner = {
        "id": "code:alpha",
        "kind": "apex-class",
        "label": "Alpha",
        "source": "src/alpha.cls",
        "evidenceState": "CONFIRMED",
        "sourceSnapshot": SNAPSHOT,
        "extractorId": "source-node-parser",
    }
    edge_artifacts = {
        f"edge:{index}": _edge_artifact(f"edge:{index}", owner["id"], node["id"])
        for index, node in enumerate(artifact_nodes)
    }
    raw_graph = {
        "sourceSnapshot": SNAPSHOT,
        "nodes": [owner, *artifact_nodes],
        "edges": [
            {
                "id": edge_id,
                "from": owner["id"],
                "relation": "defined_in",
                "to": artifact_nodes[index]["id"],
                "source": f"evidence/edge-{index}.json",
                "evidenceState": "CONFIRMED",
                "sourceSnapshot": SNAPSHOT,
                "extractorId": EXTRACTOR_ID,
                "extractorVersion": EXTRACTOR_VERSION,
                "extractorImplementationSha256": EXTRACTOR_SHA256,
                "sourceArtifactSha256": artifact_sha256(edge_artifacts[edge_id]),
            }
            for index, edge_id in enumerate(edge_artifacts)
        ],
    }
    source_content = _canonical_bytes(raw_graph)
    source_digest = source_graph_sha256(source_content)
    prepared = deepcopy(raw_graph)
    for item in [*prepared["nodes"], *prepared["edges"]]:
        item["sourceHash"] = source_digest
    graph = normalize_source_graph(
        prepared,
        ontology,
        profile,
        project_id=PROJECT,
        source_graph_sha256=source_digest,
    )
    root_args = {
        "expected_source_graph_sha256": source_digest,
        "expected_ontology_sha256": ontology.sha256,
        "expected_profile_sha256": profile.sha256,
        "expected_project_id": PROJECT,
        "expected_source_snapshot": SNAPSHOT,
        "expected_normalized_graph_sha256": graph.graph_sha256,
        "implementation_root": ROOT,
        "source_graph_content": source_content,
    }
    envelopes = tuple(
        compile_trusted_edge_envelope(
            edge,
            graph,
            ontology,
            profile,
            registry,
            edge_policy,
            source_artifact_locator=edge.source or "",
            source_artifact_content=edge_artifacts[edge.edge_id],
            observed_at=T0,
            valid_until=T0 + timedelta(hours=1),
            **root_args,
        )
        for edge in graph.edges
    )
    replay = TrustedEdgeReplayInput(
        ontology=ontology,
        profile=profile,
        registry=registry,
        policy=edge_policy,
        envelopes=envelopes,
        artifact_contents=tuple(
            ArtifactContent(locator=f"evidence/edge-{index}.json", content=content)
            for index, (edge_id, content) in enumerate(sorted(edge_artifacts.items()))
        ),
        evaluated_at=T0,
        required_edge_ids=tuple(sorted(edge_artifacts)),
        expected_registry_sha256=DEFAULT_EXTRACTOR_REGISTRY_SHA256,
        expected_policy_sha256=DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
        **root_args,
    )
    propagation = load_propagation_policy(
        ROOT / "config" / "propagation-policy.json", ontology
    )
    path_policy = load_complete_path_replay_policy(
        ROOT / "config" / "complete-path-replay-policy.json", propagation
    )
    complete = compile_complete_graph_path_replay(
        graph, propagation, path_policy, replay
    )
    assert complete.artifact is not None
    return {
        "ontology": ontology,
        "profile": profile,
        "graph": graph,
        "propagation": propagation,
        "path_policy": path_policy,
        "replay": replay,
        "complete": complete.artifact,
    }


def _policy():
    return load_change_seed_policy(
        ROOT / "config" / "change-seed-policy.json", implementation_root=ROOT
    )


def _inputs(repository: Path, system: dict) -> ChangeSeedInputs:
    change_policy = load_verified_change_policy(
        ROOT / "config" / "verified-change-policy.json", implementation_root=ROOT
    )
    producer = LocalGitChangeProducer(PROJECT, repository, change_policy)
    captured = producer.capture(repository)
    assert captured.artifact is not None
    return ChangeSeedInputs(
        repository_hint=repository,
        candidate=captured.artifact,
        producer=producer,
        graph=system["graph"],
        ontology=system["ontology"],
        profile=system["profile"],
        propagation_policy=system["propagation"],
        path_policy=system["path_policy"],
        trusted_edge_replay=system["replay"],
        complete_path_candidate=system["complete"],
    )


def _codes(result) -> set[ChangeSeedGapCode]:
    return {gap.code for gap in result.gaps}


@pytest.fixture(autouse=True)
def _fixed_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(change_verification_module, "_utc_now", lambda: T0)
    monkeypatch.setattr(path_replay_module, "_utc_now", lambda: T0 + timedelta(minutes=1))
    monkeypatch.setattr(change_seed_module, "_utc_now", lambda: T0 + timedelta(minutes=1))


def test_policy_is_self_hashed_externally_pinned_and_pins_mapper() -> None:
    policy = _policy()
    assert policy.sha256 == DEFAULT_CHANGE_SEED_POLICY_SHA256
    assert policy.mapper.implementation_sha256 == change_seed_module._implementation_sha256(
        (ROOT / policy.mapper.implementation_locator).read_bytes()
    )


def test_maps_every_verified_change_to_anchor_owner_and_complete_paths(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    result = compile_change_seed_mapping(_inputs(repository, system), _policy())

    assert result.local_mapping_complete is True
    assert result.change_seed_scope_attested is False
    assert result.release_eligible is False
    assert result.artifact is not None
    assert len(result.artifact.bindings) == 1
    binding = result.artifact.bindings[0]
    assert binding.change.path == "src/alpha.cls"
    assert binding.anchor_node_id == "file:0"
    assert binding.declared_entity_ids == ("code:alpha",)
    assert binding.seed_ids == ("file:0",)
    expected_paths = tuple(
        sorted(
            path.path_sha256
            for path in system["complete"].paths
            if path.seed_id in binding.seed_ids
        )
    )
    assert binding.affected_path_sha256s == expected_paths
    assert {
        ChangeSeedGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED,
        ChangeSeedGapCode.GRAPH_INPUT_TREE_NOT_ATTESTED,
        ChangeSeedGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE,
    } <= _codes(result)
    assert str(repository) not in result.model_dump_json()


def test_exact_mapping_is_invariant_to_graph_order(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first_system = _graph_system()
    first = compile_change_seed_mapping(_inputs(repository, first_system), _policy())
    second_system = _graph_system()
    reversed_graph = second_system["graph"].model_copy(
        update={
            "nodes": tuple(reversed(second_system["graph"].nodes)),
            "edges": tuple(reversed(second_system["graph"].edges)),
        }
    )
    # A reordered graph object is not current replay authority and must fail closed.
    altered = _inputs(repository, second_system)
    from dataclasses import replace

    altered = replace(altered, graph=reversed_graph)
    second = compile_change_seed_mapping(altered, _policy())

    assert first.artifact is not None
    assert second.artifact is None
    assert ChangeSeedGapCode.PATH_REPLAY_FAILED in _codes(second)


@pytest.mark.parametrize(
    ("anchors", "locator", "expected"),
    [
        (2, "src/alpha.cls", ChangeSeedGapCode.AMBIGUOUS_CHANGED_ARTIFACT),
        (1, "src/other.cls", ChangeSeedGapCode.UNMAPPED_CHANGED_ARTIFACT),
    ],
)
def test_unmapped_and_ambiguous_changes_fail_closed(
    tmp_path: Path, anchors: int, locator: str, expected: ChangeSeedGapCode
) -> None:
    repository = _repository(tmp_path)
    system = _graph_system(anchors=anchors, locator=locator)
    result = compile_change_seed_mapping(_inputs(repository, system), _policy())

    assert result.artifact is None
    assert expected in _codes(result)


def test_delete_requires_base_graph_or_tombstone_receipt(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.cls").unlink()
    system = _graph_system()
    result = compile_change_seed_mapping(_inputs(repository, system), _policy())

    assert result.artifact is None
    assert ChangeSeedGapCode.DELETED_ARTIFACT_REQUIRES_BASE_GRAPH in _codes(result)


def test_repository_mutation_after_capture_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    (repository / "src" / "alpha.cls").write_text("mutated again\n", encoding="utf-8")

    result = compile_change_seed_mapping(inputs, _policy())

    assert result.artifact is None
    assert ChangeSeedGapCode.CHANGE_CAPTURE_REPLAY_FAILED in _codes(result)


def test_rehashed_policy_mutation_does_not_bypass_external_pin(tmp_path: Path) -> None:
    document = json.loads(
        (ROOT / "config" / "change-seed-policy.json").read_text(encoding="utf-8")
    )
    document["maximumChanges"] += 1
    document["sha256"] = contract_sha256(document)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(Exception, match="externally pinned"):
        load_change_seed_policy(path, implementation_root=ROOT)


def test_cross_project_graph_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    system["graph"] = system["graph"].model_copy(update={"project_id": "other-project"})
    result = compile_change_seed_mapping(_inputs(repository, system), _policy())

    assert result.artifact is None
    assert ChangeSeedGapCode.PROJECT_SCOPE_MISMATCH in _codes(result)


@pytest.mark.parametrize(
    ("locators", "expected"),
    [
        (
            ("src/alpha.cls", "SRC/ALPHA.cls"),
            ChangeSeedGapCode.LOCATOR_ALIAS_COLLISION,
        ),
        (
            ("src/alpha.cls", "../unsafe.cls"),
            ChangeSeedGapCode.UNSAFE_GRAPH_LOCATOR,
        ),
    ],
)
def test_graph_locator_aliases_and_unsafe_values_block_the_aggregate(
    tmp_path: Path,
    locators: tuple[str, ...],
    expected: ChangeSeedGapCode,
) -> None:
    repository = _repository(tmp_path)
    system = _graph_system(locators=locators)
    result = compile_change_seed_mapping(_inputs(repository, system), _policy())

    assert result.artifact is None
    assert expected in _codes(result)


def test_tampered_change_manifest_is_replayed_from_repository(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    tampered = inputs.candidate.model_copy(update={"manifest_sha256": "0" * 64})
    from dataclasses import replace

    result = compile_change_seed_mapping(replace(inputs, candidate=tampered), _policy())

    assert result.artifact is None
    assert ChangeSeedGapCode.CHANGE_CAPTURE_REPLAY_FAILED in _codes(result)


def test_tampered_complete_path_candidate_is_replayed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    tampered = inputs.complete_path_candidate.model_copy(
        update={"artifact_sha256": "0" * 64}
    )
    from dataclasses import replace

    result = compile_change_seed_mapping(
        replace(inputs, complete_path_candidate=tampered), _policy()
    )

    assert result.artifact is None
    assert ChangeSeedGapCode.PATH_REPLAY_FAILED in _codes(result)


def test_runtime_policy_model_mutation_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    altered = _policy().model_copy(update={"maximum_changes": 1})

    result = compile_change_seed_mapping(_inputs(repository, system), altered)

    assert result.artifact is None
    assert ChangeSeedGapCode.POLICY_ROOT_MISMATCH in _codes(result)


def test_mapping_artifact_consumer_replays_and_rejects_tamper(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    compiled = compile_change_seed_mapping(inputs, _policy())
    assert compiled.artifact is not None

    refreshed = verify_change_seed_mapping(compiled.artifact, inputs, _policy())
    tampered = compiled.artifact.model_copy(update={"seed_ids": ("invented",)})
    rejected = verify_change_seed_mapping(tampered, inputs, _policy())

    assert refreshed.artifact is not None
    assert rejected.artifact is None
    assert ChangeSeedGapCode.MAPPING_ARTIFACT_TAMPERED in _codes(rejected)


def test_mapping_artifact_consumer_rejects_future_and_expired_receipts(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    compiled = compile_change_seed_mapping(inputs, _policy())
    assert compiled.artifact is not None
    future = compiled.artifact.model_copy(update={"evaluated_at": "2026-09-09T09:00:00Z"})
    expired = compiled.artifact.model_copy(update={"valid_until": "2026-09-09T08:01:00Z"})

    future_result = verify_change_seed_mapping(future, inputs, _policy())
    expired_result = verify_change_seed_mapping(expired, inputs, _policy())

    assert ChangeSeedGapCode.MAPPING_ARTIFACT_FROM_FUTURE in _codes(future_result)
    assert ChangeSeedGapCode.MAPPING_ARTIFACT_EXPIRED in _codes(expired_result)
    assert future_result.artifact is None
    assert expired_result.artifact is None


def test_still_valid_historical_mapping_refreshes_temporal_nested_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    compiled = compile_change_seed_mapping(inputs, _policy())
    assert compiled.artifact is not None

    later = T0 + timedelta(minutes=2)
    monkeypatch.setattr(change_verification_module, "_utc_now", lambda: later)
    monkeypatch.setattr(path_replay_module, "_utc_now", lambda: later)
    monkeypatch.setattr(change_seed_module, "_utc_now", lambda: later)
    refreshed = verify_change_seed_mapping(compiled.artifact, inputs, _policy())

    assert refreshed.artifact is not None
    assert refreshed.artifact.evaluated_at == "2026-09-09T08:02:00Z"
    assert (
        refreshed.artifact.verified_change_manifest_sha256
        != compiled.artifact.verified_change_manifest_sha256
    )
    assert (
        refreshed.artifact.affected_path_identity_sha256s
        == compiled.artifact.affected_path_identity_sha256s
    )


@pytest.mark.parametrize("target", ["change-root", "path-root", "raw-path"])
def test_rehashed_historical_audit_root_mutation_is_rejected(
    tmp_path: Path, target: str
) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)
    compiled = compile_change_seed_mapping(inputs, _policy())
    assert compiled.artifact is not None
    body = compiled.artifact.model_dump(mode="json")
    body.pop("artifact_sha256")
    if target == "change-root":
        body["verified_change_manifest_sha256"] = "1" * 64
    elif target == "path-root":
        body["complete_path_artifact_sha256"] = "1" * 64
    else:
        body["affected_path_sha256s"] = ["1" * 64]
        binding = body["bindings"][0]
        binding.pop("binding_sha256")
        binding["affected_path_sha256s"] = ["1" * 64]
        binding["binding_sha256"] = stable_sha256(binding)
    candidate = ChangeSeedMappingArtifact.model_validate(
        {**body, "artifact_sha256": stable_sha256(body)}
    )

    result = verify_change_seed_mapping(candidate, inputs, _policy())

    assert result.artifact is None
    assert ChangeSeedGapCode.MAPPING_ARTIFACT_TAMPERED in _codes(result)


def test_forged_producer_subclass_cannot_supply_change_authority(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    system = _graph_system()
    inputs = _inputs(repository, system)

    class ForgedProducer(LocalGitChangeProducer):
        pass

    from dataclasses import replace

    forged = ForgedProducer(
        inputs.producer.project_id,
        inputs.producer.expected_repository_root,
        inputs.producer.policy,
    )
    result = compile_change_seed_mapping(replace(inputs, producer=forged), _policy())

    assert result.artifact is None
    assert ChangeSeedGapCode.CHANGE_CAPTURE_REPLAY_FAILED in _codes(result)
