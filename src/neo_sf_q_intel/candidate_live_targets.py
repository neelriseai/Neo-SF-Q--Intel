from __future__ import annotations

import hashlib
import inspect
import json
import sys
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.graph_production import ProducedNode, TreeSide
from neo_sf_q_intel.live_target_plan import (
    ApexTestTarget,
    BrowserTarget,
    DatasetTarget,
    MetadataTarget,
    RestTarget,
    SemanticScopeEntity,
    SourceOperationProfile,
    VerifiedLiveTargetScope,
)
from neo_sf_q_intel.ontology import SourceGraphProfile, contract_sha256

_MAX_KNOWLEDGE_BYTES = 8 * 1024 * 1024
_MAX_CATALOG_BYTES = 1024 * 1024
_MAX_TARGETS = 256
_POLICY_BYTES = 2 * 1024 * 1024


class ProductPolicyRole(StrEnum):
    VERIFIED_CHANGE = "VERIFIED_CHANGE"
    OPERATION_SEED = "OPERATION_SEED"
    RELEASE_INPUT = "RELEASE_INPUT"
    LIVE_TARGET = "LIVE_TARGET"
    BROWSER = "BROWSER"
    TEST = "TEST"


class TargetPartition(StrEnum):
    STANDARD_REST = "STANDARD_REST"
    CUSTOM_REST = "CUSTOM_REST"
    METADATA = "METADATA"
    APEX_TEST = "APEX_TEST"
    BROWSER_INTENT = "BROWSER_INTENT"
    SYNTHETIC_DATASET = "SYNTHETIC_DATASET"


_REQUIRED_POLICY_ROLES = tuple(ProductPolicyRole)
_PARTITION_POLICY_ROLES: dict[TargetPartition, frozenset[ProductPolicyRole]] = {
    partition: frozenset(
        {
            ProductPolicyRole.VERIFIED_CHANGE,
            ProductPolicyRole.OPERATION_SEED,
            ProductPolicyRole.RELEASE_INPUT,
            ProductPolicyRole.LIVE_TARGET,
            *((ProductPolicyRole.BROWSER,) if partition is TargetPartition.BROWSER_INTENT else ()),
            *((ProductPolicyRole.TEST,) if partition is TargetPartition.APEX_TEST else ()),
        }
    )
    for partition in TargetPartition
}


