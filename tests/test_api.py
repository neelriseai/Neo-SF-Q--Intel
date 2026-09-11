import asyncio
from pathlib import Path
from threading import get_ident
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import neo_sf_q_intel.api as api_module
from neo_sf_q_intel.api import create_app
from neo_sf_q_intel.config import PROVIDER_CREDENTIAL_SOURCE_CONFLICT, Settings
from neo_sf_q_intel.domain import ChangeRequest, DecisionCode, ReleaseDecision
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.service import AssuranceService
from tests.test_foundation_pipeline import _pipeline, _repository
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
    assert health.json()["foundation_capture_configured"] is False
    assert (
        health.json()["foundation_capture_configuration_code"]
        == "FOUNDATION_PIPELINE_NOT_CONFIGURED"
    )
    assert response.status_code == 200
    assert response.json()["decision"]["code"] == "INCOMPLETE"


def test_api_cors_uses_the_exact_configured_dashboard_origin_allowlist() -> None:
    settings = Settings(
        allow_llm=False,
        web_allowed_origins="http://localhost:3100,https://neo.example.test",
    )
    service = AssuranceService(source(), InMemoryRunRepository())
    client = TestClient(create_app(settings=settings, service=service))

    allowed = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3100",
            "Access-Control-Request-Method": "GET",
        },
    )
    blocked = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3200",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3100"
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


def test_health_surfaces_provider_source_conflict_without_configuration_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=project-secret\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-process-secret")
    settings = Settings(ai_provider="openai", _env_file=env_file)
    service = AssuranceService(source(), InMemoryRunRepository())

    response = TestClient(create_app(settings=settings, service=service)).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["provider_status"] == "blocked"
    assert body["provider_configuration_codes"] == [
        PROVIDER_CREDENTIAL_SOURCE_CONFLICT
    ]
    assert PROVIDER_CREDENTIAL_SOURCE_CONFLICT in body["degradation_codes"]
    rendered = response.text
    assert "project-secret" not in rendered
    assert "stale-process-secret" not in rendered


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


def test_foundation_capture_api_returns_only_fresh_bounded_projection(
    tmp_path: Path,
) -> None:
    repository = InMemoryRunRepository()
    service = AssuranceService(
        source(),
        repository,
        foundation_pipeline=_pipeline(_repository(tmp_path)),
    )
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post("/api/v1/foundation/candidate-evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authority_scope"] == "ANALYSIS_ONLY"
    assert payload["evidence_completeness"] == "INCOMPLETE"
    assert payload["release_eligible"] is False
    assert payload["non_authoritative_projection"] is True
    assert "RELEASE_EVIDENCE_MODEL_INCOMPLETE" in payload["blocking_gap_codes"]
    assert "verified_change" not in payload
    assert "produced_graph" not in payload
    assert "operation_seeds" not in payload
    assert len(response.content) <= 32_768
    assert repository.runs == {}


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"{}", "application/json"),
        (b"null", "application/json"),
        (b'{"repository_root":"C:/private"}', "application/json"),
        (b"path=C%3A%2Fprivate", "application/x-www-form-urlencoded"),
    ],
)
def test_foundation_capture_api_rejects_every_nonempty_body_before_service(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    content_type: str,
) -> None:
    service = AssuranceService(source())
    calls = 0

    def forbidden() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(service, "capture_candidate_foundation", forbidden)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post(
        "/api/v1/foundation/candidate-evidence",
        content=content,
        headers={"content-type": content_type},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "FOUNDATION_SCOPE_INPUT_FORBIDDEN"
    assert calls == 0


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"params": {"path": "C:/private"}},
        {"headers": {"x-repository-root": "C:/private"}},
        {"headers": {"x-salesforce-app-root": "C:/private"}},
        {"headers": {"x-salesforce-repository-root": "C:/private"}},
        {"headers": {"x-seed-ids": "seed:a"}},
        {"headers": {"x-base-ref": "main"}},
        {"headers": {"x-branch": "candidate"}},
        {"headers": {"x-change-paths": "force-app/main"}},
    ],
)
def test_foundation_capture_api_rejects_query_and_scope_headers(
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict,
) -> None:
    service = AssuranceService(source())
    calls = 0

    def forbidden() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(service, "capture_candidate_foundation", forbidden)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post("/api/v1/foundation/candidate-evidence", **request_kwargs)

    assert response.status_code == 400
    assert response.json()["code"] == "FOUNDATION_SCOPE_INPUT_FORBIDDEN"
    assert calls == 0


