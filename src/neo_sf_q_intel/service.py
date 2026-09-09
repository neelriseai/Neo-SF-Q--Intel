from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import OperationalError

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest, DecisionCode, ReleaseDecision
from neo_sf_q_intel.governance import decide
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.outcome_repository import (
    InMemoryOutcomeRepository,
    JsonOutcomeRepository,
    OutcomeAppendRequest,
    OutcomePage,
    OutcomeReplayError,
    OutcomeRepository,
    PostgresOutcomeRepository,
    SQLiteOutcomeRepository,
    replay_outcome_request,
)
from neo_sf_q_intel.outcomes import (
    IncidentEventPayload,
    OutcomeKind,
    OutcomeMemoryError,
    OutcomeRecord,
    load_outcome_policy,
)
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.providers import ModelProvider, create_model_provider
from neo_sf_q_intel.repository import (
    InMemoryRunRepository,
    PostgresRunRepository,
    RunRepository,
    SQLiteRunRepository,
)
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.safety import require_no_sensitive_text
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
        outcome_repository: OutcomeRepository | None = None,
        outcome_persistence: str | None = None,
        degradation_codes: Iterable[str] = (),
        gap_codes: Iterable[str] = (),
    ) -> None:
        self.source = source
        self.repository = repository or InMemoryRunRepository()
        self.persistence_mode = persistence_mode or self._repository_mode(self.repository)
        self.persistence_warning = persistence_warning
        self.run_persistence = self.persistence_mode
        self.outcome_repository = (
            outcome_repository
            if outcome_repository is not None
            else InMemoryOutcomeRepository()
        )
        self.outcome_persistence = outcome_persistence or self._outcome_repository_mode(
            self.outcome_repository
        )
        self.outcome_durable = isinstance(
            self.outcome_repository,
            (PostgresOutcomeRepository, SQLiteOutcomeRepository, JsonOutcomeRepository),
        )
        self.degradation_codes = tuple(dict.fromkeys(degradation_codes))
        configured_gaps = list(gap_codes)
        if not self.outcome_durable:
            configured_gaps.append("OUTCOME_PROCESS_CACHE_NON_DURABLE")
        self.gap_codes = tuple(dict.fromkeys(configured_gaps))
        active_reasoning_policy = reasoning_policy or ReasoningPolicy.load()
        retriever = EvidenceRetriever(source, active_reasoning_policy)
        self._source_project_id = retriever.project_id
        self._source_snapshot = retriever.source_snapshot
        self._source_graph_sha256 = retriever.source_graph_sha256
        self._ontology_identity = dict(retriever.ontology_identity)
        self.workflow = AssuranceWorkflow(
            ChangeIntelligenceService(retriever, active_reasoning_policy),
            checkpointer=checkpointer,
        )
        self.semantic_index = (
            SemanticEvidenceIndex(source, model_provider) if model_provider else None
        )
        self._checkpoint_context = checkpoint_context

    def analyze(self, request: ChangeRequest) -> AssuranceRun:
        if request.project_id is None:
            request = request.model_copy(update={"project_id": self._source_project_id})
        elif request.project_id != self._source_project_id:
            raise ValueError(
                f"Request project {request.project_id!r} does not match loaded source "
                f"{self._source_project_id!r}"
            )
        policy = self.workflow.analysis.policy
        ontology_identity = self._ontology_identity
        result = self.workflow.run(
            AssuranceRun(
                request=request,
                reasoning_policy_version=policy.schema_version,
                reasoning_policy_sha256=policy.policy_sha256,
                reasoning_eval_set_id=policy.retrieval_eval_set_id,
                reasoning_eval_set_sha256=policy.retrieval_eval_set_sha256,
                source_snapshot=self._source_snapshot,
                source_graph_sha256=self._source_graph_sha256,
                ontology_id=ontology_identity["ontologyId"],
                ontology_version=ontology_identity["ontologyVersion"],
                ontology_sha256=ontology_identity["ontologySha256"],
                source_profile_id=ontology_identity["sourceProfileId"],
                source_profile_version=ontology_identity["sourceProfileVersion"],
                source_profile_sha256=ontology_identity["sourceProfileSha256"],
                normalized_graph_sha256=ontology_identity["normalizedGraphSha256"],
            )
        )
        self.repository.save(result)
        return result

    def get(self, run_id: UUID) -> AssuranceRun | None:
        run = self.repository.get(run_id)
        return self._effective_view(run) if run else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        return [self._effective_view(run) for run in self.repository.list_recent(limit)]

    def append_outcome(
        self,
        record: OutcomeRecord,
        idempotency_key: str,
        *,
        predecessor: OutcomeRecord | None = None,
        predecessor_chain: tuple[OutcomeRecord, ...] = (),
    ) -> OutcomeRecord:
        self._require_source_project(record.lineage.project_id)
        stored_run = self._raw_originating_run(record)
        self._require_persisted_outcome_history(predecessor, predecessor_chain)
        return self.outcome_repository.append(
            OutcomeAppendRequest(
                record=record,
                originating_run=stored_run,
                predecessor=predecessor,
                predecessor_chain=predecessor_chain,
            ),
            idempotency_key,
        )

    def _require_persisted_outcome_history(
        self,
        predecessor: OutcomeRecord | None,
        predecessor_chain: tuple[OutcomeRecord, ...],
    ) -> None:
        history = predecessor_chain + (
            (predecessor,) if predecessor is not None else ()
        )
        for historical in history:
            self._require_source_project(historical.lineage.project_id)
            persisted = self.outcome_repository.get(
                historical.lineage.project_id, historical.outcome_id
            )
            if persisted is None:
                raise OutcomeReplayError(
                    "Outcome history is not present in outcome persistence"
                )
            if persisted != historical:
                raise OutcomeReplayError(
                    "Supplied outcome history differs from outcome persistence"
                )

    def get_outcome(self, project_id: str, outcome_id: str) -> OutcomeRecord | None:
        self._require_source_project(project_id)
        record = self.outcome_repository.get(project_id, outcome_id)
        return self._replay_stored_outcome(record) if record is not None else None

    def query_outcomes(
        self,
        *,
        project_id: str,
        kinds: Iterable[OutcomeKind | str] | None = None,
        source_snapshot: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> OutcomePage:
        self._require_source_project(project_id)
        page = self.outcome_repository.query(
            project_id=project_id,
            kinds=kinds,
            source_snapshot=source_snapshot,
            cursor=cursor,
            limit=limit,
        )
        return OutcomePage(
            records=tuple(self._replay_stored_outcome(record) for record in page.records),
            next_cursor=page.next_cursor,
        )

    def _raw_originating_run(self, record: OutcomeRecord) -> AssuranceRun:
        try:
            originating_run_id = UUID(record.lineage.run_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise OutcomeReplayError("Outcome lineage run ID is invalid") from exc
        stored_run = self.repository.get(originating_run_id)
        if stored_run is None:
            raise OutcomeReplayError("Originating run is not present in run persistence")
        if str(stored_run.run_id) != record.lineage.run_id:
            raise OutcomeReplayError("Stored run ID differs from outcome lineage")
        return stored_run

    def _replay_stored_outcome(self, record: OutcomeRecord) -> OutcomeRecord:
        stored_run = self._raw_originating_run(record)
        predecessor: OutcomeRecord | None = None
        predecessor_chain: tuple[OutcomeRecord, ...] = ()
        if isinstance(record.payload, IncidentEventPayload):
            predecessor, predecessor_chain = self._stored_incident_history(record)
        return replay_outcome_request(
            OutcomeAppendRequest(
                record=record,
                originating_run=stored_run,
                predecessor=predecessor,
                predecessor_chain=predecessor_chain,
            )
        )

    def _stored_incident_history(
        self, record: OutcomeRecord
    ) -> tuple[OutcomeRecord | None, tuple[OutcomeRecord, ...]]:
        assert isinstance(record.payload, IncidentEventPayload)
        predecessor_id = record.payload.predecessor_outcome_id
        if predecessor_id is None:
            return None, ()
        try:
            maximum_depth = load_outcome_policy().limits.maximum_incident_chain_depth
        except (OSError, OutcomeMemoryError, ValueError) as exc:
            raise OutcomeReplayError("Outcome policy is unavailable for history replay") from exc
        project_id = record.lineage.project_id
        reverse_history: list[OutcomeRecord] = []
        seen = {record.outcome_id}
        while predecessor_id is not None:
            if predecessor_id in seen:
                raise OutcomeReplayError("Incident history contains a duplicate outcome ID")
            seen.add(predecessor_id)
            historical = self.outcome_repository.get(project_id, predecessor_id)
            if historical is None:
                raise OutcomeReplayError("Incident history references a missing outcome")
            reverse_history.append(historical)
            if len(reverse_history) + 1 > maximum_depth:
                raise OutcomeReplayError("Incident history exceeds its policy depth bound")
            predecessor_id = (
                historical.payload.predecessor_outcome_id
                if isinstance(historical.payload, IncidentEventPayload)
                else None
            )
        return reverse_history[0], tuple(reversed(reverse_history[1:]))

    def _require_source_project(self, project_id: str) -> None:
        require_no_sensitive_text((project_id, self._source_project_id))
        if project_id != self._source_project_id:
            raise ValueError("Outcome project does not match the loaded source project")

    @staticmethod
    def _effective_view(run: AssuranceRun) -> AssuranceRun:
        """Return a current-policy view without rewriting historical audit state."""
        view = run.model_copy(deep=True)
        try:
            policy = GovernancePolicy.load()
        except (OSError, ValueError):
            effective = ReleaseDecision(
                code=DecisionCode.INCOMPLETE,
                reasons=["GOVERNANCE_POLICY_INVALID"],
            )
        else:
            if (
                not policy.release_authority.enabled
                and view.decision is not None
                and view.decision.code is DecisionCode.INCOMPLETE
            ):
                return view
            effective = (
                ReleaseDecision(
                    code=DecisionCode.INCOMPLETE,
                    reasons=[policy.release_authority.reason_code],
                )
                if not policy.release_authority.enabled
                else decide(view, policy)
            )
        if view.decision != effective:
            view.recorded_decision = view.decision
            view.decision = effective
        return view

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

    @staticmethod
    def _outcome_repository_mode(repository: OutcomeRepository) -> str:
        if isinstance(repository, PostgresOutcomeRepository):
            return "postgresql"
        if isinstance(repository, SQLiteOutcomeRepository):
            return "sqlite"
        if isinstance(repository, JsonOutcomeRepository):
            return "json"
        return "process-cache"


_SQLITE_UNAVAILABLE_ERROR_NAMES = frozenset(
    {
        "SQLITE_BUSY",
        "SQLITE_CANTOPEN",
        "SQLITE_FULL",
        "SQLITE_IOERR",
        "SQLITE_LOCKED",
        "SQLITE_PERM",
        "SQLITE_READONLY",
    }
)
_SQLITE_UNAVAILABLE_MESSAGE_MARKERS = (
    "attempt to write a readonly database",
    "database is locked",
    "database is read-only",
    "disk i/o error",
    "read-only",
    "readonly",
    "unable to open database file",
)


def _is_sqlite_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, OSError):
        return True
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    error_name = getattr(exc, "sqlite_errorname", None)
    if error_name is not None:
        base_error_name = "_".join(error_name.split("_", 2)[:2])
        return base_error_name in _SQLITE_UNAVAILABLE_ERROR_NAMES
    message = str(exc).casefold()
    return any(marker in message for marker in _SQLITE_UNAVAILABLE_MESSAGE_MARKERS)


def _select_outcome_repository(
    settings: Settings,
    repository_root: Path,
) -> tuple[OutcomeRepository, list[str], list[str]]:
    degradation_codes: list[str] = []
    gap_codes: list[str] = []
    if settings.database_url:
        try:
            postgres = PostgresOutcomeRepository(settings.database_url.get_secret_value())
            postgres.setup()
            return postgres, degradation_codes, gap_codes
        except (OperationalError, OSError, TimeoutError, ConnectionError):
            degradation_codes.append("OUTCOME_POSTGRESQL_UNAVAILABLE")
    else:
        gap_codes.append("OUTCOME_POSTGRESQL_NOT_CONFIGURED")

    try:
        sqlite = SQLiteOutcomeRepository(
            settings.resolved_outcome_sqlite_path(repository_root)
        )
        return sqlite, degradation_codes, gap_codes
    except (OSError, sqlite3.OperationalError) as exc:
        if not _is_sqlite_unavailable(exc):
            raise
        degradation_codes.append("OUTCOME_SQLITE_UNAVAILABLE")

    try:
        json_repository = JsonOutcomeRepository(
            settings.resolved_outcome_json_path(repository_root)
        )
        return json_repository, degradation_codes, gap_codes
    except OSError:
        degradation_codes.append("OUTCOME_JSON_UNAVAILABLE")

    gap_codes.append("OUTCOME_PROCESS_CACHE_NON_DURABLE")
    return InMemoryOutcomeRepository(), degradation_codes, gap_codes


def create_service(settings: Settings, repository_root: Path) -> AssuranceService:
    source = load_salesforce_source(
        settings.resolved_salesforce_root(repository_root),
        expected_graph_sha256=settings.require_graph_sha256(),
        minimum_contract_version=settings.source_min_contract_version,
        required_capabilities=settings.required_capabilities,
        ontology_path=settings.resolved_canonical_ontology_path(repository_root),
        source_profile_path=settings.resolved_source_graph_profile_path(repository_root),
        expected_source_profile_sha256=settings.require_source_profile_sha256(),
    )
    repository: RunRepository | None = None
    checkpoint_context = None
    checkpointer = None
    persistence_warning = None
    degradation_codes: list[str] = []
    gap_codes: list[str] = []
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
            degradation_codes.append("RUN_POSTGRESQL_UNAVAILABLE")
    else:
        gap_codes.append("RUN_POSTGRESQL_NOT_CONFIGURED")
    if repository is None:
        try:
            sqlite = SQLiteRunRepository(settings.resolved_sqlite_path(repository_root))
            sqlite.setup()
            repository = sqlite
        except (OSError, sqlite3.OperationalError) as exc:
            if not _is_sqlite_unavailable(exc):
                raise
            repository = InMemoryRunRepository()
            persistence_warning = (
                f"SQLite unavailable ({type(exc).__name__}); using process memory cache"
            )
            degradation_codes.append("RUN_SQLITE_UNAVAILABLE")
            gap_codes.append("RUN_PROCESS_CACHE_NON_DURABLE")
    try:
        outcome_repository, outcome_degradations, outcome_gaps = (
            _select_outcome_repository(settings, repository_root)
        )
        degradation_codes.extend(outcome_degradations)
        gap_codes.extend(outcome_gaps)
        return AssuranceService(
            source,
            repository,
            model_provider=create_model_provider(settings),
            checkpointer=checkpointer,
            checkpoint_context=checkpoint_context,
            persistence_mode=AssuranceService._repository_mode(repository),
            persistence_warning=persistence_warning,
            outcome_repository=outcome_repository,
            outcome_persistence=AssuranceService._outcome_repository_mode(outcome_repository),
            degradation_codes=degradation_codes,
            gap_codes=gap_codes,
        )
    except Exception as exc:
        if checkpoint_context is not None:
            with suppress(Exception):
                checkpoint_context.__exit__(type(exc), exc, exc.__traceback__)
        raise
