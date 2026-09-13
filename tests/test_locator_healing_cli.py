from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from neo_sf_q_intel.context_feeds import ContextFeedError, FieldMetadata, KnowledgeSection
from neo_sf_q_intel.locator_healing_cli import (
    _combined_intent_sections,
    _context_feeds_for_request,
    _field_metadata_for,
    _propose_with_incremental_context,
    _ranking_context_is_adequate,
    _ranking_summary,
    _section_summary,
)
from neo_sf_q_intel.locator_proposal import (
    ContextToolPlanRecord,
    LocatorProposalRecord,
    build_healing_context,
)
from neo_sf_q_intel.specialist import (
    ProviderCallOutcome,
    ProviderCallStatus,
    ProviderFinishReason,
    ProviderInvocationReceipt,
    ProviderProfile,
)


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


def test_context_feeds_use_preloaded_incremental_intent_before_lookup() -> None:
    def knowledge_lookup(root: Path, **kwargs: object) -> KnowledgeSection:
        raise AssertionError("incremental context already resolved the bounded section")

    result = _context_feeds_for_request(
        {"intentLookup": {"page": "strategic-deal-workbench", "section": "Page elements"}},
        repository_root=Path("repo"),
        preloaded_intent_section="incremental field-level section",
        knowledge_lookup=knowledge_lookup,
    )

    assert result.intent_section == "incremental field-level section"


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


def test_incremental_context_summary_and_combination_are_bounded() -> None:
    section = "\n".join(
        [
            "knowledge-repo/pages/strategic-deal-workbench.md#Field behavior",
            "Regional VP Approver uses lookup suggestion selection.",
            "Approval status changes after submission.",
            "extra",
        ]
    )

    summary = _section_summary(section)
    combined = _combined_intent_sections([section, "second section"])

    assert "Regional VP Approver" in summary
    assert len(summary) <= 512
    assert combined is not None
    assert "[intent_context_1]" in combined
    assert "second section" in combined


def test_incremental_context_requires_intent_citation_when_context_was_fetched() -> None:
    without_intent = _proposal_record(cited_refs=["cand:0"], confidence_milli=990)
    with_intent = _proposal_record(
        cited_refs=["cand:0", "intent:L08.regional-vp-approver.lookup"],
        confidence_milli=900,
    )
    partial_intent = _proposal_record(
        cited_refs=["cand:0", "intent:L08.regional-vp-approver.lookup"],
        confidence_milli=990,
        intent_fit="PARTIAL",
        missing_context="PAGE_FLOW",
    )

    assert _ranking_context_is_adequate(without_intent, False) is True
    assert _ranking_context_is_adequate(without_intent, True) is False
    assert _ranking_context_is_adequate(with_intent, True) is True
    assert _ranking_context_is_adequate(partial_intent, True) is False


def test_incremental_ranking_summary_is_safe_and_compact() -> None:
    record = _proposal_record(
        cited_refs=["cand:0", "intent:L08.regional-vp-approver.lookup"],
        confidence_milli=875,
    )

    summary = _ranking_summary(record)

    assert summary == {
        "accepted": True,
        "rejectionCode": None,
        "candidateOrdinal": 0,
        "confidenceMilli": 875,
        "citedRefs": ["cand:0", "intent:L08.regional-vp-approver.lookup"],
        "intentCited": True,
        "intentFit": "SUFFICIENT",
        "missingContext": "NONE",
    }


