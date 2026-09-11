"""Opt-in current local AUT capture; never contacts or mutates Salesforce."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from neo_sf_q_intel.candidate_target_compiler import (
    HostOwnedSourceContractPort,
    ProductionCandidateTargetCompiler,
    SourceOperationDeclarations,
)
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.foundation_pipeline import CandidateFoundationPipeline
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.salesforce_source import load_salesforce_source
from neo_sf_q_intel.service import AssuranceService


@pytest.mark.skipif(
    os.environ.get("NEO_AUT_SOURCE_INTEGRATION") != "1",
    reason="Set NEO_AUT_SOURCE_INTEGRATION=1 to capture configured local AUT source only",
)
def test_current_aut_source_declarations_are_exact_candidate_bound() -> None:
    implementation_root = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=implementation_root / ".env", allow_llm=False)
    repository_root, app_root = settings.resolved_salesforce_roots(implementation_root)
    source = load_salesforce_source(app_root)
    pipeline = CandidateFoundationPipeline.from_host_configuration(
        project_id=source.project_id,
        repository_root=repository_root,
        salesforce_app_root=app_root,
        implementation_root=implementation_root,
    )
    bundle = AssuranceService(
        source, InMemoryRunRepository(), foundation_pipeline=pipeline
    ).analyze_current_candidate()
    locator = (app_root / "contracts/agent-interface.json").relative_to(repository_root).as_posix()
    inputs = HostOwnedSourceContractPort(repository_root, locator).capture(
        bundle, pipeline.source_profile
    )
    contract = json.loads(inputs.source_contract_bytes)
    operations_locator = contract["sourceOperations"]["locator"]
    operation_file = next(
        value for value in inputs.referenced_artifacts if value.locator == operations_locator
    )
    declarations = SourceOperationDeclarations.model_validate_json(operation_file.artifact_bytes)
    compiled = ProductionCandidateTargetCompiler().compile(inputs)
    bindings = {value.locator: value for value in compiled.captured_source_files}
    assert (
        bindings[operations_locator].content_sha256
        == hashlib.sha256(operation_file.artifact_bytes).hexdigest()
    )
    assert compiled.source_contract_locator == locator
    assert len(compiled.changed_file_dispositions) == len(bundle.operation_seed_artifact.bindings)
    assert {value.binding_sha256 for value in compiled.changed_file_dispositions} == {
        value.binding_sha256 for value in bundle.operation_seed_artifact.bindings
    }
    graph_ids = {value.node_id for value in bundle.graph_production_artifact.candidate.nodes}
    for declaration in (
        *declarations.standard_rest,
        *declarations.custom_rest,
        *declarations.synthetic_datasets,
        *declarations.browser_intents,
    ):
        assert declaration.source_node_id in graph_ids
    assert len(compiled.source_operation_profile.synthetic_datasets) == len(
        declarations.synthetic_datasets
    )
    for dataset in compiled.source_operation_profile.synthetic_datasets:
        source_dataset = next(
            value
            for value in declarations.synthetic_datasets
            if value.dataset_id == dataset.dataset_id
        )
        assert dataset.maximum_records == source_dataset.exact_cardinality
        assert dataset.predicates == source_dataset.predicates
    browser = compiled.source_operation_profile.browser_intents[0]
    assert browser.locator_rebind == declarations.browser_intents[0].locator_rebind
    rebind = browser.locator_rebind
    assert rebind is not None
    assert rebind.metadata_drift.member == "Strategic_Deal_Record_Page"
    assert rebind.metadata_drift.component_identifier == "dealWorkbench"
    assert rebind.metadata_drift.property_name == "locatorVariant"
    assert rebind.metadata_drift.operation == "SET_SCALAR"
    assert rebind.metadata_drift.baseline_value == "baseline"
    assert tuple(value.model_dump() for value in rebind.metadata_drift.allowed_pre_states) == (
        {"state": "ABSENT"},
        {"state": "PRESENT", "value": "baseline"},
    )
    assert rebind.metadata_drift.alternate_value == "reordered"
    assert rebind.metadata_drift.restoration == "PREIMAGE_EXACT"
    assert rebind.navigation.marker_value == "SDA-DEMO-BASELINE-15-v1|discount-exact"
    assert tuple(
        value.semantic_identity.field_api_name
        for value in rebind.obligations
        if value.semantic_identity.kind == "FIELD"
    ) == (
        "AccountId",
        "Amount",
        "CloseDate",
        "Discount__c",
        "Name",
        "Regional_VP_Approver__c",
        "StageName",
        "Strategic_Deal__c",
    )
    assert len(rebind.obligations) == 9
    for obligation in rebind.obligations:
        assert obligation.original_locator.attribute == "data-testid"
        if obligation.semantic_identity.kind == "FIELD":
            assert obligation.original_locator.value == (
                f"deal-baseline-{obligation.semantic_identity.field_api_name}"
            )
    assert rebind.obligations[-1].semantic_identity.action == "save-evaluate"
    assert rebind.obligations[-1].original_locator.value == "save-evaluate-v1"
    assert rebind.data_mutation == "FORBIDDEN"
    assert not compiled.authorizes_execution
    assert compiled.authority_scope == "TARGET_DERIVATION_ONLY"
