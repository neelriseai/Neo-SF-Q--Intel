import hashlib
import json
from pathlib import Path

import pytest

from neo_sf_q_intel.ontology import (
    NormalizedEdge,
    NormalizedGraph,
    NormalizedNode,
    SourceEvidenceState,
    contract_sha256,
    load_canonical_ontology,
)
from neo_sf_q_intel.propagation import (
    PolicyContractError,
    PropagationEvaluationError,
    RiskLevel,
    TraversalDirection,
    evaluate_analysis_risk,
    load_analysis_risk_policy,
    load_propagation_policy,
    traverse_propagation,
)

ROOT = Path(__file__).parents[1]
ONTOLOGY_PATH = ROOT / "config" / "ontology" / "canonical-ontology.json"
PROPAGATION_PATH = ROOT / "config" / "propagation-policy.json"
RISK_PATH = ROOT / "config" / "analysis-risk-policy.json"
SOURCE_HASH = "a" * 64


def _contracts():
    ontology = load_canonical_ontology(ONTOLOGY_PATH)
    propagation = load_propagation_policy(PROPAGATION_PATH, ontology)
    risk = load_analysis_risk_policy(RISK_PATH, ontology, propagation)
    return ontology, propagation, risk


def _node(ontology, node_id: str, canonical_class: str) -> NormalizedNode:
    node_class = ontology.nodes_by_id[canonical_class]
    return NormalizedNode(
        node_id=node_id,
        raw_kind=f"fixture.{canonical_class}",
        canonical_class=canonical_class,
        role=node_class.role,
        materiality=node_class.materiality,
        label=node_id,
        source="fixtures/graph.json",
        raw_evidence_state="CONFIRMED",
        evidence_state=SourceEvidenceState.CONFIRMED,
        source_snapshot="snapshot-1",
        source_hash=SOURCE_HASH,
        extractor_id="deterministic-fixture-parser",
    )


def _edge(
    ontology,
    edge_id: str,
    source_id: str,
    relation: str,
    target_id: str,
    *,
    evidence_state: SourceEvidenceState = SourceEvidenceState.CONFIRMED,
) -> NormalizedEdge:
    return NormalizedEdge(
        edge_id=edge_id,
        source_id=source_id,
        target_id=target_id,
        raw_relation=f"fixture.{relation}",
        canonical_relation=relation,
        materiality=ontology.relations_by_id[relation].materiality,
        source="fixtures/graph.json",
        raw_evidence_state=evidence_state.value,
        evidence_state=evidence_state,
        source_snapshot="snapshot-1",
        source_hash=SOURCE_HASH,
        extractor_id="deterministic-fixture-parser",
    )


def _graph(ontology, nodes, edges) -> NormalizedGraph:
    ordered_nodes = sorted(nodes, key=lambda item: item.node_id)
    ordered_edges = sorted(edges, key=lambda item: item.edge_id)
    body = {
        "project_id": "fixture-project",
        "source_graph_sha256": SOURCE_HASH,
        "ontology_id": ontology.ontology_id,
        "ontology_version": ontology.ontology_version,
        "ontology_sha256": ontology.sha256,
        "profile_id": "fixture-profile",
        "profile_version": "1.0.0",
        "profile_sha256": "b" * 64,
        "source_snapshot": "snapshot-1",
        "nodes": [item.model_dump(mode="json") for item in ordered_nodes],
        "edges": [item.model_dump(mode="json") for item in ordered_edges],
        "mapping_gaps": [],
        "trust_gaps": [],
    }
    graph_sha256 = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return NormalizedGraph(
        project_id="fixture-project",
        source_graph_sha256=SOURCE_HASH,
        ontology_id=ontology.ontology_id,
        ontology_version=ontology.ontology_version,
        ontology_sha256=ontology.sha256,
        profile_id="fixture-profile",
        profile_version="1.0.0",
        profile_sha256="b" * 64,
        source_snapshot="snapshot-1",
        nodes=tuple(ordered_nodes),
        edges=tuple(ordered_edges),
        mapping_gaps=(),
        trust_gaps=(),
        graph_sha256=graph_sha256,
    )