def test_foundation_capture_api_has_no_request_body_and_sanitizes_unavailability() -> None:
    service = AssuranceService(source())
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    operation = client.get("/openapi.json").json()["paths"][
        "/api/v1/foundation/candidate-evidence"
    ]["post"]
    response = client.post("/api/v1/foundation/candidate-evidence")

    assert "requestBody" not in operation
    assert response.status_code == 503
    assert response.json() == {
        "type": "FOUNDATION_CAPTURE_PROBLEM",
        "code": "FOUNDATION_PIPELINE_UNAVAILABLE",
        "authority_scope": "ANALYSIS_ONLY",
        "evidence_completeness": "INCOMPLETE",
        "release_eligible": False,
        "retryable": False,
    }


def test_foundation_capture_api_rejects_large_body_without_request_body_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AssuranceService(source())
    calls = 0

    def forbidden() -> None:
        nonlocal calls
        calls += 1

    async def body_must_not_be_used(_request: Request) -> bytes:
        raise AssertionError("request body was buffered")

    monkeypatch.setattr(service, "capture_candidate_foundation", forbidden)
    monkeypatch.setattr(Request, "body", body_must_not_be_used)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post(
        "/api/v1/foundation/candidate-evidence",
        content=b"x" * 2_000_000,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "FOUNDATION_SCOPE_INPUT_FORBIDDEN"
    assert calls == 0


def test_foundation_body_probe_stops_after_first_chunk_without_content_length() -> None:
    receive_calls = 0

    async def receive() -> dict[str, object]:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls > 1:
            raise AssertionError("body probe read beyond the first nonempty chunk")
        return {"type": "http.request", "body": b"x", "more_body": True}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/foundation/candidate-evidence",
            "headers": [],
        },
        receive,
    )

    assert asyncio.run(api_module._foundation_request_has_body(request)) is True
    assert receive_calls == 1


def test_foundation_capture_api_runs_blocking_capture_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AssuranceService(source())
    event_loop_threads: list[int] = []
    capture_threads: list[int] = []
    original = api_module.run_in_threadpool

    async def recording_threadpool(function):  # noqa: ANN001, ANN202
        event_loop_threads.append(get_ident())
        return await original(function)

    def unavailable() -> None:
        capture_threads.append(get_ident())
        raise RuntimeError("must be sanitized")

    monkeypatch.setattr(api_module, "run_in_threadpool", recording_threadpool)
    monkeypatch.setattr(service, "capture_candidate_foundation", unavailable)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post("/api/v1/foundation/candidate-evidence")

    assert response.status_code == 503
    assert event_loop_threads and capture_threads
    assert capture_threads[0] != event_loop_threads[0]


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"json": {}},
        {"params": {"alias": "caller-scope"}},
        {"headers": {"x-target-org": "caller-scope"}},
        {"headers": {"authorization": "Bearer caller-token"}},
    ],
)
def test_live_operator_advisory_rejects_caller_scope_before_service(
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict,
) -> None:
    service = AssuranceService(source())
    calls = 0

    def forbidden():  # noqa: ANN202
        nonlocal calls
        calls += 1
        raise AssertionError("service must not run")

    monkeypatch.setattr(service, "run_live_operator_advisory_demo", forbidden)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post("/api/v1/demo/live-operator-advisory", **request_kwargs)

    assert response.status_code == 400
    assert response.json()["gap_codes"] == ["LIVE_DEMO_CALLER_INPUT_FORBIDDEN"]
    assert response.json()["release_eligible"] is False
    assert calls == 0
