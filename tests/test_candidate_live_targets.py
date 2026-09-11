from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.candidate_live_targets import (
    CandidateLiveTargetContractError,
    CandidateLiveTargetInputs,
    CandidateLiveTargetTrustPins,
    CandidateScopeRelation,
    CandidateTargetObligationCatalog,
    CapturedProductPolicy,
    HostOwnedCandidateLiveTargetAdapter,
    ObligationDerivationProof,
    ProductPolicyRole,
    TargetPartition,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_target_plan import (
    ApexTestTarget,
    BrowserTarget,
    DatasetPredicate,
    DatasetTarget,
    MetadataTarget,
    ResponseField,
    RestTarget,
    RestVariable,
)
from neo_sf_q_intel.ontology import load_canonical_ontology, load_source_graph_profile
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.service import AssuranceService
from tests.test_foundation_pipeline import ROOT, _pipeline, _repository
from tests.test_workflow import source


class _Port:
    def __init__(self, inputs: CandidateLiveTargetInputs) -> None:
        self.inputs = inputs
        self.calls = 0

    def capture(self) -> CandidateLiveTargetInputs:
        self.calls += 1
        return self.inputs


def _bundle(tmp_path: Path, *, entity_name: str = "Entity__c"):
    service = AssuranceService(
        source(),
        InMemoryRunRepository(),
        foundation_pipeline=_pipeline(_repository(tmp_path, entity_name=entity_name)),
    )
    return service.analyze_current_candidate()


def _source_profile():
    ontology = load_canonical_ontology(ROOT / "config/ontology/canonical-ontology.json")
    return load_source_graph_profile(
        ROOT / "config/source-profiles/salesforce-dx-semantic-graph.json",
        ontology,
    )


def _scope_ids(bundle) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                seed.node_id
                for binding in bundle.operation_seed_artifact.bindings
                for seed in binding.seeds
            }
            | {seed.node_id for seed in bundle.operation_seed_artifact.indirect_seeds}
        )
    )


