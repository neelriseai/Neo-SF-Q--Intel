from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import neo_sf_q_intel.path_replay as path_replay_module
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.ontology import contract_sha256
from neo_sf_q_intel.path_replay import (
    DEFAULT_PATH_REPLAY_POLICY_SHA256,
    CompleteGraphPathArtifact,
    PathReplayContractError,
    PathReplayGapCode,
    compile_complete_graph_path_replay,
    load_complete_path_replay_policy,
    verify_complete_graph_path_replay,
)
from neo_sf_q_intel.propagation import (
    PropagationPolicy,
    TraversalDirection,
    load_propagation_policy,
    traverse_propagation,
)
from tests.support.path_replay_fixture import (
    OBSERVED,
    ROOT,
    build_system,
    compile_envelopes,
    replay_input,
)

PATH_POLICY_PATH = ROOT / "config" / "complete-path-replay-policy.json"


@pytest.fixture(autouse=True)
def _fixed_current_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        path_replay_module, "_utc_now", lambda: OBSERVED + timedelta(minutes=1)
    )


def _path_policy(system: dict):
    propagation = load_propagation_policy(
        ROOT / "config" / "propagation-policy.json", system["ontology"]
    )
    policy = load_complete_path_replay_policy(PATH_POLICY_PATH, propagation)
    return propagation, policy


def _codes(result) -> set[PathReplayGapCode]:
    return {gap.code for gap in result.gaps}


def _rehashed_candidate(original, mutate):
    body = original.model_dump(mode="json")
    body.pop("artifact_sha256")
    mutate(body)
    for path in body["paths"]:
        path.pop("path_sha256", None)
        path["path_sha256"] = stable_sha256(path)
    scope = body["required_scope"]
    scope.pop("scope_sha256", None)
    scope["required_path_sha256s"] = sorted(
        path["path_sha256"] for path in body["paths"]
    )
    scope["path_count"] = len(body["paths"])
    scope["hop_count"] = sum(len(path["hops"]) for path in body["paths"])
    scope["path_seed_ids"] = sorted({path["seed_id"] for path in body["paths"]})
    scope["zero_material_path_seed_ids"] = sorted(
        set(scope["seed_ids"]) - set(scope["path_seed_ids"])
    )
    scope["material_output_ids"] = sorted(
        {path["target_id"] for path in body["paths"]}
    )
    scope["required_edge_ids"] = sorted(
        {hop["edge_id"] for path in body["paths"] for hop in path["hops"]}
    )
    scope["scope_sha256"] = stable_sha256(scope)
    return CompleteGraphPathArtifact.model_validate(
        {**body, "artifact_sha256": stable_sha256(body)}
    )


def _compile_result(system: dict):
    propagation, policy = _path_policy(system)
    result = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        replay_input(system),
    )
    return propagation, policy, result


def test_policy_is_self_hashed_externally_pinned_and_binds_r01_roots(
    tmp_path: Path,
) -> None:
    system = build_system()
    propagation, policy = _path_policy(system)

    assert policy.sha256 == DEFAULT_PATH_REPLAY_POLICY_SHA256
    assert policy.propagation_policy.policy_sha256 == propagation.sha256
    assert policy.trusted_edge_policy.policy_sha256 == system["policy"].sha256
    assert policy.extractor_registry.registry_sha256 == system["registry"].sha256

    changed = deepcopy(policy.model_dump(mode="json", by_alias=True))
    changed["maximumRequiredPaths"] += 1
    path = tmp_path / "changed.json"
    path.write_text(__import__("json").dumps(changed), encoding="utf-8")
    with pytest.raises(PathReplayContractError):
        load_complete_path_replay_policy(path, propagation)


def test_complete_path_scope_is_runtime_derived_and_deterministic() -> None:
    system = build_system()
    propagation, policy, first = _compile_result(system)
    second = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        replay_input(
            system,
            envelopes=tuple(reversed(compile_envelopes(system))),
            artifacts=tuple(reversed(replay_input(system).artifact_contents)),
        ),
    )

    assert first == second
    assert first.local_edge_envelope_path_coverage_complete is True
    assert first.release_eligible is False
    assert first.artifact is not None
    scope = first.artifact.required_scope
    assert scope.seed_ids == ("node-a", "node-b", "node-c")
    assert scope.material_output_ids == ("node-a", "node-b", "node-c")
    assert scope.required_edge_ids == ("edge-a", "edge-b")
    assert scope.path_count == 6
    assert scope.hop_count == 8
    assert all(path.local_edge_envelope_complete for path in first.artifact.paths)
    assert _codes(first) == {
        PathReplayGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED,
        PathReplayGapCode.UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED,
    }


@pytest.mark.parametrize("required", [("edge-a",), ("edge-a", "edge-b", "extra")])
def test_caller_cannot_narrow_or_expand_the_required_edge_scope(required) -> None:
    system = build_system()
    propagation, policy = _path_policy(system)
    result = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        replay_input(system, required_edge_ids=required),
    )

    assert result.artifact is None
    assert PathReplayGapCode.EDGE_SCOPE_MISMATCH in _codes(result)


