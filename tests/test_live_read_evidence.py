from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import neo_sf_q_intel.live_read_evidence as live_read
from neo_sf_q_intel.candidate_target_compiler import (
    ExpectedAssertionPredicate,
    ExpectedDatasetContract,
    ExpectedExecutionContract,
    ExpectedRunnerProvenance,
    ExpectedTargetAssertion,
)
from neo_sf_q_intel.classification_bootstrap import (
    CLASSIFICATION_OPERATION_PLAN_SHA256,
    ClassificationAuthority,
    ClassificationPins,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import (
    ExecutionToolVersion,
    SQLiteExecutionAssertionStore,
    TrustedExecutionRunnerKey,
)
from neo_sf_q_intel.live_read_evidence import (
    CliCompleted,
    CliInvocation,
    HostLiveReadCapture,
    HostLiveReadConfig,
    HostOwnedLiveReadExecutor,
    LiveReadCode,
    LiveReadError,
    ResolvedDatasetScope,
    ResolvedTargetVariables,
    ResolvedVariableRow,
    SubprocessCliRunner,
)
from neo_sf_q_intel.live_receipt_ledger import (
    SQLiteLiveReceiptLedger,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipt_producer import (
    LiveReceiptProducerCode,
    LiveReceiptProducerError,
    TrustedLiveReceiptProducer,
)
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    HostEnrollmentBinding,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptReference,
    ReceiptScope,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    sign_live_receipt,
)
from neo_sf_q_intel.live_target_plan import LiveTargetPlan, TargetPartition
from tests.test_live_receipts import (
    HOST_KEY,
    PRODUCT_KEY,
    _issuer_registry,
    _profile,
)
from tests.test_live_target_plan import _producer, _scope
from tests.test_live_target_plan import _profile as _source_profile

ALIAS = "caip-dev"
ORG_ID = "00D000000000001AAA"
ACTOR = "admin@example.invalid"
ACTOR_ID = "005000000000001AAA"
INSTANCE_HOST = "example.my.salesforce.com"
EDITION = "Developer Edition"


def _classification_authority(now: datetime | None = None) -> ClassificationAuthority:
    now = now or datetime.now(UTC)
    pins = ClassificationPins(
        alias=ALIAS,
        api_version="v67.0",
        org_fingerprint_sha256=_digest(ORG_ID),
        actor_fingerprint_sha256=_digest(ACTOR.casefold()),
        actor_user_id_sha256=_digest(ACTOR_ID),
        instance_host_sha256=_digest(INSTANCE_HOST),
        edition_sha256=_digest(EDITION),
        environment_class="DEVELOPER_EDITION",
    )
    return ClassificationAuthority(
        schema_version="1.0.0",
        authority_class="FIXED_SYSTEM_CLASSIFICATION_ONLY",
        task_authority_sha256="e" * 64,
        pins_sha256=pins.pins_sha256,
        operation_plan_sha256=CLASSIFICATION_OPERATION_PLAN_SHA256,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=30),
        maximum_response_bytes=65536,
    )


def _trusted_live_read_keys() -> dict[str, TrustedExecutionRunnerKey]:
    profile = _profile().profile
    gate_ids = frozenset({"SF-L03", "SF-L04", "SF-L05"})
    return {
        "salesforce-live-read-runner": TrustedExecutionRunnerKey(
            key_id="salesforce-live-read-runner",
            producer_id="product-receipt-issuer",
            runner_id="salesforce-live-read-runner",
            allowed_gate_ids=gate_ids,
            allowed_receipt_roles=frozenset(
                profile.gates_by_id[gate_id].receiptType for gate_id in gate_ids
            ),
            hmac_key=PRODUCT_KEY,
        )
    }


class _Port:
    def __init__(self, capture: HostLiveReadCapture) -> None:
        self.capture_value = capture
        self.calls = 0

    def capture(self) -> HostLiveReadCapture:
        self.calls += 1
        return self.capture_value


class _Runner:
    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.invocations: list[CliInvocation] = []
        self.members = {"a001234": "fixture-run"}

    def run(self, invocation: CliInvocation) -> CliCompleted:
        self.invocations.append(invocation)
        if self.failure == "nonzero":
            return CliCompleted(1, b'{"token":"must-not-leak"}')
        if self.failure == "malformed":
            return CliCompleted(0, b'{"status":0,"status":0}')
        if self.failure == "oversize":
            return CliCompleted(0, b"", output_exceeded=True)
        if self.failure == "timeout":
            return CliCompleted(-1, b"", timed_out=True)
        args = invocation.arguments
        if args == ("--version",):
            return CliCompleted(0, b"@salesforce/cli/2.149.9 win32-x64 node-v24.19.0\n")
        if args[:2] == ("org", "display"):
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "id": ORG_ID,
                        "username": ACTOR,
                        "instanceUrl": f"https://{INSTANCE_HOST}",
                        "connectedStatus": "Connected",
                        "alias": ALIAS,
                        "accessToken": "secret-display-token-canary",
                    },
                }
            )
        if args[:2] == ("data", "query"):
            query = args[args.index("--query") + 1]
            if " FROM Organization " in query:
                record = {"Id": ORG_ID, "IsSandbox": False, "OrganizationType": EDITION}
                object_name = "Organization"
            elif " FROM User " in query:
                record = {"Id": ACTOR_ID, "Username": ACTOR, "IsActive": True}
                object_name = "User"
            else:
                raise AssertionError("Unexpected system classification query")
            record["attributes"] = {
                "type": object_name,
                "url": f"/services/data/v67.0/sobjects/{object_name}/{record['Id']}",
            }
            return _completed(
                {"status": 0, "result": {"records": [record], "done": True, "totalSize": 1}}
            )
        if args[:4] == ("api", "request", "rest", "/services/oauth2/userinfo"):
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "statusCode": 200,
                        "headers": {"content-type": "application/json"},
                        "body": {
                            "user_id": ACTOR_ID,
                            "organization_id": ORG_ID,
                            "preferred_username": ACTOR,
                        },
                    },
                    "warnings": [],
                }
            )
        if args[:4] == ("api", "request", "rest", args[3]):
            route = args[3]
            record_id = route.split("?", 1)[0].rsplit("/", 1)[1]
            member = {
                "Id": record_id,
                "Ownership_Marker__c": self.members[record_id],
                "OwnerId": ACTOR_ID,
            }
            result: Any = {"result": member}
            if route.startswith("/services/data/"):
                result = member
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "statusCode": 200,
                        "headers": {"content-type": "application/json; charset=UTF-8"},
                        "body": result,
                    },
                    "warnings": [],
                }
            )
        if args[:3] == ("project", "retrieve", "start"):
            stage = Path(args[args.index("--target-metadata-dir") + 1])
            (stage / "unpackaged" / "classes").mkdir(parents=True)
            (stage / "unpackaged" / "classes" / "ContractSelectedClass.cls").write_text(
                "public class ContractSelectedClass {}", encoding="utf-8"
            )
            (stage / "unpackaged" / "package.xml").write_text(
                '<Package xmlns="http://soap.sforce.com/2006/04/metadata"><types>'
                "<members>ContractSelectedClass</members><name>ApexClass</name>"
                "</types><version>67.0</version></Package>",
                encoding="utf-8",
            )
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "done": True,
                        "success": True,
                        "status": "Succeeded",
                        "fileProperties": [
                            {
                                "type": "ApexClass",
                                "fullName": "ContractSelectedClass",
                                "fileName": "unpackaged/classes/ContractSelectedClass.cls",
                            },
                            {
                                "type": "Package",
                                "fullName": "package",
                                "fileName": "unpackaged/package.xml",
                            },
                        ],
                    },
                }
            )
        raise AssertionError(f"unexpected arguments: {args!r}")


class _MetadataBombRunner(_Runner):
    def run(self, invocation: CliInvocation) -> CliCompleted:
        if invocation.arguments[:3] != ("project", "retrieve", "start"):
            return super().run(invocation)
        completed = super().run(invocation)
        args = invocation.arguments
        stage = Path(args[args.index("--target-metadata-dir") + 1])
        (stage / "unpackaged/classes/ContractSelectedClass.cls").write_bytes(b"x" * 65537)
        return completed


def _completed(document: dict[str, Any]) -> CliCompleted:
    return CliCompleted(0, json.dumps(document, separators=(",", ":")).encode())