class CandidateLiveTargetContractError(RuntimeError):
    """A host-captured candidate cannot be safely bound to a complete live target scope."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CapturedProductPolicy(_Model):
    role: ProductPolicyRole
    artifact_bytes: bytes = Field(alias="artifactBytes", min_length=2, max_length=_POLICY_BYTES)

    @model_validator(mode="after")
    def validate_policy(self) -> CapturedProductPolicy:
        _strict_json_object(self.artifact_bytes, f"{self.role.value} product policy")
        return self


class CandidateLiveTargetTrustPins(_Model):
    obligation_catalog_sha256: str = Field(
        alias="obligationCatalogSha256", pattern=r"^[a-f0-9]{64}$"
    )
    product_policy_sha256s: dict[ProductPolicyRole, str] = Field(alias="productPolicySha256s")

    @model_validator(mode="after")
    def validate_pins(self) -> CandidateLiveTargetTrustPins:
        if set(self.product_policy_sha256s) != set(_REQUIRED_POLICY_ROLES) or any(
            not _is_sha256(value) for value in self.product_policy_sha256s.values()
        ):
            raise ValueError("Product policy trust pins are incomplete")
        return self


class ObligationDerivationProof(_Model):
    partition: TargetPartition
    target_sha256: str = Field(alias="targetSha256", pattern=r"^[a-f0-9]{64}$")
    source_entity_ids: tuple[str, ...] = Field(alias="sourceEntityIds", min_length=1)
    source_seed_sha256s: tuple[str, ...] = Field(alias="sourceSeedSha256s", min_length=1)
    policy_roles: tuple[ProductPolicyRole, ...] = Field(alias="policyRoles", min_length=1)

    @model_validator(mode="after")
    def validate_proof(self) -> ObligationDerivationProof:
        if self.source_entity_ids != tuple(sorted(set(self.source_entity_ids))):
            raise ValueError("Proof entity IDs must be sorted and unique")
        if self.source_seed_sha256s != tuple(sorted(set(self.source_seed_sha256s))):
            raise ValueError("Proof seed identities must be sorted and unique")
        if self.policy_roles != tuple(sorted(set(self.policy_roles), key=str)):
            raise ValueError("Proof policy roles must be sorted and unique")
        if set(self.policy_roles) != _PARTITION_POLICY_ROLES[self.partition]:
            raise ValueError("Proof does not cite every mandatory product policy")
        return self


class CandidateTargetObligationCatalog(_Model):
    """Analysis-only typed obligations; the later host policy remains the authority plane."""

    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    authority_scope: Literal["ANALYSIS_ONLY"] = Field(alias="authorityScope")
    authorizes_execution: Literal[False] = Field(alias="authorizesExecution")
    complete: Literal[True]
    catalog_id: str = Field(alias="catalogId", min_length=1, max_length=200)
    catalog_version: str = Field(alias="catalogVersion")
    candidate_bundle_sha256: str = Field(alias="candidateBundleSha256", pattern=r"^[a-f0-9]{64}$")
    project_index_sha256: str = Field(alias="projectIndexSha256", pattern=r"^[a-f0-9]{64}$")
    application_graph_sha256: str = Field(alias="applicationGraphSha256", pattern=r"^[a-f0-9]{64}$")
    knowledge_source_snapshot_sha256: str = Field(
        alias="knowledgeSourceSnapshotSha256", pattern=r"^[a-f0-9]{64}$"
    )
    source_profile_sha256: str = Field(alias="sourceProfileSha256", pattern=r"^[a-f0-9]{64}$")
    product_policy_sha256s: dict[ProductPolicyRole, str] = Field(alias="productPolicySha256s")
    requirement_ids: tuple[str, ...] = Field(alias="requirementIds", min_length=1)
    valid_until: str = Field(alias="validUntil")
    standard_rest: tuple[RestTarget, ...] = Field(alias="standardRest", min_length=1)
    custom_rest: tuple[RestTarget, ...] = Field(alias="customRest", min_length=1)
    metadata: tuple[MetadataTarget, ...] = Field(min_length=1)
    apex_tests: tuple[ApexTestTarget, ...] = Field(alias="apexTests", min_length=1)
    browser_intents: tuple[BrowserTarget, ...] = Field(alias="browserIntents", min_length=1)
    synthetic_datasets: tuple[DatasetTarget, ...] = Field(alias="syntheticDatasets", min_length=1)
    derivation_proofs: tuple[ObligationDerivationProof, ...] = Field(
        alias="derivationProofs", min_length=1
    )
    artifact_sha256: str = Field(alias="artifactSha256", pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_catalog(self) -> CandidateTargetObligationCatalog:
        _require_semver(self.catalog_version)
        _parse_timestamp(self.valid_until)
        if self.requirement_ids != tuple(sorted(set(self.requirement_ids))):
            raise ValueError("Requirement IDs must be sorted and unique")
        if set(self.product_policy_sha256s) != set(_REQUIRED_POLICY_ROLES) or any(
            not _is_sha256(value) for value in self.product_policy_sha256s.values()
        ):
            raise ValueError("Product policy identities are incomplete")
        target_count = sum(
            len(values)
            for values in (
                self.standard_rest,
                self.custom_rest,
                self.metadata,
                self.apex_tests,
                self.browser_intents,
                self.synthetic_datasets,
            )
        )
        if target_count > _MAX_TARGETS:
            raise ValueError("Target obligation capacity exceeded")
        obligation_ids = tuple(item.obligation_id for item in self.apex_tests)
        if obligation_ids != tuple(sorted(set(obligation_ids))):
            raise ValueError("Apex obligation IDs must be sorted and unique")
        for values in (
            self.standard_rest,
            self.custom_rest,
            self.metadata,
            self.apex_tests,
            self.browser_intents,
            self.synthetic_datasets,
        ):
            identities = tuple(
                stable_sha256(item.model_dump(by_alias=True, mode="json")) for item in values
            )
            if identities != tuple(sorted(set(identities))):
                raise ValueError("Target obligations must be sorted and unique")
        for item in (*self.standard_rest, *self.custom_rest):
            if len(item.response_fields) > 128 or "*" in item.route_template:
                raise ValueError("REST obligation is unbounded")
        for item in self.metadata:
            if "*" in item.metadata_type or "*" in item.member:
                raise ValueError("Metadata obligation cannot contain a wildcard")
        for item in self.synthetic_datasets:
            if len(item.field_projection) > 128:
                raise ValueError("Dataset projection capacity exceeded")
        proof_keys = tuple(
            (item.partition.value, item.target_sha256) for item in self.derivation_proofs
        )
        if proof_keys != tuple(sorted(set(proof_keys))):
            raise ValueError("Derivation proofs must be sorted and unique")
        _verify_digest(self, "artifact_sha256")
        return self


class CandidateScopeRelation(StrEnum):
    DIRECT_ADD = "DIRECT_ADD"
    DIRECT_DELETE = "DIRECT_DELETE"
    DIRECT_MODIFY = "DIRECT_MODIFY"
    INDIRECT_PROPAGATION = "INDIRECT_PROPAGATION"


class OperationScopedEntity(_Model):
    relation: CandidateScopeRelation
    side: TreeSide
    entity_id: str = Field(alias="entityId", min_length=1, max_length=4600)
    canonical_class: str = Field(alias="canonicalClass", min_length=1, max_length=200)
    seed_sha256: str = Field(alias="seedSha256", pattern=r"^[a-f0-9]{64}$")
    operation_binding_sha256: str | None = Field(
        default=None,
        alias="operationBindingSha256",
        pattern=r"^[a-f0-9]{64}$",
    )


class CandidateLiveTargetInputs(_Model):
    candidate_bundle: CandidateAssuranceBundle = Field(alias="candidateBundle")
    project_index_bytes: bytes = Field(
        alias="projectIndexBytes", min_length=2, max_length=_MAX_KNOWLEDGE_BYTES
    )
    application_graph_bytes: bytes = Field(
        alias="applicationGraphBytes", min_length=2, max_length=_MAX_KNOWLEDGE_BYTES
    )
    source_graph_profile: SourceGraphProfile = Field(alias="sourceGraphProfile")
    obligation_catalog_bytes: bytes = Field(
        alias="obligationCatalogBytes", min_length=2, max_length=_MAX_CATALOG_BYTES
    )
    product_policies: tuple[CapturedProductPolicy, ...] = Field(
        alias="productPolicies",
        min_length=len(_REQUIRED_POLICY_ROLES),
        max_length=len(_REQUIRED_POLICY_ROLES),
    )


class CandidateLiveTargetInputPort(Protocol):
    """The host selects current files and candidate state; callers supply no target subset."""

    def capture(self) -> CandidateLiveTargetInputs: ...


class CandidateLiveTargetDerivation(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["TARGET_DERIVATION_ONLY"] = "TARGET_DERIVATION_ONLY"
    authorizes_execution: Literal[False] = False
    candidate_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    application_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    knowledge_source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    obligation_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    adapter_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_entities: tuple[OperationScopedEntity, ...] = Field(min_length=1)
    verified_scope: VerifiedLiveTargetScope
    source_operation_profile: SourceOperationProfile
    source_operation_profile_bytes: bytes
    derivation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_derivation(self) -> CandidateLiveTargetDerivation:
        keys = tuple(
            (item.relation.value, item.side.value, item.entity_id, item.seed_sha256)
            for item in self.operation_entities
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Operation-scoped entities must be sorted and unique")
        expected_bytes = _canonical_json_bytes(
            self.source_operation_profile.model_dump(by_alias=True, mode="json")
        )
        if self.source_operation_profile_bytes != expected_bytes:
            raise ValueError("Source-operation profile bytes are not canonical")
        if (
            self.source_operation_profile.scope_artifact_sha256
            != self.verified_scope.artifact_sha256
        ):
            raise ValueError("Source-operation profile is not bound to verified scope")
        _verify_digest(self, "derivation_sha256")
        return self


class HostOwnedCandidateLiveTargetAdapter:
    """Bind the current host candidate and complete obligations without caller target input."""

    def __init__(
        self,
        input_port: CandidateLiveTargetInputPort,
        trust_pins: CandidateLiveTargetTrustPins,
    ) -> None:
        self._input_port = input_port
        self._trust_pins = CandidateLiveTargetTrustPins.model_validate(
            trust_pins.model_dump(mode="python")
        )

    def derive(self) -> CandidateLiveTargetDerivation:
        captured_value = self._input_port.capture()
        captured = CandidateLiveTargetInputs.model_validate(
            captured_value.model_dump(mode="python")
        )
        bundle = CandidateAssuranceBundle.model_validate(
            captured.candidate_bundle.model_dump(mode="json")
        )
        catalog = _load_catalog(captured.obligation_catalog_bytes)
        catalog_file_sha = hashlib.sha256(captured.obligation_catalog_bytes).hexdigest()
        if catalog_file_sha != self._trust_pins.obligation_catalog_sha256:
            raise CandidateLiveTargetContractError("Obligation catalog is not host-pinned")
        policies = _verify_product_policies(
            captured.product_policies, bundle, catalog, self._trust_pins
        )
        project_index = _strict_json_object(captured.project_index_bytes, "project index")
        application_graph = _strict_json_object(
            captured.application_graph_bytes, "application graph"
        )

        project_index_sha = hashlib.sha256(captured.project_index_bytes).hexdigest()
        application_graph_sha = hashlib.sha256(captured.application_graph_bytes).hexdigest()
        self._verify_capture_bindings(
            bundle,
            catalog,
            captured.source_graph_profile,
            project_index,
            application_graph,
            project_index_sha,
            application_graph_sha,
            policies,
        )
        entities, operation_entities = _derive_operation_entities(
            bundle, captured.source_graph_profile
        )
        _verify_complete_target_coverage(catalog, entities, operation_entities)

        valid_until = min(
            (
                bundle.graph_production_artifact.valid_until,
                bundle.operation_seed_artifact.valid_until,
                catalog.valid_until,
            ),
            key=_parse_timestamp,
        )
        scope = _sealed(
            VerifiedLiveTargetScope,
            "artifact_sha256",
            schema_version="1.0.0",
            producer_kind="HOST_VERIFIED_CHANGE_GRAPH_SCOPE",
            complete=True,
            project_id=bundle.foundation.project_id,
            source_snapshot_sha256=(bundle.operation_seed_artifact.source_snapshot_sha256),
            verified_change_sha256=bundle.verified_change_manifest_sha256,
            semantic_graph_sha256=bundle.graph_production_receipt_sha256,
            ontology_sha256=bundle.graph_production_artifact.ontology_sha256,
            source_profile_sha256=bundle.graph_production_artifact.source_profile_sha256,
            entities=entities,
            mandatory_obligation_ids=tuple(item.obligation_id for item in catalog.apex_tests),
            valid_until=valid_until,
        )
        profile = SourceOperationProfile(
            schemaVersion="1.0.0",
            profileId=catalog.catalog_id,
            profileVersion=catalog.catalog_version,
            scopeArtifactSha256=scope.artifact_sha256,
            standardRest=catalog.standard_rest,
            customRest=catalog.custom_rest,
            metadata=catalog.metadata,
            apexTests=catalog.apex_tests,
            browserIntents=catalog.browser_intents,
            syntheticDatasets=catalog.synthetic_datasets,
        )
        profile_bytes = _canonical_json_bytes(profile.model_dump(by_alias=True, mode="json"))
        body = {
            "schema_version": "1.0.0",
            "authority_scope": "TARGET_DERIVATION_ONLY",
            "authorizes_execution": False,
            "candidate_bundle_sha256": bundle.bundle_sha256,
            "project_index_sha256": project_index_sha,
            "application_graph_sha256": application_graph_sha,
            "knowledge_source_snapshot_sha256": catalog.knowledge_source_snapshot_sha256,
            "obligation_catalog_sha256": catalog.artifact_sha256,
            "adapter_implementation_sha256": _implementation_sha256(),
            "operation_entities": [item.model_dump(mode="json") for item in operation_entities],
            "verified_scope": scope.model_dump(mode="json"),
            "source_operation_profile": profile.model_dump(mode="json"),
            "source_operation_profile_bytes": profile_bytes.decode("utf-8"),
        }
        return CandidateLiveTargetDerivation.model_validate(
            {**body, "derivation_sha256": stable_sha256(body)}
        )

    @staticmethod
    def _verify_capture_bindings(
        bundle: CandidateAssuranceBundle,
        catalog: CandidateTargetObligationCatalog,
        source_profile: SourceGraphProfile,
        project_index: dict[str, Any],
        application_graph: dict[str, Any],
        project_index_sha: str,
        application_graph_sha: str,
        policies: dict[ProductPolicyRole, CapturedProductPolicy],
    ) -> None:
        if catalog.candidate_bundle_sha256 != bundle.bundle_sha256:
            raise CandidateLiveTargetContractError("Obligations target a different candidate")
        if set(policies) != set(_REQUIRED_POLICY_ROLES):
            raise CandidateLiveTargetContractError("Product policy set is incomplete")
        if (
            catalog.project_index_sha256 != project_index_sha
            or catalog.application_graph_sha256 != application_graph_sha
        ):
            raise CandidateLiveTargetContractError("Knowledge provenance has changed")
        snapshots = (
            project_index.get("sourceSnapshot"),
            application_graph.get("sourceSnapshot"),
            catalog.knowledge_source_snapshot_sha256,
        )
        if len(set(snapshots)) != 1 or not all(
            isinstance(value, str) and _is_sha256(value) for value in snapshots
        ):
            raise CandidateLiveTargetContractError("Knowledge snapshots are not identical")
        if application_graph.get("authorization") != (
            "Discovery only; graph edges grant no tool or write authority."
        ):
            raise CandidateLiveTargetContractError("Knowledge graph authority boundary is missing")
        _verify_trace_inputs_are_non_authoritative(project_index, application_graph)
        profile_body = source_profile.model_dump(by_alias=True, mode="json")
        if (
            source_profile.sha256 != contract_sha256(profile_body)
            or source_profile.sha256 != catalog.source_profile_sha256
            or source_profile.sha256 != bundle.graph_production_artifact.source_profile_sha256
            or source_profile.ontology.ontology_sha256
            != bundle.graph_production_artifact.ontology_sha256
        ):
            raise CandidateLiveTargetContractError("Source graph profile identity mismatch")


def _derive_operation_entities(
    bundle: CandidateAssuranceBundle,
    source_profile: SourceGraphProfile,
    *,
    accounted_nonruntime_binding_sha256s: frozenset[str] = frozenset(),
) -> tuple[tuple[SemanticScopeEntity, ...], tuple[OperationScopedEntity, ...]]:
    graph = bundle.graph_production_artifact
    nodes: dict[TreeSide, dict[str, ProducedNode]] = {
        TreeSide.BASE: {item.node_id: item for item in graph.base.nodes},
        TreeSide.CANDIDATE: {item.node_id: item for item in graph.candidate.nodes},
    }
    mapping = source_profile.node_mapping
    operation_entities: list[OperationScopedEntity] = []
    classes_by_id: dict[str, str] = {}

    def add(
        *,
        side: TreeSide,
        node_id: str,
        seed_sha256: str,
        relation: CandidateScopeRelation,
        binding_sha256: str | None,
    ) -> None:
        node = nodes[side].get(node_id)
        if node is None:
            raise CandidateLiveTargetContractError("Operation seed is absent from its graph side")
        canonical_class = mapping.get(node.raw_kind)
        if canonical_class is None:
            raise CandidateLiveTargetContractError("Operation seed has no canonical class")
        previous = classes_by_id.setdefault(node_id, canonical_class)
        if previous != canonical_class:
            raise CandidateLiveTargetContractError(
                "Semantic entity changes canonical class by side"
            )
        operation_entities.append(
            OperationScopedEntity(
                relation=relation,
                side=side,
                entityId=node_id,
                canonicalClass=canonical_class,
                seedSha256=seed_sha256,
                operationBindingSha256=binding_sha256,
            )
        )

    for binding in bundle.operation_seed_artifact.bindings:
        if not binding.seeds:
            if binding.binding_sha256 in accounted_nonruntime_binding_sha256s:
                continue
            raise CandidateLiveTargetContractError(
                "A changed file has no source-derived semantic target"
            )
        relation = CandidateScopeRelation(f"DIRECT_{binding.change.operation.value}")
        for seed in binding.seeds:
            add(
                side=seed.side,
                node_id=seed.node_id,
                seed_sha256=seed.seed_sha256,
                relation=relation,
                binding_sha256=binding.binding_sha256,
            )
    for seed in bundle.operation_seed_artifact.indirect_seeds:
        add(
            side=seed.side,
            node_id=seed.node_id,
            seed_sha256=seed.seed_sha256,
            relation=CandidateScopeRelation.INDIRECT_PROPAGATION,
            binding_sha256=None,
        )
    if not operation_entities:
        raise CandidateLiveTargetContractError("Candidate has no semantic live target scope")
    scoped = tuple(
        SemanticScopeEntity(entityId=entity_id, canonicalClass=canonical_class)
        for entity_id, canonical_class in sorted(classes_by_id.items())
    )
    operation_entities_tuple = tuple(
        sorted(
            operation_entities,
            key=lambda item: (
                item.relation.value,
                item.side.value,
                item.entity_id,
                item.seed_sha256,
            ),
        )
    )
    return scoped, operation_entities_tuple


def _verify_complete_target_coverage(
    catalog: CandidateTargetObligationCatalog,
    entities: tuple[SemanticScopeEntity, ...],
    operation_entities: tuple[OperationScopedEntity, ...],
) -> None:
    groups = (
        (TargetPartition.STANDARD_REST, catalog.standard_rest),
        (TargetPartition.CUSTOM_REST, catalog.custom_rest),
        (TargetPartition.METADATA, catalog.metadata),
        (TargetPartition.APEX_TEST, catalog.apex_tests),
        (TargetPartition.BROWSER_INTENT, catalog.browser_intents),
        (TargetPartition.SYNTHETIC_DATASET, catalog.synthetic_datasets),
    )
    expected = {item.entity_id for item in entities}
    seed_ids_by_entity: dict[str, set[str]] = {entity_id: set() for entity_id in expected}
    for item in operation_entities:
        seed_ids_by_entity[item.entity_id].add(item.seed_sha256)
    targets: dict[tuple[TargetPartition, str], Any] = {}
    for partition, group in groups:
        covered: set[str] = set()
        for item in group:
            source_ids = set(item.source_entity_ids)
            if not source_ids.issubset(expected):
                raise CandidateLiveTargetContractError("Target obligation invents scope")
            covered.update(source_ids)
            target_sha = stable_sha256(item.model_dump(by_alias=True, mode="json"))
            targets[(partition, target_sha)] = item
        if covered != expected:
            raise CandidateLiveTargetContractError(
                f"{partition.value} obligations do not exactly cover every derived entity"
            )
    proofs = {(item.partition, item.target_sha256): item for item in catalog.derivation_proofs}
    if set(proofs) != set(targets):
        raise CandidateLiveTargetContractError("Obligation derivation proof set is incomplete")
    for key, target in targets.items():
        proof = proofs[key]
        expected_source_ids = tuple(sorted(target.source_entity_ids))
        expected_seed_ids = tuple(
            sorted(
                {
                    seed_sha
                    for entity_id in expected_source_ids
                    for seed_sha in seed_ids_by_entity[entity_id]
                }
            )
        )
        if (
            proof.source_entity_ids != expected_source_ids
            or proof.source_seed_sha256s != expected_seed_ids
        ):
            raise CandidateLiveTargetContractError(
                "Obligation proof is not derived from exact operation seeds"
            )


def _verify_trace_inputs_are_non_authoritative(
    project_index: dict[str, Any], application_graph: dict[str, Any]
) -> None:
    if not isinstance(project_index.get("requirements"), list):
        raise CandidateLiveTargetContractError("Project index trace collection is unavailable")
    if not isinstance(application_graph.get("nodes"), list) or not isinstance(
        application_graph.get("edges"), list
    ):
        raise CandidateLiveTargetContractError("Application graph trace collection is unavailable")
    if application_graph.get("authorization") != (
        "Discovery only; graph edges grant no tool or write authority."
    ):
        raise CandidateLiveTargetContractError("Knowledge graph authority boundary is missing")


def _verify_product_policies(
    captured: tuple[CapturedProductPolicy, ...],
    bundle: CandidateAssuranceBundle,
    catalog: CandidateTargetObligationCatalog,
    trust_pins: CandidateLiveTargetTrustPins,
) -> dict[ProductPolicyRole, CapturedProductPolicy]:
    roles = tuple(item.role for item in captured)
    if roles != tuple(sorted(set(roles), key=str)) or set(roles) != set(_REQUIRED_POLICY_ROLES):
        raise CandidateLiveTargetContractError("Product policy set is incomplete or duplicated")
    policies = {item.role: item for item in captured}
    actual_pins = {
        role: hashlib.sha256(item.artifact_bytes).hexdigest() for role, item in policies.items()
    }
    if (
        actual_pins != trust_pins.product_policy_sha256s
        or actual_pins != catalog.product_policy_sha256s
    ):
        raise CandidateLiveTargetContractError("Obligation catalog policy pins are stale")

    declared_policy_identities: dict[ProductPolicyRole, str] = {}
    for role in (ProductPolicyRole.VERIFIED_CHANGE, ProductPolicyRole.OPERATION_SEED):
        policy = policies[role]
        document = _strict_json_object(policy.artifact_bytes, f"{role.value} product policy")
        declared = document.get("sha256")
        if not isinstance(declared, str) or not _is_sha256(declared):
            raise CandidateLiveTargetContractError("Product policy has no declared identity")
        declared_policy_identities[role] = declared
    if (
        declared_policy_identities[ProductPolicyRole.VERIFIED_CHANGE]
        != bundle.graph_production_artifact.verified_change_policy_sha256
        or declared_policy_identities[ProductPolicyRole.OPERATION_SEED]
        != bundle.operation_seed_artifact.policy_sha256
    ):
        raise CandidateLiveTargetContractError(
            "Candidate graph or operation seeds use different product policies"
        )
    return policies


def _load_catalog(raw: bytes) -> CandidateTargetObligationCatalog:
    try:
        return CandidateTargetObligationCatalog.model_validate(
            _strict_json_object(raw, "target obligation catalog")
        )
    except ValidationError as exc:
        raise CandidateLiveTargetContractError("Invalid target obligation catalog") from exc


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise CandidateLiveTargetContractError(f"Duplicate key in {label}")
            output[key] = value
        return output

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except CandidateLiveTargetContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateLiveTargetContractError(f"Invalid {label}") from exc
    if not isinstance(value, dict):
        raise CandidateLiveTargetContractError(f"Invalid {label}")
    return value


def _sealed(model: type[_Model], digest_field: str, **values: Any) -> Any:
    body = model.model_construct(**values).model_dump(mode="json")
    body[digest_field] = stable_sha256(body)
    return model.model_validate(body)


def _verify_digest(value: _Model, field: str) -> None:
    body = value.model_dump(mode="json")
    declared = body.pop(field)
    if declared != stable_sha256(body):
        raise ValueError(f"{field} mismatch")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _implementation_sha256() -> str:
    source = inspect.getsource(sys.modules[__name__]).encode()
    return hashlib.sha256(source).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Timestamp must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must have a timezone")
    return parsed


def _require_semver(value: str) -> None:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Version must be semantic")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
