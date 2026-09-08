import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.context_pack import (
    ContextCompilationError,
    ContextCompilerContractError,
    ContextRelationshipState,
    SemanticCandidateReceipt,
    UnresolvedSourceFragment,
    compile_graph_context_pack,
    load_context_compiler_policy,
    validate_graph_context_pack,
)
from neo_sf_q_intel.ontology import (
    NormalizationGap,
    NormalizedEdge,
    NormalizedGraph,
    NormalizedNode,
    SourceEvidenceState,
    contract_sha256,
    load_canonical_ontology,
)
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.propagation import (
    PropagationResult,
    load_propagation_policy,
    traverse_propagation,
)

ROOT = Path(__file__).parents[1]
ONTOLOGY_PATH = ROOT / "config" / "ontology" / "canonical-ontology.json"
PROPAGATION_PATH = ROOT / "config" / "propagation-policy.json"
COMPILER_PATH = ROOT / "config" / "context-compiler-policy.json"
REASONING_PATH = ROOT / "config" / "reasoning-policy.json"
SOURCE_HASH = "a" * 64
REQUEST_HASH = "f" * 64


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stable_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _contracts():
    ontology = load_canonical_ontology(ONTOLOGY_PATH)
    propagation = load_propagation_policy(PROPAGATION_PATH, ontology)
    compiler = load_context_compiler_policy(COMPILER_PATH)
    reasoning = ReasoningPolicy.load(REASONING_PATH)
    return ontology, propagation, compiler, reasoning


def _node(ontology, node_id: str) -> NormalizedNode:
    node_class = ontology.nodes_by_id["code"]
    return NormalizedNode(
        node_id=node_id,
        raw_kind="fixture.code",
        canonical_class="code",
        role=node_class.role,
        materiality=node_class.materiality,
        label=node_id,
        source=f"fixtures/{node_id}.txt",
        raw_evidence_state="CONFIRMED",
        evidence_state=SourceEvidenceState.CONFIRMED,
        source_snapshot="snapshot-1",
        source_hash=SOURCE_HASH,
        extractor_id="deterministic-fixture-parser",
    )


