from __future__ import annotations

import inspect
import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from neo_sf_q_intel.api import create_app
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_baseline import LiveBaselineResult
from neo_sf_q_intel.live_read_evidence import LiveReadObservation, LiveReadResult
from neo_sf_q_intel.live_target_plan import TargetPartition
from neo_sf_q_intel.mcp_server import build_mcp
from neo_sf_q_intel.service import AssuranceService
from tests.test_workflow import source


def _live_read_result() -> LiveReadResult:
    observation = LiveReadObservation(
        target_sha256="1" * 64,
        partition=TargetPartition.STANDARD_REST,
        status="PASSED",
        artifact_sha256="2" * 64,
        assertions_sha256="3" * 64,
        request_sha256="4" * 64,
        item_count=7,
        byte_count=512,
        duration_ms=25,
        invocation_count=7,
        member_root_sha256=None,
    )
    body = {
        "authority_scope": "READ_ONLY_LIVE_EVIDENCE",
        "release_eligible": False,
        "evidence_phase": "LIVE_BASELINE",
        "plan_sha256": "5" * 64,
        "observations": (observation.model_dump(mode="json"),),
        "stored_receipt_ids": ("live-receipt:" + "6" * 64,),
    }
    return LiveReadResult(**body, result_sha256=stable_sha256(body))


def _result(
    state: str = "COMPLETED",
    *,
    modes: tuple[str, str] = ("SQLITE", "SQLITE"),
) -> LiveBaselineResult:
    return LiveBaselineResult(
        campaign_id="baseline:" + "7" * 32 if state == "COMPLETED" else None,
        state=state,
        ledger_mode=modes[0],
        assertion_store_mode=modes[1],
        gap_codes=(
            ("LIVE_CAMPAIGN_INCOMPLETE",)
            if state == "COMPLETED"
            else ("LIVE_BASELINE_TARGET_POLICY_DENIED",)
        ),
        degradation_codes=(
            ("LIVE_BASELINE_POSTGRESQL_UNAVAILABLE",) if state == "COMPLETED" else ()
        ),
        read_result=_live_read_result() if state == "COMPLETED" else None,
    )


@dataclass
class _Baseline:
    result: LiveBaselineResult | None = None
    fail: bool = False
    calls: int = 0

    def run(self) -> LiveBaselineResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("raw org response and secret must remain hidden")
        assert self.result is not None
        return self.result


def _service(baseline: _Baseline) -> AssuranceService:
    return AssuranceService(
        source(),
        live_baseline_service_factory=lambda _candidate_provider: baseline,
    )


def _client(baseline: _Baseline) -> TestClient:
    return TestClient(
        create_app(
            settings=Settings(_env_file=None, allow_llm=False),
            service=_service(baseline),
        )
    )


def test_service_returns_only_bounded_live_read_summary_without_run_promotion() -> None:
    baseline = _Baseline(_result())
    service = _service(baseline)

    view = service.run_live_baseline()

    assert baseline.calls == 1
    assert set(view.model_dump(mode="json")) == {
        "campaign_id",
        "state",
        "gap_codes",
        "ledger_mode",
        "assertion_store_mode",
        "read",
        "release_eligible",
    }
    assert view.read is not None
    assert view.read.observation_count == 1
    assert view.read.receipt_count == 1
    assert view.read.item_count == 7
    assert view.read.invocation_count == 7
    assert service.list_recent() == []
    serialized = view.model_dump_json()
    assert "live-receipt:" not in serialized
    assert "services/data" not in serialized
    assert "target_sha256" not in serialized


