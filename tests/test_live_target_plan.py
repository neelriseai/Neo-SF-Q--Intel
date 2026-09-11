from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

import neo_sf_q_intel.live_target_plan as live
from neo_sf_q_intel.live_target_plan import (
    HostOwnedLiveTargetPlanProducer,
    LiveTargetCapture,
    LiveTargetContractError,
    LiveTargetGapCode,
    LiveTargetPolicy,
    SemanticScopeEntity,
    SourceOperationProfile,
    TargetPartition,
    VerifiedLiveTargetScope,
    derive_target_sha256,
)
from neo_sf_q_intel.ontology import contract_sha256
from tests.test_candidate_target_compiler import _rebind_declaration

T0 = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
H = "a" * 64


class _Port:
    def __init__(self, capture: LiveTargetCapture) -> None:
        self.value = capture
        self.calls = 0

    def capture(self) -> LiveTargetCapture:
        self.calls += 1
        return self.value


def _scope(*, entities: tuple[str, ...] = ("entity:a", "entity:b")) -> VerifiedLiveTargetScope:
    return live._with_digest(
        VerifiedLiveTargetScope,
        "artifact_sha256",
        schema_version="1.0.0",
        producer_kind="HOST_VERIFIED_CHANGE_GRAPH_SCOPE",
        complete=True,
        project_id="renamable-project",
        source_snapshot_sha256="1" * 64,
        verified_change_sha256="2" * 64,
        semantic_graph_sha256="3" * 64,
        ontology_sha256="4" * 64,
        source_profile_sha256="5" * 64,
        entities=tuple(
            sorted(
                (
                    SemanticScopeEntity(entityId=value, canonicalClass="source-component")
                    for value in entities
                ),
                key=lambda item: (item.canonical_class, item.entity_id),
            )
        ),
        mandatory_obligation_ids=("obligation:a", "obligation:b"),
        valid_until="2026-09-10T09:00:00Z",
    )