def _write_rehashed(path: Path, document: dict) -> None:
    document["sha256"] = contract_sha256(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def _hash_document(document: dict) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_policies_are_separately_versioned_hashed_and_exhaustive() -> None:
    ontology, propagation, risk = _contracts()

    assert set(propagation.rules_by_relation) == set(ontology.relations_by_id)
    assert set(risk.relation_score_map) == set(ontology.relations_by_id)
    assert risk.propagation_policy.policy_sha256 == propagation.sha256
    assert "severity" not in json.loads(RISK_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("policy_path", [PROPAGATION_PATH, RISK_PATH])
def test_policy_tampering_is_rejected(tmp_path: Path, policy_path: Path) -> None:
    ontology, propagation, _ = _contracts()
    document = json.loads(policy_path.read_text(encoding="utf-8"))
    document["policyVersion"] = "9.9.9"
    tampered = tmp_path / policy_path.name
    tampered.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(PolicyContractError, match="digest"):
        if policy_path == PROPAGATION_PATH:
            load_propagation_policy(tampered, ontology)
        else:
            load_analysis_risk_policy(tampered, ontology, propagation)


def test_rehashed_illegal_route_and_incomplete_relation_table_are_rejected(
    tmp_path: Path,
) -> None:
    ontology, _, _ = _contracts()
    document = json.loads(PROPAGATION_PATH.read_text(encoding="utf-8"))
    document["relationRules"][0]["routes"][0]["edgeSourceRoles"] = ["ACTOR"]
    illegal = tmp_path / "illegal.json"
    _write_rehashed(illegal, document)
    with pytest.raises(PolicyContractError, match="illegal role pair"):
        load_propagation_policy(
            illegal, ontology, expected_sha256=document["sha256"]
        )

    document = json.loads(PROPAGATION_PATH.read_text(encoding="utf-8"))
    document["relationRules"].pop()
    incomplete = tmp_path / "incomplete.json"
    _write_rehashed(incomplete, document)
    with pytest.raises(PolicyContractError, match="exhaustive"):
        load_propagation_policy(
            incomplete, ontology, expected_sha256=document["sha256"]
        )

    document = json.loads(PROPAGATION_PATH.read_text(encoding="utf-8"))
    document["relationRules"][0]["routes"] = []
    empty = tmp_path / "empty.json"
    _write_rehashed(empty, document)
    with pytest.raises(PolicyContractError, match="at least 1 item"):
        load_propagation_policy(empty, ontology, expected_sha256=document["sha256"])


def test_paired_policy_rehash_does_not_bypass_external_root_pins(tmp_path: Path) -> None:
    ontology, _, _ = _contracts()
    propagation_doc = json.loads(PROPAGATION_PATH.read_text(encoding="utf-8"))
    propagation_doc["maximumPathsPerTarget"] -= 1
    propagation_path = tmp_path / "propagation.json"
    _write_rehashed(propagation_path, propagation_doc)
    with pytest.raises(PolicyContractError, match="trusted digest"):
        load_propagation_policy(propagation_path, ontology)

    propagation = load_propagation_policy(
        propagation_path, ontology, expected_sha256=propagation_doc["sha256"]
    )
    risk_doc = json.loads(RISK_PATH.read_text(encoding="utf-8"))
    risk_doc["propagationPolicy"]["policySha256"] = propagation.sha256
    risk_path = tmp_path / "risk.json"
    _write_rehashed(risk_path, risk_doc)
    with pytest.raises(PolicyContractError, match="trusted digest"):
        load_analysis_risk_policy(risk_path, ontology, propagation)


def test_inverse_traversal_is_not_inferred_when_policy_only_allows_forward() -> None:
    ontology, policy, _ = _contracts()
    requirement = _node(ontology, "requirement", "requirement")
    code = _node(ontology, "code", "code")
    edge = _edge(ontology, "edge", "requirement", "implements", "code")
    graph = _graph(ontology, [requirement, code], [edge])

    forward = traverse_propagation(graph, ["requirement"], policy)
    inverse = traverse_propagation(graph, ["code"], policy)

    assert [(path.seed_id, path.target_id) for path in forward.paths] == [
        ("requirement", "code")
    ]
    assert inverse.paths == ()


def test_cycle_paths_are_finite_and_never_repeat_a_node() -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b", "c")]
    edges = [
        _edge(ontology, "ab", "a", "revalidates", "b"),
        _edge(ontology, "bc", "b", "revalidates", "c"),
        _edge(ontology, "ca", "c", "revalidates", "a"),
    ]

    result = traverse_propagation(_graph(ontology, nodes, edges), ["a"], policy)

    assert result.paths
    assert len(result.paths) < 20
    for path in result.paths:
        visited = [path.seed_id, *(hop.traversal_to_id for hop in path.hops)]
        assert len(visited) == len(set(visited))


def test_diamond_preserves_both_ordered_full_path_receipts() -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b", "c", "d")]
    edges = [
        _edge(ontology, "ab", "a", "revalidates", "b"),
        _edge(ontology, "ac", "a", "revalidates", "c"),
        _edge(ontology, "bd", "b", "revalidates", "d"),
        _edge(ontology, "cd", "c", "revalidates", "d"),
    ]

    result = traverse_propagation(_graph(ontology, nodes, edges), ["a"], policy)
    paths_to_d = [path for path in result.paths if path.target_id == "d"]

    assert [[hop.edge_id for hop in path.hops] for path in paths_to_d] == [
        ["ab", "bd"],
        ["ac", "cd"],
    ]
    assert all(path.source_snapshot == "snapshot-1" for path in paths_to_d)
    assert all(
        hop.position == position
        and hop.source_hash == SOURCE_HASH
        and hop.source_artifact == "fixtures/graph.json"
        and hop.extractor_id == "deterministic-fixture-parser"
        for path in paths_to_d
        for position, hop in enumerate(path.hops, start=1)
    )


def test_traversal_and_receipt_hashes_are_input_order_independent() -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b", "c", "d")]
    edges = [
        _edge(ontology, "ab", "a", "revalidates", "b"),
        _edge(ontology, "ac", "a", "revalidates", "c"),
        _edge(ontology, "bd", "b", "revalidates", "d"),
        _edge(ontology, "cd", "c", "revalidates", "d"),
    ]

    first = traverse_propagation(_graph(ontology, nodes, edges), ["c", "a"], policy)
    second = traverse_propagation(
        _graph(ontology, list(reversed(nodes)), list(reversed(edges))),
        ["a", "c", "a"],
        policy,
    )

    assert first == second
    assert first.result_sha256 == second.result_sha256
    assert first.seed_ids == ("a", "c")


def test_multiple_seeds_keep_distinct_receipts_for_the_same_target() -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b", "c")]
    edges = [
        _edge(ontology, "ac", "a", "revalidates", "c"),
        _edge(ontology, "bc", "b", "revalidates", "c"),
    ]

    result = traverse_propagation(_graph(ontology, nodes, edges), ["b", "a"], policy)
    paths_to_c = [path for path in result.paths if path.target_id == "c"]

    assert {(path.seed_id, path.target_id) for path in paths_to_c} == {
        ("a", "c"),
        ("b", "c"),
    }
    assert len({path.path_sha256 for path in paths_to_c}) == 2


def test_total_path_budget_bounds_frontier_enumeration(tmp_path: Path) -> None:
    ontology, _, _ = _contracts()
    policy_doc = json.loads(PROPAGATION_PATH.read_text(encoding="utf-8"))
    policy_doc["maximumTotalPaths"] = 2
    policy_path = tmp_path / "bounded-propagation.json"
    _write_rehashed(policy_path, policy_doc)
    policy = load_propagation_policy(
        policy_path, ontology, expected_sha256=policy_doc["sha256"]
    )
    nodes = [_node(ontology, item, "code") for item in ("a", "b", "c", "d")]
    edges = [
        _edge(ontology, f"a{target}", "a", "revalidates", target)
        for target in ("b", "c", "d")
    ]

    result = traverse_propagation(_graph(ontology, nodes, edges), ["a"], policy)

    assert len(result.paths) == 2
    assert "TOTAL_PATH_LIMIT_REACHED" in {gap.code for gap in result.gaps}


def test_relation_specific_depth_stops_an_otherwise_legal_second_hop() -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "user-interface") for item in ("a", "b", "c")]
    edges = [
        _edge(ontology, "ab", "a", "contains", "b"),
        _edge(ontology, "bc", "b", "contains", "c"),
    ]

    result = traverse_propagation(_graph(ontology, nodes, edges), ["a"], policy)

    assert {path.target_id for path in result.paths} == {"b"}


