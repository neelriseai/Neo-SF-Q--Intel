from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import ChangeIntent, ChangeRequest
from neo_sf_q_intel.salesforce_cli import SalesforceCLI
from neo_sf_q_intel.service import AssuranceService, create_service


def build_mcp(
    settings: Settings,
    service: AssuranceService,
) -> MCPServer:
    server = MCPServer("Neo SF Q-Intel")

    @server.tool()
    def analyze_change(
        requirement: str,
        changed_paths: list[str] | None = None,
        change_intent: ChangeIntent = ChangeIntent.INFORMATIONAL,
    ) -> dict:
        """Analyze Salesforce change impact and return evidence-governed output."""
        run = service.analyze(
            ChangeRequest(
                requirement=requirement,
                changed_paths=changed_paths or [],
                change_intent=change_intent,
            )
        )
        return run.model_dump(mode="json")

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
        """Check the configured non-production org without returning auth material."""
        return SalesforceCLI(settings.require_operator_alias()).org_status()

    return server


def run() -> None:
    settings = Settings()
    service = create_service(settings, Path.cwd())
    build_mcp(settings, service).run(transport="stdio")
