from pathlib import Path

from neo_sf_q_intel.domain import ChangeRequest, DecisionCode, RunStatus
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot
from neo_sf_q_intel.service import AssuranceService


def source() -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"schemaVersion": "1.4.0"},
        project_index={"sourceSnapshot": "demo"},
        graph={
            "sourceSnapshot": "demo",
            "nodes": [
                {
                    "id": "lwc:Workbench",
                    "kind": "lightning-component",
                    "label": "Deal Workbench",
                    "source": "force-app/lwc/workbench/workbench.js",
                },
                {
                    "id": "test:Workbench",
                    "kind": "apex-test",
                    "label": "Workbench Test",
                    "source": "force-app/classes/WorkbenchTest.cls",
                },
            ],
            "edges": [
                {
                    "from": "test:Workbench",
                    "relation": "tests",
                    "to": "lwc:Workbench",
                }
            ],
        },
    )


def test_specialist_agents_produce_traceable_governed_run() -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository)
    run = service.analyze(ChangeRequest(requirement="Change Deal Workbench layout"))

    assert run.status is RunStatus.COMPLETED
    assert [activity.agent for activity in run.activities] == [
        "Change Analyst",
        "Test Intelligence",
        "UI Healing",
        "Governance Review",
    ]
    assert run.selected_tests[0].test_id == "test:Workbench"
    assert run.healing_proposals[0].requires_human_approval
    assert run.governance and run.governance.passed
    assert run.decision and run.decision.code is DecisionCode.CONDITIONAL_GO
    assert repository.get(run.run_id) == run


def test_no_evidence_causes_abstention_instead_of_invention() -> None:
    service = AssuranceService(source())
    run = service.analyze(ChangeRequest(requirement="Unrelated quantum payroll change"))

    assert not run.evidence
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE
    assert run.activities[0].status == "ABSTAINED"
