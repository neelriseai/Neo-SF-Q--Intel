from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import OperationalError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.candidate_assurance import (
    CandidateAssuranceBundle,
    CandidateAssuranceView,
    CandidateSideAssurance,
    CandidateSpecialistCapture,
    build_candidate_assurance_bundle,
    build_candidate_assurance_view,
    candidate_side_requests,
    source_from_verified_graph_side,
)
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.context_pack import (
    DEFAULT_CONTEXT_COMPILER_POLICY_SHA256,
    UnresolvedSourceFragment,
    compile_graph_context_pack,
    load_context_compiler_policy,
)
from neo_sf_q_intel.domain import (
    AnalysisGap,
    AssuranceRun,
    ChangeRequest,
    DecisionCode,
    ReleaseDecision,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.foundation_pipeline import (
    CandidateFoundationEvidence,
    CandidateFoundationPipeline,
)
from neo_sf_q_intel.governance import decide
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.graph_production import TreeSide
from neo_sf_q_intel.live_baseline import (
    LiveBaselineResult,
    create_live_baseline_service,
)
from neo_sf_q_intel.live_campaign_status import (
    LiveCampaignStatus,
    LiveCampaignStatusError,
    LiveCampaignStatusReader,
    create_live_campaign_status_reader,
)
from neo_sf_q_intel.ontology import SourceEvidenceState
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
from neo_sf_q_intel.postgres_schema import scoped_connection_string
from neo_sf_q_intel.propagation import (
    DEFAULT_PROPAGATION_POLICY_SHA256,
    load_propagation_policy,
    traverse_propagation,
)
from neo_sf_q_intel.providers import (
    ModelProvider,
    create_model_provider,
    create_specialist_provider,
)
from neo_sf_q_intel.reasoning_workflow import (
    SpecialistReplayBundle,
    SpecialistStageInput,
    canonical_change_request_sha256,
    integrate_reasoning_workflow,
    load_reasoning_workflow_policy,
)
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
from neo_sf_q_intel.specialist import (
    GraphContextReplayInputs,
    ProviderCallStatus,
    ProviderPort,
    SpecialistIdentity,
    SpecialistRequest,
    execute_specialist_capture,
    load_specialist_evaluation_contract,
    load_specialist_policy,
    replay_verify_analysis_context,
)
from neo_sf_q_intel.workflow import AssuranceWorkflow


class FoundationPipelineUnavailable(RuntimeError):
    """Stable, secret-safe application-boundary refusal."""

    code = "FOUNDATION_PIPELINE_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__(self.code)


FOUNDATION_PIPELINE_NOT_CONFIGURED = "FOUNDATION_PIPELINE_NOT_CONFIGURED"
FOUNDATION_PIPELINE_CONFIGURATION_INVALID = "FOUNDATION_PIPELINE_CONFIGURATION_INVALID"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CONTEXT_COMPILER_POLICY_PATH = _REPOSITORY_ROOT / "config" / "context-compiler-policy.json"
_PROPAGATION_POLICY_PATH = _REPOSITORY_ROOT / "config" / "propagation-policy.json"


class _LiveBaselineRunner(Protocol):
    def run(self) -> LiveBaselineResult: ...


class LiveBaselineReadView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_count: int = Field(ge=1, le=256)
    receipt_count: int = Field(ge=1, le=256)
    item_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    invocation_count: int = Field(ge=1, le=1000)


class LiveBaselineView(BaseModel):
    """Bounded application projection; raw routes, values, IDs and receipts never cross it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str | None = Field(default=None, pattern=r"^baseline:[a-f0-9]{32}$")
    state: Literal["BLOCKED", "IN_PROGRESS", "COMPLETED"]
    gap_codes: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")], ...] = Field(
        max_length=32
    )
    ledger_mode: Literal["POSTGRESQL", "SQLITE", "UNAVAILABLE"]
    assertion_store_mode: Literal["POSTGRESQL", "SQLITE", "UNAVAILABLE"]
    read: LiveBaselineReadView | None = None
    release_eligible: Literal[False] = False

    @model_validator(mode="after")
    def validate_view(self) -> LiveBaselineView:
        if (self.state == "COMPLETED") != (self.read is not None):
            raise ValueError("Only completed live baselines expose a read summary")
        return self


class LiveSalesforceDiagnosticView(BaseModel):
    """Secret-safe live connectivity projection for the operator demo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["PASSED", "BLOCKED"]
    target_alias_configured: bool
    connected: bool = False
    read: LiveBaselineReadView | None = None
    diagnostic_only: Literal[True] = True
    release_eligible: Literal[False] = False
    error_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")] | None = None

    @model_validator(mode="after")
    def validate_diagnostic(self) -> LiveSalesforceDiagnosticView:
        if (self.status == "PASSED") != self.connected:
            raise ValueError("Live diagnostic status and connectivity differ")
        if self.status == "PASSED" and self.error_code is not None:
            raise ValueError("Passed live diagnostic cannot carry an error code")
        if self.status == "BLOCKED" and self.error_code is None:
            raise ValueError("Blocked live diagnostic requires an error code")
        if self.status == "BLOCKED" and self.read is not None:
            raise ValueError("Blocked live diagnostic cannot carry read evidence")
        return self


class LiveOperatorAdvisoryView(BaseModel):
    """One bounded demo slice: live Salesforce connectivity plus real LLM candidate advisory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    capability_id: Literal["demo.live-operator-advisory"] = "demo.live-operator-advisory"
    authority_scope: Literal["DIAGNOSTIC_ADVISORY_ONLY"] = "DIAGNOSTIC_ADVISORY_ONLY"
    live_salesforce: LiveSalesforceDiagnosticView
    candidate: CandidateAssuranceView | None = None
    candidate_available: bool
    llm_advisory_available: bool
    candidate_analysis_count: int = Field(ge=0, le=2)
    specialist_capture_count: int = Field(ge=0, le=16)
    release_eligible: Literal[False] = False
    gap_codes: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")], ...] = Field(
        max_length=32
    )
    view_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_view(self) -> LiveOperatorAdvisoryView:
        if (self.candidate is not None) != self.candidate_available:
            raise ValueError("Candidate availability differs from candidate projection")
        if self.candidate is None and self.specialist_capture_count:
            raise ValueError("Specialist captures require a candidate projection")
        if self.llm_advisory_available and self.specialist_capture_count == 0:
            raise ValueError("LLM advisory availability requires at least one specialist capture")
        if self.llm_advisory_available and self.candidate is None:
            raise ValueError("LLM advisory requires a candidate projection")
        body = self.model_dump(mode="json")
        declared = body.pop("view_sha256")
        if declared != stable_sha256(body):
            raise ValueError("Live operator advisory view digest is invalid")
        if len(self.model_dump_json().encode("utf-8")) > 65_536:
            raise ValueError("Live operator advisory view exceeds its response bound")
        return self


@dataclass(frozen=True, slots=True)
class _VerifiedCandidateAuthority:
    manifest_sha256: str
    operation_seed_sha256: str


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _live_salesforce_context_fragment(
    live: LiveSalesforceDiagnosticView | None,
    source: SalesforceSourceSnapshot,
    *,
    node_id: str,
) -> tuple[UnresolvedSourceFragment, ...]:
    if live is None:
        return ()
    node = next(
        (item for item in source.normalized_graph.nodes if item.node_id == node_id),
        None,
    )
    if node is None or not node.source or not node.extractor_id:
        return ()
    body = {
        "schema_version": "1.0.0",
        "evidence_type": "LIVE_SALESFORCE_READ_DIAGNOSTIC",
        "authority_scope": "READ_ONLY_DIAGNOSTIC_CONTEXT",
        "status": live.status,
        "connected": live.connected,
        "target_alias_configured": live.target_alias_configured,
        "diagnostic_only": live.diagnostic_only,
        "release_eligible": live.release_eligible,
        "error_code": live.error_code,
        "read": live.read.model_dump(mode="json") if live.read is not None else None,
        "claim_boundary": (
            "This runtime evidence is context for LLM advisory only. It is not deployment, "
            "browser acceptance, restore, reconciliation, or release evidence."
        ),
    }
    content = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    body_sha256 = stable_sha256(body)
    return (
        UnresolvedSourceFragment(
            fragment_id=f"live-salesforce-read:{body_sha256[:32]}",
            evidence_id=f"live-salesforce-read:{body_sha256[:32]}",
            node_id=node_id,
            source_locator=node.source,
            content=content,
            content_sha256=_content_sha256(content),
            source_snapshot=source.normalized_graph.source_snapshot,
            source_graph_sha256=source.normalized_graph.source_graph_sha256,
            extractor_id=node.extractor_id,
            evidence_state=SourceEvidenceState.UNVERIFIED,
            mandatory=False,
            may_authorize=False,
        ),
    )


class AssuranceService:
    def __init__(
        self,
        source: SalesforceSourceSnapshot,
        repository: RunRepository | None = None,
        model_provider: ModelProvider | None = None,
        specialist_provider: ProviderPort | None = None,
        checkpointer: Any | None = None,
        checkpoint_context: Any | None = None,
        reasoning_policy: ReasoningPolicy | None = None,
        persistence_mode: str | None = None,
        persistence_warning: str | None = None,
        outcome_repository: OutcomeRepository | None = None,
        outcome_persistence: str | None = None,
        degradation_codes: Iterable[str] = (),
        gap_codes: Iterable[str] = (),
        foundation_pipeline: CandidateFoundationPipeline | None = None,
        foundation_configuration_code: str | None = None,
        live_campaign_status_reader: LiveCampaignStatusReader | None = None,
        live_diagnostic_reader: Callable[[], LiveSalesforceDiagnosticView] | None = None,
        live_baseline_service_factory: (
            Callable[[Callable[[], CandidateAssuranceBundle]], _LiveBaselineRunner] | None
        ) = None,
    ) -> None:
        self.source = source
        self.repository = repository or InMemoryRunRepository()
        self.persistence_mode = persistence_mode or self._repository_mode(self.repository)
        self.persistence_warning = persistence_warning
        self.run_persistence = self.persistence_mode
        self.outcome_repository = (
            outcome_repository if outcome_repository is not None else InMemoryOutcomeRepository()
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
        self._foundation_pipeline = foundation_pipeline
        self._live_campaign_status_reader = live_campaign_status_reader
        self._live_diagnostic_reader = live_diagnostic_reader
        self.foundation_configuration_code = (
            None
            if foundation_pipeline is not None
            else foundation_configuration_code or FOUNDATION_PIPELINE_NOT_CONFIGURED
        )
        active_reasoning_policy = reasoning_policy or ReasoningPolicy.load()
        self._reasoning_policy = active_reasoning_policy
        self._checkpointer = checkpointer
        self._model_provider = model_provider
        self._specialist_provider = specialist_provider
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
        self._live_baseline_service = (
            live_baseline_service_factory(self.analyze_current_candidate)
            if live_baseline_service_factory is not None
            else None
        )

    def analyze(self, request: ChangeRequest) -> AssuranceRun:
        if request.change_intent.value == "VERIFIED_CHANGE":
            raise ValueError("VERIFIED_CHANGE_REQUIRES_HOST_CAPTURE")
        result = self._analyze_source(request, self.source)
        self.repository.save(result)
        return result

    def _analyze_source(
        self,
        request: ChangeRequest,
        source: SalesforceSourceSnapshot,
        upstream_gaps: tuple[AnalysisGap, ...] = (),
        *,
        verified_authority: _VerifiedCandidateAuthority | None = None,
    ) -> AssuranceRun:
        if request.change_intent.value == "VERIFIED_CHANGE":
            if verified_authority is None or (
                request.verified_change_manifest_sha256,
                request.verified_operation_seed_sha256,
            ) != (
                verified_authority.manifest_sha256,
                verified_authority.operation_seed_sha256,
            ):
                raise ValueError("VERIFIED_CHANGE_REQUIRES_HOST_CAPTURE")
        elif verified_authority is not None:
            raise ValueError("HOST_CAPTURE_AUTHORITY_REQUIRES_VERIFIED_CHANGE")
        retriever = EvidenceRetriever(source, self._reasoning_policy)
        if request.project_id is None:
            request = request.model_copy(update={"project_id": retriever.project_id})
        elif request.project_id != retriever.project_id:
            raise ValueError(
                f"Request project {request.project_id!r} does not match loaded source "
                f"{retriever.project_id!r}"
            )
        analysis = ChangeIntelligenceService(
            retriever,
            self._reasoning_policy,
            upstream_gaps=upstream_gaps,
        )
        workflow = (
            self.workflow
            if source is self.source and not upstream_gaps
            else AssuranceWorkflow(analysis, checkpointer=self._checkpointer)
        )
        policy = analysis.policy
        ontology_identity = retriever.ontology_identity
        return workflow.run(
            AssuranceRun(
                request=request,
                reasoning_policy_version=policy.schema_version,
                reasoning_policy_sha256=policy.policy_sha256,
                reasoning_eval_set_id=policy.retrieval_eval_set_id,
                reasoning_eval_set_sha256=policy.retrieval_eval_set_sha256,
                source_snapshot=retriever.source_snapshot,
                source_graph_sha256=retriever.source_graph_sha256,
                ontology_id=ontology_identity["ontologyId"],
                ontology_version=ontology_identity["ontologyVersion"],
                ontology_sha256=ontology_identity["ontologySha256"],
                source_profile_id=ontology_identity["sourceProfileId"],
                source_profile_version=ontology_identity["sourceProfileVersion"],
                source_profile_sha256=ontology_identity["sourceProfileSha256"],
                normalized_graph_sha256=ontology_identity["normalizedGraphSha256"],
            )
        )

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
        history = predecessor_chain + ((predecessor,) if predecessor is not None else ())
        for historical in history:
            self._require_source_project(historical.lineage.project_id)
            persisted = self.outcome_repository.get(
                historical.lineage.project_id, historical.outcome_id
            )
            if persisted is None:
                raise OutcomeReplayError("Outcome history is not present in outcome persistence")
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

    @property
    def foundation_capture_configured(self) -> bool:
        """Report construction state only; runtime currency is probed by each POST."""

        return self._foundation_pipeline is not None

    def capture_candidate_foundation(self) -> CandidateFoundationEvidence:
        """Capture fresh host-owned evidence without accepting or persisting caller scope."""

        if self._foundation_pipeline is None:
            raise FoundationPipelineUnavailable
        try:
            captured = self._foundation_pipeline.capture_current()
            if not captured.evidence.foundation_execution_complete:
                return captured.evidence
            return self._foundation_pipeline.verify_current(captured).evidence
        except Exception:
            raise FoundationPipelineUnavailable from None

    def _candidate_specialist_captures(
        self,
        run: AssuranceRun,
        source: SalesforceSourceSnapshot,
        *,
        live_salesforce: LiveSalesforceDiagnosticView | None = None,
    ) -> tuple[CandidateSpecialistCapture, ...]:
        """Run configured advisory specialists for one verified candidate side."""

        if self._specialist_provider is None:
            return ()
        propagation_policy = load_propagation_policy(
            _PROPAGATION_POLICY_PATH,
            self._foundation_pipeline.ontology if self._foundation_pipeline else source.ontology,
            expected_sha256=DEFAULT_PROPAGATION_POLICY_SHA256,
        )
        compiler_policy = load_context_compiler_policy(
            _CONTEXT_COMPILER_POLICY_PATH,
            expected_sha256=DEFAULT_CONTEXT_COMPILER_POLICY_SHA256,
        )
        request_sha256 = canonical_change_request_sha256(run.request)
        propagation = traverse_propagation(
            source.normalized_graph,
            tuple(run.request.verified_seed_ids),
            propagation_policy,
        )
        pack = compile_graph_context_pack(
            source.normalized_graph,
            propagation,
            propagation_policy,
            self._reasoning_policy,
            compiler_policy,
            request_sha256=request_sha256,
            reasoning_policy_locator="config/reasoning-policy.json",
            expected_reasoning_policy_sha256=self._reasoning_policy.policy_sha256,
            expected_retrieval_eval_set_sha256=self._reasoning_policy.retrieval_eval_set_sha256,
            expected_propagation_policy_sha256=propagation_policy.sha256,
            expected_compiler_policy_sha256=compiler_policy.sha256,
            unresolved_fragments=_live_salesforce_context_fragment(
                live_salesforce,
                source,
                node_id=run.request.verified_seed_ids[0],
            ),
        )
        context = replay_verify_analysis_context(
            pack,
            GraphContextReplayInputs(
                graph=source.normalized_graph,
                propagation=propagation,
                propagation_policy=propagation_policy,
                reasoning_policy=self._reasoning_policy,
                compiler_policy=compiler_policy,
                ontology=source.ontology,
                request_sha256=request_sha256,
                reasoning_policy_locator="config/reasoning-policy.json",
                expected_ontology_sha256=source.ontology.sha256,
                expected_reasoning_policy_sha256=self._reasoning_policy.policy_sha256,
                expected_retrieval_eval_set_sha256=(
                    self._reasoning_policy.retrieval_eval_set_sha256
                ),
                expected_propagation_policy_sha256=propagation_policy.sha256,
                expected_compiler_policy_sha256=compiler_policy.sha256,
                unresolved_fragments=_live_salesforce_context_fragment(
                    live_salesforce,
                    source,
                    node_id=run.request.verified_seed_ids[0],
                ),
            ),
        )
        workflow_policy = load_reasoning_workflow_policy()
        specialist_policy = load_specialist_policy()
        evaluation = load_specialist_evaluation_contract()
        captures: list[CandidateSpecialistCapture] = []
        stages: list[SpecialistStageInput] = []
        for spec in workflow_policy.specialist_profiles:
            if run.request.change_intent.value not in spec.eligible_change_intents:
                continue
            request_body = {
                "request_id": f"{run.run_id}:{spec.stage_id}",
                "context_request_sha256": request_sha256,
                "identity": {
                    "specialist_id": spec.specialist_id,
                    "specialist_version": spec.specialist_version,
                    "capability_id": spec.capability_id,
                },
                "task": (
                    "Assess replay-verified Salesforce candidate-change impacts from the "
                    "provided graph context. Produce only source-bound advisory proposals. "
                    "For RELATION proposals, copy evidence_id, subject_entity_id, "
                    "target_entity_id and canonical_relation exactly from one "
                    "allowlists.relation_proposal_edges tuple; never reverse an edge."
                ),
                "question": (
                    "Which impacted components, tests, automation targets, or governance "
                    "risks need deterministic review before this candidate can proceed?"
                ),
            }
            specialist_request = SpecialistRequest(
                request_id=request_body["request_id"],
                context_request_sha256=request_body["context_request_sha256"],
                request_sha256=stable_sha256(request_body),
                identity=SpecialistIdentity(**request_body["identity"]),
                task=request_body["task"],
                question=request_body["question"],
            )
            capture = execute_specialist_capture(
                context,
                specialist_request,
                specialist_policy,
                evaluation,
                self._specialist_provider,
                expected_provider_profile_sha256=(
                    self._specialist_provider.profile.profile_sha256
                ),
            )
            replay_bundle = SpecialistReplayBundle(
                target_run_id=run.run_id,
                target_trace_id=run.trace_id,
                artifact=capture.artifact,
                context=context,
                request=specialist_request,
                profile=capture.profile,
                policy=specialist_policy,
                evaluation=evaluation,
                prompt=capture.prompt,
                outcome=capture.outcome,
                expected_provider_profile_sha256=capture.expected_provider_profile_sha256,
                expected_provider_capture_sha256=capture.expected_provider_capture_sha256,
            )
            stages.append(SpecialistStageInput(spec=spec, replay_bundle=replay_bundle))
            result = integrate_reasoning_workflow(
                run,
                tuple(stages),
                workflow_policy,
                expected_workflow_policy_sha256=workflow_policy.policy_sha256,
                evaluated_at=_utc_timestamp(),
                live_evaluated_at=_utc_timestamp,
            )
            capture_body = {
                "workflow_result": result.model_dump(mode="json"),
                "artifact": capture.artifact.model_dump(mode="json"),
                "prompt": capture.prompt.model_dump(mode="json"),
                "provider_profile": capture.profile.model_dump(mode="json"),
                "provider_outcome": {
                    **capture.outcome.model_dump(mode="json"),
                    "raw_response": capture.outcome.raw_response,
                },
                "provider_raw_response": capture.outcome.raw_response,
                "expected_provider_profile_sha256": capture.expected_provider_profile_sha256,
                "expected_provider_capture_sha256": capture.expected_provider_capture_sha256,
            }
            captures.append(
                CandidateSpecialistCapture.model_validate(
                    {**capture_body, "capture_sha256": stable_sha256(capture_body)}
                )
            )
        return tuple(captures)

    def analyze_current_candidate(
        self,
        *,
        live_salesforce: LiveSalesforceDiagnosticView | None = None,
    ) -> CandidateAssuranceBundle:
        """Replay and analyze host-owned Git state; accepts no caller-selected scope."""

        if self._foundation_pipeline is None:
            raise FoundationPipelineUnavailable
        try:
            captured = self._foundation_pipeline.capture_current()
            if not captured.evidence.foundation_execution_complete:
                raise FoundationPipelineUnavailable
            verified = self._foundation_pipeline.verify_current(captured)
            graph = verified.produced_graph
            seeds = verified.operation_seeds
            if graph is None or seeds is None:
                raise FoundationPipelineUnavailable
            upstream_gaps = tuple(
                AnalysisGap(
                    code=code,
                    message=(
                        "The replayed candidate foundation records this release-blocking gap."
                    ),
                    blocking=True,
                )
                for code in verified.evidence.blocking_gap_codes
            ) + (
                AnalysisGap(
                    code="CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE",
                    message=(
                        "Workflow checkpoints are outside the atomic component-run and "
                        "candidate-bundle persistence transaction."
                    ),
                    blocking=True,
                ),
            )
            analyses: list[CandidateSideAssurance] = []
            for (
                side,
                graph_side,
                operations,
                request,
                operation_bindings,
                indirect_ids,
                indirect_receipts,
            ) in candidate_side_requests(verified):
                source = source_from_verified_graph_side(
                    repository_root=self._foundation_pipeline.repository_root,
                    graph=graph,
                    side=graph_side,
                    ontology=self._foundation_pipeline.ontology,
                    source_profile=self._foundation_pipeline.source_profile,
                )
                run = self._analyze_source(
                    request,
                    source,
                    upstream_gaps,
                    verified_authority=_VerifiedCandidateAuthority(
                        manifest_sha256=verified.verified_change.manifest_sha256,
                        operation_seed_sha256=seeds.artifact_sha256,
                    ),
                )
                specialist_captures = self._candidate_specialist_captures(
                    run,
                    source,
                    live_salesforce=live_salesforce,
                )
                analyses.append(
                    CandidateSideAssurance(
                        side=side,
                        operation_scope=operations,
                        changed_paths=tuple(request.changed_paths),
                        verified_seed_ids=tuple(request.verified_seed_ids),
                        operation_bindings=operation_bindings,
                        indirect_seed_ids=indirect_ids,
                        indirect_seed_sha256s=indirect_receipts,
                        graph_side_receipt_sha256=graph_side.side_receipt_sha256,
                        operation_seed_side_receipt_sha256=(
                            seeds.base_side_receipt_sha256
                            if side is TreeSide.BASE
                            else seeds.candidate_side_receipt_sha256
                        ),
                        producer_raw_graph_sha256=graph_side.raw_graph_sha256,
                        analysis_normalized_graph_sha256=(source.normalized_graph.graph_sha256),
                        run=run,
                        specialist_captures=specialist_captures,
                    )
                )
            bundle = build_candidate_assurance_bundle(verified, tuple(analyses))
            self.repository.save_candidate_bundle(bundle)
            return bundle
        except FoundationPipelineUnavailable:
            raise
        except Exception:
            raise FoundationPipelineUnavailable from None

    def analyze_current_candidate_view(self) -> CandidateAssuranceView:
        """Persist the full candidate bundle and return only its bounded client view."""

        return build_candidate_assurance_view(self.analyze_current_candidate())

    def run_live_operator_advisory_demo(self) -> LiveOperatorAdvisoryView:
        """Run one diagnostic demo slice without accepting caller scope or release authority."""

        live = self._run_live_diagnostic()
        candidate: CandidateAssuranceView | None = None
        gaps: list[str] = []
        successful_specialist_count = 0
        try:
            bundle = self.analyze_current_candidate(live_salesforce=live)
            successful_specialist_count = _successful_specialist_capture_count(bundle)
            candidate = build_candidate_assurance_view(bundle)
        except Exception:
            gaps.append("CANDIDATE_ADVISORY_UNAVAILABLE")
        if live.status == "BLOCKED" and live.error_code is not None:
            gaps.append(live.error_code)
        if candidate is not None and successful_specialist_count == 0:
            gaps.append("LLM_ADVISORY_UNAVAILABLE")
        candidate_count = len(candidate.analyses) if candidate is not None else 0
        specialist_count = (
            sum(item.specialist_capture_count for item in candidate.analyses)
            if candidate is not None
            else 0
        )
        body = {
            "schema_version": "1.0.0",
            "capability_id": "demo.live-operator-advisory",
            "authority_scope": "DIAGNOSTIC_ADVISORY_ONLY",
            "live_salesforce": live.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json") if candidate is not None else None,
            "candidate_available": candidate is not None,
            "llm_advisory_available": successful_specialist_count > 0,
            "candidate_analysis_count": candidate_count,
            "specialist_capture_count": specialist_count,
            "release_eligible": False,
            "gap_codes": sorted(set(gaps)),
        }
        return LiveOperatorAdvisoryView.model_validate(
            {**body, "view_sha256": stable_sha256(body)}
        )

    def _run_live_diagnostic(self) -> LiveSalesforceDiagnosticView:
        if self._live_diagnostic_reader is None:
            baseline = self.run_live_baseline()
            if baseline.state == "COMPLETED" and baseline.read is not None:
                return LiveSalesforceDiagnosticView(
                    status="PASSED",
                    target_alias_configured=True,
                    connected=True,
                    read=baseline.read,
                )
            code = (
                baseline.gap_codes[0]
                if baseline.gap_codes
                else "LIVE_DIAGNOSTIC_NOT_CONFIGURED"
            )
            return LiveSalesforceDiagnosticView(
                status="BLOCKED",
                target_alias_configured=(baseline.ledger_mode != "UNAVAILABLE"),
                error_code=code,
            )
        try:
            return LiveSalesforceDiagnosticView.model_validate(self._live_diagnostic_reader())
        except Exception:
            return LiveSalesforceDiagnosticView(
                status="BLOCKED",
                target_alias_configured=True,
                error_code="LIVE_DIAGNOSTIC_UNAVAILABLE",
            )

    @property
    def live_receipt_ledger_mode(self) -> str:
        if self._live_campaign_status_reader is None:
            return "UNAVAILABLE"
        return self._live_campaign_status_reader.ledger_mode

    @property
    def live_receipt_ledger_degradation_code(self) -> str | None:
        if self._live_campaign_status_reader is None:
            return "LIVE_RECEIPT_LEDGER_NOT_CONFIGURED"
        return self._live_campaign_status_reader.ledger_degradation_code

    def get_live_campaign_status(self, campaign_id: str) -> LiveCampaignStatus:
        """Replay one exact campaign without accepting evidence or authority from callers."""

        if self._live_campaign_status_reader is None:
            raise LiveCampaignStatusError
        return self._live_campaign_status_reader.get(campaign_id)

    def run_live_baseline(self) -> LiveBaselineView:
        """Run the fixed host baseline without accepting caller execution scope."""

        if self._live_baseline_service is None:
            return LiveBaselineView(
                state="BLOCKED",
                gap_codes=("LIVE_BASELINE_SERVICE_UNAVAILABLE",),
                ledger_mode="UNAVAILABLE",
                assertion_store_mode="UNAVAILABLE",
            )
        try:
            observed = self._live_baseline_service.run()
            if not isinstance(observed, LiveBaselineResult):
                raise TypeError
            result = LiveBaselineResult.model_validate(observed.model_dump(mode="python"))
            read = result.read_result
            summary = None
            if read is not None:
                observations = read.observations
                summary = LiveBaselineReadView(
                    plan_sha256=read.plan_sha256,
                    result_sha256=read.result_sha256,
                    artifact_set_sha256=stable_sha256(
                        sorted(item.artifact_sha256 for item in observations)
                    ),
                    observation_count=len(observations),
                    receipt_count=len(read.stored_receipt_ids),
                    item_count=sum(item.item_count for item in observations),
                    byte_count=sum(item.byte_count for item in observations),
                    invocation_count=sum(item.invocation_count for item in observations),
                )
            return LiveBaselineView(
                campaign_id=result.campaign_id,
                state=result.state,
                gap_codes=tuple(dict.fromkeys((*result.gap_codes, *result.degradation_codes))),
                ledger_mode=result.ledger_mode,
                assertion_store_mode=result.assertion_store_mode,
                read=summary,
            )
        except Exception:
            return LiveBaselineView(
                state="BLOCKED",
                gap_codes=("LIVE_BASELINE_SERVICE_UNAVAILABLE",),
                ledger_mode="UNAVAILABLE",
                assertion_store_mode="UNAVAILABLE",
            )

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
            postgres = PostgresOutcomeRepository(
                settings.database_url.get_secret_value(), schema=settings.postgres_schema
            )
            postgres.setup()
            return postgres, degradation_codes, gap_codes
        except (OperationalError, OSError, TimeoutError, ConnectionError):
            degradation_codes.append("OUTCOME_POSTGRESQL_UNAVAILABLE")
    else:
        gap_codes.append("OUTCOME_POSTGRESQL_NOT_CONFIGURED")

    try:
        sqlite = SQLiteOutcomeRepository(settings.resolved_outcome_sqlite_path(repository_root))
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
        minimum_contract_version=settings.source_min_contract_version,
        required_capabilities=settings.required_capabilities,
        ontology_path=settings.resolved_canonical_ontology_path(repository_root),
        source_profile_path=settings.resolved_source_graph_profile_path(repository_root),
        expected_source_profile_sha256=settings.require_source_profile_sha256(),
    )
    foundation_pipeline = None
    foundation_configuration_code = FOUNDATION_PIPELINE_NOT_CONFIGURED
    if settings.salesforce_repository_root is not None:
        try:
            salesforce_repository_root, salesforce_app_root = settings.resolved_salesforce_roots(
                repository_root
            )
            foundation_pipeline = CandidateFoundationPipeline.from_host_configuration(
                project_id=source.project_id,
                repository_root=salesforce_repository_root,
                salesforce_app_root=salesforce_app_root,
                implementation_root=repository_root,
            )
            foundation_configuration_code = None
        except Exception:
            foundation_pipeline = None
            foundation_configuration_code = FOUNDATION_PIPELINE_CONFIGURATION_INVALID
    repository: RunRepository | None = None
    checkpoint_context = None
    checkpointer = None
    persistence_warning = None
    degradation_codes: list[str] = list(settings.provider_configuration_codes)
    gap_codes: list[str] = []
    live_campaign_status_reader = create_live_campaign_status_reader(settings, repository_root)
    if live_campaign_status_reader.ledger_degradation_code:
        degradation_codes.append(live_campaign_status_reader.ledger_degradation_code)
    if live_campaign_status_reader.ledger_mode == "UNAVAILABLE":
        gap_codes.append("LIVE_RECEIPT_LEDGER_UNAVAILABLE")
    if settings.database_url:
        database_url = settings.database_url.get_secret_value()
        try:
            postgres = PostgresRunRepository(database_url, schema=settings.postgres_schema)
            postgres.setup()
            checkpoint_context = PostgresSaver.from_conn_string(
                scoped_connection_string(database_url, settings.postgres_schema)
            )
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
        outcome_repository, outcome_degradations, outcome_gaps = _select_outcome_repository(
            settings, repository_root
        )
        degradation_codes.extend(outcome_degradations)
        gap_codes.extend(outcome_gaps)
        source_profile = source.source_profile
        if source_profile is None:
            raise RuntimeError("SOURCE_PROFILE_UNAVAILABLE")
        candidate_source_profile = (
            foundation_pipeline.source_profile
            if isinstance(foundation_pipeline, CandidateFoundationPipeline)
            else source_profile
        )
        return AssuranceService(
            source,
            repository,
            model_provider=(
                None if settings.provider_calls_blocked else create_model_provider(settings)
            ),
            specialist_provider=(
                None if settings.provider_calls_blocked else create_specialist_provider(settings)
            ),
            checkpointer=checkpointer,
            checkpoint_context=checkpoint_context,
            persistence_mode=AssuranceService._repository_mode(repository),
            persistence_warning=persistence_warning,
            outcome_repository=outcome_repository,
            outcome_persistence=AssuranceService._outcome_repository_mode(outcome_repository),
            degradation_codes=degradation_codes,
            gap_codes=gap_codes,
            foundation_pipeline=foundation_pipeline,
            foundation_configuration_code=foundation_configuration_code,
            live_campaign_status_reader=live_campaign_status_reader,
            live_baseline_service_factory=lambda candidate_provider: (
                create_live_baseline_service(
                    settings,
                    repository_root,
                    candidate_provider=candidate_provider,
                    source_profile=candidate_source_profile,
                )
            ),
        )
    except Exception as exc:
        if checkpoint_context is not None:
            with suppress(Exception):
                checkpoint_context.__exit__(type(exc), exc, exc.__traceback__)
        raise


def _successful_specialist_capture_count(bundle: CandidateAssuranceBundle) -> int:
    return sum(
        1
        for analysis in bundle.analyses
        for capture in analysis.specialist_captures
        if capture.artifact.provider_receipt.status is ProviderCallStatus.SUCCESS
        and bool(capture.artifact.proposals)
    )