def _profile(scope: VerifiedLiveTargetScope) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "profileId": "source-contract-profile",
        "profileVersion": "1.0.0",
        "scopeArtifactSha256": scope.artifact_sha256,
        "standardRest": [
            {
                "sourceEntityIds": ["entity:a"],
                "method": "GET",
                "routeTemplate": "/services/data/{apiVersion}/sobjects/{objectApiName}/{recordId}",
                "variables": [
                    {
                        "name": "apiVersion",
                        "valueSource": "API_VERSION",
                        "dataType": "STRING",
                        "datasetField": None,
                    },
                    {
                        "name": "objectApiName",
                        "valueSource": "SYNTHETIC_DATASET_RECEIPT",
                        "dataType": "STRING",
                        "datasetField": "Id",
                    },
                    {
                        "name": "recordId",
                        "valueSource": "SYNTHETIC_DATASET_RECEIPT",
                        "dataType": "ID",
                        "datasetField": "Id",
                    },
                ],
                "responseFields": [
                    {"path": "Id", "dataType": "STRING", "required": True, "nullable": False}
                ],
                "maximumResponseBytes": 8192,
                "datasetId": "synthetic",
                "minimumCardinality": 1,
                "maximumCardinality": 1,
                "requestExpansion": "EACH_DATASET_RECORD",
                "responseRecordPath": "",
                "datasetFieldPaths": {"Id": "Id"},
                "parentBindings": [],
            }
        ],
        "customRest": [
            {
                "sourceEntityIds": ["entity:b"],
                "method": "GET",
                "routeTemplate": "/services/apexrest/{resourcePath}/{recordId}",
                "variables": [
                    {
                        "name": "resourcePath",
                        "valueSource": "SYNTHETIC_DATASET_RECEIPT",
                        "dataType": "STRING",
                        "datasetField": "Id",
                    },
                    {
                        "name": "recordId",
                        "valueSource": "SYNTHETIC_DATASET_RECEIPT",
                        "dataType": "ID",
                        "datasetField": "Id",
                    },
                ],
                "responseFields": [
                    {"path": "result", "dataType": "OBJECT", "required": True, "nullable": False},
                    {"path": "Id", "dataType": "STRING", "required": True, "nullable": False},
                ],
                "maximumResponseBytes": 8192,
                "datasetId": "synthetic",
                "minimumCardinality": 1,
                "maximumCardinality": 1,
                "requestExpansion": "EACH_DATASET_RECORD",
                "responseRecordPath": "",
                "datasetFieldPaths": {"Id": "Id"},
                "parentBindings": [],
            }
        ],
        "metadata": [
            {
                "sourceEntityIds": ["entity:a", "entity:b"],
                "metadataType": "ApexClass",
                "member": "ContractSelectedClass",
                "maximumFiles": 4,
                "maximumBytes": 65536,
            }
        ],
        "apexTests": [
            {
                "sourceEntityIds": ["entity:a"],
                "obligationId": "obligation:a",
                "apexClass": "ContractSelectedTest",
                "methodName": "firstRequiredMethod",
                "maximumSeconds": 120,
            },
            {
                "sourceEntityIds": ["entity:b"],
                "obligationId": "obligation:b",
                "apexClass": "ContractSelectedTest",
                "methodName": "secondRequiredMethod",
                "maximumSeconds": 120,
            },
        ],
        "browserIntents": [
            {
                "sourceEntityIds": ["entity:a"],
                "applicationIntent": "contract-selected-application",
                "actionClass": "NAVIGATE",
                "surfaceIntent": "contract-selected-read-only-surface",
            }
        ],
        "syntheticDatasets": [
            {
                "sourceEntityIds": ["entity:a", "entity:b"],
                "objectApiName": "Contract_Selected__c",
                "fieldProjection": ["Id", "Ownership_Marker__c", "OwnerId"],
                "identityField": "Id",
                "predicates": [
                    {
                        "field": "Ownership_Marker__c",
                        "operator": "IN_SET",
                        "valueSource": "SOURCE_LITERAL_SET",
                        "values": [f"synthetic-{index:02d}" for index in range(25)],
                    },
                    {
                        "field": "OwnerId",
                        "operator": "EQUALS",
                        "valueSource": "CURRENT_ENROLLED_ACTOR",
                        "values": [],
                    },
                ],
                "ownershipMarkerField": "Ownership_Marker__c",
                "maximumRecords": 25,
                "datasetId": "synthetic",
            }
        ],
    }


def _targets(document: dict[str, Any]) -> tuple[str, ...]:
    profile = SourceOperationProfile.model_validate(document)
    values = (
        *((TargetPartition.STANDARD_REST, item) for item in profile.standard_rest),
        *((TargetPartition.CUSTOM_REST, item) for item in profile.custom_rest),
        *((TargetPartition.METADATA, item) for item in profile.metadata),
        *((TargetPartition.APEX_TEST, item) for item in profile.apex_tests),
        *((TargetPartition.BROWSER_INTENT, item) for item in profile.browser_intents),
        *((TargetPartition.SYNTHETIC_DATASET, item) for item in profile.synthetic_datasets),
    )
    return tuple(sorted(derive_target_sha256(partition, item) for partition, item in values))


def _policy(document: dict[str, Any], **changes: Any) -> LiveTargetPolicy:
    policy_document = {
        "schemaVersion": "1.0.0",
        "policyId": "test-host-policy",
        "policyVersion": "1.0.0",
        "sha256": "",
        "acceptanceProfileSha256": "9" * 64,
        "producerId": "host-owned-live-target-plan-producer",
        "producerVersion": "1.0.0",
        "producerImplementationSha256": live._producer_implementation_sha256(),
        "validUntil": "2026-09-10T09:00:00Z",
        "maximumPlanFreshnessSeconds": 300,
        "allowedEnvironmentClasses": ["DEVELOPER_EDITION", "SANDBOX", "SCRATCH_ORG"],
        "requiredPartitions": [
            "APEX_TEST",
            "BROWSER_INTENT",
            "CUSTOM_REST",
            "METADATA",
            "STANDARD_REST",
            "SYNTHETIC_DATASET",
        ],
        "authorizedTargetSha256s": list(set(_targets(document))),
        "allowedStandardRoutePrefixes": ["/services/data/"],
        "allowedCustomRoutePrefixes": ["/services/apexrest/"],
        "allowedMetadataTypes": ["ApexClass"],
        "allowedBrowserActionClasses": ["NAVIGATE"],
        "allowedApplicationIntents": ["contract-selected-application"],
        "maximumTargets": 32,
        "maximumResponseBytes": 65536,
        "maximumMetadataFiles": 32,
        "maximumMetadataBytes": 1048576,
        "maximumTestSeconds": 300,
        "maximumDatasetRecords": 50,
        **changes,
    }
    for key in (
        "allowedEnvironmentClasses",
        "requiredPartitions",
        "authorizedTargetSha256s",
        "allowedStandardRoutePrefixes",
        "allowedCustomRoutePrefixes",
        "allowedMetadataTypes",
        "allowedBrowserActionClasses",
        "allowedApplicationIntents",
    ):
        policy_document[key] = sorted(policy_document[key])
    policy_document["sha256"] = contract_sha256(policy_document)
    return LiveTargetPolicy.model_validate(policy_document)


