import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import ChangeIntent, ChangeRequest, DecisionCode
from neo_sf_q_intel.policy import ReasoningPolicy, ReasoningPolicyError
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot
from neo_sf_q_intel.service import AssuranceService
from tests.graph_fixtures import add_trusted_envelopes, fixture_digest


def policy_source(relation: str = "tests") -> SalesforceSourceSnapshot:
    source_hash = fixture_digest("policy-graph")
    graph = {
        "sourceSnapshot": "policy",
        "nodes": [
            {"id": "field:Alpha", "kind": "field", "label": "Alpha threshold"},
            {"id": "test:Component", "kind": "component-test", "label": "UI check"},
            {"id": "test:Locator", "kind": "locator-use-case", "label": "Locator check"},
            {"id": "fixture:One", "kind": "synthetic-fixture", "label": "Sample row"},
            {"id": "unknown:One", "kind": "unclassified-security-rule", "label": "Rule"},
        ],
        "edges": [
            {"from": "test:Component", "relation": relation, "to": "field:Alpha"},
            {"from": "test:Locator", "relation": "covers", "to": "field:Alpha"},
            {"from": "fixture:One", "relation": "references", "to": "field:Alpha"},
            {"from": "unknown:One", "relation": "references", "to": "field:Alpha"},
        ],
    }
    add_trusted_envelopes(graph, snapshot_id="policy", source_hash=source_hash)
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Policy Fixture"},
        project_index={"sourceSnapshot": "policy"},
        trusted_graph_sha256=source_hash,
        graph=graph,
    )


def test_policy_classifies_validation_and_excludes_fixture_and_unknown_kinds() -> None:
    service = AssuranceService(policy_source())

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert [item.entity_id for item in run.impacts] == ["field:Alpha"]
    assert {item.test_id for item in run.selected_tests} == {"test:Component", "test:Locator"}
    assert {gap.code for gap in run.analysis_gaps} == {
        "UNMAPPED_EDGE_ENDPOINT",
        "UNMAPPED_NODE_KIND",
    }
    assert run.governance and not run.governance.passed
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE


def test_unrecognized_relation_is_not_traversed() -> None:
    source = policy_source()
    source.graph["nodes"].extend(
        [
            {"id": "capability:Bridge", "kind": "capability", "label": "Bridge"},
            {"id": "class:Hidden", "kind": "apex-class", "label": "Hidden downstream"},
        ]
    )
    source.graph["edges"].extend(
        [
            {
                "from": "capability:Bridge",
                "relation": "unreviewed_relation",
                "to": "field:Alpha",
            },
            {"from": "class:Hidden", "relation": "reads", "to": "capability:Bridge"},
        ]
    )
    add_trusted_envelopes(
        source.graph,
        snapshot_id=source.snapshot_id,
        source_hash=source.trusted_graph_sha256 or "",
    )
    service = ChangeIntelligenceService(EvidenceRetriever(source))

    _, impacts, _, gaps = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "class:Hidden" not in {item.entity_id for item in impacts}
    assert {gap.code for gap in gaps} == {
        "ILLEGAL_ENDPOINT_SIGNATURE",
        "UNMAPPED_EDGE_ENDPOINT",
        "UNMAPPED_NODE_KIND",
        "UNMAPPED_RELATION",
    }


def test_policy_covers_source_graph_node_roles_that_are_not_business_impacts() -> None:
    policy = ReasoningPolicy.load()

    assert policy.node_kinds["automated-test"].role == "VALIDATION"
    assert policy.node_kinds["test-specification"].role == "VALIDATION"
    assert policy.node_kinds["data-fixture"].role == "EVIDENCE"
    assert policy.node_kinds["dataset"].role == "EVIDENCE"
    assert policy.retrieval_eval_set_id == "retrieval-boundaries-v1"
    assert policy.schema_version == "2.0.0"
    assert policy.ontology_id == "change-evidence-core"
    assert policy.ontology_version == "1.0.0"
    assert len(policy.ontology_sha256) == 64
    assert len(policy.policy_sha256) == 64
    assert Path(policy.retrieval_eval_set_path).name == "retrieval-boundaries-v1.json"


