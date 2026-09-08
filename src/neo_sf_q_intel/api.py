from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest
from neo_sf_q_intel.service import AssuranceService, create_service


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
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "provider": active_settings.ai_provider,
            "source_snapshot": active_service.source.snapshot_id,
            "persistence": ("postgresql" if active_settings.database_url else "in-memory-demo"),
        }

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