def test_unconfirmed_or_incomplete_edge_cannot_create_a_path_receipt() -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b")]
    edge = _edge(
        ontology,
        "ab",
        "a",
        "revalidates",
        "b",
        evidence_state=SourceEvidenceState.INFERRED,
    )

    result = traverse_propagation(_graph(ontology, nodes, [edge]), ["a"], policy)

    assert result.paths == ()
    assert [gap.code for gap in result.gaps] == ["EDGE_EVIDENCE_STATE_REJECTED"]


@pytest.mark.parametrize("untrusted_id", ["a", "b"])
def test_unverified_seed_or_target_cannot_create_a_path_receipt(untrusted_id: str) -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b")]
    nodes = [
        node.model_copy(
            update={
                "evidence_state": SourceEvidenceState.UNVERIFIED,
                "source": None,
                "source_snapshot": None,
                "source_hash": None,
                "extractor_id": None,
            }
        )
        if node.node_id == untrusted_id
        else node
        for node in nodes
    ]

    result = traverse_propagation(
        _graph(ontology, nodes, [_edge(ontology, "ab", "a", "revalidates", "b")]),
        ["a"],
        policy,
    )

    assert result.paths == ()
    expected = (
        "SEED_PROVENANCE_INCOMPLETE"
        if untrusted_id == "a"
        else "NODE_PROVENANCE_INCOMPLETE"
    )
    assert expected in {gap.code for gap in result.gaps}


