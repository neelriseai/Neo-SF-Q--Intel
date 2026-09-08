from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import (
    AgentActivity,
    AgentStatus,
    AssuranceRun,
    Claim,
    RunStatus,
    TestSelection,
)
from neo_sf_q_intel.governance import assess_run, decide
from neo_sf_q_intel.healing import propose_safe_healing


class WorkflowState(TypedDict, total=False):
    run: AssuranceRun
    candidate_tests: list[TestSelection]


class AssuranceWorkflow:
    """Typed, deterministic orchestration around bounded specialist agents."""

    def __init__(
        self, analysis: ChangeIntelligenceService, checkpointer: Any | None = None
    ) -> None:
        self.analysis = analysis
        graph = StateGraph(WorkflowState)
        graph.add_node("change_analyst", self._change_analyst)
        graph.add_node("test_intelligence", self._test_intelligence)
        graph.add_node("ui_healing", self._ui_healing)
        graph.add_node("governance_review", self._governance_review)
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

    def _change_analyst(self, state: WorkflowState) -> WorkflowState:
        run = state["run"].model_copy(deep=True)
        evidence, impacts, tests = self.analysis.analyze(run.request)
        run.evidence = evidence
        run.impacts = impacts
        run.activities.append(
            AgentActivity(
                agent="Change Analyst",
                status=AgentStatus.COMPLETED if evidence else AgentStatus.ABSTAINED,
                summary=f"Grounded {len(impacts)} impacts in {len(evidence)} evidence items.",
                evidence_ids=[item.evidence_id for item in evidence[:20]],
            )
        )
        return {"run": run, "candidate_tests": tests}

    def _test_intelligence(self, state: WorkflowState) -> WorkflowState:
        run = state["run"].model_copy(deep=True)
        selected = state.get("candidate_tests", [])
        run.selected_tests = selected
        run.activities.append(
            AgentActivity(
                agent="Test Intelligence",
                status=AgentStatus.COMPLETED if selected else AgentStatus.ABSTAINED,
                summary=f"Selected {len(selected)} graph-connected validations.",
                evidence_ids=[evidence for item in selected for evidence in item.evidence_ids],
            )
        )
        return {"run": run, "candidate_tests": selected}

    def _ui_healing(self, state: WorkflowState) -> WorkflowState:
        run = state["run"].model_copy(deep=True)
        proposals = propose_safe_healing(run.impacts)
        run.healing_proposals = proposals
        run.activities.append(
            AgentActivity(
                agent="UI Healing",
                status=AgentStatus.COMPLETED if proposals else AgentStatus.ABSTAINED,
                summary=f"Proposed {len(proposals)} evidence-bound locator recoveries.",
                evidence_ids=[evidence for item in proposals for evidence in item.evidence_ids],
            )
        )
        return {"run": run}

    def _governance_review(self, state: WorkflowState) -> WorkflowState:
        run = state["run"].model_copy(deep=True)
        run.claims = [
            Claim(
                claim_id=f"impact:{index}",
                text=f"{impact.label} is impacted through {impact.relation}.",
                evidence_ids=impact.evidence_ids,
                supported=True,
            )
            for index, impact in enumerate(run.impacts, start=1)
        ]
        run.governance = assess_run(run)
        run.decision = decide(run)
        run.status = RunStatus.COMPLETED
        run.activities.append(
            AgentActivity(
                agent="Governance Review",
                status=(AgentStatus.COMPLETED if run.governance.passed else AgentStatus.ABSTAINED),
                summary=f"Applied governance gates; decision is {run.decision.code}.",
                evidence_ids=run.decision.evidence_ids,
            )
        )
        return {"run": run}
