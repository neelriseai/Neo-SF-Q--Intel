from __future__ import annotations

import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import OperationalError

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.providers import ModelProvider, create_model_provider
from neo_sf_q_intel.repository import (
    InMemoryRunRepository,
    PostgresRunRepository,
    RunRepository,
    SQLiteRunRepository,
)
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot, load_salesforce_source
from neo_sf_q_intel.semantic import SemanticEvidenceIndex, SemanticHit
from neo_sf_q_intel.workflow import AssuranceWorkflow


class AssuranceService:
    def __init__(
        self,
        source: SalesforceSourceSnapshot,
        repository: RunRepository | None = None,
        model_provider: ModelProvider | None = None,
        checkpointer: Any | None = None,
        checkpoint_context: Any | None = None,
        reasoning_policy: ReasoningPolicy | None = None,
        persistence_mode: str | None = None,
        persistence_warning: str | None = None,
    ) -> None:
        self.source = source
        self.repository = repository or InMemoryRunRepository()
        self.persistence_mode = persistence_mode or self._repository_mode(self.repository)
        self.persistence_warning = persistence_warning
        retriever = EvidenceRetriever(source)
        self.workflow = AssuranceWorkflow(
            ChangeIntelligenceService(retriever, reasoning_policy), checkpointer=checkpointer
        )
        self.semantic_index = (
            SemanticEvidenceIndex(source, model_provider) if model_provider else None
        )
        self._checkpoint_context = checkpoint_context

    def analyze(self, request: ChangeRequest) -> AssuranceRun:
        if request.project_id is None:
            request = request.model_copy(update={"project_id": self.source.project_id})
        elif request.project_id != self.source.project_id:
            raise ValueError(
                f"Request project {request.project_id!r} does not match loaded source "
                f"{self.source.project_id!r}"
            )
        result = self.workflow.run(AssuranceRun(request=request))
        self.repository.save(result)
        return result

    def get(self, run_id: UUID) -> AssuranceRun | None:
        return self.repository.get(run_id)

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        return self.repository.list_recent(limit)

    async def semantic_search(self, query: str, limit: int = 8) -> list[SemanticHit]:
        if self.semantic_index is None:
            raise RuntimeError("Semantic retrieval requires an enabled model provider")
        return await self.semantic_index.search(query, limit)

    def close(self) -> None:
        if self._checkpoint_context is not None:
            self._checkpoint_context.__exit__(None, None, None)
            self._checkpoint_context = None

    @staticmethod
    def _repository_mode(repository: RunRepository) -> str:
        if isinstance(repository, PostgresRunRepository):
            return "postgresql-runs-checkpoints"
        if isinstance(repository, SQLiteRunRepository):
            return "sqlite-fallback"
        return "memory-cache"


def create_service(settings: Settings, repository_root: Path) -> AssuranceService:
    source = load_salesforce_source(
        settings.resolved_salesforce_root(repository_root),
        expected_graph_sha256=settings.require_graph_sha256(),
        minimum_contract_version=settings.source_min_contract_version,
        required_capabilities=settings.required_capabilities,
    )
    repository: RunRepository | None = None
    checkpoint_context = None
    checkpointer = None
    persistence_warning = None
    if settings.database_url:
        database_url = settings.database_url.get_secret_value()
        try:
            postgres = PostgresRunRepository(database_url)
            postgres.setup()
            checkpoint_context = PostgresSaver.from_conn_string(database_url)
            checkpointer = checkpoint_context.__enter__()
            checkpointer.setup()
            repository = postgres
        except (OperationalError, OSError, TimeoutError, ConnectionError) as exc:
            if checkpoint_context is not None:
                with suppress(Exception):
                    checkpoint_context.__exit__(type(exc), exc, exc.__traceback__)
                checkpoint_context = None
            checkpointer = None
            persistence_warning = f"PostgreSQL unavailable ({type(exc).__name__}); using SQLite"
    if repository is None:
        try:
            sqlite = SQLiteRunRepository(settings.resolved_sqlite_path(repository_root))
            sqlite.setup()
            repository = sqlite
        except (OSError, sqlite3.Error) as exc:
            repository = InMemoryRunRepository()
            persistence_warning = (
                f"SQLite unavailable ({type(exc).__name__}); using process memory cache"
            )
    return AssuranceService(
        source,
        repository,
        model_provider=create_model_provider(settings),
        checkpointer=checkpointer,
        checkpoint_context=checkpoint_context,
        persistence_mode=AssuranceService._repository_mode(repository),
        persistence_warning=persistence_warning,
    )