def _edge(ontology, edge_id: str, source_id: str, target_id: str) -> NormalizedEdge:
    return NormalizedEdge(
        edge_id=edge_id,
        source_id=source_id,
        target_id=target_id,
        raw_relation="fixture.revalidates",
        canonical_relation="revalidates",
        materiality=ontology.relations_by_id["revalidates"].materiality,
        source="fixtures/graph.json",
        raw_evidence_state="CONFIRMED",
        evidence_state=SourceEvidenceState.CONFIRMED,
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
    return NormalizedGraph.model_validate({**body, "graph_sha256": _stable_hash(body)})


def _inputs():
    ontology, propagation_policy, compiler, reasoning = _contracts()
    nodes = [_node(ontology, item) for item in ("a", "b", "c")]
    graph = _graph(
        ontology,
        nodes,
        [_edge(ontology, "ab", "a", "b"), _edge(ontology, "ac", "a", "c")],
    )
    propagation = traverse_propagation(graph, ["a"], propagation_policy)
    return graph, propagation, compiler, reasoning


def _fragment(
    node_id: str,
    *,
    fragment_id: str | None = None,
    evidence_id: str | None = None,
    content: str | None = None,
    mandatory: bool = False,
) -> UnresolvedSourceFragment:
    text = content or f"unresolved fragment {node_id}"
    return UnresolvedSourceFragment(
        fragment_id=fragment_id or f"fragment-{node_id}",
        evidence_id=evidence_id or f"evidence-{node_id}",
        node_id=node_id,
        source_locator=f"fixtures/{node_id}.txt",
        content=text,
        content_sha256=_hash(text),
        source_snapshot="snapshot-1",
        source_graph_sha256=SOURCE_HASH,
        extractor_id="deterministic-fixture-parser",
        mandatory=mandatory,
    )


def _candidate(
    node_id: str,
    *,
    candidate_id: str | None = None,
    evidence_id: str | None = None,
    content: str | None = None,
    score: float = 0.75,
) -> SemanticCandidateReceipt:
    text = content or f"semantic candidate {node_id}"
    return SemanticCandidateReceipt(
        candidate_id=candidate_id or f"candidate-{node_id}",
        evidence_id=evidence_id or f"candidate-evidence-{node_id}",
        node_id=node_id,
        source_locator=f"fixtures/{node_id}.txt",
        content=text,
        content_sha256=_hash(text),
        source_snapshot="snapshot-1",
        source_graph_sha256=SOURCE_HASH,
        score=score,
        retriever_id="fixture-cosine",
        retrieval_model="fixture-embedding",
    )


def _compile(**overrides):
    graph, propagation, compiler, reasoning = _inputs()
    _, propagation_policy, _, _ = _contracts()
    values = {
        "graph": graph,
        "propagation": propagation,
        "propagation_policy": propagation_policy,
        "reasoning_policy": reasoning,
        "compiler_policy": compiler,
        "request_sha256": REQUEST_HASH,
        "reasoning_policy_locator": "config/reasoning-policy.json",
        "expected_reasoning_policy_sha256": reasoning.policy_sha256,
        "expected_retrieval_eval_set_sha256": reasoning.retrieval_eval_set_sha256,
        "expected_propagation_policy_sha256": propagation_policy.sha256,
        "expected_compiler_policy_sha256": compiler.sha256,
    }
    values.update(overrides)
    return compile_graph_context_pack(**values)


def _compile_bound(graph, propagation, reasoning, compiler, **overrides):
    _, propagation_policy, _, _ = _contracts()
    values = {
        "request_sha256": REQUEST_HASH,
        "reasoning_policy_locator": "config/reasoning-policy.json",
        "expected_reasoning_policy_sha256": reasoning.policy_sha256,
        "expected_retrieval_eval_set_sha256": reasoning.retrieval_eval_set_sha256,
        "expected_propagation_policy_sha256": propagation_policy.sha256,
        "expected_compiler_policy_sha256": compiler.sha256,
    }
    values.update(overrides)
    return compile_graph_context_pack(
        graph,
        propagation,
        propagation_policy,
        reasoning,
        compiler,
        **values,
    )


def _validate_bound(pack, graph, propagation, reasoning, compiler, **overrides):
    _, propagation_policy, _, _ = _contracts()
    values = {
        "request_sha256": REQUEST_HASH,
        "reasoning_policy_locator": "config/reasoning-policy.json",
        "expected_reasoning_policy_sha256": reasoning.policy_sha256,
        "expected_retrieval_eval_set_sha256": reasoning.retrieval_eval_set_sha256,
        "expected_propagation_policy_sha256": propagation_policy.sha256,
        "expected_compiler_policy_sha256": compiler.sha256,
    }
    values.update(overrides)
    return validate_graph_context_pack(
        pack,
        graph,
        propagation,
        propagation_policy,
        reasoning,
        compiler,
        **values,
    )


def _rehashed_pack(pack, mutate) -> object:
    body = pack.model_dump(mode="json")
    mutate(body)
    for _ in range(8):
        envelope = dict(body)
        envelope["context_pack_sha256"] = "0" * 64
        size = len(json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode())
        if body["budget"]["total_pack_bytes"] == size:
            break
        body["budget"]["total_pack_bytes"] = size
    body.pop("context_pack_sha256")
    body["context_pack_sha256"] = _stable_hash(body)
    return type(pack).model_validate(body)


def _policy_with_limits(tmp_path: Path, **limits):
    document = json.loads(COMPILER_PATH.read_text(encoding="utf-8"))
    document["limits"].update(limits)
    document["sha256"] = contract_sha256(document)
    path = tmp_path / "context-policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_context_compiler_policy(path, expected_sha256=document["sha256"])


def _rehashed_propagation(propagation, *, hop_updates: dict) -> PropagationResult:
    body = propagation.model_dump(mode="json")
    path = body["paths"][0]
    path["hops"][0].update(hop_updates)
    path.pop("path_sha256")
    path["path_sha256"] = _stable_hash(path)
    body.pop("result_sha256")
    body["result_sha256"] = _stable_hash(body)
    return PropagationResult.model_validate(body)


def test_compiler_binds_every_reasoning_and_graph_identity() -> None:
    graph, propagation, compiler, reasoning = _inputs()
    pack = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
        unresolved_fragments=(_fragment("a"),),
        semantic_candidates=(_candidate("b"),),
    )

    assert pack.posture == "ANALYSIS_ONLY"
    assert pack.may_authorize is False
    assert pack.authority_eligible is False
    assert pack.analysis_complete is False
    assert (
        pack.project_id,
        pack.source_snapshot,
        pack.source_graph_sha256,
        pack.normalized_graph_sha256,
    ) == (
        graph.project_id,
        graph.source_snapshot,
        graph.source_graph_sha256,
        graph.graph_sha256,
    )
    assert pack.reasoning_policy_sha256 == reasoning.policy_sha256
    assert pack.retrieval_eval_set_sha256 == reasoning.retrieval_eval_set_sha256
    assert pack.propagation_policy_sha256 == propagation.policy_sha256
    assert pack.compiler_policy_sha256 == compiler.sha256
    assert pack.unresolved_fragments[0].relationship_state == "CANDIDATE"
    assert pack.unresolved_fragments[0].evidence_state == "UNVERIFIED"
    assert "FRAGMENT_RESOLUTION_UNAVAILABLE" in {gap.code for gap in pack.gaps}
    assert pack.semantic_candidates[0].relationship_state == "CANDIDATE"
    assert pack.semantic_candidates[0].may_authorize is False


