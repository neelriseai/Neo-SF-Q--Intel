from __future__ import annotations

from pathlib import Path

import pytest

from neo_sf_q_intel.context_feeds import ContextFeedError, FieldMetadata, KnowledgeSection
from neo_sf_q_intel.locator_healing_cli import (
    _context_feeds_for_request,
    _field_metadata_for,
)
from neo_sf_q_intel.locator_proposal import ContextToolPlanRecord
from neo_sf_q_intel.specialist import ProviderCallStatus, ProviderInvocationReceipt


class _Settings:
    def __init__(self, *, root: Path | None = Path("source"), fail: bool = False) -> None:
        self.root = root
        self.fail = fail

    def resolved_salesforce_root(self, repository_root: Path | None = None) -> Path:
        if self.fail or self.root is None:
            raise ValueError("not configured")
        return (repository_root or Path.cwd()) / self.root


def test_cli_metadata_enrichment_reads_configured_source_root() -> None:
    calls: list[tuple[Path, str, str]] = []

    def lookup(root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata:
        calls.append((root, object_api_name, field_api_name))
        return FieldMetadata(
            objectApiName=object_api_name,
            fieldApiName=field_api_name,
            type="Lookup",
            label="Regional VP Approver",
            required=False,
            sourcePath="force-app/main/default/objects/Opportunity/fields/x.field-meta.xml",
        )

    result = _field_metadata_for(
        _Settings(root=Path("dx")),
        object_api_name="Opportunity",
        field_api_name="Regional_VP_Approver__c",
        lookup=lookup,
        repository_root=Path("repo"),
    )

    assert result is not None
    assert result.field_type == "Lookup"
    assert calls == [
        (
            Path("repo") / "dx",
            "Opportunity",
            "Regional_VP_Approver__c",
        )
    ]


def test_cli_metadata_enrichment_is_non_fatal_when_source_root_is_unconfigured() -> None:
    called = False

    def lookup(root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata:
        nonlocal called
        called = True
        raise AssertionError("lookup should not be called")

    assert (
        _field_metadata_for(
            _Settings(fail=True),
            object_api_name="Opportunity",
            field_api_name="Regional_VP_Approver__c",
            lookup=lookup,
            repository_root=Path("repo"),
        )
        is None
    )
    assert called is False


def test_cli_metadata_enrichment_is_non_fatal_when_lookup_fails() -> None:
    def lookup(root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata:
        raise ContextFeedError("FIELD_NOT_FOUND")

    assert (
        _field_metadata_for(
            _Settings(),
            object_api_name="Opportunity",
            field_api_name="Absent__c",
            lookup=lookup,
            repository_root=Path("repo"),
        )
        is None
    )


def test_context_feeds_resolve_one_requested_knowledge_section_without_graph_fallback() -> None:
    calls: list[dict[str, object]] = []

    def knowledge_lookup(root: Path, **kwargs: object) -> KnowledgeSection:
        calls.append({"root": root, **kwargs})
        return KnowledgeSection(
            path="knowledge-repo/pages/strategic-deal-workbench.md",
            section="Page elements",
            body="Regional VP Approver must be selected from the lookup suggestion.",
            chars=67,
            truncated=False,
        )

    result = _context_feeds_for_request(
        {
            "intentLookup": {
                "page": "strategic-deal-workbench",
                "section": "Page elements",
            },
            "evidenceGraphLookup": {"entityId": "field:Opportunity.Regional_VP_Approver__c"},
        },
        repository_root=Path("repo"),
        knowledge_lookup=knowledge_lookup,
    )

    assert result.graph_edges == ()
    assert result.intent_section is not None
    assert result.intent_section.startswith("knowledge-repo/pages/strategic-deal-workbench.md")
    assert "Regional VP Approver" in result.intent_section
    assert calls == [
        {
            "root": Path("repo"),
            "page": "strategic-deal-workbench",
            "module": None,
            "impact": None,
            "section": "Page elements",
        }
    ]


def test_context_feeds_keep_caller_supplied_bounded_context_over_lookup() -> None:
    def knowledge_lookup(root: Path, **kwargs: object) -> KnowledgeSection:
        raise AssertionError("raw intentSection should already be bounded by the caller")

    result = _context_feeds_for_request(
        {
            "intentSection": "caller-selected section",
            "intentLookup": {"page": "strategic-deal-workbench"},
            "graphEdges": ["field:Opportunity.Regional_VP_Approver__c -> changed-by -> delta:1"],
        },
        repository_root=Path("repo"),
        knowledge_lookup=knowledge_lookup,
    )

    assert result.intent_section == "caller-selected section"
    assert result.graph_edges == (
        "field:Opportunity.Regional_VP_Approver__c -> changed-by -> delta:1",
    )


def test_context_feeds_execute_planned_knowledge_section_when_no_explicit_context() -> None:
    calls: list[dict[str, object]] = []

    def knowledge_lookup(root: Path, **kwargs: object) -> KnowledgeSection:
        calls.append({"root": root, **kwargs})
        return KnowledgeSection(
            path="knowledge-repo/impact/field-impact-matrix.md",
            section="Opportunity fields",
            body="Regional VP Approver drives native approval route.",
            chars=51,
            truncated=False,
        )

    plan = ContextToolPlanRecord(
        accepted=True,
        promptSha256="0" * 64,
        receipt=_receipt(),
        toolCalls=[
            {
                "tool": "knowledge_section",
                "arguments": {"impact": "field-impact-matrix", "section": "Opportunity fields"},
            }
        ],
        rationale="Impact section clarifies field role.",
    )

    result = _context_feeds_for_request(
        {},
        context_plan=plan,
        repository_root=Path("repo"),
        knowledge_lookup=knowledge_lookup,
    )

    assert result.intent_section is not None
    assert "Regional VP Approver drives native approval route" in result.intent_section
    assert calls == [
        {
            "root": Path("repo"),
            "page": None,
            "module": None,
            "impact": "field-impact-matrix",
            "section": "Opportunity fields",
        }
    ]


def test_context_feeds_ignore_planned_whole_document_to_avoid_noisy_context() -> None:
    def knowledge_lookup(root: Path, **kwargs: object) -> KnowledgeSection:
        raise AssertionError("broad whole-document plan must not be executed")

    plan = ContextToolPlanRecord(
        accepted=True,
        promptSha256="0" * 64,
        receipt=_receipt(),
        toolCalls=[
            {
                "tool": "knowledge_section",
                "arguments": {"module": "persona-permission-journeys"},
            }
        ],
        rationale="Broad context is not suitable for locator ranking.",
    )

    result = _context_feeds_for_request(
        {},
        context_plan=plan,
        repository_root=Path("repo"),
        knowledge_lookup=knowledge_lookup,
    )

    assert result.intent_section is None
    assert result.graph_edges == ()


def test_context_feeds_can_use_injected_evidence_graph_lookup_without_static_app_graph() -> None:
    calls: list[dict[str, object]] = []

    def evidence_lookup(raw: object) -> tuple[str, ...]:
        assert isinstance(raw, dict)
        calls.append(raw)
        return ("field:Opportunity.Regional_VP_Approver__c -> MODIFY -> lwc:Workbench",)

    result = _context_feeds_for_request(
        {
            "evidenceGraphLookup": {
                "entityId": "field:Opportunity.Regional_VP_Approver__c",
                "side": "CANDIDATE",
            }
        },
        repository_root=Path("repo"),
        evidence_graph_lookup=evidence_lookup,
    )

    assert result.graph_edges == (
        "field:Opportunity.Regional_VP_Approver__c -> MODIFY -> lwc:Workbench",
    )
    assert calls == [
        {"entityId": "field:Opportunity.Regional_VP_Approver__c", "side": "CANDIDATE"}
    ]


def test_context_feeds_omit_missing_knowledge_section_instead_of_adding_ambiguous_context() -> None:
    def knowledge_lookup(root: Path, **kwargs: object) -> KnowledgeSection:
        raise ContextFeedError("SECTION_NOT_FOUND")

    result = _context_feeds_for_request(
        {"intentLookup": {"page": "strategic-deal-workbench", "section": "Missing"}},
        repository_root=Path("repo"),
        knowledge_lookup=knowledge_lookup,
    )

    assert result.intent_section is None
    assert result.graph_edges == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"intentLookup": {"page": "strategic-deal-workbench", "module": "policy"}},
        {"intentLookup": {"page": 7}},
        {"intentSection": ["too", "wide"]},
        {"graphEdges": "field:Opportunity.Name -> noisy -> object:Opportunity"},
        {"evidenceGraphLookup": "field:Opportunity.Name"},
    ],
)
def test_context_feeds_reject_malformed_context_requests(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="REQUEST_SCHEMA_INVALID"):
        _context_feeds_for_request(payload, repository_root=Path("repo"))


def _receipt() -> ProviderInvocationReceipt:
    return ProviderInvocationReceipt(
        status=ProviderCallStatus.SUCCESS,
        provider_kind="openai",
        model_id="gpt-test",
        deployment_id="gpt-test",
        model_version="gpt-test",
        api_version="responses-v1",
        response_format="STRICT_JSON_SCHEMA",
        temperature_milli=0,
        top_p_milli=1000,
        reasoning_profile="locator-context-plan-v1",
        tools_enabled=False,
        provider_profile_sha256="1" * 64,
        invoked_at="2026-09-13T00:00:00.000Z",
        completed_at="2026-09-13T00:00:01.000Z",
        request_sha256="0" * 64,
        prompt_sha256="0" * 64,
        response_sha256="2" * 64,
        finish_reason="STOP",
        input_tokens=10,
        output_tokens=5,
        duration_milliseconds=1000,
        timeout_milliseconds=30000,
        maximum_output_tokens=500,
        maximum_total_tokens=16000,
    )
