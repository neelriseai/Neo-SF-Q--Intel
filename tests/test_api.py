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
        json={"requirement": "Change Deal Workbench layout"},
    )

    assert health.status_code == 200
    assert health.json()["source_snapshot"] == "demo"
    assert response.status_code == 200
    assert response.json()["decision"]["code"] == "CONDITIONAL_GO"
    assert len(response.json()["activities"]) == 4