def test_input_permutations_and_identical_duplicates_have_one_stable_pack() -> None:
    graph, propagation, compiler, reasoning = _inputs()
    fragment_a, fragment_b = _fragment("a"), _fragment("b")
    candidate_b, candidate_c = _candidate("b", score=0.5), _candidate("c", score=0.9)

    first = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
        unresolved_fragments=(fragment_b, fragment_a, fragment_a),
        semantic_candidates=(candidate_b, candidate_c, candidate_b),
    )
    second = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
        unresolved_fragments=(fragment_a, fragment_b),
        semantic_candidates=(candidate_c, candidate_b),
    )

    assert first == second
    assert first.context_pack_sha256 == second.context_pack_sha256
    assert [item.fragment_id for item in first.unresolved_fragments] == [
        "fragment-a",
        "fragment-b",
    ]
    assert [item.candidate_id for item in first.semantic_candidates] == [
        "candidate-c",
        "candidate-b",
    ]


def test_conflicting_duplicate_receipts_fail_closed() -> None:
    first = _candidate("b", candidate_id="duplicate", score=0.2)
    second = _candidate("b", candidate_id="duplicate", score=0.8)
    with pytest.raises(ContextCompilationError, match="Conflicting duplicate semantic candidate"):
        _compile(semantic_candidates=(first, second))


def test_duplicate_evidence_under_different_candidate_ids_fails_closed() -> None:
    first = _candidate("b", candidate_id="first", evidence_id="same")
    second = _candidate("b", candidate_id="second", evidence_id="same")
    with pytest.raises(ContextCompilationError, match="Duplicate semantic-candidate evidence"):
        _compile(semantic_candidates=(first, second))


def test_policy_tampering_and_paired_rehash_need_external_pin(tmp_path: Path) -> None:
    document = json.loads(COMPILER_PATH.read_text(encoding="utf-8"))
    document["limits"]["maximumCandidateReceipts"] = 1
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContextCompilerContractError, match="digest"):
        load_context_compiler_policy(tampered)

    document["sha256"] = contract_sha256(document)
    tampered.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContextCompilerContractError, match="trusted digest"):
        load_context_compiler_policy(tampered)


@pytest.mark.parametrize(
    "locator",
    ["C:/workspace/source.txt", "/workspace/source.txt", "../source.txt", "a/../source.txt"],
)
def test_absolute_or_traversing_source_locators_are_rejected(locator: str) -> None:
    with pytest.raises(ValidationError, match="repository-relative"):
        UnresolvedSourceFragment(
            fragment_id="fragment",
            evidence_id="evidence",
            node_id="a",
            source_locator=locator,
            content="content",
            content_sha256=_hash("content"),
            source_snapshot="snapshot-1",
            source_graph_sha256=SOURCE_HASH,
            extractor_id="deterministic-fixture-parser",
        )


def test_cross_snapshot_and_foreign_source_digest_are_rejected() -> None:
    foreign_snapshot = _fragment("a").model_copy(update={"source_snapshot": "snapshot-2"})
    with pytest.raises(ContextCompilationError, match="crosses the graph source boundary"):
        _compile(unresolved_fragments=(foreign_snapshot,))

    foreign_hash = _candidate("b").model_copy(update={"source_graph_sha256": "d" * 64})
    with pytest.raises(ContextCompilationError, match="crosses the graph source boundary"):
        _compile(semantic_candidates=(foreign_hash,))