@pytest.mark.parametrize("untrusted_id", ["a", "b"])
def test_seed_or_target_with_foreign_source_hash_cannot_create_path(
    untrusted_id: str,
) -> None:
    ontology, policy, _ = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b")]
    nodes = [
        node.model_copy(update={"source_hash": "d" * 64})
        if node.node_id == untrusted_id
        else node
        for node in nodes
    ]
    graph = _graph(
        ontology,
        nodes,
        [_edge(ontology, "ab", "a", "revalidates", "b")],
    )

    result = traverse_propagation(graph, ["a"], policy)

    assert result.paths == ()
    assert "NODE_SOURCE_HASH_MISMATCH" in {gap.code for gap in result.gaps}


def test_edge_with_foreign_source_hash_cannot_create_path() -> None:
    ontology, policy, _ = _contracts()
    edge = _edge(ontology, "ab", "a", "revalidates", "b").model_copy(
        update={"source_hash": "e" * 64}
    )
    graph = _graph(
        ontology,
        [_node(ontology, "a", "code"), _node(ontology, "b", "code")],
        [edge],
    )

    result = traverse_propagation(graph, ["a"], policy)

    assert result.paths == ()
    assert "EDGE_SOURCE_HASH_MISMATCH" in {gap.code for gap in result.gaps}


