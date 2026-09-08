from pathlib import Path

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import ChangeRequest
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot


def source() -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"schemaVersion": "1.4.0"},
        project_index={"sourceSnapshot": "snapshot"},
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
        ChangeRequest(requirement="Change Opportunity Discount policy")
    )
    assert {item.entity_id for item in impacts} >= {
        "field:Opportunity.Discount__c",
        "flow:Approval",
    }
    assert "test:Policy" not in {item.entity_id for item in impacts}
    assert [item.test_id for item in tests] == ["test:Policy"]
    assert all(item.evidence_ids for item in impacts)
    assert len(evidence) == 3
    assert not gaps
