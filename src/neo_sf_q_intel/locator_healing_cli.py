from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.context_feeds import (
    ContextFeedError,
    FieldMetadata,
    KnowledgeSection,
    knowledge_index,
    knowledge_section,
    metadata_lookup,
)
from neo_sf_q_intel.locator_proposal import (
    CONTEXT_PLAN_RESPONSE_SCHEMA,
    RESPONSE_SCHEMA,
    ContextToolPlanRecord,
    KnowledgeDocumentView,
    LocatorProposalError,
    build_healing_context,
    propose_context_tool_plan,
    propose_locator,
)
from neo_sf_q_intel.providers import (
    OpenAISpecialistProvider,
    ProviderConfigurationBlockedError,
)


def main() -> int:
    try:
        request = _read_request(sys.stdin.read())
        settings = Settings()
        field_metadata = _field_metadata_for(
            settings,
            object_api_name=request["objectApiName"],
            field_api_name=request["fieldApiName"],
        )
        repository_root = Path.cwd()
        base_context = build_healing_context(
            obligation_id=request["obligationId"],
            object_api_name=request["objectApiName"],
            field_api_name=request["fieldApiName"],
            dom_evidence=request["domEvidence"],
            field_metadata=field_metadata,
        )
        if not settings.allow_llm:
            return _write_blocked("LLM_DISABLED")
        context_provider = OpenAISpecialistProvider(
            settings,
            response_schema_name="locator_context_tool_plan",
            response_schema=CONTEXT_PLAN_RESPONSE_SCHEMA,
            reasoning_profile="locator-context-plan-v1",
        )
        context_plan = _context_plan_for_request(
            request,
            base_context,
            context_provider,
            repository_root=repository_root,
        )
        context_feeds = _context_feeds_for_request(
            request,
            context_plan=context_plan,
            repository_root=repository_root,
        )
        context = build_healing_context(
            obligation_id=request["obligationId"],
            object_api_name=request["objectApiName"],
            field_api_name=request["fieldApiName"],
            dom_evidence=request["domEvidence"],
            field_metadata=field_metadata,
            graph_edges=context_feeds.graph_edges,
            intent_section=context_feeds.intent_section,
        )
        provider = OpenAISpecialistProvider(
            settings,
            response_schema_name="locator_healing_proposal",
            response_schema=RESPONSE_SCHEMA,
            reasoning_profile="locator-healing-v1",
        )
        record = propose_locator(provider, context)
        body = record.model_dump(by_alias=True, mode="json", exclude_none=True)
        if context_plan is not None:
            body["contextPlan"] = context_plan.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        sys.stdout.write(json.dumps(body, sort_keys=True, separators=(",", ":")))
        sys.stdout.write("\n")
        return 0
    except (LocatorProposalError, ProviderConfigurationBlockedError, ValueError) as error:
        return _write_blocked(getattr(error, "code", str(error) or "LOCATOR_HEALING_BLOCKED"))
    except Exception:
        return _write_blocked("LOCATOR_HEALING_BRIDGE_FAILED")


def _read_request(raw: str) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("REQUEST_NOT_JSON") from error
    if not isinstance(body, dict):
        raise ValueError("REQUEST_NOT_JSON")
    required = ("obligationId", "objectApiName", "fieldApiName", "domEvidence")
    if any(key not in body for key in required):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if not all(isinstance(body[key], str) and body[key] for key in required[:3]):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if not isinstance(body["domEvidence"], Mapping):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    return body


class _ResolvedContextFeeds:
    def __init__(self, *, graph_edges: Sequence[str] = (), intent_section: str | None = None):
        self.graph_edges = tuple(graph_edges)
        self.intent_section = intent_section


def _context_feeds_for_request(
    request: Mapping[str, Any],
    *,
    context_plan: ContextToolPlanRecord | None = None,
    repository_root: Path | None = None,
    knowledge_lookup: Callable[..., KnowledgeSection] = knowledge_section,
    evidence_graph_lookup: Callable[[Mapping[str, Any]], Sequence[str]] | None = None,
) -> _ResolvedContextFeeds:
    """Resolve only bounded context explicitly requested by the caller.

    This is deliberately not an automatic knowledge-repo dump. The caller may pass already-bounded
    ``intentSection`` / ``graphEdges`` for backwards compatibility, or may request one exact
    knowledge section through ``intentLookup``. Evidence graph support is hook-shaped here but does
    not fall back to the retired static Salesforce application graph; until a change-delta accessor
    is wired in, graph context must be supplied by the caller or by an injected evidence lookup.
    """

    raw_intent = request.get("intentSection")
    if raw_intent is not None and not isinstance(raw_intent, str):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    raw_edges = request.get("graphEdges")
    if raw_edges is not None and not _string_sequence(raw_edges):
        raise ValueError("REQUEST_SCHEMA_INVALID")

    intent_section = raw_intent or _intent_section_from_lookup(
        request.get("intentLookup") or _intent_lookup_from_plan(context_plan),
        repository_root=repository_root,
        knowledge_lookup=knowledge_lookup,
    )
    graph_edges = tuple(raw_edges or ()) or _graph_edges_from_lookup(
        request.get("evidenceGraphLookup"),
        evidence_graph_lookup=evidence_graph_lookup,
    )
    return _ResolvedContextFeeds(graph_edges=graph_edges, intent_section=intent_section)