def _catalog_document(
    bundle,
    project_index_bytes: bytes,
    application_graph_bytes: bytes,
    *,
    source_ids: tuple[str, ...] | None = None,
    product_policies: tuple[CapturedProductPolicy, ...],
) -> dict[str, Any]:
    ids = _scope_ids(bundle) if source_ids is None else source_ids
    snapshot = json.loads(project_index_bytes)["sourceSnapshot"]
    profile = _source_profile()
    common = {"sourceEntityIds": ids}
    entity_api_name = (
        ids[0].removeprefix("object:")
        if len(ids) == 1 and ids[0].startswith("object:")
        else "Entity__c"
    )
    values = {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "authorizes_execution": False,
        "complete": True,
        "catalog_id": "fixture-source-operations",
        "catalog_version": "1.0.0",
        "candidate_bundle_sha256": bundle.bundle_sha256,
        "project_index_sha256": hashlib.sha256(project_index_bytes).hexdigest(),
        "application_graph_sha256": hashlib.sha256(application_graph_bytes).hexdigest(),
        "knowledge_source_snapshot_sha256": snapshot,
        "source_profile_sha256": profile.sha256,
        "product_policy_sha256s": {
            item.role: hashlib.sha256(item.artifact_bytes).hexdigest() for item in product_policies
        },
        "requirement_ids": ("REQ-SF-001", "REQ-UIA-001"),
        "valid_until": bundle.operation_seed_artifact.valid_until,
        "standard_rest": (
            RestTarget(
                **common,
                method="GET",
                routeTemplate="/services/data/{apiVersion}/sobjects/{recordId}",
                variables=(
                    RestVariable(
                        name="apiVersion",
                        valueSource="API_VERSION",
                        dataType="STRING",
                        datasetField=None,
                    ),
                    RestVariable(
                        name="recordId",
                        valueSource="SYNTHETIC_DATASET_RECEIPT",
                        dataType="ID",
                        datasetField="Id",
                    ),
                ),
                responseFields=(
                    ResponseField(path="Id", dataType="STRING", required=True, nullable=False),
                ),
                maximumResponseBytes=65536,
                datasetId="synthetic",
                minimumCardinality=1,
                maximumCardinality=1,
                requestExpansion="EACH_DATASET_RECORD",
                responseRecordPath="",
                datasetFieldPaths={"Id": "Id"},
                parentBindings=(),
            ),
        ),
        "custom_rest": (
            RestTarget(
                **common,
                method="GET",
                routeTemplate="/services/apexrest/example/{recordId}",
                variables=(
                    RestVariable(
                        name="recordId",
                        valueSource="SYNTHETIC_DATASET_RECEIPT",
                        dataType="ID",
                        datasetField="Id",
                    ),
                ),
                responseFields=(
                    ResponseField(path="status", dataType="STRING", required=True, nullable=False),
                    ResponseField(path="Id", dataType="STRING", required=True, nullable=False),
                ),
                maximumResponseBytes=65536,
                datasetId="synthetic",
                minimumCardinality=1,
                maximumCardinality=1,
                requestExpansion="EACH_DATASET_RECORD",
                responseRecordPath="",
                datasetFieldPaths={"Id": "Id"},
                parentBindings=(),
            ),
        ),
        "metadata": (
            MetadataTarget(
                **common,
                metadataType="CustomObject",
                member=entity_api_name,
                maximumFiles=4,
                maximumBytes=262144,
            ),
        ),
        "apex_tests": (
            ApexTestTarget(
                **common,
                obligationId="obligation:object-contract",
                apexClass="EntityPolicyTest",
                methodName="verifiesPolicy",
                maximumSeconds=120,
            ),
        ),
        "browser_intents": (
            BrowserTarget(
                **common,
                applicationIntent="record-assurance",
                actionClass="NAVIGATE",
                surfaceIntent="inspect-record-read-only",
            ),
        ),
        "synthetic_datasets": (
            DatasetTarget(
                **common,
                objectApiName=entity_api_name,
                fieldProjection=("Id", "Synthetic_Marker__c", "OwnerId"),
                identityField="Id",
                predicates=(
                    DatasetPredicate(
                        field="Synthetic_Marker__c",
                        operator="IN_SET",
                        valueSource="SOURCE_LITERAL_SET",
                        values=tuple(f"synthetic-{index:02d}" for index in range(10)),
                    ),
                    DatasetPredicate(
                        field="OwnerId",
                        operator="EQUALS",
                        valueSource="CURRENT_ENROLLED_ACTOR",
                        values=(),
                    ),
                ),
                ownershipMarkerField="Synthetic_Marker__c",
                maximumRecords=10,
                datasetId="synthetic",
            ),
        ),
    }
    groups = {
        TargetPartition.STANDARD_REST: values["standard_rest"],
        TargetPartition.CUSTOM_REST: values["custom_rest"],
        TargetPartition.METADATA: values["metadata"],
        TargetPartition.APEX_TEST: values["apex_tests"],
        TargetPartition.BROWSER_INTENT: values["browser_intents"],
        TargetPartition.SYNTHETIC_DATASET: values["synthetic_datasets"],
    }
    seed_by_id: dict[str, set[str]] = {}
    for seed in (
        *(seed for binding in bundle.operation_seed_artifact.bindings for seed in binding.seeds),
        *bundle.operation_seed_artifact.indirect_seeds,
    ):
        seed_by_id.setdefault(seed.node_id, set()).add(seed.seed_sha256)
    common_roles = {
        ProductPolicyRole.VERIFIED_CHANGE,
        ProductPolicyRole.OPERATION_SEED,
        ProductPolicyRole.RELEASE_INPUT,
        ProductPolicyRole.LIVE_TARGET,
    }
    proofs = []
    for partition, targets in groups.items():
        roles = set(common_roles)
        if partition is TargetPartition.BROWSER_INTENT:
            roles.add(ProductPolicyRole.BROWSER)
        if partition is TargetPartition.APEX_TEST:
            roles.add(ProductPolicyRole.TEST)
        for target in targets:
            proofs.append(
                ObligationDerivationProof(
                    partition=partition,
                    targetSha256=stable_sha256(target.model_dump(by_alias=True, mode="json")),
                    sourceEntityIds=tuple(sorted(target.source_entity_ids)),
                    sourceSeedSha256s=tuple(
                        sorted(
                            {
                                seed_sha
                                for item in target.source_entity_ids
                                for seed_sha in seed_by_id.get(item, {"0" * 64})
                            }
                        )
                    ),
                    policyRoles=tuple(sorted(roles, key=str)),
                )
            )
    values["derivation_proofs"] = tuple(
        sorted(proofs, key=lambda item: (item.partition.value, item.target_sha256))
    )
    body = CandidateTargetObligationCatalog.model_construct(**values).model_dump(mode="json")
    body["artifact_sha256"] = stable_sha256(body)
    catalog = CandidateTargetObligationCatalog.model_validate(body)
    return catalog.model_dump(by_alias=True, mode="json")