def test_extra_valid_envelope_is_not_accepted_outside_the_derived_edge_union() -> None:
    system = build_system()
    propagation, policy = _path_policy(system)
    envelopes = compile_envelopes(system)
    result = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        replay_input(system, envelopes=(*envelopes, envelopes[0])),
    )

    assert result.artifact is None
    assert PathReplayGapCode.EDGE_REPLAY_INCOMPLETE in _codes(result)


def test_current_clock_rejects_expired_envelopes_even_if_replay_input_is_historical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = build_system()
    propagation, policy = _path_policy(system)
    historical = replay_input(system)
    assert historical.evaluated_at == OBSERVED

    monkeypatch.setattr(
        path_replay_module, "_utc_now", lambda: OBSERVED + timedelta(hours=2)
    )
    result = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        historical,
    )

    assert result.artifact is None
    assert PathReplayGapCode.EDGE_REPLAY_INCOMPLETE in _codes(result)


def test_policy_rotation_cannot_substitute_r01_trust_roots() -> None:
    system = build_system()
    propagation, policy = _path_policy(system)
    replay = replay_input(system)
    forged = replace(replay, expected_policy_sha256="0" * 64)

    result = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        forged,
    )

    assert result.artifact is None
    assert PathReplayGapCode.PATH_ROOT_OR_POLICY_MISMATCH in _codes(result)


def test_removed_reordered_reversed_and_rehashed_candidate_paths_fail_closed() -> None:
    system = build_system()
    propagation, policy, compiled = _compile_result(system)
    assert compiled.artifact is not None
    original = compiled.artifact

    candidates = [
        CompleteGraphPathArtifact.model_construct(
            **{**original.__dict__, "paths": original.paths[:-1]}
        ),
        CompleteGraphPathArtifact.model_construct(
            **{**original.__dict__, "paths": tuple(reversed(original.paths))}
        ),
    ]
    first_path = original.paths[0]
    first_hop = first_path.hops[0]
    reversed_hop = first_hop.model_copy(
        update={
            "direction": (
                TraversalDirection.TARGET_TO_SOURCE
                if first_hop.direction.value == "SOURCE_TO_TARGET"
                else TraversalDirection.SOURCE_TO_TARGET
            )
        }
    )
    candidates.append(
        CompleteGraphPathArtifact.model_construct(
            **{
                **original.__dict__,
                "paths": (
                    first_path.model_construct(
                        **{**first_path.__dict__, "hops": (reversed_hop, *first_path.hops[1:])}
                    ),
                    *original.paths[1:],
                ),
            }
        )
    )

    for candidate in candidates:
        result = verify_complete_graph_path_replay(
            candidate,
            system["graph"],
            propagation,
            policy,
            replay_input(system),
        )
        assert result.artifact is None
        assert PathReplayGapCode.PATH_ARTIFACT_STRUCTURALLY_INVALID in _codes(result)


def test_valid_candidate_is_replayed_not_trusted_by_its_self_hash() -> None:
    system = build_system()
    propagation, policy, compiled = _compile_result(system)
    assert compiled.artifact is not None

    verified = verify_complete_graph_path_replay(
        compiled.artifact,
        system["graph"],
        propagation,
        policy,
        replay_input(system),
    )

    assert verified == compiled


def test_still_fresh_historical_candidate_returns_a_current_refreshed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = build_system()
    propagation, policy, compiled = _compile_result(system)
    assert compiled.artifact is not None
    old_sha = compiled.artifact.artifact_sha256

    monkeypatch.setattr(
        path_replay_module, "_utc_now", lambda: OBSERVED + timedelta(minutes=2)
    )
    refreshed = verify_complete_graph_path_replay(
        compiled.artifact,
        system["graph"],
        propagation,
        policy,
        replay_input(system),
    )

    assert refreshed.artifact is not None
    assert refreshed.artifact.artifact_sha256 != old_sha
    assert refreshed.artifact.evaluated_at == "2026-01-01T00:02:00Z"


def test_fully_rehashed_historical_hop_provenance_tamper_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = build_system()
    propagation, policy, compiled = _compile_result(system)
    assert compiled.artifact is not None

    def mutate(body):
        body["paths"][0]["hops"][0]["source_artifact_sha256"] = "0" * 64

    tampered = _rehashed_candidate(compiled.artifact, mutate)
    monkeypatch.setattr(
        path_replay_module, "_utc_now", lambda: OBSERVED + timedelta(minutes=2)
    )
    result = verify_complete_graph_path_replay(
        tampered,
        system["graph"],
        propagation,
        policy,
        replay_input(system),
    )

    assert result.artifact is None
    assert PathReplayGapCode.PATH_HOP_TAMPERED in _codes(result)