def test_core_reasoning_policy_contains_only_canonical_vocabulary() -> None:
    body = json.loads(Path("config/reasoning-policy.json").read_text(encoding="utf-8"))
    source_terms = {
        "apex-class",
        "apex-trigger",
        "lightning-component",
        "permission-set",
        "calls_internal_apex",
        "field_grant",
        "object_grant",
    }

    assert source_terms.isdisjoint(body["nodeKinds"])
    assert source_terms.isdisjoint(body["traversableRelations"])


def _ranking_source(node_count: int = 1) -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Ranking Fixture"},
        project_index={"sourceSnapshot": "ranking"},
        trusted_graph_sha256=fixture_digest("ranking-graph"),
        graph={
            "sourceSnapshot": "ranking",
            "nodes": [
                {
                    "id": f"field:Candidate{index}",
                    "kind": "field",
                    "label": f"Alpha candidate {index}",
                }
                for index in range(node_count)
            ],
            "edges": [],
        },
    )


def test_retrieval_threshold_boundary_is_policy_controlled() -> None:
    base = ReasoningPolicy.load()
    query = "alpha beta gamma delta"

    accepted = EvidenceRetriever(_ranking_source(), base).search(query, [])
    rejected = EvidenceRetriever(
        _ranking_source(), replace(base, long_query_minimum_score=0.26)
    ).search(query, [])

    assert [item.node["id"] for item in accepted] == ["field:Candidate0"]
    assert rejected == []


def test_ambiguity_limit_is_explicit_and_order_independent() -> None:
    base = ReasoningPolicy.load()
    source = _ranking_source(6)
    query = "alpha beta gamma delta"

    rejected = EvidenceRetriever(source, base).search(query, [])
    accepted = EvidenceRetriever(source, replace(base, ambiguity_tie_limit=7)).search(query, [])
    source.graph["nodes"].reverse()
    permuted = EvidenceRetriever(source, replace(base, ambiguity_tie_limit=7)).search(query, [])

    assert rejected == []
    assert [item.node["id"] for item in accepted] == [item.node["id"] for item in permuted]


def test_evaluation_set_content_is_pinned_into_reasoning_identity(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    ontology_dir = config_dir / "ontology"
    eval_dir = tmp_path / "quality" / "evals"
    ontology_dir.mkdir(parents=True)
    eval_dir.mkdir(parents=True)
    policy_body = json.loads(Path("config/reasoning-policy.json").read_text(encoding="utf-8"))
    eval_body = json.loads(
        Path("quality/evals/retrieval-boundaries-v1.json").read_text(encoding="utf-8")
    )
    policy_path = config_dir / "reasoning-policy.json"
    eval_path = eval_dir / "retrieval-boundaries-v1.json"
    ontology_path = ontology_dir / "canonical-ontology.json"
    eval_path.write_text(json.dumps(eval_body), encoding="utf-8")
    ontology_path.write_text(
        Path("config/ontology/canonical-ontology.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    policy_path.write_text(json.dumps(policy_body), encoding="utf-8")
    original = ReasoningPolicy.load(policy_path)

    eval_body["cases"][0]["expected"] = "TAMPERED"
    eval_path.write_text(json.dumps(eval_body), encoding="utf-8")
    with pytest.raises(ReasoningPolicyError, match="digest"):
        ReasoningPolicy.load(policy_path)

    new_eval_digest = hashlib.sha256(
        json.dumps(eval_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    policy_body["retrieval"]["evalSetSha256"] = new_eval_digest
    policy_path.write_text(json.dumps(policy_body), encoding="utf-8")
    changed = ReasoningPolicy.load(policy_path)

    assert changed.retrieval_eval_set_sha256 != original.retrieval_eval_set_sha256
    assert changed.policy_sha256 != original.policy_sha256


def test_capacity_gaps_preserve_all_mandatory_validations() -> None:
    source = policy_source()
    source.graph["nodes"] = [
        {"id": "field:Alpha", "kind": "field", "label": "Alpha threshold"},
        *[
            {
                "id": f"class:{index}",
                "kind": "apex-class",
                "label": f"Handler {index}",
            }
            for index in range(3)
        ],
        *[
            {
                "id": f"test:{index}",
                "kind": "apex-test",
                "label": f"Validation {index}",
                "mandatory": True,
            }
            for index in range(3)
        ],
    ]
    source.graph["edges"] = [
        *[
            {"from": f"class:{index}", "relation": "reads", "to": "field:Alpha"}
            for index in range(3)
        ],
        *[
            {"from": f"test:{index}", "relation": "tests", "to": "field:Alpha"}
            for index in range(3)
        ],
    ]
    add_trusted_envelopes(
        source.graph,
        snapshot_id=source.snapshot_id,
        source_hash=source.trusted_graph_sha256 or "",
    )
    constrained = replace(ReasoningPolicy.load(), max_impacts=1, max_selected_tests=1)
    service = AssuranceService(source, reasoning_policy=constrained)

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert len(run.impacts) == 1
    assert len(run.selected_tests) == 3
    assert all(item.classification == "MANDATORY" for item in run.selected_tests)
    assert {gap.code for gap in run.analysis_gaps} == {
        "TRUNCATED_IMPACTS",
        "VALIDATION_LIMIT_EXCEEDED",
    }
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE


def test_recommended_validation_truncation_is_visible_but_not_release_blocking() -> None:
    source = policy_source()
    source.graph["nodes"] = [
        {"id": "field:Alpha", "kind": "field", "label": "Alpha threshold"},
        *[
            {
                "id": f"test:{index}",
                "kind": "component-test",
                "label": f"Recommended validation {index}",
            }
            for index in range(3)
        ],
    ]
    source.graph["edges"] = [
        {"from": f"test:{index}", "relation": "tests", "to": "field:Alpha"} for index in range(3)
    ]
    add_trusted_envelopes(
        source.graph,
        snapshot_id=source.snapshot_id,
        source_hash=source.trusted_graph_sha256 or "",
    )
    constrained = replace(ReasoningPolicy.load(), max_selected_tests=1)
    service = AssuranceService(source, reasoning_policy=constrained)

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert len(run.selected_tests) == 1
    assert [(item.code, item.blocking) for item in run.analysis_gaps] == [
        ("RECOMMENDED_VALIDATION_TRUNCATED", False)
    ]
    assert run.governance and run.governance.passed
    semantics = next(
        item
        for item in run.governance.guardrails
        if item.control_id == "analysis.semantics-complete"
    )
    assert not semantics.blocking
    assert semantics.reason_code == "NON_BLOCKING_GAPS_OBSERVED"


def test_traversal_capacity_emits_explicit_gap() -> None:
    source = SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Traversal Fixture"},
        project_index={"sourceSnapshot": "traversal"},
        trusted_graph_sha256=fixture_digest("traversal-graph"),
        graph={
            "sourceSnapshot": "traversal",
            "nodes": [
                {"id": "field:Alpha", "kind": "field", "label": "Alpha threshold"},
                {"id": "class:Middle", "kind": "apex-class", "label": "Middle"},
                {"id": "class:End", "kind": "apex-class", "label": "End"},
            ],
            "edges": [
                {"from": "class:Middle", "relation": "reads", "to": "field:Alpha"},
                {"from": "class:End", "relation": "calls", "to": "class:Middle"},
            ],
        },
    )
    add_trusted_envelopes(
        source.graph,
        snapshot_id=source.snapshot_id,
        source_hash=source.trusted_graph_sha256 or "",
    )
    constrained = replace(ReasoningPolicy.load(), max_traversal_nodes=1)
    service = ChangeIntelligenceService(EvidenceRetriever(source), constrained)

    _, _, _, gaps = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "TRUNCATED_TRAVERSAL" in {gap.code for gap in gaps}


@pytest.mark.parametrize("state", ["INFERRED", "REJECTED", "STALE", "CONTRADICTORY"])
def test_untrusted_edge_states_cannot_enter_release_reasoning(state: str) -> None:
    source = policy_source()
    source.graph["edges"][0]["evidenceState"] = state
    service = AssuranceService(source)

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "test:Component" not in {item.test_id for item in run.selected_tests}
    assert (f"EVIDENCE_STATE_{state}", True) in {
        (item.code, item.blocking) for item in run.analysis_gaps
    }
    assert run.governance and not run.governance.passed
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE


def test_expired_or_cross_snapshot_edge_cannot_enter_release_reasoning() -> None:
    source = policy_source()
    source.graph["edges"][0]["validUntil"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    source.graph["edges"][1]["sourceSnapshot"] = "another-snapshot"
    service = AssuranceService(source)

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert {"EDGE_EXPIRED", "EDGE_SNAPSHOT_MISMATCH"} <= {item.code for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed


def test_edge_source_hash_must_match_the_verified_graph() -> None:
    source = policy_source()
    source.graph["edges"][0]["sourceHash"] = "f" * 64

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "EDGE_SOURCE_MISMATCH" in {item.code for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed


def test_missing_edge_evidence_state_never_defaults_to_confirmed() -> None:
    source = policy_source()
    del source.graph["edges"][0]["evidenceState"]

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "test:Component" not in {item.test_id for item in run.selected_tests}
    assert "MISSING_EVIDENCE_STATE" in {item.code for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed


def test_missing_node_evidence_state_is_visible_and_unverified() -> None:
    source = policy_source()
    del source.graph["nodes"][0]["evidenceState"]

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    field_evidence = next(
        item for item in run.evidence if item.attributes.get("entity_id") == "field:Alpha"
    )
    assert field_evidence.state == "UNVERIFIED"
    assert not run.impacts
    assert not run.selected_tests
    assert "MISSING_EVIDENCE_STATE" in {item.code for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed


@pytest.mark.parametrize(
    ("field", "expected_gap"),
    [
        ("sourceHash", "MISSING_SOURCE_HASH"),
        ("sourceSnapshot", "MISSING_SOURCE_SNAPSHOT"),
        ("extractorId", "MISSING_EXTRACTOR_ID"),
    ],
)
def test_incomplete_node_provenance_cannot_create_impacts_or_tests(
    field: str, expected_gap: str
) -> None:
    source = policy_source()
    del source.graph["nodes"][0][field]

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    evidence = next(item for item in run.evidence if item.attributes["entity_id"] == "field:Alpha")
    assert evidence.state == "UNVERIFIED"
    assert evidence.attributes["snapshot_id"] == source.graph["nodes"][0].get("sourceSnapshot")
    assert evidence.attributes["source_hash"] == source.graph["nodes"][0].get("sourceHash")
    assert not run.impacts
    assert not run.selected_tests
    assert expected_gap in {item.code for item in run.analysis_gaps}


def test_raw_human_confirmed_node_cannot_create_impacts_or_tests() -> None:
    source = policy_source()
    source.graph["nodes"][0]["evidenceState"] = "HUMAN_CONFIRMED"

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert not run.impacts
    assert not run.selected_tests
    assert "UNVERIFIED_HUMAN_CONFIRMATION" in {item.code for item in run.analysis_gaps}


def test_service_uses_one_immutable_normalized_snapshot_after_construction() -> None:
    source = policy_source()
    service = AssuranceService(source)
    bound_hash = source.normalized_graph.graph_sha256
    source.graph["edges"][0]["relation"] = "mutated_after_binding"
    source.graph["sourceSnapshot"] = "mutated-after-binding"
    source.contract["application"] = "Mutated Project"

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert run.normalized_graph_sha256 == bound_hash
    assert run.source_snapshot == "policy"
    assert run.request.project_id == "policy-fixture"
    assert "test:Component" in {item.test_id for item in run.selected_tests}
    assert "UNMAPPED_RELATION" not in {item.code for item in run.analysis_gaps}


def test_raw_human_confirmed_edge_requires_scoped_approval_receipt() -> None:
    source = policy_source()
    source.graph["edges"][0]["evidenceState"] = "HUMAN_CONFIRMED"

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "test:Component" not in {item.test_id for item in run.selected_tests}
    assert "UNVERIFIED_HUMAN_CONFIRMATION" in {item.code for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed
