from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neo_sf_q_intel.candidate_target_compiler import (
    ProductionCandidateTargetCompiler,
    compose_candidate_live_plan,
)
from neo_sf_q_intel.live_healing import HealingFailure, HealingGap, digest
from neo_sf_q_intel.live_healing_bridge import (
    CandidateHealingBinding,
    HealingBridgeError,
    HealingBridgeGap,
    HostHealingEvidence,
    HostMetadataPreimage,
    HostSourceHealingBridge,
    UnavailableBrowserHealingDispatch,
    assertion_expected_value_sha256,
    compile_healing_source,
    preview_metadata_intent,
)
from neo_sf_q_intel.live_receipts import EvidencePhase
from neo_sf_q_intel.live_target_plan import TargetPartition
from neo_sf_q_intel.metadata_recovery import (
    _root,
    edit_compiled_property,
    metadata_state_sha256,
)
from neo_sf_q_intel.ontology import contract_sha256
from tests.test_candidate_target_compiler import (
    _allow_all_policy,
    _bundle,
    _classification,
    _declarations,
    _inputs,
    _runner,
)

NS = "http://soap.sforce.com/2006/04/metadata"
SECRET = "credential-canary-never-exported"


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(scope="module")
def composition(tmp_path_factory):
    compilation = ProductionCandidateTargetCompiler().compile(
        _inputs(_bundle(tmp_path_factory.mktemp("healing-bridge-source")))
    )
    policy = _allow_all_policy(compilation)
    result = compose_candidate_live_plan(
        compilation,
        _classification(compilation, policy),
        policy,
        runner_provenance=_runner(),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    )
    assert result.evaluation.state == "READY"
    return result


@pytest.fixture
def now(composition):
    return datetime.fromisoformat(composition.evaluation.plan.evaluated_at.replace("Z", "+00:00"))


@pytest.fixture
def source(composition, now):
    return compile_healing_source(composition, now=now)


def preimage_for(source, now, *, present=False):
    target = source.targets[0]
    drift = target.browser_intent.locator_rebind.metadata_drift
    manifest = (
        f'<Package xmlns="{NS}"><types><members>{drift.member}</members>'
        "<name>FlexiPage</name></types><version>67.0</version></Package>"
    ).encode()
    prop = (
        f"<componentInstanceProperties><name>{drift.property_name}</name>"
        f"<value>{drift.baseline_value}</value></componentInstanceProperties>"
        if present
        else ""
    )
    member = (
        f'<FlexiPage xmlns="{NS}"><flexiPageRegions><itemInstances><componentInstance>'
        f"{prop}<componentName>{drift.component_name}</componentName>"
        f"<identifier>{drift.component_identifier}</identifier>"
        "</componentInstance></itemInstances></flexiPageRegions>"
        f"<description>{SECRET}</description></FlexiPage>"
    ).encode()
    state = metadata_state_sha256(
        {
            "unpackaged/package.xml": manifest,
            f"unpackaged/flexipages/{drift.member}.flexipage": member,
        }
    )
    return HostMetadataPreimage(
        source_binding_sha256=digest(source),
        target_sha256=target.planned_target_sha256,
        state_sha256=state,
        receipt_sha256=h("authenticated-preimage"),
        observed_at=now,
        valid_until=now + timedelta(minutes=2),
        manifest_bytes=manifest,
        member_bytes=member,
    )


class VerifiedFakePort:
    """Offline fake verifier. Not a receipt issuer or a production authority implementation."""

    def __init__(self, now, *, binding_changes=None, preimage_changes=None, reject=False):
        self.now = now
        self.binding_changes = binding_changes or {}
        self.preimage_changes = preimage_changes or {}
        self.reject = reject
        self.captured = self.verified = 0

    def capture(self, source, api_version):
        self.captured += 1
        preimage = preimage_for(source, self.now)
        intent = preview_metadata_intent(source, preimage, api_version=api_version, now=self.now)
        binding = CandidateHealingBinding(
            source_binding_sha256=digest(source),
            scope_sha256=digest(source.scope),
            evidence_phase=EvidencePhase.DEPLOYED_CANDIDATE,
            expected_execution_contract_sha256=h("independent-candidate-contract"),
            classification_receipt_sha256=source.classification_receipt_sha256,
            session_enrollment_receipt_sha256=h("session"),
            metadata_intent_sha256=digest(intent),
            metadata_preimage_receipt_sha256=preimage.receipt_sha256,
            expected_residue_sha256=h("independently-authenticated-residue-inventory"),
            valid_until=self.now + timedelta(minutes=2),
        ).model_copy(update=self.binding_changes)
        return HostHealingEvidence(binding, replace(preimage, **self.preimage_changes))

    def verify(self, source, evidence, intent, request, now):
        self.verified += 1
        assert request.targets == source.targets
        assert request.scope == source.scope
        assert request.temporary_metadata_plan_sha256 == digest(intent)
        assert request.evidence_phase is EvidencePhase.DEPLOYED_CANDIDATE
        if self.reject:
            raise RuntimeError(SECRET)


