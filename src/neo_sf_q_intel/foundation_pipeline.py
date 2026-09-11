from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neo_sf_q_intel.change_verification import (
    LocalGitChangeProducer,
    VerifiedChangePolicy,
    VerifiedChangeSet,
    load_verified_change_policy,
)
from neo_sf_q_intel.change_verification import (
    _static_artifact as _static_change_artifact,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.graph_production import (
    GraphProducerPolicy,
    GraphProductionArtifact,
    GraphProductionInputs,
    LocalTreeGraphProducer,
    load_graph_producer_policy,
)
from neo_sf_q_intel.graph_production import (
    _static_artifact as _static_graph_artifact,
)
from neo_sf_q_intel.ontology import (
    CanonicalOntology,
    SourceGraphProfile,
    contract_sha256,
    load_canonical_ontology,
    load_source_graph_profile,
)
from neo_sf_q_intel.operation_seed import (
    OperationAwareSeedCompiler,
    OperationSeedArtifact,
    OperationSeedInputs,
    OperationSeedPolicy,
    load_operation_seed_policy,
)
from neo_sf_q_intel.operation_seed import (
    _static_artifact as _static_seed_artifact,
)


class FoundationStage(StrEnum):
    VERIFIED_CHANGE_CAPTURE = "VERIFIED_CHANGE_CAPTURE"
    TREE_GRAPH_PRODUCTION = "TREE_GRAPH_PRODUCTION"
    OPERATION_SEED_MAPPING = "OPERATION_SEED_MAPPING"


class FoundationStageState(StrEnum):
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


class FoundationOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    ABSTAINED = "ABSTAINED"


class FoundationPipelineVerificationError(RuntimeError):
    """Sanitized refusal from current host-owned projection verification."""

    code: Literal["FOUNDATION_PROJECTION_NOT_CURRENT"] = "FOUNDATION_PROJECTION_NOT_CURRENT"

    def __init__(self) -> None:
        super().__init__(self.code)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

DEFAULT_FOUNDATION_PIPELINE_POLICY_SHA256 = (
    "7beb2e9bb6a2fc9cca647bac29046ea2138045ca1dcc10242873e021be1f24c5"
)


class FoundationImplementationPin(_Model):
    implementation_id: str = Field(alias="implementationId", min_length=1, max_length=200)
    implementation_version: str = Field(alias="implementationVersion")
    implementation_locator: str = Field(alias="implementationLocator", min_length=1, max_length=500)
    implementation_sha256: Sha256 = Field(alias="implementationSha256")


class FoundationPipelinePolicy(_Model):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: Literal["1.0.1"] = Field(alias="policyVersion")
    sha256: Sha256
    composer: FoundationImplementationPin
    verified_change_policy_sha256: Sha256 = Field(alias="verifiedChangePolicySha256")
    graph_producer_policy_sha256: Sha256 = Field(alias="graphProducerPolicySha256")
    operation_seed_policy_sha256: Sha256 = Field(alias="operationSeedPolicySha256")
    maximum_projection_bytes: int = Field(alias="maximumProjectionBytes", ge=1024, le=1_000_000)


class FoundationPipelineContractError(RuntimeError):
    """The host-owned foundation composition policy is unavailable or mismatched."""


def _implementation_sha256(content: bytes) -> str:
    normalized = re.sub(
        rb"DEFAULT_FOUNDATION_PIPELINE_POLICY_SHA256\s*=\s*"
        rb'(?:"[a-f0-9]{64}"|\(\s*"[a-f0-9]{64}"\s*\))',
        b'DEFAULT_FOUNDATION_PIPELINE_POLICY_SHA256 = (\n    "' + (b"0" * 64) + b'"\n)',
        content,
        count=1,
    )
    return hashlib.sha256(normalized).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise FoundationPipelineContractError("Duplicate foundation policy key")
        value[key] = item
    return value


def load_foundation_pipeline_policy(
    path: Path,
    *,
    implementation_root: Path,
    expected_sha256: str = DEFAULT_FOUNDATION_PIPELINE_POLICY_SHA256,
) -> FoundationPipelinePolicy:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except FoundationPipelineContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoundationPipelineContractError(
            "Foundation pipeline policy cannot be loaded"
        ) from exc
    if not isinstance(document, dict) or document.get("sha256") != contract_sha256(document):
        raise FoundationPipelineContractError("Foundation pipeline policy self-hash is invalid")
    if document.get("sha256") != expected_sha256:
        raise FoundationPipelineContractError("Foundation pipeline policy is not externally pinned")
    try:
        policy = FoundationPipelinePolicy.model_validate(document)
    except Exception as exc:
        raise FoundationPipelineContractError("Foundation pipeline policy is invalid") from exc
    implementation = (
        implementation_root.resolve() / policy.composer.implementation_locator
    ).resolve()
    try:
        implementation.relative_to(implementation_root.resolve())
    except ValueError as exc:
        raise FoundationPipelineContractError(
            "Foundation pipeline implementation escapes its root"
        ) from exc
    loaded = Path(inspect.getsourcefile(CandidateFoundationPipeline) or "").resolve()
    if implementation != loaded:
        raise FoundationPipelineContractError("Loaded foundation pipeline differs from policy")
    try:
        digest = _implementation_sha256(implementation.read_bytes())
    except OSError as exc:
        raise FoundationPipelineContractError(
            "Foundation pipeline implementation is unavailable"
        ) from exc
    if digest != policy.composer.implementation_sha256:
        raise FoundationPipelineContractError(
            "Foundation pipeline implementation digest is invalid"
        )
    return policy


class ReceiptReference(_Model):
    role: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    sha256: Sha256


class FoundationMeasurement(_Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: int = Field(ge=0)


class FoundationStageEvidence(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    stage: FoundationStage
    sequence: int = Field(ge=1, le=3)
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{0,99}$")
    state: FoundationStageState
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    local_foundation_stage_complete: bool
    input_receipts: tuple[ReceiptReference, ...] = Field(max_length=3)
    output_receipt: ReceiptReference | None = None
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    policy_sha256: Sha256
    evaluated_at: str = Field(pattern=r"^(?:unavailable|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$")
    valid_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    gap_codes: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = Field(
        max_length=100
    )
    measurements: tuple[FoundationMeasurement, ...] = Field(max_length=20)
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def validate_evidence(self) -> FoundationStageEvidence:
        if self.input_receipts != tuple(
            sorted(self.input_receipts, key=lambda item: item.role)
        ) or len({item.role for item in self.input_receipts}) != len(self.input_receipts):
            raise ValueError("Input receipt roles must be sorted and unique")
        names = tuple(item.name for item in self.measurements)
        if names != tuple(sorted(set(names))):
            raise ValueError("Measurements must be sorted and unique")
        if self.gap_codes != tuple(sorted(set(self.gap_codes))):
            raise ValueError("Gap codes must be sorted and unique")
        executed = self.state is FoundationStageState.EXECUTED
        if executed != self.local_foundation_stage_complete or executed != (
            self.output_receipt is not None
        ):
            raise ValueError("Stage state differs from its local receipt")
        if self.state is FoundationStageState.NOT_RUN and (
            "UPSTREAM_STAGE_INCOMPLETE" not in self.gap_codes
        ):
            raise ValueError("A skipped dependent stage must identify its upstream gap")
        expected_capability, expected_inputs, expected_output, required_gaps = _STAGE_CONTRACTS[
            self.stage
        ]
        if self.capability_id != expected_capability:
            raise ValueError("Foundation stage capability differs from its contract")
        actual_inputs = tuple(item.role for item in self.input_receipts)
        if self.state is FoundationStageState.NOT_RUN:
            if actual_inputs or self.output_receipt is not None:
                raise ValueError("A skipped stage cannot claim receipt lineage")
        else:
            if actual_inputs != expected_inputs:
                raise ValueError("Foundation stage inputs differ from its exact contract")
            if self.output_receipt is not None and self.output_receipt.role != expected_output:
                raise ValueError("Foundation stage output differs from its exact contract")
            if not required_gaps.issubset(self.gap_codes):
                raise ValueError("Permanent foundation-stage gaps cannot be omitted")
        body = self.model_dump(mode="json")
        declared = body.pop("evidence_sha256")
        if declared != stable_sha256(body):
            raise ValueError("Stage-evidence digest differs from its canonical content")
        return self


_STAGE_ORDER = (
    FoundationStage.VERIFIED_CHANGE_CAPTURE,
    FoundationStage.TREE_GRAPH_PRODUCTION,
    FoundationStage.OPERATION_SEED_MAPPING,
)

_CHANGE_GAPS = frozenset(
    {
        "CANDIDATE_BUILD_NOT_VERIFIED",
        "CHANGE_SEED_SCOPE_NOT_ATTESTED",
        "CONFLICT_SCOPE_NOT_ATTESTED",
        "DEPLOYMENT_NOT_ATTESTED",
        "GIT_COMMIT_SIGNATURE_NOT_ATTESTED",
        "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
        "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
        "REPOSITORY_ORIGIN_NOT_ATTESTED",
        "RISK_FACTORS_NOT_ATTESTED",
        "TEST_EXECUTION_SCOPE_NOT_ATTESTED",
        "TEST_OBLIGATION_SCOPE_NOT_ATTESTED",
        "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
    }
)
_GRAPH_GAPS = _CHANGE_GAPS | {"SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE"}
_OPERATION_GAPS = _GRAPH_GAPS | {"GRAPH_INPUT_TREE_NOT_ATTESTED"}
_STAGE_CONTRACTS = {
    FoundationStage.VERIFIED_CHANGE_CAPTURE: (
        "source.verified-change-set",
        (),
        "verified-change",
        _CHANGE_GAPS,
    ),
    FoundationStage.TREE_GRAPH_PRODUCTION: (
        "source.tree-graph-production",
        ("verified-change",),
        "graph-production",
        _GRAPH_GAPS,
    ),
    FoundationStage.OPERATION_SEED_MAPPING: (
        "source.change-seed-mapping",
        ("graph-production", "verified-change"),
        "operation-seeds",
        _OPERATION_GAPS,
    ),
}


class CandidateFoundationEvidence(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,199}$")
    stages: tuple[FoundationStageEvidence, FoundationStageEvidence, FoundationStageEvidence]
    outcome: FoundationOutcome
    foundation_execution_complete: bool
    evidence_completeness: Literal["INCOMPLETE"] = "INCOMPLETE"
    non_authoritative_projection: Literal[True] = True
    pipeline_policy_id: str = Field(min_length=1, max_length=200)
    pipeline_policy_version: Literal["1.0.1"]
    pipeline_policy_sha256: Sha256
    pipeline_implementation_sha256: Sha256
    blocking_gap_codes: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = Field(
        max_length=100
    )
    chain_sha256: Sha256

    @model_validator(mode="after")
    def validate_chain(self) -> CandidateFoundationEvidence:
        if tuple(item.stage for item in self.stages) != _STAGE_ORDER or tuple(
            item.sequence for item in self.stages
        ) != (1, 2, 3):
            raise ValueError("Foundation stages must be complete and ordered")
        if self.foundation_execution_complete != all(
            item.local_foundation_stage_complete for item in self.stages
        ):
            raise ValueError("Chain completeness differs from its stages")
        if (self.outcome is FoundationOutcome.EXECUTED) != (self.foundation_execution_complete):
            raise ValueError("Foundation outcome differs from chain completeness")
        gaps = tuple(sorted({code for stage in self.stages for code in stage.gap_codes}))
        if self.blocking_gap_codes != gaps:
            raise ValueError("Chain gaps differ from its stage projections")
        if "RELEASE_EVIDENCE_MODEL_INCOMPLETE" not in self.blocking_gap_codes:
            raise ValueError("Global release-evidence interlock cannot be omitted")
        first, second, third = self.stages
        if first.output_receipt is not None and second.state is not FoundationStageState.NOT_RUN:
            expected = ReceiptReference(role="verified-change", sha256=first.output_receipt.sha256)
            if expected not in second.input_receipts:
                raise ValueError("Graph production is not linked to verified change")
        if (
            first.output_receipt is not None
            and second.output_receipt is not None
            and third.state is not FoundationStageState.NOT_RUN
        ):
            expected = (
                ReceiptReference(role="graph-production", sha256=second.output_receipt.sha256),
                ReceiptReference(role="verified-change", sha256=first.output_receipt.sha256),
            )
            if third.input_receipts != expected:
                raise ValueError("Operation seeds are not linked to exact upstream outputs")
        body = self.model_dump(mode="json")
        declared = body.pop("chain_sha256")
        if declared != stable_sha256(body):
            raise ValueError("Foundation-chain digest differs from its canonical content")
        if len(str(body).encode("utf-8")) > 32_768:
            raise ValueError("Foundation-chain projection exceeds its byte bound")
        return self


@dataclass(frozen=True, slots=True)
class CandidateFoundationResult:
    """Safe projection plus runtime-only authority artifacts."""

    evidence: CandidateFoundationEvidence
    verified_change: VerifiedChangeSet | None
    produced_graph: GraphProductionArtifact | None
    operation_seeds: OperationSeedArtifact | None


@dataclass(frozen=True, slots=True)
class CandidateFoundationPipeline:
    """Host-owned composition of the R0.4a, R0.4c and R0.4d foundations."""

    project_id: str
    repository_root: Path
    salesforce_project_locator: str
    policy: FoundationPipelinePolicy
    change_producer: LocalGitChangeProducer
    graph_producer: LocalTreeGraphProducer
    operation_compiler: OperationAwareSeedCompiler
    ontology: CanonicalOntology
    source_profile: SourceGraphProfile

    @classmethod
    def from_host_configuration(
        cls,
        *,
        project_id: str,
        repository_root: Path,
        salesforce_app_root: Path,
        implementation_root: Path | None = None,
    ) -> CandidateFoundationPipeline:
        """Resolve every producer and policy once in the trusted host composition root."""

        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,199}", project_id):
            raise ValueError("Project identity is not a bounded logical identifier")
        root = repository_root.resolve(strict=True)
        app_root = salesforce_app_root.resolve(strict=True)
        try:
            project_locator = app_root.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("Salesforce application root is outside its Git repository") from exc
        if not project_locator:
            project_locator = "."
        config_root = (implementation_root or Path(__file__).resolve().parents[2]).resolve(
            strict=True
        )
        pipeline_policy = load_foundation_pipeline_policy(
            config_root / "config" / "foundation-pipeline-policy.json",
            implementation_root=config_root,
        )
        ontology = load_canonical_ontology(
            config_root / "config" / "ontology" / "canonical-ontology.json"
        )
        profile = load_source_graph_profile(
            config_root / "config" / "source-profiles" / "salesforce-dx-semantic-graph.json",
            ontology,
        )
        change_policy = load_verified_change_policy(
            config_root / "config" / "verified-change-policy.json",
            implementation_root=config_root,
        )
        graph_policy = load_graph_producer_policy(
            config_root / "config" / "graph-producer-policy.json",
            implementation_root=config_root,
        )
        seed_policy = load_operation_seed_policy(
            config_root / "config" / "operation-seed-policy.json",
            implementation_root=config_root,
        )
        if (
            pipeline_policy.verified_change_policy_sha256 != change_policy.sha256
            or pipeline_policy.graph_producer_policy_sha256 != graph_policy.sha256
            or pipeline_policy.operation_seed_policy_sha256 != seed_policy.sha256
        ):
            raise FoundationPipelineContractError(
                "Foundation pipeline dependency policy differs from its pin"
            )
        change_producer = LocalGitChangeProducer(project_id, root, change_policy)
        return cls(
            project_id=project_id,
            repository_root=root,
            salesforce_project_locator=project_locator,
            policy=pipeline_policy,
            change_producer=change_producer,
            graph_producer=LocalTreeGraphProducer(graph_policy),
            operation_compiler=OperationAwareSeedCompiler(seed_policy),
            ontology=ontology,
            source_profile=profile,
        )

    def capture_current(self) -> CandidateFoundationResult:
        """Run against the configured repository; accepts no caller-selected scope."""

        try:
            self._require_runtime_policy()
            change = self.change_producer.capture(self.repository_root)
            change_stage = _change_stage(change, self.change_producer.policy)
        except Exception:
            change_stage = _exception_stage(
                FoundationStage.VERIFIED_CHANGE_CAPTURE,
                1,
                "source.verified-change-set",
                self.change_producer.policy,
                (),
                "CHANGE_STAGE_EXCEPTION",
            )
            return self._blocked_result(change_stage, None, None)
        if change.artifact is None:
            return self._blocked_result(change_stage, None, None)

        graph_inputs = GraphProductionInputs(
            candidate=change.artifact,
            change_producer=self.change_producer,
            repository_hint=self.repository_root,
            ontology=self.ontology,
            profile=self.source_profile,
            reuse_request_scoped_capture=True,
        )
        try:
            graph = self.graph_producer.capture(graph_inputs)
            graph_stage = _graph_stage(graph, change.artifact, self.graph_producer.policy)
        except Exception:
            graph_stage = _exception_stage(
                FoundationStage.TREE_GRAPH_PRODUCTION,
                2,
                "source.tree-graph-production",
                self.graph_producer.policy,
                (
                    ReceiptReference(
                        role="verified-change",
                        sha256=change.artifact.manifest_sha256,
                    ),
                ),
                "GRAPH_STAGE_EXCEPTION",
            )
            return self._blocked_result(change_stage, graph_stage, None)
        if graph.artifact is None:
            return self._blocked_result(
                change_stage,
                graph_stage,
                None,
            )
        if not _candidate_project_matches(graph.artifact, self.salesforce_project_locator):
            graph_stage = _exception_stage(
                FoundationStage.TREE_GRAPH_PRODUCTION,
                2,
                "source.tree-graph-production",
                self.graph_producer.policy,
                (
                    ReceiptReference(
                        role="verified-change",
                        sha256=change.artifact.manifest_sha256,
                    ),
                ),
                "CONFIGURED_PROJECT_ROOT_MISMATCH",
            )
            return self._blocked_result(change_stage, graph_stage, None)

        operation_inputs = OperationSeedInputs(
            graph_candidate=graph.artifact,
            graph_producer=self.graph_producer,
            graph_inputs=graph_inputs,
            reuse_request_scoped_capture=True,
        )
        seed_inputs = _seed_input_receipts(change.artifact, graph.artifact)
        try:
            compiled_seeds = self.operation_compiler.compile(operation_inputs)
        except Exception:
            seed_stage = _exception_stage(
                FoundationStage.OPERATION_SEED_MAPPING,
                3,
                "source.change-seed-mapping",
                self.operation_compiler.policy,
                seed_inputs,
                "SEED_COMPILE_EXCEPTION",
            )
            return self._blocked_result(change_stage, graph_stage, seed_stage)
        if compiled_seeds.artifact is not None:
            try:
                seeds = self.operation_compiler.verify(compiled_seeds.artifact, operation_inputs)
            except Exception:
                seed_stage = _exception_stage(
                    FoundationStage.OPERATION_SEED_MAPPING,
                    3,
                    "source.change-seed-mapping",
                    self.operation_compiler.policy,
                    seed_inputs,
                    "SEED_VERIFY_EXCEPTION",
                )
                return self._blocked_result(change_stage, graph_stage, seed_stage)
        else:
            seeds = compiled_seeds
        seed_stage = _seed_stage(
            seeds,
            change.artifact,
            graph.artifact,
            self.operation_compiler.policy,
        )
        if seeds.artifact is None:
            return self._blocked_result(change_stage, graph_stage, seed_stage)
        evidence = _chain(self.policy, self.project_id, (change_stage, graph_stage, seed_stage))
        return CandidateFoundationResult(
            evidence=evidence,
            verified_change=change.artifact,
            produced_graph=graph.artifact,
            operation_seeds=seeds.artifact,
        )

    def verify_current(self, candidate: CandidateFoundationResult) -> CandidateFoundationResult:
        """Independently recapture once and compare every retained foundation artifact."""

        try:
            if not isinstance(candidate, CandidateFoundationResult):
                raise TypeError
            evidence = CandidateFoundationEvidence.model_validate(
                candidate.evidence.model_dump(mode="json")
            )
            change = candidate.verified_change
            graph = candidate.produced_graph
            seeds = candidate.operation_seeds
            if change is None or graph is None or seeds is None:
                raise ValueError
            _require_projection_artifact_binding(evidence, change, graph, seeds)
        except Exception:
            raise FoundationPipelineVerificationError from None

        current = self.capture_current()
        if (
            current.verified_change is None
            or current.produced_graph is None
            or current.operation_seeds is None
            or _static_projection(evidence) != _static_projection(current.evidence)
            or _static_change_artifact(change) != _static_change_artifact(current.verified_change)
            or _static_graph_artifact(graph) != _static_graph_artifact(current.produced_graph)
            or _static_seed_artifact(seeds) != _static_seed_artifact(current.operation_seeds)
        ):
            raise FoundationPipelineVerificationError
        return current

    def _require_runtime_policy(self) -> None:
        document = self.policy.model_dump(mode="json", by_alias=True)
        loaded = Path(inspect.getsourcefile(CandidateFoundationPipeline) or "").resolve()
        if (
            contract_sha256(document) != self.policy.sha256
            or self.policy.sha256 != DEFAULT_FOUNDATION_PIPELINE_POLICY_SHA256
            or self.policy.composer.implementation_id != "candidate-foundation-pipeline"
            or self.policy.composer.implementation_version != "1.0.1"
            or _implementation_sha256(loaded.read_bytes())
            != self.policy.composer.implementation_sha256
            or self.policy.verified_change_policy_sha256 != self.change_producer.policy.sha256
            or self.policy.graph_producer_policy_sha256 != self.graph_producer.policy.sha256
            or self.policy.operation_seed_policy_sha256 != self.operation_compiler.policy.sha256
            or self.policy.maximum_projection_bytes != 32_768
        ):
            raise FoundationPipelineContractError("Foundation pipeline runtime policy is invalid")

    def _blocked_result(
        self,
        change_stage: FoundationStageEvidence,
        graph_stage: FoundationStageEvidence | None,
        seed_stage: FoundationStageEvidence | None,
    ) -> CandidateFoundationResult:
        if graph_stage is None:
            graph_stage = _not_run_stage(
                FoundationStage.TREE_GRAPH_PRODUCTION,
                2,
                "source.tree-graph-production",
                self.graph_producer.policy.policy_id,
                self.graph_producer.policy.policy_version,
                self.graph_producer.policy.sha256,
            )
        if seed_stage is None:
            seed_stage = _not_run_stage(
                FoundationStage.OPERATION_SEED_MAPPING,
                3,
                "source.change-seed-mapping",
                self.operation_compiler.policy.policy_id,
                self.operation_compiler.policy.policy_version,
                self.operation_compiler.policy.sha256,
            )
        return CandidateFoundationResult(
            evidence=_chain(
                self.policy,
                self.project_id,
                (change_stage, graph_stage, seed_stage),
            ),
            verified_change=None,
            produced_graph=None,
            operation_seeds=None,
        )


def _require_projection_artifact_binding(
    evidence: CandidateFoundationEvidence,
    change: VerifiedChangeSet,
    graph: GraphProductionArtifact,
    seeds: OperationSeedArtifact,
) -> None:
    first, second, third = evidence.stages
    if (
        first.output_receipt is None
        or first.output_receipt.sha256 != change.manifest_sha256
        or first.evaluated_at != change.observed_at
        or first.valid_until != change.valid_until
        or second.output_receipt is None
        or second.output_receipt.sha256 != graph.receipt_sha256
        or second.evaluated_at != graph.observed_at
        or second.valid_until != graph.valid_until
        or third.output_receipt is None
        or third.output_receipt.sha256 != seeds.artifact_sha256
        or third.evaluated_at != seeds.evaluated_at
        or third.valid_until != seeds.valid_until
    ):
        raise ValueError("Projection is not bound to its retained artifacts")


def _candidate_project_matches(graph: GraphProductionArtifact, expected_locator: str) -> bool:
    project_roots = tuple(
        PurePosixPath(item.path).parent.as_posix()
        for item in graph.candidate.dispositions
        if PurePosixPath(item.path).name == "sfdx-project.json"
    )
    return project_roots == (expected_locator,)


def _static_projection(evidence: CandidateFoundationEvidence) -> dict:
    """Project only fields whose meaning does not rotate with producer clocks."""

    document = evidence.model_dump(mode="json")
    document.pop("chain_sha256")
    for stage in document["stages"]:
        stage.pop("evaluated_at")
        stage.pop("valid_until")
        stage.pop("evidence_sha256")
        stage["input_receipts"] = [{"role": item["role"]} for item in stage["input_receipts"]]
        if stage["output_receipt"] is not None:
            stage["output_receipt"] = {"role": stage["output_receipt"]["role"]}
    return document


def _codes(evaluation: object) -> tuple[str, ...]:
    return tuple(sorted({item.code.value for item in getattr(evaluation, "gaps", ())}))


def _measurements(values: dict[str, int]) -> tuple[FoundationMeasurement, ...]:
    return tuple(
        FoundationMeasurement(name=name, value=value) for name, value in sorted(values.items())
    )


def _stage(
    *,
    stage: FoundationStage,
    sequence: int,
    capability_id: str,
    complete: bool,
    inputs: tuple[ReceiptReference, ...],
    output: ReceiptReference | None,
    policy_id: str,
    policy_version: str,
    policy_sha256: str,
    evaluated_at: str,
    valid_until: str | None,
    gap_codes: tuple[str, ...],
    measurements: tuple[FoundationMeasurement, ...],
) -> FoundationStageEvidence:
    body = {
        "schema_version": "1.0.0",
        "stage": stage.value,
        "sequence": sequence,
        "capability_id": capability_id,
        "state": "EXECUTED" if complete else "FAILED",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "local_foundation_stage_complete": complete,
        "input_receipts": [item.model_dump(mode="json") for item in inputs],
        "output_receipt": output.model_dump(mode="json") if output else None,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "policy_sha256": policy_sha256,
        "evaluated_at": evaluated_at,
        "valid_until": valid_until,
        "gap_codes": list(gap_codes),
        "measurements": [item.model_dump(mode="json") for item in measurements],
    }
    return FoundationStageEvidence.model_validate({**body, "evidence_sha256": stable_sha256(body)})


def _exception_stage(
    stage: FoundationStage,
    sequence: int,
    capability_id: str,
    policy: VerifiedChangePolicy | GraphProducerPolicy | OperationSeedPolicy,
    inputs: tuple[ReceiptReference, ...],
    code: str,
) -> FoundationStageEvidence:
    required_gaps = _STAGE_CONTRACTS[stage][3]
    return _stage(
        stage=stage,
        sequence=sequence,
        capability_id=capability_id,
        complete=False,
        inputs=tuple(sorted(inputs, key=lambda item: item.role)),
        output=None,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        evaluated_at="unavailable",
        valid_until=None,
        gap_codes=tuple(sorted(required_gaps | {code})),
        measurements=(),
    )


def _seed_input_receipts(
    change: VerifiedChangeSet, graph: GraphProductionArtifact
) -> tuple[ReceiptReference, ...]:
    return tuple(
        sorted(
            (
                ReceiptReference(role="graph-production", sha256=graph.receipt_sha256),
                ReceiptReference(role="verified-change", sha256=change.manifest_sha256),
            ),
            key=lambda item: item.role,
        )
    )


def _change_stage(evaluation: object, policy: VerifiedChangePolicy) -> FoundationStageEvidence:
    artifact = getattr(evaluation, "artifact", None)
    values: dict[str, int] = {}
    if artifact is not None:
        values = {
            "add_count": sum(item.operation.value == "ADD" for item in artifact.changes),
            "base_file_count": len(artifact.base_files),
            "candidate_file_count": len(artifact.candidate_files),
            "change_count": len(artifact.changes),
            "delete_count": sum(item.operation.value == "DELETE" for item in artifact.changes),
            "modify_count": sum(item.operation.value == "MODIFY" for item in artifact.changes),
        }
    return _stage(
        stage=FoundationStage.VERIFIED_CHANGE_CAPTURE,
        sequence=1,
        capability_id="source.verified-change-set",
        complete=artifact is not None,
        inputs=(),
        output=(
            ReceiptReference(role="verified-change", sha256=artifact.manifest_sha256)
            if artifact is not None
            else None
        ),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        evaluated_at=evaluation.evaluated_at,
        valid_until=artifact.valid_until if artifact is not None else None,
        gap_codes=_codes(evaluation),
        measurements=_measurements(values),
    )


def _graph_stage(
    evaluation: object,
    change: VerifiedChangeSet,
    policy: GraphProducerPolicy,
) -> FoundationStageEvidence:
    artifact = getattr(evaluation, "artifact", None)
    values: dict[str, int] = {}
    if artifact is not None:
        values = {
            "base_edge_count": artifact.base.raw_edge_count,
            "base_node_count": artifact.base.raw_node_count,
            "candidate_edge_count": artifact.candidate.raw_edge_count,
            "candidate_node_count": artifact.candidate.raw_node_count,
            "graph_delta_count": len(artifact.delta),
            "tombstone_count": len(artifact.tombstones),
        }
    return _stage(
        stage=FoundationStage.TREE_GRAPH_PRODUCTION,
        sequence=2,
        capability_id="source.tree-graph-production",
        complete=artifact is not None,
        inputs=(ReceiptReference(role="verified-change", sha256=change.manifest_sha256),),
        output=(
            ReceiptReference(role="graph-production", sha256=artifact.receipt_sha256)
            if artifact is not None
            else None
        ),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        evaluated_at=evaluation.evaluated_at,
        valid_until=artifact.valid_until if artifact is not None else None,
        gap_codes=_codes(evaluation),
        measurements=_measurements(values),
    )


def _seed_stage(
    evaluation: object,
    change: VerifiedChangeSet,
    graph: GraphProductionArtifact,
    policy: OperationSeedPolicy,
) -> FoundationStageEvidence:
    artifact = getattr(evaluation, "artifact", None)
    values: dict[str, int] = {}
    if artifact is not None:
        values = {
            "binding_count": len(artifact.bindings),
            "direct_graph_delta_count": len(artifact.direct_graph_delta_sha256s),
            "indirect_graph_delta_count": len(artifact.indirect_graph_delta_sha256s),
            "seed_count": sum(len(item.seeds) for item in artifact.bindings)
            + len(artifact.indirect_seeds),
            "tombstone_count": len(artifact.tombstone_sha256s),
        }
    inputs = _seed_input_receipts(change, graph)
    return _stage(
        stage=FoundationStage.OPERATION_SEED_MAPPING,
        sequence=3,
        capability_id="source.change-seed-mapping",
        complete=artifact is not None,
        inputs=inputs,
        output=(
            ReceiptReference(role="operation-seeds", sha256=artifact.artifact_sha256)
            if artifact is not None
            else None
        ),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        evaluated_at=evaluation.evaluated_at,
        valid_until=artifact.valid_until if artifact is not None else None,
        gap_codes=_codes(evaluation),
        measurements=_measurements(values),
    )


def _not_run_stage(
    stage: FoundationStage,
    sequence: int,
    capability_id: str,
    policy_id: str,
    policy_version: str,
    policy_sha256: str,
) -> FoundationStageEvidence:
    body = {
        "schema_version": "1.0.0",
        "stage": stage.value,
        "sequence": sequence,
        "capability_id": capability_id,
        "state": "NOT_RUN",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "local_foundation_stage_complete": False,
        "input_receipts": [],
        "output_receipt": None,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "policy_sha256": policy_sha256,
        "evaluated_at": "unavailable",
        "valid_until": None,
        "gap_codes": ["UPSTREAM_STAGE_INCOMPLETE"],
        "measurements": [],
    }
    return FoundationStageEvidence.model_validate({**body, "evidence_sha256": stable_sha256(body)})


def _chain(
    policy: FoundationPipelinePolicy,
    project_id: str,
    stages: tuple[FoundationStageEvidence, FoundationStageEvidence, FoundationStageEvidence],
) -> CandidateFoundationEvidence:
    body = {
        "schema_version": "1.0.0",
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "project_id": project_id,
        "stages": [item.model_dump(mode="json") for item in stages],
        "outcome": (
            "EXECUTED"
            if all(item.local_foundation_stage_complete for item in stages)
            else "ABSTAINED"
        ),
        "foundation_execution_complete": all(
            item.local_foundation_stage_complete for item in stages
        ),
        "evidence_completeness": "INCOMPLETE",
        "non_authoritative_projection": True,
        "pipeline_policy_id": policy.policy_id,
        "pipeline_policy_version": policy.policy_version,
        "pipeline_policy_sha256": policy.sha256,
        "pipeline_implementation_sha256": policy.composer.implementation_sha256,
        "blocking_gap_codes": sorted({code for stage in stages for code in stage.gap_codes}),
    }
    return CandidateFoundationEvidence.model_validate({**body, "chain_sha256": stable_sha256(body)})