def _producer(
    document: dict[str, Any],
    scope: VerifiedLiveTargetScope,
    *,
    policy_changes: dict[str, Any] | None = None,
) -> tuple[HostOwnedLiveTargetPlanProducer, _Port]:
    raw = json.dumps(document, separators=(",", ":")).encode()
    capture = LiveTargetCapture(
        sourceOperationProfileBytes=raw,
        verifiedScope=scope,
        organizationClassificationReceiptSha256="6" * 64,
        organizationEnvironmentClass="DEVELOPER_EDITION",
        organizationReceiptValidUntil="2026-09-10T09:00:00Z",
    )
    port = _Port(capture)
    return (
        HostOwnedLiveTargetPlanProducer(
            port,
            _policy(document, **(policy_changes or {})),
            clock=lambda: T0,
        ),
        port,
    )


def _codes(result: live.LiveTargetPlanEvaluation) -> set[LiveTargetGapCode]:
    return {gap.code for gap in result.gaps}


def _rebind_profile(scope):
    document = _profile(scope)
    rebind = _rebind_declaration()
    rebind["navigation"].update(
        {
            "objectApiName": "Contract_Selected__c",
            "markerField": "Ownership_Marker__c",
            "markerValue": "synthetic-00",
        }
    )
    for value in rebind["obligations"]:
        value["semanticIdentity"]["objectApiName"] = "Contract_Selected__c"
    document["browserIntents"][0].update(
        {
            "actionClass": "LOCATOR_REBIND_PREVIEW",
            "locatorRebind": rebind,
        }
    )
    return document


def test_complete_rebind_plan_preserves_every_obligation_without_execution_authority() -> None:
    scope = _scope()
    document = _rebind_profile(scope)
    producer, _ = _producer(
        document,
        scope,
        policy_changes={
            "allowedBrowserActionClasses": ["LOCATOR_REBIND_PREVIEW"],
            "allowedMetadataTypes": ["ApexClass", "FlexiPage"],
        },
    )
    result = producer.produce()
    assert result.state == "READY"
    assert not result.plan.authorizes_execution
    target = next(
        value for value in result.plan.targets if value.partition is TargetPartition.BROWSER_INTENT
    )
    assert target.specification["locatorRebind"] == document["browserIntents"][0]["locatorRebind"]


def test_rebind_obligation_subset_does_not_inherit_complete_host_policy() -> None:
    scope = _scope()
    document = _rebind_profile(scope)
    producer, port = _producer(
        document,
        scope,
        policy_changes={
            "allowedBrowserActionClasses": ["LOCATOR_REBIND_PREVIEW"],
            "allowedMetadataTypes": ["ApexClass", "FlexiPage"],
        },
    )
    document["browserIntents"][0]["locatorRebind"]["obligations"].pop()
    port.value = port.value.model_copy(
        update={
            "source_operation_profile_bytes": json.dumps(document).encode(),
        }
    )
    result = producer.produce()
    assert result.plan is None
    assert {LiveTargetGapCode.DENIED_TARGET, LiveTargetGapCode.OMITTED_POLICY_TARGET}.issubset(
        _codes(result)
    )