def test_intermediate_node_with_foreign_source_hash_stops_multi_hop_path() -> None:
    ontology, policy, _ = _contracts()
    nodes = [
        _node(ontology, "a", "code"),
        _node(ontology, "b", "code").model_copy(
            update={"source_hash": "d" * 64}
        ),
        _node(ontology, "c", "code"),
    ]
    graph = _graph(
        ontology,
        nodes,
        [
            _edge(ontology, "ab", "a", "revalidates", "b"),
            _edge(ontology, "bc", "b", "revalidates", "c"),
        ],
    )

    result = traverse_propagation(graph, ["a"], policy)

    assert result.paths == ()
    assert {
        (gap.code, gap.entity_id) for gap in result.gaps
    } == {("NODE_SOURCE_HASH_MISMATCH", "b")}


def test_conflicting_path_levels_are_retained_and_highest_score_wins() -> None:
    ontology, propagation_policy, risk_policy = _contracts()
    security = _node(ontology, "security", "security-policy")
    artifact = _node(ontology, "artifact", "source-artifact")
    target = _node(ontology, "target", "code")
    edges = [
        _edge(ontology, "auth", "security", "authorizes", "target"),
        _edge(ontology, "declared", "target", "declared-in", "artifact"),
    ]
    graph = _graph(ontology, [security, artifact, target], edges)
    propagation = traverse_propagation(
        graph,
        ["security", "artifact"],
        propagation_policy,
    )

    result = evaluate_analysis_risk(
        propagation,
        {
            "target": {
                "businessCriticality": "MEDIUM",
                "changeIntent": "PLANNED_CHANGE",
                "securityReach": "NONE",
            }
        },
        risk_policy,
        expected_graph=graph,
    )
    target_result = next(item for item in result.assessments if item.target_id == "target")

    assert len(target_result.path_contributions) == 2
    assert {item.level for item in target_result.path_contributions} == {
        RiskLevel.HIGH,
        RiskLevel.LOW,
    }
    assert target_result.level is RiskLevel.HIGH
    assert target_result.conflicting_path_levels is True


def test_missing_risk_factor_abstains_without_default_severity() -> None:
    ontology, propagation_policy, risk_policy = _contracts()
    nodes = [_node(ontology, item, "code") for item in ("a", "b")]
    graph = _graph(
        ontology,
        nodes,
        [_edge(ontology, "ab", "a", "revalidates", "b")],
    )
    propagation = traverse_propagation(
        graph,
        ["a"],
        propagation_policy,
    )

    result = evaluate_analysis_risk(
        propagation,
        {
            "b": {
                "businessCriticality": "HIGH",
                "changeIntent": "OBSERVED_CHANGE",
            }
        },
        risk_policy,
        expected_graph=graph,
    )

    assert all(item.target_id != "b" for item in result.assessments)
    assert [
        (gap.code, gap.factor_id)
        for gap in result.gaps
        if gap.target_id == "b"
    ] == [("MISSING_RISK_FACTOR", "securityReach")]


def test_in_memory_policy_tampering_is_rejected_at_evaluation_boundary() -> None:
    ontology, propagation_policy, risk_policy = _contracts()
    graph = _graph(
        ontology,
        [_node(ontology, "a", "code"), _node(ontology, "b", "code")],
        [_edge(ontology, "ab", "a", "revalidates", "b")],
    )
    forged_propagation = propagation_policy.model_copy(update={"maximum_total_depth": 1})
    with pytest.raises(PropagationEvaluationError, match="digest"):
        traverse_propagation(graph, ["a"], forged_propagation)

    propagation = traverse_propagation(graph, ["a"], propagation_policy)
    forged_risk = risk_policy.model_copy(update={"required_factors": ("changeIntent",)})
    with pytest.raises(PropagationEvaluationError, match="digest"):
        evaluate_analysis_risk(
            propagation,
            {"b": {"changeIntent": "OBSERVED_CHANGE"}},
            forged_risk,
            expected_graph=graph,
        )


