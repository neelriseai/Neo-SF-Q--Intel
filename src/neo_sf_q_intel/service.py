from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.providers import ModelProvider, create_model_provider
from neo_sf_q_intel.repository import (
    InMemoryRunRepository,
    PostgresRunRepository,
    RunRepository,
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
    ) -> None:
        self.source = source
        self.repository = repository or InMemoryRunRepository()
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


def create_service(settings: Settings, repository_root: Path) -> AssuranceService:
    source = load_salesforce_source(
        settings.resolved_salesforce_root(repository_root),
        expected_graph_sha256=settings.require_graph_sha256(),
        minimum_contract_version=settings.source_min_contract_version,
        required_capabilities=settings.required_capabilities,
    )
    repository: RunRepository
    if settings.database_url:
        database_url = settings.database_url.get_secret_value()
        postgres = PostgresRunRepository(database_url)
        postgres.setup()
        repository = postgres
        checkpoint_context = PostgresSaver.from_conn_string(database_url)
        checkpointer = checkpoint_context.__enter__()
        checkpointer.setup()
    else:
        repository = InMemoryRunRepository()
        checkpoint_context = None
        checkpointer = None
    return AssuranceService(
        source,
        repository,
        model_provider=create_model_provider(settings),
        checkpointer=checkpointer,
        checkpoint_context=checkpoint_context,
    )
