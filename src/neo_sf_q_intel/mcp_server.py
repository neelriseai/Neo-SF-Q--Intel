from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import ChangeIntent, ChangeRequest
from neo_sf_q_intel.service import AssuranceService, create_service


class _NeoMCPServer(MCPServer):
    """Enforce genuinely empty input for host-owned, zero-scope operations."""

    _EMPTY_INPUT_TOOLS = frozenset({"run_live_salesforce_baseline"})

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
    ) -> Any:
        if name in self._EMPTY_INPUT_TOOLS and arguments:
            raise ToolError(f"Tool {name} accepts no caller input")
        return await super().call_tool(name, arguments, context)


def build_mcp(
    settings: Settings,
    service: AssuranceService,
) -> MCPServer:
    server = _NeoMCPServer("Neo SF Q-Intel")

    @server.tool()
    def analyze_change(
        requirement: str,
        changed_paths: list[str] | None = None,
        change_intent: ChangeIntent = ChangeIntent.INFORMATIONAL,
    ) -> dict:
        """Analyze Salesforce change impact and return evidence-governed output."""
        if change_intent is ChangeIntent.VERIFIED_CHANGE:
            raise ValueError("VERIFIED_CHANGE_REQUIRES_HOST_CAPTURE")
        run = service.analyze(
            ChangeRequest(
                requirement=requirement,
                changed_paths=changed_paths or [],
                change_intent=change_intent,
            )
        )
        return run.model_dump(mode="json")

    @server.tool()
    def analyze_current_candidate() -> dict:
        """Replay and analyze the host-configured Git candidate without caller scope."""
        return service.analyze_current_candidate_view().model_dump(mode="json")

    @server.tool()
    def run_live_salesforce_baseline() -> dict:
        """Run the fixed host-owned live baseline without accepting caller scope."""
        return service.run_live_baseline().model_dump(mode="json")

    @server.tool()
    async def search_evidence(query: str, limit: int = 8, semantic: bool = False) -> list[dict]:
        """Search source evidence lexically or by embedding similarity."""
        bounded_limit = min(50, max(1, limit))
        if semantic:
            hits = await service.semantic_search(query, bounded_limit)
            return [
                {
                    "evidence": hit.evidence.model_dump(mode="json"),
                    "score": hit.score,
                    "reasons": ["embedding cosine similarity"],
                }
                for hit in hits
            ]
        retriever = service.workflow.analysis.retriever
        return [
            {
                "evidence": retriever.evidence_for(hit.node).model_dump(mode="json"),
                "score": hit.score,
                "reasons": hit.reasons,
            }
            for hit in retriever.search(query, [], limit=bounded_limit)
        ]

    @server.tool()
    def inspect_salesforce() -> dict:
        """Report live inspection unavailable until host-owned org authorization exists."""
        return {
            "schema_version": "1.0.0",
            "status": "UNAVAILABLE",
            "capability_id": "runtime.salesforce-live-evidence",
            "reason_code": "SALESFORCE_ORG_CLASSIFICATION_UNAVAILABLE",
            "execution_status": "NOT_RUN",
            "evidence_ids": [],
            "release_eligible": False,
        }

    return server


def run() -> None:
    settings = Settings()
    service = create_service(settings, Path.cwd())
    build_mcp(settings, service).run(transport="stdio")