def _is_business_rest(invocation: CliInvocation) -> bool:
    return invocation.arguments[:3] == ("api", "request", "rest") and (
        invocation.arguments[3].startswith(("/services/data/", "/services/apexrest/"))
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _support(*, role: str, scope: ReceiptScope, artifact: str, now: datetime) -> SignedLiveReceipt:
    payload = SupportingReceiptPayload(
        receipt_role=role,
        scope=scope,
        provenance=ReceiptProvenance.HOST_AUTHORITY,
        outcome=ReceiptOutcome.RECORDED,
        issued_at=now - timedelta(minutes=5),
        terminal_at=now - timedelta(minutes=4),
        expires_at=now + timedelta(minutes=30),
        artifact_sha256=artifact,
    )
    return sign_live_receipt(payload, issuer_id="host-authority-issuer", hmac_key=HOST_KEY)


def _expected_contract(
    plan: LiveTargetPlan, scope: ReceiptScope
) -> tuple[ExpectedExecutionContract, bytes]:
    datasets = tuple(
        ExpectedDatasetContract(
            datasetTargetSha256=target.target_sha256,
            sourceEntityIds=target.source_entity_ids,
            objectApiName=target.specification["objectApiName"],
            identityField=target.specification["identityField"],
            fieldProjection=tuple(sorted(target.specification["fieldProjection"])),
            ownershipMarkerField=target.specification["ownershipMarkerField"],
            predicateSha256=stable_sha256(target.specification["predicates"]),
            exactCardinality=target.specification["maximumRecords"],
        )
        for target in plan.targets
        if target.partition is TargetPartition.SYNTHETIC_DATASET
    )
    assertions = []
    gates = {
        TargetPartition.STANDARD_REST: "SF-L03",
        TargetPartition.CUSTOM_REST: "SF-L04",
        TargetPartition.METADATA: "SF-L05",
    }
    for target in plan.targets:
        if target.partition not in gates:
            continue
        specification = target.specification
        compatible = tuple(
            sorted(
                item.dataset_target_sha256
                for item in datasets
                if set(item.source_entity_ids) & set(target.source_entity_ids)
            )
        )
        if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}:
            predicate = ExpectedAssertionPredicate.REST_CLOSED_PROJECTION
            projection = tuple(
                sorted(
                    f"{field['path']}:{field['dataType']}:{str(field['required']).lower()}:"
                    f"{str(field['nullable']).lower()}"
                    + (
                        f":literal={stable_sha256(field['expectedLiteral'])}"
                        if field.get("expectedLiteral") is not None
                        else ""
                    )
                    for field in specification["responseFields"]
                )
            )
            metadata_type = metadata_member = None
            invocation_count = next(
                item.exact_cardinality
                for item in datasets
                if item.dataset_target_sha256 in compatible
            )
            maximum = invocation_count
            resolution = stable_sha256(
                {
                    "route_template": specification["routeTemplate"],
                    "variables": sorted(
                        specification["variables"], key=lambda value: value["name"]
                    ),
                    "compatible_dataset_target_sha256s": compatible,
                    "response_projection": projection,
                    "request_expansion": "EACH_DATASET_RECORD",
                    "invocation_count": invocation_count,
                    "response_record_path": specification["responseRecordPath"],
                    "dataset_field_paths": specification["datasetFieldPaths"],
                    "parent_bindings": specification["parentBindings"],
                    "per_response_minimum_cardinality": 1,
                    "per_response_maximum_cardinality": 1,
                }
            )
        else:
            predicate = ExpectedAssertionPredicate.METADATA_EXACT_MEMBER_MANIFEST
            metadata_type = specification["metadataType"]
            metadata_member = specification["member"]
            projection = (f"{metadata_type}:{metadata_member}",)
            maximum = int(specification["maximumFiles"])
            resolution = None
            invocation_count = 1
        assertions.append(
            ExpectedTargetAssertion(
                assertionId=(
                    f"target:{target.partition.value.casefold()}:{target.target_sha256[:32]}"
                ),
                gateId=gates[target.partition],
                partition=target.partition,
                targetSha256=target.target_sha256,
                predicate=predicate,
                expectedSha256=stable_sha256(
                    {
                        "target": target.target_sha256,
                        "projection": projection,
                        "minimum": invocation_count,
                        "maximum": maximum,
                        "invocation_count": invocation_count,
                        "request_expansion": None
                        if target.partition is TargetPartition.METADATA
                        else "EACH_DATASET_RECORD",
                    }
                ),
                minimumCardinality=invocation_count,
                maximumCardinality=maximum,
                invocationCount=invocation_count,
                requestExpansion=None
                if target.partition is TargetPartition.METADATA
                else "EACH_DATASET_RECORD",
                perResponseMinimumCardinality=None
                if target.partition is TargetPartition.METADATA
                else 1,
                perResponseMaximumCardinality=None
                if target.partition is TargetPartition.METADATA
                else 1,
                exactProjection=projection,
                metadataType=metadata_type,
                metadataMember=metadata_member,
                compatibleDatasetTargetSha256s=compatible,
                resolutionContractSha256=resolution,
            )
        )
    assertions_tuple = tuple(sorted(assertions, key=lambda item: item.assertion_id))
    body = {
        "schema_version": "1.0.0",
        "authority_scope": "EXECUTION_EXPECTATION_ONLY",
        "authorizes_execution": False,
        "execution_id": f"candidate-live:{plan.plan_sha256[:32]}",
        "request_sha256": plan.verified_change_sha256,
        "candidate_bundle_sha256": scope.candidate_sha256,
        "compilation_sha256": "2" * 64,
        "plan_sha256": plan.plan_sha256,
        "scope": scope.model_dump(mode="json"),
        "evidence_phase": EvidencePhase.LIVE_BASELINE.value,
        "gate_ids": tuple(sorted({item.gate_id for item in assertions_tuple})),
        "runner_provenance": ExpectedRunnerProvenance(
            producerId="product-receipt-issuer",
            runnerId="salesforce-live-read-runner",
            runnerKeyId="salesforce-live-read-runner",
            runnerVersion="1.0.0",
            adapterVersion="1.0.0",
            toolVersions=(ExecutionToolVersion(tool_id="salesforce-cli", version="2.149.9"),),
        ).model_dump(mode="json"),
        "assertions": tuple(item.model_dump(mode="json") for item in assertions_tuple),
        "datasets": tuple(item.model_dump(mode="json") for item in datasets),
        "verified_local_validations": (),
        "valid_until": plan.valid_until,
    }
    contract = ExpectedExecutionContract(**body, contract_sha256=stable_sha256(body))
    document = json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return contract, document


def _gate_receipt(
    *,
    gate_id: str,
    scope: ReceiptScope,
    profile: Any,
    now: datetime,
    enrollment: HostEnrollmentBinding | None = None,
    dependencies: tuple[SignedLiveReceipt, ...] = (),
    inputs: tuple[SignedLiveReceipt, ...] = (),
) -> SignedLiveReceipt:
    definition = profile.profile.gates_by_id[gate_id]
    payload = GateReceiptPayload(
        gate_id=gate_id,
        receipt_type=definition.receiptType,
        effect_class=definition.effectClass,
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        requirement_ids=definition.requirementIds,
        capability_ids=definition.capabilityIds,
        scope=scope,
        dependency_receipt_ids=tuple(item.receipt_id for item in dependencies),
        input_receipts=tuple(
            ReceiptReference(role=item.receipt_role, receipt_id=item.receipt_id) for item in inputs
        ),
        provenance=(
            ReceiptProvenance.HOST_AUTHORITY
            if gate_id == "SF-L01"
            else ReceiptProvenance.PRODUCT_OWNED
        ),
        outcome=ReceiptOutcome.PASSED,
        issued_at=now - timedelta(minutes=3),
        terminal_at=now - timedelta(minutes=2),
        expires_at=now + timedelta(minutes=30),
        artifact_index_sha256="a" * 64,
        assertions_sha256="b" * 64,
        enrollment=enrollment,
    )
    key = HOST_KEY if gate_id == "SF-L01" else PRODUCT_KEY
    issuer = "host-authority-issuer" if gate_id == "SF-L01" else "product-receipt-issuer"
    return sign_live_receipt(payload, issuer_id=issuer, hmac_key=key)