def test_forged_graph_and_propagation_result_digests_are_rejected() -> None:
    ontology, propagation_policy, risk_policy = _contracts()
    graph = _graph(
        ontology,
        [_node(ontology, "a", "code"), _node(ontology, "b", "code")],
        [_edge(ontology, "ab", "a", "revalidates", "b")],
    )
    with pytest.raises(PropagationEvaluationError, match="graph digest"):
        traverse_propagation(
            graph.model_copy(update={"graph_sha256": "f" * 64}),
            ["a"],
            propagation_policy,
        )

    propagation = traverse_propagation(graph, ["a"], propagation_policy)
    forged = propagation.model_copy(update={"paths": ()})
    with pytest.raises(PropagationEvaluationError, match="result or path digest"):
        evaluate_analysis_risk(forged, {}, risk_policy, expected_graph=graph)


def test_risk_rejects_rehashed_result_with_forged_source_profile() -> None:
    ontology, propagation_policy, risk_policy = _contracts()
    graph = _graph(
        ontology,
        [_node(ontology, "a", "code"), _node(ontology, "b", "code")],
        [_edge(ontology, "ab", "a", "revalidates", "b")],
    )
    propagation = traverse_propagation(graph, ["a"], propagation_policy)
    forged_paths = []
    for path in propagation.paths:
        path_body = path.model_dump(mode="json")
        path_body["profile_id"] = "forged-profile"
        path_body.pop("path_sha256")
        forged_paths.append({**path_body, "path_sha256": _hash_document(path_body)})
    result_body = propagation.model_dump(mode="json")
    result_body["profile_id"] = "forged-profile"
    result_body["paths"] = forged_paths
    result_body.pop("result_sha256")
    forged = type(propagation).model_validate(
        {**result_body, "result_sha256": _hash_document(result_body)}
    )

    with pytest.raises(PropagationEvaluationError, match="expected source and graph"):
        evaluate_analysis_risk(forged, {}, risk_policy, expected_graph=graph)


def test_blocking_propagation_gap_prevents_partial_risk_assessment() -> None:
    ontology, propagation_policy, risk_policy = _contracts()
    unverified = _node(ontology, "b", "code").model_copy(
        update={"evidence_state": SourceEvidenceState.UNVERIFIED}
    )
    graph = _graph(
        ontology,
        [_node(ontology, "a", "code"), unverified],
        [_edge(ontology, "ab", "a", "revalidates", "b")],
    )
    propagation = traverse_propagation(
        graph,
        ["a"],
        propagation_policy,
    )

    risk = evaluate_analysis_risk(
        propagation,
        {
            "b": {
                "businessCriticality": "HIGH",
                "changeIntent": "PLANNED_CHANGE",
                "securityReach": "DIRECT",
            }
        },
        risk_policy,
        expected_graph=graph,
    )

    assert risk.assessments == ()
    assert {gap.code for gap in risk.gaps} == {"PROPAGATION_INCOMPLETE"}


def test_receipts_record_original_edge_orientation_when_traversed_in_reverse() -> None:
    ontology, policy, _ = _contracts()
    code = _node(ontology, "code", "code")
    artifact = _node(ontology, "artifact", "source-artifact")
    graph = _graph(
        ontology,
        [code, artifact],
        [_edge(ontology, "declared", "code", "declared-in", "artifact")],
    )

    result = traverse_propagation(graph, ["artifact"], policy)
    hop = result.paths[0].hops[0]

    assert hop.direction is TraversalDirection.TARGET_TO_SOURCE
    assert (hop.traversal_from_id, hop.traversal_to_id) == ("artifact", "code")
    assert (hop.edge_source_id, hop.edge_target_id) == ("code", "artifact")
