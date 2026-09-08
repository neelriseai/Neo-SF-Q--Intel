from dataclasses import replace
from pathlib import Path

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import ChangeRequest, DecisionCode
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot
from neo_sf_q_intel.service import AssuranceService


def policy_source(relation: str = "tests") -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Policy Fixture"},
        project_index={"sourceSnapshot": "policy"},
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

    run = service.analyze(ChangeRequest(requirement="Alpha threshold"))

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

    _, impacts, _, gaps = service.analyze(ChangeRequest(requirement="Alpha threshold"))

    assert "class:Hidden" not in {item.entity_id for item in impacts}
    assert {gap.code for gap in gaps} == {"UNKNOWN_RELATION", "UNKNOWN_NODE_KIND"}


def test_policy_covers_source_graph_node_roles_that_are_not_business_impacts() -> None:
    policy = ReasoningPolicy.load()

    assert policy.node_kinds["component-test"].role == "VALIDATION"
    assert policy.node_kinds["locator-use-case"].role == "VALIDATION"
    assert policy.node_kinds["synthetic-fixture"].role == "EVIDENCE"
    assert policy.node_kinds["synthetic-dataset"].role == "EVIDENCE"


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

    run = service.analyze(ChangeRequest(requirement="Alpha threshold"))

    assert len(run.impacts) == 1
    assert len(run.selected_tests) == 3
    assert all(item.classification == "MANDATORY" for item in run.selected_tests)
    assert {gap.code for gap in run.analysis_gaps} == {
        "TRUNCATED_IMPACTS",
        "VALIDATION_LIMIT_EXCEEDED",
    }
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE


def test_traversal_capacity_emits_explicit_gap() -> None:
    source = SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": "Traversal Fixture"},
        project_index={"sourceSnapshot": "traversal"},
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

    _, _, _, gaps = service.analyze(ChangeRequest(requirement="Alpha threshold"))

    assert "TRUNCATED_TRAVERSAL" in {gap.code for gap in gaps}