def _fixture(
    tmp_path: Path,
    *,
    runner: _Runner | None = None,
    durable_inputs: bool = True,
    record_count: int = 1,
    expected_ttl_seconds: int | None = None,
    expected_phase_gate_ids: tuple[str, ...] | None = None,
) -> tuple[
    HostOwnedLiveReadExecutor,
    _Port,
    _Runner,
    SQLiteLiveReceiptLedger,
]:
    acceptance_profile = _profile()
    subprocess.run(("git", "init", "--quiet", str(tmp_path)), check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(".runtime/\n", encoding="utf-8")
    now = datetime.now(UTC).replace(microsecond=0)
    members = (
        {"a001234": "fixture-run"}
        if record_count == 1
        else {f"a00{index:012d}": f"fixture-{index:03d}" for index in range(record_count)}
    )
    scope = ReceiptScope(
        campaign_id="campaign-live-read",
        project_id="project-generic",
        source_contract_sha256="1" * 64,
        candidate_sha256="2" * 64,
        build_sha256="3" * 64,
        operation_plan_sha256="4" * 64,
        restore_scope_sha256="5" * 64,
        policy_sha256="6" * 64,
        profile_sha256=acceptance_profile.profile_sha256,
        org_fingerprint_sha256=_digest(ORG_ID),
        actor_fingerprint_sha256=_digest(ACTOR.casefold()),
        recovery_deadline=now + timedelta(hours=1),
    )
    enrollment_binding = HostEnrollmentBinding(
        alias_reference_sha256=_digest(ALIAS),
        organization_id_sha256=scope.org_fingerprint_sha256,
        instance_host_sha256=_digest(INSTANCE_HOST),
        edition_sha256=_digest(EDITION),
        environment_class="DEVELOPER_EDITION",
        persona_fingerprint_sha256=scope.actor_fingerprint_sha256,
    )
    enrollment = _gate_receipt(
        gate_id="SF-L01",
        scope=scope,
        profile=acceptance_profile,
        now=now,
        enrollment=enrollment_binding,
    )
    cli = _gate_receipt(
        gate_id="SF-L02",
        scope=scope,
        profile=acceptance_profile,
        now=now,
        dependencies=(enrollment,),
        inputs=(enrollment,),
    )

    verified_scope = _scope()
    operation_profile = _source_profile(verified_scope)
    operation_profile["standardRest"][0]["routeTemplate"] = (
        "/services/data/v{apiVersion}/sobjects/Contract_Selected__c/{recordId}"
    )
    operation_profile["customRest"][0]["routeTemplate"] = (
        "/services/apexrest/sda/v1/policy/{recordId}"
    )
    for partition in ("standardRest", "customRest"):
        rest = operation_profile[partition][0]
        rest["variables"] = [
            {**variable, "datasetField": None if variable["valueSource"] == "API_VERSION" else "Id"}
            for variable in rest["variables"]
            if variable["name"] not in {"objectApiName", "resourcePath"}
        ]
        rest["requestExpansion"] = "EACH_DATASET_RECORD"
        rest["responseRecordPath"] = ""
        prefix = "" if partition == "standardRest" else "result."
        rest["datasetFieldPaths"] = {
            "Id": prefix + "Id",
            "Ownership_Marker__c": prefix + "Ownership_Marker__c",
            "OwnerId": prefix + "OwnerId",
        }
        rest["parentBindings"] = []
    operation_profile["syntheticDatasets"][0]["identityField"] = "Id"
    operation_profile["syntheticDatasets"][0]["maximumRecords"] = record_count
    operation_profile["syntheticDatasets"][0]["predicates"] = [
        {
            "field": "Ownership_Marker__c",
            "operator": "IN_SET",
            "valueSource": "SOURCE_LITERAL_SET",
            "values": list(members.values()),
        },
        {
            "field": "OwnerId",
            "operator": "EQUALS",
            "valueSource": "CURRENT_ENROLLED_ACTOR",
            "values": [],
        },
    ]
    operation_profile["standardRest"][0]["responseFields"] = [
        {"path": "Id", "dataType": "STRING", "required": True, "nullable": False},
        {"path": "OwnerId", "dataType": "STRING", "required": True, "nullable": False},
        {"path": "Ownership_Marker__c", "dataType": "STRING", "required": True, "nullable": False},
    ]
    operation_profile["customRest"][0]["responseFields"] = [
        {"path": "result.Id", "dataType": "STRING", "required": True, "nullable": False},
        {"path": "result.OwnerId", "dataType": "STRING", "required": True, "nullable": False},
        {
            "path": "result.Ownership_Marker__c",
            "dataType": "STRING",
            "required": True,
            "nullable": False,
        },
    ]
    evaluation = _producer(operation_profile, verified_scope)[0].produce()
    assert evaluation.plan is not None
    body = evaluation.plan.model_dump(mode="json")
    body["project_id"] = scope.project_id
    body["acceptance_profile_sha256"] = acceptance_profile.profile_sha256
    body["organization_classification_receipt_sha256"] = hashlib.sha256(
        serialize_live_receipt(enrollment)
    ).hexdigest()
    body["evaluated_at"] = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    body["valid_until"] = (now + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    body.pop("plan_sha256")
    plan = LiveTargetPlan(**body, plan_sha256=stable_sha256(body))
    resolved = tuple(
        sorted(
            (
                ResolvedTargetVariables(
                    target_sha256=target.target_sha256,
                    rows=tuple(
                        ResolvedVariableRow(record_id=record_id, values={"recordId": record_id})
                        for record_id in members
                    ),
                )
                for target in plan.targets
                if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
            ),
            key=lambda item: item.target_sha256,
        )
    )
    target_plan_receipt = _support(
        role="LIVE_TARGET_PLAN_RECEIPT",
        scope=scope,
        artifact=plan.plan_sha256,
        now=now,
    )
    dataset_target = next(
        target for target in plan.targets if target.partition is TargetPartition.SYNTHETIC_DATASET
    )
    dataset_scopes = tuple(
        sorted(
            (
                ResolvedDatasetScope(
                    target_sha256=target.target_sha256,
                    dataset_target_sha256=dataset_target.target_sha256,
                    object_api_name="Contract_Selected__c",
                    record_id_field="Id",
                    record_ids=tuple(members),
                    ownership_marker_field="Ownership_Marker__c",
                    field_projection=("Id", "OwnerId", "Ownership_Marker__c"),
                    member_predicate_values={
                        record_id: {"Ownership_Marker__c": marker, "OwnerId": ACTOR_ID}
                        for record_id, marker in members.items()
                    },
                    expected_records=record_count,
                    response_record_path="",
                    field_paths=target.specification["datasetFieldPaths"],
                )
                for target in plan.targets
                if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
            ),
            key=lambda item: item.target_sha256,
        )
    )
    _, expected_contract_document = _expected_contract(plan, scope)
    if expected_ttl_seconds is not None or expected_phase_gate_ids is not None:
        expected_body = json.loads(expected_contract_document)
        if expected_ttl_seconds is not None:
            expected_body["valid_until"] = (
                now + timedelta(seconds=expected_ttl_seconds)
            ).isoformat()
        if expected_phase_gate_ids is not None:
            expected_body["local_validation_phase_policy_sha256"] = "f" * 64
            expected_body["phase_read_only_gate_ids"] = list(expected_phase_gate_ids)
        expected_body["contract_sha256"] = stable_sha256(
            {key: value for key, value in expected_body.items() if key != "contract_sha256"}
        )
        expected_contract_document = json.dumps(
            expected_body, sort_keys=True, separators=(",", ":")
        ).encode()
    expected_contract_sha256 = hashlib.sha256(expected_contract_document).hexdigest()
    expected_contract_receipt = _support(
        role="EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        scope=scope,
        artifact=expected_contract_sha256,
        now=now,
    )
    dataset_receipt = _support(
        role="LIVE_DATASET_SCOPE_RECEIPT",
        scope=scope,
        artifact=stable_sha256(
            {
                "expectedExecutionContractSha256": expected_contract_sha256,
                "resolvedDatasetScopes": [item.model_dump(mode="json") for item in dataset_scopes],
                "resolvedVariables": [item.model_dump(mode="json") for item in resolved],
            }
        ),
        now=now,
    )
    capture = HostLiveReadCapture(
        plan=plan,
        enrollment_receipt=enrollment,
        cli_authentication_receipt=cli,
        target_plan_receipt=target_plan_receipt,
        dataset_scope_receipt=dataset_receipt,
        resolved_variables=resolved,
        resolved_dataset_scopes=dataset_scopes,
        expected_execution_contract_sha256=expected_contract_sha256,
        expected_execution_contract_document=expected_contract_document,
        expected_execution_contract_receipt=expected_contract_receipt,
    )
    port = _Port(capture)
    registry = _issuer_registry(acceptance_profile)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "live-read-receipts.db")
    ledger.setup()
    if durable_inputs:
        for receipt in (
            enrollment,
            cli,
            target_plan_receipt,
            dataset_receipt,
            expected_contract_receipt,
        ):
            ledger.append(serialize_live_receipt(receipt))
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "live-read-assertions.db")
    gate_roles = {
        item.receiptType
        for item in acceptance_profile.profile.gates_by_id.values()
        if item.receiptType != "HOST_ENROLLMENT_RECEIPT"
    }
    producer = TrustedLiveReceiptProducer(
        pinned_profile=acceptance_profile,
        expected_scope=scope,
        issuer=TrustedIssuer(
            issuer_id="product-receipt-issuer",
            issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION,
            hmac_key=PRODUCT_KEY,
            allowed_receipt_roles=frozenset(gate_roles),
        ),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_live_read_keys(),
        expected_execution_contract_document=expected_contract_document,
    )
    fake = runner or _Runner()
    fake.members = members
    executor = HostOwnedLiveReadExecutor(
        config=HostLiveReadConfig(
            classification_authority=_classification_authority(),
            alias=ALIAS,
            environment_class="DEVELOPER_EDITION",
            org_fingerprint_sha256=scope.org_fingerprint_sha256,
            actor_fingerprint_sha256=scope.actor_fingerprint_sha256,
            actor_user_id_sha256=_digest(ACTOR_ID),
            instance_host_sha256=_digest(INSTANCE_HOST),
            edition_sha256=_digest(EDITION),
            api_version="v67.0",
            salesforce_cli_version="2.149.9",
            staging_root=Path(".runtime/live-read-tests"),
        ),
        repository_root=tmp_path,
        input_port=port,
        issuer_registry=registry,
        receipt_producer=producer,
        execution_assertion_store=assertion_store,
        runner_authentication_key=PRODUCT_KEY,
        runner=fake,
        clock=lambda: now,
    )
    return executor, port, fake, ledger