def test_unresolved_fragment_still_requires_a_confirmed_graph_node_reference() -> None:
    ontology, propagation_policy, compiler, reasoning = _contracts()
    unverified = _node(ontology, "a").model_copy(
        update={"evidence_state": SourceEvidenceState.UNVERIFIED}
    )
    graph = _graph(ontology, [unverified], [])
    propagation = traverse_propagation(graph, ["a"], propagation_policy)

    with pytest.raises(ContextCompilationError, match="untrusted graph node"):
        _compile_bound(
            graph,
            propagation,
            reasoning,
            compiler,
            unresolved_fragments=(_fragment("a"),),
        )


def test_candidate_cannot_self_promote_to_authority() -> None:
    body = _candidate("b").model_dump(mode="json")
    body["may_authorize"] = True
    body["relationship_state"] = ContextRelationshipState.CONFIRMED
    with pytest.raises(ValidationError):
        SemanticCandidateReceipt.model_validate(body)


def test_mandatory_path_overflow_is_blocking_and_paths_are_atomic(tmp_path: Path) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(
        tmp_path,
        maximumAuthoritativePaths=1,
        maximumAuthoritativeHops=1,
    )

    pack = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
    )

    assert len(pack.confirmed_structure_paths) == 1
    assert len(pack.confirmed_structure_paths[0].hops) == 1
    assert pack.budget.structure_paths_omitted == 1
    assert any(
        gap.code == "MANDATORY_PATH_BUDGET_EXCEEDED" and gap.blocking
        for gap in pack.gaps
    )


def test_mandatory_fragment_overflow_blocks_without_partial_content(tmp_path: Path) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(
        tmp_path,
        maximumSourceFragments=1,
        maximumSourceCharacters=5,
    )
    fragment = _fragment("a", content="content-too-large", mandatory=True)

    pack = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
        unresolved_fragments=(fragment,),
    )

    assert pack.unresolved_fragments == ()
    assert pack.budget.unresolved_characters_selected == 0
    assert pack.budget.unresolved_characters_omitted == len(fragment.content)
    assert any(
        gap.code == "MANDATORY_FRAGMENT_BUDGET_EXCEEDED" and gap.blocking
        for gap in pack.gaps
    )


def test_candidate_truncation_is_visible_nonblocking_and_score_ordered(
    tmp_path: Path,
) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(tmp_path, maximumCandidateReceipts=1)

    pack = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
        semantic_candidates=(
            _candidate("b", score=0.2),
            _candidate("c", score=0.9),
        ),
    )

    assert [item.node_id for item in pack.semantic_candidates] == ["c"]
    assert pack.budget.candidate_receipts_omitted == 1
    truncation = next(gap for gap in pack.gaps if gap.code == "CANDIDATE_BUDGET_TRUNCATED")
    assert truncation.blocking is False


def test_inputs_are_bounded_before_deduplication_or_sorting(tmp_path: Path) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(
        tmp_path,
        maximumInputSourceFragments=1,
        maximumInputCandidateReceipts=1,
    )
    with pytest.raises(ContextCompilationError, match="pre-sort safety limit"):
        _compile_bound(
            graph,
            propagation,
            reasoning,
            compiler,
            unresolved_fragments=(_fragment("a"), _fragment("a")),
        )
    with pytest.raises(ContextCompilationError, match="pre-sort safety limit"):
        _compile_bound(
            graph,
            propagation,
            reasoning,
            compiler,
            semantic_candidates=(_candidate("b"), _candidate("b")),
        )


def test_authority_and_candidate_evidence_namespaces_are_disjoint() -> None:
    with pytest.raises(ContextCompilationError, match="disjoint evidence IDs"):
        _compile(
            unresolved_fragments=(_fragment("a", evidence_id="same"),),
            semantic_candidates=(_candidate("b", evidence_id="same"),),
        )


def test_graph_or_propagation_identity_tampering_fails_closed() -> None:
    graph, propagation, compiler, reasoning = _inputs()
    with pytest.raises(ContextCompilationError, match="graph digest"):
        _compile_bound(
            graph.model_copy(update={"graph_sha256": "d" * 64}),
            propagation,
            reasoning,
            compiler,
        )


@pytest.mark.parametrize(
    "hop_updates,error",
    [
        ({"edge_id": "unknown-edge"}, "unknown graph edge"),
        ({"source_artifact": "fixtures/other.json"}, "differs from its exact"),
        (
            {
                "direction": "TARGET_TO_SOURCE",
                "edge_source_id": "b",
                "edge_target_id": "a",
            },
            "differs from its exact",
        ),
    ],
)
def test_rehashed_forged_path_receipts_are_checked_against_exact_graph_edges(
    hop_updates: dict, error: str
) -> None:
    graph, propagation, compiler, reasoning = _inputs()
    forged = _rehashed_propagation(propagation, hop_updates=hop_updates)

    with pytest.raises(ContextCompilationError, match=error):
        _compile_bound(
            graph,
            forged,
            reasoning,
            compiler,
        )