def _inputs(tmp_path: Path, *, entity_name: str = "Entity__c"):
    bundle = _bundle(tmp_path, entity_name=entity_name)
    project_index = (ROOT / "knowledge/project-index.json").read_bytes()
    application_graph = (ROOT / "knowledge/application-graph.json").read_bytes()
    policy_files = {
        ProductPolicyRole.VERIFIED_CHANGE: "config/verified-change-policy.json",
        ProductPolicyRole.OPERATION_SEED: "config/operation-seed-policy.json",
        ProductPolicyRole.RELEASE_INPUT: "config/release-input-policy.json",
        ProductPolicyRole.LIVE_TARGET: "config/live-target-policy.json",
        ProductPolicyRole.BROWSER: "config/live-salesforce-acceptance-profile.json",
        ProductPolicyRole.TEST: "config/quality-policy.json",
    }
    product_policies = tuple(
        CapturedProductPolicy(
            role=role,
            artifactBytes=(ROOT / path).read_bytes(),
        )
        for role, path in sorted(policy_files.items(), key=lambda item: str(item[0]))
    )
    catalog = _catalog_document(
        bundle,
        project_index,
        application_graph,
        product_policies=product_policies,
    )
    catalog_bytes = json.dumps(
        catalog, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    inputs = CandidateLiveTargetInputs(
        candidateBundle=bundle,
        projectIndexBytes=project_index,
        applicationGraphBytes=application_graph,
        sourceGraphProfile=_source_profile(),
        obligationCatalogBytes=catalog_bytes,
        productPolicies=product_policies,
    )
    pins = CandidateLiveTargetTrustPins(
        obligationCatalogSha256=hashlib.sha256(catalog_bytes).hexdigest(),
        productPolicySha256s={
            item.role: hashlib.sha256(item.artifact_bytes).hexdigest() for item in product_policies
        },
    )
    return inputs, catalog, pins


def _with_catalog(
    inputs: CandidateLiveTargetInputs,
    pins: CandidateLiveTargetTrustPins,
    catalog: dict[str, Any],
) -> tuple[CandidateLiveTargetInputs, CandidateLiveTargetTrustPins]:
    raw = json.dumps(catalog, separators=(",", ":"), sort_keys=True).encode()
    return (
        inputs.model_copy(update={"obligation_catalog_bytes": raw}),
        pins.model_copy(update={"obligation_catalog_sha256": hashlib.sha256(raw).hexdigest()}),
    )


def test_host_adapter_binds_complete_operation_aware_scope_without_authority(
    tmp_path: Path,
) -> None:
    inputs, _, pins = _inputs(tmp_path)
    port = _Port(inputs)

    result = HostOwnedCandidateLiveTargetAdapter(port, pins).derive()

    assert port.calls == 1
    assert result.authorizes_execution is False
    assert result.verified_scope.complete is True
    assert result.source_operation_profile.scope_artifact_sha256 == (
        result.verified_scope.artifact_sha256
    )
    assert {item.entity_id for item in result.verified_scope.entities} == set(
        _scope_ids(inputs.candidate_bundle)
    )
    assert {item.relation for item in result.operation_entities} == {
        CandidateScopeRelation.DIRECT_MODIFY
    }
    assert {item.side.value for item in result.operation_entities} == {"BASE", "CANDIDATE"}
    assert result.verified_scope.mandatory_obligation_ids == ("obligation:object-contract",)
    assert HostOwnedCandidateLiveTargetAdapter(port, pins).derive() == result


def test_entity_rename_does_not_change_control_flow(tmp_path: Path) -> None:
    inputs, _, pins = _inputs(tmp_path, entity_name="Renamed_Entity__c")

    result = HostOwnedCandidateLiveTargetAdapter(_Port(inputs), pins).derive()

    assert {item.canonical_class for item in result.verified_scope.entities} == {"data-entity"}
    assert {item.entity_id for item in result.verified_scope.entities} == {
        "object:Renamed_Entity__c"
    }


def test_omitted_or_invented_scope_is_blocked(tmp_path: Path) -> None:
    inputs, catalog, pins = _inputs(tmp_path)
    changed = _catalog_document(
        inputs.candidate_bundle,
        inputs.project_index_bytes,
        inputs.application_graph_bytes,
        source_ids=("object:Invented__c",),
        product_policies=inputs.product_policies,
    )
    narrowed, narrowed_pins = _with_catalog(inputs, pins, changed)
    with pytest.raises(CandidateLiveTargetContractError, match="invents scope"):
        HostOwnedCandidateLiveTargetAdapter(_Port(narrowed), narrowed_pins).derive()

    catalog["metadata"] = []
    omitted_partition, omitted_pins = _with_catalog(inputs, pins, catalog)
    with pytest.raises(CandidateLiveTargetContractError, match="Invalid target obligation"):
        HostOwnedCandidateLiveTargetAdapter(_Port(omitted_partition), omitted_pins).derive()
    assert catalog


def test_changed_knowledge_or_candidate_binding_fails_closed(tmp_path: Path) -> None:
    inputs, catalog, pins = _inputs(tmp_path)
    changed_index = inputs.project_index_bytes + b" "
    stale_knowledge = inputs.model_copy(update={"project_index_bytes": changed_index})
    with pytest.raises(CandidateLiveTargetContractError, match="Knowledge provenance"):
        HostOwnedCandidateLiveTargetAdapter(_Port(stale_knowledge), pins).derive()

    catalog["candidateBundleSha256"] = "f" * 64
    body = CandidateTargetObligationCatalog.model_validate(
        {**catalog, "candidateBundleSha256": inputs.candidate_bundle.bundle_sha256}
    ).model_dump(mode="json")
    body["candidate_bundle_sha256"] = "f" * 64
    body.pop("artifact_sha256")
    body["artifact_sha256"] = stable_sha256(body)
    stale_candidate, stale_pins = _with_catalog(
        inputs,
        pins,
        CandidateTargetObligationCatalog.model_validate(body).model_dump(
            by_alias=True, mode="json"
        ),
    )
    with pytest.raises(CandidateLiveTargetContractError, match="different candidate"):
        HostOwnedCandidateLiveTargetAdapter(_Port(stale_candidate), stale_pins).derive()


def test_host_catalog_policy_and_operation_proof_pins_fail_closed(tmp_path: Path) -> None:
    inputs, catalog, pins = _inputs(tmp_path)
    wrong_catalog_pin = pins.model_copy(update={"obligation_catalog_sha256": "f" * 64})
    with pytest.raises(CandidateLiveTargetContractError, match="not host-pinned"):
        HostOwnedCandidateLiveTargetAdapter(_Port(inputs), wrong_catalog_pin).derive()

    changed_policy = inputs.product_policies[-1].model_copy(
        update={"artifact_bytes": inputs.product_policies[-1].artifact_bytes + b" "}
    )
    stale_policy_inputs = inputs.model_copy(
        update={"product_policies": (*inputs.product_policies[:-1], changed_policy)}
    )
    with pytest.raises(CandidateLiveTargetContractError, match="policy pins are stale"):
        HostOwnedCandidateLiveTargetAdapter(_Port(stale_policy_inputs), pins).derive()

    body = CandidateTargetObligationCatalog.model_validate(catalog).model_dump(mode="json")
    body["derivation_proofs"][0]["source_seed_sha256s"] = ("f" * 64,)
    body.pop("artifact_sha256")
    body["artifact_sha256"] = stable_sha256(body)
    rebound_catalog = CandidateTargetObligationCatalog.model_validate(body).model_dump(
        by_alias=True, mode="json"
    )
    rebound_inputs, rebound_pins = _with_catalog(inputs, pins, rebound_catalog)
    with pytest.raises(CandidateLiveTargetContractError, match="exact operation seeds"):
        HostOwnedCandidateLiveTargetAdapter(_Port(rebound_inputs), rebound_pins).derive()

    invalid_partition = json.loads(json.dumps(catalog))
    invalid_partition["derivationProofs"][0]["partition"] = "UNSUPPORTED"
    with pytest.raises(ValidationError):
        CandidateTargetObligationCatalog.model_validate(invalid_partition)

    duplicate_target = json.loads(json.dumps(catalog))
    duplicate_target["metadata"].append(duplicate_target["metadata"][0])
    with pytest.raises(ValidationError, match="sorted and unique"):
        CandidateTargetObligationCatalog.model_validate(duplicate_target)


def test_duplicate_catalog_key_and_wildcard_metadata_are_rejected(tmp_path: Path) -> None:
    inputs, catalog, pins = _inputs(tmp_path)
    duplicate = b'{"schemaVersion":"1.0.0","schemaVersion":"1.0.0"}'
    with pytest.raises(CandidateLiveTargetContractError, match="Duplicate key"):
        HostOwnedCandidateLiveTargetAdapter(
            _Port(inputs.model_copy(update={"obligation_catalog_bytes": duplicate})), pins
        ).derive()

    catalog["metadata"][0]["member"] = "*"
    with pytest.raises(CandidateLiveTargetContractError, match="Invalid target obligation"):
        wildcard_inputs, wildcard_pins = _with_catalog(inputs, pins, catalog)
        HostOwnedCandidateLiveTargetAdapter(_Port(wildcard_inputs), wildcard_pins).derive()


def test_developer_trace_graph_cannot_authorize_or_block_product_obligations(
    tmp_path: Path,
) -> None:
    inputs, _, pins = _inputs(tmp_path)
    graph = json.loads(inputs.application_graph_bytes)
    graph["edges"] = [
        item
        for item in graph["edges"]
        if not (item["from"] == "requirement:REQ-SF-001" and item["to"] == "capability:tools.mcp")
    ]
    graph_bytes = json.dumps(
        graph, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    catalog = _catalog_document(
        inputs.candidate_bundle,
        inputs.project_index_bytes,
        graph_bytes,
        product_policies=inputs.product_policies,
    )
    catalog_bytes = json.dumps(catalog, separators=(",", ":"), sort_keys=True).encode()
    changed = inputs.model_copy(
        update={
            "application_graph_bytes": graph_bytes,
            "obligation_catalog_bytes": catalog_bytes,
        }
    )
    changed_pins = pins.model_copy(
        update={"obligation_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest()}
    )
    assert (
        HostOwnedCandidateLiveTargetAdapter(_Port(changed), changed_pins)
        .derive()
        .authorizes_execution
        is False
    )
