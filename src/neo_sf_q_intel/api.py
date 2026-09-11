from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as FastAPIPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from neo_sf_q_intel.candidate_assurance import CandidateAssuranceView
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import AssuranceRun, ChangeIntent, ChangeRequest
from neo_sf_q_intel.foundation_pipeline import CandidateFoundationEvidence
from neo_sf_q_intel.live_campaign_status import (
    LiveCampaignStatus,
    LiveCampaignStatusError,
)
from neo_sf_q_intel.service import (
    AssuranceService,
    FoundationPipelineUnavailable,
    LiveBaselineView,
    create_service,
)


class FoundationCaptureProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["FOUNDATION_CAPTURE_PROBLEM"] = "FOUNDATION_CAPTURE_PROBLEM"
    code: Literal["FOUNDATION_SCOPE_INPUT_FORBIDDEN", "FOUNDATION_PIPELINE_UNAVAILABLE"]
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    evidence_completeness: Literal["INCOMPLETE"] = "INCOMPLETE"
    release_eligible: Literal[False] = False
    retryable: bool


class LiveCampaignStatusProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["LIVE_CAMPAIGN_STATUS_PROBLEM"] = "LIVE_CAMPAIGN_STATUS_PROBLEM"
    code: Literal["LIVE_CAMPAIGN_STATUS_UNAVAILABLE"] = "LIVE_CAMPAIGN_STATUS_UNAVAILABLE"
    evidence_completeness: Literal["INCOMPLETE"] = "INCOMPLETE"
    release_eligible: Literal[False] = False
    retryable: Literal[False] = False


_FOUNDATION_SCOPE_HEADER_SEGMENTS = frozenset(
    {
        "artifact",
        "base",
        "branch",
        "candidate",
        "change",
        "changed",
        "commit",
        "graph",
        "path",
        "paths",
        "policy",
        "project",
        "ref",
        "repository",
        "root",
        "scope",
        "seed",
        "snapshot",
        "source",
        "time",
    }
)


def _is_foundation_scope_header(name: str) -> bool:
    normalized = name.casefold().replace("_", "-")
    if not normalized.startswith("x-"):
        return False
    return bool(set(normalized.removeprefix("x-").split("-")) & _FOUNDATION_SCOPE_HEADER_SEGMENTS)


async def _foundation_request_has_body(request: Request) -> bool:
    declared_length = request.headers.get("content-length")
    if declared_length is not None and declared_length.strip() != "0":
        return True
    async for chunk in request.stream():
        if chunk:
            return True
    return False


def _is_live_baseline_caller_header(name: str) -> bool:
    normalized = name.casefold().replace("_", "-")
    return normalized.startswith("x-") or normalized in {"authorization", "cookie"}


def _live_baseline_problem(code: str) -> LiveBaselineView:
    return LiveBaselineView(
        state="BLOCKED",
        gap_codes=(code,),
        ledger_mode="UNAVAILABLE",
        assertion_store_mode="UNAVAILABLE",
    )


def _foundation_problem(code: str, *, retryable: bool, status_code: int) -> JSONResponse:
    problem = FoundationCaptureProblem.model_validate({"code": code, "retryable": retryable})
    return JSONResponse(status_code=status_code, content=problem.model_dump(mode="json"))


