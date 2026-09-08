from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import (
    AgentActivity,
    AgentDeliverable,
    AgentStatus,
    AssuranceRun,
    DecisionCode,
    ReleaseDecision,
    RunStatus,
    TestSelection,
)
from neo_sf_q_intel.governance import assess_run, build_grounded_claims, decide
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.healing import propose_safe_healing
from neo_sf_q_intel.observability import emit_agent_activity


class WorkflowState(TypedDict, total=False):
    run: AssuranceRun
    candidate_tests: list[TestSelection]


class AssuranceWorkflow:
    """Typed, deterministic orchestration around bounded specialist stages."""

    def __init__(
        self,
        analysis: ChangeIntelligenceService,
        checkpointer: Any | None = None,
        governance_policy: GovernancePolicy | None = None,
    ) -> None:
        self.analysis = analysis
        self.governance_policy = governance_policy or GovernancePolicy.load()
        graph = StateGraph(WorkflowState)
        graph.add_node(
            "change_analyst",
            self._guard_stage(
                "Change Analyst",
                "change_analysis",
                ["reasoning.graph-impact"],
                self._change_analyst,
            ),
        )
        graph.add_node(
            "test_intelligence",
            self._guard_stage(
                "Test Intelligence",
                "test_selection",
                ["quality.test-selection"],
                self._test_intelligence,
            ),
        )
        graph.add_node(
            "ui_healing",
            self._guard_stage(
                "UI Healing",
                "healing_strategy",
                ["automation.locator-healing"],
                self._ui_healing,
            ),
        )
        graph.add_node(
            "governance_review",
            self._guard_stage(
                "Governance Review",
                "release_governance",
                ["governance.claim-grounding", "governance.release-decision"],
                self._governance_review,
            ),
        )
        graph.add_edge(START, "change_analyst")
        graph.add_edge("change_analyst", "test_intelligence")
        graph.add_edge("test_intelligence", "ui_healing")
        graph.add_edge("ui_healing", "governance_review")
        graph.add_edge("governance_review", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    def run(self, run: AssuranceRun) -> AssuranceRun:
        initial = run.model_copy(update={"status": RunStatus.RUNNING}, deep=True)
        result = self.graph.invoke(
            {"run": initial},
            config={"configurable": {"thread_id": str(run.run_id)}},
        )
        return result["run"]

    def _guard_stage(
        self,
        agent: str,
        stage: str,
        capability_ids: list[str],
        operation: Callable[[WorkflowState], WorkflowState],
    ) -> Callable[[WorkflowState], WorkflowState]:
        def guarded(state: WorkflowState) -> WorkflowState:
            if state["run"].status is RunStatus.FAILED:
                return state
            started_at, started_ns = datetime.now(UTC), perf_counter_ns()
            try:
                return operation(state)
            except Exception as exc:  # stage boundary must convert failures into bounded evidence
                run = state["run"].model_copy(deep=True)
                run.status = RunStatus.FAILED
                run.decision = ReleaseDecision(
                    code=DecisionCode.INCOMPLETE,
                    reasons=["STAGE_EXECUTION_FAILED: trusted stage output is unavailable."],
                )
                error_class = self._safe_error_class(exc)
                activity = self._activity(
                    run=run,
                    capability_ids=capability_ids,
                    agent=agent,
                    stage=stage,
                    status=AgentStatus.FAILED,
                    started_at=started_at,
                    started_ns=started_ns,
                    summary=f"{agent} failed before producing a trusted stage result.",
                    input_evidence_ids=[],
                    output_artifact_ids=[],
                    policy_refs=[],
                    gap_codes=["STAGE_EXECUTION_FAILED"],
                    error_class=error_class,
                )
                run.activities.append(activity)
                run.deliverables.append(
                    AgentDeliverable(
                        agent=agent,
                        capability_ids=capability_ids,
                        status=AgentStatus.FAILED,
                        conclusion=activity.summary,
                        blocking_gaps=["STAGE_EXECUTION_FAILED"],
                        measurements={"failure_count": 1, "error_class": error_class},
                        next_permitted_action=(
                            "Inspect the sanitized failure class and retry only after correction."
                        ),
                    )
                )
                return {"run": run}

        return guarded

    @staticmethod
    def _safe_error_class(exc: Exception) -> str:
        if isinstance(exc, TimeoutError):
            return "TIMEOUT"
        if isinstance(exc, (ValueError, TypeError)):
            return "VALIDATION_ERROR"
        if isinstance(exc, OSError):
            return "DEPENDENCY_ERROR"
        return "STAGE_ERROR"

    def _change_analyst(self, state: WorkflowState) -> WorkflowState:
        started_at, started_ns = datetime.now(UTC), perf_counter_ns()
        run = state["run"].model_copy(deep=True)
        evidence, impacts, tests, gaps = self.analysis.analyze(run.request)
        run.evidence = evidence
        run.impacts = impacts
        run.analysis_gaps = gaps
        status = AgentStatus.COMPLETED if evidence else AgentStatus.ABSTAINED
        activity = self._activity(
            run=run,
            capability_ids=["reasoning.graph-impact"],
            agent="Change Analyst",
            stage="change_analysis",
            status=status,
            started_at=started_at,
            started_ns=started_ns,
            summary=(
                f"Grounded {len(impacts)} impacts in {len(evidence)} evidence items; "
                f"recorded {len(gaps)} analysis gaps."
            ),
            input_evidence_ids=[],
            output_artifact_ids=[item.entity_id for item in impacts],
            policy_refs=self._reasoning_refs(run),
            gap_codes=[gap.code for gap in gaps],
        )
        run.activities.append(activity)
        run.deliverables.append(
            AgentDeliverable(
                agent="Change Analyst",
                capability_ids=["reasoning.graph-impact"],
                status=status,
                conclusion=activity.summary,
                evidence_ids=[item.evidence_id for item in evidence],
                blocking_gaps=[gap.code for gap in gaps if gap.blocking],
                nonblocking_gaps=[gap.code for gap in gaps if not gap.blocking],
                measurements={"impact_count": len(impacts), "evidence_count": len(evidence)},
                next_permitted_action="Select graph-connected tests or stop on blocking gaps.",
            )
        )
        return {"run": run, "candidate_tests": tests}

    def _test_intelligence(self, state: WorkflowState) -> WorkflowState:
        started_at, started_ns = datetime.now(UTC), perf_counter_ns()
        run = state["run"].model_copy(deep=True)
        selected = state.get("candidate_tests", [])
        run.selected_tests = selected
        status = AgentStatus.COMPLETED if selected else AgentStatus.ABSTAINED
        evidence_ids = [evidence for item in selected for evidence in item.evidence_ids]
        activity = self._activity(
            run=run,
            capability_ids=["quality.test-selection"],
            agent="Test Intelligence",
            stage="test_selection",
            status=status,
            started_at=started_at,
            started_ns=started_ns,
            summary=f"Selected {len(selected)} graph-connected validations.",
            input_evidence_ids=evidence_ids,
            output_artifact_ids=[item.test_id for item in selected],
            policy_refs=self._reasoning_refs(run),
            gap_codes=[],
        )
        run.activities.append(activity)
        run.deliverables.append(
            AgentDeliverable(
                agent="Test Intelligence",
                capability_ids=["quality.test-selection"],
                status=status,
                conclusion=activity.summary,
                evidence_ids=evidence_ids,
                measurements={"selected_test_count": len(selected)},
                next_permitted_action=(
                    "Execute selected tests through a trusted runner when available."
                ),
            )
        )
        return {"run": run, "candidate_tests": selected}

    def _ui_healing(self, state: WorkflowState) -> WorkflowState:
        started_at, started_ns = datetime.now(UTC), perf_counter_ns()
        run = state["run"].model_copy(deep=True)
        proposals = propose_safe_healing(run.impacts)
        run.healing_proposals = proposals
        status = AgentStatus.COMPLETED if proposals else AgentStatus.ABSTAINED
        evidence_ids = [evidence for item in proposals for evidence in item.evidence_ids]
        activity = self._activity(
            run=run,
            capability_ids=["automation.locator-healing"],
            agent="UI Healing",
            stage="healing_strategy",
            status=status,
            started_at=started_at,
            started_ns=started_ns,
            summary=(
                f"Proposed {len(proposals)} evidence-bound locator strategies; "
                "no browser action was executed."
            ),
            input_evidence_ids=evidence_ids,
            output_artifact_ids=[item.target_id for item in proposals],
            policy_refs=[],
            gap_codes=["BROWSER_WORKER_UNAVAILABLE"] if proposals else [],
        )
        run.activities.append(activity)
        run.deliverables.append(
            AgentDeliverable(
                agent="UI Healing",
                capability_ids=["automation.locator-healing"],
                status=status,
                conclusion=activity.summary,
                evidence_ids=evidence_ids,
                nonblocking_gaps=["BROWSER_WORKER_UNAVAILABLE"] if proposals else [],
                measurements={"strategy_proposal_count": len(proposals)},
                next_permitted_action=(
                    "Request human approval before a browser worker applies a unique candidate."
                ),
            )
        )
        return {"run": run}

    def _governance_review(self, state: WorkflowState) -> WorkflowState:
        started_at, started_ns = datetime.now(UTC), perf_counter_ns()
        run = state["run"].model_copy(deep=True)
        run.claims = build_grounded_claims(run)
        run.governance = assess_run(run, self.governance_policy)
        run.decision = decide(run, self.governance_policy)
        run.status = RunStatus.COMPLETED
        status = AgentStatus.COMPLETED if run.governance.passed else AgentStatus.ABSTAINED
        gap_codes = [item.reason_code for item in run.governance.guardrails if item.blocking]
        activity = self._activity(
            run=run,
            capability_ids=["governance.claim-grounding", "governance.release-decision"],
            agent="Governance Review",
            stage="release_governance",
            status=status,
            started_at=started_at,
            started_ns=started_ns,
            summary=f"Applied governance gates; decision is {run.decision.code}.",
            input_evidence_ids=run.decision.evidence_ids,
            output_artifact_ids=[f"decision:{run.decision.code}"],
            policy_refs=[f"governance:{run.governance.policy_sha256}"],
            gap_codes=gap_codes,
        )
        run.activities.append(activity)
        run.deliverables.append(
            AgentDeliverable(
                agent="Governance Review",
                capability_ids=["governance.claim-grounding", "governance.release-decision"],
                status=status,
                conclusion=activity.summary,
                evidence_ids=run.decision.evidence_ids,
                blocking_gaps=gap_codes,
                measurements={
                    "metric_count": len(run.governance.metrics),
                    "guardrail_count": len(run.governance.guardrails),
                    "decision": run.decision.code,
                },
                next_permitted_action=(
                    "Proceed only within the permissions represented by the release decision."
                ),
            )
        )
        return {"run": run}

    @staticmethod
    def _reasoning_refs(run: AssuranceRun) -> list[str]:
        pairs = (
            ("reasoning", run.reasoning_policy_sha256),
            ("project", run.request.project_id),
            ("source-snapshot", run.source_snapshot),
            ("ontology", run.ontology_sha256),
            ("source-profile", run.source_profile_sha256),
            ("normalized-graph", run.normalized_graph_sha256),
            ("source-graph", run.source_graph_sha256),
        )
        return [f"{label}:{value}" for label, value in pairs if value]

    @staticmethod
    def _activity(
        *,
        run: AssuranceRun,
        capability_ids: list[str],
        agent: str,
        stage: str,
        status: AgentStatus,
        started_at: datetime,
        started_ns: int,
        summary: str,
        input_evidence_ids: list[str],
        output_artifact_ids: list[str],
        policy_refs: list[str],
        gap_codes: list[str],
        error_class: str | None = None,
    ) -> AgentActivity:
        activity = AgentActivity(
            capability_ids=capability_ids,
            agent=agent,
            stage=stage,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            summary=summary,
            input_evidence_ids=list(dict.fromkeys(input_evidence_ids))[:100],
            output_artifact_ids=list(dict.fromkeys(output_artifact_ids))[:100],
            policy_refs=policy_refs,
            gap_codes=list(dict.fromkeys(gap_codes))[:100],
            error_class=error_class,
        )
        emit_agent_activity(run_id=run.run_id, trace_id=run.trace_id, activity=activity)
        return activity
