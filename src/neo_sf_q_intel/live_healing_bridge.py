"""Host-only, non-authorizing source-to-healing translation. No I/O or live dispatch.

Raw metadata preimages and source locator declarations remain in memory. Only the report is
an output projection; it contains roots, counts and gaps, never locators or XML. A production
candidate proof verifier and a stateful Python/TypeScript healing dispatch do not yet exist.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import ConfigDict, Field

from neo_sf_q_intel.candidate_target_compiler import CandidateLivePlanComposition
from neo_sf_q_intel.live_healing import (
    Digest,
    HealingAssertion,
    HealingCommand,
    HealingFailure,
    HealingGap,
    HealingLease,
    HealingObservation,
    HealingRequest,
    SourceHealingTarget,
    digest,
)
from neo_sf_q_intel.live_read_evidence import _metadata_manifest
from neo_sf_q_intel.live_receipts import EvidencePhase, ReceiptScope
from neo_sf_q_intel.live_target_plan import (
    BrowserLocatorObligation,
    TargetPartition,
    _unordered_canonical_sha256,
    derive_target_sha256,
)
from neo_sf_q_intel.metadata_recovery import (
    MetadataPropertyIntent,
    edit_compiled_property,
    metadata_state_sha256,
)
from neo_sf_q_intel.ontology import contract_sha256
from neo_sf_q_intel.temporal import UtcModel, aware_utc, parse_aware_utc


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HealingBridgeGap(StrEnum):
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    SOURCE_BINDING_EXPIRED = "SOURCE_BINDING_EXPIRED"
    SOURCE_PLAN_BLOCKED = "SOURCE_PLAN_BLOCKED"
    NO_LOCATOR_REBIND_TARGET = "NO_LOCATOR_REBIND_TARGET"
    MULTI_TARGET_METADATA_SCOPE_UNSUPPORTED = "MULTI_TARGET_METADATA_SCOPE_UNSUPPORTED"
    CANDIDATE_EXECUTION_BINDING_UNAVAILABLE = "CANDIDATE_EXECUTION_BINDING_UNAVAILABLE"
    CANDIDATE_EXECUTION_BINDING_INVALID = "CANDIDATE_EXECUTION_BINDING_INVALID"
    METADATA_PREIMAGE_INVALID = "METADATA_PREIMAGE_INVALID"
    BROWSER_HEALING_DISPATCH_UNAVAILABLE = "BROWSER_HEALING_DISPATCH_UNAVAILABLE"


class HealingBridgeError(RuntimeError):
    def __init__(self, code: HealingBridgeGap):
        self.code = code
        super().__init__(code.value)


class CompiledHealingSource(_Model):
    """Private source intent, not an execution authority or an accepted receipt."""

    authority_scope: Literal["SOURCE_BINDING_ONLY"] = "SOURCE_BINDING_ONLY"
    authorizes_execution: Literal[False] = False
    composition_sha256: Digest
    compilation_sha256: Digest
    target_plan_sha256: Digest
    baseline_execution_contract_sha256: Digest
    classification_receipt_sha256: Digest
    scope: ReceiptScope
    valid_until: datetime
    deployment_test_methods: tuple[str, ...]
    targets: tuple[SourceHealingTarget, ...] = Field(min_length=1, max_length=100)


def assertion_expected_value_sha256(obligation: BrowserLocatorObligation) -> str:
    """One obligation means the conjunction of *all* its read-only assertions is true."""
    return contract_sha256({assertion: True for assertion in obligation.assertions})


def compile_healing_source(
    composition: CandidateLivePlanComposition, *, now: datetime
) -> CompiledHealingSource:
    """Revalidate a configured host compiler result; never compile caller source/path JSON."""
    try:
        current = aware_utc(now)
        # Revalidate nested model_construct/model_copy and detach mutable specification dicts.
        captured = CandidateLivePlanComposition.model_validate_json(
            composition.model_dump_json(warnings=False)
        )
        compilation, evaluation = captured.compilation, captured.evaluation
        plan, expected = evaluation.plan, captured.expected_execution_contract
        if evaluation.state != "READY" or plan is None or expected is None:
            raise HealingBridgeError(HealingBridgeGap.SOURCE_PLAN_BLOCKED)
        if compilation.blocking_reason_codes or captured.deferred_local_validations:
            raise HealingBridgeError(HealingBridgeGap.SOURCE_PLAN_BLOCKED)
        scope, profile = compilation.verified_scope, compilation.source_operation_profile
        bindings = (
            (plan.project_id, scope.project_id),
            (plan.source_snapshot_sha256, scope.source_snapshot_sha256),
            (plan.verified_change_sha256, scope.verified_change_sha256),
            (plan.semantic_graph_sha256, scope.semantic_graph_sha256),
            (plan.ontology_sha256, scope.ontology_sha256),
            (plan.graph_source_profile_sha256, scope.source_profile_sha256),
            (plan.scope_artifact_sha256, scope.artifact_sha256),
            (plan.source_operation_profile_id, profile.profile_id),
            (plan.source_operation_profile_version, profile.profile_version),
            (
                plan.source_operation_profile_bytes_sha256,
                hashlib.sha256(compilation.source_operation_profile_bytes).hexdigest(),
            ),
            (
                plan.source_operation_profile_canonical_sha256,
                _unordered_canonical_sha256(profile.model_dump(by_alias=True, mode="json")),
            ),
            (
                evaluation.source_operation_profile_bytes_sha256,
                plan.source_operation_profile_bytes_sha256,
            ),
            (evaluation.scope_artifact_sha256, scope.artifact_sha256),
            (evaluation.host_policy_sha256, plan.host_policy_sha256),
            (
                evaluation.source_operation_profile_canonical_sha256,
                plan.source_operation_profile_canonical_sha256,
            ),
            (expected.scope.project_id, scope.project_id),
            (expected.scope.source_contract_sha256, compilation.source_contract_sha256),
            (expected.scope.candidate_sha256, compilation.candidate_bundle_sha256),
            (expected.scope.policy_sha256, plan.host_policy_sha256),
            (expected.scope.profile_sha256, plan.acceptance_profile_sha256),
        )
        if any(left != right for left, right in bindings):
            raise HealingBridgeError(HealingBridgeGap.SOURCE_BINDING_INVALID)
        groups = (
            (TargetPartition.STANDARD_REST, profile.standard_rest),
            (TargetPartition.CUSTOM_REST, profile.custom_rest),
            (TargetPartition.METADATA, profile.metadata),
            (TargetPartition.APEX_TEST, profile.apex_tests),
            (TargetPartition.BROWSER_INTENT, profile.browser_intents),
            (TargetPartition.SYNTHETIC_DATASET, profile.synthetic_datasets),
        )
        declared = tuple(
            sorted(
                derive_target_sha256(partition, target)
                for partition, targets in groups
                for target in targets
            )
        )
        if (
            len(declared) != len(set(declared))
            or tuple(sorted(value.target_sha256 for value in plan.targets)) != declared
            or evaluation.authorized_target_sha256s != declared
            or evaluation.proposed_target_sha256s != declared
        ):
            raise HealingBridgeError(HealingBridgeGap.SOURCE_BINDING_INVALID)
        by_target = {
            derive_target_sha256(partition, target): target
            for partition, targets in groups
            for target in targets
        }
        if any(
            "sourceEntityIds" in target.specification
            or target.source_entity_ids
            != tuple(sorted(by_target[target.target_sha256].source_entity_ids))
            for target in plan.targets
        ) or {
            value.target_sha256
            for value in expected.assertions
            if value.partition is TargetPartition.BROWSER_INTENT
        } != {
            value.target_sha256
            for value in plan.targets
            if value.partition is TargetPartition.BROWSER_INTENT
        }:
            raise HealingBridgeError(HealingBridgeGap.SOURCE_BINDING_INVALID)
        until = min(
            parse_aware_utc(plan.valid_until),
            parse_aware_utc(scope.valid_until),
            parse_aware_utc(expected.valid_until),
            expected.scope.recovery_deadline,
        )
        if current < parse_aware_utc(plan.evaluated_at) or current >= until:
            raise HealingBridgeError(HealingBridgeGap.SOURCE_BINDING_EXPIRED)
        targets = []
        for browser in profile.browser_intents:
            rebind = browser.locator_rebind
            if rebind is None:
                continue  # Other declared browser actions remain in the exact full plan root.
            target_sha = derive_target_sha256(TargetPartition.BROWSER_INTENT, browser)
            source_assertion_sha = contract_sha256(
                {
                    "sourceContractSha256": compilation.source_contract_sha256,
                    "plannedTargetSha256": target_sha,
                    "locatorRebind": rebind.model_dump(by_alias=True, mode="json"),
                }
            )
            assertions = tuple(
                sorted(
                    (
                        HealingAssertion(
                            obligation_sha256=contract_sha256(
                                {
                                    "sourceAssertionContractSha256": source_assertion_sha,
                                    "obligation": obligation.model_dump(by_alias=True, mode="json"),
                                }
                            ),
                            original_locator_sha256=contract_sha256(
                                obligation.original_locator.model_dump(by_alias=True, mode="json")
                            ),
                            semantic_identity_sha256=contract_sha256(
                                obligation.semantic_identity.model_dump(by_alias=True, mode="json")
                            ),
                            expected_value_sha256=assertion_expected_value_sha256(obligation),
                        )
                        for obligation in rebind.obligations
                    ),
                    key=lambda item: item.obligation_sha256,
                )
            )
            targets.append(
                SourceHealingTarget(
                    browser_intent=browser,
                    planned_target_sha256=target_sha,
                    source_assertion_contract_sha256=source_assertion_sha,
                    assertions=assertions,
                )
            )
        if not targets:
            raise HealingBridgeError(HealingBridgeGap.NO_LOCATOR_REBIND_TARGET)
        return CompiledHealingSource(
            composition_sha256=captured.composition_sha256,
            compilation_sha256=compilation.compilation_sha256,
            target_plan_sha256=plan.plan_sha256,
            baseline_execution_contract_sha256=expected.contract_sha256,
            classification_receipt_sha256=plan.organization_classification_receipt_sha256,
            scope=expected.scope,
            valid_until=until,
            deployment_test_methods=tuple(
                sorted({f"{value.apex_class}.{value.method_name}" for value in profile.apex_tests})
            ),
            targets=tuple(sorted(targets, key=lambda item: item.planned_target_sha256)),
        )
    except HealingBridgeError:
        raise
    except Exception:
        raise HealingBridgeError(HealingBridgeGap.SOURCE_BINDING_INVALID) from None


@dataclass(frozen=True, slots=True, repr=False)
class HostMetadataPreimage:
    """Private bounded authenticated retrieval bytes; no caller file/path parameter."""

    source_binding_sha256: str
    target_sha256: str
    state_sha256: str
    receipt_sha256: str
    observed_at: datetime
    valid_until: datetime
    manifest_bytes: bytes
    member_bytes: bytes


def preview_metadata_intent(
    source: CompiledHealingSource,
    preimage: HostMetadataPreimage,
    *,
    api_version: str,
    now: datetime,
) -> MetadataPropertyIntent:
    """Pure preview, not authentication; host verifier must authenticate these exact bytes.

    Declaration root uses the shared source model's alias JSON; the final intent root uses
    digest(intent), the recovery adapter's canonical runtime snake_case representation.
    """
    try:
        if len(source.targets) != 1:
            raise HealingBridgeError(HealingBridgeGap.MULTI_TARGET_METADATA_SCOPE_UNSUPPORTED)
        target = source.targets[0]
        rebind = target.browser_intent.locator_rebind
        assert rebind is not None
        drift = rebind.metadata_drift
        if (
            preimage.source_binding_sha256 != digest(source)
            or preimage.target_sha256 != target.planned_target_sha256
            or not re.fullmatch(r"[a-f0-9]{64}", preimage.receipt_sha256)
            or not re.fullmatch(r"[1-9][0-9]{0,2}\.0", api_version)
            or not aware_utc(preimage.observed_at)
            <= aware_utc(now)
            < min(aware_utc(preimage.valid_until), source.valid_until)
            or type(preimage.manifest_bytes) is not bytes
            or type(preimage.member_bytes) is not bytes
            or not 0 < len(preimage.manifest_bytes) <= 16_384
            or not 0 < len(preimage.member_bytes) <= drift.maximum_bytes
            or _metadata_manifest(preimage.manifest_bytes) != [(drift.metadata_type, drift.member)]
        ):
            raise ValueError("Invalid preimage")
        namespace = "{http://soap.sforce.com/2006/04/metadata}"
        version = ET.fromstring(preimage.manifest_bytes).find(namespace + "version")
        if version is None or version.text != api_version or len(version) or version.attrib:
            raise ValueError("Manifest API binding differs")
        member_path = f"unpackaged/flexipages/{drift.member}.flexipage"
        files = {
            "unpackaged/package.xml": preimage.manifest_bytes,
            member_path: preimage.member_bytes,
        }
        before = metadata_state_sha256(files)
        if before != preimage.state_sha256:
            raise ValueError("Preimage digest differs")
        candidate = {**files, member_path: edit_compiled_property(preimage.member_bytes, drift)}
        return MetadataPropertyIntent(
            scope_sha256=digest(source.scope),
            source_contract_sha256=source.scope.source_contract_sha256,
            source_property_declaration_sha256=contract_sha256(
                drift.model_dump(by_alias=True, mode="json")
            ),
            target_plan_sha256=source.target_plan_sha256,
            drift=drift,
            expected_preimage_sha256=before,
            expected_candidate_sha256=metadata_state_sha256(candidate),
            api_version=api_version,
            test_level="RunSpecifiedTests" if source.deployment_test_methods else "NoTestRun",
            test_classes=tuple(
                sorted({value.split(".")[0] for value in source.deployment_test_methods})
            ),
            tests=source.deployment_test_methods,
            expected_test_count=len(source.deployment_test_methods),
        )
    except HealingBridgeError:
        raise
    except Exception:
        raise HealingBridgeError(HealingBridgeGap.METADATA_PREIMAGE_INVALID) from None


class CandidateHealingBinding(_Model):
    """Proof references, not a self-attesting authorization. A host port must verify them."""

    source_binding_sha256: Digest
    scope_sha256: Digest
    evidence_phase: Literal[EvidencePhase.DEPLOYED_CANDIDATE]
    expected_execution_contract_sha256: Digest
    classification_receipt_sha256: Digest
    session_enrollment_receipt_sha256: Digest
    metadata_intent_sha256: Digest
    metadata_preimage_receipt_sha256: Digest
    expected_residue_sha256: Digest
    valid_until: datetime


@dataclass(frozen=True, slots=True, repr=False)
class HostHealingEvidence:
    binding: CandidateHealingBinding
    preimage: HostMetadataPreimage


class HostHealingEvidencePort(Protocol):
    """Host-only port; no production implementation exists yet.

    capture returns current host-owned references/bytes, never caller JSON. verify must
    independently authenticate the candidate-phase expected contract, metadata preimage,
    classification and session receipts against the full source/scope/request/intent, including
    org, actor, API, policy, source, all obligations, expiry, and expected residue accounting.
    A matching digest or baseline expectation alone is not proof. It must raise on any gap.
    Execution still requires the orchestrator's separate durable one-use authority/restore lease.
    """

    def capture(self, source: CompiledHealingSource, api_version: str) -> HostHealingEvidence: ...

    def verify(
        self,
        source: CompiledHealingSource,
        evidence: HostHealingEvidence,
        intent: MetadataPropertyIntent,
        request: HealingRequest,
        now: datetime,
    ) -> None: ...


class HealingBridgeReport(_Model):
    status: Literal["NOT_READY"] = "NOT_READY"
    capability_status: Literal["FOUNDATION"] = "FOUNDATION"
    authorizes_execution: Literal[False] = False
    acceptance_credit: Literal[False] = False
    release_eligible: Literal[False] = False
    source_binding_sha256: Digest | None = None
    request_sha256: Digest | None = None
    metadata_intent_sha256: Digest | None = None
    target_sha256s: tuple[Digest, ...] = ()
    obligation_count: int = Field(ge=0, le=20_000)
    gap_codes: tuple[HealingBridgeGap, ...]


@dataclass(frozen=True, slots=True, repr=False)
class HostHealingPreparation:
    """In-process product intent. Persist/export only report, not this private container."""

    report: HealingBridgeReport
    source: CompiledHealingSource | None = None
    request: HealingRequest | None = None
    metadata_intent: MetadataPropertyIntent | None = None


class UnavailableBrowserHealingDispatch:
    """Explicit current coordinator limit; never redirect healing to diagnostic smoke.

    The TS coordinator exposes runReadOnlySmoke/READ_ONLY_DOM_CAPTURE; worker CANDIDATE_READBACK
    does not implement per-obligation locator rebind, a retained session across metadata drift,
    or authenticated HealingObservation production. Neither method launches a process/browser.
    """

    gap = HealingBridgeGap.BROWSER_HEALING_DISPATCH_UNAVAILABLE

    def execute(self, command: HealingCommand, lease: HealingLease) -> HealingObservation:
        raise HealingFailure(HealingGap.EXECUTION_DISABLED)

    def close_local(self, command: HealingCommand) -> HealingObservation:
        # No fabricated successful closure receipt, even though this adapter opens nothing.
        raise HealingFailure(HealingGap.EXECUTION_DISABLED)


class HostSourceHealingBridge:
    """Zero-argument preparation from configured host services; no API/MCP endpoint or writes."""

    def __init__(
        self,
        capture_composition: Callable[[], CandidateLivePlanComposition],
        *,
        api_version: str,
        evidence_port: HostHealingEvidencePort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not re.fullmatch(r"[1-9][0-9]{0,2}\.0", api_version):
            raise ValueError("Invalid host API version")
        self._capture = capture_composition
        self._api_version = api_version
        self._evidence = evidence_port
        self._clock = clock or (lambda: datetime.now(UTC))

    def prepare(self) -> HostHealingPreparation:
        source = request = intent = None
        gaps = {HealingBridgeGap.BROWSER_HEALING_DISPATCH_UNAVAILABLE}
        try:
            source = compile_healing_source(self._capture(), now=self._clock())
            if self._evidence is None:
                gaps.add(HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_UNAVAILABLE)
            elif len(source.targets) != 1:
                gaps.add(HealingBridgeGap.MULTI_TARGET_METADATA_SCOPE_UNSUPPORTED)
            else:
                try:
                    evidence = self._evidence.capture(source, self._api_version)
                    binding = CandidateHealingBinding.model_validate_json(
                        evidence.binding.model_dump_json(warnings=False)
                    )
                    intent = preview_metadata_intent(
                        source,
                        evidence.preimage,
                        api_version=self._api_version,
                        now=self._clock(),
                    )
                    if (
                        binding.source_binding_sha256 != digest(source)
                        or binding.scope_sha256 != digest(source.scope)
                        or binding.classification_receipt_sha256
                        != source.classification_receipt_sha256
                        or binding.metadata_intent_sha256 != digest(intent)
                        or binding.metadata_preimage_receipt_sha256
                        != evidence.preimage.receipt_sha256
                        or binding.expected_execution_contract_sha256
                        == source.baseline_execution_contract_sha256
                        or aware_utc(self._clock()) >= min(binding.valid_until, source.valid_until)
                    ):
                        raise ValueError("Candidate binding differs")
                    proposed = HealingRequest(
                        scope=source.scope,
                        evidence_phase=binding.evidence_phase,
                        target_plan_sha256=source.target_plan_sha256,
                        expected_execution_contract_sha256=binding.expected_execution_contract_sha256,
                        classification_receipt_sha256=binding.classification_receipt_sha256,
                        session_enrollment_receipt_sha256=binding.session_enrollment_receipt_sha256,
                        expected_aut_prestate_sha256=intent.expected_preimage_sha256,
                        expected_residue_sha256=binding.expected_residue_sha256,
                        temporary_metadata_plan_sha256=digest(intent),
                        temporary_metadata_expected_state_sha256=intent.expected_candidate_sha256,
                        targets=source.targets,
                    )
                    self._evidence.verify(source, evidence, intent, proposed, self._clock())
                    if aware_utc(self._clock()) >= min(
                        binding.valid_until, source.valid_until, evidence.preimage.valid_until
                    ):
                        raise ValueError("Proof expired during verification")
                    request = proposed
                except HealingBridgeError:
                    raise
                except Exception:
                    raise HealingBridgeError(
                        HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_INVALID
                    ) from None
        except HealingBridgeError as error:
            gaps.add(error.code)
            request = intent = None
        except Exception:
            gaps.add(HealingBridgeGap.SOURCE_BINDING_INVALID)
            request = intent = None
        report = HealingBridgeReport(
            source_binding_sha256=digest(source) if source else None,
            request_sha256=digest(request) if request else None,
            metadata_intent_sha256=digest(intent) if intent else None,
            target_sha256s=tuple(value.planned_target_sha256 for value in source.targets)
            if source
            else (),
            obligation_count=sum(len(value.assertions) for value in source.targets)
            if source
            else 0,
            gap_codes=tuple(sorted(gaps)),
        )
        return HostHealingPreparation(report, source, request, intent)