def bridge(composition, now, port=None):
    return HostSourceHealingBridge(
        lambda: composition, api_version="67.0", evidence_port=port, clock=lambda: now
    )


def reseal(model, field, **changes):
    body = model.model_copy(update=changes).model_dump(mode="json", warnings=False)
    body.pop(field)
    body[field] = contract_sha256(body)
    return type(model).model_validate(body)


def with_plan(composition, **changes):
    """Even self-consistent recomputed surrounding hashes must not hide cross-source roots."""
    plan = reseal(composition.evaluation.plan, "plan_sha256", **changes)
    expectation = reseal(
        composition.expected_execution_contract, "contract_sha256", plan_sha256=plan.plan_sha256
    )
    expectation_bytes = json.dumps(
        expectation.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    evaluation = reseal(composition.evaluation, "evaluation_sha256", plan=plan)
    return reseal(
        composition,
        "composition_sha256",
        evaluation=evaluation,
        expected_execution_contract=expectation,
        expected_execution_contract_bytes=expectation_bytes,
        expected_execution_contract_bytes_sha256=hashlib.sha256(expectation_bytes).hexdigest(),
    )


def test_complete_source_obligations_are_derived_not_inferred(composition, source):
    assert not source.authorizes_execution
    expected = composition.compilation.source_operation_profile.browser_intents[0].locator_rebind
    target = source.targets[0]
    assert target.browser_intent.locator_rebind == expected
    assert len(target.assertions) == len(expected.obligations) == 2
    assert tuple(value.obligation_sha256 for value in target.assertions) == tuple(
        sorted(value.obligation_sha256 for value in target.assertions)
    )
    by_locator = {value.original_locator_sha256: value for value in target.assertions}
    for obligation in expected.obligations:
        item = by_locator[
            contract_sha256(obligation.original_locator.model_dump(by_alias=True, mode="json"))
        ]
        assert item.semantic_identity_sha256 == contract_sha256(
            obligation.semantic_identity.model_dump(by_alias=True, mode="json")
        )
        assert item.expected_value_sha256 == assertion_expected_value_sha256(obligation)
        assert item.expected_value_sha256 == contract_sha256(
            {name: True for name in obligation.assertions}
        )
    assert source.scope == composition.expected_execution_contract.scope


def test_default_preparation_reports_missing_proof_and_dispatch(composition, now):
    result = bridge(composition, now).prepare()
    assert result.source is not None
    assert result.request is result.metadata_intent is None
    assert result.report.gap_codes == (
        HealingBridgeGap.BROWSER_HEALING_DISPATCH_UNAVAILABLE,
        HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_UNAVAILABLE,
    )
    assert result.report.status == "NOT_READY"
    assert result.report.capability_status == "FOUNDATION"
    assert not any(
        (
            result.report.authorizes_execution,
            result.report.acceptance_credit,
            result.report.release_eligible,
        )
    )


@pytest.mark.parametrize("present", [False, True])
def test_exact_absent_or_present_preimage_uses_shared_pure_builder(source, now, present):
    preimage = preimage_for(source, now, present=present)
    intent = preview_metadata_intent(source, preimage, api_version="67.0", now=now)
    drift = source.targets[0].browser_intent.locator_rebind.metadata_drift
    candidate = edit_compiled_property(preimage.member_bytes, drift)
    assert preimage.member_bytes != candidate
    assert intent.drift == drift
    assert intent.expected_preimage_sha256 == preimage.state_sha256
    assert intent.expected_candidate_sha256 == metadata_state_sha256(
        {
            "unpackaged/package.xml": preimage.manifest_bytes,
            intent.relative_file: candidate,
        }
    )
    assert intent.source_property_declaration_sha256 == contract_sha256(
        drift.model_dump(by_alias=True, mode="json")
    )
    assert digest(intent) == _root(intent)
    assert intent.source_contract_sha256 == source.scope.source_contract_sha256
    assert intent.scope_sha256 == digest(source.scope)
    assert intent.target_plan_sha256 == source.target_plan_sha256
    assert intent.test_level == "RunSpecifiedTests"
    assert intent.tests == source.deployment_test_methods == ("DealPolicyTest.verifies",)
    assert intent.test_classes == ("DealPolicyTest",)
    assert intent.expected_test_count == len(source.deployment_test_methods)
    assert SECRET in candidate.decode()  # Non-property bytes are preserved privately, not exported.


def test_authenticated_candidate_binding_builds_request_but_does_not_execute(composition, now):
    port = VerifiedFakePort(now)
    result = bridge(composition, now, port).prepare()
    assert port.captured == port.verified == 1
    assert result.request is not None and result.metadata_intent is not None
    assert result.request.evidence_phase is EvidencePhase.DEPLOYED_CANDIDATE
    assert result.request.expected_execution_contract_sha256 != (
        composition.expected_execution_contract.contract_sha256
    )
    assert result.request.expected_aut_prestate_sha256 == (
        result.metadata_intent.expected_preimage_sha256
    )
    assert result.request.temporary_metadata_expected_state_sha256 == (
        result.metadata_intent.expected_candidate_sha256
    )
    assert result.report.gap_codes == (HealingBridgeGap.BROWSER_HEALING_DISPATCH_UNAVAILABLE,)
    assert result.report.status == "NOT_READY"


@pytest.mark.parametrize(
    "field",
    [
        "source_snapshot_sha256",
        "verified_change_sha256",
        "semantic_graph_sha256",
        "ontology_sha256",
        "graph_source_profile_sha256",
        "scope_artifact_sha256",
        "source_operation_profile_bytes_sha256",
        "source_operation_profile_canonical_sha256",
        "host_policy_sha256",
        "acceptance_profile_sha256",
    ],
)
def test_rehashed_cross_source_or_policy_plan_abstains(composition, now, field):
    port = VerifiedFakePort(now)
    altered = with_plan(composition, **{field: h("other-" + field)})
    result = bridge(altered, now, port).prepare()
    assert HealingBridgeGap.SOURCE_BINDING_INVALID in result.report.gap_codes
    assert result.source is result.request is None
    assert port.captured == port.verified == 0


def test_partial_target_plan_cannot_hide_authorized_dataset(composition, now):
    targets = tuple(
        value
        for value in composition.evaluation.plan.targets
        if value.partition is not TargetPartition.SYNTHETIC_DATASET
    )
    result = bridge(with_plan(composition, targets=targets), now, VerifiedFakePort(now)).prepare()
    assert HealingBridgeGap.SOURCE_BINDING_INVALID in result.report.gap_codes
    assert result.request is None


def test_mutated_nested_plan_specification_is_revalidated_before_host_capture(composition, now):
    malformed = composition.model_copy(deep=True)
    target = next(
        value
        for value in malformed.evaluation.plan.targets
        if value.partition is TargetPartition.BROWSER_INTENT
    )
    target.specification["locatorRebind"]["obligations"].pop()
    port = VerifiedFakePort(now)
    result = bridge(malformed, now, port).prepare()
    assert HealingBridgeGap.SOURCE_BINDING_INVALID in result.report.gap_codes
    assert port.captured == 0


@pytest.mark.parametrize(
    "field",
    [
        "source_binding_sha256",
        "scope_sha256",
        "classification_receipt_sha256",
        "metadata_intent_sha256",
        "metadata_preimage_receipt_sha256",
    ],
)
def test_cross_request_candidate_proofs_abstain_before_verifier(composition, now, field):
    port = VerifiedFakePort(now, binding_changes={field: h("different-proof")})
    result = bridge(composition, now, port).prepare()
    assert HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_INVALID in result.report.gap_codes
    assert result.request is result.metadata_intent is None
    assert port.verified == 0


@pytest.mark.parametrize(
    "phase",
    [
        EvidencePhase.LIVE_BASELINE,
        EvidencePhase.CANDIDATE_CHECK_ONLY,
        EvidencePhase.RESTORED_BASELINE,
    ],
)
def test_no_baseline_check_only_or_restored_phase_can_be_relabelled(composition, now, phase):
    result = bridge(
        composition,
        now,
        VerifiedFakePort(
            now,
            binding_changes={
                "evidence_phase": phase,
            },
        ),
    ).prepare()
    assert HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_INVALID in result.report.gap_codes
    assert result.request is None


def test_baseline_expectation_root_is_not_candidate_proof(composition, now):
    baseline_sha = composition.expected_execution_contract.contract_sha256
    result = bridge(
        composition,
        now,
        VerifiedFakePort(
            now,
            binding_changes={
                "expected_execution_contract_sha256": baseline_sha,
            },
        ),
    ).prepare()
    assert HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_INVALID in result.report.gap_codes


def test_self_attesting_digests_do_not_bypass_host_authentication(composition, now):
    port = VerifiedFakePort(now, reject=True)
    result = bridge(composition, now, port).prepare()
    assert port.verified == 1
    assert result.request is result.metadata_intent is None
    assert HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_INVALID in result.report.gap_codes
    assert SECRET not in result.report.model_dump_json()


@pytest.mark.parametrize(
    "mutation",
    [
        {"state_sha256": h("different-state")},
        {"source_binding_sha256": h("different-source")},
        {"target_sha256": h("different-target")},
        {"receipt_sha256": "bad-receipt"},
        {"manifest_bytes": b"<Package/>"},
        {"member_bytes": b"malformed XML"},
        {"manifest_bytes": b"x" * 16_385},
        {"member_bytes": b"x" * 1_048_577},
    ],
)
def test_wrong_malformed_oversized_preimage_abstains(source, now, mutation):
    with pytest.raises(HealingBridgeError) as error:
        preview_metadata_intent(
            source, replace(preimage_for(source, now), **mutation), api_version="67.0", now=now
        )
    assert error.value.code is HealingBridgeGap.METADATA_PREIMAGE_INVALID
    assert SECRET not in str(error.value)


@pytest.mark.parametrize(
    "old,new",
    [
        (b"<version>67.0</version>", b"<version>66.0</version>"),
        (b"<version>67.0</version>", b"<version>67.0</version><version>67.0</version>"),
        (b"<version>67.0</version>", b'<version extra="bad">67.0</version>'),
        (b"<version>67.0</version>", b"<version>67.0<nested/></version>"),
        (b"<name>FlexiPage</name>", b"<name>ApexClass</name>"),
        (b"<members>", b"<members>Unexpected"),
        (b"</Package>", b"<types><members>Other</members><name>FlexiPage</name></types></Package>"),
    ],
)
def test_manifest_must_be_exact_member_and_host_api(source, now, old, new):
    preimage = preimage_for(source, now)
    altered = replace(preimage, manifest_bytes=preimage.manifest_bytes.replace(old, new))
    with pytest.raises(HealingBridgeError):
        preview_metadata_intent(source, altered, api_version="67.0", now=now)


def test_self_consistent_live_wrong_property_prestate_is_blocked(source, now):
    preimage = preimage_for(source, now, present=True)
    drift = source.targets[0].browser_intent.locator_rebind.metadata_drift
    wrong = preimage.member_bytes.replace(drift.baseline_value.encode(), b"unapproved")
    preimage = replace(
        preimage,
        member_bytes=wrong,
        state_sha256=metadata_state_sha256(
            {
                "unpackaged/package.xml": preimage.manifest_bytes,
                f"unpackaged/flexipages/{drift.member}.flexipage": wrong,
            }
        ),
    )
    with pytest.raises(HealingBridgeError):
        preview_metadata_intent(source, preimage, api_version="67.0", now=now)


def test_plan_expiry_and_naive_clock_block_before_evidence(composition, source, now):
    for clock in (source.valid_until, now.replace(tzinfo=None)):
        port = VerifiedFakePort(now)
        result = bridge(composition, clock, port).prepare()
        assert result.request is None
        assert port.captured == 0


@pytest.mark.parametrize("changes", ["expired-binding", "expired-preimage", "future-preimage"])
def test_evidence_expiry_or_future_preimage_abstains(composition, now, changes):
    port = VerifiedFakePort(
        now,
        binding_changes={"valid_until": now} if changes == "expired-binding" else {},
        preimage_changes={"valid_until": now}
        if changes == "expired-preimage"
        else {"observed_at": now + timedelta(seconds=1)}
        if changes == "future-preimage"
        else {},
    )
    result = bridge(composition, now, port).prepare()
    assert result.request is None
    assert len(result.report.gap_codes) == 2


def test_expiry_during_host_verification_drops_request(composition, now):
    clock = [now]

    class SlowVerifier(VerifiedFakePort):
        def verify(self, *args):
            super().verify(*args)
            clock[0] += timedelta(minutes=3)

    result = HostSourceHealingBridge(
        lambda: composition,
        api_version="67.0",
        evidence_port=SlowVerifier(now),
        clock=lambda: clock[0],
    ).prepare()
    assert result.request is result.metadata_intent is None
    assert HealingBridgeGap.CANDIDATE_EXECUTION_BINDING_INVALID in result.report.gap_codes


def test_report_contains_only_roots_counts_and_gaps(composition, now):
    result = bridge(composition, now, VerifiedFakePort(now)).prepare()
    serialized = result.report.model_dump_json()
    assert result.request is not None
    for value in (
        SECRET,
        "Opportunity",
        "Fixture_Record",
        "data-testid",
        "frontdoor",
        "locatorRebind",
        "manifest_bytes",
        "sessionId",
        "member_bytes",
    ):
        assert value not in serialized
        assert value not in repr(result)


def test_bridge_and_unavailable_dispatch_have_no_process_or_file_effects(
    composition, now, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("No subprocess, browser launch or filesystem write is permitted")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    result = bridge(composition, now, VerifiedFakePort(now)).prepare()
    assert result.request is not None
    dispatch = UnavailableBrowserHealingDispatch()
    for call in (lambda: dispatch.execute(None, None), lambda: dispatch.close_local(None)):
        with pytest.raises(HealingFailure) as error:
            call()
        assert error.value.code is HealingGap.EXECUTION_DISABLED


def test_capture_errors_are_sanitized_and_callers_cannot_override_targets_or_paths(now):
    def broken():
        raise RuntimeError(SECRET)

    service = HostSourceHealingBridge(broken, api_version="67.0", clock=lambda: now)
    result = service.prepare()
    assert result.request is None
    assert SECRET not in result.report.model_dump_json()
    with pytest.raises(TypeError):
        service.prepare(alias="caller", path="../elsewhere", targets=[])


@pytest.mark.parametrize("version", ["", "67.0; command", "../67.0", "67", "67.1"])
def test_host_api_version_is_bounded(composition, version):
    with pytest.raises(ValueError):
        HostSourceHealingBridge(lambda: composition, api_version=version)


def test_default_denied_plan_cannot_reach_host_evidence_port(composition, now):
    compilation = composition.compilation
    policy = _allow_all_policy(compilation)
    body = policy.model_dump(by_alias=True, mode="json")
    body["authorizedTargetSha256s"] = []
    body["sha256"] = contract_sha256(body)
    policy = type(policy).model_validate(body)
    blocked = compose_candidate_live_plan(
        compilation,
        _classification(compilation, policy),
        policy,
        runner_provenance=_runner(),
        clock=lambda: now,
    )
    port = VerifiedFakePort(now)
    result = bridge(blocked, now, port).prepare()
    assert HealingBridgeGap.SOURCE_PLAN_BLOCKED in result.report.gap_codes
    assert result.source is result.request is None
    assert port.captured == 0


def test_renamed_source_and_multiple_targets_keep_complete_set_then_block_dispatch(tmp_path):
    declarations = _declarations("renamedPanel")
    original = declarations["browserIntents"][0]
    declarations["browserIntents"].append({**original, "surfaceIntent": "independent-surface"})
    compilation = ProductionCandidateTargetCompiler().compile(
        _inputs(_bundle(tmp_path, component="renamedPanel", declarations=declarations))
    )
    policy = _allow_all_policy(compilation)
    now = datetime.now(UTC).replace(microsecond=0)
    composition = compose_candidate_live_plan(
        compilation,
        _classification(compilation, policy),
        policy,
        runner_provenance=_runner(),
        clock=lambda: now,
    )
    source = compile_healing_source(composition, now=now)
    assert len(source.targets) == 2
    assert sum(len(target.assertions) for target in source.targets) == 4
    assert all(
        target.browser_intent.locator_rebind.metadata_drift.component_name == "c:renamedPanel"
        for target in source.targets
    )
    assert source.targets[0].source_assertion_contract_sha256 != (
        source.targets[1].source_assertion_contract_sha256
    )
    port = VerifiedFakePort(now)
    result = bridge(composition, now, port).prepare()
    assert HealingBridgeGap.MULTI_TARGET_METADATA_SCOPE_UNSUPPORTED in result.report.gap_codes
    assert result.report.obligation_count == 4
    assert len(result.report.target_sha256s) == 2
    assert result.request is result.metadata_intent is None
    assert port.captured == 0
    with pytest.raises(HealingBridgeError) as error:
        preview_metadata_intent(source, preimage_for(source, now), api_version="67.0", now=now)
    assert error.value.code is HealingBridgeGap.MULTI_TARGET_METADATA_SCOPE_UNSUPPORTED
