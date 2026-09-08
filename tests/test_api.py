from uuid import UUID

from fastapi.testclient import TestClient

from neo_sf_q_intel.api import create_app
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.service import AssuranceService
from tests.test_workflow import source


def test_api_exposes_health_and_typed_analysis() -> None:
    settings = Settings(allow_llm=False)
    service = AssuranceService(source(), InMemoryRunRepository())
    client = TestClient(create_app(settings=settings, service=service))

    health = client.get("/health")
    response = client.post(
        "/api/v1/assurance-runs",
        json={
            "requirement": "Change Deal Workbench layout",
            "change_intent": "PLANNED_CHANGE",
        },
    )

    assert health.status_code == 200
    assert health.json()["source_snapshot"] == "demo"
    assert health.json()["persistence"] == "memory-cache"
    assert response.status_code == 200
    assert response.json()["decision"]["code"] == "INCOMPLETE"


def test_api_uses_neutral_intent_when_the_caller_does_not_assert_a_change() -> None:
    service = AssuranceService(source(), InMemoryRunRepository())
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post(
        "/api/v1/assurance-runs",
        json={"requirement": "Change Deal Workbench layout"},
    )

    assert response.status_code == 200
    assert response.json()["request"]["change_intent"] == "INFORMATIONAL"
    assert response.json()["impacts"] == []
    assert len(response.json()["activities"]) == 4


def test_api_returns_persisted_incomplete_decision_for_stage_failure() -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository)

    class FailingAnalysis:
        policy = service.workflow.analysis.policy

        def analyze(self, request):  # noqa: ANN001, ANN201
            raise RuntimeError("sensitive dependency detail")

    service.workflow.analysis = FailingAnalysis()  # type: ignore[assignment]
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post(
        "/api/v1/assurance-runs",
        json={"requirement": "Assess a metadata change"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "FAILED"
    assert payload["decision"] == {
        "code": "INCOMPLETE",
        "reasons": ["STAGE_EXECUTION_FAILED: trusted stage output is unavailable."],
        "evidence_ids": [],
    }
    assert repository.get(UUID(payload["run_id"])) is not None
