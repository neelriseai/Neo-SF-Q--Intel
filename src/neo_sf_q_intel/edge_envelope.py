"""Deterministic, source-neutral provenance envelopes for canonical graph edges.

The existing normalized graph remains an analysis substrate.  A source claim such as
``evidenceState=CONFIRMED`` is not release evidence unless this module independently compiles and
replays a complete envelope against pinned policy, extractor registry, artifact bytes and graph
roots.  This module does not grant release authority; the global governance interlock remains the
only current release posture.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.edge_extractors import EdgeExtractionError, extract_canonical_edge_claim
from neo_sf_q_intel.ontology import (
    CanonicalOntology,
    NormalizedEdge,
    NormalizedGraph,
    OntologyNormalizationError,
    SourceEvidenceState,
    SourceGraphProfile,
    contract_sha256,
    normalize_source_graph,
)
from neo_sf_q_intel.safety import UnsafeLocatorError, require_safe_repository_locator


class TrustedEdgeContractError(RuntimeError):
    """Raised when a trust root is missing, malformed or differs from its external pin."""


class TrustedEdgeCompilationError(RuntimeError):
    """Raised with bounded reason codes when an edge cannot receive a trusted envelope."""

    def __init__(self, codes: tuple[str, ...]) -> None:
        self.codes = codes
        super().__init__("Trusted edge compilation rejected: " + ", ".join(codes))


class EdgeEnvelopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class EdgeDirection(StrEnum):
    SOURCE_TO_TARGET = "SOURCE_TO_TARGET"


class ExtractorTrustEntry(EdgeEnvelopeModel):
    extractor_id: str = Field(alias="extractorId", min_length=1, max_length=256)
    extractor_version: str = Field(alias="extractorVersion", min_length=1, max_length=64)
    implementation_sha256: str = Field(
        alias="implementationSha256", pattern=r"^[a-f0-9]{64}$"
    )
    implementation_locator: str = Field(
        alias="implementationLocator", min_length=1, max_length=2048
    )
    enabled: bool

    @model_validator(mode="after")
    def validate_identity(self) -> ExtractorTrustEntry:
        _require_identifier(self.extractor_id, "extractorId")
        _require_semver(self.extractor_version, "extractorVersion")
        require_safe_repository_locator(self.implementation_locator)
        return self


class ExtractorTrustRegistry(EdgeEnvelopeModel):
    schema_version: str = Field(alias="schemaVersion")
    registry_id: str = Field(alias="registryId", min_length=1, max_length=256)
    registry_version: str = Field(alias="registryVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractors: tuple[ExtractorTrustEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_registry(self) -> ExtractorTrustRegistry:
        _require_semver(self.schema_version, "schemaVersion")
        _require_identifier(self.registry_id, "registryId")
        _require_semver(self.registry_version, "registryVersion")
        identities = [(item.extractor_id, item.extractor_version) for item in self.extractors]
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError("Extractor identities must be sorted and unique")
        return self

    @property
    def entries_by_identity(self) -> dict[tuple[str, str], ExtractorTrustEntry]:
        return {
            (item.extractor_id, item.extractor_version): item for item in self.extractors
        }


class ExtractorRegistryPin(EdgeEnvelopeModel):
    registry_id: str = Field(alias="registryId", min_length=1)
    registry_version: str = Field(alias="registryVersion")
    registry_sha256: str = Field(alias="registrySha256", pattern=r"^[a-f0-9]{64}$")


class TrustedEdgePolicy(EdgeEnvelopeModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=256)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    posture: Literal["PROVENANCE_ONLY_ANALYSIS"]
    extractor_registry: ExtractorRegistryPin = Field(alias="extractorRegistry")
    accepted_evidence_states: tuple[Literal[SourceEvidenceState.CONFIRMED], ...] = Field(
        alias="acceptedEvidenceStates", min_length=1, max_length=1
    )
    maximum_future_skew_seconds: int = Field(
        alias="maximumFutureSkewSeconds", ge=0, le=86_400
    )
    maximum_freshness_seconds: int = Field(
        alias="maximumFreshnessSeconds", ge=1, le=31_536_000
    )

    @model_validator(mode="after")
    def validate_policy(self) -> TrustedEdgePolicy:
        _require_semver(self.schema_version, "schemaVersion")
        _require_identifier(self.policy_id, "policyId")
        _require_semver(self.policy_version, "policyVersion")
        _require_semver(self.extractor_registry.registry_version, "registryVersion")
        _require_identifier(self.extractor_registry.registry_id, "registryId")
        if self.accepted_evidence_states != (SourceEvidenceState.CONFIRMED,):
            raise ValueError("Only deterministic CONFIRMED evidence may receive an envelope")
        return self


class DirectedEndpointSignature(EdgeEnvelopeModel):
    direction: Literal[EdgeDirection.SOURCE_TO_TARGET] = EdgeDirection.SOURCE_TO_TARGET
    source_id: str = Field(min_length=1, max_length=1024)
    source_class: str = Field(min_length=1, max_length=256)
    target_id: str = Field(min_length=1, max_length=1024)
    target_class: str = Field(min_length=1, max_length=256)


class TrustedEdgeEnvelope(EdgeEnvelopeModel):
    schema_version: str = "1.0.0"
    authority_scope: Literal["EDGE_PROVENANCE_ONLY"] = "EDGE_PROVENANCE_ONLY"
    edge_id: str = Field(min_length=1, max_length=1024)
    canonical_relation: str = Field(min_length=1, max_length=256)
    endpoint_signature: DirectedEndpointSignature
    project_id: str = Field(min_length=1, max_length=1024)
    source_snapshot: str = Field(min_length=1, max_length=1024)
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str = Field(min_length=1, max_length=256)
    ontology_version: str = Field(min_length=1, max_length=64)
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1, max_length=256)
    profile_version: str = Field(min_length=1, max_length=64)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_locator: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor_id: str = Field(min_length=1, max_length=256)
    extractor_version: str = Field(min_length=1, max_length=64)
    extractor_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor_registry_id: str = Field(min_length=1, max_length=256)
    extractor_registry_version: str = Field(min_length=1, max_length=64)
    extractor_registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trust_policy_id: str = Field(min_length=1, max_length=256)
    trust_policy_version: str = Field(min_length=1, max_length=64)
    trust_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_state: Literal[SourceEvidenceState.CONFIRMED]
    observed_at: str = Field(min_length=20, max_length=20)
    valid_until: str = Field(min_length=20, max_length=20)
    freshness_seconds: int = Field(ge=1, le=31_536_000)
    envelope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_canonical_envelope(self) -> TrustedEdgeEnvelope:
        _require_semver(self.schema_version, "schema_version")
        _require_semver(self.ontology_version, "ontology_version")
        _require_semver(self.profile_version, "profile_version")
        _require_semver(self.extractor_version, "extractor_version")
        _require_semver(self.extractor_registry_version, "extractor_registry_version")
        _require_semver(self.trust_policy_version, "trust_policy_version")
        try:
            require_safe_repository_locator(self.source_artifact_locator)
        except UnsafeLocatorError as exc:
            raise ValueError("source_artifact_locator is unsafe") from exc
        observed = _parse_timestamp(self.observed_at)
        valid_until = _parse_timestamp(self.valid_until)
        if valid_until <= observed:
            raise ValueError("valid_until must follow observed_at")
        if int((valid_until - observed).total_seconds()) != self.freshness_seconds:
            raise ValueError("freshness_seconds must equal the exact validity interval")
        body = self.model_dump(mode="json")
        declared = body.pop("envelope_sha256")
        if stable_sha256(body) != declared:
            raise ValueError("Envelope digest does not match its canonical content")
        return self


class ArtifactContent(EdgeEnvelopeModel):
    locator: str = Field(min_length=1, max_length=2048)
    content: bytes = Field(max_length=16_777_216)

    @model_validator(mode="after")
    def validate_locator(self) -> ArtifactContent:
        require_safe_repository_locator(self.locator)
        return self


class EdgeEnvelopeRejectionCode(StrEnum):
    ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
    DUPLICATE_ARTIFACT_LOCATOR = "DUPLICATE_ARTIFACT_LOCATOR"
    DUPLICATE_EDGE_ENVELOPE = "DUPLICATE_EDGE_ENVELOPE"
    EDGE_ENVELOPE_DIGEST_MISMATCH = "EDGE_ENVELOPE_DIGEST_MISMATCH"
    EDGE_EVIDENCE_STATE_REJECTED = "EDGE_EVIDENCE_STATE_REJECTED"
    EDGE_ID_MISMATCH = "EDGE_ID_MISMATCH"
    EVIDENCE_EXPIRED = "EVIDENCE_EXPIRED"
    EVIDENCE_FROM_FUTURE = "EVIDENCE_FROM_FUTURE"
    EXTRACTOR_DISABLED = "EXTRACTOR_DISABLED"
    EXTRACTOR_IDENTITY_MISMATCH = "EXTRACTOR_IDENTITY_MISMATCH"
    EXTRACTOR_IMPLEMENTATION_MISMATCH = "EXTRACTOR_IMPLEMENTATION_MISMATCH"
    EXTRACTOR_REGISTRY_MISMATCH = "EXTRACTOR_REGISTRY_MISMATCH"
    FRESHNESS_EXCEEDS_POLICY = "FRESHNESS_EXCEEDS_POLICY"
    GRAPH_ROOT_MISMATCH = "GRAPH_ROOT_MISMATCH"
    ILLEGAL_ENDPOINT_SIGNATURE = "ILLEGAL_ENDPOINT_SIGNATURE"
    MISSING_ARTIFACT_DIGEST = "MISSING_ARTIFACT_DIGEST"
    MISSING_EDGE_ENVELOPE = "MISSING_EDGE_ENVELOPE"
    NORMALIZED_GRAPH_INTEGRITY_FAILURE = "NORMALIZED_GRAPH_INTEGRITY_FAILURE"
    ONTOLOGY_ROOT_MISMATCH = "ONTOLOGY_ROOT_MISMATCH"
    POLICY_ROOT_MISMATCH = "POLICY_ROOT_MISMATCH"
    PROFILE_ROOT_MISMATCH = "PROFILE_ROOT_MISMATCH"
    PROJECT_MISMATCH = "PROJECT_MISMATCH"
    RELATION_MISMATCH = "RELATION_MISMATCH"
    SNAPSHOT_MISMATCH = "SNAPSHOT_MISMATCH"
    SOURCE_ARTIFACT_LOCATOR_MISMATCH = "SOURCE_ARTIFACT_LOCATOR_MISMATCH"
    UNKNOWN_EDGE = "UNKNOWN_EDGE"
    UNKNOWN_EXTRACTOR = "UNKNOWN_EXTRACTOR"
    UNSAFE_ARTIFACT_LOCATOR = "UNSAFE_ARTIFACT_LOCATOR"
    EXTRACTOR_EXECUTION_MISMATCH = "EXTRACTOR_EXECUTION_MISMATCH"


class EdgeEnvelopeRejection(EdgeEnvelopeModel):
    code: EdgeEnvelopeRejectionCode
    edge_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    blocking: Literal[True] = True


class TrustedEdgeVerification(EdgeEnvelopeModel):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    project_id: str
    source_snapshot: str
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: str
    ontology_version: str
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str
    profile_version: str
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trust_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor_registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    required_edge_ids: tuple[str, ...] = Field(min_length=1)
    coverage_complete: bool
    release_eligible: Literal[False] = False
    blocking_gap_codes: tuple[
        Literal["UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"], ...
    ] = ("UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",)
    accepted_envelopes: tuple[TrustedEdgeEnvelope, ...]
    rejections: tuple[EdgeEnvelopeRejection, ...]
    verification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_verification(self) -> TrustedEdgeVerification:
        if tuple(sorted(self.accepted_envelopes, key=lambda item: item.edge_id)) != (
            self.accepted_envelopes
        ):
            raise ValueError("Accepted envelopes must be sorted by edge ID")
        if self.required_edge_ids != tuple(sorted(set(self.required_edge_ids))):
            raise ValueError("Required edge IDs must be sorted and unique")
        accepted_ids = {item.edge_id for item in self.accepted_envelopes}
        expected_coverage = set(self.required_edge_ids) <= accepted_ids and not self.rejections
        if self.coverage_complete is not expected_coverage:
            raise ValueError("coverage_complete differs from verified scope")
        if self.blocking_gap_codes != ("UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",):
            raise ValueError("R0.1 must retain the upstream capture-attestation gap")
        rejection_keys = [
            (item.edge_identity_sha256, item.code.value) for item in self.rejections
        ]
        if rejection_keys != sorted(set(rejection_keys)):
            raise ValueError("Rejections must be sorted and deduplicated")
        body = self.model_dump(mode="json")
        declared = body.pop("verification_sha256")
        if stable_sha256(body) != declared:
            raise ValueError("Verification digest does not match its canonical content")
        return self

    @property
    def accepted_by_edge_id(self) -> dict[str, TrustedEdgeEnvelope]:
        return {item.edge_id: item for item in self.accepted_envelopes}


@dataclass(frozen=True, slots=True)
class TrustedEdgeReplayInput:
    """Complete current trust inputs required by an authority-sensitive consumer."""

    ontology: CanonicalOntology
    profile: SourceGraphProfile
    registry: ExtractorTrustRegistry
    policy: TrustedEdgePolicy
    envelopes: tuple[TrustedEdgeEnvelope, ...]
    artifact_contents: tuple[ArtifactContent, ...]
    evaluated_at: datetime
    required_edge_ids: tuple[str, ...]
    expected_source_graph_sha256: str
    expected_ontology_sha256: str
    expected_profile_sha256: str
    expected_project_id: str
    expected_source_snapshot: str
    expected_normalized_graph_sha256: str
    implementation_root: Path
    source_graph_content: bytes
    expected_registry_sha256: str
    expected_policy_sha256: str

    def verify(self, graph: NormalizedGraph) -> TrustedEdgeVerification:
        return verify_trusted_edge_envelopes(
            graph,
            self.ontology,
            self.profile,
            self.registry,
            self.policy,
            self.envelopes,
            self.artifact_contents,
            evaluated_at=self.evaluated_at,
            required_edge_ids=self.required_edge_ids,
            expected_source_graph_sha256=self.expected_source_graph_sha256,
            expected_ontology_sha256=self.expected_ontology_sha256,
            expected_profile_sha256=self.expected_profile_sha256,
            expected_project_id=self.expected_project_id,
            expected_source_snapshot=self.expected_source_snapshot,
            expected_normalized_graph_sha256=self.expected_normalized_graph_sha256,
            implementation_root=self.implementation_root,
            source_graph_content=self.source_graph_content,
            expected_registry_sha256=self.expected_registry_sha256,
            expected_policy_sha256=self.expected_policy_sha256,
        )


DEFAULT_EXTRACTOR_REGISTRY_SHA256 = (
    "43da9da63df0f148091aa6356865bb17f167be8721ef0bc87fb9eb619f7529e6"
)
DEFAULT_TRUSTED_EDGE_POLICY_SHA256 = (
    "baf97c1ba7602cc7919e92e251948b659b343c88ef49e53f05cdca3a3e60f328"
)

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _require_semver(value: str, field: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError(f"{field} must use semantic versioning")


def _require_identifier(value: str, field: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase stable identifier")


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must use canonical UTC second precision")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    canonical = value.astimezone(UTC)
    if canonical.microsecond:
        raise ValueError("Timestamp must use whole-second precision")
    return canonical.strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_sha256(body: Any) -> str:
    encoded = json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def artifact_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_graph_sha256(content: bytes) -> str:
    """Match the canonical LF-normalized UTF-8 source-root semantics used by ingestion."""

    try:
        text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise TrustedEdgeContractError("Raw source graph is not valid UTF-8 text") from exc
    return hashlib.sha256(text.encode()).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrustedEdgeContractError("Duplicate JSON key is not allowed")
        result[key] = value
    return result


def _load_self_hashed_contract(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file():
        raise TrustedEdgeContractError(f"Required trust contract is missing: {path.name}")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except TrustedEdgeContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedEdgeContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(document, dict):
        raise TrustedEdgeContractError(f"Expected a JSON object in {path.name}")
    actual = contract_sha256(document)
    if document.get("sha256") != actual:
        raise TrustedEdgeContractError(f"{path.name} digest does not match its content")
    if actual != expected_sha256.casefold():
        raise TrustedEdgeContractError(f"{path.name} does not match its external trust pin")
    return document


def load_extractor_trust_registry(
    path: Path,
    *,
    implementation_root: Path | None = None,
    expected_sha256: str = DEFAULT_EXTRACTOR_REGISTRY_SHA256,
) -> ExtractorTrustRegistry:
    document = _load_self_hashed_contract(path, expected_sha256)
    try:
        registry = ExtractorTrustRegistry.model_validate(document)
    except ValidationError as exc:
        raise TrustedEdgeContractError("Invalid extractor trust registry") from exc
    _require_extractor_implementations(
        registry, implementation_root or path.parent.parent
    )
    return registry


def load_trusted_edge_policy(
    path: Path,
    registry: ExtractorTrustRegistry,
    *,
    expected_sha256: str = DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
) -> TrustedEdgePolicy:
    document = _load_self_hashed_contract(path, expected_sha256)
    try:
        policy = TrustedEdgePolicy.model_validate(document)
    except ValidationError as exc:
        raise TrustedEdgeContractError("Invalid trusted-edge policy") from exc
    pin = policy.extractor_registry
    if (pin.registry_id, pin.registry_version, pin.registry_sha256) != (
        registry.registry_id,
        registry.registry_version,
        registry.sha256,
    ):
        raise TrustedEdgeContractError("Trusted-edge policy does not pin the loaded registry")
    return policy


def _require_runtime_roots(
    graph: NormalizedGraph,
    ontology: CanonicalOntology,
    profile: SourceGraphProfile,
    registry: ExtractorTrustRegistry,
    policy: TrustedEdgePolicy,
    *,
    expected_registry_sha256: str,
    expected_policy_sha256: str,
    expected_source_graph_sha256: str,
    expected_ontology_sha256: str,
    expected_profile_sha256: str,
    expected_project_id: str,
    expected_source_snapshot: str,
    expected_normalized_graph_sha256: str,
    implementation_root: Path,
    source_graph_content: bytes,
) -> None:
    registry_body = registry.model_dump(mode="json", by_alias=True)
    policy_body = policy.model_dump(mode="json", by_alias=True)
    if (
        contract_sha256(registry_body) != registry.sha256
        or registry.sha256 != expected_registry_sha256.casefold()
    ):
        raise TrustedEdgeContractError("Extractor registry failed replay against its trust pin")
    if (
        contract_sha256(policy_body) != policy.sha256
        or policy.sha256 != expected_policy_sha256.casefold()
    ):
        raise TrustedEdgeContractError("Trusted-edge policy failed replay against its trust pin")
    pin = policy.extractor_registry
    if (pin.registry_id, pin.registry_version, pin.registry_sha256) != (
        registry.registry_id,
        registry.registry_version,
        registry.sha256,
    ):
        raise TrustedEdgeContractError("Trusted-edge policy registry pin differs at replay")
    ontology_body = ontology.model_dump(mode="json", by_alias=True)
    if contract_sha256(ontology_body) != ontology.sha256:
        raise TrustedEdgeContractError("Ontology failed canonical replay")
    profile_body = profile.model_dump(mode="json", by_alias=True)
    if contract_sha256(profile_body) != profile.sha256:
        raise TrustedEdgeContractError("Source profile failed canonical replay")
    if ontology.sha256 != expected_ontology_sha256.casefold():
        raise TrustedEdgeContractError("Ontology differs from its external trust pin")
    if profile.sha256 != expected_profile_sha256.casefold():
        raise TrustedEdgeContractError("Source profile differs from its external trust pin")
    if graph.source_graph_sha256 != expected_source_graph_sha256.casefold():
        raise TrustedEdgeContractError("Source graph differs from its external trust pin")
    if graph.project_id != expected_project_id:
        raise TrustedEdgeContractError("Project differs from its external trust pin")
    if graph.source_snapshot != expected_source_snapshot:
        raise TrustedEdgeContractError("Snapshot differs from its external trust pin")
    if graph.graph_sha256 != expected_normalized_graph_sha256.casefold():
        raise TrustedEdgeContractError("Normalized graph differs from its external trust pin")
    if (graph.ontology_id, graph.ontology_version, graph.ontology_sha256) != (
        ontology.ontology_id,
        ontology.ontology_version,
        ontology.sha256,
    ):
        raise TrustedEdgeContractError("Graph and ontology roots differ")
    if (graph.profile_id, graph.profile_version, graph.profile_sha256) != (
        profile.profile_id,
        profile.profile_version,
        profile.sha256,
    ):
        raise TrustedEdgeContractError("Graph and source-profile roots differ")
    profile_pin = profile.ontology
    if (
        profile_pin.ontology_id,
        profile_pin.ontology_version,
        profile_pin.ontology_sha256,
    ) != (ontology.ontology_id, ontology.ontology_version, ontology.sha256):
        raise TrustedEdgeContractError("Source profile and ontology roots differ")
    _require_extractor_implementations(registry, implementation_root)
    _require_source_graph_replay(
        graph,
        ontology,
        profile,
        source_graph_content,
        expected_project_id=expected_project_id,
        expected_source_graph_sha256=expected_source_graph_sha256,
    )


def _require_extractor_implementations(
    registry: ExtractorTrustRegistry, implementation_root: Path
) -> None:
    root = implementation_root.resolve()
    for entry in registry.extractors:
        if not entry.enabled:
            continue
        candidate = (root / entry.implementation_locator).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise TrustedEdgeContractError(
                "Extractor implementation escapes the configured repository root"
            ) from exc
        try:
            content = candidate.read_bytes()
        except OSError as exc:
            raise TrustedEdgeContractError("Extractor implementation is unavailable") from exc
        if artifact_sha256(content) != entry.implementation_sha256:
            raise TrustedEdgeContractError("Extractor implementation digest does not match")
        identity = (
            entry.extractor_id,
            entry.extractor_version,
            entry.implementation_locator,
        )
        if identity not in _EXTRACTOR_DISPATCH:
            raise TrustedEdgeContractError("Extractor implementation has no trusted dispatcher")
        loaded_source = inspect.getsourcefile(_EXTRACTOR_DISPATCH[identity])
        if loaded_source is None or Path(loaded_source).resolve() != candidate:
            raise TrustedEdgeContractError(
                "Loaded extractor implementation differs from the registry locator"
            )


_EXTRACTOR_DISPATCH: dict[tuple[str, str, str], Callable[[bytes], dict[str, str]]] = {
    (
        "canonical-edge-artifact-parser",
        "1.0.0",
        "src/neo_sf_q_intel/edge_extractors.py",
    ): extract_canonical_edge_claim,
}


def _require_source_graph_replay(
    graph: NormalizedGraph,
    ontology: CanonicalOntology,
    profile: SourceGraphProfile,
    source_graph_content: bytes,
    *,
    expected_project_id: str,
    expected_source_graph_sha256: str,
) -> None:
    if source_graph_sha256(source_graph_content) != expected_source_graph_sha256.casefold():
        raise TrustedEdgeContractError("Raw source graph bytes differ from their external pin")
    try:
        raw_graph = json.loads(
            source_graph_content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except TrustedEdgeContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedEdgeContractError("Raw source graph is not valid UTF-8 JSON") from exc
    if not isinstance(raw_graph, dict):
        raise TrustedEdgeContractError("Raw source graph must be a JSON object")
    prepared_graph = json.loads(json.dumps(raw_graph))
    for collection_name in ("nodes", "edges"):
        collection = prepared_graph.get(collection_name)
        if not isinstance(collection, list):
            raise TrustedEdgeContractError("Raw source graph collections are invalid")
        for item in collection:
            if not isinstance(item, dict):
                raise TrustedEdgeContractError("Raw source graph entities are invalid")
            existing = item.get("sourceHash")
            if existing is not None and existing != expected_source_graph_sha256:
                raise TrustedEdgeContractError("Source entity graph root differs from its pin")
            item["sourceHash"] = expected_source_graph_sha256
    try:
        replayed = normalize_source_graph(
            prepared_graph,
            ontology,
            profile,
            project_id=expected_project_id,
            source_graph_sha256=expected_source_graph_sha256,
        )
    except OntologyNormalizationError as exc:
        raise TrustedEdgeContractError("Raw source graph failed structural replay") from exc
    if replayed != graph:
        raise TrustedEdgeContractError("Normalized graph differs from pinned source replay")


def _execute_extractor(entry: ExtractorTrustEntry, content: bytes) -> dict[str, str]:
    extractor = _EXTRACTOR_DISPATCH.get(
        (entry.extractor_id, entry.extractor_version, entry.implementation_locator)
    )
    if extractor is None:
        raise TrustedEdgeContractError("Extractor implementation has no trusted dispatcher")
    return extractor(content)


def _graph_integrity_valid(graph: NormalizedGraph) -> bool:
    body = graph.model_dump(mode="json")
    declared = body.pop("graph_sha256")
    node_ids = [item.node_id for item in graph.nodes]
    edge_ids = [item.edge_id for item in graph.edges]
    known_nodes = set(node_ids)
    return bool(
        stable_sha256(body) == declared
        and len(node_ids) == len(known_nodes)
        and len(edge_ids) == len(set(edge_ids))
        and all(
            item.source_id in known_nodes and item.target_id in known_nodes
            for item in graph.edges
        )
    )


def _edge_identity_sha256(edge_id: str) -> str:
    return hashlib.sha256(edge_id.encode()).hexdigest()


def _signature_for(
    edge: NormalizedEdge, graph: NormalizedGraph
) -> DirectedEndpointSignature | None:
    nodes = {item.node_id: item for item in graph.nodes}
    source = nodes.get(edge.source_id)
    target = nodes.get(edge.target_id)
    if source is None or target is None:
        return None
    return DirectedEndpointSignature(
        source_id=edge.source_id,
        source_class=source.canonical_class,
        target_id=edge.target_id,
        target_class=target.canonical_class,
    )


def _signature_is_legal(
    relation_id: str,
    signature: DirectedEndpointSignature,
    ontology: CanonicalOntology,
    *,
    edge: NormalizedEdge | None = None,
    profile: SourceGraphProfile | None = None,
) -> bool:
    relation = ontology.relations_by_id.get(relation_id)
    if relation is None:
        return False
    pair = (signature.source_class, signature.target_class)
    if pair not in {
        (item.source_class, item.target_class) for item in relation.legal_endpoints
    }:
        return False
    if edge is None or profile is None:
        return True
    mapping = profile.relation_definitions.get(edge.raw_relation)
    return bool(
        mapping
        and mapping.canonical_relation == relation_id
        and pair
        in {(item.source_class, item.target_class) for item in mapping.legal_endpoints}
    )


def compile_trusted_edge_envelope(
    edge: NormalizedEdge,
    graph: NormalizedGraph,
    ontology: CanonicalOntology,
    profile: SourceGraphProfile,
    registry: ExtractorTrustRegistry,
    policy: TrustedEdgePolicy,
    *,
    source_artifact_locator: str,
    source_artifact_content: bytes,
    observed_at: datetime,
    valid_until: datetime,
    expected_source_graph_sha256: str,
    expected_ontology_sha256: str,
    expected_profile_sha256: str,
    expected_project_id: str,
    expected_source_snapshot: str,
    expected_normalized_graph_sha256: str,
    implementation_root: Path,
    source_graph_content: bytes,
    expected_registry_sha256: str = DEFAULT_EXTRACTOR_REGISTRY_SHA256,
    expected_policy_sha256: str = DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
) -> TrustedEdgeEnvelope:
    """Compile only a currently replay-valid deterministic edge envelope."""

    _require_runtime_roots(
        graph,
        ontology,
        profile,
        registry,
        policy,
        expected_registry_sha256=expected_registry_sha256,
        expected_policy_sha256=expected_policy_sha256,
        expected_source_graph_sha256=expected_source_graph_sha256,
        expected_ontology_sha256=expected_ontology_sha256,
        expected_profile_sha256=expected_profile_sha256,
        expected_project_id=expected_project_id,
        expected_source_snapshot=expected_source_snapshot,
        expected_normalized_graph_sha256=expected_normalized_graph_sha256,
        implementation_root=implementation_root,
        source_graph_content=source_graph_content,
    )
    codes: set[str] = set()
    if not _graph_integrity_valid(graph):
        codes.add(EdgeEnvelopeRejectionCode.NORMALIZED_GRAPH_INTEGRITY_FAILURE.value)
    if not graph.project_id or not graph.source_snapshot or not graph.source_graph_sha256:
        codes.add(EdgeEnvelopeRejectionCode.GRAPH_ROOT_MISMATCH.value)
    graph_edge = {item.edge_id: item for item in graph.edges}.get(edge.edge_id)
    if graph_edge != edge:
        codes.add(EdgeEnvelopeRejectionCode.EDGE_ID_MISMATCH.value)
    signature = _signature_for(edge, graph)
    if signature is None or not _signature_is_legal(
        edge.canonical_relation, signature, ontology, edge=edge, profile=profile
    ):
        codes.add(EdgeEnvelopeRejectionCode.ILLEGAL_ENDPOINT_SIGNATURE.value)
    if edge.evidence_state not in policy.accepted_evidence_states:
        codes.add(EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED.value)
    if edge.source_snapshot != graph.source_snapshot:
        codes.add(EdgeEnvelopeRejectionCode.SNAPSHOT_MISMATCH.value)
    if edge.source_hash != graph.source_graph_sha256:
        codes.add(EdgeEnvelopeRejectionCode.GRAPH_ROOT_MISMATCH.value)
    try:
        locator = require_safe_repository_locator(source_artifact_locator)
    except UnsafeLocatorError:
        locator = "invalid"
        codes.add(EdgeEnvelopeRejectionCode.UNSAFE_ARTIFACT_LOCATOR.value)
    if edge.source != locator:
        codes.add(EdgeEnvelopeRejectionCode.SOURCE_ARTIFACT_LOCATOR_MISMATCH.value)
    extractor_id = edge.extractor_id or ""
    extractor_version = edge.extractor_version or ""
    extractor_implementation_sha256 = edge.extractor_implementation_sha256 or ""
    entry = registry.entries_by_identity.get((extractor_id, extractor_version))
    if entry is None:
        codes.add(EdgeEnvelopeRejectionCode.UNKNOWN_EXTRACTOR.value)
    else:
        if not entry.enabled:
            codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_DISABLED.value)
        if entry.implementation_sha256 != extractor_implementation_sha256:
            codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_IMPLEMENTATION_MISMATCH.value)
    if edge.extractor_id != extractor_id:
        codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_IDENTITY_MISMATCH.value)
    artifact_digest = artifact_sha256(source_artifact_content)
    if edge.source_artifact_sha256 != artifact_digest:
        codes.add(EdgeEnvelopeRejectionCode.ARTIFACT_DIGEST_MISMATCH.value)
    try:
        if entry is None or not entry.enabled:
            raise EdgeExtractionError("Extractor is not registered")
        extracted = _execute_extractor(entry, source_artifact_content)
    except EdgeExtractionError:
        extracted = {}
        codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_EXECUTION_MISMATCH.value)
    expected_claim = {
        "canonicalRelation": edge.canonical_relation,
        "edgeId": edge.edge_id,
        "evidenceState": edge.evidence_state.value,
        "extractorId": extractor_id,
        "extractorVersion": extractor_version,
        "sourceId": edge.source_id,
        "sourceSnapshot": edge.source_snapshot or "",
        "targetId": edge.target_id,
    }
    if extracted != expected_claim:
        codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_EXECUTION_MISMATCH.value)
    try:
        observed_text = _format_timestamp(observed_at)
        valid_text = _format_timestamp(valid_until)
        freshness = int((valid_until - observed_at).total_seconds())
    except (TypeError, ValueError):
        codes.add(EdgeEnvelopeRejectionCode.FRESHNESS_EXCEEDS_POLICY.value)
        observed_text = "1970-01-01T00:00:00Z"
        valid_text = "1970-01-01T00:00:01Z"
        freshness = 1
    if freshness < 1 or freshness > policy.maximum_freshness_seconds:
        codes.add(EdgeEnvelopeRejectionCode.FRESHNESS_EXCEEDS_POLICY.value)
    if codes:
        raise TrustedEdgeCompilationError(tuple(sorted(codes)))
    assert graph.project_id is not None
    assert graph.source_snapshot is not None
    assert graph.source_graph_sha256 is not None
    assert signature is not None
    body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "authority_scope": "EDGE_PROVENANCE_ONLY",
        "edge_id": edge.edge_id,
        "canonical_relation": edge.canonical_relation,
        "endpoint_signature": signature.model_dump(mode="json"),
        "project_id": graph.project_id,
        "source_snapshot": graph.source_snapshot,
        "source_graph_sha256": graph.source_graph_sha256,
        "normalized_graph_sha256": graph.graph_sha256,
        "ontology_id": graph.ontology_id,
        "ontology_version": graph.ontology_version,
        "ontology_sha256": graph.ontology_sha256,
        "profile_id": graph.profile_id,
        "profile_version": graph.profile_version,
        "profile_sha256": graph.profile_sha256,
        "source_artifact_locator": locator,
        "source_artifact_sha256": artifact_digest,
        "extractor_id": extractor_id,
        "extractor_version": extractor_version,
        "extractor_implementation_sha256": extractor_implementation_sha256,
        "extractor_registry_id": registry.registry_id,
        "extractor_registry_version": registry.registry_version,
        "extractor_registry_sha256": registry.sha256,
        "trust_policy_id": policy.policy_id,
        "trust_policy_version": policy.policy_version,
        "trust_policy_sha256": policy.sha256,
        "evidence_state": SourceEvidenceState.CONFIRMED.value,
        "observed_at": observed_text,
        "valid_until": valid_text,
        "freshness_seconds": freshness,
    }
    return TrustedEdgeEnvelope.model_validate(
        {**body, "envelope_sha256": stable_sha256(body)}
    )


def verify_trusted_edge_envelopes(
    graph: NormalizedGraph,
    ontology: CanonicalOntology,
    profile: SourceGraphProfile,
    registry: ExtractorTrustRegistry,
    policy: TrustedEdgePolicy,
    envelopes: tuple[TrustedEdgeEnvelope, ...],
    artifact_contents: tuple[ArtifactContent, ...],
    *,
    evaluated_at: datetime,
    required_edge_ids: tuple[str, ...],
    expected_source_graph_sha256: str,
    expected_ontology_sha256: str,
    expected_profile_sha256: str,
    expected_project_id: str,
    expected_source_snapshot: str,
    expected_normalized_graph_sha256: str,
    implementation_root: Path,
    source_graph_content: bytes,
    expected_registry_sha256: str = DEFAULT_EXTRACTOR_REGISTRY_SHA256,
    expected_policy_sha256: str = DEFAULT_TRUSTED_EDGE_POLICY_SHA256,
) -> TrustedEdgeVerification:
    """Replay all roots and return a deterministic, typed blocking result.

    The result is analysis-only metadata.  It cannot change a release decision and intentionally
    has no release-authority field.
    """

    _require_runtime_roots(
        graph,
        ontology,
        profile,
        registry,
        policy,
        expected_registry_sha256=expected_registry_sha256,
        expected_policy_sha256=expected_policy_sha256,
        expected_source_graph_sha256=expected_source_graph_sha256,
        expected_ontology_sha256=expected_ontology_sha256,
        expected_profile_sha256=expected_profile_sha256,
        expected_project_id=expected_project_id,
        expected_source_snapshot=expected_source_snapshot,
        expected_normalized_graph_sha256=expected_normalized_graph_sha256,
        implementation_root=implementation_root,
        source_graph_content=source_graph_content,
    )
    evaluated_text = _format_timestamp(evaluated_at)
    evaluated = _parse_timestamp(evaluated_text)
    graph_edges = {item.edge_id: item for item in graph.edges}
    if not required_edge_ids:
        raise TrustedEdgeContractError("Verification requires an explicit non-empty edge scope")
    if tuple(sorted(set(required_edge_ids))) != required_edge_ids:
        raise TrustedEdgeContractError("Required edge scope must be sorted and unique")
    graph_valid = _graph_integrity_valid(graph)
    artifact_by_locator: dict[str, str] = {}
    duplicate_artifacts: set[str] = set()
    for item in artifact_contents:
        prior = artifact_by_locator.get(item.locator)
        if prior is not None:
            duplicate_artifacts.add(item.locator)
        else:
            artifact_by_locator[item.locator] = artifact_sha256(item.content)
    envelope_counts: dict[str, int] = {}
    for envelope in envelopes:
        envelope_counts[envelope.edge_id] = envelope_counts.get(envelope.edge_id, 0) + 1
    accepted: list[TrustedEdgeEnvelope] = []
    rejection_keys: set[tuple[str, EdgeEnvelopeRejectionCode]] = set()

    def reject(edge_id: str, code: EdgeEnvelopeRejectionCode) -> None:
        rejection_keys.add((_edge_identity_sha256(edge_id), code))

    for edge_id in required_edge_ids:
        if envelope_counts.get(edge_id, 0) == 0:
            reject(edge_id, EdgeEnvelopeRejectionCode.MISSING_EDGE_ENVELOPE)
        if edge_id not in graph_edges:
            reject(edge_id, EdgeEnvelopeRejectionCode.UNKNOWN_EDGE)
    for envelope in envelopes:
        codes: set[EdgeEnvelopeRejectionCode] = set()
        edge = graph_edges.get(envelope.edge_id)
        try:
            TrustedEdgeEnvelope.model_validate(envelope.model_dump(mode="json"))
        except ValidationError:
            codes.add(EdgeEnvelopeRejectionCode.EDGE_ENVELOPE_DIGEST_MISMATCH)
        if envelope_counts[envelope.edge_id] > 1:
            codes.add(EdgeEnvelopeRejectionCode.DUPLICATE_EDGE_ENVELOPE)
        if edge is None:
            codes.add(EdgeEnvelopeRejectionCode.UNKNOWN_EDGE)
        if not graph_valid:
            codes.add(EdgeEnvelopeRejectionCode.NORMALIZED_GRAPH_INTEGRITY_FAILURE)
        envelope_body = envelope.model_dump(mode="json")
        declared = envelope_body.pop("envelope_sha256")
        if stable_sha256(envelope_body) != declared:
            codes.add(EdgeEnvelopeRejectionCode.EDGE_ENVELOPE_DIGEST_MISMATCH)
        if edge is not None:
            signature = _signature_for(edge, graph)
            if envelope.edge_id != edge.edge_id:
                codes.add(EdgeEnvelopeRejectionCode.EDGE_ID_MISMATCH)
            if envelope.canonical_relation != edge.canonical_relation:
                codes.add(EdgeEnvelopeRejectionCode.RELATION_MISMATCH)
            if (
                signature is None
                or envelope.endpoint_signature != signature
                or not _signature_is_legal(
                    edge.canonical_relation,
                    signature,
                    ontology,
                    edge=edge,
                    profile=profile,
                )
            ):
                codes.add(EdgeEnvelopeRejectionCode.ILLEGAL_ENDPOINT_SIGNATURE)
            if edge.evidence_state not in policy.accepted_evidence_states:
                codes.add(EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED)
            if envelope.evidence_state not in policy.accepted_evidence_states:
                codes.add(EdgeEnvelopeRejectionCode.EDGE_EVIDENCE_STATE_REJECTED)
            if edge.source != envelope.source_artifact_locator:
                codes.add(EdgeEnvelopeRejectionCode.SOURCE_ARTIFACT_LOCATOR_MISMATCH)
            if edge.extractor_id != envelope.extractor_id:
                codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_IDENTITY_MISMATCH)
            if (
                edge.extractor_version != envelope.extractor_version
                or edge.extractor_implementation_sha256
                != envelope.extractor_implementation_sha256
            ):
                codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_IDENTITY_MISMATCH)
            if edge.source_artifact_sha256 != envelope.source_artifact_sha256:
                codes.add(EdgeEnvelopeRejectionCode.ARTIFACT_DIGEST_MISMATCH)
            if edge.source_snapshot != graph.source_snapshot:
                codes.add(EdgeEnvelopeRejectionCode.SNAPSHOT_MISMATCH)
            if edge.source_hash != graph.source_graph_sha256:
                codes.add(EdgeEnvelopeRejectionCode.GRAPH_ROOT_MISMATCH)
        if envelope.project_id != graph.project_id:
            codes.add(EdgeEnvelopeRejectionCode.PROJECT_MISMATCH)
        if envelope.source_snapshot != graph.source_snapshot:
            codes.add(EdgeEnvelopeRejectionCode.SNAPSHOT_MISMATCH)
        if (
            envelope.source_graph_sha256 != graph.source_graph_sha256
            or envelope.normalized_graph_sha256 != graph.graph_sha256
        ):
            codes.add(EdgeEnvelopeRejectionCode.GRAPH_ROOT_MISMATCH)
        if (
            envelope.ontology_id,
            envelope.ontology_version,
            envelope.ontology_sha256,
        ) != (graph.ontology_id, graph.ontology_version, graph.ontology_sha256):
            codes.add(EdgeEnvelopeRejectionCode.ONTOLOGY_ROOT_MISMATCH)
        if (
            envelope.profile_id,
            envelope.profile_version,
            envelope.profile_sha256,
        ) != (graph.profile_id, graph.profile_version, graph.profile_sha256):
            codes.add(EdgeEnvelopeRejectionCode.PROFILE_ROOT_MISMATCH)
        if (
            envelope.trust_policy_id,
            envelope.trust_policy_version,
            envelope.trust_policy_sha256,
        ) != (policy.policy_id, policy.policy_version, policy.sha256):
            codes.add(EdgeEnvelopeRejectionCode.POLICY_ROOT_MISMATCH)
        if (
            envelope.extractor_registry_id,
            envelope.extractor_registry_version,
            envelope.extractor_registry_sha256,
        ) != (registry.registry_id, registry.registry_version, registry.sha256):
            codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_REGISTRY_MISMATCH)
        entry = registry.entries_by_identity.get(
            (envelope.extractor_id, envelope.extractor_version)
        )
        if entry is None:
            codes.add(EdgeEnvelopeRejectionCode.UNKNOWN_EXTRACTOR)
        else:
            if not entry.enabled:
                codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_DISABLED)
            if entry.implementation_sha256 != envelope.extractor_implementation_sha256:
                codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_IMPLEMENTATION_MISMATCH)
        try:
            require_safe_repository_locator(envelope.source_artifact_locator)
        except UnsafeLocatorError:
            codes.add(EdgeEnvelopeRejectionCode.UNSAFE_ARTIFACT_LOCATOR)
        artifact_digest = artifact_by_locator.get(envelope.source_artifact_locator)
        if envelope.source_artifact_locator in duplicate_artifacts:
            codes.add(EdgeEnvelopeRejectionCode.DUPLICATE_ARTIFACT_LOCATOR)
        if artifact_digest is None:
            codes.add(EdgeEnvelopeRejectionCode.MISSING_ARTIFACT_DIGEST)
        elif artifact_digest != envelope.source_artifact_sha256:
            codes.add(EdgeEnvelopeRejectionCode.ARTIFACT_DIGEST_MISMATCH)
        content = next(
            (
                item.content
                for item in artifact_contents
                if item.locator == envelope.source_artifact_locator
            ),
            None,
        )
        if content is not None:
            try:
                if entry is None or not entry.enabled:
                    raise EdgeExtractionError("Extractor is not registered")
                extracted = _execute_extractor(entry, content)
            except EdgeExtractionError:
                extracted = {}
            expected_claim = {
                "canonicalRelation": envelope.canonical_relation,
                "edgeId": envelope.edge_id,
                "evidenceState": envelope.evidence_state.value,
                "extractorId": envelope.extractor_id,
                "extractorVersion": envelope.extractor_version,
                "sourceId": envelope.endpoint_signature.source_id,
                "sourceSnapshot": envelope.source_snapshot,
                "targetId": envelope.endpoint_signature.target_id,
            }
            if extracted != expected_claim:
                codes.add(EdgeEnvelopeRejectionCode.EXTRACTOR_EXECUTION_MISMATCH)
        try:
            observed = _parse_timestamp(envelope.observed_at)
            valid_until = _parse_timestamp(envelope.valid_until)
            if envelope.freshness_seconds > policy.maximum_freshness_seconds:
                codes.add(EdgeEnvelopeRejectionCode.FRESHNESS_EXCEEDS_POLICY)
            if observed > evaluated and int((observed - evaluated).total_seconds()) > (
                policy.maximum_future_skew_seconds
            ):
                codes.add(EdgeEnvelopeRejectionCode.EVIDENCE_FROM_FUTURE)
            if valid_until <= evaluated:
                codes.add(EdgeEnvelopeRejectionCode.EVIDENCE_EXPIRED)
        except ValueError:
            codes.add(EdgeEnvelopeRejectionCode.EDGE_ENVELOPE_DIGEST_MISMATCH)
        if codes:
            for code in codes:
                reject(envelope.edge_id, code)
        else:
            accepted.append(envelope)
    rejections = tuple(
        EdgeEnvelopeRejection(code=code, edge_identity_sha256=edge_digest)
        for edge_digest, code in sorted(rejection_keys, key=lambda item: (item[0], item[1].value))
    )
    accepted_tuple = tuple(sorted(accepted, key=lambda item: item.edge_id))
    coverage_complete = (
        set(required_edge_ids) <= {item.edge_id for item in accepted_tuple} and not rejections
    )
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "project_id": graph.project_id or "unavailable",
        "source_snapshot": graph.source_snapshot or "unavailable",
        "source_graph_sha256": graph.source_graph_sha256 or "0" * 64,
        "normalized_graph_sha256": graph.graph_sha256,
        "ontology_id": graph.ontology_id,
        "ontology_version": graph.ontology_version,
        "ontology_sha256": graph.ontology_sha256,
        "profile_id": graph.profile_id,
        "profile_version": graph.profile_version,
        "profile_sha256": graph.profile_sha256,
        "trust_policy_sha256": policy.sha256,
        "extractor_registry_sha256": registry.sha256,
        "evaluated_at": evaluated_text,
        "required_edge_ids": list(required_edge_ids),
        "coverage_complete": coverage_complete,
        "release_eligible": False,
        "blocking_gap_codes": ["UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"],
        "accepted_envelopes": [item.model_dump(mode="json") for item in accepted_tuple],
        "rejections": [item.model_dump(mode="json") for item in rejections],
    }
    return TrustedEdgeVerification.model_validate(
        {**body, "verification_sha256": stable_sha256(body)}
    )
