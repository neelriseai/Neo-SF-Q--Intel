from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest
from neo_sf_q_intel.foundation_pipeline import CandidateFoundationEvidence
from neo_sf_q_intel.service import (
    AssuranceService,
    FoundationPipelineUnavailable,
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
        allow_origins=["http://localhost:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "persistence_status": (
                "degraded"
                if active_service.degradation_codes or active_service.gap_codes
                else "ready"
            ),
            "provider": active_settings.ai_provider,
            "source_snapshot": active_service.source.snapshot_id,
            "persistence": active_service.persistence_mode,
            "persistence_warning": active_service.persistence_warning,
            "run_persistence": active_service.run_persistence,
            "outcome_persistence": active_service.outcome_persistence,
            "outcome_durable": active_service.outcome_durable,
            "degradation_codes": list(active_service.degradation_codes),
            "gap_codes": list(active_service.gap_codes),
            "foundation_capture_configured": active_service.foundation_capture_configured,
            "foundation_capture_configuration_code": active_service.foundation_configuration_code,
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
        return active_service.analyze(request)

    @app.get("/api/v1/assurance-runs", response_model=list[AssuranceRun])
    def list_assurance_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[AssuranceRun]:
        return active_service.list_recent(limit)

    @app.get("/api/v1/assurance-runs/{run_id}", response_model=AssuranceRun)
    def get_assurance_run(run_id: UUID) -> AssuranceRun:
        result = active_service.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Assurance run not found")
        return result

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
