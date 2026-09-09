from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from neo_sf_q_intel.api import create_app
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import ChangeRequest, DecisionCode, ReleaseDecision
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
    assert health.json()["status"] == "ok"
    assert health.json()["persistence_status"] == "degraded"
    assert health.json()["source_snapshot"] == "demo"
    assert health.json()["persistence"] == "memory-cache"
    assert health.json()["run_persistence"] == "memory-cache"
    assert health.json()["outcome_persistence"] == "process-cache"
    assert health.json()["outcome_durable"] is False
    assert health.json()["degradation_codes"] == []
    assert health.json()["gap_codes"] == ["OUTCOME_PROCESS_CACHE_NON_DURABLE"]
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


@pytest.mark.parametrize(
    "recorded_code",
    [DecisionCode.GO, DecisionCode.CONDITIONAL_GO, DecisionCode.NO_GO, None],
)
def test_api_revalidates_historical_release_decisions_on_get_and_list(
    recorded_code: DecisionCode | None,
) -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository)
    run = service.analyze(ChangeRequest(requirement="Assess a metadata change"))
    recorded_decision = (
        ReleaseDecision(
            code=recorded_code,
            reasons=["historical-policy-result"],
        )
        if recorded_code is not None
        else None
    )
    historical = run.model_copy(
        update={"decision": recorded_decision},
        deep=True,
    )
    repository.save(historical)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    get_payload = client.get(f"/api/v1/assurance-runs/{run.run_id}").json()
    list_payload = client.get("/api/v1/assurance-runs").json()[0]

    for payload in (get_payload, list_payload):
        assert payload["decision"]["code"] == "INCOMPLETE"
        assert payload["decision"]["reasons"] == ["RELEASE_EVIDENCE_MODEL_INCOMPLETE"]
        if recorded_code is None:
            assert payload["recorded_decision"] is None
        else:
            assert payload["recorded_decision"]["code"] == recorded_code
            assert payload["recorded_decision"]["reasons"] == ["historical-policy-result"]
    assert repository.get(run.run_id).decision == historical.decision


def test_api_read_fails_closed_when_current_governance_policy_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository)
    run = service.analyze(ChangeRequest(requirement="Assess a metadata change"))
    recorded = repository.get(run.run_id)
    assert recorded and recorded.decision
    monkeypatch.setattr(
        "neo_sf_q_intel.service.GovernancePolicy.load",
        lambda: (_ for _ in ()).throw(ValueError("invalid policy")),
    )
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    payload = client.get(f"/api/v1/assurance-runs/{run.run_id}").json()

    assert payload["decision"]["code"] == "INCOMPLETE"
    assert payload["decision"]["reasons"] == ["GOVERNANCE_POLICY_INVALID"]
    assert payload["recorded_decision"] == recorded.decision.model_dump(mode="json")
    assert repository.get(run.run_id) == recorded
