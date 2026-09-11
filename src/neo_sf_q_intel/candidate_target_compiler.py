from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle
from neo_sf_q_intel.candidate_live_targets import (
    OperationScopedEntity,
    _derive_operation_entities,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import ExecutionToolVersion
from neo_sf_q_intel.graph_adapter import SemanticFileStatus
from neo_sf_q_intel.live_receipt_ledger import LiveReceiptLedger
from neo_sf_q_intel.live_receipts import EvidencePhase, ReceiptScope, TrustedIssuerRegistry
from neo_sf_q_intel.live_target_plan import (
    ApexTestTarget,
    BrowserLocatorRebind,
    BrowserTarget,
    DatasetPredicate,
    DatasetTarget,
    HostOwnedLiveTargetPlanProducer,
    LiveTargetCapture,
    LiveTargetGap,
    LiveTargetGapCode,
    LiveTargetPlan,
    LiveTargetPlanEvaluation,
    LiveTargetPolicy,
    MetadataTarget,
    ResponseField,
    ResponseParentBinding,
    RestTarget,
    RestVariable,
    SemanticScopeEntity,
    SourceOperationProfile,
    TargetPartition,
    VerifiedLiveTargetScope,
    derive_target_sha256,
    validate_browser_dataset,
)
from neo_sf_q_intel.local_validation import (
    LocalValidationArtifactStore,
    LocalValidationEvidence,
    LocalValidationRunnerPins,
    VerifiedLocalValidation,
    verify_local_validation_evidence,
)
from neo_sf_q_intel.local_validation_phase import (
    ALL_EVIDENCE_PHASES,
    CANDIDATE_REQUIRED_PHASES,
    READ_ONLY_BASELINE_GATES,
    DeferredLocalValidation,
    HostLocalValidationPhasePolicy,
    classify_local_validation_phase,
)
from neo_sf_q_intel.ontology import SourceGraphProfile
from neo_sf_q_intel.operation_seed import SideFileEvidence
from neo_sf_q_intel.safety import require_safe_repository_locator
from neo_sf_q_intel.salesforce_graph_adapter import SalesforceGraphAdapterError, _xml

_MAX_CONTRACT_BYTES = 2 * 1024 * 1024
_MAX_REFERENCED_ARTIFACT_BYTES = 8 * 1024 * 1024
_MAX_TARGETS = 256
_SAFE_API_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")


class CandidateTargetCompilerError(RuntimeError):
    """Host-owned candidate inputs cannot be compiled into bounded target obligations."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TargetDerivationState(StrEnum):
    DERIVED = "DERIVED"
    UNSUPPORTED = "UNSUPPORTED"


class TargetDerivationReason(StrEnum):
    GRAPH_METADATA_IDENTITY = "GRAPH_METADATA_IDENTITY"
    GRAPH_CONNECTED_APEX_TEST = "GRAPH_CONNECTED_APEX_TEST"
    CONTRACT_STANDARD_READ_ROUTE = "CONTRACT_STANDARD_READ_ROUTE"
    CONTRACT_CUSTOM_READ_ROUTE = "CONTRACT_CUSTOM_READ_ROUTE"
    CONTRACT_LOCATOR_HEALING_SUITE = "CONTRACT_LOCATOR_HEALING_SUITE"
    CONTRACT_SYNTHETIC_DATASET = "CONTRACT_SYNTHETIC_DATASET"
    CAPABILITY_NOT_IMPLEMENTED = "CAPABILITY_NOT_IMPLEMENTED"
    NO_GRAPH_CONNECTED_ENTITY = "NO_GRAPH_CONNECTED_ENTITY"
    NON_APEX_TEST_OBLIGATION = "NON_APEX_TEST_OBLIGATION"
    SOURCE_SCHEMA_INSUFFICIENT = "SOURCE_SCHEMA_INSUFFICIENT"
    UNSUPPORTED_METADATA_FAMILY = "UNSUPPORTED_METADATA_FAMILY"
    EXACT_APEX_METHODS_UNRESOLVED = "EXACT_APEX_METHODS_UNRESOLVED"


class SourceOperationsReference(_Model):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    locator: str = Field(min_length=1, max_length=500)


class SourceRestDeclaration(_Model):
    source_node_id: str = Field(alias="sourceNodeId", min_length=1)
    dataset_id: str = Field(alias="datasetId", min_length=1)
    method: Literal["GET"]
    route_template: str = Field(alias="routeTemplate", min_length=1, max_length=1000)
    variables: tuple[RestVariable, ...]
    response_fields: tuple[ResponseField, ...] = Field(alias="responseFields", min_length=1)
    minimum_cardinality: int = Field(alias="minimumCardinality", ge=1, le=4096)
    maximum_cardinality: int = Field(alias="maximumCardinality", ge=1, le=4096)
    maximum_response_bytes: int = Field(alias="maximumResponseBytes", ge=1, le=1048576)
    request_expansion: Literal["EACH_DATASET_RECORD"] = Field(alias="requestExpansion")
    response_record_path: str = Field(alias="responseRecordPath", max_length=300)
    dataset_field_paths: dict[str, str] = Field(alias="datasetFieldPaths", min_length=1)
    parent_bindings: tuple[ResponseParentBinding, ...] = Field(alias="parentBindings")

    @model_validator(mode="after")
    def validate_projection(self) -> SourceRestDeclaration:
        if not any(item.required for item in self.response_fields):
            raise ValueError("Source REST projection needs at least one required field")
        for variable in self.variables:
            expected = "STRING" if variable.value_source == "API_VERSION" else "ID"
            if variable.data_type != expected:
                raise ValueError("Source REST variable type is unsupported")
        RestTarget(
            sourceEntityIds=(self.source_node_id,),
            **self.model_dump(by_alias=True, exclude={"source_node_id"}),
        )
        return self


class SourceDatasetDeclaration(_Model):
    dataset_id: str = Field(alias="datasetId", min_length=1)
    source_node_id: str = Field(alias="sourceNodeId", min_length=1)
    object_api_name: str = Field(alias="objectApiName", pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    field_projection: tuple[str, ...] = Field(alias="fieldProjection", min_length=1)
    predicates: tuple[DatasetPredicate, ...] = Field(min_length=1)
    ownership_marker_field: str = Field(alias="ownershipMarkerField", min_length=1)
    exact_cardinality: int = Field(alias="exactCardinality", ge=1, le=4096)
    identity_field: str = Field(alias="identityField", min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_ownership(self) -> SourceDatasetDeclaration:
        if (
            self.ownership_marker_field not in self.field_projection
            or self.ownership_marker_field not in {item.field for item in self.predicates}
            or any(not _SAFE_API_NAME.fullmatch(value) for value in self.field_projection)
        ):
            raise ValueError("Source dataset must declare an exact projected ownership predicate")
        DatasetTarget(
            sourceEntityIds=(self.source_node_id,),
            maximumRecords=self.exact_cardinality,
            **self.model_dump(by_alias=True, exclude={"source_node_id", "exact_cardinality"}),
        )
        return self


class SourceBrowserDeclaration(_Model):
    source_node_id: str = Field(alias="sourceNodeId", min_length=1)
    application_intent: str = Field(alias="applicationIntent", min_length=1)
    action_class: Literal[
        "EXPAND_READ_ONLY",
        "FOCUS",
        "LOCATOR_REBIND_PREVIEW",
        "NAVIGATE",
        "RELOAD",
        "SCROLL",
        "SELECT_READ_ONLY_VIEW",
    ] = Field(alias="actionClass")
    surface_intent: str = Field(alias="surfaceIntent", min_length=1)
    locator_rebind: BrowserLocatorRebind | None = Field(
        default=None, alias="locatorRebind", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_browser_declaration(self) -> SourceBrowserDeclaration:
        BrowserTarget(
            sourceEntityIds=(self.source_node_id,),
            **self.model_dump(by_alias=True, exclude={"source_node_id"}),
        )
        return self


class SourceLocalTestObligation(_Model):
    obligation_id: str = Field(alias="obligationId", min_length=1, max_length=200)
    runner_kind: Literal["NODE_TEST", "NODE_GENERATED_CHECK"] = Field(alias="runnerKind")
    working_directory: str = Field(alias="workingDirectory", min_length=1, max_length=500)
    test_locators: tuple[str, ...] = Field(alias="testLocators", min_length=1, max_length=64)
    maximum_seconds: int = Field(alias="maximumSeconds", ge=1, le=900)
    regenerated_locators: tuple[str, ...] = Field(alias="regeneratedLocators", max_length=64)
    required_result: Literal["COMPLETE_PASS_NO_SKIP"] = Field(alias="requiredResult")
    required_evidence_phases: tuple[EvidencePhase, ...] = Field(
        default=ALL_EVIDENCE_PHASES, alias="requiredEvidencePhases", min_length=3, max_length=4
    )

    @model_validator(mode="after")
    def validate_local_obligation(self) -> SourceLocalTestObligation:
        if self.required_evidence_phases not in (ALL_EVIDENCE_PHASES, CANDIDATE_REQUIRED_PHASES):
            raise ValueError("Local obligations may request baseline deferral only")
        for path in (self.working_directory, *self.test_locators, *self.regenerated_locators):
            require_safe_repository_locator(path)
        for paths in (self.test_locators, self.regenerated_locators):
            if paths != tuple(sorted(set(paths))):
                raise ValueError("Local obligation locators must be sorted and unique")
        if any(
            not path.startswith(self.working_directory + "/")
            or not path.endswith((".js", ".mjs", ".cjs"))
            for path in self.test_locators
        ):
            raise ValueError("Local Node test must be an exact script inside its working directory")
        if self.runner_kind == "NODE_GENERATED_CHECK":
            if len(self.test_locators) != 1 or not self.regenerated_locators:
                raise ValueError("Regeneration check must bind one generator and its exact outputs")
        elif self.regenerated_locators:
            raise ValueError("Ordinary test runner cannot attest generated outputs")
        return self


class SourceNonRuntimeFile(_Model):
    locator: str = Field(min_length=1, max_length=500)
    category: Literal[
        "DOCUMENTATION", "GENERATED_INDEX", "LOCAL_CATALOG_TOOL", "LOCAL_TEST_SPECIFICATION"
    ]
    local_obligation_ids: tuple[str, ...] = Field(
        alias="localObligationIds", min_length=1, max_length=64
    )

    @model_validator(mode="after")
    def validate_category(self) -> SourceNonRuntimeFile:
        require_safe_repository_locator(self.locator)
        allowed = {
            "DOCUMENTATION": (".md",),
            "GENERATED_INDEX": (".json",),
            "LOCAL_CATALOG_TOOL": (".js", ".mjs", ".cjs"),
            "LOCAL_TEST_SPECIFICATION": (".json",),
        }
        if not self.locator.endswith(allowed[self.category]):
            raise ValueError("Nonruntime category does not match supported path suffix")
        if self.local_obligation_ids != tuple(sorted(set(self.local_obligation_ids))):
            raise ValueError("Local obligation references must be sorted and unique")
        return self


class SourceOperationDeclarations(_Model):
    """Closed source-owned facts; graph binding and host policy supply no missing facts."""

    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    standard_rest: tuple[SourceRestDeclaration, ...] = Field(alias="standardRest")
    custom_rest: tuple[SourceRestDeclaration, ...] = Field(alias="customRest")
    synthetic_datasets: tuple[SourceDatasetDeclaration, ...] = Field(alias="syntheticDatasets")
    browser_intents: tuple[SourceBrowserDeclaration, ...] = Field(alias="browserIntents")
    non_runtime_files: tuple[SourceNonRuntimeFile, ...] = Field(alias="nonRuntimeFiles")
    local_test_obligations: tuple[SourceLocalTestObligation, ...] = Field(
        alias="localTestObligations"
    )

    @model_validator(mode="after")
    def validate_declarations(self) -> SourceOperationDeclarations:
        datasets = {item.dataset_id: item for item in self.synthetic_datasets}
        if len(datasets) != len(self.synthetic_datasets):
            raise ValueError("Source dataset IDs must be unique")
        for browser in self.browser_intents:
            if browser.locator_rebind is not None:
                validate_browser_dataset(browser.locator_rebind, datasets)
        local = {item.obligation_id: item for item in self.local_test_obligations}
        if len(local) != len(self.local_test_obligations) or len(
            {item.locator for item in self.non_runtime_files}
        ) != len(self.non_runtime_files):
            raise ValueError("Nonruntime paths and local obligations must be unique")
        used: set[str] = set()
        for file in self.non_runtime_files:
            used.update(file.local_obligation_ids)
            if not set(file.local_obligation_ids).issubset(local):
                raise ValueError("Nonruntime file cites an unknown local obligation")
            if file.category == "GENERATED_INDEX" and not any(
                local[key].runner_kind == "NODE_GENERATED_CHECK"
                and file.locator in local[key].regenerated_locators
                for key in file.local_obligation_ids
            ):
                raise ValueError(
                    "Generated index must require exact deterministic regeneration check"
                )
        if used != set(local):
            raise ValueError("Every local obligation must bind a declared nonruntime file")
        for item in (*self.standard_rest, *self.custom_rest):
            if item.dataset_id not in datasets:
                raise ValueError("REST declaration requires an explicit dataset")
            if item.minimum_cardinality > item.maximum_cardinality:
                raise ValueError("REST cardinality is invalid")
            if not any(v.value_source == "SYNTHETIC_DATASET_RECEIPT" for v in item.variables):
                raise ValueError("REST declaration must restrict a synthetic record")
            dataset = datasets[item.dataset_id]
            if (
                dataset.identity_field not in item.dataset_field_paths
                or not set(item.dataset_field_paths).issubset(dataset.field_projection)
                or any(
                    v.dataset_field != dataset.identity_field
                    for v in item.variables
                    if v.value_source == "SYNTHETIC_DATASET_RECEIPT"
                )
                or any(
                    binding.dataset_field != dataset.identity_field
                    for binding in item.parent_bindings
                )
            ):
                raise ValueError(
                    "REST declaration must bind the declared dataset identity and projection"
                )
        return self


class CapturedSourceArtifact(_Model):
    locator: str = Field(min_length=1, max_length=500)
    artifact_bytes: bytes = Field(
        alias="artifactBytes", min_length=2, max_length=_MAX_REFERENCED_ARTIFACT_BYTES
    )
    artifact_sha256: str = Field(alias="artifactSha256", pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> CapturedSourceArtifact:
        require_safe_repository_locator(self.locator)
        if hashlib.sha256(self.artifact_bytes).hexdigest() != self.artifact_sha256:
            raise ValueError("Captured source artifact digest mismatch")
        return self


class CandidateSourceFileBinding(_Model):
    locator: str = Field(min_length=1, max_length=4096)
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CandidateChangedFileDisposition(_Model):
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str
    disposition: Literal[
        "SALESFORCE_RUNTIME",
        "CONSUMED_SOURCE_CONTRACT",
        "NON_SALESFORCE_RUNTIME",
        "UNKNOWN_BLOCKING",
    ]
    category: str | None
    side_file_bindings: tuple[SideFileEvidence, ...] = Field(min_length=1, max_length=2)
    local_obligation_ids: tuple[str, ...]


class RequiredLocalValidation(_Model):
    obligation: SourceLocalTestObligation
    bound_files: tuple[CandidateSourceFileBinding, ...] = Field(min_length=1)
    candidate_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    command_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_requirement: Literal["HOST_ATTESTED_COMPLETE_CURRENT_CANDIDATE_RESULT"] = (
        "HOST_ATTESTED_COMPLETE_CURRENT_CANDIDATE_RESULT"
    )
    satisfied: Literal[False] = False


class CandidateTargetCompilerInputs(_Model):
    candidate_bundle: CandidateAssuranceBundle = Field(alias="candidateBundle")
    source_contract_bytes: bytes = Field(
        alias="sourceContractBytes", min_length=2, max_length=_MAX_CONTRACT_BYTES
    )
    source_contract_sha256: str = Field(alias="sourceContractSha256", pattern=r"^[a-f0-9]{64}$")
    source_contract_locator: str = Field(
        alias="sourceContractLocator", min_length=1, max_length=500
    )
    source_profile: SourceGraphProfile = Field(alias="sourceProfile")
    referenced_artifacts: tuple[CapturedSourceArtifact, ...] = Field(alias="referencedArtifacts")

    @model_validator(mode="after")
    def validate_inputs(self) -> CandidateTargetCompilerInputs:
        if hashlib.sha256(self.source_contract_bytes).hexdigest() != self.source_contract_sha256:
            raise ValueError("Source contract digest mismatch")
        locators = tuple(item.locator for item in self.referenced_artifacts)
        if locators != tuple(sorted(set(locators))):
            raise ValueError("Referenced source artifacts must be sorted and unique")
        _verify_candidate_file(
            self.candidate_bundle, self.source_contract_locator, self.source_contract_bytes
        )
        for item in self.referenced_artifacts:
            _verify_candidate_file(self.candidate_bundle, item.locator, item.artifact_bytes)
        if sum(len(item.artifact_bytes) for item in self.referenced_artifacts) > 32 * 1024 * 1024:
            raise ValueError("Aggregate captured source capacity exceeded")
        return self


class TargetDerivationRecord(_Model):
    state: TargetDerivationState
    partition: TargetPartition
    reason: TargetDerivationReason
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds")
    source_artifact_sha256s: tuple[str, ...] = Field(alias="sourceArtifactSha256s")
    target_sha256: str | None = Field(default=None, alias="targetSha256", pattern=r"^[a-f0-9]{64}$")
    obligation_id: str | None = Field(default=None, alias="obligationId")

    @model_validator(mode="after")
    def validate_record(self) -> TargetDerivationRecord:
        for values in (self.source_entity_ids, self.source_artifact_sha256s):
            if values != tuple(sorted(set(values))):
                raise ValueError("Derivation bindings must be sorted and unique")
        if (self.state is TargetDerivationState.DERIVED) is not (self.target_sha256 is not None):
            raise ValueError("Derived target status and digest differ")
        return self


class CandidateTargetCompilation(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["TARGET_DERIVATION_ONLY"] = "TARGET_DERIVATION_ONLY"
    authorizes_execution: Literal[False] = False
    candidate_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_contract_locator: str
    captured_source_files: tuple[CandidateSourceFileBinding, ...] = Field(min_length=2)
    changed_file_dispositions: tuple[CandidateChangedFileDisposition, ...]
    required_local_validations: tuple[RequiredLocalValidation, ...]
    referenced_artifact_sha256s: tuple[str, ...]
    operation_entities: tuple[OperationScopedEntity, ...] = Field(min_length=1)
    verified_scope: VerifiedLiveTargetScope
    source_operation_profile: SourceOperationProfile
    source_operation_profile_bytes: bytes
    derivations: tuple[TargetDerivationRecord, ...] = Field(min_length=1)
    blocking_reason_codes: tuple[str, ...]
    compilation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_compilation(self) -> CandidateTargetCompilation:
        changed = tuple(value.binding_sha256 for value in self.changed_file_dispositions)
        if changed != tuple(sorted(set(changed))):
            raise ValueError("Changed file dispositions must be complete, sorted and unique")
        locators = tuple(item.locator for item in self.captured_source_files)
        if locators != tuple(sorted(set(locators))):
            raise ValueError("Captured source file bindings must be sorted and unique")
        contracts = [
            item
            for item in self.captured_source_files
            if item.locator == self.source_contract_locator
        ]
        if len(contracts) != 1 or contracts[0].content_sha256 != self.source_contract_sha256:
            raise ValueError("Compiled source contract locator/digest binding is inconsistent")
        canonical = _canonical_bytes(
            self.source_operation_profile.model_dump(by_alias=True, mode="json")
        )
        if canonical != self.source_operation_profile_bytes:
            raise ValueError("Source-operation profile bytes are not canonical")
        if self.source_operation_profile.scope_artifact_sha256 != (
            self.verified_scope.artifact_sha256
        ):
            raise ValueError("Source-operation profile is not scope-bound")
        keys = tuple(
            (
                item.partition.value,
                item.state.value,
                item.reason.value,
                item.target_sha256 or "",
                item.source_entity_ids,
                item.obligation_id or "",
            )
            for item in self.derivations
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Target derivations must be sorted and unique")
        _verify_digest(self, "compilation_sha256")
        return self


class HostOrganizationClassification(_Model):
    receipt_sha256: str = Field(alias="receiptSha256", pattern=r"^[a-f0-9]{64}$")
    environment_class: Literal["DEVELOPER_EDITION", "SANDBOX", "SCRATCH_ORG"] = Field(
        alias="environmentClass"
    )
    valid_until: str = Field(alias="validUntil")
    execution_scope: ReceiptScope = Field(alias="executionScope")

    @model_validator(mode="after")
    def validate_classification(self) -> HostOrganizationClassification:
        _parse_time(self.valid_until)
        return self


class ExecutionSupportRequirement(_Model):
    receipt_role: Literal[
        "SF-L01",
        "SF-L02",
        "LIVE_TARGET_PLAN_RECEIPT",
        "LIVE_DATASET_SCOPE_RECEIPT",
        "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        "LOCAL_SOURCE_VALIDATION_RECEIPT",
    ]
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: Literal[
        "HOST_RECEIPT_REQUIRED", "RUNTIME_RESOLUTION_REQUIRED", "NOT_REQUIRED_FOR_PHASE_UNRESOLVED"
    ]


class ExpectedAssertionPredicate(StrEnum):
    REST_CLOSED_PROJECTION = "REST_CLOSED_PROJECTION"
    METADATA_EXACT_MEMBER_MANIFEST = "METADATA_EXACT_MEMBER_MANIFEST"
    APEX_TEST_EXACT_OUTCOME = "APEX_TEST_EXACT_OUTCOME"
    BROWSER_INTENT_EXACT_SCOPE = "BROWSER_INTENT_EXACT_SCOPE"
    DATASET_EXACT_MEMBERSHIP = "DATASET_EXACT_MEMBERSHIP"


class ExpectedRunnerProvenance(_Model):
    producer_id: str = Field(alias="producerId", min_length=1, max_length=200)
    runner_id: str = Field(alias="runnerId", min_length=1, max_length=200)
    runner_key_id: str = Field(alias="runnerKeyId", min_length=1, max_length=200)
    runner_version: str = Field(alias="runnerVersion", min_length=1, max_length=100)
    adapter_version: str = Field(alias="adapterVersion", min_length=1, max_length=100)
    tool_versions: tuple[ExecutionToolVersion, ...] = Field(
        alias="toolVersions", min_length=1, max_length=16
    )

    @model_validator(mode="after")
    def validate_provenance(self) -> ExpectedRunnerProvenance:
        ids = tuple(item.tool_id for item in self.tool_versions)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Expected tool versions must be sorted and unique")
        return self


class ExpectedTargetAssertion(_Model):
    assertion_id: str = Field(alias="assertionId", min_length=1, max_length=256)
    gate_id: Literal["SF-L03", "SF-L04", "SF-L05", "SF-L07", "SF-L08"] = Field(alias="gateId")
    partition: TargetPartition
    target_sha256: str = Field(alias="targetSha256", pattern=r"^[a-f0-9]{64}$")
    predicate: ExpectedAssertionPredicate
    expected_sha256: str = Field(alias="expectedSha256", pattern=r"^[a-f0-9]{64}$")
    minimum_cardinality: int = Field(alias="minimumCardinality", ge=0, le=4096)
    maximum_cardinality: int = Field(alias="maximumCardinality", ge=1, le=4096)
    invocation_count: int = Field(alias="invocationCount", ge=1, le=4096)
    request_expansion: Literal["EACH_DATASET_RECORD"] | None = Field(alias="requestExpansion")
    per_response_minimum_cardinality: int | None = Field(
        alias="perResponseMinimumCardinality", ge=1, le=4096
    )
    per_response_maximum_cardinality: int | None = Field(
        alias="perResponseMaximumCardinality", ge=1, le=4096
    )
    exact_projection: tuple[str, ...] = Field(alias="exactProjection")
    metadata_type: str | None = Field(default=None, alias="metadataType")
    metadata_member: str | None = Field(default=None, alias="metadataMember")
    compatible_dataset_target_sha256s: tuple[str, ...] = Field(
        default=(), alias="compatibleDatasetTargetSha256s"
    )
    resolution_contract_sha256: str | None = Field(
        default=None,
        alias="resolutionContractSha256",
        pattern=r"^[a-f0-9]{64}$",
    )

    @model_validator(mode="after")
    def validate_assertion(self) -> ExpectedTargetAssertion:
        if self.minimum_cardinality > self.maximum_cardinality:
            raise ValueError("Expected assertion cardinality is invalid")
        for values in (
            self.exact_projection,
            self.compatible_dataset_target_sha256s,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("Expected assertion bindings must be sorted and unique")
        metadata = self.partition is TargetPartition.METADATA
        if metadata is not (self.metadata_type is not None and self.metadata_member is not None):
            raise ValueError("Metadata assertion must bind exact type and member")
        rest = self.partition in {
            TargetPartition.STANDARD_REST,
            TargetPartition.CUSTOM_REST,
        }
        if rest is not (self.resolution_contract_sha256 is not None):
            raise ValueError("REST assertion must bind exact variable resolution contract")
        if rest:
            if (
                self.request_expansion != "EACH_DATASET_RECORD"
                or self.per_response_minimum_cardinality is None
                or self.per_response_maximum_cardinality is None
                or self.minimum_cardinality
                != self.invocation_count * self.per_response_minimum_cardinality
                or self.maximum_cardinality
                != self.invocation_count * self.per_response_maximum_cardinality
            ):
                raise ValueError(
                    "REST assertion must account for every declared dataset invocation"
                )
        elif (
            self.request_expansion is not None
            or self.invocation_count != 1
            or self.per_response_minimum_cardinality is not None
            or self.per_response_maximum_cardinality is not None
        ):
            raise ValueError("Non-REST assertion cannot declare REST expansion")
        return self


class ExpectedDatasetContract(_Model):
    dataset_target_sha256: str = Field(alias="datasetTargetSha256", pattern=r"^[a-f0-9]{64}$")
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    object_api_name: str = Field(alias="objectApiName", min_length=1, max_length=200)
    field_projection: tuple[str, ...] = Field(alias="fieldProjection", min_length=1)
    ownership_marker_field: str = Field(alias="ownershipMarkerField", min_length=1)
    predicate_sha256: str = Field(alias="predicateSha256", pattern=r"^[a-f0-9]{64}$")
    exact_cardinality: int = Field(alias="exactCardinality", ge=1, le=4096)
    identity_field: str = Field(alias="identityField", min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_dataset(self) -> ExpectedDatasetContract:
        for values in (self.source_entity_ids, self.field_projection):
            if values != tuple(sorted(set(values))):
                raise ValueError("Expected dataset bindings must be sorted and unique")
        if (
            self.ownership_marker_field not in self.field_projection
            or self.identity_field not in self.field_projection
        ):
            raise ValueError("Dataset ownership marker must be projected")
        return self


class ExpectedExecutionContract(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["EXECUTION_EXPECTATION_ONLY"] = "EXECUTION_EXPECTATION_ONLY"
    authorizes_execution: Literal[False] = False
    execution_id: str = Field(min_length=1, max_length=200)
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    compilation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: ReceiptScope
    evidence_phase: Literal[EvidencePhase.LIVE_BASELINE] = EvidencePhase.LIVE_BASELINE
    gate_ids: tuple[str, ...] = Field(min_length=1)
    runner_provenance: ExpectedRunnerProvenance
    assertions: tuple[ExpectedTargetAssertion, ...] = Field(min_length=1, max_length=256)
    datasets: tuple[ExpectedDatasetContract, ...]
    verified_local_validations: tuple[VerifiedLocalValidation, ...] = ()
    local_validation_phase_policy_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    deferred_local_validations: tuple[DeferredLocalValidation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    phase_read_only_gate_ids: tuple[str, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    valid_until: str
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_contract(self) -> ExpectedExecutionContract:
        if (self.local_validation_phase_policy_sha256 is not None) != bool(
            self.phase_read_only_gate_ids
        ) or (
            self.phase_read_only_gate_ids
            and self.phase_read_only_gate_ids != READ_ONLY_BASELINE_GATES
        ):
            raise ValueError("Phase-scoped execution permits only fixed read-only baseline gates")
        deferred_ids = tuple(value.obligation_id for value in self.deferred_local_validations)
        if deferred_ids != tuple(sorted(set(deferred_ids))) or any(
            value.phase_policy_sha256 != self.local_validation_phase_policy_sha256
            or value.source_contract_sha256 != self.scope.source_contract_sha256
            or value.not_required_for_phase is not self.evidence_phase
            for value in self.deferred_local_validations
        ):
            raise ValueError("Deferred local evidence differs from exact phase/source policy")
        if set(deferred_ids) & {value.obligation_id for value in self.verified_local_validations}:
            raise ValueError("A local obligation cannot be both verified and deferred")
        assertion_ids = tuple(item.assertion_id for item in self.assertions)
        if assertion_ids != tuple(sorted(set(assertion_ids))):
            raise ValueError("Expected assertion IDs must be sorted and unique")
        expected_gates = tuple(sorted({item.gate_id for item in self.assertions}))
        if self.gate_ids != expected_gates:
            raise ValueError("Expected execution gates differ from assertions")
        dataset_ids = tuple(item.dataset_target_sha256 for item in self.datasets)
        if dataset_ids != tuple(sorted(set(dataset_ids))):
            raise ValueError("Expected datasets must be sorted and unique")
        known_datasets = set(dataset_ids)
        if any(
            not set(item.compatible_dataset_target_sha256s).issubset(known_datasets)
            for item in self.assertions
        ):
            raise ValueError("Assertion cites an unknown dataset contract")
        local_ids = tuple(value.obligation_id for value in self.verified_local_validations)
        if local_ids != tuple(sorted(set(local_ids))) or len(
            {value.receipt_id for value in self.verified_local_validations}
        ) != len(local_ids):
            raise ValueError("Expected local evidence must be sorted and unique")
        if any(
            _parse_time(self.valid_until) > value.expires_at
            for value in self.verified_local_validations
        ):
            raise ValueError("Execution expectations cannot outlive required local evidence")
        _parse_time(self.valid_until)
        _verify_digest(self, "contract_sha256")
        return self


class CandidateLivePlanComposition(_Model):
    compilation: CandidateTargetCompilation
    evaluation: LiveTargetPlanEvaluation
    expected_execution_contract: ExpectedExecutionContract | None
    expected_execution_contract_bytes: bytes | None
    expected_execution_contract_bytes_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    support_requirements: tuple[ExecutionSupportRequirement, ...]
    verified_local_validations: tuple[VerifiedLocalValidation, ...]
    local_validation_phase_policy_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    deferred_local_validations: tuple[DeferredLocalValidation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    composition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_composition(self) -> CandidateLivePlanComposition:
        values = (
            self.expected_execution_contract,
            self.expected_execution_contract_bytes,
            self.expected_execution_contract_bytes_sha256,
        )
        if any(item is not None for item in values) and not all(
            item is not None for item in values
        ):
            raise ValueError("Expected execution contract representation is incomplete")
        if self.expected_execution_contract is not None:
            assert self.expected_execution_contract_bytes is not None
            assert self.expected_execution_contract_bytes_sha256 is not None
            expected = _canonical_bytes(self.expected_execution_contract.model_dump(mode="json"))
            if (
                self.expected_execution_contract_bytes != expected
                or hashlib.sha256(expected).hexdigest()
                != self.expected_execution_contract_bytes_sha256
                or self.expected_execution_contract.verified_local_validations
                != self.verified_local_validations
                or self.expected_execution_contract.deferred_local_validations
                != self.deferred_local_validations
                or self.expected_execution_contract.local_validation_phase_policy_sha256
                != self.local_validation_phase_policy_sha256
                or self.expected_execution_contract.compilation_sha256
                != self.compilation.compilation_sha256
                or self.expected_execution_contract.candidate_bundle_sha256
                != self.compilation.candidate_bundle_sha256
            ):
                raise ValueError("Expected execution contract bytes differ from model")
        expected_local = {
            value.obligation.obligation_id: value.command_contract_sha256
            for value in self.compilation.required_local_validations
        }
        if any(
            expected_local.get(value.obligation_id) != value.command_contract_sha256
            for value in (*self.verified_local_validations, *self.deferred_local_validations)
        ):
            raise ValueError("Verified local evidence differs from compiler obligations")
        if self.evaluation.plan is not None and (
            self.evaluation.plan.deferred_local_validations != self.deferred_local_validations
            or self.evaluation.plan.local_validation_phase_policy_sha256
            != self.local_validation_phase_policy_sha256
        ):
            raise ValueError("Plan differs from exact unresolved local obligations")
        if self.evaluation.plan is not None:
            accounted = [
                value.obligation_id
                for value in (*self.verified_local_validations, *self.deferred_local_validations)
            ]
            if len(accounted) != len(set(accounted)) or set(accounted) != set(expected_local):
                raise ValueError(
                    "Ready composition must account for every independent local obligation"
                )
            if (
                self.expected_execution_contract is None
                or self.expected_execution_contract.plan_sha256 != self.evaluation.plan.plan_sha256
            ):
                raise ValueError("Ready composition requires exact current execution expectations")
        _verify_digest(self, "composition_sha256")
        return self


@dataclass(frozen=True, slots=True)
class HostOwnedSourceContractPort:
    source_root: Path
    contract_locator: str = "contracts/agent-interface.json"

    def capture(
        self,
        bundle: CandidateAssuranceBundle,
        source_profile: SourceGraphProfile,
    ) -> CandidateTargetCompilerInputs:
        root = self.source_root.absolute()
        if root.resolve(strict=True) != root:
            raise CandidateTargetCompilerError("Source root contains an alias or link")
        _require_current_bundle(bundle)
        contract_bytes = _read_candidate_file(
            root, self.contract_locator, bundle, _MAX_CONTRACT_BYTES
        )
        contract = _strict_object(contract_bytes, "source contract")
        operations_ref = _source_operations_reference(contract)
        declarations = _parse_source_declarations(
            _read_candidate_file(
                root, operations_ref.locator, bundle, _MAX_REFERENCED_ARTIFACT_BYTES
            )
        )
        locators = _referenced_locators(contract, bundle, declarations)
        if sum(_candidate_file(bundle, locator).size_bytes for locator in locators) > (
            32 * 1024 * 1024
        ):
            raise CandidateTargetCompilerError("Aggregate source capture capacity exceeded")
        artifacts = tuple(
            CapturedSourceArtifact(
                locator=locator,
                artifactBytes=(
                    content := _read_candidate_file(
                        root, locator, bundle, _MAX_REFERENCED_ARTIFACT_BYTES
                    )
                ),
                artifactSha256=hashlib.sha256(content).hexdigest(),
            )
            for locator in locators
        )
        # Recheck the entire admitted capture after the last read. A contract
        # changed while another referenced artifact was read cannot enter a plan.
        _read_candidate_file(root, self.contract_locator, bundle, _MAX_CONTRACT_BYTES)
        for locator in locators:
            _read_candidate_file(root, locator, bundle, _MAX_REFERENCED_ARTIFACT_BYTES)
        return CandidateTargetCompilerInputs(
            candidateBundle=bundle,
            sourceContractBytes=contract_bytes,
            sourceContractSha256=hashlib.sha256(contract_bytes).hexdigest(),
            sourceContractLocator=self.contract_locator,
            sourceProfile=source_profile,
            referencedArtifacts=artifacts,
        )


class ProductionCandidateTargetCompiler:
    """Derive bounded targets from host-captured product truth, never a supplied catalog."""

    def compile(self, captured: CandidateTargetCompilerInputs) -> CandidateTargetCompilation:
        inputs = CandidateTargetCompilerInputs.model_validate(captured.model_dump(mode="python"))
        bundle = CandidateAssuranceBundle.model_validate(
            inputs.candidate_bundle.model_dump(mode="json")
        )
        _require_current_bundle(bundle)
        contract = _strict_object(inputs.source_contract_bytes, "source contract")
        _verify_contract_identity(contract, bundle)
        if inputs.source_profile.sha256 != bundle.graph_production_artifact.source_profile_sha256:
            raise CandidateTargetCompilerError("Candidate and source profile differ")
        artifacts = {item.locator: item for item in inputs.referenced_artifacts}
        reference = _source_operations_reference(contract)
        if reference.locator not in artifacts:
            raise CandidateTargetCompilerError("Referenced source artifact capture is incomplete")
        declarations = _parse_source_declarations(artifacts[reference.locator].artifact_bytes)
        expected_locators = _referenced_locators(contract, bundle, declarations)
        if set(artifacts) != set(expected_locators):
            raise CandidateTargetCompilerError("Referenced source artifact capture is incomplete")

        dispositions, local_validations = _classify_changed_files(
            inputs, declarations, reference.locator
        )
        accounted = frozenset(
            value.binding_sha256
            for value in dispositions
            if value.disposition != "SALESFORCE_RUNTIME"
        )
        entities, operation_entities = _derive_operation_entities(
            bundle, inputs.source_profile, accounted_nonruntime_binding_sha256s=accounted
        )
        entities = tuple(sorted(entities, key=lambda item: (item.canonical_class, item.entity_id)))
        graph_nodes, adjacency = _candidate_graph(bundle)
        targets: dict[TargetPartition, list[_Model]] = defaultdict(list)
        records: list[TargetDerivationRecord] = []

        self._metadata_targets(entities, graph_nodes, inputs, targets, records)
        self._rest_targets(declarations, entities, graph_nodes, adjacency, inputs, targets, records)
        self._apex_targets(bundle, entities, graph_nodes, adjacency, inputs, targets, records)
        self._browser_and_dataset_targets(
            declarations, artifacts, entities, graph_nodes, adjacency, inputs, targets, records
        )

        ordered_groups = {
            partition: tuple(
                sorted(
                    values,
                    key=lambda item: stable_sha256(item.model_dump(by_alias=True, mode="json")),
                )
            )
            for partition, values in targets.items()
        }
        target_count = sum(len(values) for values in ordered_groups.values())
        if target_count > _MAX_TARGETS:
            raise CandidateTargetCompilerError("Candidate target capacity exceeded")
        apex = tuple(ordered_groups.get(TargetPartition.APEX_TEST, ()))
        obligation_ids = tuple(sorted(_obligation_id(item) for item in _selected_test_ids(bundle)))
        scope = _sealed(
            VerifiedLiveTargetScope,
            "artifact_sha256",
            schema_version="1.0.0",
            producer_kind="HOST_VERIFIED_CHANGE_GRAPH_SCOPE",
            complete=True,
            project_id=bundle.foundation.project_id,
            source_snapshot_sha256=bundle.operation_seed_artifact.source_snapshot_sha256,
            verified_change_sha256=bundle.verified_change_manifest_sha256,
            semantic_graph_sha256=bundle.graph_production_receipt_sha256,
            ontology_sha256=bundle.graph_production_artifact.ontology_sha256,
            source_profile_sha256=bundle.graph_production_artifact.source_profile_sha256,
            entities=entities,
            mandatory_obligation_ids=obligation_ids,
            valid_until=min(
                bundle.graph_production_artifact.valid_until,
                bundle.operation_seed_artifact.valid_until,
                key=_parse_time,
            ),
        )
        profile = SourceOperationProfile(
            schemaVersion="1.0.0",
            profileId=f"candidate-{inputs.source_contract_sha256[:16]}",
            profileVersion="1.0.0",
            scopeArtifactSha256=scope.artifact_sha256,
            standardRest=ordered_groups.get(TargetPartition.STANDARD_REST, ()),
            customRest=ordered_groups.get(TargetPartition.CUSTOM_REST, ()),
            metadata=ordered_groups.get(TargetPartition.METADATA, ()),
            apexTests=apex,
            browserIntents=ordered_groups.get(TargetPartition.BROWSER_INTENT, ()),
            syntheticDatasets=ordered_groups.get(TargetPartition.SYNTHETIC_DATASET, ()),
        )
        profile_bytes = _canonical_bytes(profile.model_dump(by_alias=True, mode="json"))
        ordered_records = tuple(
            sorted(
                records,
                key=lambda item: (
                    item.partition.value,
                    item.state.value,
                    item.reason.value,
                    item.target_sha256 or "",
                    item.source_entity_ids,
                    item.obligation_id or "",
                ),
            )
        )
        body = {
            "schema_version": "1.0.0",
            "authority_scope": "TARGET_DERIVATION_ONLY",
            "authorizes_execution": False,
            "candidate_bundle_sha256": bundle.bundle_sha256,
            "source_contract_sha256": inputs.source_contract_sha256,
            "source_contract_locator": inputs.source_contract_locator,
            "captured_source_files": tuple(
                sorted(
                    (
                        CandidateSourceFileBinding(
                            locator=inputs.source_contract_locator,
                            size_bytes=len(inputs.source_contract_bytes),
                            content_sha256=inputs.source_contract_sha256,
                        ),
                        *(
                            CandidateSourceFileBinding(
                                locator=item.locator,
                                size_bytes=len(item.artifact_bytes),
                                content_sha256=item.artifact_sha256,
                            )
                            for item in inputs.referenced_artifacts
                        ),
                    ),
                    key=lambda item: item.locator,
                )
            ),
            "changed_file_dispositions": dispositions,
            "required_local_validations": local_validations,
            "referenced_artifact_sha256s": tuple(
                item.artifact_sha256 for item in inputs.referenced_artifacts
            ),
            "operation_entities": operation_entities,
            "verified_scope": scope,
            "source_operation_profile": profile,
            "source_operation_profile_bytes": profile_bytes,
            "derivations": ordered_records,
            "blocking_reason_codes": tuple(
                sorted(
                    {
                        item.reason.value
                        for item in ordered_records
                        if item.state is TargetDerivationState.UNSUPPORTED
                    }
                    | ({"LOCAL_SOURCE_VALIDATION_REQUIRED"} if local_validations else set())
                    | (
                        {"UNKNOWN_CHANGED_FILE"}
                        if any(value.disposition == "UNKNOWN_BLOCKING" for value in dispositions)
                        else set()
                    )
                )
            ),
        }
        return _sealed(CandidateTargetCompilation, "compilation_sha256", **body)

    @staticmethod
    def _metadata_targets(
        entities: tuple[SemanticScopeEntity, ...],
        nodes: dict[str, Any],
        inputs: CandidateTargetCompilerInputs,
        targets: dict[TargetPartition, list[_Model]],
        records: list[TargetDerivationRecord],
    ) -> None:
        for entity in entities:
            node = nodes.get(entity.entity_id)
            mapping = _metadata_identity(node)
            if mapping is None:
                _unsupported(
                    records,
                    TargetPartition.METADATA,
                    TargetDerivationReason.UNSUPPORTED_METADATA_FAMILY,
                    (entity.entity_id,),
                    (inputs.source_contract_sha256,),
                )
                continue
            metadata_type, member = mapping
            target = MetadataTarget(
                sourceEntityIds=(entity.entity_id,),
                metadataType=metadata_type,
                member=member,
                maximumFiles=64,
                maximumBytes=4 * 1024 * 1024,
            )
            _derived(
                targets,
                records,
                TargetPartition.METADATA,
                TargetDerivationReason.GRAPH_METADATA_IDENTITY,
                target,
                (inputs.source_contract_sha256,),
            )

    @staticmethod
    def _rest_targets(
        declarations: SourceOperationDeclarations,
        entities: tuple[SemanticScopeEntity, ...],
        nodes: dict[str, Any],
        adjacency: dict[str, set[str]],
        inputs: CandidateTargetCompilerInputs,
        targets: dict[TargetPartition, list[_Model]],
        records: list[TargetDerivationRecord],
    ) -> None:
        scope_ids = tuple(item.entity_id for item in entities)
        datasets = {item.dataset_id: item for item in declarations.synthetic_datasets}
        for partition, values, reason in (
            (
                TargetPartition.STANDARD_REST,
                declarations.standard_rest,
                TargetDerivationReason.CONTRACT_STANDARD_READ_ROUTE,
            ),
            (
                TargetPartition.CUSTOM_REST,
                declarations.custom_rest,
                TargetDerivationReason.CONTRACT_CUSTOM_READ_ROUTE,
            ),
        ):
            if not values:
                _unsupported(
                    records,
                    partition,
                    TargetDerivationReason.SOURCE_SCHEMA_INSUFFICIENT,
                    scope_ids,
                    (inputs.source_contract_sha256,),
                )
            for item in values:
                bound = _connected_scope_ids(item.source_node_id, scope_ids, adjacency)
                dataset = datasets[item.dataset_id]
                dataset_node = nodes.get(dataset.source_node_id)
                if (
                    item.source_node_id not in nodes
                    or not bound
                    or dataset_node is None
                    or dataset_node.raw_kind != "object"
                    or dataset_node.label != dataset.object_api_name
                    or not set(bound).intersection(
                        _connected_scope_ids(dataset.source_node_id, scope_ids, adjacency)
                    )
                ):
                    _unsupported(
                        records,
                        partition,
                        TargetDerivationReason.NO_GRAPH_CONNECTED_ENTITY,
                        scope_ids,
                        (inputs.source_contract_sha256,),
                    )
                    continue
                specification = item.model_dump(
                    by_alias=True, mode="python", exclude={"source_node_id"}
                )
                target = RestTarget(sourceEntityIds=bound, **specification)
                _derived(
                    targets, records, partition, reason, target, (inputs.source_contract_sha256,)
                )

    @staticmethod
    def _apex_targets(
        bundle: CandidateAssuranceBundle,
        entities: tuple[SemanticScopeEntity, ...],
        nodes: dict[str, Any],
        adjacency: dict[str, set[str]],
        inputs: CandidateTargetCompilerInputs,
        targets: dict[TargetPartition, list[_Model]],
        records: list[TargetDerivationRecord],
    ) -> None:
        scope_ids = tuple(item.entity_id for item in entities)
        artifacts = {item.locator: item for item in inputs.referenced_artifacts}
        for test_id in _selected_test_ids(bundle):
            node = nodes.get(test_id)
            obligation_id = _obligation_id(test_id)
            bound = _connected_scope_ids(test_id, scope_ids, adjacency)
            if node is None or node.raw_kind != "apex-test":
                _unsupported(
                    records,
                    TargetPartition.APEX_TEST,
                    TargetDerivationReason.NON_APEX_TEST_OBLIGATION,
                    bound,
                    (inputs.source_contract_sha256,),
                    obligation_id=obligation_id,
                )
                continue
            if not bound:
                _unsupported(
                    records,
                    TargetPartition.APEX_TEST,
                    TargetDerivationReason.NO_GRAPH_CONNECTED_ENTITY,
                    (),
                    (inputs.source_contract_sha256,),
                    obligation_id=obligation_id,
                )
                continue
            owners = tuple(owner for owner in node.owners if owner.path.endswith(".cls"))
            methods = ()
            if len(owners) == 1 and owners[0].path in artifacts:
                methods = _exact_apex_test_methods(
                    artifacts[owners[0].path].artifact_bytes, node.label
                )
            if not methods:
                _unsupported(
                    records,
                    TargetPartition.APEX_TEST,
                    TargetDerivationReason.EXACT_APEX_METHODS_UNRESOLVED,
                    bound,
                    (inputs.source_contract_sha256,),
                    obligation_id=obligation_id,
                )
                continue
            for method in methods:
                target = ApexTestTarget(
                    sourceEntityIds=bound,
                    obligationId=obligation_id,
                    apexClass=node.label,
                    methodName=method,
                    maximumSeconds=300,
                )
                _derived(
                    targets,
                    records,
                    TargetPartition.APEX_TEST,
                    TargetDerivationReason.GRAPH_CONNECTED_APEX_TEST,
                    target,
                    (inputs.source_contract_sha256, artifacts[owners[0].path].artifact_sha256),
                )

    @staticmethod
    def _browser_and_dataset_targets(
        declarations: SourceOperationDeclarations,
        artifacts: dict[str, CapturedSourceArtifact],
        entities: tuple[SemanticScopeEntity, ...],
        nodes: dict[str, Any],
        adjacency: dict[str, set[str]],
        inputs: CandidateTargetCompilerInputs,
        targets: dict[TargetPartition, list[_Model]],
        records: list[TargetDerivationRecord],
    ) -> None:
        scope_ids = tuple(item.entity_id for item in entities)
        for item in declarations.browser_intents:
            bound = _connected_scope_ids(item.source_node_id, scope_ids, adjacency)
            node = nodes.get(item.source_node_id)
            if (
                node is None
                or node.raw_kind
                not in {"lightning-component", "custom-application", "flexipage", "lightning-page"}
                or not bound
            ):
                _unsupported(
                    records,
                    TargetPartition.BROWSER_INTENT,
                    TargetDerivationReason.NO_GRAPH_CONNECTED_ENTITY,
                    scope_ids,
                    (inputs.source_contract_sha256,),
                )
                continue
            source_hashes = [inputs.source_contract_sha256]
            if item.locator_rebind is not None:
                proof = _browser_rebind_source_proof(
                    item, declarations, nodes, adjacency, scope_ids, artifacts
                )
                if proof is None:
                    _unsupported(
                        records,
                        TargetPartition.BROWSER_INTENT,
                        TargetDerivationReason.SOURCE_SCHEMA_INSUFFICIENT,
                        bound,
                        tuple(source_hashes),
                    )
                    continue
                source_hashes.extend(proof)
            target = BrowserTarget(
                sourceEntityIds=bound,
                **item.model_dump(by_alias=True, mode="python", exclude={"source_node_id"}),
            )
            _derived(
                targets,
                records,
                TargetPartition.BROWSER_INTENT,
                TargetDerivationReason.CONTRACT_LOCATOR_HEALING_SUITE,
                target,
                tuple(source_hashes),
            )
        for item in declarations.synthetic_datasets:
            bound = _connected_scope_ids(item.source_node_id, scope_ids, adjacency)
            node = nodes.get(item.source_node_id)
            if (
                node is None
                or node.raw_kind != "object"
                or not bound
                or node.label != item.object_api_name
            ):
                _unsupported(
                    records,
                    TargetPartition.SYNTHETIC_DATASET,
                    TargetDerivationReason.NO_GRAPH_CONNECTED_ENTITY,
                    scope_ids,
                    (inputs.source_contract_sha256,),
                )
                continue
            target = DatasetTarget(
                sourceEntityIds=bound,
                maximumRecords=item.exact_cardinality,
                **item.model_dump(
                    by_alias=True, mode="python", exclude={"source_node_id", "exact_cardinality"}
                ),
            )
            _derived(
                targets,
                records,
                TargetPartition.SYNTHETIC_DATASET,
                TargetDerivationReason.CONTRACT_SYNTHETIC_DATASET,
                target,
                (inputs.source_contract_sha256,),
            )
        for partition, values in (
            (TargetPartition.BROWSER_INTENT, declarations.browser_intents),
            (TargetPartition.SYNTHETIC_DATASET, declarations.synthetic_datasets),
        ):
            if not values:
                _unsupported(
                    records,
                    partition,
                    TargetDerivationReason.SOURCE_SCHEMA_INSUFFICIENT,
                    scope_ids,
                    (inputs.source_contract_sha256,),
                )


def _browser_rebind_source_proof(
    declaration: SourceBrowserDeclaration,
    declarations: SourceOperationDeclarations,
    nodes: dict[str, Any],
    adjacency: dict[str, set[str]],
    scope_ids: tuple[str, ...],
    artifacts: dict[str, CapturedSourceArtifact],
) -> tuple[str, ...] | None:
    """Prove captured page/property facts, never live prestate or authority."""
    rebind = declaration.locator_rebind
    assert rebind is not None
    drift = rebind.metadata_drift
    component = nodes.get(declaration.source_node_id)
    pages = [
        value
        for value in nodes.values()
        if value.raw_kind == "lightning-page" and value.label == drift.member
    ]
    dataset = next(
        value
        for value in declarations.synthetic_datasets
        if value.dataset_id == rebind.navigation.dataset_id
    )
    dataset_node = nodes.get(dataset.source_node_id)
    if (
        component is None
        or component.raw_kind != "lightning-component"
        or drift.component_name != f"c:{component.label}"
        or len(pages) != 1
        or dataset_node is None
        or dataset_node.raw_kind != "object"
        or dataset_node.label != rebind.navigation.object_api_name
        or not _connected_scope_ids(dataset.source_node_id, scope_ids, adjacency)
        # Bind the page directly to the already impact-bound component; applying
        # the component's radius from its parent would drop a valid boundary edge.
        or declaration.source_node_id not in adjacency.get(pages[0].node_id, set())
    ):
        return None
    page_paths = [
        owner.path
        for owner in pages[0].owners
        if owner.path.endswith(f"/{drift.member}.flexipage-meta.xml")
    ]
    component_paths = [
        owner.path
        for owner in component.owners
        if owner.path.endswith(f"/{component.label}.js-meta.xml")
    ]
    if len(page_paths) != 1 or len(component_paths) != 1:
        return None
    if any(path not in artifacts for path in (*page_paths, *component_paths)):
        return None
    page_artifact, component_artifact = artifacts[page_paths[0]], artifacts[component_paths[0]]
    if len(page_artifact.artifact_bytes) > drift.maximum_bytes:
        return None
    try:
        page = _xml(page_artifact.artifact_bytes, page_artifact.locator, "FlexiPage")
        metadata = _xml(
            component_artifact.artifact_bytes,
            component_artifact.locator,
            "LightningComponentBundle",
        )
    except SalesforceGraphAdapterError:
        return None
    namespace = "{http://soap.sforce.com/2006/04/metadata}"

    def texts(parent: ElementTree.Element, name: str) -> list[str | None]:
        return [value.text for value in parent.findall(namespace + name)]

    if texts(page, "type") != ["RecordPage"] or texts(page, "sobjectType") != [
        rebind.navigation.object_api_name
    ]:
        return None
    instances = [
        value
        for value in page.iter(namespace + "componentInstance")
        if drift.component_identifier in texts(value, "identifier")
    ]
    if (
        len(instances) != 1
        or texts(instances[0], "identifier") != [drift.component_identifier]
        or texts(instances[0], "componentName") != [drift.component_name]
    ):
        return None
    if "lightning__RecordPage" not in [
        value.text for value in metadata.findall(f"{namespace}targets/{namespace}target")
    ]:
        return None
    configs = [
        config
        for config in metadata.findall(f"{namespace}targetConfigs/{namespace}targetConfig")
        if "lightning__RecordPage" in config.get("targets", "").split(",")
    ]
    if len(configs) != 1:
        return None
    objects = [value.text for value in configs[0].findall(f"{namespace}objects/{namespace}object")]
    if objects and rebind.navigation.object_api_name not in objects:
        return None
    properties = [
        value
        for config in configs
        for value in config.findall(namespace + "property")
        if value.get("name") == drift.property_name
    ]
    if len(properties) != 1 or properties[0].get("type") != "String":
        return None
    allowed_values = properties[0].get("datasource", "").split(",")
    if (
        properties[0].get("default") != drift.baseline_value
        or drift.baseline_value not in allowed_values
        or drift.alternate_value not in allowed_values
        or len(set(allowed_values)) != len(allowed_values)
    ):
        return None
    current = [
        value
        for value in instances[0].findall(namespace + "componentInstanceProperties")
        if drift.property_name in texts(value, "name")
    ]
    if len(current) > 1 or (
        current
        and (
            texts(current[0], "name") != [drift.property_name]
            or len(texts(current[0], "value")) != 1
            or len(current[0].findall(namespace + "value")[0]) != 0
        )
    ):
        return None
    allowed_states = {value.state for value in drift.allowed_pre_states}
    if current:
        if "PRESENT" not in allowed_states or texts(current[0], "value") != [drift.baseline_value]:
            return None
    elif "ABSENT" not in allowed_states:
        return None
    return (page_artifact.artifact_sha256, component_artifact.artifact_sha256)


def compose_candidate_live_plan(
    compilation: CandidateTargetCompilation,
    classification: HostOrganizationClassification,
    policy: LiveTargetPolicy,
    runner_provenance: ExpectedRunnerProvenance,
    *,
    clock: Callable[[], datetime] | None = None,
    local_validation_evidence: tuple[LocalValidationEvidence, ...] = (),
    local_validation_runner_pins: LocalValidationRunnerPins | None = None,
    local_validation_artifact_store: LocalValidationArtifactStore | None = None,
    issuer_registry: TrustedIssuerRegistry | None = None,
    receipt_ledger: LiveReceiptLedger | None = None,
    host_phase: EvidencePhase = EvidencePhase.LIVE_BASELINE,
    local_validation_phase_policy: HostLocalValidationPhasePolicy | None = None,
) -> CandidateLivePlanComposition:
    if not isinstance(local_validation_evidence, tuple):
        raise CandidateTargetCompilerError("Local validation requires typed artifact/receipt pairs")
    compilation = CandidateTargetCompilation.model_validate(compilation.model_dump(mode="python"))
    observed_at = (clock or (lambda: datetime.now(UTC)))()
    phase_deferred = classify_local_validation_phase(
        compilation,
        host_phase=host_phase,
        policy=local_validation_phase_policy,
        observed_at=observed_at,
    )
    verified_local_validations: tuple[VerifiedLocalValidation, ...] = ()
    if local_validation_evidence:
        if any(
            value is None
            for value in (
                local_validation_runner_pins,
                local_validation_artifact_store,
                issuer_registry,
                receipt_ledger,
            )
        ):
            raise CandidateTargetCompilerError(
                "Local validation requires host pins and durable trust ports"
            )
        verified_local_validations = verify_local_validation_evidence(
            compilation,
            local_validation_evidence,
            scope=classification.execution_scope,
            registry=issuer_registry,
            ledger=receipt_ledger,
            artifact_store=local_validation_artifact_store,
            runner_pins=local_validation_runner_pins,
            observed_at=observed_at,
        )
    verified_ids = {value.obligation_id for value in verified_local_validations}
    deferred_local_validations = tuple(
        value for value in phase_deferred if value.obligation_id not in verified_ids
    )
    deferred_ids = {value.obligation_id for value in deferred_local_validations}
    pending_local_validations = tuple(
        value
        for value in compilation.required_local_validations
        if value.obligation.obligation_id not in verified_ids | deferred_ids
    )

    class _Port:
        def capture(self) -> LiveTargetCapture:
            return LiveTargetCapture(
                sourceOperationProfileBytes=compilation.source_operation_profile_bytes,
                verifiedScope=compilation.verified_scope,
                organizationClassificationReceiptSha256=classification.receipt_sha256,
                organizationEnvironmentClass=classification.environment_class,
                organizationReceiptValidUntil=classification.valid_until,
            )

    evaluation = HostOwnedLiveTargetPlanProducer(_Port(), policy, clock=clock).produce()
    unsupported = tuple(
        item for item in compilation.derivations if item.state is TargetDerivationState.UNSUPPORTED
    )
    unknown_files = tuple(
        value
        for value in compilation.changed_file_dispositions
        if value.disposition == "UNKNOWN_BLOCKING"
    )
    unsupported_phase = host_phase is not EvidencePhase.LIVE_BASELINE
    if unsupported or unknown_files or pending_local_validations or unsupported_phase:
        body = evaluation.model_dump(mode="python", exclude={"evaluation_sha256"})
        body.update(state="BLOCKED", plan=None, authorized_target_sha256s=())
        body["gaps"] = tuple(
            sorted(
                set(
                    (
                        *evaluation.gaps,
                        *(
                            (
                                LiveTargetGap(
                                    code=LiveTargetGapCode.UNSUPPORTED_TARGET,
                                    identity_sha256=stable_sha256({"evidence_phase": host_phase}),
                                ),
                            )
                            if unsupported_phase
                            else ()
                        ),
                        *(
                            LiveTargetGap(
                                code=LiveTargetGapCode.UNSUPPORTED_TARGET,
                                partition=item.partition,
                                identity_sha256=stable_sha256(item.model_dump(mode="json")),
                            )
                            for item in unsupported
                        ),
                        *(
                            LiveTargetGap(
                                code=LiveTargetGapCode.UNSUPPORTED_TARGET,
                                identity_sha256=stable_sha256(value.model_dump(mode="json")),
                            )
                            for value in unknown_files
                        ),
                        *(
                            LiveTargetGap(
                                code=LiveTargetGapCode.LOCAL_VALIDATION_REQUIRED,
                                identity_sha256=value.command_contract_sha256,
                            )
                            for value in pending_local_validations
                        ),
                    )
                ),
                key=lambda item: (item.code.value, item.identity_sha256),
            )
        )
        evaluation = _sealed(LiveTargetPlanEvaluation, "evaluation_sha256", **body)
    phase_policy_sha = (
        local_validation_phase_policy.policy_sha256
        if local_validation_phase_policy is not None
        else None
    )
    if evaluation.plan is not None and local_validation_phase_policy is not None:
        plan_body = evaluation.plan.model_dump(mode="python", exclude={"plan_sha256"})
        plan_body.update(
            targets=evaluation.plan.targets,
            local_validation_phase_policy_sha256=phase_policy_sha,
            evidence_phase=host_phase,
            deferred_local_validations=deferred_local_validations,
            phase_read_only_gate_ids=READ_ONLY_BASELINE_GATES,
            valid_until=min(
                _parse_time(evaluation.plan.valid_until), local_validation_phase_policy.valid_until
            )
            .astimezone(UTC)
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        plan = _sealed(LiveTargetPlan, "plan_sha256", **plan_body)
        evaluation_body = evaluation.model_dump(mode="python", exclude={"evaluation_sha256"})
        evaluation_body["plan"] = plan
        evaluation_body["gaps"] = evaluation.gaps
        evaluation = _sealed(LiveTargetPlanEvaluation, "evaluation_sha256", **evaluation_body)
    scope = classification.execution_scope
    if (
        scope.project_id != compilation.verified_scope.project_id
        or scope.source_contract_sha256 != compilation.source_contract_sha256
        or scope.candidate_sha256 != compilation.candidate_bundle_sha256
    ):
        raise CandidateTargetCompilerError("Execution receipt scope differs from compilation")
    requirements = [
        ExecutionSupportRequirement(receipt_role="SF-L01", status="HOST_RECEIPT_REQUIRED"),
        ExecutionSupportRequirement(receipt_role="SF-L02", status="HOST_RECEIPT_REQUIRED"),
    ]
    requirements.extend(
        ExecutionSupportRequirement(
            receipt_role="LOCAL_SOURCE_VALIDATION_RECEIPT",
            artifact_sha256=value.command_contract_sha256,
            status="HOST_RECEIPT_REQUIRED",
        )
        for value in pending_local_validations
    )
    requirements.extend(
        ExecutionSupportRequirement(
            receipt_role="LOCAL_SOURCE_VALIDATION_RECEIPT",
            artifact_sha256=value.command_contract_sha256,
            status="NOT_REQUIRED_FOR_PHASE_UNRESOLVED",
        )
        for value in deferred_local_validations
    )
    expected_contract = None
    expected_contract_bytes = None
    expected_contract_bytes_sha256 = None
    if evaluation.plan is not None:
        expected_contract = _expected_execution_contract(
            compilation,
            evaluation,
            classification.execution_scope,
            runner_provenance,
            verified_local_validations,
        )
        expected_contract_bytes = _canonical_bytes(expected_contract.model_dump(mode="json"))
        expected_contract_bytes_sha256 = hashlib.sha256(expected_contract_bytes).hexdigest()
        requirements.append(
            ExecutionSupportRequirement(
                receipt_role="LIVE_TARGET_PLAN_RECEIPT",
                artifact_sha256=evaluation.plan.plan_sha256,
                status="HOST_RECEIPT_REQUIRED",
            )
        )
        requirements.append(
            ExecutionSupportRequirement(
                receipt_role="EXPECTED_EXECUTION_CONTRACT_RECEIPT",
                artifact_sha256=expected_contract_bytes_sha256,
                status="HOST_RECEIPT_REQUIRED",
            )
        )
        if any(
            target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
            and any(
                variable.get("valueSource") == "SYNTHETIC_DATASET_RECEIPT"
                for variable in target.specification.get("variables", ())
            )
            for target in evaluation.plan.targets
        ):
            requirements.append(
                ExecutionSupportRequirement(
                    receipt_role="LIVE_DATASET_SCOPE_RECEIPT",
                    status="RUNTIME_RESOLUTION_REQUIRED",
                )
            )
    body = {
        "compilation": compilation,
        "evaluation": evaluation,
        "expected_execution_contract": expected_contract,
        "expected_execution_contract_bytes": expected_contract_bytes,
        "expected_execution_contract_bytes_sha256": (expected_contract_bytes_sha256),
        "support_requirements": tuple(requirements),
        "verified_local_validations": verified_local_validations,
    }
    if phase_policy_sha is not None:
        body.update(
            local_validation_phase_policy_sha256=phase_policy_sha,
            deferred_local_validations=deferred_local_validations,
        )
    return _sealed(CandidateLivePlanComposition, "composition_sha256", **body)


_GATE_BY_TARGET = {
    TargetPartition.STANDARD_REST: "SF-L03",
    TargetPartition.CUSTOM_REST: "SF-L04",
    TargetPartition.METADATA: "SF-L05",
    TargetPartition.BROWSER_INTENT: "SF-L07",
    TargetPartition.APEX_TEST: "SF-L08",
}


def _expected_execution_contract(
    compilation: CandidateTargetCompilation,
    evaluation: LiveTargetPlanEvaluation,
    scope: ReceiptScope,
    runner_provenance: ExpectedRunnerProvenance,
    verified_local_validations: tuple[VerifiedLocalValidation, ...] = (),
) -> ExpectedExecutionContract:
    plan = evaluation.plan
    if plan is None:
        raise CandidateTargetCompilerError("Blocked plan cannot create execution expectations")
    dataset_targets = tuple(
        item for item in plan.targets if item.partition is TargetPartition.SYNTHETIC_DATASET
    )
    datasets = tuple(
        ExpectedDatasetContract(
            datasetTargetSha256=item.target_sha256,
            sourceEntityIds=item.source_entity_ids,
            objectApiName=item.specification["objectApiName"],
            fieldProjection=tuple(sorted(item.specification["fieldProjection"])),
            ownershipMarkerField=item.specification["ownershipMarkerField"],
            predicateSha256=stable_sha256(item.specification["predicates"]),
            exactCardinality=item.specification["maximumRecords"],
            identityField=item.specification["identityField"],
        )
        for item in dataset_targets
    )
    assertions: list[ExpectedTargetAssertion] = []
    for item in plan.targets:
        if item.partition is TargetPartition.SYNTHETIC_DATASET:
            continue
        partition = item.partition
        specification = item.specification
        compatible = tuple(
            sorted(
                target.target_sha256
                for target in dataset_targets
                if (
                    item.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
                    and target.specification.get("datasetId") == item.specification.get("datasetId")
                )
            )
        )
        metadata_type = None
        metadata_member = None
        resolution_contract_sha256 = None
        minimum = 1
        maximum = 1
        invocation_count = 1
        request_expansion = None
        per_response_minimum = None
        per_response_maximum = None
        if partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}:
            matching_datasets = [
                value for value in datasets if value.dataset_target_sha256 in compatible
            ]
            if len(matching_datasets) != 1:
                raise CandidateTargetCompilerError(
                    "REST expansion needs exactly one proven dataset"
                )
            invocation_count = matching_datasets[0].exact_cardinality
            request_expansion = specification["requestExpansion"]
            per_response_minimum = specification["minimumCardinality"]
            per_response_maximum = specification["maximumCardinality"]
            minimum = per_response_minimum * invocation_count
            maximum = per_response_maximum * invocation_count
            predicate = ExpectedAssertionPredicate.REST_CLOSED_PROJECTION
            projection = tuple(
                sorted(
                    f"{field['path']}:{field['dataType']}:{str(field['required']).lower()}:{str(field['nullable']).lower()}"
                    + (
                        f":literal={stable_sha256(field['expectedLiteral'])}"
                        if field.get("expectedLiteral") is not None
                        else ""
                    )
                    for field in specification["responseFields"]
                )
            )
            synthetic = any(
                variable["valueSource"] == "SYNTHETIC_DATASET_RECEIPT"
                for variable in specification["variables"]
            )
            if synthetic and not compatible:
                raise CandidateTargetCompilerError(
                    "REST expectation has no compatible dataset contract"
                )
            resolution_contract_sha256 = stable_sha256(
                {
                    "route_template": specification["routeTemplate"],
                    "variables": sorted(
                        specification["variables"], key=lambda value: value["name"]
                    ),
                    "compatible_dataset_target_sha256s": compatible,
                    "response_projection": projection,
                    "request_expansion": request_expansion,
                    "invocation_count": invocation_count,
                    "response_record_path": specification["responseRecordPath"],
                    "dataset_field_paths": specification["datasetFieldPaths"],
                    "parent_bindings": specification["parentBindings"],
                    "per_response_minimum_cardinality": per_response_minimum,
                    "per_response_maximum_cardinality": per_response_maximum,
                }
            )
        elif partition is TargetPartition.METADATA:
            predicate = ExpectedAssertionPredicate.METADATA_EXACT_MEMBER_MANIFEST
            metadata_type = specification["metadataType"]
            metadata_member = specification["member"]
            projection = (f"{metadata_type}:{metadata_member}",)
            maximum = int(specification["maximumFiles"])
        elif partition is TargetPartition.APEX_TEST:
            predicate = ExpectedAssertionPredicate.APEX_TEST_EXACT_OUTCOME
            projection = (f"{specification['apexClass']}.{specification['methodName']}",)
        elif partition is TargetPartition.BROWSER_INTENT:
            predicate = ExpectedAssertionPredicate.BROWSER_INTENT_EXACT_SCOPE
            projection = tuple(
                sorted(
                    (
                        f"action:{specification['actionClass']}",
                        f"application:{specification['applicationIntent']}",
                        f"surface:{specification['surfaceIntent']}",
                    )
                )
            )
        else:
            raise CandidateTargetCompilerError("Unsupported execution target partition")
        assertions.append(
            ExpectedTargetAssertion(
                assertionId=f"target:{partition.value.casefold()}:{item.target_sha256[:32]}",
                gateId=_GATE_BY_TARGET[partition],
                partition=partition,
                targetSha256=item.target_sha256,
                predicate=predicate,
                expectedSha256=stable_sha256(
                    {
                        "target": item.target_sha256,
                        "projection": projection,
                        "minimum": minimum,
                        "maximum": maximum,
                        "invocation_count": invocation_count,
                        "request_expansion": request_expansion,
                    }
                ),
                minimumCardinality=minimum,
                maximumCardinality=maximum,
                invocationCount=invocation_count,
                requestExpansion=request_expansion,
                perResponseMinimumCardinality=per_response_minimum,
                perResponseMaximumCardinality=per_response_maximum,
                exactProjection=projection,
                metadataType=metadata_type,
                metadataMember=metadata_member,
                compatibleDatasetTargetSha256s=compatible,
                resolutionContractSha256=resolution_contract_sha256,
            )
        )
    assertions_tuple = tuple(sorted(assertions, key=lambda item: item.assertion_id))
    body = {
        "schema_version": "1.0.0",
        "authority_scope": "EXECUTION_EXPECTATION_ONLY",
        "authorizes_execution": False,
        "execution_id": f"candidate-live:{plan.plan_sha256[:32]}",
        "request_sha256": compilation.verified_scope.verified_change_sha256,
        "candidate_bundle_sha256": compilation.candidate_bundle_sha256,
        "compilation_sha256": compilation.compilation_sha256,
        "plan_sha256": plan.plan_sha256,
        "scope": scope,
        "evidence_phase": EvidencePhase.LIVE_BASELINE,
        "gate_ids": tuple(sorted({item.gate_id for item in assertions_tuple})),
        "runner_provenance": runner_provenance,
        "assertions": assertions_tuple,
        "datasets": datasets,
        "verified_local_validations": verified_local_validations,
        "valid_until": min(
            _parse_time(plan.valid_until),
            *(value.expires_at for value in verified_local_validations),
        )
        .isoformat()
        .replace("+00:00", "Z")
        if verified_local_validations
        else plan.valid_until,
    }
    if plan.local_validation_phase_policy_sha256 is not None:
        body.update(
            local_validation_phase_policy_sha256=plan.local_validation_phase_policy_sha256,
            deferred_local_validations=plan.deferred_local_validations,
            phase_read_only_gate_ids=plan.phase_read_only_gate_ids,
        )
    return _sealed(ExpectedExecutionContract, "contract_sha256", **body)


_METADATA_KIND: dict[str, str] = {
    "apex-class": "ApexClass",
    "apex-test": "ApexClass",
    "approval-process": "ApprovalProcess",
    "custom-application": "CustomApplication",
    "custom-metadata": "CustomMetadata",
    "custom-metadata-record": "CustomMetadata",
    "field": "CustomField",
    "flexipage": "FlexiPage",
    "flow": "Flow",
    "layout": "Layout",
    "lightning-component": "LightningComponentBundle",
    "list-view": "ListView",
    "object": "CustomObject",
    "permission-set": "PermissionSet",
    "report": "Report",
    "report-folder": "ReportFolder",
    "tab": "CustomTab",
    "workflow": "Workflow",
}


def _metadata_identity(node: Any) -> tuple[str, str] | None:
    if node is None:
        return None
    metadata_type = _METADATA_KIND.get(node.raw_kind)
    member = node.label
    if metadata_type is None or not isinstance(member, str) or not member or "*" in member:
        return None
    return metadata_type, member


def _candidate_graph(
    bundle: CandidateAssuranceBundle,
) -> tuple[dict[str, Any], dict[str, set[str]]]:
    graph = bundle.graph_production_artifact
    nodes = {item.node_id: item for item in (*graph.base.nodes, *graph.candidate.nodes)}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in (*graph.base.edges, *graph.candidate.edges):
        adjacency[edge.source_id].add(edge.target_id)
        adjacency[edge.target_id].add(edge.source_id)
    return nodes, adjacency


def _connected_scope_ids(
    start: str,
    scope_ids: tuple[str, ...],
    adjacency: dict[str, set[str]],
    *,
    maximum_depth: int = 3,
) -> tuple[str, ...]:
    expected = set(scope_ids)
    queue = deque([(start, 0)])
    visited = {start}
    matched: set[str] = set()
    while queue:
        current, depth = queue.popleft()
        if current in expected:
            matched.add(current)
        if depth == maximum_depth:
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
    return tuple(sorted(matched))


def _object_bindings(
    entities: tuple[SemanticScopeEntity, ...],
    nodes: dict[str, Any],
    adjacency: dict[str, set[str]],
) -> dict[str, tuple[str, ...]]:
    scope_ids = tuple(item.entity_id for item in entities)
    result: dict[str, set[str]] = defaultdict(set)
    for node_id, node in nodes.items():
        if node.raw_kind != "object" or not _SAFE_API_NAME.fullmatch(node.label):
            continue
        for bound in _connected_scope_ids(node_id, scope_ids, adjacency):
            result[node.label].add(bound)
    return {key: tuple(sorted(values)) for key, values in result.items() if values}


def _selected_test_ids(bundle: CandidateAssuranceBundle) -> tuple[str, ...]:
    return tuple(
        sorted(
            {item.test_id for analysis in bundle.analyses for item in analysis.run.selected_tests}
        )
    )


def _obligation_id(test_id: str) -> str:
    return f"selected-test:{hashlib.sha256(test_id.encode()).hexdigest()}"


def _source_operations_reference(contract: dict[str, Any]) -> SourceOperationsReference:
    try:
        reference = SourceOperationsReference.model_validate(contract.get("sourceOperations"))
        require_safe_repository_locator(reference.locator)
        return reference
    except (ValidationError, ValueError):
        raise CandidateTargetCompilerError(
            "Explicit versioned sourceOperations reference required"
        ) from None


def _referenced_locators(
    contract: dict[str, Any],
    bundle: CandidateAssuranceBundle,
    declarations: SourceOperationDeclarations | None = None,
) -> tuple[str, ...]:
    locators = {_source_operations_reference(contract).locator}
    if declarations is not None:
        candidate_paths = {
            value.path for value in bundle.graph_production_artifact.candidate.dispositions
        }
        locators.update(
            value.locator
            for value in declarations.non_runtime_files
            if value.locator in candidate_paths
        )
        for obligation in declarations.local_test_obligations:
            locators.update(obligation.test_locators)
            locators.update(obligation.regenerated_locators)
    nodes = {item.node_id: item for item in bundle.graph_production_artifact.candidate.nodes}
    if declarations is not None:
        for browser in declarations.browser_intents:
            if browser.locator_rebind is None:
                continue
            member = browser.locator_rebind.metadata_drift.member
            for node in nodes.values():
                if node.node_id == browser.source_node_id or (
                    node.raw_kind == "lightning-page" and node.label == member
                ):
                    locators.update(
                        owner.path
                        for owner in node.owners
                        if owner.path.endswith((".flexipage-meta.xml", ".js-meta.xml"))
                    )
    for test_id in _selected_test_ids(bundle):
        node = nodes.get(test_id)
        if node is not None and node.raw_kind == "apex-test":
            locators.update(owner.path for owner in node.owners if owner.path.endswith(".cls"))
    for value in locators:
        require_safe_repository_locator(value)
    if len(locators) > _MAX_TARGETS:
        raise CandidateTargetCompilerError("Referenced source artifact capacity exceeded")
    return tuple(sorted(locators))


def _parse_source_declarations(raw: bytes) -> SourceOperationDeclarations:
    try:
        return SourceOperationDeclarations.model_validate(_strict_object(raw, "source operations"))
    except ValidationError:
        raise CandidateTargetCompilerError(
            "Versioned source-operation declarations are invalid"
        ) from None


def _classify_changed_files(
    inputs: CandidateTargetCompilerInputs,
    declarations: SourceOperationDeclarations,
    operations_locator: str,
) -> tuple[tuple[CandidateChangedFileDisposition, ...], tuple[RequiredLocalValidation, ...]]:
    bundle = inputs.candidate_bundle
    consumed = {inputs.source_contract_locator, operations_locator}
    nonruntime = {value.locator: value for value in declarations.non_runtime_files}
    if consumed & nonruntime.keys():
        raise CandidateTargetCompilerError(
            "Consumed source contracts cannot be declared nonruntime"
        )
    all_dispositions = {
        (value.side, value.path): value
        for side in (
            bundle.graph_production_artifact.base,
            bundle.graph_production_artifact.candidate,
        )
        for value in side.dispositions
    }
    for locator in nonruntime:
        facts = [value for (_, path), value in all_dispositions.items() if path == locator]
        if not facts or any(
            value.semantic_status is not SemanticFileStatus.NOT_APPLICABLE for value in facts
        ):
            raise CandidateTargetCompilerError(
                "Salesforce runtime or unknown source cannot be declared nonruntime"
            )
    changed: list[CandidateChangedFileDisposition] = []
    required: set[str] = set()
    for binding in bundle.operation_seed_artifact.bindings:
        path = binding.change.path
        facts = [all_dispositions[(image.side, image.path)] for image in binding.file_evidence]
        declared = nonruntime.get(path)
        category = None
        obligation_ids: tuple[str, ...] = ()
        if any(value.semantic_status is SemanticFileStatus.PARSED for value in facts):
            disposition = "SALESFORCE_RUNTIME"
        elif path in consumed:
            disposition = "CONSUMED_SOURCE_CONTRACT"
        elif declared is not None and all(
            value.semantic_status is SemanticFileStatus.NOT_APPLICABLE
            and value.semantic_reason_code == "NO_SEMANTIC_FAMILY"
            for value in facts
        ):
            disposition = "NON_SALESFORCE_RUNTIME"
            category = declared.category
            obligation_ids = declared.local_obligation_ids
            required.update(obligation_ids)
        else:
            disposition = "UNKNOWN_BLOCKING"
        changed.append(
            CandidateChangedFileDisposition(
                binding_sha256=binding.binding_sha256,
                locator=path,
                disposition=disposition,
                category=category,
                side_file_bindings=binding.file_evidence,
                local_obligation_ids=obligation_ids,
            )
        )
    files = tuple(
        CandidateSourceFileBinding(
            locator=value.path,
            size_bytes=value.size_bytes,
            content_sha256=value.content_sha256,
        )
        for value in sorted(
            bundle.graph_production_artifact.candidate.dispositions, key=lambda value: value.path
        )
    )
    local = []
    for obligation in declarations.local_test_obligations:
        if obligation.obligation_id not in required:
            continue
        local.append(
            RequiredLocalValidation(
                obligation=obligation,
                bound_files=files,
                candidate_tree_sha256=bundle.operation_seed_artifact.candidate_tree_sha256,
                command_contract_sha256=stable_sha256(
                    {
                        "obligation": obligation.model_dump(by_alias=True, mode="json"),
                        "files": [value.model_dump(mode="json") for value in files],
                        "candidate": bundle.bundle_sha256,
                        "candidate_tree_sha256": (
                            bundle.operation_seed_artifact.candidate_tree_sha256
                        ),
                        "changed_file_dispositions": [
                            value.model_dump(mode="json")
                            for value in changed
                            if obligation.obligation_id in value.local_obligation_ids
                        ],
                    }
                ),
            )
        )
    return tuple(sorted(changed, key=lambda value: value.binding_sha256)), tuple(
        sorted(local, key=lambda value: value.obligation.obligation_id)
    )


def _verify_contract_identity(contract: dict[str, Any], bundle: CandidateAssuranceBundle) -> None:
    schema = contract.get("schemaVersion")
    application = contract.get("application")
    if (
        not isinstance(schema, str)
        or not re.fullmatch(r"\d+\.\d+\.\d+", schema)
        or not isinstance(application, str)
    ):
        raise CandidateTargetCompilerError("Source contract identity is invalid")
    canonical = re.sub(r"[^a-z0-9]+", "-", application.casefold()).strip("-")
    if canonical != bundle.foundation.project_id:
        raise CandidateTargetCompilerError("Source contract targets a different project")


def _require_current_bundle(bundle: CandidateAssuranceBundle) -> None:
    now = datetime.now(UTC)
    for artifact in (bundle.graph_production_artifact, bundle.operation_seed_artifact):
        observed = getattr(artifact, "observed_at", None) or artifact.evaluated_at
        if _parse_time(artifact.valid_until) <= now or _parse_time(observed) > now:
            raise CandidateTargetCompilerError(
                "Candidate source capture is expired or from the future"
            )


def _candidate_file(bundle: CandidateAssuranceBundle, locator: str) -> Any:
    require_safe_repository_locator(locator)
    matches = [
        item
        for item in bundle.graph_production_artifact.candidate.dispositions
        if item.path == locator
    ]
    if len(matches) != 1:
        raise CandidateTargetCompilerError(
            "Source locator is absent from exact verified candidate manifest"
        )
    return matches[0]


def _verify_candidate_file(bundle: CandidateAssuranceBundle, locator: str, raw: bytes) -> None:
    item = _candidate_file(bundle, locator)
    if item.size_bytes != len(raw) or item.content_sha256 != hashlib.sha256(raw).hexdigest():
        raise CandidateTargetCompilerError(
            "Source bytes differ from exact verified candidate manifest"
        )


def _read_candidate_file(
    root: Path, locator: str, bundle: CandidateAssuranceBundle, cap: int
) -> bytes:
    item = _candidate_file(bundle, locator)
    if item.size_bytes > cap:
        raise CandidateTargetCompilerError("Source artifact exceeds bounded read capacity")
    target = root.joinpath(*PurePosixPath(locator).parts)
    chain = (
        root,
        *tuple(
            root.joinpath(*PurePosixPath(locator).parts[:index])
            for index in range(1, len(PurePosixPath(locator).parts) + 1)
        ),
    )

    def inspect_chain() -> tuple[tuple[int, int, int, int], ...]:
        result = []
        for index, path in enumerate(chain):
            details = path.lstat()
            if (
                stat.S_ISLNK(details.st_mode)
                or getattr(details, "st_file_attributes", 0) & 0x400
                or (index < len(chain) - 1 and not stat.S_ISDIR(details.st_mode))
                or (index == len(chain) - 1 and not stat.S_ISREG(details.st_mode))
            ):
                raise CandidateTargetCompilerError(
                    "Source path contains a link, reparse point or special file"
                )
            result.append((details.st_dev, details.st_ino, details.st_mode, details.st_size))
        return tuple(result)

    descriptor = None
    try:
        before = inspect_chain()
        if before[-1][3] != item.size_bytes:
            raise CandidateTargetCompilerError("Source changed after verified capture")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != before[-1]:
            raise CandidateTargetCompilerError("Source changed during bounded capture")
        chunks = []
        remaining = item.size_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        final = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise CandidateTargetCompilerError("Source changed during bounded capture")
        if inspect_chain() != before:
            raise CandidateTargetCompilerError("Source path changed during bounded capture")
    except OSError:
        raise CandidateTargetCompilerError("Bounded no-follow source capture failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _verify_candidate_file(bundle, locator, raw)
    _require_current_bundle(bundle)
    return raw


def _exact_apex_test_methods(raw: bytes, expected_class: str) -> tuple[str, ...]:
    # Parse only the explicitly supported Apex test declaration grammar. Unknown
    # signatures remain obligations and block; no class-wide runner fallback exists.
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return ()
    escaped_backslash = re.escape(chr(92))
    line_comment = "/" * 2 + r"[^\n]*"
    block_comment = r"/\*[\s\S]*?\*/"
    tokens = re.compile(
        r"'(?:(?:"
        + escaped_backslash
        + r".)|[^'"
        + escaped_backslash
        + r"])*'"
        + "|"
        + line_comment
        + "|"
        + block_comment
    )
    text = tokens.sub(lambda match: " " * len(match.group()), text)
    classes = tuple(re.finditer(r"\bclass\s+([A-Za-z][A-Za-z0-9_]*)", text, re.I))
    if len(classes) != 1 or classes[0].group(1) != expected_class:
        return ()
    body = text[classes[0].end() :]
    if re.match(r"\s*\{", body) is None:
        return ()
    pattern = re.compile(
        r"(?:@isTest(?:\s*\([^)]*\))?\s+(?:(?:public|private|global)\s+)?static\s+void"
        r"|(?:(?:public|private|global)\s+)?static\s+testMethod\s+void)\s+"
        r"([A-Za-z][A-Za-z0-9_]*)\s*\(\s*\)\s*\{",
        re.I,
    )
    matches = tuple(pattern.finditer(body))
    declared = len(re.findall(r"@isTest\b|\btestMethod\b", body, re.I))
    if not matches or declared != len(matches):
        return ()
    depth = 0
    depths = {}
    declaration_offsets = {match.start() for match in matches}
    for index, char in enumerate(body):
        if index in declaration_offsets:
            depths[index] = depth
        depth += (char == "{") - (char == "}")
        if depth < 0:
            return ()
    names = tuple(sorted(match.group(1) for match in matches))
    if depth != 0 or len(names) != len(set(names)) or any(depths[m.start()] != 1 for m in matches):
        return ()
    return names


def _strict_object(raw: bytes, label: str) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateTargetCompilerError(f"Duplicate key in {label}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=hook)
    except CandidateTargetCompilerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateTargetCompilerError(f"Invalid {label}") from exc
    if not isinstance(value, dict):
        raise CandidateTargetCompilerError(f"Invalid {label}")
    return value


def _target_digest(partition: TargetPartition, target: _Model) -> str:
    return derive_target_sha256(partition, target)


def _derived(
    targets: dict[TargetPartition, list[_Model]],
    records: list[TargetDerivationRecord],
    partition: TargetPartition,
    reason: TargetDerivationReason,
    target: _Model,
    source_artifact_sha256s: tuple[str, ...],
) -> None:
    targets[partition].append(target)
    records.append(
        TargetDerivationRecord(
            state=TargetDerivationState.DERIVED,
            partition=partition,
            reason=reason,
            sourceEntityIds=tuple(sorted(set(target.source_entity_ids))),
            sourceArtifactSha256s=tuple(sorted(set(source_artifact_sha256s))),
            targetSha256=_target_digest(partition, target),
            obligationId=getattr(target, "obligation_id", None),
        )
    )


def _unsupported(
    records: list[TargetDerivationRecord],
    partition: TargetPartition,
    reason: TargetDerivationReason,
    source_ids: tuple[str, ...],
    artifact_ids: tuple[str, ...],
    *,
    obligation_id: str | None = None,
) -> None:
    records.append(
        TargetDerivationRecord(
            state=TargetDerivationState.UNSUPPORTED,
            partition=partition,
            reason=reason,
            sourceEntityIds=tuple(sorted(set(source_ids))),
            sourceArtifactSha256s=tuple(sorted(set(artifact_ids))),
            obligationId=obligation_id,
        )
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sealed(model: type[_Model], field: str, **values: Any) -> Any:
    body = model.model_construct(**values).model_dump(mode="json")
    body[field] = stable_sha256(body)
    return model.model_validate(body)


def _verify_digest(model: _Model, field: str) -> None:
    body = model.model_dump(mode="json")
    declared = body.pop(field)
    if declared != stable_sha256(body):
        raise ValueError(f"{field} mismatch")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Timestamp must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(UTC)


__all__ = [
    "CandidateLivePlanComposition",
    "CandidateTargetCompilation",
    "CandidateTargetCompilerError",
    "CandidateTargetCompilerInputs",
    "CapturedSourceArtifact",
    "ExecutionSupportRequirement",
    "HostOrganizationClassification",
    "HostOwnedSourceContractPort",
    "ProductionCandidateTargetCompiler",
    "TargetDerivationReason",
    "TargetDerivationRecord",
    "TargetDerivationState",
    "compose_candidate_live_plan",
]