def test_rebind_metadata_scope_requires_separate_host_type_and_byte_allowance() -> None:
    scope = _scope()
    document = _rebind_profile(scope)
    for policy_changes in (
        {"allowedMetadataTypes": ["ApexClass"]},
        {"allowedMetadataTypes": ["ApexClass", "FlexiPage"], "maximumMetadataBytes": 8192},
    ):
        producer, _ = _producer(
            document,
            scope,
            policy_changes={
                **policy_changes,
                "allowedBrowserActionClasses": ["LOCATOR_REBIND_PREVIEW"],
            },
        )
        result = producer.produce()
        assert result.plan is None
        assert LiveTargetGapCode.UNSUPPORTED_TARGET in _codes(result)


def test_complete_host_authorized_plan_binds_raw_and_canonical_profile() -> None:
    scope = _scope()
    document = _profile(scope)
    producer, port = _producer(document, scope)

    result = producer.produce()

    assert result.state == "READY"
    assert result.plan is not None
    assert result.plan.authorizes_execution is False
    assert result.plan.all_or_block_authorized is True
    assert result.plan.scope_artifact_sha256 == scope.artifact_sha256
    assert (
        result.plan.source_operation_profile_bytes_sha256
        == hashlib.sha256(port.value.source_operation_profile_bytes).hexdigest()
    )
    expected_profile_sha = live._unordered_canonical_sha256(document)
    assert result.plan.source_operation_profile_canonical_sha256 == expected_profile_sha
    assert len(result.plan.targets) == 7
    assert port.calls == 1


def test_permuted_profile_has_same_canonical_identity_and_target_partition() -> None:
    scope = _scope()
    first = _profile(scope)
    second = json.loads(json.dumps(first))
    second["apexTests"].reverse()
    second["standardRest"][0]["variables"].reverse()
    second["metadata"][0]["sourceEntityIds"].reverse()
    second["syntheticDatasets"][0]["fieldProjection"].reverse()
    second = dict(reversed(tuple(second.items())))
    first_result = _producer(first, scope)[0].produce()
    second_result = _producer(second, scope)[0].produce()

    assert first_result.state == second_result.state == "READY"
    assert first_result.source_operation_profile_canonical_sha256 == (
        second_result.source_operation_profile_canonical_sha256
    )
    assert first_result.proposed_target_sha256s == second_result.proposed_target_sha256s
    assert first_result.plan is not None and second_result.plan is not None
    assert first_result.plan.targets == second_result.plan.targets
    assert first_result.source_operation_profile_bytes_sha256 != (
        second_result.source_operation_profile_bytes_sha256
    )


def test_consistent_entity_rename_remains_generic_and_ready() -> None:
    old_scope = _scope()
    document = _profile(old_scope)
    renamed_scope = _scope(entities=("entity:renamed-a", "entity:renamed-b"))
    renamed = json.loads(
        json.dumps(document)
        .replace("entity:a", "entity:renamed-a")
        .replace("entity:b", "entity:renamed-b")
    )
    renamed["scopeArtifactSha256"] = renamed_scope.artifact_sha256

    result = _producer(renamed, renamed_scope)[0].produce()

    assert result.state == "READY"
    assert result.plan is not None
    assert result.plan.project_id == "renamable-project"


def test_duplicate_json_key_and_unknown_field_are_rejected() -> None:
    scope = _scope()
    document = _profile(scope)
    producer, port = _producer(document, scope)
    port.value = port.value.model_copy(
        update={
            "source_operation_profile_bytes": (b'{"schemaVersion":"1.0.0","schemaVersion":"1.0.0"}')
        }
    )
    with pytest.raises(LiveTargetContractError, match="Duplicate JSON key"):
        producer.produce()

    document["unexpectedAuthority"] = True
    raw = json.dumps(document, separators=(",", ":")).encode()
    port.value = port.value.model_copy(update={"source_operation_profile_bytes": raw})
    with pytest.raises(LiveTargetContractError, match="schema is invalid"):
        producer.produce()