def test_nonempty_seed_scope_with_zero_material_paths_is_incomplete() -> None:
    system = build_system((), isolated_node_ids=("isolated",))
    propagation, policy = _path_policy(system)
    result = compile_complete_graph_path_replay(
        system["graph"], propagation, policy, replay_input(system)
    )

    assert result.artifact is None
    assert PathReplayGapCode.NO_REQUIRED_MATERIAL_PATH_SCOPE in _codes(result)


def test_isolated_seed_is_explicit_without_narrowing_connected_paths() -> None:
    system = build_system(isolated_node_ids=("isolated",))
    _, _, result = _compile_result(system)

    assert result.artifact is not None
    assert result.artifact.required_scope.seed_ids == (
        "isolated",
        "node-a",
        "node-b",
        "node-c",
    )
    assert result.artifact.required_scope.zero_material_path_seed_ids == ("isolated",)


def test_structural_capacity_truncation_is_blocking() -> None:
    system = build_system()
    propagation, path_policy = _path_policy(system)
    propagation_body = propagation.model_dump(mode="json", by_alias=True)
    propagation_body["maximumTotalPaths"] = 1
    propagation_body["sha256"] = contract_sha256(propagation_body)
    bounded = PropagationPolicy.model_validate(propagation_body)
    path_body = path_policy.model_dump(mode="json", by_alias=True)
    path_body["propagationPolicy"] = {
        "policyId": bounded.policy_id,
        "policyVersion": bounded.policy_version,
        "policySha256": bounded.sha256,
    }
    path_body["sha256"] = contract_sha256(path_body)
    bounded_path = path_policy.__class__.model_validate(path_body)

    result = compile_complete_graph_path_replay(
        system["graph"],
        bounded,
        bounded_path,
        replay_input(system),
        expected_path_policy_sha256=bounded_path.sha256,
    )

    assert result.artifact is None
    assert PathReplayGapCode.PROPAGATION_SCOPE_INCOMPLETE in _codes(result)


def test_path_replay_failure_does_not_remove_legacy_analysis_paths() -> None:
    system = build_system()
    propagation, policy = _path_policy(system)
    legacy = traverse_propagation(system["graph"], ["node-a"], propagation)
    incomplete = compile_complete_graph_path_replay(
        system["graph"],
        propagation,
        policy,
        replay_input(system, envelopes=()),
    )

    assert legacy.paths
    assert incomplete.artifact is None
    assert PathReplayGapCode.EDGE_REPLAY_INCOMPLETE in _codes(incomplete)


def test_naive_time_source_fails_with_a_typed_release_only_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = build_system()
    propagation, policy = _path_policy(system)
    monkeypatch.setattr(
        path_replay_module,
        "_utc_now",
        lambda: OBSERVED.replace(tzinfo=None),
    )

    result = compile_complete_graph_path_replay(
        system["graph"], propagation, policy, replay_input(system)
    )

    assert result.artifact is None
    assert PathReplayGapCode.TIME_AUTHORITY_UNAVAILABLE in _codes(result)


def test_renamed_diamond_cycle_and_input_permutation_keep_complete_simple_scope() -> None:
    topology = (
        ("edge-ab", "node-a", "node-b"),
        ("edge-ac", "node-a", "node-c"),
        ("edge-bd", "node-b", "node-d"),
        ("edge-cd", "node-c", "node-d"),
        ("edge-da", "node-d", "node-a"),
    )
    renamed = tuple(
        (
            edge.replace("edge", "link"),
            source.replace("node", "unit"),
            target.replace("node", "unit"),
        )
        for edge, source, target in topology
    )

    results = []
    for specs in (topology, tuple(reversed(topology)), renamed):
        system = build_system(specs)
        _, _, result = _compile_result(system)
        assert result.artifact is not None
        artifact = result.artifact
        assert artifact.local_edge_envelope_path_coverage_complete is True
        assert artifact.required_scope.required_edge_ids == tuple(
            sorted(edge for edge, _, _ in specs)
        )
        identities = []
        for path in artifact.paths:
            visited = [path.seed_id, *(hop.traversal_to_id for hop in path.hops)]
            assert len(visited) == len(set(visited))
            for hop in path.hops:
                expected = (
                    (hop.edge_source_id, hop.edge_target_id)
                    if hop.direction is TraversalDirection.SOURCE_TO_TARGET
                    else (hop.edge_target_id, hop.edge_source_id)
                )
                assert (hop.traversal_from_id, hop.traversal_to_id) == expected
            identities.append(
                (
                    path.seed_id,
                    path.target_id,
                    tuple(
                        (hop.edge_id, hop.direction.value) for hop in path.hops
                    ),
                )
            )
        assert len(identities) == len(set(identities))
        results.append((artifact, tuple(identities)))

    assert results[0][1] == results[1][1]
    assert results[0][0].required_scope.path_count == results[2][0].required_scope.path_count
    assert results[0][0].required_scope.hop_count == results[2][0].required_scope.hop_count