def create_app(
    settings: Settings | None = None,
    service: AssuranceService | None = None,
    repository_root: Path | None = None,
) -> FastAPI:
    active_settings = settings or Settings()
    root = repository_root or Path.cwd()
    active_service = service or create_service(active_settings, root)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        active_service.close()

    app = FastAPI(
        title="Neo SF Q-Intel API",
        version="0.1.0",
        description="Evidence-grounded Salesforce change assurance",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.parsed_web_allowed_origins()),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        degradation_codes = tuple(
            dict.fromkeys(
                (
                    *active_service.degradation_codes,
                    *active_settings.provider_configuration_codes,
                )
            )
        )
        return {
            "status": "ok",
            "persistence_status": (
                "degraded" if degradation_codes or active_service.gap_codes else "ready"
            ),
            "provider": active_settings.ai_provider,
            "provider_status": (
                "disabled"
                if not active_settings.allow_llm
                else "blocked"
                if active_settings.provider_calls_blocked
                else "ready"
            ),
            "provider_configuration_codes": list(active_settings.provider_configuration_codes),
            "source_snapshot": active_service.source.snapshot_id,
            "persistence": active_service.persistence_mode,
            "persistence_warning": active_service.persistence_warning,
            "run_persistence": active_service.run_persistence,
            "outcome_persistence": active_service.outcome_persistence,
            "outcome_durable": active_service.outcome_durable,
            "degradation_codes": list(degradation_codes),
            "gap_codes": list(active_service.gap_codes),
            "foundation_capture_configured": active_service.foundation_capture_configured,
            "foundation_capture_configuration_code": active_service.foundation_configuration_code,
            "live_receipt_ledger_mode": active_service.live_receipt_ledger_mode,
            "live_receipt_ledger_degradation_code": (
                active_service.live_receipt_ledger_degradation_code
            ),
        }

    @app.post(
        "/api/v1/foundation/candidate-evidence",
        response_model=CandidateFoundationEvidence,
        responses={
            400: {"model": FoundationCaptureProblem},
            503: {"model": FoundationCaptureProblem},
        },
    )
    async def capture_candidate_foundation(
        request: Request,
    ) -> CandidateFoundationEvidence | JSONResponse:
        if (
            request.query_params
            or any(_is_foundation_scope_header(name) for name in request.headers)
            or await _foundation_request_has_body(request)
        ):
            return _foundation_problem(
                "FOUNDATION_SCOPE_INPUT_FORBIDDEN",
                retryable=False,
                status_code=400,
            )
        try:
            evidence = await run_in_threadpool(active_service.capture_candidate_foundation)
            if len(evidence.model_dump_json().encode("utf-8")) > 32_768:
                raise FoundationPipelineUnavailable
            return evidence
        except Exception:
            return _foundation_problem(
                "FOUNDATION_PIPELINE_UNAVAILABLE",
                retryable=False,
                status_code=503,
            )

    @app.post("/api/v1/assurance-runs", response_model=AssuranceRun)
    def create_assurance_run(request: ChangeRequest) -> AssuranceRun:
        if request.change_intent is ChangeIntent.VERIFIED_CHANGE:
            raise HTTPException(
                status_code=400,
                detail="VERIFIED_CHANGE_REQUIRES_HOST_CAPTURE",
            )
        return active_service.analyze(request)

    @app.post(
        "/api/v1/live-salesforce/baseline",
        response_model=LiveBaselineView,
        responses={
            400: {"model": LiveBaselineView},
            409: {"model": LiveBaselineView},
            503: {"model": LiveBaselineView},
        },
    )
    async def run_live_salesforce_baseline(request: Request) -> LiveBaselineView | JSONResponse:
        if (
            request.query_params
            or any(_is_live_baseline_caller_header(name) for name in request.headers)
            or await _foundation_request_has_body(request)
        ):
            problem = _live_baseline_problem("LIVE_BASELINE_CALLER_INPUT_FORBIDDEN")
            return JSONResponse(status_code=400, content=problem.model_dump(mode="json"))
        view = await run_in_threadpool(active_service.run_live_baseline)
        if len(view.model_dump_json().encode("utf-8")) > 8_192:
            view = _live_baseline_problem("LIVE_BASELINE_RESPONSE_CAPACITY_EXCEEDED")
        if view.state != "BLOCKED":
            return view
        unavailable = (
            view.ledger_mode == "UNAVAILABLE"
            or view.assertion_store_mode == "UNAVAILABLE"
            or any(
                code.endswith(("_UNAVAILABLE", "_NOT_CONFIGURED", "_NOT_ENABLED"))
                or "CONFIGURATION" in code
                for code in view.gap_codes
            )
        )
        return JSONResponse(
            status_code=503 if unavailable else 409,
            content=view.model_dump(mode="json"),
        )

    @app.post(
        "/api/v1/assurance-runs/analyze-current-candidate",
        response_model=CandidateAssuranceView,
        responses={
            400: {"model": FoundationCaptureProblem},
            503: {"model": FoundationCaptureProblem},
        },
    )
    async def analyze_current_candidate(
        request: Request,
    ) -> CandidateAssuranceView | JSONResponse:
        if (
            request.query_params
            or any(_is_foundation_scope_header(name) for name in request.headers)
            or await _foundation_request_has_body(request)
        ):
            return _foundation_problem(
                "FOUNDATION_SCOPE_INPUT_FORBIDDEN",
                retryable=False,
                status_code=400,
            )
        try:
            return await run_in_threadpool(active_service.analyze_current_candidate_view)
        except Exception:
            return _foundation_problem(
                "FOUNDATION_PIPELINE_UNAVAILABLE",
                retryable=False,
                status_code=503,
            )

    @app.get("/api/v1/assurance-runs", response_model=list[AssuranceRun])
    def list_assurance_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[AssuranceRun]:
        return active_service.list_recent(limit)

    @app.get("/api/v1/assurance-runs/{run_id}", response_model=AssuranceRun)
    def get_assurance_run(run_id: UUID) -> AssuranceRun:
        result = active_service.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Assurance run not found")
        return result

    @app.get(
        "/api/v1/live-campaigns/{campaign_id}/status",
        response_model=LiveCampaignStatus,
        responses={503: {"model": LiveCampaignStatusProblem}},
    )
    def get_live_campaign_status(
        campaign_id: Annotated[
            str,
            FastAPIPath(
                min_length=1,
                max_length=256,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
            ),
        ],
    ) -> LiveCampaignStatus | JSONResponse:
        try:
            return active_service.get_live_campaign_status(campaign_id)
        except LiveCampaignStatusError:
            problem = LiveCampaignStatusProblem()
            return JSONResponse(status_code=503, content=problem.model_dump(mode="json"))

    @app.get("/api/v1/evidence/semantic-search")
    async def semantic_search(
        query: str = Query(min_length=3, max_length=2_000),
        limit: int = Query(default=8, ge=1, le=50),
    ) -> list[dict]:
        try:
            hits = await active_service.semantic_search(query, limit)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return [
            {
                "evidence": hit.evidence.model_dump(mode="json"),
                "score": hit.score,
            }
            for hit in hits
        ]

    return app


def run() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