def test_zero_and_missing_partitions_block_without_partial_authorization() -> None:
    scope = _scope()
    document = _profile(scope)
    for key in (
        "standardRest",
        "customRest",
        "metadata",
        "apexTests",
        "browserIntents",
        "syntheticDatasets",
    ):
        document[key] = []
    result = _producer(document, scope)[0].produce()

    assert result.state == "BLOCKED"
    assert result.plan is None
    assert result.authorized_target_sha256s == ()
    assert LiveTargetGapCode.ZERO_TARGETS in _codes(result)
    assert LiveTargetGapCode.MISSING_PARTITION in _codes(result)


def test_one_denied_target_blocks_the_whole_plan() -> None:
    scope = _scope()
    document = _profile(scope)
    allowed = list(_targets(document))[1:]
    result = _producer(
        document,
        scope,
        policy_changes={"authorizedTargetSha256s": allowed},
    )[0].produce()

    assert result.state == "BLOCKED"
    assert result.plan is None
    assert result.authorized_target_sha256s == ()
    assert LiveTargetGapCode.DENIED_TARGET in _codes(result)


def test_source_cannot_add_or_omit_targets_from_fixed_host_partition() -> None:
    scope = _scope()
    original = _profile(scope)
    policy = _policy(original)

    added = _profile(scope)
    added["metadata"].append({**added["metadata"][0], "member": "UnapprovedExtraClass"})
    added_port = _Port(
        LiveTargetCapture(
            sourceOperationProfileBytes=json.dumps(added).encode(),
            verifiedScope=scope,
            organizationClassificationReceiptSha256="6" * 64,
            organizationEnvironmentClass="DEVELOPER_EDITION",
            organizationReceiptValidUntil="2026-09-10T09:00:00Z",
        )
    )
    added_result = HostOwnedLiveTargetPlanProducer(added_port, policy, clock=lambda: T0).produce()
    assert LiveTargetGapCode.DENIED_TARGET in _codes(added_result)

    omitted = _profile(scope)
    omitted["metadata"] = []
    omitted_port = _Port(
        added_port.value.model_copy(
            update={"source_operation_profile_bytes": json.dumps(omitted).encode()}
        )
    )
    omitted_result = HostOwnedLiveTargetPlanProducer(
        omitted_port, policy, clock=lambda: T0
    ).produce()
    assert LiveTargetGapCode.OMITTED_POLICY_TARGET in _codes(omitted_result)
    assert omitted_result.authorized_target_sha256s == ()


def test_route_variables_are_an_exact_typed_contract() -> None:
    scope = _scope()
    original = _profile(scope)
    policy = _policy(original)
    malformed = _profile(scope)
    malformed["standardRest"][0]["variables"] = malformed["standardRest"][0]["variables"][:-1]
    port = _Port(
        LiveTargetCapture(
            sourceOperationProfileBytes=json.dumps(malformed).encode(),
            verifiedScope=scope,
            organizationClassificationReceiptSha256="6" * 64,
            organizationEnvironmentClass="DEVELOPER_EDITION",
            organizationReceiptValidUntil="2026-09-10T09:00:00Z",
        )
    )

    with pytest.raises(LiveTargetContractError, match="schema is invalid"):
        HostOwnedLiveTargetPlanProducer(port, policy, clock=lambda: T0).produce()