def test_exact_three_partition_read_appends_digest_only_nonrelease_receipts(
    tmp_path: Path,
) -> None:
    executor, port, runner, ledger = _fixture(tmp_path)

    result = executor.execute()

    assert result.release_eligible is False
    assert result.authority_scope == "READ_ONLY_LIVE_EVIDENCE"
    assert {item.partition for item in result.observations} == {
        TargetPartition.STANDARD_REST,
        TargetPartition.CUSTOM_REST,
        TargetPartition.METADATA,
    }
    assert len(result.stored_receipt_ids) == 3
    assert port.calls == 1
    assert all(
        item.arguments == ("--version",) or ALIAS in item.arguments for item in runner.invocations
    )
    assert list((tmp_path / ".runtime" / "live-read-tests").glob("capture-*")) == []
    assert len(ledger.replay(campaign_id="campaign-live-read")) == 8
    serialized = result.model_dump_json()
    assert ORG_ID not in serialized
    assert ACTOR not in serialized
    assert "synthetic-record" not in serialized


def test_expected_contract_bytes_cannot_be_substituted_before_cli_access(
    tmp_path: Path,
) -> None:
    executor, port, runner, _ = _fixture(tmp_path)
    port.capture_value = port.capture_value.model_copy(
        update={
            "expected_execution_contract_document": (
                port.capture_value.expected_execution_contract_document + b" "
            )
        }
    )

    with pytest.raises(LiveReadError, match=f"^{LiveReadCode.AUTHORITY_INVALID.value}$"):
        executor.execute()

    assert runner.invocations == []


@pytest.mark.parametrize("seconds,valid", [(30, True), (-1, False), (7200, False)])
def test_expected_contract_expiry_can_narrow_but_never_outlive_plan_or_current_authority(
    tmp_path: Path, seconds: int, valid: bool
) -> None:
    executor, port, runner, _ = _fixture(tmp_path, expected_ttl_seconds=seconds)
    if valid:
        executor._validate_authority(port.capture_value, executor.clock())
        assert 0 < executor._remaining_timeout(port.capture_value) <= seconds
    else:
        with pytest.raises(LiveReadError, match="AUTHORITY_INVALID"):
            executor.execute()
    assert runner.invocations == []


@pytest.mark.parametrize(
    "gate_ids",
    [
        ("SF-L03", "SF-L04", "SF-L05"),
        ("SF-L03", "SF-L04"),
        ("SF-L03", "SF-L04", "SF-L05", "SF-L06"),
    ],
)
def test_phase_authority_must_match_plan_and_exact_read_only_gate_set_before_cli(
    tmp_path: Path, gate_ids: tuple[str, ...]
) -> None:
    executor, _, runner, _ = _fixture(tmp_path, expected_phase_gate_ids=gate_ids)

    # The independently signed expected-contract receipt is durable, but cannot add a phase
    # policy not sealed into the source plan, nor widen or narrow the fixed read-only gate set.
    with pytest.raises(LiveReadError, match="AUTHORITY_INVALID"):
        executor.execute()

    assert runner.invocations == []


def test_signed_but_undurable_expected_contract_receipt_cannot_bind_gate(
    tmp_path: Path,
) -> None:
    _, port, _, _ = _fixture(tmp_path)
    capture = port.capture_value
    profile = _profile()
    scope = capture.enrollment_receipt.payload.scope
    ledger = SQLiteLiveReceiptLedger(tmp_path / "missing-expected-contract.db")
    ledger.setup()
    for receipt in (
        capture.enrollment_receipt,
        capture.cli_authentication_receipt,
        capture.target_plan_receipt,
        capture.dataset_scope_receipt,
    ):
        assert receipt is not None
        ledger.append(serialize_live_receipt(receipt))
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=TrustedIssuer(
            issuer_id="product-receipt-issuer",
            issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION,
            hmac_key=PRODUCT_KEY,
            allowed_receipt_roles=frozenset(
                item.receiptType
                for item in profile.profile.gates
                if item.receiptType != "HOST_ENROLLMENT_RECEIPT"
            ),
        ),
        issuer_registry=_issuer_registry(profile),
        ledger=ledger,
        execution_assertion_store=SQLiteExecutionAssertionStore(
            tmp_path / "missing-expected-assertions.db"
        ),
        trusted_execution_runner_keys=_trusted_live_read_keys(),
        expected_execution_contract_document=(capture.expected_execution_contract_document),
    )
    definition = producer.gate_definition("SF-L03")
    available = {
        receipt.receipt_role: receipt
        for receipt in (
            capture.enrollment_receipt,
            capture.target_plan_receipt,
            capture.dataset_scope_receipt,
            capture.expected_execution_contract_receipt,
        )
        if receipt is not None
    }

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
    ):
        producer.bind_gate(
            gate_id="SF-L03",
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            dependency_receipts=(capture.cli_authentication_receipt,),
            input_receipts=tuple(available[role] for role in definition.requiredInputReceiptRoles),
        )


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("nonzero", LiveReadCode.CLI_FAILED),
        ("malformed", LiveReadCode.RESPONSE_INVALID),
        ("oversize", LiveReadCode.OUTPUT_LIMIT),
        ("timeout", LiveReadCode.CLI_TIMEOUT),
    ],
)
def test_cli_boundary_failures_are_sanitized_and_append_nothing(
    tmp_path: Path, failure: str, code: LiveReadCode
) -> None:
    executor, _, _, ledger = _fixture(tmp_path, runner=_Runner(failure=failure))

    with pytest.raises(LiveReadError, match=f"^{code.value}$") as raised:
        executor.execute()

    assert "must-not-leak" not in str(raised.value)
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()


def test_current_org_identity_mismatch_blocks_before_any_target(tmp_path: Path) -> None:
    executor, _, runner, ledger = _fixture(tmp_path)
    executor = replace(
        executor,
        config=executor.config.model_copy(update={"org_fingerprint_sha256": "f" * 64}),
    )

    with pytest.raises(LiveReadError, match=f"^{LiveReadCode.AUTHORITY_INVALID.value}$"):
        executor.execute()

    assert runner.invocations == []
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()


def test_unbound_dataset_values_are_rejected_without_invoking_cli(tmp_path: Path) -> None:
    executor, port, runner, _ = _fixture(tmp_path)
    capture = port.capture_value
    changed = capture.resolved_variables[0].model_copy(
        update={
            "rows": (ResolvedVariableRow(record_id="a001234", values={"recordId": "different"}),)
        }
    )
    port.capture_value = capture.model_copy(
        update={
            "resolved_variables": tuple(
                sorted(
                    (changed, *capture.resolved_variables[1:]), key=lambda item: item.target_sha256
                )
            )
        }
    )

    with pytest.raises(LiveReadError, match=f"^{LiveReadCode.AUTHORITY_INVALID.value}$"):
        executor.execute()

    assert runner.invocations == []