def test_incremental_planner_uses_real_knowledge_repo_until_intent_is_sufficient() -> None:
    request = {
        "obligationId": "L08.regional-vp-approver.lookup",
        "objectApiName": "Opportunity",
        "fieldApiName": "Regional_VP_Approver__c",
        "domEvidence": {
            "candidates": [
                {
                    "ordinal": 0,
                    "tag": "input",
                    "role": "combobox",
                    "structure": "lightning-input-field>input[role=combobox]",
                    "attrNames": ["data-value", "role", "title"],
                    "attrHashes": {"data-value": "a" * 16, "title": "b" * 16},
                    "nameDigest": "c" * 16,
                    "nearby": ["label", "lightning-icon", "lookup"],
                    "visible": True,
                    "enabled": True,
                },
                {
                    "ordinal": 1,
                    "tag": "input",
                    "role": "textbox",
                    "structure": "lightning-input>input[type=text]",
                    "attrNames": ["name", "type"],
                    "attrHashes": {"name": "d" * 16, "type": "e" * 16},
                    "nameDigest": "f" * 16,
                    "nearby": ["amount", "discount"],
                    "visible": True,
                    "enabled": True,
                },
            ]
        },
    }
    metadata = FieldMetadata(
        objectApiName="Opportunity",
        fieldApiName="Regional_VP_Approver__c",
        type="Lookup",
        label="Regional VP Approver",
        required=False,
        sourcePath="force-app/main/default/objects/Opportunity/fields/Regional_VP_Approver__c.field-meta.xml",
    )
    base_context = build_healing_context(
        obligation_id=request["obligationId"],
        object_api_name=request["objectApiName"],
        field_api_name=request["fieldApiName"],
        dom_evidence=request["domEvidence"],
        field_metadata=metadata,
    )
    planner = _QueueProvider(
        [
            _json_outcome(
                {
                    "toolCalls": [
                        {
                            "tool": "knowledge_section",
                            "arguments": {
                                "page": "strategic-deal-workbench",
                                "module": None,
                                "impact": None,
                                "section": "Field behavior in plain English",
                            },
                        }
                    ],
                    "rationale": "Start with the narrow field behavior section.",
                }
            ),
            _json_outcome(
                {
                    "toolCalls": [
                        {
                            "tool": "knowledge_section",
                            "arguments": {
                                "page": "strategic-deal-workbench",
                                "module": None,
                                "impact": None,
                                "section": "Main happy path",
                            },
                        }
                    ],
                    "rationale": "Previous ranking was partial; add the exact page flow.",
                }
            ),
        ]
    )
    ranker = _QueueProvider(
        [
            _json_outcome(
                _proposal_json(
                    intent_fit="PARTIAL",
                    missing_context="PAGE_FLOW",
                    rationale=(
                        "Candidate 0 looks like a lookup, but the field section alone does not "
                        "prove the full interaction."
                    ),
                )
            ),
            _json_outcome(
                _proposal_json(
                    intent_fit="SUFFICIENT",
                    missing_context="NONE",
                    rationale=(
                        "Candidate 0 matches the lookup field and the fetched flow says typed "
                        "lookup text must be selected from the suggestion."
                    ),
                )
            ),
        ]
    )

    record, plans = _propose_with_incremental_context(
        request,
        base_context,
        ranker,
        planner,
        repository_root=Path.cwd(),
        field_metadata=metadata,
    )

    assert record.accepted is True
    assert record.proposal is not None
    assert record.proposal.candidate_ordinal == 0
    assert record.proposal.context_assessment.intent_fit == "SUFFICIENT"
    assert len(plans) == 2
    assert len(planner.prompts) == 2
    assert len(ranker.prompts) == 2
    assert "Field behavior in plain English" in planner.prompts[0]
    assert "priorContext" in planner.prompts[1]
    assert "previousRanking" in planner.prompts[1]
    assert "Main happy path" in ranker.prompts[1]


def test_incremental_planner_downgrades_when_final_context_stays_partial() -> None:
    request, metadata, base_context = _incremental_request_fixture("Strategic_Deal__c")
    planner = _QueueProvider(
        [
            _json_outcome(
                {
                    "toolCalls": [
                        {
                            "tool": "knowledge_section",
                            "arguments": {
                                "page": "strategic-deal-workbench",
                                "module": None,
                                "impact": None,
                                "section": "Field behavior in plain English",
                            },
                        }
                    ],
                    "rationale": "Start with bounded field behavior.",
                }
            ),
            _json_outcome(
                {
                    "toolCalls": [
                        {
                            "tool": "knowledge_section",
                            "arguments": {
                                "page": "strategic-deal-workbench",
                                "module": None,
                                "impact": None,
                                "section": "Live-observed page elements",
                            },
                        }
                    ],
                    "rationale": "Add page elements.",
                }
            ),
        ]
    )
    ranker = _QueueProvider(
        [
            _json_outcome(
                _proposal_json(
                    intent_fit="PARTIAL",
                    missing_context="PAGE_FLOW",
                    rationale="First context is still partial.",
                    obligation_id=request["obligationId"],
                )
            ),
            _json_outcome(
                _proposal_json(
                    intent_fit="PARTIAL",
                    missing_context="FIELD_BEHAVIOR",
                    rationale="Second context is still partial.",
                    obligation_id=request["obligationId"],
                )
            ),
        ]
    )

    record, plans = _propose_with_incremental_context(
        request,
        base_context,
        ranker,
        planner,
        repository_root=Path.cwd(),
        field_metadata=metadata,
    )

    assert len(plans) == 2
    assert record.accepted is False
    assert record.rejection_code == "PROPOSAL_CONTEXT_INSUFFICIENT"
    assert record.proposal is not None
    assert record.proposal.candidate_ordinal == 0


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


