import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from neo_sf_q_intel.domain import (
    AgentActivity,
    AgentStatus,
    ChangeIntent,
    ChangeRequest,
    DecisionCode,
    RunStatus,
)
from neo_sf_q_intel.observability import MAX_AUDIT_EVENT_BYTES, emit_agent_activity
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot
from neo_sf_q_intel.service import AssuranceService
from tests.graph_fixtures import add_trusted_envelopes, fixture_digest


def source() -> SalesforceSourceSnapshot:
    source_hash = fixture_digest("workflow-graph")
    graph = {
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
    }
    add_trusted_envelopes(graph, snapshot_id="demo", source_hash=source_hash)
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"schemaVersion": "1.4.0", "application": "Workflow Fixture"},
        project_index={"sourceSnapshot": "demo"},
        trusted_graph_sha256=source_hash,
        graph=graph,
    )


def test_specialist_agents_produce_traceable_governed_run() -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository)
    run = service.analyze(
        ChangeRequest(
            requirement="Change Deal Workbench layout",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )

    assert run.status is RunStatus.COMPLETED
    assert run.reasoning_policy_version == "2.0.0"
    assert len(run.reasoning_policy_sha256) == 64
    assert run.reasoning_eval_set_id == "retrieval-boundaries-v1"
    assert len(run.reasoning_eval_set_sha256) == 64
    assert run.schema_version == "2.0.0"
    assert run.source_snapshot == "demo"
    assert run.source_graph_sha256 == fixture_digest("workflow-graph")
    assert run.ontology_id == "change-evidence-core"
    assert run.ontology_version == "1.1.0"
    assert len(run.ontology_sha256) == 64
    assert run.source_profile_id == "salesforce-application-graph"
    assert run.source_profile_version == "1.0.1"
    assert len(run.source_profile_sha256) == 64
    assert len(run.normalized_graph_sha256) == 64
    assert {
        reference.split(":", 1)[0]
        for reference in run.activities[0].policy_refs
    } == {
        "reasoning",
        "project",
        "source-snapshot",
        "ontology",
        "source-profile",
        "normalized-graph",
        "source-graph",
    }
    assert [activity.agent for activity in run.activities] == [
        "Change Analyst",
        "Test Intelligence",
        "UI Healing",
        "Governance Review",
    ]
    assert run.selected_tests[0].test_id == "test:Workbench"
    assert run.healing_proposals[0].requires_human_approval
    assert len(run.deliverables) == 4
    assert all(activity.capability_ids for activity in run.activities)
    assert all(activity.completed_at >= activity.started_at for activity in run.activities)
    assert run.activities[2].gap_codes == ["BROWSER_WORKER_UNAVAILABLE"]
    assert run.deliverables[2].nonblocking_gaps == ["BROWSER_WORKER_UNAVAILABLE"]
    assert run.governance and run.governance.passed
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE
    assert repository.get(run.run_id) == run
    assert run.request.project_id == "workflow-fixture"


def test_no_evidence_causes_abstention_instead_of_invention() -> None:
    service = AssuranceService(source())
    run = service.analyze(ChangeRequest(requirement="Unrelated quantum payroll change"))

    assert not run.evidence
    assert run.source_snapshot == "demo"
    assert run.source_graph_sha256 == fixture_digest("workflow-graph")
    assert len(run.normalized_graph_sha256) == 64
    assert run.decision and run.decision.code is DecisionCode.INCOMPLETE
    assert run.activities[0].status == "ABSTAINED"


def test_stage_logging_is_structured_and_does_not_copy_requirement(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = AssuranceService(source())
    secret_marker = "private-requirement-text"

    with caplog.at_level(logging.INFO, logger="neo_sf_q_intel.agent_activity"):
        run = service.analyze(ChangeRequest(requirement=f"Assess {secret_marker}"))

    events = [json.loads(record.message) for record in caplog.records]
    assert len(events) == 4
    assert all(event["run_id"] == str(run.run_id) for event in events)
    assert all(event["trace_id"] == str(run.trace_id) for event in events)
    assert all(event["capability_ids"] for event in events)
    assert all(secret_marker not in record.message for record in caplog.records)


def test_stage_logging_hashes_untrusted_references_and_is_byte_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime.now(UTC)
    poisoned = "https://instance.example.test/session?token=secret-value"
    activity = AgentActivity(
        capability_ids=["reasoning.graph-impact"],
        agent="Change Analyst",
        stage="change_analysis",
        status=AgentStatus.COMPLETED,
        started_at=now,
        completed_at=now,
        duration_ms=1,
        summary="Bounded diagnostic event",
        input_evidence_ids=[poisoned, *(f"evidence:{index}:" + "x" * 450 for index in range(99))],
        output_artifact_ids=[poisoned],
        policy_refs=[poisoned],
    )

    with caplog.at_level(logging.INFO, logger="neo_sf_q_intel.agent_activity"):
        emit_agent_activity(
            run_id=uuid4(),
            trace_id=uuid4(),
            activity=activity,
        )

    event = caplog.records[-1].message
    payload = json.loads(event)
    assert poisoned not in event
    assert "secret-value" not in event
    assert len(event.encode("utf-8")) <= MAX_AUDIT_EVENT_BYTES
    assert payload["input_evidence_count"] == 100
    assert len(payload["input_evidence_refs"]) == 25


def test_throwing_stage_emits_one_sanitized_failure_and_stops_downstream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository)

    class FailingAnalysis:
        policy = service.workflow.analysis.policy

        def analyze(self, request: ChangeRequest):  # noqa: ANN201
            raise RuntimeError("secret-value https://instance.example.test/session")

    service.workflow.analysis = FailingAnalysis()  # type: ignore[assignment]

    with caplog.at_level(logging.INFO, logger="neo_sf_q_intel.agent_activity"):
        result = service.analyze(ChangeRequest(requirement="Assess a metadata change"))

    assert result.status is RunStatus.FAILED
    assert result.decision and result.decision.code is DecisionCode.INCOMPLETE
    assert result.decision.reasons == [
        "STAGE_EXECUTION_FAILED: trusted stage output is unavailable."
    ]
    assert len(result.activities) == 1
    assert len(result.deliverables) == 1
    assert result.activities[0].status is AgentStatus.FAILED
    assert result.activities[0].error_class == "STAGE_ERROR"
    assert result.deliverables[0].blocking_gaps == ["STAGE_EXECUTION_FAILED"]
    assert repository.get(result.run_id) == result
    assert len(caplog.records) == 1
    assert "secret-value" not in caplog.records[0].message
    assert "https://" not in caplog.records[0].message


def test_rejects_cross_project_evidence_use() -> None:
    service = AssuranceService(source())

    with pytest.raises(ValueError, match="does not match loaded source"):
        service.analyze(
            ChangeRequest(
                requirement="Assess the workbench",
                project_id="another-project",
            )
        )