def _context_plan_for_request(
    request: Mapping[str, Any],
    base_context,
    provider,
    *,
    repository_root: Path,
) -> ContextToolPlanRecord | None:
    if request.get("contextPlanning") is False:
        return None
    if request.get("intentSection") is not None or request.get("intentLookup") is not None:
        return None
    try:
        documents = _knowledge_document_views(repository_root)
    except (ContextFeedError, OSError, ValueError):
        documents = ()
    if not documents:
        return None
    return propose_context_tool_plan(
        provider,
        base_context,
        knowledge_documents=documents,
    )


def _knowledge_document_views(repository_root: Path) -> tuple[KnowledgeDocumentView, ...]:
    index = knowledge_index(repository_root)
    views: list[KnowledgeDocumentView] = []
    for group, documents in (
        ("pages", index.pages),
        ("modules", index.modules),
        ("impact", index.impact),
    ):
        for document in documents:
            views.append(
                KnowledgeDocumentView(
                    key=document.key,
                    group=group,
                    headings=list(document.headings[:16]),
                )
            )
    return tuple(views)


def _intent_lookup_from_plan(plan: ContextToolPlanRecord | None) -> Mapping[str, Any] | None:
    if plan is None or not plan.accepted or not plan.tool_calls:
        return None
    call = plan.tool_calls[0]
    if call.tool != "knowledge_section":
        return None
    if call.arguments.section is None:
        return None
    return call.arguments.model_dump(mode="json", exclude_none=True)


def _intent_section_from_lookup(
    raw_lookup: Any,
    *,
    repository_root: Path | None,
    knowledge_lookup: Callable[..., KnowledgeSection],
) -> str | None:
    if raw_lookup is None:
        return None
    if not isinstance(raw_lookup, Mapping):
        raise ValueError("REQUEST_SCHEMA_INVALID")

    selectors = {
        "page": raw_lookup.get("page"),
        "module": raw_lookup.get("module"),
        "impact": raw_lookup.get("impact"),
    }
    if any(value is not None and not isinstance(value, str) for value in selectors.values()):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    section = raw_lookup.get("section")
    if section is not None and not isinstance(section, str):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if sum(value is not None for value in selectors.values()) != 1:
        raise ValueError("REQUEST_SCHEMA_INVALID")

    try:
        result = knowledge_lookup(
            repository_root or Path.cwd(),
            page=selectors["page"],
            module=selectors["module"],
            impact=selectors["impact"],
            section=section,
        )
    except (ContextFeedError, OSError, ValueError):
        return None

    label = result.section or "document"
    return f"{result.path}#{label}\n{result.body}"


def _graph_edges_from_lookup(
    raw_lookup: Any,
    *,
    evidence_graph_lookup: Callable[[Mapping[str, Any]], Sequence[str]] | None,
) -> tuple[str, ...]:
    if raw_lookup is None:
        return ()
    if not isinstance(raw_lookup, Mapping):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if evidence_graph_lookup is None:
        return ()
    try:
        result = evidence_graph_lookup(raw_lookup)
    except (ContextFeedError, OSError, ValueError):
        return ()
    if not _string_sequence(result):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    return tuple(result)


def _string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and all(
        isinstance(item, str) for item in value
    )


def _field_metadata_for(
    settings: Settings,
    *,
    object_api_name: str,
    field_api_name: str,
    lookup=metadata_lookup,
    repository_root: Path | None = None,
) -> FieldMetadata | None:
    """Return source metadata when configured; absence never blocks locator healing.

    Metadata improves ranking and explanations, but the model can still choose from a bounded
    digest-only DOM candidate set without it. A missing source checkout, absent field, or malformed
    metadata therefore degrades the prompt rather than turning locator healing into a hard outage.
    Identity mismatches are still caught later by ``build_healing_context`` if metadata is returned.
    """

    try:
        root = settings.resolved_salesforce_root(repository_root or Path.cwd())
        return lookup(root, object_api_name, field_api_name)
    except (ContextFeedError, OSError, ValueError):
        return None


def _write_blocked(code: str) -> int:
    safe = code if code.isupper() and code.replace("_", "").isalnum() else "LOCATOR_HEALING_BLOCKED"
    sys.stdout.write(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "accepted": False,
                "rejectionCode": safe,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