class _QueueProvider:
    def __init__(self, outcomes: list[ProviderCallOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.prompts: list[str] = []

    @property
    def profile(self) -> ProviderProfile:
        body = {
            "provider_kind": "openai",
            "model_id": "gpt-test",
            "deployment_id": "gpt-test",
            "model_version": "gpt-test",
            "api_version": "responses-v1",
            "response_format": "STRICT_JSON_SCHEMA",
            "temperature_milli": 0,
            "top_p_milli": 1000,
            "reasoning_profile": "test",
            "tools_enabled": False,
        }
        return ProviderProfile(**body, profile_sha256=_digest(body))

    def __call__(
        self, prompt: str, *, timeout_milliseconds: int, maximum_output_tokens: int
    ) -> ProviderCallOutcome:
        self.prompts.append(prompt)
        assert self._outcomes
        return self._outcomes.pop(0)


def _json_outcome(body: dict[str, object]) -> ProviderCallOutcome:
    return ProviderCallOutcome(
        status=ProviderCallStatus.SUCCESS,
        provider_profile_sha256="1" * 64,
        invoked_at="2026-09-13T00:00:00.000Z",
        completed_at="2026-09-13T00:00:01.000Z",
        duration_milliseconds=1000,
        raw_response=json.dumps(body),
        finish_reason=ProviderFinishReason.STOP,
        input_tokens=100,
        output_tokens=20,
    )


def _proposal_json(
    *,
    intent_fit: str,
    missing_context: str,
    rationale: str,
    obligation_id: str = "L08.regional-vp-approver.lookup",
) -> dict[str, object]:
    return {
        "candidateOrdinal": 0,
        "confidenceMilli": 930,
        "rationale": rationale,
        "citedRefs": ["cand:0", f"intent:{obligation_id}"],
        "contextAssessment": {
            "intentFit": intent_fit,
            "missingContext": missing_context,
            "reason": rationale,
        },
    }


def _incremental_request_fixture(field_api_name: str):
    request = {
        "obligationId": f"L08.{field_api_name}.healing",
        "objectApiName": "Opportunity",
        "fieldApiName": field_api_name,
        "domEvidence": {
            "candidates": [
                {
                    "ordinal": 0,
                    "tag": "input",
                    "role": "checkbox",
                    "structure": "lightning-input-field>input[type=checkbox]",
                    "attrNames": ["type", "checked"],
                    "attrHashes": {"type": "a" * 16, "checked": "b" * 16},
                    "nameDigest": "c" * 16,
                    "nearby": ["strategic", "deal"],
                    "visible": True,
                    "enabled": True,
                },
                {
                    "ordinal": 1,
                    "tag": "input",
                    "role": "textbox",
                    "structure": "lightning-input-field>input[type=text]",
                    "attrNames": ["name", "type"],
                    "attrHashes": {"name": "d" * 16, "type": "e" * 16},
                    "nameDigest": "f" * 16,
                    "nearby": ["name"],
                    "visible": True,
                    "enabled": True,
                },
            ]
        },
    }
    metadata = FieldMetadata(
        objectApiName="Opportunity",
        fieldApiName=field_api_name,
        type="Checkbox",
        label="Strategic Deal",
        required=False,
        sourcePath=(
            "force-app/main/default/objects/Opportunity/fields/"
            f"{field_api_name}.field-meta.xml"
        ),
    )
    base_context = build_healing_context(
        obligation_id=request["obligationId"],
        object_api_name=request["objectApiName"],
        field_api_name=request["fieldApiName"],
        dom_evidence=request["domEvidence"],
        field_metadata=metadata,
    )
    return request, metadata, base_context


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _proposal_record(
    *,
    cited_refs: list[str],
    confidence_milli: int,
    accepted: bool = True,
    intent_fit: str = "SUFFICIENT",
    missing_context: str = "NONE",
) -> LocatorProposalRecord:
    return LocatorProposalRecord(
        obligationId="L08.regional-vp-approver.lookup",
        accepted=accepted,
        promptSha256="0" * 64,
        receipt=_receipt(),
        proposal=(
            {
                "candidateOrdinal": 0,
                "confidenceMilli": confidence_milli,
                "rationale": "Candidate matches bounded intent and DOM evidence.",
                "citedRefs": cited_refs,
                "contextAssessment": {
                    "intentFit": intent_fit,
                    "missingContext": missing_context,
                    "reason": "Intent section directly describes the lookup field.",
                },
            }
            if accepted
            else None
        ),
    )
