from pathlib import Path

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import ChangeIntent, ChangeRequest
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot


def source() -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"schemaVersion": "1.4.0"},
        project_index={"sourceSnapshot": "snapshot"},
        trusted_graph_sha256="snapshot-digest",
        graph={
            "sourceSnapshot": "snapshot",
            "nodes": [
                {
                    "id": "field:Opportunity.Discount__c",
                    "kind": "field",
                    "label": "Discount",
                    "source": "force-app/objects/Opportunity/fields/Discount__c.field-meta.xml",
                },
                {
                    "id": "flow:Approval",
                    "kind": "flow",
                    "label": "Approval Flow",
                    "source": "force-app/flows/Approval.flow-meta.xml",
                },
                {
                    "id": "test:Policy",
                    "kind": "apex-test",
                    "label": "Policy Test",
                    "source": "force-app/classes/PolicyTest.cls",
                },
            ],
            "edges": [
                {
                    "from": "flow:Approval",
                    "relation": "reads",
                    "to": "field:Opportunity.Discount__c",
                },
                {"from": "test:Policy", "relation": "tests", "to": "flow:Approval"},
            ],
        },
    )


def test_generic_change_analysis_traverses_graph_and_selects_tests() -> None:
    service = ChangeIntelligenceService(EvidenceRetriever(source()))
    evidence, impacts, tests, gaps = service.analyze(
        ChangeRequest(
            requirement="Change Opportunity Discount policy",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )
    assert {item.entity_id for item in impacts} >= {
        "field:Opportunity.Discount__c",
        "flow:Approval",
    }
    assert "test:Policy" not in {item.entity_id for item in impacts}
    assert [item.test_id for item in tests] == ["test:Policy"]
    assert all(item.evidence_ids for item in impacts)
    assert {item.strength_basis for item in impacts} <= {
        "DECLARED_CHANGE_MATCH",
        "CONFIRMED_GRAPH_RELATION",
    }
    assert len(evidence) == 3
    assert not gaps


def test_informational_relevance_does_not_become_material_impact() -> None:
    service = ChangeIntelligenceService(EvidenceRetriever(source()))

    evidence, impacts, tests, gaps = service.analyze(
        ChangeRequest(requirement="Describe Opportunity Discount policy")
    )

    assert evidence
    assert impacts == []
    assert tests == []
    assert gaps == []


def test_changed_path_is_context_without_explicit_change_intent() -> None:
    service = ChangeIntelligenceService(EvidenceRetriever(source()))

    evidence, impacts, tests, gaps = service.analyze(
        ChangeRequest(
            requirement="Assess the configured change",
            changed_paths=["force-app/objects/Opportunity/fields/Discount__c.field-meta.xml"],
        )
    )

    assert evidence
    assert impacts == []
    assert tests == []
    assert gaps == []


def test_planned_change_is_labelled_as_declared_not_observed() -> None:
    service = ChangeIntelligenceService(EvidenceRetriever(source()))

    _, impacts, _, _ = service.analyze(
        ChangeRequest(
            requirement="Assess the configured change",
            changed_paths=["force-app/objects/Opportunity/fields/Discount__c.field-meta.xml"],
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )

    direct = next(item for item in impacts if item.entity_id.startswith("field:"))
    assert direct.relation == "direct:declared"


def test_observed_change_abstains_without_verified_diff_receipt() -> None:
    service = ChangeIntelligenceService(EvidenceRetriever(source()))

    _, impacts, tests, gaps = service.analyze(
        ChangeRequest(
            requirement="Opportunity Discount policy changed",
            change_intent=ChangeIntent.OBSERVED_CHANGE,
        )
    )

    assert impacts == []
    assert tests == []
    assert [(item.code, item.blocking) for item in gaps] == [("OBSERVED_CHANGE_UNVERIFIED", True)]
