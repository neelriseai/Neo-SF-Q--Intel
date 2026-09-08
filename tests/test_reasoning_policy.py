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


def policy_source(relation: str = "tests") -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Policy Fixture"},
        project_index={"sourceSnapshot": "policy"},
        trusted_graph_sha256="policy-digest",
        graph={
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
        },
    )


def test_policy_classifies_validation_and_excludes_fixture_and_unknown_kinds() -> None:
    service = AssuranceService(policy_source())

    run = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert [item.entity_id for item in run.impacts] == ["field:Alpha"]
    assert {item.test_id for item in run.selected_tests} == {"test:Component", "test:Locator"}
    assert [gap.code for gap in run.analysis_gaps] == ["UNKNOWN_NODE_KIND"]
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
    service = ChangeIntelligenceService(EvidenceRetriever(source))

    _, impacts, _, gaps = service.analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "class:Hidden" not in {item.entity_id for item in impacts}
    assert {gap.code for gap in gaps} == {"UNKNOWN_RELATION", "UNKNOWN_NODE_KIND"}


def test_policy_covers_source_graph_node_roles_that_are_not_business_impacts() -> None:
    policy = ReasoningPolicy.load()

    assert policy.node_kinds["component-test"].role == "VALIDATION"
    assert policy.node_kinds["locator-use-case"].role == "VALIDATION"
    assert policy.node_kinds["synthetic-fixture"].role == "EVIDENCE"
    assert policy.node_kinds["synthetic-dataset"].role == "EVIDENCE"
    assert policy.retrieval_eval_set_id == "retrieval-boundaries-v1"
    assert policy.schema_version == "1.1.0"
    assert len(policy.policy_sha256) == 64
    assert Path(policy.retrieval_eval_set_path).name == "retrieval-boundaries-v1.json"


def _ranking_source(node_count: int = 1) -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Ranking Fixture"},
        project_index={"sourceSnapshot": "ranking"},
        trusted_graph_sha256="ranking-digest",
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
    eval_dir = tmp_path / "quality" / "evals"
    config_dir.mkdir()
    eval_dir.mkdir(parents=True)
    policy_body = json.loads(Path("config/reasoning-policy.json").read_text(encoding="utf-8"))
    eval_body = json.loads(
        Path("quality/evals/retrieval-boundaries-v1.json").read_text(encoding="utf-8")
    )
    policy_path = config_dir / "reasoning-policy.json"
    eval_path = eval_dir / "retrieval-boundaries-v1.json"
    eval_path.write_text(json.dumps(eval_body), encoding="utf-8")
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
        trusted_graph_sha256="traversal-digest",
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
    assert (f"EDGE_STATE_{state}", True) in {
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
    source.graph["edges"][0]["sourceHash"] = "different-digest"

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="Alpha threshold", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert "EDGE_SOURCE_MISMATCH" in {item.code for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed
