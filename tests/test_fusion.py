import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.fusion import (
    CandidateFusionResult,
    CandidateState,
    FusionCandidate,
    FusionChannelRoot,
    FusionContractError,
    FusionError,
    FusionEvaluationContract,
    FusionModality,
    ModalityAvailability,
    ModalityStatus,
    channel_roots_sha256,
    compile_candidate_fusion,
    load_fusion_evaluation_contract,
    load_fusion_policy,
    validate_candidate_fusion,
)
from neo_sf_q_intel.ontology import contract_sha256
from tests.test_context_pack import _compile as compile_fixture_context

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "config" / "fusion-policy.json"
EVALUATION_PATH = ROOT / "quality" / "evals" / "hybrid-fusion-evaluation-v1.json"
EVALUATED_AT = "2026-09-09T12:00:00Z"
BUILT_AT = "2026-09-09T11:00:00Z"
EXPIRES_AT = "2026-09-10T12:00:00Z"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _root(modality: FusionModality) -> FusionChannelRoot:
    common = {
        "modality": modality,
        "producer_id": f"fixture-{modality.value.casefold()}-retriever",
        "producer_version": "1.0.0",
        "corpus_id": "fixture-context-corpus",
        "corpus_version": "1.0.0",
        "corpus_manifest_sha256": _digest("fixture-corpus"),
        "index_id": f"fixture-{modality.value.casefold()}-index",
        "index_version": "1.0.0",
        "index_build_sha256": _digest(f"fixture-{modality.value}-index-build"),
        "built_at": BUILT_AT,
        "expires_at": EXPIRES_AT,
    }
    if modality in (FusionModality.EXACT, FusionModality.LEXICAL):
        common["analyzer_policy_sha256"] = _digest("fixture-analyzer-policy")
    elif modality is FusionModality.GRAPH:
        common["path_policy_sha256"] = _digest("fixture-path-policy")
    else:
        common.update(
            {
                "provider_kind": "fixture-provider",
                "embedding_model_id": "fixture-embedding-model",
                "embedding_deployment_id": "fixture-embedding-deployment",
                "embedding_version": "1.0.0",
                "embedding_dimensions": 8,
                "vector_normalization": "L2",
                "vector_distance": "COSINE",
                "chunking_policy_sha256": _digest("fixture-chunking-policy"),
            }
        )
    return FusionChannelRoot(**common)


def _roots() -> tuple[FusionChannelRoot, ...]:
    return tuple(_root(modality) for modality in FusionModality)


def _evaluation(policy) -> FusionEvaluationContract:
    return load_fusion_evaluation_contract(
        EVALUATION_PATH,
        expected_sha256=policy.evaluation_set_sha256,
    )

def _availability(
    *, semantic: ModalityStatus = ModalityStatus.AVAILABLE
) -> tuple[ModalityAvailability, ...]:
    rows = [
        ModalityAvailability(
            modality=FusionModality.EXACT,
            status=ModalityStatus.AVAILABLE,
        ),
        ModalityAvailability(
            modality=FusionModality.LEXICAL,
            status=ModalityStatus.AVAILABLE,
        ),
        ModalityAvailability(
            modality=FusionModality.GRAPH,
            status=ModalityStatus.AVAILABLE,
        ),
    ]
    if semantic is ModalityStatus.AVAILABLE:
        rows.append(
            ModalityAvailability(
                modality=FusionModality.SEMANTIC,
                status=semantic,
            )
        )
    else:
        rows.append(
            ModalityAvailability(
                modality=FusionModality.SEMANTIC,
                status=semantic,
                reason_code="VECTOR_PROVIDER_UNAVAILABLE",
            )
        )
    return tuple(rows)


def _candidate(
    pack,
    candidate_id: str,
    modality: FusionModality,
    *,
    entity_id: str = "b",
    key: str = "candidate.relevance",
    value: str = "supported",
    score: str = "0.75",
    evidence_ids: tuple[str, ...] | None = None,
) -> FusionCandidate:
    root = _root(modality)
    if evidence_ids is None:
        evidence_ids = (
            (pack.confirmed_structure_paths[0].path_sha256,)
            if modality is FusionModality.GRAPH
            else ("ab",)
        )
    values = dict(
        candidate_id=candidate_id,
        entity_id=entity_id,
        assertion_key=key,
        assertion_value=value,
        modality=modality,
        normalized_score=Decimal(score),
        evidence_ids=evidence_ids,
        request_sha256=pack.request_sha256,
        project_id=pack.project_id,
        source_snapshot=pack.source_snapshot,
        source_graph_sha256=pack.source_graph_sha256,
        normalized_graph_sha256=pack.normalized_graph_sha256,
        context_pack_sha256=pack.context_pack_sha256,
        ontology_id=pack.ontology_id,
        ontology_version=pack.ontology_version,
        ontology_sha256=pack.ontology_sha256,
        profile_id=pack.profile_id,
        profile_version=pack.profile_version,
        profile_sha256=pack.profile_sha256,
        query_sha256=_digest("fixture-query"),
        producer_id=root.producer_id,
        producer_version=root.producer_version,
        corpus_id=root.corpus_id,
        corpus_version=root.corpus_version,
        corpus_manifest_sha256=root.corpus_manifest_sha256,
        index_id=root.index_id,
        index_version=root.index_version,
        index_build_sha256=root.index_build_sha256,
        built_at=BUILT_AT,
        evaluated_at=EVALUATED_AT,
        expires_at=EXPIRES_AT,
        analyzer_policy_sha256=root.analyzer_policy_sha256,
        path_policy_sha256=root.path_policy_sha256,
        graph_path_sha256=(
            pack.confirmed_structure_paths[0].path_sha256
            if modality is FusionModality.GRAPH
            else None
        ),
        provider_kind=root.provider_kind,
        embedding_model_id=root.embedding_model_id,
        embedding_deployment_id=root.embedding_deployment_id,
        embedding_version=root.embedding_version,
        embedding_dimensions=root.embedding_dimensions,
        vector_normalization=root.vector_normalization,
        vector_distance=root.vector_distance,
        chunking_policy_sha256=root.chunking_policy_sha256,
    )
    if modality is FusionModality.EXACT:
        values["exact_match_sha256"] = _digest("")
        exact_body = {
            "query_sha256": values["query_sha256"],
            "entity_id": entity_id,
            "assertion_key": key,
            "assertion_value": value,
            "evidence_ids": evidence_ids,
            "analyzer_policy_sha256": root.analyzer_policy_sha256,
            "index_build_sha256": root.index_build_sha256,
        }
        values["exact_match_sha256"] = hashlib.sha256(
            json.dumps(exact_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    return FusionCandidate(**values)


def _compile(
    candidates=(),
    availability=None,
    *,
    pack=None,
    policy=None,
    channel_roots=None,
    **root_overrides,
):
    pack = pack or compile_fixture_context()
    policy = policy or load_fusion_policy(POLICY_PATH)
    channel_roots = channel_roots or _roots()
    evaluation = _evaluation(policy)
    trusted = {
        "expected_context_pack_sha256": pack.context_pack_sha256,
        "expected_reasoning_policy_sha256": pack.reasoning_policy_sha256,
        "expected_retrieval_eval_set_sha256": pack.retrieval_eval_set_sha256,
        "expected_compiler_policy_sha256": pack.compiler_policy_sha256,
        "expected_fusion_policy_sha256": policy.sha256,
        "expected_fusion_evaluation_set_sha256": policy.evaluation_set_sha256,
        "expected_channel_roots_sha256": channel_roots_sha256(channel_roots),
        "expected_query_sha256": _digest("fixture-query"),
    }
    trusted.update(root_overrides)
    return compile_candidate_fusion(
        pack,
        policy,
        evaluation,
        tuple(candidates),
        availability or _availability(),
        channel_roots,
        evaluated_at=EVALUATED_AT,
        **trusted,
    )


def _policy(tmp_path: Path, **limits):
    document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    document["limits"].update(limits)
    document["sha256"] = contract_sha256(document)
    path = tmp_path / "fusion-policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_fusion_policy(path, expected_sha256=document["sha256"])


def _rehash(result, mutate):
    body = result.model_dump(mode="json")
    mutate(body)
    for _ in range(12):
        envelope = {**body, "result_sha256": "0" * 64}
        size = len(json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode())
        if body["budget"]["result_bytes"] == size:
            break
        body["budget"]["result_bytes"] = size
    body.pop("result_sha256")
    body["result_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CandidateFusionResult.model_validate(body)


def test_policy_is_self_hashed_and_externally_pinned(tmp_path: Path) -> None:
    policy = load_fusion_policy(POLICY_PATH)
    assert policy.posture == "ANALYSIS_ONLY"

    document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    document["reciprocalRankConstant"] = 1
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(FusionContractError, match="digest"):
        load_fusion_policy(tampered)

    document["sha256"] = contract_sha256(document)
    tampered.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(FusionContractError, match="trusted digest"):
        load_fusion_policy(tampered)


def test_evaluation_contract_loader_rejects_duplicate_keys_and_paired_rehash(
    tmp_path: Path,
) -> None:
    policy = load_fusion_policy(POLICY_PATH)
    evaluation = _evaluation(policy)
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(evaluation.model_dump(mode="json", by_alias=True)))
    assert (
        load_fusion_evaluation_contract(path, expected_sha256=evaluation.sha256)
        == evaluation
    )

    duplicate = evaluation.model_dump_json(by_alias=True)[:-1] + ',"schemaVersion":"1.0.0"}'
    path.write_text(duplicate)
    with pytest.raises(FusionContractError, match="Duplicate JSON key"):
        load_fusion_evaluation_contract(path, expected_sha256=evaluation.sha256)

    body = evaluation.model_dump(mode="json", by_alias=True)
    body["requiredCases"] = [*body["requiredCases"], "untrusted-extra-case"]
    body["sha256"] = contract_sha256(body)
    path.write_text(json.dumps(body))
    with pytest.raises(FusionContractError, match="trusted digest"):
        load_fusion_evaluation_contract(path, expected_sha256=evaluation.sha256)

def test_permutation_is_deterministic_with_quantized_scores_and_stable_ties() -> None:
    pack = compile_fixture_context()
    rows = [
        _candidate(pack, "semantic", FusionModality.SEMANTIC, score="0.900000004"),
        _candidate(pack, "graph", FusionModality.GRAPH, score="0.9"),
        _candidate(pack, "exact", FusionModality.EXACT, score="0.900000003"),
    ]

    first = _compile(rows, pack=pack)
    second = _compile(reversed(rows), pack=pack)

    assert first == second
    assert first.candidates[0].relationship_state is CandidateState.CANDIDATE
    assert first.candidates[0].may_authorize is False
    assert [item.modality for item in first.candidates[0].contributions] == [
        FusionModality.EXACT,
        FusionModality.GRAPH,
        FusionModality.SEMANTIC,
    ]


def test_semantic_perfect_score_cannot_promote_authority() -> None:
    pack = compile_fixture_context()
    result = _compile(
        [_candidate(pack, "perfect", FusionModality.SEMANTIC, score="1.0")],
        pack=pack,
    )

    assert result.relationship_state is CandidateState.CANDIDATE
    assert result.may_authorize is False
    assert result.candidates[0].relationship_state is CandidateState.CANDIDATE
    assert result.candidates[0].may_authorize is False


def test_confirmed_graph_path_only_produces_non_authoritative_relevance() -> None:
    pack = compile_fixture_context()
    result = _compile([_candidate(pack, "graph", FusionModality.GRAPH)], pack=pack)
    candidate = result.candidates[0]

    assert candidate.relationship_state is CandidateState.CANDIDATE
    assert candidate.may_authorize is False
    assert candidate.may_satisfy_release_evidence is False
    assert candidate.authority_eligible is False
    assert result.may_satisfy_release_evidence is False
    assert result.authority_eligible is False


@pytest.mark.parametrize("score", [math.nan, math.inf, -math.inf])
def test_nonfinite_scores_are_rejected(score: float) -> None:
    pack = compile_fixture_context()
    body = _candidate(pack, "bad", FusionModality.SEMANTIC).model_dump(mode="python")
    body["normalized_score"] = score
    with pytest.raises(ValidationError, match="finite"):
        FusionCandidate.model_validate(body)


def test_cross_snapshot_and_invisible_evidence_are_rejected() -> None:
    pack = compile_fixture_context()
    crossed = _candidate(pack, "crossed", FusionModality.GRAPH).model_copy(
        update={"source_snapshot": "foreign-snapshot"}
    )
    with pytest.raises(FusionError, match="source boundary"):
        _compile([crossed], pack=pack)

    invisible = _candidate(
        pack,
        "invisible",
        FusionModality.GRAPH,
        evidence_ids=("not-in-pack",),
    )
    with pytest.raises(FusionError, match="outside the trusted context"):
        _compile([invisible], pack=pack)


def test_evidence_must_be_bound_to_the_candidate_entity() -> None:
    pack = compile_fixture_context()
    misbound = _candidate(
        pack,
        "misbound",
        FusionModality.SEMANTIC,
        entity_id="b",
        evidence_ids=("ac",),
    )
    with pytest.raises(FusionError, match="not bound to its asserted entity"):
        _compile([misbound], pack=pack)


def test_unknown_or_stale_index_receipt_is_rejected() -> None:
    pack = compile_fixture_context()
    semantic = _candidate(pack, "semantic", FusionModality.SEMANTIC)
    unknown = semantic.model_copy(update={"index_build_sha256": "d" * 64})
    with pytest.raises(FusionError, match="unknown or untrusted channel index"):
        _compile([unknown], pack=pack)

    stale = semantic.model_copy(update={"expires_at": "2026-09-09T11:59:59Z"})
    with pytest.raises(FusionError, match="malformed, stale, or invalid"):
        _compile([stale], pack=pack)


@pytest.mark.parametrize(
    "updates,error",
    [
        (
            {
                "built_at": "2026-09-09T13:00:00Z",
                "expires_at": "2026-09-10T13:00:00Z",
            },
            "future",
        ),
        (
            {
                "built_at": "2026-09-09T10:00:00Z",
                "expires_at": "2026-09-09T11:59:59Z",
            },
            "stale or expired",
        ),
    ],
)
def test_every_channel_root_is_fresh_even_with_no_candidates(
    updates: dict[str, str], error: str
) -> None:
    roots = list(_roots())
    roots[-1] = roots[-1].model_copy(update=updates)

    with pytest.raises(FusionError, match=error):
        _compile(channel_roots=tuple(roots))


def test_forged_channel_root_model_copy_is_strictly_revalidated() -> None:
    roots = list(_roots())
    roots[-1] = roots[-1].model_copy(update={"embedding_dimensions": 0})

    with pytest.raises(FusionError, match="malformed, stale, or invalid"):
        _compile(channel_roots=tuple(roots))


def test_semantic_provider_outage_is_visible_but_exact_and_graph_still_fuse() -> None:
    pack = compile_fixture_context()
    rows = [
        _candidate(pack, "exact", FusionModality.EXACT),
        _candidate(pack, "graph", FusionModality.GRAPH),
    ]
    result = _compile(
        rows,
        availability=_availability(semantic=ModalityStatus.UNAVAILABLE),
        pack=pack,
    )

    assert result.degraded is True
    assert result.analysis_complete is False
    assert result.fusion_complete is True
    assert result.upstream_context_complete is False
    assert {item.modality for item in result.candidates[0].contributions} == {
        FusionModality.EXACT,
        FusionModality.GRAPH,
    }
    assert "SEMANTIC_UNAVAILABLE" in {gap.code for gap in result.gaps}


def test_unavailable_modality_cannot_smuggle_candidates() -> None:
    pack = compile_fixture_context()
    semantic = _candidate(pack, "semantic", FusionModality.SEMANTIC)
    with pytest.raises(FusionError, match="Unavailable modality"):
        _compile(
            [semantic],
            availability=_availability(semantic=ModalityStatus.UNAVAILABLE),
            pack=pack,
        )


def test_duplicate_claims_deduplicate_and_conflicting_values_are_preserved() -> None:
    pack = compile_fixture_context()
    rows = [
        _candidate(pack, "exact-yes", FusionModality.EXACT, value="yes"),
        _candidate(pack, "semantic-yes", FusionModality.SEMANTIC, value="yes"),
        _candidate(pack, "graph-no", FusionModality.GRAPH, value="no"),
    ]
    result = _compile(rows, pack=pack)

    assert len(result.candidates) == 2
    assert {item.assertion_value for item in result.candidates} == {"yes", "no"}
    assert all(item.has_conflict for item in result.candidates)
    assert len({item.conflict_set_id for item in result.candidates}) == 1
    yes = next(item for item in result.candidates if item.assertion_value == "yes")
    assert {item.modality for item in yes.contributions} == {
        FusionModality.EXACT,
        FusionModality.SEMANTIC,
    }


def test_same_modality_duplicate_cannot_amplify_rank_score() -> None:
    pack = compile_fixture_context()
    first = _candidate(pack, "lexical-a", FusionModality.LEXICAL)
    duplicate = _candidate(pack, "lexical-b", FusionModality.LEXICAL)
    baseline = _compile([first], pack=pack)
    repeated = _compile([first, duplicate], pack=pack)

    assert baseline.candidates[0].fused_score == repeated.candidates[0].fused_score
    assert baseline.candidates[0].contributions[0].rank == 1
    assert repeated.candidates[0].contributions[0].rank == 1


def test_conflicting_duplicate_candidate_id_fails_closed() -> None:
    pack = compile_fixture_context()
    first = _candidate(pack, "duplicate", FusionModality.GRAPH, value="one")
    second = _candidate(pack, "duplicate", FusionModality.GRAPH, value="two")
    with pytest.raises(FusionError, match="Conflicting duplicate"):
        _compile([first, second], pack=pack)


def test_output_bounds_use_exact_atomic_omissions(tmp_path: Path) -> None:
    pack = compile_fixture_context()
    policy = _policy(tmp_path, maximumOutputCandidates=1)
    rows = [
        _candidate(pack, "conflict-one", FusionModality.EXACT, value="one"),
        _candidate(pack, "conflict-two", FusionModality.GRAPH, value="two"),
        _candidate(
            pack,
            "other",
            FusionModality.SEMANTIC,
            key="candidate.secondary",
            value="three",
        ),
    ]
    result = _compile(rows, pack=pack, policy=policy)

    # The two-value conflict is one atomic bundle and cannot be partially exposed.
    assert len(result.candidates) == 0
    assert len(result.omissions) == 3
    assert result.budget.output_candidates_available == 3
    assert result.budget.output_candidates_selected == 0
    assert result.budget.output_candidates_omitted == 3
    assert "CONFLICT_SET_BUDGET_EXCEEDED" in {gap.code for gap in result.gaps}
    assert "PROTECTED_CANDIDATE_BUDGET_EXCEEDED" in {gap.code for gap in result.gaps}


def test_protected_candidates_cannot_be_evicted_by_semantic_flood(tmp_path: Path) -> None:
    pack = compile_fixture_context()
    policy = _policy(tmp_path, maximumOutputCandidates=2)
    protected = [
        _candidate(
            pack,
            "exact-protected",
            FusionModality.EXACT,
            key="candidate.exact",
        ),
        _candidate(
            pack,
            "graph-protected",
            FusionModality.GRAPH,
            key="candidate.graph",
        ),
    ]
    semantic = [
        _candidate(
            pack,
            f"semantic-{index}",
            FusionModality.SEMANTIC,
            key=f"candidate.semantic-{index}",
            score="1.0",
        )
        for index in range(8)
    ]
    result = _compile([*semantic, *protected], pack=pack, policy=policy)

    assert {item.assertion_key for item in result.candidates} == {
        "candidate.exact",
        "candidate.graph",
    }


def test_input_count_and_field_bounds_fail_before_fusion(tmp_path: Path) -> None:
    pack = compile_fixture_context()
    policy = _policy(tmp_path, maximumInputCandidates=1, maximumOmissionRecords=1)
    rows = [
        _candidate(pack, "one", FusionModality.GRAPH),
        _candidate(pack, "two", FusionModality.EXACT),
    ]
    with pytest.raises(FusionError, match="maximumInputCandidates"):
        _compile(rows, pack=pack, policy=policy)

    long_field = _candidate(pack, "x" * 257, FusionModality.GRAPH)
    with pytest.raises(FusionError, match="maximumIdentifierCharacters"):
        _compile([long_field], pack=pack)


def test_all_modalities_unavailable_is_blocking_but_replayable() -> None:
    availability = tuple(
        ModalityAvailability(
            modality=modality,
            status=ModalityStatus.UNAVAILABLE,
            reason_code="SOURCE_UNAVAILABLE",
        )
        for modality in FusionModality
    )
    result = _compile(availability=availability)
    assert result.analysis_complete is False
    assert "NO_MODALITY_AVAILABLE" in {gap.code for gap in result.gaps}


def test_semantic_only_blocks_when_deterministic_channels_are_unavailable() -> None:
    pack = compile_fixture_context()
    availability = tuple(
        ModalityAvailability(
            modality=modality,
            status=(
                ModalityStatus.AVAILABLE
                if modality is FusionModality.SEMANTIC
                else ModalityStatus.UNAVAILABLE
            ),
            reason_code=(
                None if modality is FusionModality.SEMANTIC else "SOURCE_UNAVAILABLE"
            ),
        )
        for modality in FusionModality
    )
    result = _compile(
        [_candidate(pack, "semantic", FusionModality.SEMANTIC)],
        availability=availability,
        pack=pack,
    )
    assert "DETERMINISTIC_CHANNELS_UNAVAILABLE" in {gap.code for gap in result.gaps}
    assert result.analysis_complete is False


def test_outage_does_not_change_protected_selection_with_semantic_corroboration(
    tmp_path: Path,
) -> None:
    pack = compile_fixture_context()
    policy = _policy(tmp_path, maximumOutputCandidates=1)
    deterministic = [
        _candidate(
            pack,
            "exact-first",
            FusionModality.EXACT,
            key="candidate.first",
            score="0.9",
        ),
        _candidate(
            pack,
            "exact-second",
            FusionModality.EXACT,
            key="candidate.second",
            score="0.8",
        ),
    ]
    semantic_corroboration = _candidate(
        pack,
        "semantic-second",
        FusionModality.SEMANTIC,
        key="candidate.second",
        score="1.0",
    )
    enabled = _compile(
        [*deterministic, semantic_corroboration], pack=pack, policy=policy
    )
    outage = _compile(
        deterministic,
        availability=_availability(semantic=ModalityStatus.UNAVAILABLE),
        pack=pack,
        policy=policy,
    )
    assert [item.fused_candidate_id for item in enabled.candidates] == [
        item.fused_candidate_id for item in outage.candidates
    ]
    assert [item.contributions for item in enabled.candidates] == [
        item.contributions for item in outage.candidates
    ]


def test_external_context_and_evaluation_roots_are_mandatory() -> None:
    with pytest.raises(FusionError, match="trusted replay root"):
        _compile(expected_context_pack_sha256="d" * 64)
    with pytest.raises(FusionError, match="evaluation identity"):
        _compile(expected_retrieval_eval_set_sha256="d" * 64)
    with pytest.raises(FusionError, match="Fusion evaluation set"):
        _compile(expected_fusion_evaluation_set_sha256="d" * 64)
    with pytest.raises(FusionError, match="trusted query root"):
        _compile(
            [_candidate(compile_fixture_context(), "semantic", FusionModality.SEMANTIC)],
            expected_query_sha256="d" * 64,
        )


def test_paired_rehashed_result_is_rejected_by_trusted_replay() -> None:
    pack = compile_fixture_context()
    policy = load_fusion_policy(POLICY_PATH)
    rows = (_candidate(pack, "graph", FusionModality.GRAPH),)
    result = _compile(rows, pack=pack, policy=policy)
    forged = _rehash(
        result,
        lambda body: body["candidates"][0].update({"fused_score": "0.99999999"}),
    )

    with pytest.raises(FusionError, match="trusted deterministic replay"):
        validate_candidate_fusion(
            forged,
            pack,
            policy,
            _evaluation(policy),
            rows,
            _availability(),
            _roots(),
            evaluated_at=EVALUATED_AT,
            expected_query_sha256=_digest("fixture-query"),
            expected_context_pack_sha256=pack.context_pack_sha256,
            expected_reasoning_policy_sha256=pack.reasoning_policy_sha256,
            expected_retrieval_eval_set_sha256=pack.retrieval_eval_set_sha256,
            expected_compiler_policy_sha256=pack.compiler_policy_sha256,
            expected_fusion_policy_sha256=policy.sha256,
            expected_fusion_evaluation_set_sha256=policy.evaluation_set_sha256,
            expected_channel_roots_sha256=channel_roots_sha256(_roots()),
        )


def test_clean_result_validates_by_exact_replay() -> None:
    pack = compile_fixture_context()
    policy = load_fusion_policy(POLICY_PATH)
    rows = (_candidate(pack, "graph", FusionModality.GRAPH),)
    result = _compile(rows, pack=pack, policy=policy)

    assert (
        validate_candidate_fusion(
            result,
            pack,
            policy,
            _evaluation(policy),
            rows,
            _availability(),
            _roots(),
            evaluated_at=EVALUATED_AT,
            expected_query_sha256=_digest("fixture-query"),
            expected_context_pack_sha256=pack.context_pack_sha256,
            expected_reasoning_policy_sha256=pack.reasoning_policy_sha256,
            expected_retrieval_eval_set_sha256=pack.retrieval_eval_set_sha256,
            expected_compiler_policy_sha256=pack.compiler_policy_sha256,
            expected_fusion_policy_sha256=policy.sha256,
            expected_fusion_evaluation_set_sha256=policy.evaluation_set_sha256,
            expected_channel_roots_sha256=channel_roots_sha256(_roots()),
        )
        == result
    )
