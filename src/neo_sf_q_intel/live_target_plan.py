from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from neo_sf_q_intel.live_receipts import EvidencePhase
from neo_sf_q_intel.local_validation_phase import (
    READ_ONLY_BASELINE_GATES,
    DeferredLocalValidation,
)
from neo_sf_q_intel.ontology import contract_sha256
from neo_sf_q_intel.temporal import aware_utc, parse_aware_utc


class LiveTargetContractError(RuntimeError):
    """A strict live-target input or pinned policy is invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RestVariable(_Model):
    name: str = Field(min_length=1, max_length=100)
    value_source: Literal["API_VERSION", "SYNTHETIC_DATASET_RECEIPT"] = Field(alias="valueSource")
    data_type: Literal["STRING", "ID", "INTEGER"] = Field(alias="dataType")
    dataset_field: str | None = Field(alias="datasetField", min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_binding(self) -> RestVariable:
        if (self.value_source == "SYNTHETIC_DATASET_RECEIPT") != (self.dataset_field is not None):
            raise ValueError("Dataset variables require an explicit field; API variables forbid it")
        return self


class ResponseExpectedLiteral(_Model):
    value: StrictStr | StrictInt | StrictFloat | StrictBool | None

    @model_validator(mode="after")
    def validate_literal(self) -> ResponseExpectedLiteral:
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Expected literal must be a finite JSON primitive")
        return self


class ResponseField(_Model):
    path: str = Field(min_length=1, max_length=300)
    data_type: Literal["STRING", "INTEGER", "NUMBER", "BOOLEAN", "OBJECT", "ARRAY"] = Field(
        alias="dataType"
    )
    required: bool = Field(strict=True)
    nullable: bool = Field(strict=True)
    expected_literal: ResponseExpectedLiteral | None = Field(
        default=None, alias="expectedLiteral", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_literal_type(self) -> ResponseField:
        if self.expected_literal is not None:
            value = self.expected_literal.value
            valid = (
                self.nullable
                if value is None
                else {
                    "STRING": type(value) is str,
                    "INTEGER": type(value) is int,
                    "NUMBER": type(value) in {int, float},
                    "BOOLEAN": type(value) is bool,
                    "OBJECT": False,
                    "ARRAY": False,
                }[self.data_type]
            )
            if not valid:
                raise ValueError("Expected literal must match declared response type/nullability")
        return self


class ResponseParentBinding(_Model):
    collection_path: str = Field(alias="collectionPath", min_length=1, max_length=300)
    parent_id_path: str = Field(alias="parentIdPath", min_length=1, max_length=300)
    dataset_field: str = Field(alias="datasetField", min_length=1, max_length=200)
    maximum_cardinality: int = Field(alias="maximumCardinality", ge=1, le=4096)

    @model_validator(mode="after")
    def validate_paths(self) -> ResponseParentBinding:
        if not self.collection_path.endswith("[]") or "[]" in self.parent_id_path:
            raise ValueError("Parent binding needs one collection and a scalar relative ID path")
        return self


class RestTarget(_Model):
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    method: Literal["GET"]
    route_template: str = Field(alias="routeTemplate", min_length=1, max_length=1000)
    variables: tuple[RestVariable, ...]
    response_fields: tuple[ResponseField, ...] = Field(alias="responseFields", min_length=1)
    maximum_response_bytes: int = Field(alias="maximumResponseBytes", ge=1)
    dataset_id: str = Field(alias="datasetId", min_length=1, max_length=200)
    minimum_cardinality: int = Field(alias="minimumCardinality", ge=1, le=4096)
    maximum_cardinality: int = Field(alias="maximumCardinality", ge=1, le=4096)
    request_expansion: Literal["EACH_DATASET_RECORD"] = Field(alias="requestExpansion")
    response_record_path: str = Field(alias="responseRecordPath", max_length=300)
    dataset_field_paths: dict[str, str] = Field(alias="datasetFieldPaths", min_length=1)
    parent_bindings: tuple[ResponseParentBinding, ...] = Field(alias="parentBindings")

    @model_validator(mode="after")
    def validate_rest_target(self) -> RestTarget:
        _require_unique(self.source_entity_ids, "source entity bindings")
        variable_names = tuple(item.name for item in self.variables)
        response_paths = tuple(item.path for item in self.response_fields)
        _require_unique(variable_names, "REST variables")
        _require_unique(response_paths, "REST response fields")
        placeholders = tuple(
            sorted(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", self.route_template))
        )
        if tuple(sorted(variable_names)) != placeholders:
            raise ValueError("REST variables must exactly match route placeholders")
        if any(_contains_wildcard(value) for value in response_paths):
            raise ValueError("REST response fields cannot use wildcards")
        if self.minimum_cardinality > self.maximum_cardinality:
            raise ValueError("REST cardinality is invalid")
        if any(
            not value or value not in response_paths for value in self.dataset_field_paths.values()
        ):
            raise ValueError("Dataset response mappings must refer to declared projected fields")
        _require_unique(
            tuple(item.collection_path for item in self.parent_bindings), "parent collections"
        )
        for binding in self.parent_bindings:
            if (
                binding.collection_path not in response_paths
                or binding.collection_path + "." + binding.parent_id_path not in response_paths
            ):
                raise ValueError("Parent bindings must reference exact projected collection fields")
        return self


class MetadataTarget(_Model):
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    metadata_type: str = Field(alias="metadataType", min_length=1, max_length=200)
    member: str = Field(min_length=1, max_length=500)
    maximum_files: int = Field(alias="maximumFiles", ge=1)
    maximum_bytes: int = Field(alias="maximumBytes", ge=1)

    @model_validator(mode="after")
    def validate_metadata_target(self) -> MetadataTarget:
        _require_unique(self.source_entity_ids, "source entity bindings")
        return self


class ApexTestTarget(_Model):
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    obligation_id: str = Field(alias="obligationId", min_length=1, max_length=300)
    apex_class: str = Field(alias="apexClass", min_length=1, max_length=200)
    method_name: str = Field(alias="methodName", min_length=1, max_length=200)
    maximum_seconds: int = Field(alias="maximumSeconds", ge=1)

    @model_validator(mode="after")
    def validate_apex_target(self) -> ApexTestTarget:
        _require_unique(self.source_entity_ids, "source entity bindings")
        return self


class BrowserRecordNavigation(_Model):
    kind: Literal["DATASET_RECORD_PAGE"]
    dataset_id: str = Field(alias="datasetId", min_length=1, max_length=200)
    object_api_name: str = Field(alias="objectApiName", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    identity_field: str = Field(alias="identityField", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    marker_field: str = Field(alias="markerField", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    marker_value: str = Field(alias="markerValue", min_length=1, max_length=1000)
    resolution: Literal["EXACTLY_ONE_AUTHORIZED_DATASET_MEMBER"]


class BrowserAbsentPropertyState(_Model):
    state: Literal["ABSENT"]


class BrowserPresentPropertyState(_Model):
    state: Literal["PRESENT"]
    value: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")


class BrowserMetadataDrift(_Model):
    metadata_type: Literal["FlexiPage"] = Field(alias="metadataType")
    member: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    component_name: str = Field(
        alias="componentName", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}:[A-Za-z][A-Za-z0-9_]{0,199}$"
    )
    component_identifier: str = Field(
        alias="componentIdentifier", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$"
    )
    property_name: str = Field(alias="propertyName", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    operation: Literal["SET_SCALAR"]
    baseline_value: StrictStr = Field(
        alias="baselineValue", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,99}$"
    )
    allowed_pre_states: tuple[
        Annotated[
            BrowserAbsentPropertyState | BrowserPresentPropertyState,
            Field(discriminator="state"),
        ],
        ...,
    ] = Field(alias="allowedPreStates", min_length=1, max_length=2)
    alternate_value: StrictStr = Field(
        alias="alternateValue", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,99}$"
    )
    restoration: Literal["PREIMAGE_EXACT"]
    maximum_files: Literal[1] = Field(alias="maximumFiles")
    maximum_bytes: int = Field(alias="maximumBytes", ge=1, le=1048576)

    @model_validator(mode="after")
    def validate_scalar_set(self) -> BrowserMetadataDrift:
        states = tuple(value.state for value in self.allowed_pre_states)
        if states != tuple(sorted(set(states))):
            raise ValueError("Allowed property prestates must be unique and sorted ABSENT, PRESENT")
        if self.alternate_value == self.baseline_value or any(
            value.state == "PRESENT" and value.value != self.baseline_value
            for value in self.allowed_pre_states
        ):
            raise ValueError(
                "Scalar SET must bind exact baseline prestates and a distinct alternate"
            )
        return self


class BrowserOriginalLocator(_Model):
    kind: Literal["ATTRIBUTE_EQUALS"]
    attribute: str = Field(pattern=r"^data-[a-z][a-z0-9-]{0,63}$")
    value: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


class BrowserFieldIdentity(_Model):
    kind: Literal["FIELD"]
    object_api_name: str = Field(alias="objectApiName", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    field_api_name: str = Field(alias="fieldApiName", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")


class BrowserActionIdentity(_Model):
    kind: Literal["ACTION"]
    object_api_name: str = Field(alias="objectApiName", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    action: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,199}$")


class BrowserLocatorObligation(_Model):
    obligation_id: str = Field(alias="obligationId", pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,199}$")
    original_locator: BrowserOriginalLocator = Field(alias="originalLocator")
    semantic_identity: BrowserFieldIdentity | BrowserActionIdentity = Field(
        alias="semanticIdentity", discriminator="kind"
    )
    assertions: tuple[Literal["EDITABLE", "ENABLED", "VISIBLE"], ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_read_only_assertions(self) -> BrowserLocatorObligation:
        expected = (
            ("EDITABLE", "ENABLED", "VISIBLE")
            if self.semantic_identity.kind == "FIELD"
            else ("ENABLED", "VISIBLE")
        )
        if self.assertions != expected:
            raise ValueError("Locator obligations require the complete sorted read-only assertions")
        return self


class BrowserLocatorRebind(_Model):
    navigation: BrowserRecordNavigation
    metadata_drift: BrowserMetadataDrift = Field(alias="metadataDrift")
    obligation_policy: Literal["COMPLETE_DECLARED_SET"] = Field(alias="obligationPolicy")
    obligations: tuple[BrowserLocatorObligation, ...] = Field(min_length=1, max_length=64)
    data_mutation: Literal["FORBIDDEN"] = Field(alias="dataMutation")

    @model_validator(mode="after")
    def validate_obligations(self) -> BrowserLocatorRebind:
        ids = tuple(value.obligation_id for value in self.obligations)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Locator obligations must be complete, sorted and unique")
        originals = [value.original_locator.model_dump_json() for value in self.obligations]
        semantics = [value.semantic_identity.model_dump_json() for value in self.obligations]
        if len(set(originals)) != len(originals) or len(set(semantics)) != len(semantics):
            raise ValueError("Locator identities cannot be shared across obligations")
        if any(
            value.semantic_identity.object_api_name != self.navigation.object_api_name
            for value in self.obligations
        ):
            raise ValueError("Locator identities must bind the exact navigation object")
        return self


class BrowserTarget(_Model):
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    application_intent: str = Field(alias="applicationIntent", min_length=1, max_length=200)
    action_class: str = Field(alias="actionClass", min_length=1, max_length=100)
    surface_intent: str = Field(alias="surfaceIntent", min_length=1, max_length=300)
    locator_rebind: BrowserLocatorRebind | None = Field(
        default=None, alias="locatorRebind", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_browser_target(self) -> BrowserTarget:
        _require_unique(self.source_entity_ids, "source entity bindings")
        if (self.action_class == "LOCATOR_REBIND_PREVIEW") != (self.locator_rebind is not None):
            raise ValueError("Locator rebind requires its complete closed source contract only")
        return self


class DatasetPredicate(_Model):
    field: str = Field(min_length=1, max_length=200)
    operator: Literal["EQUALS", "IN_SET"]
    value_source: Literal["SOURCE_LITERAL_SET", "CURRENT_ENROLLED_ACTOR"] = Field(
        alias="valueSource"
    )
    values: tuple[str, ...] = Field(max_length=4096)

    @model_validator(mode="after")
    def validate_values(self) -> DatasetPredicate:
        if self.value_source == "CURRENT_ENROLLED_ACTOR":
            if self.operator != "EQUALS" or self.values:
                raise ValueError("Current actor predicate must be EQUALS with no source literals")
        elif (
            (self.operator == "EQUALS" and len(self.values) != 1)
            or not self.values
            or self.values != tuple(sorted(set(self.values)))
            or any(not value or len(value) > 1000 for value in self.values)
        ):
            raise ValueError("Source literal set must be explicit, bounded, sorted and unique")
        return self


class DatasetTarget(_Model):
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    object_api_name: str = Field(alias="objectApiName", min_length=1, max_length=200)
    field_projection: tuple[str, ...] = Field(alias="fieldProjection", min_length=1)
    predicates: tuple[DatasetPredicate, ...] = Field(min_length=1)
    ownership_marker_field: str = Field(alias="ownershipMarkerField", min_length=1, max_length=200)
    maximum_records: int = Field(alias="maximumRecords", ge=1)
    dataset_id: str = Field(alias="datasetId", min_length=1, max_length=200)
    identity_field: str = Field(alias="identityField", min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_dataset_target(self) -> DatasetTarget:
        _require_unique(self.source_entity_ids, "source entity bindings")
        _require_unique(self.field_projection, "dataset fields")
        predicate_fields = tuple(item.field for item in self.predicates)
        _require_unique(predicate_fields, "dataset predicates")
        if not set(predicate_fields).issubset(self.field_projection):
            raise ValueError("Dataset predicate fields must be projected")
        markers = [item for item in self.predicates if item.field == self.ownership_marker_field]
        actors = [item for item in self.predicates if item.value_source == "CURRENT_ENROLLED_ACTOR"]
        if (
            self.identity_field not in self.field_projection
            or len(markers) != 1
            or markers[0].value_source != "SOURCE_LITERAL_SET"
            or len(markers[0].values) != self.maximum_records
            or len(actors) != 1
        ):
            raise ValueError(
                "Dataset requires exact marker membership, projected identity and actor ownership"
            )
        return self


class SourceOperationProfile(_Model):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    profile_id: str = Field(alias="profileId", min_length=1, max_length=200)
    profile_version: str = Field(alias="profileVersion")
    scope_artifact_sha256: str = Field(alias="scopeArtifactSha256", pattern=r"^[a-f0-9]{64}$")
    standard_rest: tuple[RestTarget, ...] = Field(alias="standardRest")
    custom_rest: tuple[RestTarget, ...] = Field(alias="customRest")
    metadata: tuple[MetadataTarget, ...]
    apex_tests: tuple[ApexTestTarget, ...] = Field(alias="apexTests")
    browser_intents: tuple[BrowserTarget, ...] = Field(alias="browserIntents")
    synthetic_datasets: tuple[DatasetTarget, ...] = Field(alias="syntheticDatasets")

    @model_validator(mode="after")
    def validate_profile(self) -> SourceOperationProfile:
        _require_semver(self.profile_version)
        datasets = {value.dataset_id: value for value in self.synthetic_datasets}
        if len(datasets) != len(self.synthetic_datasets):
            raise ValueError("Dataset identities must be unique")
        for browser in self.browser_intents:
            if browser.locator_rebind is not None:
                validate_browser_dataset(browser.locator_rebind, datasets)
        return self


def validate_browser_dataset(rebind: BrowserLocatorRebind, datasets: dict[str, Any]) -> None:
    navigation = rebind.navigation
    dataset = datasets.get(navigation.dataset_id)
    if dataset is None or (
        navigation.object_api_name != dataset.object_api_name
        or navigation.identity_field != dataset.identity_field
        or navigation.marker_field != dataset.ownership_marker_field
        or not any(
            value.field == navigation.marker_field
            and value.value_source == "SOURCE_LITERAL_SET"
            and navigation.marker_value in value.values
            for value in dataset.predicates
        )
    ):
        raise ValueError(
            "Browser navigation must resolve an exact declared authorized dataset member"
        )


class SemanticScopeEntity(_Model):
    entity_id: str = Field(alias="entityId", min_length=1, max_length=4600)
    canonical_class: str = Field(alias="canonicalClass", min_length=1, max_length=200)


class VerifiedLiveTargetScope(_Model):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    producer_kind: Literal["HOST_VERIFIED_CHANGE_GRAPH_SCOPE"] = Field(alias="producerKind")
    complete: Literal[True]
    project_id: str = Field(alias="projectId", min_length=1, max_length=200)
    source_snapshot_sha256: str = Field(alias="sourceSnapshotSha256", pattern=r"^[a-f0-9]{64}$")
    verified_change_sha256: str = Field(alias="verifiedChangeSha256", pattern=r"^[a-f0-9]{64}$")
    semantic_graph_sha256: str = Field(alias="semanticGraphSha256", pattern=r"^[a-f0-9]{64}$")
    ontology_sha256: str = Field(alias="ontologySha256", pattern=r"^[a-f0-9]{64}$")
    source_profile_sha256: str = Field(alias="sourceProfileSha256", pattern=r"^[a-f0-9]{64}$")
    entities: tuple[SemanticScopeEntity, ...] = Field(min_length=1)
    mandatory_obligation_ids: tuple[str, ...] = Field(alias="mandatoryObligationIds")
    valid_until: str = Field(alias="validUntil")
    artifact_sha256: str = Field(alias="artifactSha256", pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_scope(self) -> VerifiedLiveTargetScope:
        entity_keys = tuple((item.canonical_class, item.entity_id) for item in self.entities)
        if entity_keys != tuple(sorted(set(entity_keys))):
            raise ValueError("Scope entities must be sorted and unique")
        _require_unique(tuple(item.entity_id for item in self.entities), "scope entity IDs")
        if self.mandatory_obligation_ids != tuple(sorted(set(self.mandatory_obligation_ids))):
            raise ValueError("Mandatory obligations must be sorted and unique")
        _parse_timestamp(self.valid_until)
        _verify_digest(self, "artifact_sha256")
        return self


class LiveTargetCapture(_Model):
    source_operation_profile_bytes: bytes = Field(
        alias="sourceOperationProfileBytes", min_length=2, max_length=1_048_576
    )
    verified_scope: VerifiedLiveTargetScope = Field(alias="verifiedScope")
    organization_classification_receipt_sha256: str = Field(
        alias="organizationClassificationReceiptSha256", pattern=r"^[a-f0-9]{64}$"
    )
    organization_environment_class: str = Field(
        alias="organizationEnvironmentClass", min_length=1, max_length=100
    )
    organization_receipt_valid_until: str = Field(alias="organizationReceiptValidUntil")


class LiveTargetInputPort(Protocol):
    def capture(self) -> LiveTargetCapture: ...


class LiveTargetPolicy(_Model):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acceptance_profile_sha256: str = Field(
        alias="acceptanceProfileSha256", pattern=r"^[a-f0-9]{64}$"
    )
    producer_id: str = Field(alias="producerId", min_length=1, max_length=200)
    producer_version: str = Field(alias="producerVersion")
    producer_implementation_sha256: str = Field(
        alias="producerImplementationSha256", pattern=r"^[a-f0-9]{64}$"
    )
    valid_until: str = Field(alias="validUntil")
    maximum_plan_freshness_seconds: int = Field(alias="maximumPlanFreshnessSeconds", ge=1, le=3600)
    allowed_environment_classes: tuple[
        Literal["DEVELOPER_EDITION", "SANDBOX", "SCRATCH_ORG"], ...
    ] = Field(alias="allowedEnvironmentClasses", min_length=1)
    required_partitions: tuple[
        Literal[
            "STANDARD_REST",
            "CUSTOM_REST",
            "METADATA",
            "APEX_TEST",
            "BROWSER_INTENT",
            "SYNTHETIC_DATASET",
        ],
        ...,
    ] = Field(alias="requiredPartitions", min_length=1)
    authorized_target_sha256s: tuple[str, ...] = Field(alias="authorizedTargetSha256s")
    allowed_standard_route_prefixes: tuple[str, ...] = Field(alias="allowedStandardRoutePrefixes")
    allowed_custom_route_prefixes: tuple[str, ...] = Field(alias="allowedCustomRoutePrefixes")
    allowed_metadata_types: tuple[str, ...] = Field(alias="allowedMetadataTypes")
    allowed_browser_action_classes: tuple[
        Literal[
            "EXPAND_READ_ONLY",
            "FOCUS",
            "LOCATOR_REBIND_PREVIEW",
            "NAVIGATE",
            "RELOAD",
            "SCROLL",
            "SELECT_READ_ONLY_VIEW",
        ],
        ...,
    ] = Field(alias="allowedBrowserActionClasses")
    allowed_application_intents: tuple[str, ...] = Field(alias="allowedApplicationIntents")
    maximum_targets: int = Field(alias="maximumTargets", ge=1)
    maximum_response_bytes: int = Field(alias="maximumResponseBytes", ge=1)
    maximum_metadata_files: int = Field(alias="maximumMetadataFiles", ge=1)
    maximum_metadata_bytes: int = Field(alias="maximumMetadataBytes", ge=1)
    maximum_test_seconds: int = Field(alias="maximumTestSeconds", ge=1)
    maximum_dataset_records: int = Field(alias="maximumDatasetRecords", ge=1)

    @model_validator(mode="after")
    def validate_policy(self) -> LiveTargetPolicy:
        _require_semver(self.policy_version)
        _require_semver(self.producer_version)
        _parse_timestamp(self.valid_until)
        for values, label in (
            (self.allowed_environment_classes, "environment classes"),
            (self.required_partitions, "required partitions"),
            (self.authorized_target_sha256s, "authorized targets"),
            (self.allowed_standard_route_prefixes, "standard route prefixes"),
            (self.allowed_custom_route_prefixes, "custom route prefixes"),
            (self.allowed_metadata_types, "metadata types"),
            (self.allowed_browser_action_classes, "browser action classes"),
            (self.allowed_application_intents, "application intents"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"Policy {label} must be sorted and unique")
        if any(
            not re.fullmatch(r"[a-f0-9]{64}", value) for value in self.authorized_target_sha256s
        ):
            raise ValueError("Authorized targets must be lowercase SHA-256 digests")
        if set(self.allowed_metadata_types) & _SECRET_METADATA_TYPES:
            raise ValueError("Secret-bearing metadata families cannot be authorized")
        if self.sha256 != contract_sha256(self.model_dump(by_alias=True, mode="json")):
            raise ValueError("Live-target policy digest mismatch")
        return self


class TargetPartition(StrEnum):
    STANDARD_REST = "STANDARD_REST"
    CUSTOM_REST = "CUSTOM_REST"
    METADATA = "METADATA"
    APEX_TEST = "APEX_TEST"
    BROWSER_INTENT = "BROWSER_INTENT"
    SYNTHETIC_DATASET = "SYNTHETIC_DATASET"


class LiveTargetGapCode(StrEnum):
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    BROAD_DATA_SCOPE = "BROAD_DATA_SCOPE"
    DENIED_TARGET = "DENIED_TARGET"
    DUPLICATE_TARGET = "DUPLICATE_TARGET"
    EXPIRED_INPUT = "EXPIRED_INPUT"
    MISSING_PARTITION = "MISSING_PARTITION"
    OMITTED_POLICY_TARGET = "OMITTED_POLICY_TARGET"
    OMITTED_SCOPE_ENTITY = "OMITTED_SCOPE_ENTITY"
    OBLIGATION_SCOPE_MISMATCH = "OBLIGATION_SCOPE_MISMATCH"
    ORGANIZATION_CLASS_BLOCKED = "ORGANIZATION_CLASS_BLOCKED"
    PROFILE_SCOPE_MISMATCH = "PROFILE_SCOPE_MISMATCH"
    UNSUPPORTED_TARGET = "UNSUPPORTED_TARGET"
    UNKNOWN_SCOPE_ENTITY = "UNKNOWN_SCOPE_ENTITY"
    ZERO_TARGETS = "ZERO_TARGETS"
    LOCAL_VALIDATION_REQUIRED = "LOCAL_VALIDATION_REQUIRED"


class LiveTargetGap(_Model):
    code: LiveTargetGapCode
    partition: TargetPartition | None = None
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    blocking: Literal[True] = True


class PlannedTarget(_Model):
    partition: TargetPartition
    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_entity_ids: tuple[str, ...]
    specification: dict[str, Any]

    @model_validator(mode="after")
    def validate_target(self) -> PlannedTarget:
        if self.source_entity_ids != tuple(sorted(set(self.source_entity_ids))):
            raise ValueError("Planned target source entities must be sorted and unique")
        expected = _target_sha256(
            self.partition,
            {"sourceEntityIds": list(self.source_entity_ids), **self.specification},
        )
        if self.target_sha256 != expected:
            raise ValueError("Planned target digest mismatch")
        model = {
            TargetPartition.STANDARD_REST: RestTarget,
            TargetPartition.CUSTOM_REST: RestTarget,
            TargetPartition.METADATA: MetadataTarget,
            TargetPartition.APEX_TEST: ApexTestTarget,
            TargetPartition.BROWSER_INTENT: BrowserTarget,
            TargetPartition.SYNTHETIC_DATASET: DatasetTarget,
        }[self.partition]
        model.model_validate(
            {"sourceEntityIds": list(self.source_entity_ids), **self.specification}
        )
        return self


class LiveTargetPlan(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    producer_kind: Literal["HOST_OWNED_LIVE_TARGET_PLAN_PRODUCER"] = (
        "HOST_OWNED_LIVE_TARGET_PLAN_PRODUCER"
    )
    authority_scope: Literal["TARGET_SELECTION_ONLY"] = "TARGET_SELECTION_ONLY"
    authorizes_execution: Literal[False] = False
    all_or_block_authorized: Literal[True] = True
    project_id: str
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_change_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_source_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_operation_profile_id: str
    source_operation_profile_version: str
    source_operation_profile_bytes_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_operation_profile_canonical_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    host_policy_id: str
    host_policy_version: str
    host_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acceptance_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    producer_id: str
    producer_version: str
    producer_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    organization_classification_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    valid_until: str
    targets: tuple[PlannedTarget, ...] = Field(min_length=1)
    local_validation_phase_policy_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    evidence_phase: Literal[EvidencePhase.LIVE_BASELINE] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    deferred_local_validations: tuple[DeferredLocalValidation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    phase_read_only_gate_ids: tuple[str, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_plan(self) -> LiveTargetPlan:
        if (self.local_validation_phase_policy_sha256 is not None) != bool(
            self.phase_read_only_gate_ids
        ) or (
            self.phase_read_only_gate_ids
            and self.phase_read_only_gate_ids != READ_ONLY_BASELINE_GATES
        ):
            raise ValueError("Phase-scoped plans permit only the fixed read-only baseline gates")
        if self.deferred_local_validations:
            if (
                self.evidence_phase is not EvidencePhase.LIVE_BASELINE
                or self.local_validation_phase_policy_sha256 is None
            ):
                raise ValueError("Deferred local work requires an exact baseline phase policy")
            ids = tuple(value.obligation_id for value in self.deferred_local_validations)
            if ids != tuple(sorted(set(ids))) or any(
                value.phase_policy_sha256 != self.local_validation_phase_policy_sha256
                for value in self.deferred_local_validations
            ):
                raise ValueError("Deferred local work is duplicated or differs from phase policy")
        keys = tuple((item.partition.value, item.target_sha256) for item in self.targets)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Plan targets must be sorted and unique")
        if _parse_timestamp(self.valid_until) <= _parse_timestamp(self.evaluated_at):
            raise ValueError("Live target plan is expired")
        _verify_digest(self, "plan_sha256")
        return self


class LiveTargetPlanEvaluation(_Model):
    state: Literal["READY", "BLOCKED"]
    authority_scope: Literal["TARGET_SELECTION_ONLY"] = "TARGET_SELECTION_ONLY"
    authorizes_execution: Literal[False] = False
    source_operation_profile_bytes_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_operation_profile_canonical_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    scope_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    host_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposed_target_sha256s: tuple[str, ...]
    authorized_target_sha256s: tuple[str, ...]
    plan: LiveTargetPlan | None
    gaps: tuple[LiveTargetGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> LiveTargetPlanEvaluation:
        if self.proposed_target_sha256s != tuple(sorted(set(self.proposed_target_sha256s))):
            raise ValueError("Proposed target identities must be sorted and unique")
        if self.authorized_target_sha256s != tuple(sorted(set(self.authorized_target_sha256s))):
            raise ValueError("Authorized target identities must be sorted and unique")
        if (self.state == "READY") is not (self.plan is not None and not self.gaps):
            raise ValueError("Evaluation state differs from plan and gaps")
        if self.gaps and self.authorized_target_sha256s:
            raise ValueError("Blocked evaluation cannot contain a partial authorization")
        _verify_digest(self, "evaluation_sha256")
        return self


class HostOwnedLiveTargetPlanProducer:
    """Compile a complete plan from host-captured inputs; callers cannot supply targets."""

    def __init__(
        self,
        input_port: LiveTargetInputPort,
        policy: LiveTargetPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._input_port = input_port
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))
        if policy.producer_implementation_sha256 != _producer_implementation_sha256():
            raise LiveTargetContractError("Live-target producer implementation pin mismatch")

    def produce(self) -> LiveTargetPlanEvaluation:
        capture = self._input_port.capture()
        now = aware_utc(self._clock()).replace(microsecond=0)
        raw = capture.source_operation_profile_bytes
        raw_sha = hashlib.sha256(raw).hexdigest()
        document = _load_strict_json_bytes(raw)
        try:
            profile = SourceOperationProfile.model_validate(document)
        except ValidationError as exc:
            raise LiveTargetContractError("Source-operation profile schema is invalid") from exc
        canonical_sha = _unordered_canonical_sha256(profile.model_dump(by_alias=True, mode="json"))

        proposed = self._compile_targets(profile)
        gaps = self._validate(capture, profile, proposed, now)
        proposed_ids = tuple(sorted({item.target_sha256 for item in proposed}))
        if gaps:
            return _evaluation(
                raw_sha,
                canonical_sha,
                capture.verified_scope.artifact_sha256,
                self._policy.sha256,
                proposed_ids,
                (),
                None,
                gaps,
            )

        valid_until = min(
            now.timestamp() + self._policy.maximum_plan_freshness_seconds,
            _parse_timestamp(self._policy.valid_until).timestamp(),
            _parse_timestamp(capture.verified_scope.valid_until).timestamp(),
            _parse_timestamp(capture.organization_receipt_valid_until).timestamp(),
        )
        scope = capture.verified_scope
        plan = _with_digest(
            LiveTargetPlan,
            "plan_sha256",
            project_id=scope.project_id,
            source_snapshot_sha256=scope.source_snapshot_sha256,
            verified_change_sha256=scope.verified_change_sha256,
            semantic_graph_sha256=scope.semantic_graph_sha256,
            ontology_sha256=scope.ontology_sha256,
            graph_source_profile_sha256=scope.source_profile_sha256,
            scope_artifact_sha256=scope.artifact_sha256,
            source_operation_profile_id=profile.profile_id,
            source_operation_profile_version=profile.profile_version,
            source_operation_profile_bytes_sha256=raw_sha,
            source_operation_profile_canonical_sha256=canonical_sha,
            host_policy_id=self._policy.policy_id,
            host_policy_version=self._policy.policy_version,
            host_policy_sha256=self._policy.sha256,
            acceptance_profile_sha256=self._policy.acceptance_profile_sha256,
            producer_id=self._policy.producer_id,
            producer_version=self._policy.producer_version,
            producer_implementation_sha256=self._policy.producer_implementation_sha256,
            organization_classification_receipt_sha256=(
                capture.organization_classification_receipt_sha256
            ),
            evaluated_at=_timestamp(now),
            valid_until=_timestamp(datetime.fromtimestamp(valid_until, UTC)),
            targets=proposed,
        )
        return _evaluation(
            raw_sha,
            canonical_sha,
            scope.artifact_sha256,
            self._policy.sha256,
            proposed_ids,
            proposed_ids,
            plan,
            (),
        )

    def _compile_targets(self, profile: SourceOperationProfile) -> tuple[PlannedTarget, ...]:
        groups: tuple[tuple[TargetPartition, tuple[_Model, ...]], ...] = (
            (TargetPartition.STANDARD_REST, profile.standard_rest),
            (TargetPartition.CUSTOM_REST, profile.custom_rest),
            (TargetPartition.METADATA, profile.metadata),
            (TargetPartition.APEX_TEST, profile.apex_tests),
            (TargetPartition.BROWSER_INTENT, profile.browser_intents),
            (TargetPartition.SYNTHETIC_DATASET, profile.synthetic_datasets),
        )
        targets: list[PlannedTarget] = []
        for partition, values in groups:
            for value in values:
                specification = _normalize_unordered(value.model_dump(by_alias=True, mode="json"))
                source_ids = tuple(sorted(set(specification.pop("sourceEntityIds"))))
                targets.append(
                    PlannedTarget(
                        partition=partition,
                        target_sha256=_target_sha256(
                            partition,
                            {"sourceEntityIds": list(source_ids), **specification},
                        ),
                        source_entity_ids=source_ids,
                        specification=specification,
                    )
                )
        return tuple(sorted(targets, key=lambda item: (item.partition.value, item.target_sha256)))

    def _validate(
        self,
        capture: LiveTargetCapture,
        profile: SourceOperationProfile,
        targets: tuple[PlannedTarget, ...],
        now: datetime,
    ) -> tuple[LiveTargetGap, ...]:
        gaps: list[LiveTargetGap] = []
        scope = capture.verified_scope

        def add(
            code: LiveTargetGapCode,
            *identity: Any,
            partition: TargetPartition | None = None,
        ) -> None:
            gaps.append(
                LiveTargetGap(
                    code=code,
                    partition=partition,
                    identity_sha256=_canonical_sha256(list(identity)),
                )
            )

        if profile.scope_artifact_sha256 != scope.artifact_sha256:
            add(LiveTargetGapCode.PROFILE_SCOPE_MISMATCH, profile.scope_artifact_sha256)
        expiries = (
            self._policy.valid_until,
            scope.valid_until,
            capture.organization_receipt_valid_until,
        )
        if any(_parse_timestamp(value) <= now for value in expiries):
            add(LiveTargetGapCode.EXPIRED_INPUT, *expiries)
        if capture.organization_environment_class not in self._policy.allowed_environment_classes:
            add(
                LiveTargetGapCode.ORGANIZATION_CLASS_BLOCKED,
                capture.organization_environment_class,
            )
        if not targets:
            add(LiveTargetGapCode.ZERO_TARGETS)
        if len(targets) > self._policy.maximum_targets:
            add(LiveTargetGapCode.UNSUPPORTED_TARGET, "TARGET_CAP", len(targets))

        target_ids = [item.target_sha256 for item in targets]
        for duplicate in sorted({value for value in target_ids if target_ids.count(value) > 1}):
            add(LiveTargetGapCode.DUPLICATE_TARGET, duplicate)
        partitions = {item.partition.value for item in targets}
        for required in self._policy.required_partitions:
            if required not in partitions:
                add(LiveTargetGapCode.MISSING_PARTITION, required)

        scope_ids = {item.entity_id for item in scope.entities}
        covered_ids = {value for target in targets for value in target.source_entity_ids}
        for unknown in sorted(covered_ids - scope_ids):
            add(LiveTargetGapCode.UNKNOWN_SCOPE_ENTITY, unknown)
        for omitted in sorted(scope_ids - covered_ids):
            add(LiveTargetGapCode.OMITTED_SCOPE_ENTITY, omitted)

        apex_obligations = {
            str(item.specification["obligationId"])
            for item in targets
            if item.partition is TargetPartition.APEX_TEST
        }
        expected_obligations = set(scope.mandatory_obligation_ids)
        if apex_obligations != expected_obligations:
            for value in sorted(apex_obligations ^ expected_obligations):
                add(LiveTargetGapCode.OBLIGATION_SCOPE_MISMATCH, value)

        allowed_targets = set(self._policy.authorized_target_sha256s)
        proposed_targets = set(target_ids)
        for omitted_target in sorted(allowed_targets - proposed_targets):
            add(LiveTargetGapCode.OMITTED_POLICY_TARGET, omitted_target)
        for target in targets:
            if target.target_sha256 not in allowed_targets:
                add(
                    LiveTargetGapCode.DENIED_TARGET,
                    target.target_sha256,
                    partition=target.partition,
                )
            self._validate_target(target, add)
        return tuple(sorted(set(gaps), key=lambda item: (item.code.value, item.identity_sha256)))

    def _validate_target(self, target: PlannedTarget, add: Callable[..., None]) -> None:
        spec = target.specification
        partition = target.partition
        if partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}:
            route = str(spec["routeTemplate"])
            prefixes = (
                self._policy.allowed_standard_route_prefixes
                if partition is TargetPartition.STANDARD_REST
                else self._policy.allowed_custom_route_prefixes
            )
            if not _safe_route(route) or not any(route.startswith(prefix) for prefix in prefixes):
                add(LiveTargetGapCode.UNSUPPORTED_TARGET, target.target_sha256, partition=partition)
            if int(spec["maximumResponseBytes"]) > self._policy.maximum_response_bytes:
                add(
                    LiveTargetGapCode.UNSUPPORTED_TARGET,
                    target.target_sha256,
                    "BYTE_CAP",
                    partition=partition,
                )
        elif partition is TargetPartition.METADATA:
            if (
                spec["metadataType"] not in self._policy.allowed_metadata_types
                or _contains_wildcard(str(spec["member"]))
                or int(spec["maximumFiles"]) > self._policy.maximum_metadata_files
                or int(spec["maximumBytes"]) > self._policy.maximum_metadata_bytes
            ):
                add(LiveTargetGapCode.UNSUPPORTED_TARGET, target.target_sha256, partition=partition)
        elif partition is TargetPartition.APEX_TEST:
            if (
                not _SF_IDENTIFIER.fullmatch(str(spec["apexClass"]))
                or (
                    spec["methodName"] is not None
                    and not _SF_IDENTIFIER.fullmatch(str(spec["methodName"]))
                )
                or int(spec["maximumSeconds"]) > self._policy.maximum_test_seconds
            ):
                add(LiveTargetGapCode.UNSUPPORTED_TARGET, target.target_sha256, partition=partition)
        elif partition is TargetPartition.BROWSER_INTENT:
            if (
                spec["actionClass"] not in self._policy.allowed_browser_action_classes
                or spec["applicationIntent"] not in self._policy.allowed_application_intents
            ):
                add(LiveTargetGapCode.UNSUPPORTED_TARGET, target.target_sha256, partition=partition)
            rebind = spec.get("locatorRebind")
            if rebind is not None:
                drift = rebind["metadataDrift"]
                if (
                    drift["metadataType"] not in self._policy.allowed_metadata_types
                    or drift["maximumFiles"] > self._policy.maximum_metadata_files
                    or drift["maximumBytes"] > self._policy.maximum_metadata_bytes
                ):
                    add(
                        LiveTargetGapCode.UNSUPPORTED_TARGET,
                        target.target_sha256,
                        partition=partition,
                    )
        elif partition is TargetPartition.SYNTHETIC_DATASET:
            fields = tuple(str(value) for value in spec["fieldProjection"])
            predicates = tuple(spec["predicates"])
            unsafe = (
                int(spec["maximumRecords"]) > self._policy.maximum_dataset_records
                or not fields
                or any(_contains_wildcard(value) for value in fields)
                or not predicates
                or any(item["operator"] not in {"EQUALS", "IN_SET"} for item in predicates)
                or not _SF_IDENTIFIER.fullmatch(str(spec["objectApiName"]))
                or any(not _SF_IDENTIFIER.fullmatch(value) for value in fields)
                or spec["ownershipMarkerField"] not in fields
            )
            if unsafe:
                add(LiveTargetGapCode.BROAD_DATA_SCOPE, target.target_sha256, partition=partition)


def load_live_target_policy(path: Path) -> LiveTargetPolicy:
    document = _load_strict_json_bytes(path.read_bytes())
    try:
        return LiveTargetPolicy.model_validate(document)
    except ValidationError as exc:
        raise LiveTargetContractError("Live-target policy schema is invalid") from exc


def derive_target_sha256(partition: TargetPartition, target: _Model) -> str:
    """Return the content identity a host policy must explicitly authorize."""

    specification = target.model_dump(by_alias=True, mode="json")
    specification["sourceEntityIds"] = sorted(set(specification["sourceEntityIds"]))
    return _target_sha256(partition, specification)


def _evaluation(
    raw_sha: str,
    canonical_sha: str,
    scope_sha: str,
    policy_sha: str,
    proposed: tuple[str, ...],
    authorized: tuple[str, ...],
    plan: LiveTargetPlan | None,
    gaps: tuple[LiveTargetGap, ...],
) -> LiveTargetPlanEvaluation:
    return _with_digest(
        LiveTargetPlanEvaluation,
        "evaluation_sha256",
        state="READY" if plan is not None else "BLOCKED",
        source_operation_profile_bytes_sha256=raw_sha,
        source_operation_profile_canonical_sha256=canonical_sha,
        scope_artifact_sha256=scope_sha,
        host_policy_sha256=policy_sha,
        proposed_target_sha256s=proposed,
        authorized_target_sha256s=authorized,
        plan=plan,
        gaps=gaps,
    )


def _load_strict_json_bytes(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise LiveTargetContractError(f"Duplicate JSON key is not allowed: {key}")
            value[key] = item
        return value

    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except LiveTargetContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveTargetContractError("Expected valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise LiveTargetContractError("Expected a JSON object")
    return document


def _with_digest(model: type[_Model], digest_field: str, **values: Any) -> Any:
    document = model.model_construct(**values).model_dump(mode="json")
    document[digest_field] = _canonical_sha256(document)
    return model.model_validate(document)


def _verify_digest(value: _Model, field: str) -> None:
    document = value.model_dump(mode="json")
    declared = document.pop(field)
    if declared != _canonical_sha256(document):
        raise ValueError(f"{field} mismatch")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _target_sha256(partition: TargetPartition, specification: dict[str, Any]) -> str:
    return _unordered_canonical_sha256(
        {"partition": partition.value, "specification": specification}
    )


def _unordered_canonical_sha256(value: Any) -> str:
    return _canonical_sha256(_normalize_unordered(value))


def _normalize_unordered(item: Any) -> Any:
    if isinstance(item, dict):
        return {key: _normalize_unordered(child) for key, child in item.items()}
    if isinstance(item, list | tuple):
        normalized = [_normalize_unordered(child) for child in item]
        return sorted(normalized, key=lambda child: json.dumps(child, sort_keys=True))
    return item


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = parse_aware_utc(value)
    except ValueError as exc:
        raise ValueError("Timestamp must be valid RFC 3339 UTC") from exc
    if parsed.microsecond:
        raise ValueError("Timestamp must use whole-second UTC")
    return parsed


def _timestamp(value: datetime) -> str:
    return aware_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_semver(value: str) -> None:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("Version must use semantic versioning")


def _safe_route(value: str) -> bool:
    if not value.startswith("/") or "\\" in value or "#" in value:
        return False
    if "*" in value or "//" in value or re.search(r"https?://|\s", value, re.IGNORECASE):
        return False
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    if ".." in PurePosixPath(parsed.path).parts:
        return False
    if not parsed.query:
        return True
    query_item = r"[A-Za-z_][A-Za-z0-9_.-]*=[A-Za-z0-9_.,~{}-]+"
    return (
        len(parsed.query) <= 512
        and re.fullmatch(rf"{query_item}(?:&{query_item})*", parsed.query) is not None
    )


def _contains_wildcard(value: str) -> bool:
    return "*" in value or value.strip() in {"", "ALL", "all"}


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Ambiguous duplicate {label}")


def _producer_implementation_sha256() -> str:
    implementation = inspect.getsource(HostOwnedLiveTargetPlanProducer).encode()
    temporal = Path(__file__).with_name("temporal.py").read_bytes()
    return hashlib.sha256(implementation + b"\x00" + temporal).hexdigest()


_SF_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SECRET_METADATA_TYPES = {
    "AuthProvider",
    "Certificate",
    "ConnectedApp",
    "ExternalCredential",
    "NamedCredential",
}