def test_any_blocking_propagation_gap_removes_all_confirmed_structure_paths() -> None:
    ontology, propagation_policy, compiler, reasoning = _contracts()
    nodes = [_node(ontology, item) for item in ("a", "b", "c")]
    nodes[2] = nodes[2].model_copy(update={"evidence_state": SourceEvidenceState.UNVERIFIED})
    graph = _graph(
        ontology,
        nodes,
        [_edge(ontology, "ab", "a", "b"), _edge(ontology, "ac", "a", "c")],
    )
    propagation = traverse_propagation(graph, ["a"], propagation_policy)
    assert propagation.paths
    assert any(gap.blocking for gap in propagation.gaps)

    pack = _compile_bound(
        graph,
        propagation,
        reasoning,
        compiler,
    )

    assert pack.confirmed_structure_paths == ()
    assert pack.budget.structure_paths_omitted == len(propagation.paths)
    assert any(
        gap.code == "BLOCKING_GRAPH_GAPS_PREVENT_CONFIRMED_STRUCTURE" and gap.blocking
        for gap in pack.gaps
    )
    with pytest.raises(ContextCompilationError, match="Propagation result digest is invalid"):
        _compile_bound(
            graph,
            propagation.model_copy(update={"project_id": "foreign-project"}),
            reasoning,
            compiler,
        )


def test_foundation_exposes_current_freshness_and_risk_factor_boundaries() -> None:
    pack = _compile()
    gap_codes = {gap.code for gap in pack.gaps if gap.blocking}

    assert "EDGE_FRESHNESS_RECEIPTS_UNAVAILABLE" in gap_codes
    assert "RISK_FACTOR_RECEIPTS_UNAVAILABLE" in gap_codes
    assert pack.budget.structure_paths_available == 2
    assert pack.budget.structure_paths_selected == 2


@pytest.mark.parametrize(
    "field",
    ["policy_sha256", "retrieval_eval_set_sha256"],
)
def test_replaced_reasoning_or_eval_identity_is_rejected(field: str) -> None:
    _, _, _, reasoning = _contracts()
    forged = replace(reasoning, **{field: "d" * 64})

    with pytest.raises(ContextCompilationError, match="root is untrusted"):
        _compile(reasoning_policy=forged)


def test_propagation_policy_must_match_independent_expected_root() -> None:
    _, propagation_policy, _, _ = _contracts()

    with pytest.raises(ContextCompilationError, match="expected root"):
        _compile(expected_propagation_policy_sha256="d" * 64)


def test_valid_but_incomplete_propagation_result_fails_deterministic_replay() -> None:
    graph, propagation, compiler, reasoning = _inputs()
    body = propagation.model_dump(mode="json")
    body["paths"] = body["paths"][:1]
    body.pop("result_sha256")
    body["result_sha256"] = _stable_hash(body)
    incomplete = PropagationResult.model_validate(body)

    with pytest.raises(ContextCompilationError, match="differs from deterministic replay"):
        _compile_bound(graph, incomplete, reasoning, compiler)


@pytest.mark.parametrize(
    "limit_name",
    ["maximumInputPropagationPaths", "maximumInputPropagationHops"],
)
def test_propagation_artifact_is_bounded_before_replay(
    tmp_path: Path, limit_name: str
) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(tmp_path, **{limit_name: 1})

    with pytest.raises(ContextCompilationError, match="pre-sort safety limit"):
        _compile_bound(graph, propagation, reasoning, compiler)


def test_blocking_graph_trust_gap_suppresses_confirmed_structure() -> None:
    graph, _, compiler, reasoning = _inputs()
    body = graph.model_dump(mode="json")
    body.pop("graph_sha256")
    body["trust_gaps"] = [
        NormalizationGap(
            code="FIXTURE_TRUST_GAP",
            entity_id="c",
            blocking=True,
        ).model_dump(mode="json")
    ]
    graph_with_gap = NormalizedGraph.model_validate(
        {**body, "graph_sha256": _stable_hash(body)}
    )
    _, propagation_policy, _, _ = _contracts()
    propagation = traverse_propagation(graph_with_gap, ["a"], propagation_policy)

    pack = _compile_bound(graph_with_gap, propagation, reasoning, compiler)

    assert pack.confirmed_structure_paths == ()
    assert "GRAPH_FIXTURE_TRUST_GAP" in {gap.code for gap in pack.gaps}