def test_service_unavailable_or_exception_is_sanitized_and_no_argument() -> None:
    unconfigured = AssuranceService(source())
    view = unconfigured.run_live_baseline()
    assert view.state == "BLOCKED"
    assert view.gap_codes == ("LIVE_BASELINE_SERVICE_UNAVAILABLE",)
    assert view.ledger_mode == "UNAVAILABLE"
    assert "raw org" not in _service(_Baseline(fail=True)).run_live_baseline().model_dump_json()
    assert tuple(inspect.signature(unconfigured.run_live_baseline).parameters) == ()
    with pytest.raises(TypeError):
        unconfigured.run_live_baseline("caller-scope")  # type: ignore[call-arg]


def test_api_positive_and_blocked_views_are_bounded() -> None:
    positive = _client(_Baseline(_result())).post("/api/v1/live-salesforce/baseline")
    blocked = _client(_Baseline(_result("BLOCKED"))).post("/api/v1/live-salesforce/baseline")
    unavailable = _client(_Baseline(_result("BLOCKED", modes=("UNAVAILABLE", "UNAVAILABLE")))).post(
        "/api/v1/live-salesforce/baseline"
    )

    assert positive.status_code == 200
    assert len(positive.content) < 8_192
    assert positive.json()["state"] == "COMPLETED"
    assert blocked.status_code == 409
    assert blocked.json()["state"] == "BLOCKED"
    assert unavailable.status_code == 503
    assert unavailable.json()["release_eligible"] is False


async def test_api_and_mcp_publish_no_live_baseline_input_fields() -> None:
    baseline = _Baseline(_result())
    client = _client(baseline)
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/live-salesforce/baseline"][
        "post"
    ]
    server = build_mcp(Settings(_env_file=None, allow_llm=False), _service(baseline))
    tool = next(
        item for item in await server.list_tools() if item.name == "run_live_salesforce_baseline"
    )

    assert "requestBody" not in operation
    assert operation.get("parameters", []) == []
    assert tool.input_schema.get("properties") == {}


@pytest.mark.parametrize(
    "request_kwargs",
    (
        {"content": b"{}"},
        {"params": {"alias": "caller-org"}},
        {"headers": {"x-salesforce-alias": "caller-org"}},
        {"headers": {"authorization": "Bearer caller-token"}},
    ),
)
def test_api_rejects_every_caller_scope_surface_before_service(
    request_kwargs: dict[str, object],
) -> None:
    baseline = _Baseline(_result())

    response = _client(baseline).post(
        "/api/v1/live-salesforce/baseline",
        **request_kwargs,  # type: ignore[arg-type]
    )

    assert response.status_code == 400
    assert response.json()["gap_codes"] == ["LIVE_BASELINE_CALLER_INPUT_FORBIDDEN"]
    assert baseline.calls == 0


@pytest.mark.parametrize(
    ("baseline_result", "expected_status"),
    (
        (_result(), 200),
        (_result("BLOCKED"), 409),
        (_result("BLOCKED", modes=("UNAVAILABLE", "UNAVAILABLE")), 503),
    ),
)
async def test_mcp_and_api_share_the_exact_application_projection(
    baseline_result: LiveBaselineResult,
    expected_status: int,
) -> None:
    api_baseline = _Baseline(baseline_result)
    mcp_baseline = _Baseline(baseline_result)
    api = _client(api_baseline).post("/api/v1/live-salesforce/baseline")
    server = build_mcp(
        Settings(_env_file=None, allow_llm=False),
        _service(mcp_baseline),
    )

    mcp = await server.call_tool("run_live_salesforce_baseline", {})
    body = json.loads(mcp.content[0].text)

    assert not mcp.is_error
    assert api.status_code == expected_status
    assert body == api.json()
    assert mcp_baseline.calls == api_baseline.calls == 1


async def test_mcp_rejects_extra_input_without_running_baseline() -> None:
    baseline = _Baseline(_result())
    server = build_mcp(Settings(_env_file=None, allow_llm=False), _service(baseline))

    with pytest.raises(ToolError, match="accepts no caller input"):
        await server.call_tool("run_live_salesforce_baseline", {"alias": "caller-org"})
    assert baseline.calls == 0