def test_expired_authority_blocks_and_production_cannot_be_policy_enabled() -> None:
    scope = _scope()
    document = _profile(scope)
    expired = _producer(
        document,
        scope,
        policy_changes={"validUntil": "2026-09-10T07:59:59Z"},
    )[0].produce()
    assert LiveTargetGapCode.EXPIRED_INPUT in _codes(expired)

    policy = _policy(document)
    invalid = policy.model_dump(by_alias=True, mode="json")
    invalid["allowedEnvironmentClasses"] = ["PRODUCTION"]
    invalid["sha256"] = contract_sha256(invalid)
    with pytest.raises(ValidationError, match="allowedEnvironmentClasses"):
        LiveTargetPolicy.model_validate(invalid)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value["standardRest"][0].update(
                {
                    "routeTemplate": (
                        "https://example.invalid/services/data/{apiVersion}/"
                        "sobjects/{objectApiName}/{recordId}"
                    )
                }
            ),
            LiveTargetGapCode.UNSUPPORTED_TARGET,
        ),
        (
            lambda value: value["customRest"][0].update(
                {"routeTemplate": ("/services/apexrest/{resourcePath}/{recordId}?limit=1;next=2")}
            ),
            LiveTargetGapCode.UNSUPPORTED_TARGET,
        ),
        (
            lambda value: value["metadata"][0].update({"member": "*"}),
            LiveTargetGapCode.UNSUPPORTED_TARGET,
        ),
        (
            lambda value: value["syntheticDatasets"][0].update(
                {"fieldProjection": ["*", "Id", "Ownership_Marker__c", "OwnerId"]}
            ),
            LiveTargetGapCode.BROAD_DATA_SCOPE,
        ),
    ],
)
def test_unsupported_or_broad_source_targets_block_even_when_hash_authorized(
    mutate: Any, expected: LiveTargetGapCode
) -> None:
    scope = _scope()
    document = _profile(scope)
    mutate(document)
    result = _producer(document, scope)[0].produce()

    assert result.state == "BLOCKED"
    assert expected in _codes(result)


def test_omitted_extra_and_obligation_scope_are_explicitly_blocking() -> None:
    scope = _scope()
    omitted = _profile(scope)
    for section in omitted.values():
        if isinstance(section, list):
            for target in section:
                if isinstance(target, dict) and "sourceEntityIds" in target:
                    target["sourceEntityIds"] = [
                        value for value in target["sourceEntityIds"] if value != "entity:b"
                    ] or ["entity:a"]
    omitted_result = _producer(omitted, scope)[0].produce()
    assert LiveTargetGapCode.OMITTED_SCOPE_ENTITY in _codes(omitted_result)

    extra = _profile(scope)
    extra["metadata"][0]["sourceEntityIds"].append("entity:outside")
    extra_result = _producer(extra, scope)[0].produce()
    assert LiveTargetGapCode.UNKNOWN_SCOPE_ENTITY in _codes(extra_result)

    wrong_obligation = _profile(scope)
    wrong_obligation["apexTests"][0]["obligationId"] = "obligation:invented"
    obligation_result = _producer(wrong_obligation, scope)[0].produce()
    assert LiveTargetGapCode.OBLIGATION_SCOPE_MISMATCH in _codes(obligation_result)


def test_many_and_duplicate_targets_fail_closed() -> None:
    scope = _scope()
    many = _profile(scope)
    many["metadata"].append({**many["metadata"][0], "member": "AnotherContractClass"})
    many_result = _producer(
        many,
        scope,
        policy_changes={"maximumTargets": 7},
    )[0].produce()
    assert LiveTargetGapCode.UNSUPPORTED_TARGET in _codes(many_result)

    duplicate = _profile(scope)
    duplicate["metadata"].append(dict(duplicate["metadata"][0]))
    duplicate_result = _producer(duplicate, scope)[0].produce()
    assert LiveTargetGapCode.DUPLICATE_TARGET in _codes(duplicate_result)


def test_profile_cannot_be_rebound_to_an_unrelated_verified_scope() -> None:
    scope = _scope()
    document = _profile(scope)
    different = _scope(entities=("entity:a", "entity:b", "entity:c"))
    result = _producer(document, different)[0].produce()

    assert result.state == "BLOCKED"
    assert LiveTargetGapCode.PROFILE_SCOPE_MISMATCH in _codes(result)
    assert LiveTargetGapCode.OMITTED_SCOPE_ENTITY in _codes(result)


def test_scope_and_policy_digests_cannot_be_fabricated() -> None:
    with pytest.raises(ValidationError, match="artifact_sha256 mismatch"):
        VerifiedLiveTargetScope.model_validate(
            {**_scope().model_dump(mode="json"), "artifact_sha256": H}
        )

    scope = _scope()
    document = _profile(scope)
    policy = _policy(document)
    with pytest.raises(ValidationError, match="policy digest mismatch"):
        LiveTargetPolicy.model_validate(
            {**policy.model_dump(by_alias=True, mode="json"), "maximumTargets": 31}
        )
