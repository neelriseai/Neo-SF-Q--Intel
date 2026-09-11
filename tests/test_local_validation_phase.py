from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.candidate_target_compiler import (
    CandidateLivePlanComposition,
    CandidateTargetCompilation,
    ExpectedExecutionContract,
    ProductionCandidateTargetCompiler,
    SourceLocalTestObligation,
    _canonical_bytes,
    _sealed,
    compose_candidate_live_plan,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_target_plan import LiveTargetPlan, LiveTargetPlanEvaluation
from neo_sf_q_intel.local_validation_phase import (
    ALL_EVIDENCE_PHASES,
    CANDIDATE_REQUIRED_PHASES,
    READ_ONLY_BASELINE_GATES,
    HostLocalValidationPhasePolicy,
    LocalValidationPhaseError,
    classify_local_validation_phase,
    local_validation_phase_implementation_sha256,
)
from tests.test_candidate_target_compiler import (
    _allow_all_policy,
    _bundle,
    _classification,
    _inputs,
    _local_declarations,
    _runner,
)


def _phase_policy(compilation, **changes):
    """Offline host-policy fixture; production must independently review and pin it."""
    exemptions = []
    for required in compilation.required_local_validations:
        files = {value.locator: value for value in required.bound_files}
        exemptions.append(
            {
                "obligation_id": required.obligation.obligation_id,
                "obligation_sha256": stable_sha256(
                    required.obligation.model_dump(by_alias=True, mode="json")
                ),
                "candidate_tree_sha256": required.candidate_tree_sha256,
                "bound_files_sha256": stable_sha256(
                    [value.model_dump(mode="json") for value in required.bound_files]
                ),
                "changed_nonruntime_files": sorted(
                    [
                        {
                            "locator": value.locator,
                            "category": value.category,
                            "content_sha256": files[value.locator].content_sha256,
                        }
                        for value in compilation.changed_file_dispositions
                        if required.obligation.obligation_id in value.local_obligation_ids
                    ],
                    key=lambda value: value["locator"],
                ),
                "not_required_for_phase": "LIVE_BASELINE",
            }
        )
    now = datetime.now(UTC)
    body = {
        "schema_version": "1.0.0",
        "authority_class": "HOST_READ_ONLY_BASELINE_PHASE_APPLICABILITY",
        "policy_id": "reviewed-local-baseline-applicability",
        "project_id": compilation.verified_scope.project_id,
        "source_contract_sha256": compilation.source_contract_sha256,
        "implementation_sha256": local_validation_phase_implementation_sha256(),
        "issued_at": (now - timedelta(seconds=5)).isoformat(),
        "valid_until": (now + timedelta(minutes=2)).isoformat(),
        "exemptions": exemptions,
    }
    body.update(changes)
    for timestamp in ("issued_at", "valid_until"):
        body[timestamp] = datetime.fromisoformat(body[timestamp]).isoformat().replace("+00:00", "Z")
    body["policy_sha256"] = stable_sha256(body)
    return HostLocalValidationPhasePolicy.model_validate(body)


def _phase_compilation(tmp_path, *, omit_baseline=True, unknown=False, suffix="guide"):
    declarations = _local_declarations()
    if omit_baseline:
        declarations["localTestObligations"][0]["requiredEvidencePhases"] = [
            value.value for value in CANDIDATE_REQUIRED_PHASES
        ]
    declarations["nonRuntimeFiles"][0]["locator"] = f"workspace/dx/docs/{suffix}.md"
    changes = [
        (f"workspace/dx/docs/{suffix}.md", b"# Before\n", b"# Current\n"),
        (
            "workspace/dx/catalog.test.mjs",
            b"import test from 'node:test';test('links',()=>{});",
            b"import test from 'node:test';test('links',()=>{});",
        ),
    ]
    if unknown:
        changes.append(("workspace/dx/unknown.py", b"print(1)", b"print(2)"))
    pair = _bundle(tmp_path, declarations=declarations, extra_changes=tuple(changes))
    return ProductionCandidateTargetCompiler().compile(_inputs(pair))


@pytest.fixture(scope="module")
def phase_compilation(tmp_path_factory):
    return _phase_compilation(tmp_path_factory.mktemp("phase-candidate"))


def test_default_source_phase_applicability_is_all_and_cannot_waive_candidate_checks():
    declaration = _local_declarations()["localTestObligations"][0]
    assert SourceLocalTestObligation.model_validate(declaration).required_evidence_phases == (
        ALL_EVIDENCE_PHASES
    )
    for values in ([], ["LIVE_BASELINE"], ["CANDIDATE_CHECK_ONLY", "DEPLOYED_CANDIDATE"]):
        with pytest.raises(ValidationError):
            SourceLocalTestObligation.model_validate(
                {**declaration, "requiredEvidencePhases": values}
            )


def test_source_omission_without_host_phase_policy_still_blocks(phase_compilation):
    policy = _allow_all_policy(phase_compilation)
    result = compose_candidate_live_plan(
        phase_compilation, _classification(phase_compilation, policy), policy, _runner()
    )
    assert result.evaluation.state == "BLOCKED"
    assert result.deferred_local_validations == ()
    assert result.verified_local_validations == ()


def test_baseline_composition_preserves_unresolved_checks_and_pins_every_representation(
    phase_compilation,
):
    phase = _phase_policy(phase_compilation)
    policy = _allow_all_policy(phase_compilation)
    result = compose_candidate_live_plan(
        phase_compilation,
        _classification(phase_compilation, policy),
        policy,
        _runner(),
        local_validation_phase_policy=phase,
    )
    assert result.evaluation.state == "READY"
    assert result.verified_local_validations == ()
    assert len(result.deferred_local_validations) == len(
        phase_compilation.required_local_validations
    )
    expected = result.expected_execution_contract
    plan = result.evaluation.plan
    assert expected and plan
    assert (
        expected.deferred_local_validations
        == plan.deferred_local_validations
        == (result.deferred_local_validations)
    )
    assert (
        expected.local_validation_phase_policy_sha256 == plan.local_validation_phase_policy_sha256
    )
    assert expected.local_validation_phase_policy_sha256 == phase.policy_sha256
    assert (
        expected.phase_read_only_gate_ids
        == plan.phase_read_only_gate_ids
        == (READ_ONLY_BASELINE_GATES)
    )
    assert datetime.fromisoformat(expected.valid_until) <= phase.valid_until
    assert all(value.satisfied is False for value in phase_compilation.required_local_validations)
    assert all(value.status == "UNRESOLVED" for value in result.deferred_local_validations)
    local_support = [
        value
        for value in result.support_requirements
        if value.receipt_role == "LOCAL_SOURCE_VALIDATION_RECEIPT"
    ]
    assert {value.status for value in local_support} == {"NOT_REQUIRED_FOR_PHASE_UNRESOLVED"}


@pytest.mark.parametrize("host_phase", CANDIDATE_REQUIRED_PHASES)
def test_baseline_policy_cannot_promote_candidate_check_deploy_or_restored_phases(
    phase_compilation, host_phase
):
    policy = _allow_all_policy(phase_compilation)
    result = compose_candidate_live_plan(
        phase_compilation,
        _classification(phase_compilation, policy),
        policy,
        _runner(),
        host_phase=host_phase,
        local_validation_phase_policy=_phase_policy(phase_compilation),
    )
    assert result.evaluation.state == "BLOCKED"
    assert result.expected_execution_contract is None
    assert result.deferred_local_validations == result.verified_local_validations == ()


@pytest.mark.parametrize(
    "field",
    [
        "obligation_sha256",
        "candidate_tree_sha256",
        "bound_files_sha256",
        "content_sha256",
        "category",
    ],
)
def test_resealed_host_policy_tampering_cannot_exempt_different_input(phase_compilation, field):
    policy = _phase_policy(phase_compilation)
    body = policy.model_dump(mode="json", exclude={"policy_sha256"})
    pin = body["exemptions"][0]
    if field in {"content_sha256", "category"}:
        pin["changed_nonruntime_files"][0][field] = (
            "LOCAL_CATALOG_TOOL" if field == "category" else "0" * 64
        )
    else:
        pin[field] = "0" * 64
    body["policy_sha256"] = stable_sha256(body)
    with pytest.raises(LocalValidationPhaseError, match="LOCAL_PHASE_POLICY_MISMATCH"):
        classify_local_validation_phase(
            phase_compilation,
            policy=HostLocalValidationPhasePolicy.model_validate(body),
            observed_at=datetime.now(UTC),
        )


@pytest.mark.parametrize(
    "field", ["source_contract_sha256", "project_id", "implementation_sha256", "valid_until"]
)
def test_phase_policy_replay_requires_current_exact_source_host_and_implementation(
    phase_compilation, field
):
    values = {
        "source_contract_sha256": "0" * 64,
        "project_id": "different-project",
        "implementation_sha256": "0" * 64,
        "issued_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        "valid_until": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    changes = {field: values[field]}
    if field == "valid_until":
        changes["issued_at"] = values["issued_at"]
    with pytest.raises(LocalValidationPhaseError):
        classify_local_validation_phase(
            phase_compilation,
            policy=_phase_policy(phase_compilation, **changes),
            observed_at=datetime.now(UTC),
        )


@pytest.mark.parametrize(
    "disposition", ["UNKNOWN_BLOCKING", "SALESFORCE_RUNTIME", "CONSUMED_SOURCE_CONTRACT"]
)
def test_relabelled_runtime_contract_and_unknown_files_are_never_exempted(
    phase_compilation, disposition
):
    policy = _phase_policy(phase_compilation)
    body = phase_compilation.model_dump(mode="json", exclude={"compilation_sha256"})
    change = next(
        value for value in body["changed_file_dispositions"] if value["local_obligation_ids"]
    )
    change["disposition"] = disposition
    body["compilation_sha256"] = stable_sha256(body)
    with pytest.raises(LocalValidationPhaseError):
        classify_local_validation_phase(
            CandidateTargetCompilation.model_validate(body),
            policy=policy,
            observed_at=datetime.now(UTC),
        )


def test_unknown_executable_still_blocks_even_with_valid_nonruntime_exemptions(tmp_path):
    compilation = _phase_compilation(tmp_path, unknown=True)
    policy = _allow_all_policy(compilation)
    result = compose_candidate_live_plan(
        compilation,
        _classification(compilation, policy),
        policy,
        _runner(),
        local_validation_phase_policy=_phase_policy(compilation),
    )
    assert result.evaluation.state == "BLOCKED"
    assert result.expected_execution_contract is None
    assert result.deferred_local_validations  # Recorded, not erased by the unrelated blocker.


def test_scenario_rename_does_not_change_phase_decision_but_old_source_pin_blocks(
    tmp_path, phase_compilation
):
    renamed = _phase_compilation(tmp_path, suffix="renamed-usage")
    assert len(
        classify_local_validation_phase(
            renamed, policy=_phase_policy(renamed), observed_at=datetime.now(UTC)
        )
    ) == len(phase_compilation.required_local_validations)
    with pytest.raises(LocalValidationPhaseError):
        classify_local_validation_phase(
            renamed, policy=_phase_policy(phase_compilation), observed_at=datetime.now(UTC)
        )


def test_expected_contract_rejects_resealed_apex_execution_widening(phase_compilation):
    policy = _allow_all_policy(phase_compilation)
    result = compose_candidate_live_plan(
        phase_compilation,
        _classification(phase_compilation, policy),
        policy,
        _runner(),
        local_validation_phase_policy=_phase_policy(phase_compilation),
    )
    body = result.expected_execution_contract.model_dump(mode="json", exclude={"contract_sha256"})
    body["phase_read_only_gate_ids"].append("SF-L08")
    body["contract_sha256"] = stable_sha256(body)
    with pytest.raises(ValidationError):
        ExpectedExecutionContract.model_validate(body)


def test_fully_resealed_representation_cannot_erase_pending_local_obligations(phase_compilation):
    import hashlib

    policy = _allow_all_policy(phase_compilation)
    result = compose_candidate_live_plan(
        phase_compilation,
        _classification(phase_compilation, policy),
        policy,
        _runner(),
        local_validation_phase_policy=_phase_policy(phase_compilation),
    )
    plan_body = result.evaluation.plan.model_dump(mode="python", exclude={"plan_sha256"})
    plan_body.update(deferred_local_validations=(), targets=result.evaluation.plan.targets)
    plan = _sealed(LiveTargetPlan, "plan_sha256", **plan_body)
    expected_body = result.expected_execution_contract.model_dump(
        mode="python", exclude={"contract_sha256"}
    )
    expected_body.update(
        plan_sha256=plan.plan_sha256,
        deferred_local_validations=(),
        scope=result.expected_execution_contract.scope,
        runner_provenance=result.expected_execution_contract.runner_provenance,
        assertions=result.expected_execution_contract.assertions,
        datasets=result.expected_execution_contract.datasets,
    )
    expected = _sealed(ExpectedExecutionContract, "contract_sha256", **expected_body)
    evaluation_body = result.evaluation.model_dump(mode="python", exclude={"evaluation_sha256"})
    evaluation_body.update(plan=plan, gaps=result.evaluation.gaps)
    evaluation = _sealed(LiveTargetPlanEvaluation, "evaluation_sha256", **evaluation_body)
    expected_bytes = _canonical_bytes(expected.model_dump(mode="json"))
    body = result.model_dump(mode="python", exclude={"composition_sha256"})
    body.update(
        compilation=phase_compilation,
        evaluation=evaluation,
        expected_execution_contract=expected,
        expected_execution_contract_bytes=expected_bytes,
        expected_execution_contract_bytes_sha256=hashlib.sha256(expected_bytes).hexdigest(),
        deferred_local_validations=(),
        support_requirements=result.support_requirements,
    )
    with pytest.raises(ValidationError, match="every independent local obligation"):
        _sealed(CandidateLivePlanComposition, "composition_sha256", **body)


def test_host_policy_cannot_override_source_all_phase_requirement(phase_compilation):
    body = phase_compilation.model_dump(mode="json", exclude={"compilation_sha256"})
    body["required_local_validations"][0]["obligation"]["required_evidence_phases"] = [
        value.value for value in ALL_EVIDENCE_PHASES
    ]
    body["compilation_sha256"] = stable_sha256(body)
    compilation = CandidateTargetCompilation.model_validate(body)
    with pytest.raises(LocalValidationPhaseError):
        classify_local_validation_phase(
            compilation, policy=_phase_policy(compilation), observed_at=datetime.now(UTC)
        )