def test_undurable_input_receipts_block_before_salesforce_invocation(tmp_path: Path) -> None:
    executor, _, runner, ledger = _fixture(tmp_path, durable_inputs=False)

    with pytest.raises(LiveReadError, match=f"^{LiveReadCode.AUTHORITY_INVALID.value}$"):
        executor.execute()

    assert runner.invocations == []
    assert ledger.replay(campaign_id="campaign-live-read") == ()


def test_unpinned_salesforce_cli_version_blocks_before_any_cli_access(tmp_path: Path) -> None:
    executor, _, runner, _ = _fixture(tmp_path)
    executor = replace(
        executor,
        config=executor.config.model_copy(update={"salesforce_cli_version": "2.148.3"}),
    )

    with pytest.raises(LiveReadError, match=f"^{LiveReadCode.AUTHORITY_INVALID.value}$"):
        executor.execute()

    assert runner.invocations == []


def test_metadata_output_bound_cleans_staging_and_creates_no_gate_receipts(
    tmp_path: Path,
) -> None:
    executor, _, _, ledger = _fixture(tmp_path, runner=_MetadataBombRunner())

    with pytest.raises(LiveReadError, match=f"^{LiveReadCode.OUTPUT_LIMIT.value}$"):
        executor.execute()

    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()
    assert list((tmp_path / ".runtime" / "live-read-tests").glob("capture-*")) == []


def test_direct_process_arguments_allow_literal_data_but_reject_control_characters() -> None:
    assert live_read._safe_process_arguments(
        ("data", "query", "SELECT Id FROM Account WHERE Name = 'A|B&100%!'" )
    )
    assert not live_read._safe_process_arguments(("data", "query", "line1\nline2"))
    assert not live_read._safe_process_arguments(("data", "query", "bad\x00value"))
    assert not live_read._safe_process_arguments(("data", "query", "bad\x85value"))


def test_windows_sf_batch_is_resolved_to_direct_bundled_node(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-update"))
    batch = tmp_path / "sf/bin/sf.cmd"
    node = tmp_path / "sf/client/bin/node.exe"
    entrypoint = tmp_path / "sf/client/bin/run.js"
    batch.parent.mkdir(parents=True)
    node.parent.mkdir(parents=True)
    batch.write_text("@echo off", encoding="utf-8")
    node.write_bytes(b"node")
    entrypoint.write_bytes(b"run")
    arguments = ("data", "query", "SELECT Id FROM Account WHERE Name = 'A|B'")

    launch = live_read._resolve_windows_sf_launcher(batch, arguments)

    assert launch == [str(node), "--no-deprecation", str(entrypoint), *arguments]
    assert not any(Path(item).name.casefold() == "cmd.exe" for item in launch)


def test_windows_sf_prefers_validated_per_user_updated_client(
    tmp_path: Path, monkeypatch: Any
) -> None:
    batch = tmp_path / "install/sf/bin/sf.cmd"
    bundled_node = tmp_path / "install/sf/client/bin/node.exe"
    bundled_entrypoint = tmp_path / "install/sf/client/bin/run.js"
    updated_node = tmp_path / "profile/sf/client/bin/node.exe"
    updated_entrypoint = tmp_path / "profile/sf/client/bin/run.js"
    for item in (batch, bundled_node, bundled_entrypoint, updated_node, updated_entrypoint):
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(b"launcher")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))

    launch = live_read._resolve_windows_sf_launcher(batch, ("--version",))

    assert launch[:3] == [str(updated_node), "--no-deprecation", str(updated_entrypoint)]