def test_pack_validator_reconciles_budget_counts() -> None:
    pack = _compile()
    body = pack.model_dump(mode="json")
    body["budget"]["structure_paths_selected"] += 1

    with pytest.raises(ValidationError, match="budget"):
        type(pack).model_validate(body)

    body = pack.model_dump(mode="json")
    body["budget"]["identifier_character_limit"] = 1
    with pytest.raises(ValidationError, match="field budgets"):
        type(pack).model_validate(body)


def test_total_pack_byte_limit_rejects_oversize_output(tmp_path: Path) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(tmp_path, maximumTotalPackBytes=4096)

    with pytest.raises(ContextCompilationError, match="maximumTotalPackBytes"):
        _compile_bound(graph, propagation, reasoning, compiler)


def test_trusted_replay_rejects_paired_rehashed_gap_removal() -> None:
    graph, propagation, compiler, reasoning = _inputs()
    pack = _compile_bound(graph, propagation, reasoning, compiler)

    forged = _rehashed_pack(
        pack,
        lambda body: (body.update({"gaps": [], "analysis_complete": True})),
    )
    with pytest.raises(ContextCompilationError, match="trusted deterministic replay"):
        _validate_bound(forged, graph, propagation, reasoning, compiler)


def test_trusted_replay_rejects_rehashed_budget_and_selected_path_changes() -> None:
    graph, propagation, compiler, reasoning = _inputs()
    pack = _compile_bound(graph, propagation, reasoning, compiler)

    def mutate(body):
        removed = body["confirmed_structure_paths"].pop()
        hop_count = len(removed["hops"])
        body["budget"]["structure_paths_selected"] -= 1
        body["budget"]["structure_paths_omitted"] += 1
        body["budget"]["structure_hops_selected"] -= hop_count
        body["budget"]["structure_hops_omitted"] += hop_count
        body["budget"]["structure_path_limit"] += 10

    forged = _rehashed_pack(pack, mutate)
    with pytest.raises(ContextCompilationError, match="trusted deterministic replay"):
        _validate_bound(forged, graph, propagation, reasoning, compiler)


@pytest.mark.parametrize("identity", ["project", "snapshot"])
def test_trusted_replay_rejects_nested_rehashed_source_identity(identity: str) -> None:
    graph, propagation, compiler, reasoning = _inputs()
    pack = _compile_bound(graph, propagation, reasoning, compiler)

    def mutate(body):
        for path in body["confirmed_structure_paths"]:
            if identity == "project":
                body["project_id"] = "foreign-project"
                path["project_id"] = "foreign-project"
            else:
                body["source_snapshot"] = "foreign-snapshot"
                path["source_snapshot"] = "foreign-snapshot"
                for hop in path["hops"]:
                    hop["source_snapshot"] = "foreign-snapshot"
            path.pop("path_sha256")
            path["path_sha256"] = _stable_hash(path)

    forged = _rehashed_pack(pack, mutate)
    with pytest.raises(ContextCompilationError, match="trusted deterministic replay"):
        _validate_bound(forged, graph, propagation, reasoning, compiler)


@pytest.mark.parametrize(
    "limit,value,error",
    [
        ("maximumInputGraphNodes", 2, "graph-node"),
        ("maximumInputGraphEdges", 1, "graph-edge"),
        ("maximumInputGraphBytes", 1024, "maximumInputGraphBytes"),
    ],
)
def test_graph_input_is_bounded_before_digest_replay(
    tmp_path: Path, limit: str, value: int, error: str
) -> None:
    graph, propagation, _, reasoning = _inputs()
    compiler = _policy_with_limits(tmp_path, **{limit: value})

    with pytest.raises(ContextCompilationError, match=error):
        _compile_bound(graph, propagation, reasoning, compiler)


def test_untrusted_compiler_root_is_rejected_before_its_limits_are_used(
    tmp_path: Path,
) -> None:
    graph, propagation, trusted, reasoning = _inputs()
    forged = _policy_with_limits(tmp_path, maximumInputGraphNodes=1)

    with pytest.raises(ContextCompilationError, match="expected root"):
        _compile_bound(
            graph,
            propagation,
            reasoning,
            forged,
            expected_compiler_policy_sha256=trusted.sha256,
        )
