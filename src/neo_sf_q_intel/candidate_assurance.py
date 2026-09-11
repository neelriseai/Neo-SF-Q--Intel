from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neo_sf_q_intel.domain import (
    AssuranceRun,
    ChangeIntent,
    ChangeRequest,
    DecisionCode,
    RunStatus,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.foundation_pipeline import (
    CandidateFoundationEvidence,
    CandidateFoundationResult,
)
from neo_sf_q_intel.graph_production import (
    GraphElementOwner,
    GraphProductionArtifact,
    ProducedGraphSide,
    TreeSide,
    materialize_source_attested_reasoning_graph,
)
from neo_sf_q_intel.ontology import CanonicalOntology, SourceGraphProfile
from neo_sf_q_intel.operation_seed import OperationSeedArtifact
from neo_sf_q_intel.reasoning_workflow import ReasoningWorkflowResult
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot, canonical_project_id
from neo_sf_q_intel.specialist import (
    PromptEnvelope,
    ProviderCallOutcome,
    ProviderProfile,
    SpecialistProposalArtifact,
    provider_capture_sha256,
)

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class CandidateAssuranceContractError(RuntimeError):
    """The verified candidate cannot be represented without losing evidence."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateOperationBinding(_Model):
    operation: Literal["ADD", "MODIFY", "DELETE"]
    path: str = Field(min_length=1, max_length=4096)
    change_identity_sha256: Sha256
    operation_seed_binding_sha256: Sha256
    file_root_id: str = Field(min_length=1, max_length=4600)
    file_evidence_sha256: Sha256
    semantic_seed_ids: tuple[str, ...]
    semantic_seed_sha256s: tuple[Sha256, ...]

    @model_validator(mode="after")
    def validate_binding(self) -> CandidateOperationBinding:
        if self.semantic_seed_ids != tuple(sorted(set(self.semantic_seed_ids))):
            raise ValueError("Direct semantic seed IDs must be sorted and unique")
        if self.semantic_seed_sha256s != tuple(sorted(set(self.semantic_seed_sha256s))):
            raise ValueError("Direct semantic seed receipts must be sorted and unique")
        if len(self.semantic_seed_ids) != len(self.semantic_seed_sha256s):
            raise ValueError("Direct semantic seed IDs and receipts differ")
        return self


class CandidateSpecialistCapture(_Model):
    """Private replay material for one provider-backed advisory stage.

    ``ProviderCallOutcome.raw_response`` is intentionally excluded by that public model,
    so the candidate bundle stores a bounded replay copy here instead of relying on
    ordinary ``model_dump`` output.  Public candidate views expose only hashes/statuses.
    """

    workflow_result: ReasoningWorkflowResult
    artifact: SpecialistProposalArtifact
    prompt: PromptEnvelope
    provider_profile: ProviderProfile
    provider_outcome: dict[str, Any]
    provider_raw_response: str | None = Field(default=None, max_length=262_144)
    expected_provider_profile_sha256: Sha256
    expected_provider_capture_sha256: Sha256
    capture_sha256: Sha256

    @model_validator(mode="after")
    def validate_capture(self) -> CandidateSpecialistCapture:
        replay_outcome = ProviderCallOutcome.model_validate(self.provider_outcome)
        if replay_outcome.raw_response != self.provider_raw_response:
            raise ValueError("Provider raw response differs from private replay field")
        if self.expected_provider_profile_sha256 != self.provider_profile.profile_sha256:
            raise ValueError("Provider profile root differs from expected replay root")
        if self.expected_provider_capture_sha256 != provider_capture_sha256(replay_outcome):
            raise ValueError("Provider capture root differs from replay outcome")
        if (
            self.artifact.provider_receipt.provider_profile_sha256
            != self.provider_profile.profile_sha256
        ):
            raise ValueError("Artifact receipt differs from provider profile")
        body = self.model_dump(mode="json")
        declared = body.pop("capture_sha256")
        if declared != stable_sha256(body):
            raise ValueError("Candidate specialist capture digest is invalid")
        return self


class CandidateSideAssurance(_Model):
    side: TreeSide
    operation_scope: tuple[Literal["ADD", "MODIFY", "DELETE"], ...] = Field(min_length=1)
    changed_paths: tuple[str, ...] = Field(min_length=1, max_length=500)
    verified_seed_ids: tuple[str, ...] = Field(min_length=1, max_length=500)
    operation_bindings: tuple[CandidateOperationBinding, ...] = Field(min_length=1)
    indirect_seed_ids: tuple[str, ...]
    indirect_seed_sha256s: tuple[Sha256, ...]
    graph_side_receipt_sha256: Sha256
    operation_seed_side_receipt_sha256: Sha256
    producer_raw_graph_sha256: Sha256
    analysis_normalized_graph_sha256: Sha256
    run: AssuranceRun
    specialist_captures: tuple[CandidateSpecialistCapture, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_binding(self) -> CandidateSideAssurance:
        expected_operations = {
            TreeSide.BASE: ("DELETE", "MODIFY"),
            TreeSide.CANDIDATE: ("ADD", "MODIFY"),
        }[self.side]
        if self.operation_scope != tuple(
            value for value in expected_operations if value in self.operation_scope
        ):
            raise ValueError("Operation scope is not canonical for its graph side")
        if self.changed_paths != tuple(sorted(set(self.changed_paths))):
            raise ValueError("Changed paths must be sorted and unique")
        if self.verified_seed_ids != tuple(sorted(set(self.verified_seed_ids))):
            raise ValueError("Verified seed IDs must be sorted and unique")
        binding_keys = tuple((item.path, item.operation) for item in self.operation_bindings)
        if binding_keys != tuple(sorted(set(binding_keys))):
            raise ValueError("Operation bindings must be sorted and unique")
        if any(item.operation not in expected_operations for item in self.operation_bindings):
            raise ValueError("Operation binding is invalid for its graph side")
        if self.operation_scope != tuple(
            value
            for value in expected_operations
            if any(item.operation == value for item in self.operation_bindings)
        ):
            raise ValueError("Operation scope differs from exact seed bindings")
        if self.changed_paths != tuple(sorted({item.path for item in self.operation_bindings})):
            raise ValueError("Changed paths differ from exact seed bindings")
        if self.indirect_seed_ids != tuple(sorted(set(self.indirect_seed_ids))):
            raise ValueError("Indirect seed IDs must be sorted and unique")
        if self.indirect_seed_sha256s != tuple(sorted(set(self.indirect_seed_sha256s))):
            raise ValueError("Indirect seed receipts must be sorted and unique")
        if len(self.indirect_seed_ids) != len(self.indirect_seed_sha256s):
            raise ValueError("Indirect seed IDs and receipts differ")
        attested_ids = {
            value
            for item in self.operation_bindings
            for value in (item.file_root_id, *item.semantic_seed_ids)
        } | set(self.indirect_seed_ids)
        if self.verified_seed_ids != tuple(sorted(attested_ids)):
            raise ValueError("Verified seed IDs differ from attested operation roots")
        if self.graph_side_receipt_sha256 != self.operation_seed_side_receipt_sha256:
            raise ValueError("Operation seeds are not bound to the exact graph side")
        request = self.run.request
        for capture in self.specialist_captures:
            if (
                capture.workflow_result.run_id != self.run.run_id
                or capture.workflow_result.trace_id != self.run.trace_id
                or capture.workflow_result.project_id != request.project_id
                or capture.workflow_result.source_snapshot != self.run.source_snapshot
                or capture.workflow_result.source_graph_sha256 != self.run.source_graph_sha256
                or capture.workflow_result.normalized_graph_sha256
                != self.run.normalized_graph_sha256
                or capture.artifact.project_id != request.project_id
                or capture.artifact.source_snapshot != self.run.source_snapshot
                or capture.artifact.source_graph_sha256 != self.run.source_graph_sha256
                or capture.artifact.normalized_graph_sha256 != self.run.normalized_graph_sha256
            ):
                raise ValueError("Specialist capture is not bound to its side run")
        if (
            request.change_intent is not ChangeIntent.VERIFIED_CHANGE
            or tuple(request.changed_paths) != self.changed_paths
            or tuple(request.verified_seed_ids) != self.verified_seed_ids
            or request.source_ref != f"verified-{self.side.value.casefold()}"
            or request.verified_change_manifest_sha256 is None
            or request.verified_operation_seed_sha256 is None
            or self.run.source_snapshot is None
            or self.run.source_graph_sha256 != self.producer_raw_graph_sha256
            or self.run.normalized_graph_sha256 != self.analysis_normalized_graph_sha256
        ):
            raise ValueError("Assurance run is not bound to its verified side inputs")
        return self


class CandidateAnalysisIdentity(_Model):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,199}$")
    ontology_id: str = Field(min_length=1, max_length=200)
    ontology_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    ontology_sha256: Sha256
    source_profile_id: str = Field(min_length=1, max_length=200)
    source_profile_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    source_profile_sha256: Sha256
    reasoning_policy_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    reasoning_policy_sha256: Sha256
    reasoning_eval_set_id: str = Field(min_length=1, max_length=200)
    reasoning_eval_set_sha256: Sha256


class CandidateAssuranceBundle(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    evidence_completeness: Literal["INCOMPLETE"] = "INCOMPLETE"
    foundation: CandidateFoundationEvidence
    graph_production_artifact: GraphProductionArtifact
    operation_seed_artifact: OperationSeedArtifact
    verified_change_manifest_sha256: Sha256
    graph_production_receipt_sha256: Sha256
    operation_seed_artifact_sha256: Sha256
    analysis_identity: CandidateAnalysisIdentity
    analyses: tuple[CandidateSideAssurance, ...] = Field(min_length=1, max_length=2)
    blocking_gap_codes: tuple[str, ...] = Field(min_length=1)
    checkpoint_commit_scope: Literal["EXCLUDED_FROM_BUNDLE_TRANSACTION"] = (
        "EXCLUDED_FROM_BUNDLE_TRANSACTION"
    )
    persistence_gap_codes: tuple[Literal["CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE"]] = (
        "CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE",
    )
    bundle_sha256: Sha256

    @model_validator(mode="after")
    def validate_bundle(self) -> CandidateAssuranceBundle:
        if not self.foundation.foundation_execution_complete:
            raise ValueError("An assurance bundle requires a complete replayed foundation")
        if self.foundation.release_eligible or self.release_eligible:
            raise ValueError("Candidate analysis cannot grant release authority")
        expected_gaps = tuple(
            sorted(
                {
                    *self.foundation.blocking_gap_codes,
                    *self.persistence_gap_codes,
                }
            )
        )
        if self.blocking_gap_codes != expected_gaps:
            raise ValueError("Bundle gaps differ from foundation and persistence boundaries")
        if (
            self.graph_production_artifact.receipt_sha256 != self.graph_production_receipt_sha256
            or self.operation_seed_artifact.artifact_sha256 != self.operation_seed_artifact_sha256
            or self.graph_production_artifact.verified_change_manifest_sha256
            != self.verified_change_manifest_sha256
            or self.operation_seed_artifact.verified_change_manifest_sha256
            != self.verified_change_manifest_sha256
            or self.operation_seed_artifact.graph_production_receipt_sha256
            != self.graph_production_receipt_sha256
        ):
            raise ValueError("Embedded artifacts differ from bundle receipt bindings")
        graph = self.graph_production_artifact
        seeds = self.operation_seed_artifact
        identity = self.analysis_identity
        if (
            self.foundation.project_id != graph.project_id
            or identity.project_id != canonical_project_id(graph.project_id)
            or seeds.project_id != graph.project_id
            or seeds.repository_identity_sha256 != graph.repository_identity_sha256
            or seeds.ontology_sha256 != graph.ontology_sha256
            or seeds.source_profile_sha256 != graph.source_profile_sha256
            or identity.ontology_sha256 != graph.ontology_sha256
            or identity.source_profile_sha256 != graph.source_profile_sha256
            or seeds.base_side_receipt_sha256 != graph.base.side_receipt_sha256
            or seeds.candidate_side_receipt_sha256 != graph.candidate.side_receipt_sha256
            or seeds.base_normalized_graph_sha256 != graph.base.normalized_graph_sha256
            or seeds.candidate_normalized_graph_sha256 != graph.candidate.normalized_graph_sha256
        ):
            raise ValueError("Candidate identities are not bound to one embedded graph")
        sides = tuple(item.side for item in self.analyses)
        if sides != tuple(side for side in (TreeSide.BASE, TreeSide.CANDIDATE) if side in sides):
            raise ValueError("Candidate analyses must be unique and ordered by side")
        if len(set(sides)) != len(sides):
            raise ValueError("Candidate analysis side is duplicated")
        outputs = tuple(stage.output_receipt for stage in self.foundation.stages)
        expected_outputs = (
            self.verified_change_manifest_sha256,
            self.graph_production_receipt_sha256,
            self.operation_seed_artifact_sha256,
        )
        if (
            any(receipt is None for receipt in outputs)
            or tuple(receipt.sha256 for receipt in outputs if receipt is not None)
            != expected_outputs
        ):
            raise ValueError("Bundle artifacts differ from foundation stage receipts")
        for analysis in self.analyses:
            request = analysis.run.request
            graph_side = {
                TreeSide.BASE: self.graph_production_artifact.base,
                TreeSide.CANDIDATE: self.graph_production_artifact.candidate,
            }[analysis.side]
            seed_inputs = _side_inputs(self.operation_seed_artifact, analysis.side)
            if seed_inputs is None:
                raise ValueError("Side analysis has no operation-seed artifact scope")
            operations, paths, seed_ids, bindings, indirect_ids, indirect_receipts = seed_inputs
            if (
                request.project_id != identity.project_id
                or request.verified_change_manifest_sha256 != self.verified_change_manifest_sha256
                or request.verified_operation_seed_sha256 != self.operation_seed_artifact_sha256
                or analysis.operation_scope != operations
                or analysis.changed_paths != paths
                or analysis.verified_seed_ids != seed_ids
                or analysis.operation_bindings != bindings
                or analysis.indirect_seed_ids != indirect_ids
                or analysis.indirect_seed_sha256s != indirect_receipts
                or analysis.graph_side_receipt_sha256 != graph_side.side_receipt_sha256
                or analysis.producer_raw_graph_sha256 != graph_side.raw_graph_sha256
                or analysis.analysis_normalized_graph_sha256 != graph_side.normalized_graph_sha256
                or analysis.run.source_snapshot != graph_side.source_snapshot_sha256
                or analysis.run.ontology_id != identity.ontology_id
                or analysis.run.ontology_version != identity.ontology_version
                or analysis.run.ontology_sha256 != identity.ontology_sha256
                or analysis.run.source_profile_id != identity.source_profile_id
                or analysis.run.source_profile_version != identity.source_profile_version
                or analysis.run.source_profile_sha256 != identity.source_profile_sha256
                or analysis.run.reasoning_policy_version != identity.reasoning_policy_version
                or analysis.run.reasoning_policy_sha256 != identity.reasoning_policy_sha256
                or analysis.run.reasoning_eval_set_id != identity.reasoning_eval_set_id
                or analysis.run.reasoning_eval_set_sha256 != identity.reasoning_eval_set_sha256
            ):
                raise ValueError("Side analysis differs from bundle artifact bindings")
        body = self.model_dump(mode="json")
        declared = body.pop("bundle_sha256")
        if declared != stable_sha256(body):
            raise ValueError("Candidate assurance bundle digest is invalid")
        return self


class CandidateSideAssuranceView(_Model):
    side: TreeSide
    operation_scope: tuple[Literal["ADD", "MODIFY", "DELETE"], ...] = Field(
        min_length=1, max_length=2
    )
    changed_path_count: int = Field(ge=1, le=500)
    verified_seed_count: int = Field(ge=1, le=500)
    run_id: UUID
    trace_id: UUID
    status: RunStatus
    decision_code: DecisionCode | None
    source_snapshot: Sha256
    source_graph_sha256: Sha256
    normalized_graph_sha256: Sha256
    graph_side_receipt_sha256: Sha256
    specialist_capture_count: int = Field(default=0, ge=0, le=8)
    specialist_artifact_sha256s: tuple[Sha256, ...] = Field(default=(), max_length=8)
    reasoning_workflow_result_sha256s: tuple[Sha256, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_side_view(self) -> CandidateSideAssuranceView:
        allowed = {
            TreeSide.BASE: ("DELETE", "MODIFY"),
            TreeSide.CANDIDATE: ("ADD", "MODIFY"),
        }[self.side]
        if self.operation_scope != tuple(
            operation for operation in allowed if operation in self.operation_scope
        ):
            raise ValueError("Candidate view operation scope is invalid for its side")
        return self


class CandidateAssuranceView(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    evidence_completeness: Literal["INCOMPLETE"] = "INCOMPLETE"
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,199}$")
    verified_change_manifest_sha256: Sha256
    graph_production_receipt_sha256: Sha256
    operation_seed_artifact_sha256: Sha256
    analysis_identity: CandidateAnalysisIdentity
    analyses: tuple[CandidateSideAssuranceView, ...] = Field(min_length=1, max_length=2)
    blocking_gap_codes: tuple[str, ...] = Field(min_length=1, max_length=100)
    checkpoint_commit_scope: Literal["EXCLUDED_FROM_BUNDLE_TRANSACTION"] = (
        "EXCLUDED_FROM_BUNDLE_TRANSACTION"
    )
    persistence_gap_codes: tuple[Literal["CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE"]] = (
        "CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE",
    )
    bundle_sha256: Sha256
    view_sha256: Sha256

    @model_validator(mode="after")
    def validate_view(self) -> CandidateAssuranceView:
        if self.project_id != self.analysis_identity.project_id:
            raise ValueError("Candidate view project differs from analysis identity")
        sides = tuple(item.side for item in self.analyses)
        if sides != tuple(side for side in (TreeSide.BASE, TreeSide.CANDIDATE) if side in sides):
            raise ValueError("Candidate view sides must be unique and ordered")
        if len(set(sides)) != len(sides):
            raise ValueError("Candidate view side is duplicated")
        if len({item.run_id for item in self.analyses}) != len(self.analyses):
            raise ValueError("Candidate view component run is duplicated")
        if self.blocking_gap_codes != tuple(sorted(set(self.blocking_gap_codes))):
            raise ValueError("Candidate view gaps must be sorted and unique")
        if not set(self.persistence_gap_codes).issubset(self.blocking_gap_codes):
            raise ValueError("Candidate view omits a persistence boundary from blocking gaps")
        body = self.model_dump(mode="json")
        declared = body.pop("view_sha256")
        if declared != stable_sha256(body):
            raise ValueError("Candidate assurance view digest is invalid")
        if len(self.model_dump_json().encode("utf-8")) > 32_768:
            raise ValueError("Candidate assurance view exceeds its response bound")
        return self


def _provenance(owners: tuple[GraphElementOwner, ...], adapter_id: str) -> dict:
    parser_ids = tuple(sorted({item.parser_id for item in owners}))
    owner_body = [item.model_dump(mode="json") for item in owners]
    return {
        "source": owners[0].path,
        "extractorId": parser_ids[0] if len(parser_ids) == 1 else adapter_id,
        "owners": owner_body,
        "ownerReceiptSha256": stable_sha256(owner_body),
    }


def source_from_verified_graph_side(
    *,
    repository_root: Path,
    graph: GraphProductionArtifact,
    side: ProducedGraphSide,
    ontology: CanonicalOntology,
    source_profile: SourceGraphProfile,
) -> SalesforceSourceSnapshot:
    """Adapt a replayed producer side without reading a caller-selected source."""

    raw_content = {
        "schemaVersion": "1.0.0",
        "sourceSnapshot": side.source_snapshot_sha256,
        "nodes": [
            {
                **item.attributes,
                "id": item.node_id,
                "kind": item.raw_kind,
                "label": item.label,
                "evidenceState": item.evidence_state,
                "sourceSnapshot": side.source_snapshot_sha256,
                **_provenance(item.owners, graph.adapter_id),
            }
            for item in side.nodes
        ],
        "edges": [
            {
                **item.attributes,
                "id": item.edge_id,
                "from": item.source_id,
                "relation": item.raw_relation,
                "to": item.target_id,
                "evidenceState": item.evidence_state,
                "sourceSnapshot": side.source_snapshot_sha256,
                **_provenance(item.owners, graph.adapter_id),
            }
            for item in side.edges
        ],
    }
    if stable_sha256(raw_content) != side.raw_graph_sha256:
        raise CandidateAssuranceContractError("Producer raw graph cannot be replayed exactly")
    raw = materialize_source_attested_reasoning_graph(
        raw_content,
        source_graph_sha256=side.raw_graph_sha256,
    )
    source = SalesforceSourceSnapshot(
        root=repository_root,
        contract={"schemaVersion": "1.0.0", "projectId": graph.project_id},
        graph=raw,
        project_index={"sourceSnapshot": side.source_snapshot_sha256},
        trusted_graph_sha256=side.raw_graph_sha256,
        ontology=ontology,
        source_profile=source_profile,
        normalization_project_id=graph.project_id,
        trust_valid_until=graph.valid_until,
    )
    if (
        source.normalized_graph.mapping_gaps
        or source.normalized_graph.graph_sha256 != side.normalized_graph_sha256
    ):
        raise CandidateAssuranceContractError("Verified graph side cannot be normalized")
    return source


def _side_inputs(
    seeds: OperationSeedArtifact,
    side: TreeSide,
) -> (
    tuple[
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[CandidateOperationBinding, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]
    | None
):
    operations = {
        TreeSide.BASE: ("DELETE", "MODIFY"),
        TreeSide.CANDIDATE: ("ADD", "MODIFY"),
    }[side]
    applicable = [item for item in seeds.bindings if item.change.operation.value in operations]
    if not applicable:
        return None
    paths = tuple(sorted({item.change.path for item in applicable}))
    bindings: list[CandidateOperationBinding] = []
    for item in applicable:
        file_evidence = next(value for value in item.file_evidence if value.side is side)
        direct = tuple(seed for seed in item.seeds if seed.side is side)
        bindings.append(
            CandidateOperationBinding(
                operation=item.change.operation.value,
                path=item.change.path,
                change_identity_sha256=item.change_identity_sha256,
                operation_seed_binding_sha256=item.binding_sha256,
                file_root_id=file_evidence.source_artifact_id,
                file_evidence_sha256=file_evidence.evidence_sha256,
                semantic_seed_ids=tuple(seed.node_id for seed in direct),
                semantic_seed_sha256s=tuple(sorted(seed.seed_sha256 for seed in direct)),
            )
        )
    operation_bindings = tuple(sorted(bindings, key=lambda item: (item.path, item.operation)))
    indirect = tuple(seed for seed in seeds.indirect_seeds if seed.side is side)
    indirect_seed_ids = tuple(seed.node_id for seed in indirect)
    indirect_seed_sha256s = tuple(sorted(seed.seed_sha256 for seed in indirect))
    verified_seed_ids = tuple(
        sorted(
            {
                value
                for item in operation_bindings
                for value in (item.file_root_id, *item.semantic_seed_ids)
            }
            | set(indirect_seed_ids)
        )
    )
    if len(paths) > 500 or len(verified_seed_ids) > 500:
        raise CandidateAssuranceContractError("Verified candidate exceeds run input capacity")
    operation_scope = tuple(
        value
        for value in operations
        if any(item.change.operation.value == value for item in applicable)
    )
    return (
        operation_scope,
        paths,
        verified_seed_ids,
        operation_bindings,
        indirect_seed_ids,
        indirect_seed_sha256s,
    )


def candidate_side_requests(
    result: CandidateFoundationResult,
) -> tuple[
    tuple[
        TreeSide,
        ProducedGraphSide,
        tuple[str, ...],
        ChangeRequest,
        tuple[CandidateOperationBinding, ...],
        tuple[str, ...],
        tuple[str, ...],
    ],
    ...,
]:
    graph = result.produced_graph
    seeds = result.operation_seeds
    change = result.verified_change
    if graph is None or seeds is None or change is None:
        raise CandidateAssuranceContractError("Candidate foundation artifacts are unavailable")
    values = []
    for side, graph_side in ((TreeSide.BASE, graph.base), (TreeSide.CANDIDATE, graph.candidate)):
        inputs = _side_inputs(seeds, side)
        if inputs is None:
            continue
        operations, paths, seed_ids, bindings, indirect_ids, indirect_receipts = inputs
        request = ChangeRequest(
            requirement=f"Analyze replay-verified {side.value.casefold()} graph change operations",
            changed_paths=list(paths),
            change_intent=ChangeIntent.VERIFIED_CHANGE,
            project_id=canonical_project_id(graph.project_id),
            source_ref=f"verified-{side.value.casefold()}",
            verified_change_manifest_sha256=change.manifest_sha256,
            verified_operation_seed_sha256=seeds.artifact_sha256,
            verified_seed_ids=list(seed_ids),
        )
        values.append(
            (
                side,
                graph_side,
                operations,
                request,
                bindings,
                indirect_ids,
                indirect_receipts,
            )
        )
    if not values:
        raise CandidateAssuranceContractError("Candidate has no operation-bound graph side")
    return tuple(values)


def build_candidate_assurance_bundle(
    result: CandidateFoundationResult,
    analyses: tuple[CandidateSideAssurance, ...],
) -> CandidateAssuranceBundle:
    graph = result.produced_graph
    seeds = result.operation_seeds
    change = result.verified_change
    if graph is None or seeds is None or change is None:
        raise CandidateAssuranceContractError("Candidate foundation artifacts are unavailable")
    if not analyses:
        raise CandidateAssuranceContractError("Candidate analyses are unavailable")
    first = analyses[0].run
    identity = CandidateAnalysisIdentity(
        project_id=first.request.project_id or "",
        ontology_id=first.ontology_id or "",
        ontology_version=first.ontology_version or "",
        ontology_sha256=first.ontology_sha256 or "",
        source_profile_id=first.source_profile_id or "",
        source_profile_version=first.source_profile_version or "",
        source_profile_sha256=first.source_profile_sha256 or "",
        reasoning_policy_version=first.reasoning_policy_version,
        reasoning_policy_sha256=first.reasoning_policy_sha256,
        reasoning_eval_set_id=first.reasoning_eval_set_id,
        reasoning_eval_set_sha256=first.reasoning_eval_set_sha256,
    )
    persistence_gaps = ("CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE",)
    body = {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "evidence_completeness": "INCOMPLETE",
        "foundation": result.evidence.model_dump(mode="json"),
        "graph_production_artifact": graph.model_dump(mode="json"),
        "operation_seed_artifact": seeds.model_dump(mode="json"),
        "verified_change_manifest_sha256": change.manifest_sha256,
        "graph_production_receipt_sha256": graph.receipt_sha256,
        "operation_seed_artifact_sha256": seeds.artifact_sha256,
        "analysis_identity": identity.model_dump(mode="json"),
        "analyses": [item.model_dump(mode="json") for item in analyses],
        "blocking_gap_codes": sorted({*result.evidence.blocking_gap_codes, *persistence_gaps}),
        "checkpoint_commit_scope": "EXCLUDED_FROM_BUNDLE_TRANSACTION",
        "persistence_gap_codes": list(persistence_gaps),
    }
    return CandidateAssuranceBundle.model_validate({**body, "bundle_sha256": stable_sha256(body)})


def build_candidate_assurance_view(
    bundle: CandidateAssuranceBundle,
) -> CandidateAssuranceView:
    """Project the persisted full bundle into a bounded client-safe receipt view."""

    validated = CandidateAssuranceBundle.model_validate(bundle.model_dump(mode="json"))
    body = {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "evidence_completeness": "INCOMPLETE",
        "project_id": validated.analysis_identity.project_id,
        "verified_change_manifest_sha256": validated.verified_change_manifest_sha256,
        "graph_production_receipt_sha256": validated.graph_production_receipt_sha256,
        "operation_seed_artifact_sha256": validated.operation_seed_artifact_sha256,
        "analysis_identity": validated.analysis_identity.model_dump(mode="json"),
        "analyses": [
            {
                "side": item.side.value,
                "operation_scope": list(item.operation_scope),
                "changed_path_count": len(item.changed_paths),
                "verified_seed_count": len(item.verified_seed_ids),
                "run_id": str(item.run.run_id),
                "trace_id": str(item.run.trace_id),
                "status": item.run.status.value,
                "decision_code": (
                    item.run.decision.code.value if item.run.decision is not None else None
                ),
                "source_snapshot": item.run.source_snapshot,
                "source_graph_sha256": item.run.source_graph_sha256,
                "normalized_graph_sha256": item.run.normalized_graph_sha256,
                "graph_side_receipt_sha256": item.graph_side_receipt_sha256,
                "specialist_capture_count": len(item.specialist_captures),
                "specialist_artifact_sha256s": [
                    capture.artifact.artifact_sha256 for capture in item.specialist_captures
                ],
                "reasoning_workflow_result_sha256s": [
                    capture.workflow_result.result_sha256
                    for capture in item.specialist_captures
                ],
            }
            for item in validated.analyses
        ],
        "blocking_gap_codes": list(validated.blocking_gap_codes),
        "checkpoint_commit_scope": validated.checkpoint_commit_scope,
        "persistence_gap_codes": list(validated.persistence_gap_codes),
        "bundle_sha256": validated.bundle_sha256,
    }
    return CandidateAssuranceView.model_validate({**body, "view_sha256": stable_sha256(body)})