def test_windows_npm_sf_shim_uses_direct_node_without_shell(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-update"))
    batch = tmp_path / "npm/sf.cmd"
    node = tmp_path / "npm/node.exe"
    entrypoint = tmp_path / "npm/node_modules/@salesforce/cli/bin/run.js"
    for item in (batch, node, entrypoint):
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(b"launcher")
    arguments = ("data", "query", "SELECT Id FROM Account WHERE Name = 'A|B'")

    launch = live_read._resolve_windows_sf_launcher(batch, arguments)

    assert launch == [str(node), "--no-deprecation", str(entrypoint), *arguments]
    assert not any(Path(item).name.casefold() == "cmd.exe" for item in launch)


def test_metadata_staging_cannot_escape_ignored_runtime_directory() -> None:
    with pytest.raises(ValueError, match="ignored .runtime"):
        HostLiveReadConfig(
            classification_authority=_classification_authority(),
            alias=ALIAS,
            environment_class="DEVELOPER_EDITION",
            org_fingerprint_sha256="a" * 64,
            actor_fingerprint_sha256="b" * 64,
            actor_user_id_sha256=_digest(ACTOR_ID),
            instance_host_sha256="c" * 64,
            edition_sha256="d" * 64,
            api_version="v67.0",
            salesforce_cli_version="2.149.9",
            staging_root=Path("retrieved-metadata"),
        )


class _AlterResponseRunner(_Runner):
    def __init__(self, change: Any, *, metadata: bool = False) -> None:
        super().__init__()
        self.change = change
        self.metadata = metadata

    def run(self, invocation: CliInvocation) -> CliCompleted:
        completed = super().run(invocation)
        prefix = ("project", "retrieve", "start") if self.metadata else ("api", "request", "rest")
        if invocation.arguments[:3] == prefix and (self.metadata or _is_business_rest(invocation)):
            document = json.loads(completed.stdout)
            self.change(document, invocation)
            return _completed(document)
        return completed


@pytest.mark.parametrize(
    "change",
    [
        lambda result: result.update({"Id": "another-record"}),
        lambda result: result.update({"Ownership_Marker__c": "another-owner"}),
        lambda result: result.update({"Secret_Field__c": "must-not-leak"}),
        lambda result: result.pop("Ownership_Marker__c"),
        lambda result: result.update({"records": [{"Id": "foreign-record"}]}),
        lambda result: result.update({"Id": True}),
    ],
)
def test_rest_rejects_wrong_record_owner_projection_and_type(tmp_path: Path, change: Any) -> None:
    runner = _AlterResponseRunner(lambda document, _: change(document["result"]["body"]))
    executor, _, _, ledger = _fixture(tmp_path, runner=runner)
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$") as error:
        executor.execute()
    assert "must-not-leak" not in str(error.value)
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()


@pytest.mark.parametrize(
    "change",
    [
        lambda result: result.update(done=False),
        lambda result: result.update(success=False),
        lambda result: result.update(status="InProgress"),
        lambda result: result.pop("done"),
        lambda result: result.pop("success"),
        lambda result: result.update(messages=[{"problem": "partial"}]),
        lambda result: result["fileProperties"][0].update(fullName="WrongClass"),
        lambda result: result["fileProperties"][0].update(type="ApexTrigger"),
        lambda result: result["fileProperties"][0].update(
            fileName="unpackaged/classes/WrongClass.cls"
        ),
        lambda result: result["fileProperties"].append(dict(result["fileProperties"][0])),
        lambda result: result.update(fileProperties=[]),
    ],
)
def test_metadata_requires_terminal_exact_type_member_and_file_receipt(
    tmp_path: Path,
    change: Any,
) -> None:
    runner = _AlterResponseRunner(lambda document, _: change(document["result"]), metadata=True)
    executor, _, _, ledger = _fixture(tmp_path, runner=runner)
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        executor.execute()
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L05") == ()
    assert list((tmp_path / ".runtime/live-read-tests").glob("capture-*")) == []


@pytest.mark.parametrize("change", ["wrong_manifest", "extra_file", "missing_file", "entity"])
def test_metadata_reads_exact_manifest_and_rejects_staged_substitution(
    tmp_path: Path, change: str
) -> None:
    def alter(_: Any, invocation: CliInvocation) -> None:
        stage = Path(invocation.arguments[invocation.arguments.index("--target-metadata-dir") + 1])
        package = stage / "unpackaged/package.xml"
        if change == "wrong_manifest":
            package.write_text(package.read_text().replace("ContractSelectedClass", "WrongClass"))
        elif change == "entity":
            package.write_text(
                '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///secret">]><x>&secret;</x>'
            )
        elif change == "extra_file":
            (stage / "unpackaged/classes/Unrequested.cls").write_text("class Unrequested {}")
        else:
            (stage / "unpackaged/classes/ContractSelectedClass.cls").unlink()

    executor, _, _, ledger = _fixture(tmp_path, runner=_AlterResponseRunner(alter, metadata=True))
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        executor.execute()
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L05") == ()


def test_custom_field_accepts_cli_container_identity_and_nested_unzip_layout(
    tmp_path: Path,
) -> None:
    stage = tmp_path / "capture"
    payload_root = stage / "unpackaged"
    component = payload_root / "unpackaged/objects/Opportunity.object"
    component.parent.mkdir(parents=True)
    component.write_text(
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata"><fields>'
        "<fullName>Amount</fullName><type>Currency</type>"
        "</fields></CustomObject>",
        encoding="utf-8",
    )
    manifest = payload_root / "unpackaged/package.xml"
    manifest.write_text(
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata"><types>'
        "<members>Opportunity.Amount</members><name>CustomField</name>"
        "</types><version>67.0</version></Package>",
        encoding="utf-8",
    )
    response = {
        "result": {
            "done": True,
            "success": True,
            "status": "Succeeded",
            "fileProperties": [
                {
                    "type": "CustomObject",
                    "fullName": "Opportunity",
                    "fileName": "unpackaged/objects/Opportunity.object",
                },
                {
                    "type": "Package",
                    "fullName": "unpackaged/package.xml",
                    "fileName": "unpackaged/package.xml",
                },
            ],
        }
    }

    resolved_root = live_read._metadata_payload_root(stage)
    expected = live_read._metadata_expected_paths(response, "CustomField", "Opportunity.Amount")
    entries, total = live_read._scan_stage(
        resolved_root,
        maximum_files=4,
        maximum_bytes=65536,
        allowed_paths=expected,
    )
    live_read._validate_metadata_completion(
        response,
        "CustomField",
        "Opportunity.Amount",
        entries,
        payload_root=resolved_root,
    )

    assert resolved_root == payload_root
    assert total > 0
    assert {item["relative_path"] for item in entries} == expected


@pytest.mark.parametrize(
    ("member", "field_name"),
    [
        ("Opportunity.Amount", "OtherField__c"),
        ("Opportunity.Amount", "Amount</fullName><fullName>OtherField__c"),
        (
            "Opportunity.Amount",
            "Amount</fullName><type>Text</type></fields>"
            "<validationRules><fullName>OtherRule</fullName></validationRules>"
            "<fields><fullName>Amount",
        ),
    ],
)
def test_custom_field_rejects_component_without_exact_requested_field(
    tmp_path: Path, member: str, field_name: str
) -> None:
    stage = tmp_path / "capture"
    (stage / "unpackaged/objects").mkdir(parents=True)
    (stage / "unpackaged/objects/Opportunity.object").write_text(
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata"><fields>'
        f"<fullName>{field_name}</fullName><type>Text</type>"
        "</fields></CustomObject>",
        encoding="utf-8",
    )
    (stage / "unpackaged/package.xml").write_text(
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata"><types>'
        f"<members>{member}</members><name>CustomField</name>"
        "</types><version>67.0</version></Package>",
        encoding="utf-8",
    )
    response = {
        "result": {
            "done": True,
            "success": True,
            "status": "Succeeded",
            "fileProperties": [
                {
                    "type": "CustomObject",
                    "fullName": "Opportunity",
                    "fileName": "unpackaged/objects/Opportunity.object",
                },
                {
                    "type": "Package",
                    "fullName": "package",
                    "fileName": "unpackaged/package.xml",
                },
            ],
        }
    }
    expected = live_read._metadata_expected_paths(response, "CustomField", member)
    entries, _ = live_read._scan_stage(
        stage,
        maximum_files=4,
        maximum_bytes=65536,
        allowed_paths=expected,
    )

    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_metadata_completion(
            response,
            "CustomField",
            member,
            entries,
            payload_root=stage,
        )


def test_custom_field_rejects_same_size_change_after_stage_scan(tmp_path: Path) -> None:
    stage = tmp_path / "capture"
    component = stage / "unpackaged/objects/Opportunity.object"
    component.parent.mkdir(parents=True)
    original = (
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata"><fields>'
        "<fullName>Amount</fullName><type>Currency</type>"
        "</fields></CustomObject>"
    )
    component.write_text(original, encoding="utf-8")
    (stage / "unpackaged/package.xml").write_text(
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata"><types>'
        "<members>Opportunity.Amount</members><name>CustomField</name>"
        "</types><version>67.0</version></Package>",
        encoding="utf-8",
    )
    response = {
        "result": {
            "done": True,
            "success": True,
            "status": "Succeeded",
            "fileProperties": [
                {
                    "type": "CustomObject",
                    "fullName": "Opportunity",
                    "fileName": "unpackaged/objects/Opportunity.object",
                },
                {
                    "type": "Package",
                    "fullName": "package",
                    "fileName": "unpackaged/package.xml",
                },
            ],
        }
    }
    expected = live_read._metadata_expected_paths(
        response, "CustomField", "Opportunity.Amount"
    )
    entries, _ = live_read._scan_stage(
        stage,
        maximum_files=4,
        maximum_bytes=65536,
        allowed_paths=expected,
    )
    component.write_text(original.replace("Amount", "Amoumt"), encoding="utf-8")

    with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$"):
        live_read._validate_metadata_completion(
            response,
            "CustomField",
            "Opportunity.Amount",
            entries,
            payload_root=stage,
        )


def test_authority_expiry_during_identity_stops_dependent_read(tmp_path: Path) -> None:
    executor, port, runner, ledger = _fixture(tmp_path)
    current = [executor.clock()]
    original = runner.run

    def expire_after_identity(invocation: CliInvocation) -> CliCompleted:
        result = original(invocation)
        if invocation.arguments[:2] == ("org", "display"):
            current[0] += timedelta(hours=2)
        return result

    runner.run = expire_after_identity  # type: ignore[method-assign]
    executor = replace(executor, clock=lambda: current[0])
    with pytest.raises(LiveReadError, match="^LIVE_READ_AUTHORITY_INVALID$"):
        executor.execute()
    assert not any(_is_business_rest(item) for item in runner.invocations)
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()
    assert port.calls == 1


def test_alias_remapping_after_first_response_blocks_next_target(tmp_path: Path) -> None:
    executor, _, runner, ledger = _fixture(tmp_path)
    original = runner.run
    switched = [False]

    def switch(invocation: CliInvocation) -> CliCompleted:
        result = original(invocation)
        if _is_business_rest(invocation):
            switched[0] = True
        elif switched[0] and invocation.arguments[:2] == ("org", "display"):
            payload = json.loads(result.stdout)
            payload["result"]["id"] = "different-org"
            return _completed(payload)
        return result

    runner.run = switch  # type: ignore[method-assign]
    with pytest.raises(LiveReadError, match="^LIVE_READ_IDENTITY_MISMATCH$"):
        executor.execute()
    assert sum(_is_business_rest(item) for item in runner.invocations) == 1
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()


def test_timeout_uses_smallest_remaining_authority_and_campaign_budget(tmp_path: Path) -> None:
    executor, port, _, _ = _fixture(tmp_path)
    capture = port.capture_value
    expiry = datetime.fromisoformat(capture.plan.valid_until.replace("Z", "+00:00"))
    executor = replace(executor, clock=lambda: expiry - timedelta(seconds=2))
    assert executor._remaining_timeout(capture) == 2


@pytest.mark.parametrize("kind", ["missing_ignore", "tracked_runtime"])
def test_staging_requires_git_ignore_and_no_tracked_runtime_files(
    tmp_path: Path, kind: str
) -> None:
    executor, _, runner, _ = _fixture(tmp_path)
    if kind == "missing_ignore":
        (tmp_path / ".gitignore").write_text("")
    else:
        (tmp_path / ".runtime").mkdir()
        (tmp_path / ".runtime/tracked.txt").write_text("tracked")
        subprocess.run(
            ("git", "-C", str(tmp_path), "add", "-f", ".runtime/tracked.txt"), check=True
        )
    with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$"):
        executor.execute()
    assert not any(
        item.arguments[:3] == ("project", "retrieve", "start") for item in runner.invocations
    )


def test_unconfirmed_process_exit_quarantines_staging_without_receipt(tmp_path: Path) -> None:
    executor, _, runner, ledger = _fixture(tmp_path)
    original = runner.run

    def unconfirmed(invocation: CliInvocation) -> CliCompleted:
        result = original(invocation)
        if invocation.arguments[:3] == ("project", "retrieve", "start"):
            return replace(result, quiescent=False)
        return result

    runner.run = unconfirmed  # type: ignore[method-assign]
    with pytest.raises(LiveReadError, match="^LIVE_READ_PROCESS_NOT_QUIESCENT$"):
        executor.execute()
    assert len(list((tmp_path / ".runtime/live-read-tests").glob("capture-*"))) == 1
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L05") == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object regression")
def test_real_windows_child_tree_terminates_before_runner_returns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    marker = tmp_path / "late-child-write"
    child = tmp_path / "child.py"
    child.write_text(
        "import pathlib,sys,time\ntime.sleep(0.8)\n"
        "pathlib.Path(sys.argv[1]).write_text('unsafe')\ntime.sleep(30)\n"
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\ntime.sleep(30)\n"
    )
    monkeypatch.setattr(
        live_read,
        "_resolve_launch",
        lambda *_: [sys.executable, str(parent), str(child), str(marker)],
    )
    completed = SubprocessCliRunner().run(CliInvocation(("--version",), 0.3, 4096))
    assert completed.timed_out and completed.quiescent and completed.stdout == b""
    time.sleep(0.9)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_runtime_ancestor_junction_is_rejected_before_metadata_dispatch(tmp_path: Path) -> None:
    executor, _, runner, _ = _fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / ".runtime"
    result = subprocess.run(
        ("cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)), capture_output=True
    )
    assert result.returncode == 0
    try:
        with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$"):
            executor.execute()
        assert not any(
            item.arguments[:3] == ("project", "retrieve", "start") for item in runner.invocations
        )
        assert list(outside.iterdir()) == []
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows no-follow file handle regression")
def test_file_swap_between_check_and_open_cannot_be_hashed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    path = tmp_path / "component.xml"
    path.write_bytes(b"authorized")
    original = live_read._windows_open_no_follow

    def swap(candidate: Path, *, directory: bool) -> Any:
        if not directory:
            candidate.unlink()
            candidate.write_bytes(b"unexpected")
        return original(candidate, directory=directory)

    monkeypatch.setattr(live_read, "_windows_open_no_follow", swap)
    with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$"):
        live_read._read_nofollow(path, maximum_bytes=1024)


def test_closed_response_schema_rejects_unbounded_object_and_extra_array_records(
    tmp_path: Path,
) -> None:
    with pytest.raises(LiveReadError, match="^LIVE_READ_TARGET_INVALID$"):
        live_read._validate_response_fields(
            {"data": {"secret": "never"}},
            [
                {"path": "data", "dataType": "OBJECT", "required": True, "nullable": False},
            ],
        )
    executor, port, _, _ = _fixture(tmp_path)
    scope = port.capture_value.resolved_dataset_scopes[0].model_copy(
        update={"response_record_path": "records[]"}
    )
    row = {"Id": "a001234", "Ownership_Marker__c": "fixture-run"}
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_dataset_response({"records": [row, row]}, scope)


def test_metadata_cleanup_failure_is_sanitized_and_never_receipted(
    tmp_path: Path, monkeypatch: Any
) -> None:
    executor, _, _, ledger = _fixture(tmp_path)

    def fail_cleanup(_: Path) -> None:
        raise OSError("sensitive-retrieval-path-and-token")

    monkeypatch.setattr(live_read, "_cleanup_stage", fail_cleanup)
    with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$") as error:
        executor.execute()
    assert "sensitive" not in str(error.value)
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L05") == ()


def test_metadata_rejects_staged_directory_junction_without_touching_destination(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("retain")
    junctions: list[Path] = []

    def alter(_: Any, invocation: CliInvocation) -> None:
        stage = Path(invocation.arguments[invocation.arguments.index("--target-metadata-dir") + 1])
        junction = stage / "linked"
        result = subprocess.run(
            ("cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)), capture_output=True
        )
        assert result.returncode == 0
        junctions.append(junction)

    executor, _, _, ledger = _fixture(tmp_path, runner=_AlterResponseRunner(alter, metadata=True))
    try:
        with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$"):
            executor.execute()
        assert sentinel.read_text() == "retain"
        assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L05") == ()
    finally:
        for junction in junctions:
            junction.rmdir()


def test_dataset_predicate_rechecked_independently_of_ownership(tmp_path: Path) -> None:
    _, port, _, _ = _fixture(tmp_path)
    scope = port.capture_value.resolved_dataset_scopes[0].model_copy(
        update={
            "response_record_path": "",
            "field_projection": ("Id", "Ownership_Marker__c", "Status__c"),
            "member_predicate_values": {
                "a001234": {"Ownership_Marker__c": "fixture-run", "Status__c": "Ready"}
            },
            "field_paths": {
                "Id": "Id",
                "Ownership_Marker__c": "Ownership_Marker__c",
                "Status__c": "Status__c",
            },
        }
    )
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_dataset_response(
            {"Id": "a001234", "Ownership_Marker__c": "fixture-run", "Status__c": "Different"}, scope
        )


def test_closed_array_schema_rejects_secret_children_and_duplicates() -> None:
    fields = [
        {"path": "records[].Id", "dataType": "STRING", "required": True, "nullable": False},
        {"path": "records[].Owned", "dataType": "BOOLEAN", "required": True, "nullable": False},
    ]
    digest, count = live_read._validate_response_fields(
        {"records": [{"Id": "a", "Owned": True}]}, fields
    )
    assert len(digest) == 64 and count >= 2
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_response_fields(
            {"records": [{"Id": "a", "Owned": True, "secret": "hidden"}]}, fields
        )
    with pytest.raises(LiveReadError, match="^LIVE_READ_TARGET_INVALID$"):
        live_read._validate_response_fields({"records": []}, fields + fields)


def test_standard_read_route_requests_exact_fields(tmp_path: Path) -> None:
    executor, port, _, _ = _fixture(tmp_path)
    capture = port.capture_value
    target = next(
        item for item in capture.plan.targets if item.partition is TargetPartition.STANDARD_REST
    )
    dataset = next(
        item
        for item in capture.resolved_dataset_scopes
        if item.target_sha256 == target.target_sha256
    )
    values = next(
        item.rows[0].values
        for item in capture.resolved_variables
        if item.target_sha256 == target.target_sha256
    )
    route, _ = live_read._render_scoped_rest_target(executor.config, target, values, dataset)
    assert route.endswith("/a001234?fields=Id,OwnerId,Ownership_Marker__c")
    for scope in (
        dataset.model_copy(update={"object_api_name": "Different__c"}),
        dataset.model_copy(update={"record_ids": ("different",)}),
    ):
        with pytest.raises(LiveReadError, match="^LIVE_READ_AUTHORITY_INVALID$"):
            live_read._render_scoped_rest_target(executor.config, target, values, scope)


def test_fractional_authority_budget_is_never_rounded_up(tmp_path: Path) -> None:
    executor, port, _, _ = _fixture(tmp_path)
    expiry = datetime.fromisoformat(port.capture_value.plan.valid_until.replace("Z", "+00:00"))
    executor = replace(executor, clock=lambda: expiry - timedelta(milliseconds=100))
    assert executor._remaining_timeout(port.capture_value) == pytest.approx(0.1)


@pytest.mark.skipif(os.name != "nt", reason="Windows containment setup regression")
def test_containment_assignment_failure_never_runs_suspended_child(
    tmp_path: Path, monkeypatch: Any
) -> None:
    marker = tmp_path / "should-not-execute"
    child = tmp_path / "child.py"
    child.write_text("import pathlib,sys\npathlib.Path(sys.argv[1]).write_text('executed')\n")
    monkeypatch.setattr(
        live_read, "_resolve_launch", lambda *_: [sys.executable, str(child), str(marker)]
    )

    def deny_job(_: Any, process: Any) -> None:
        raise OSError("job assignment unavailable")

    monkeypatch.setattr(live_read._ProcessContainment, "attach_and_resume", deny_job)
    result = SubprocessCliRunner().run(CliInvocation(("--version",), 2, 1024))
    assert result.returncode == -1 and result.quiescent
    assert not marker.exists()


def test_metadata_rejects_hardlink_before_reading_unrelated_bytes(tmp_path: Path) -> None:
    outside = tmp_path / "unrelated.txt"
    outside.write_bytes(b"private-existing-data")
    link = tmp_path / "component.xml"
    os.link(outside, link)
    with pytest.raises(LiveReadError, match="^LIVE_READ_METADATA_STAGING_INVALID$"):
        live_read._read_nofollow(link, maximum_bytes=1024)
    assert outside.read_bytes() == b"private-existing-data"


def test_unrequested_metadata_file_is_rejected_before_content_read(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "unrequested.xml").write_bytes(b"private-unrequested-content")
    opened: list[Path] = []

    def record_open(path: Path, **_: Any) -> bytes:
        opened.append(path)
        return b"private-unrequested-content"

    monkeypatch.setattr(live_read, "_read_nofollow", record_open)
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._scan_stage(
            tmp_path, maximum_files=10, maximum_bytes=1024, allowed_paths={"unpackaged/package.xml"}
        )
    assert opened == []


def test_nested_records_cannot_hide_behind_parent_membership(tmp_path: Path) -> None:
    _, port, _, _ = _fixture(tmp_path)
    scope = port.capture_value.resolved_dataset_scopes[0].model_copy(
        update={"response_record_path": ""}
    )
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_dataset_response(
            {
                "Id": "a001234",
                "Ownership_Marker__c": "fixture-run",
                "history": [{"Id": "foreign-private-record"}],
            },
            scope,
        )


@pytest.mark.parametrize("required", [True, False])
def test_explicit_nullable_allows_present_null_without_inventing_value(required: bool) -> None:
    fields = [{"path": "value", "dataType": "STRING", "required": required, "nullable": True}]
    null_digest, null_count = live_read._validate_response_fields({"value": None}, fields)
    text_digest, _ = live_read._validate_response_fields({"value": "present"}, fields)
    assert null_count == 1 and null_digest != text_digest
    if required:
        with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
            live_read._validate_response_fields({}, fields)
    else:
        missing_digest, missing_count = live_read._validate_response_fields({}, fields)
        assert missing_count == 0 and missing_digest != null_digest


@pytest.mark.parametrize("required", [True, False])
def test_nonnullable_rejects_present_null_even_for_optional_field(required: bool) -> None:
    fields = [{"path": "value", "dataType": "STRING", "required": required, "nullable": False}]
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_response_fields({"value": None}, fields)


@pytest.mark.parametrize("nullable", [None, "true", 1])
def test_nullable_must_be_explicit_boolean(nullable: Any) -> None:
    field = {"path": "value", "dataType": "STRING", "required": True}
    if nullable is not None:
        field["nullable"] = nullable
    with pytest.raises(LiveReadError, match="^LIVE_READ_TARGET_INVALID$"):
        live_read._validate_response_fields({"value": None}, [field])


def test_nullable_parent_does_not_fabricate_child_presence() -> None:
    fields = [
        {"path": "parent", "dataType": "OBJECT", "required": True, "nullable": True},
        {"path": "parent.child", "dataType": "STRING", "required": True, "nullable": False},
    ]
    assert live_read._validate_response_fields({"parent": None}, fields)[1] == 1
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_response_fields({"parent": {}}, fields)


@pytest.mark.parametrize(
    ("data_type", "expected", "different"),
    [
        ("STRING", "1.1.0", "1.0.0"),
        ("STRING", "SourceObject__c", "OtherObject__c"),
        ("STRING", "USD", "EUR"),
        ("INTEGER", 20, 21),
        ("BOOLEAN", True, False),
    ],
)
def test_source_owned_literal_is_exact_not_merely_type_compatible(data_type, expected, different):
    field = {
        "path": "value",
        "dataType": data_type,
        "required": True,
        "nullable": False,
        "expectedLiteral": {"value": expected},
    }
    _, count = live_read._validate_response_fields({"value": expected}, [field])
    assert count == 1
    with pytest.raises(LiveReadError, match="RESPONSE_INVALID"):
        live_read._validate_response_fields({"value": different}, [field])


@pytest.mark.parametrize("required", [True, False])
def test_explicit_null_literal_preserves_required_vs_optional_presence(required):
    field = {
        "path": "value",
        "dataType": "STRING",
        "required": required,
        "nullable": True,
        "expectedLiteral": {"value": None},
    }
    assert live_read._validate_response_fields({"value": None}, [field])[1] == 1
    with pytest.raises(LiveReadError, match="RESPONSE_INVALID"):
        live_read._validate_response_fields({"value": "not-null"}, [field])
    if required:
        with pytest.raises(LiveReadError, match="RESPONSE_INVALID"):
            live_read._validate_response_fields({}, [field])
    else:
        assert live_read._validate_response_fields({}, [field])[1] == 0


@pytest.mark.parametrize(
    "literal", [{}, {"value": {}}, {"value": "x", "extra": True}, {"value": 1}]
)
def test_literal_wrapper_rejects_unknown_nonprimitive_or_type_confused_constraints(literal):
    field = {
        "path": "value",
        "dataType": "STRING",
        "required": True,
        "nullable": False,
        "expectedLiteral": literal,
    }
    with pytest.raises(LiveReadError, match="TARGET_INVALID"):
        live_read._validate_response_fields({"value": "x"}, [field])


def _custom_parent_scope() -> ResolvedDatasetScope:
    return ResolvedDatasetScope(
        target_sha256="1" * 64,
        dataset_target_sha256="2" * 64,
        object_api_name="OtherEntity__c",
        record_id_field="Id",
        record_ids=("a001234",),
        ownership_marker_field="Marker__c",
        field_projection=("Id", "Marker__c", "OwnerId"),
        member_predicate_values={"a001234": {"Marker__c": "owned", "OwnerId": ACTOR_ID}},
        expected_records=1,
        response_record_path="",
        field_paths={"Id": "entity.id"},
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_custom_children_require_same_independently_owned_parent(count: int) -> None:
    scope = _custom_parent_scope()
    bindings = [
        {
            "collectionPath": "history[]",
            "parentIdPath": "entityId",
            "datasetField": "Id",
            "maximumCardinality": 3,
        }
    ]
    result = {
        "entity": {"id": "a001234"},
        "history": [{"entityId": "a001234"} for _ in range(count)],
    }
    assert live_read._validate_dataset_response(result, scope, parent_bindings=bindings)[1] == 1


@pytest.mark.parametrize(
    "change", ["wrong_root", "wrong_child", "missing_child", "overflow", "extra_collection"]
)
def test_custom_parent_projection_cannot_authorize_foreign_or_missing_history(change: str) -> None:
    scope = _custom_parent_scope()
    bindings = [
        {
            "collectionPath": "history[]",
            "parentIdPath": "entityId",
            "datasetField": "Id",
            "maximumCardinality": 1,
        }
    ]
    result = {"entity": {"id": "a001234"}, "history": [{"entityId": "a001234"}]}
    if change == "wrong_root":
        result["entity"]["id"] = "foreign"
    elif change == "wrong_child":
        result["history"][0]["entityId"] = "foreign"
    elif change == "missing_child":
        result["history"][0] = {}
    elif change == "overflow":
        result["history"].append({"entityId": "a001234"})
    else:
        result["anotherHistory"] = [{"entityId": "foreign"}]
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._validate_dataset_response(result, scope, parent_bindings=bindings)


def test_all_member_expansion_rejects_omitted_invocation_before_any_command(tmp_path: Path) -> None:
    executor, port, runner, _ = _fixture(tmp_path, record_count=7)
    first = port.capture_value.resolved_variables[0]
    changed = first.model_copy(update={"rows": first.rows[:-1]})
    port.capture_value = port.capture_value.model_copy(
        update={"resolved_variables": (changed, *port.capture_value.resolved_variables[1:])}
    )
    with pytest.raises(LiveReadError, match="^LIVE_READ_AUTHORITY_INVALID$"):
        executor.execute()
    assert runner.invocations == []


def test_failed_member_never_produces_partial_passing_partition(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class FailOne(_Runner):
        def run(self, invocation):
            if (
                invocation.arguments[:3] == ("api", "request", "rest")
                and "a00000000000003" in invocation.arguments[3]
            ):
                self.invocations.append(invocation)
                return CliCompleted(1, b'{"error":"secret-canary"}')
            return super().run(invocation)

    caplog.set_level("WARNING", logger="neo_sf_q_intel.live_read_evidence")
    executor, _, runner, ledger = _fixture(tmp_path, runner=FailOne(), record_count=7)
    with pytest.raises(LiveReadError, match="^LIVE_READ_CLI_FAILED$"):
        executor.execute()
    assert len(runner.invocations) > 3
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()
    assert "secret-canary" not in caplog.text
    assert "LIVE_READ_CLI_FAILED" in caplog.text
